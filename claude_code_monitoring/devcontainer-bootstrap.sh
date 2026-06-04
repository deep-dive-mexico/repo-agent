#!/usr/bin/env bash
# Claude Code monitoring — dev container bootstrap.
#
# Vendored into <repo>/claude_code_monitoring/ by the onboarding step, and meant
# to run as a devcontainer "postCreateCommand". On every container (re)build it:
#   1. no-ops unless this repo is actually onboarded for monitoring,
#   2. seeds the machine-level hook scripts that the repo's committed
#      .claude/settings.json points at ($HOME/.claude/hooks/) from the copies
#      vendored alongside this script, and
#   3. ensures $HOME/.claude/metrics/ exists (the volume mount point) so session
#      logs persist outside the container.
#
# Idempotent and side-effect-light: hook scripts are refreshed every run (so
# fixes propagate), session data is never clobbered. Never fails the build —
# missing prerequisites are warnings, not errors. (Pricing isn't seeded here —
# it lives in the shared S3 bucket and is only needed by the dashboard builder,
# which runs centrally, not inside dev containers.)
set -euo pipefail

# This script lives at <repo>/claude_code_monitoring/ ; the repo root is its parent.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"
SETTINGS="$REPO_ROOT/.claude/settings.json"

log() { printf '  [claude-monitoring] %s\n' "$1"; }

# 1. Act only if this repo is onboarded — its settings.json must register one of
#    the monitoring session hooks. Otherwise quietly do nothing.
if [ ! -f "$SETTINGS" ] || ! grep -qE 'session-(start|end)\.sh' "$SETTINGS" 2>/dev/null; then
  log "repo not onboarded for monitoring — nothing to seed"
  exit 0
fi

HOOKS_DIR="$HOME/.claude/hooks"
METRICS_DIR="$HOME/.claude/metrics"

# A named-volume mount at ~/.claude/metrics lands root-owned when the container
# runs as a non-root user — Docker creates the mountpoint (and its parent
# ~/.claude) as root. That blocks this user from writing ANYWHERE under
# ~/.claude: our hook seeding here, and even the Claude CLI installer
# (`~/.claude/downloads`). Best-effort self-heal before we touch it.
CLAUDE_DIR="$HOME/.claude"
if [ -e "$CLAUDE_DIR" ] && [ ! -w "$CLAUDE_DIR" ]; then
  if command -v sudo >/dev/null 2>&1; then
    sudo chown -R "$(id -u):$(id -g)" "$CLAUDE_DIR" 2>/dev/null \
      && log "fixed ownership of $CLAUDE_DIR (root-owned from the volume mount)" \
      || log "WARNING: $CLAUDE_DIR is root-owned and 'sudo chown' failed — run the container as root or chown it in postCreateCommand"
  else
    log "WARNING: $CLAUDE_DIR is root-owned and 'sudo' is unavailable — set \"remoteUser\": \"root\" or chown it in postCreateCommand"
  fi
fi
mkdir -p "$HOOKS_DIR" "$METRICS_DIR"

# 2. Runtime prerequisites. Warn, don't fail — the image owner controls these.
#    Shipping signs S3 requests with the Python stdlib (SigV4) — no uv, no boto3,
#    nothing to install — so jq + python3 are all that's needed.
command -v jq      >/dev/null 2>&1 || log "WARNING: 'jq' not found — session hooks need it (e.g. apt-get install -y jq)"
command -v python3 >/dev/null 2>&1 || log "WARNING: 'python3' not found — S3 shipping needs it (stdlib only)"

# 3. Seed hook scripts from the vendored copies. Always refresh: these are code,
#    not data, so a fix in the repo propagates on the next container build.
seeded=0
for f in session-start.sh session-end.sh ship_session.py; do
  if [ -f "$HERE/$f" ]; then
    cp "$HERE/$f" "$HOOKS_DIR/$f"
    chmod +x "$HOOKS_DIR/$f"
    seeded=$((seeded + 1))
  else
    log "WARNING: vendored '$f' missing from $HERE — re-run onboarding to refresh it"
  fi
done

# 4. Enable S3 shipping by default (fixed org bucket). Seed only if absent so a
#    populated volume / local edits are never clobbered.
if [ -f "$HERE/monitoring.config.example.json" ] && [ ! -f "$METRICS_DIR/monitoring.config.json" ]; then
  cp "$HERE/monitoring.config.example.json" "$METRICS_DIR/monitoring.config.json"
  log "S3 shipping enabled — config seeded from vendored example"
fi

# 5. Register the session hooks at the USER level (~/.claude/settings.json) so
#    they fire no matter which directory Claude is launched from in this
#    container. Project settings are only read from the launch dir, which
#    silently disables monitoring when the workspace root is a parent of this
#    repo (monorepos / multi-root workspaces — a common dev-container layout),
#    and also means sessions run outside the repo go unrecorded. User-level
#    hooks cover every session in the container. Idempotent: a hook is added
#    only if its command isn't already registered. Double-firing (when both this
#    and the repo's project settings match) is de-duped by the session-end
#    ship-once guard.
#    Done with jq (the hooks' core dependency — present wherever monitoring runs),
#    NOT python3: some slim images ship a python3 missing parts of the stdlib
#    (e.g. no `json`), which would silently skip this step.
USER_SETTINGS="$HOME/.claude/settings.json"
# Literal $HOME (single-quoted) — Claude expands it at hook time, matching the
# committed project-settings template.
SS_CMD='$HOME/.claude/hooks/session-start.sh'
SE_CMD='$HOME/.claude/hooks/session-end.sh'
MERGE_PROG='def has($ev;$c): [.hooks[$ev][]?.hooks[]?.command] | any(.==$c);
(if has("SessionStart";$ss) then . else .hooks.SessionStart += [{matcher:"",hooks:[{type:"command",command:$ss}]}] end)
| (if has("SessionEnd";$se) then . else .hooks.SessionEnd += [{matcher:"",hooks:[{type:"command",command:$se}]}] end)'
if ! command -v jq >/dev/null 2>&1; then
  log "WARNING: jq missing — can't register user-level hooks; monitoring will only"
  log "         fire when Claude is launched from a dir whose .claude/settings.json has them"
elif [ -s "$USER_SETTINGS" ] && ! jq -e . "$USER_SETTINGS" >/dev/null 2>&1; then
  log "WARNING: $USER_SETTINGS exists but isn't valid JSON — leaving it untouched"
else
  base='{}'; [ -s "$USER_SETTINGS" ] && base="$(cat "$USER_SETTINGS")"
  if merged="$(printf '%s' "$base" | jq --arg ss "$SS_CMD" --arg se "$SE_CMD" "$MERGE_PROG" 2>/dev/null)"; then
    printf '%s\n' "$merged" > "$USER_SETTINGS"
    log "registered session hooks in $USER_SETTINGS (fire from any launch dir)"
  else
    log "WARNING: couldn't update $USER_SETTINGS — monitoring may only fire from the repo dir"
  fi
fi

# 6. Seed AWS creds to a machine-level path so the shipper resolves them no
#    matter the session's cwd — the per-repo walk-up fails when Claude runs from
#    a parent/sibling of this repo, or outside it entirely. Only when the repo
#    carries the (gitignored) creds file, i.e. the working tree is mounted into
#    the container; never overwrite an existing machine-level creds file.
if [ -f "$HERE/.aws-credentials.json" ] && [ ! -f "$METRICS_DIR/.aws-credentials.json" ]; then
  cp "$HERE/.aws-credentials.json" "$METRICS_DIR/.aws-credentials.json"
  chmod 600 "$METRICS_DIR/.aws-credentials.json"
  log "seeded AWS credentials to $METRICS_DIR (resolves from any launch dir)"
fi

# 6b. Mark this container as "log every session." The hooks normally apply a
#     per-repo opt-in gate (a host privacy measure), but inside a dev container the
#     whole environment is a dedicated onboarded workspace and devs launch Claude
#     from the container root — so here we log all sessions. This flag is
#     container-local ($HOME/.claude, not the metrics volume), so it never reaches
#     the host where the gate still applies.
: > "$CLAUDE_DIR/.monitor-all" 2>/dev/null \
  && log "logging all sessions in this container (per-repo gate is host-only)" || true

# 7. Sanity: is the metrics dir writable by this user? A named-volume mount can
#    land root-owned when the container runs as a non-root user, which would make
#    session logging silently fail.
if ( : > "$METRICS_DIR/.write-test" ) 2>/dev/null; then
  rm -f "$METRICS_DIR/.write-test"
else
  log "WARNING: $METRICS_DIR is not writable by '$(id -un)'. If this container runs"
  log "         as a non-root user, add a chown to postCreateCommand or set"
  log "         \"remoteUser\": \"root\" — otherwise session logs can't be written."
fi

log "ready — $seeded hook script(s) in $HOOKS_DIR, logs -> $METRICS_DIR"
exit 0
