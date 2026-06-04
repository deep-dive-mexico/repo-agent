#!/usr/bin/env bash
set -euo pipefail

METRICS_DIR="${HOME}/.claude/metrics"
SESSIONS_DIR="${METRICS_DIR}/sessions"
mkdir -p "${SESSIONS_DIR}"

# Self-seed S3 shipping config from the vendored example if absent, so shipping
# works even when no dev-container bootstrap ran (e.g. a gitignored/absent
# devcontainer.json). Only fires when this script runs from the repo's vendored
# claude_code_monitoring/ (which carries the example); the machine-level copy in
# ~/.claude/hooks has no example next to it, so it no-ops there. Never clobbers.
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
if [ -f "${_HERE}/monitoring.config.example.json" ] && [ ! -f "${METRICS_DIR}/monitoring.config.json" ]; then
  cp "${_HERE}/monitoring.config.example.json" "${METRICS_DIR}/monitoring.config.json" 2>/dev/null || true
fi

# Opt-in gate. This script can run as a USER-LEVEL hook (~/.claude/settings.json,
# installed once globally) that fires for EVERY Claude session — so we record a
# session ONLY if it belongs to an onboarded repo, identified by a
# claude_code_monitoring/ marker directory. Walk UP from the session cwd (launched
# inside the repo), then scan a couple of levels DOWN (launched from a parent that
# holds onboarded repos — the shared dev-container case). No marker in scope means
# this isn't an opted-in repo, so we do nothing — a dev's personal/non-org work is
# never logged. (This keeps a single global hook strictly per-repo.)
onboarded_root() {
  local d="$1" m
  while [ -n "$d" ] && [ "$d" != "/" ] && [ "$d" != "." ]; do
    if [ -d "$d/claude_code_monitoring" ]; then printf '%s' "$d"; return 0; fi
    d="$(dirname "$d")"
  done
  m="$(find "$1" -maxdepth 2 -type d -name claude_code_monitoring 2>/dev/null | head -n1 || true)"
  if [ -n "$m" ]; then dirname "$m"; return 0; fi
  return 1
}

INPUT="$(cat)"

SESSION_ID="$(printf '%s' "${INPUT}" | jq -r '.session_id // "unknown"')"
SOURCE="$(printf '%s' "${INPUT}" | jq -r '.source // "unknown"')"
MODEL="$(printf '%s' "${INPUT}" | jq -r '.model // .model_id // "unknown"')"
CWD="$(printf '%s' "${INPUT}" | jq -r '.cwd // "unknown"')"

# In a dev container set up by our bootstrap, log EVERY session — the whole
# container is a dedicated onboarded workspace and devs launch Claude from the
# container root, not a repo root. The bootstrap drops this flag (container-local,
# never on the host). Everywhere else (the host) apply the per-repo opt-in gate so
# a dev's personal / non-org work is never logged.
if [ ! -f "${HOME}/.claude/.monitor-all" ]; then
  GATE_DIR="${CWD}"; [ "${GATE_DIR}" = "unknown" ] && GATE_DIR="${PWD}"
  onboarded_root "${GATE_DIR}" >/dev/null 2>&1 || exit 0   # not an onboarded repo — no-op
fi

START_TS="$(date -u +%s)"
START_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

STATE_FILE="${SESSIONS_DIR}/${SESSION_ID}.json"

jq -n \
  --arg session_id "${SESSION_ID}" \
  --arg source "${SOURCE}" \
  --arg model "${MODEL}" \
  --arg cwd "${CWD}" \
  --arg start_iso "${START_ISO}" \
  --argjson start_ts "${START_TS}" \
  '{session_id: $session_id, source: $source, model: $model, cwd: $cwd, start_ts: $start_ts, start_iso: $start_iso}' \
  > "${STATE_FILE}"

exit 0
