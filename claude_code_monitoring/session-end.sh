#!/usr/bin/env bash
# NB: no `-u`. A session hook must never hard-crash the user's session; with `set
# -u` an unbound variable aborts before the ERR trap can exit 0 (which is exactly
# how a stray `stat` quirk surfaced as "unbound variable" and broke SessionEnd).
# Without it, an unset var just expands empty and the trap keeps us at exit 0.
set -eo pipefail

METRICS_DIR="${HOME}/.claude/metrics"
SESSIONS_DIR="${METRICS_DIR}/sessions"
USAGE_LOG="${METRICS_DIR}/usage.jsonl"
mkdir -p "${SESSIONS_DIR}"

# Never block shutdown — trap any unexpected failure and exit 0.
trap 'exit 0' ERR

INPUT="$(cat || true)"

SESSION_ID="$(printf '%s' "${INPUT}" | jq -r '.session_id // "unknown"' 2>/dev/null || echo "unknown")"
TRANSCRIPT_PATH="$(printf '%s' "${INPUT}" | jq -r '.transcript_path // ""' 2>/dev/null || echo "")"
REASON="$(printf '%s' "${INPUT}" | jq -r '.reason // ""' 2>/dev/null || echo "")"
END_TS="$(date -u +%s)"
END_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

STATE_FILE="${SESSIONS_DIR}/${SESSION_ID}.json"

# Opt-in gate (mirrors session-start's marker check): no state file means
# session-start didn't record this session — it wasn't an onboarded repo, or
# start never ran — so do nothing. This is what keeps a global user-level hook
# strictly per-repo: non-onboarded / personal sessions are never logged or shipped.
[[ -f "${STATE_FILE}" ]] || exit 0

# Ship-once guard. SessionEnd can fire twice for one session when the hooks are
# registered at BOTH the user level (~/.claude/settings.json, seeded by the
# dev-container bootstrap so monitoring fires from any launch dir) and the
# project level (the repo's committed .claude/settings.json) — e.g. Claude is
# launched from the onboarded repo root inside a container. Claim the session
# atomically (noclobber); the second invocation no-ops so we never
# double-append to usage.jsonl or ship the same session twice.
ENDED_MARKER="${SESSIONS_DIR}/${SESSION_ID}.ended"
if ! ( set -o noclobber; : > "${ENDED_MARKER}" ) 2>/dev/null; then
  exit 0
fi

if [[ -f "${STATE_FILE}" ]]; then
  START_TS="$(jq -r '.start_ts // 0' "${STATE_FILE}" 2>/dev/null || echo 0)"
  START_ISO="$(jq -r '.start_iso // "unknown"' "${STATE_FILE}" 2>/dev/null || echo "unknown")"
  SOURCE="$(jq -r '.source // "unknown"' "${STATE_FILE}" 2>/dev/null || echo "unknown")"
  MODEL="$(jq -r '.model // "unknown"' "${STATE_FILE}" 2>/dev/null || echo "unknown")"
  CWD="$(jq -r '.cwd // "unknown"' "${STATE_FILE}" 2>/dev/null || echo "unknown")"
else
  START_TS=0
  START_ISO="unknown"
  SOURCE="unknown"
  MODEL="unknown"
  CWD="unknown"
fi

if [[ "${START_TS}" =~ ^[0-9]+$ ]] && [[ "${START_TS}" -gt 0 ]]; then
  DURATION_SECS=$(( END_TS - START_TS ))
else
  DURATION_SECS=0
fi

# Repo attribution from cwd. With user-level hooks, sessions fire from anywhere —
# inside the onboarded repo, a parent/super-repo, a sibling, or no repo at all.
# cwd (the full launch path) is already recorded; additionally resolve the git
# repo so each record says *which* repo it belongs to. `repo_root` is the git
# top-level at cwd; `repo_remote` its origin (the canonical "which repo");
# `onboarded` flags whether that repo carries the monitoring runtime. All
# best-effort — they degrade to "unknown"/false outside a git repo.
REPO_ROOT="unknown"
REPO_REMOTE="unknown"
ONBOARDED=false
if [[ "${CWD}" != "unknown" && -d "${CWD}" ]] && command -v git >/dev/null 2>&1; then
  REPO_ROOT="$(git -C "${CWD}" rev-parse --show-toplevel 2>/dev/null || echo unknown)"
  if [[ "${REPO_ROOT}" != "unknown" ]]; then
    REPO_REMOTE="$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null || echo unknown)"
    [[ -d "${REPO_ROOT}/claude_code_monitoring" ]] && ONBOARDED=true
  fi
fi

# Account info — cached for 24h to avoid hitting the API on every session.
ACCOUNT_EMAIL="unknown"
ACCOUNT_UUID="unknown"
ACCOUNT_NAME="unknown"
ORG_NAME="unknown"
ORG_UUID="unknown"
ACCOUNT_CACHE="${METRICS_DIR}/account.json"
ACCOUNT_TTL=86400  # 24h

refresh_account=0
if [[ ! -f "${ACCOUNT_CACHE}" ]]; then
  refresh_account=1
else
  # Portable mtime. Try GNU/Linux `stat -c %Y` FIRST (most containers), then
  # BSD/macOS `stat -f %m`. NB: on GNU, `-f` is --file-system (not a format), so
  # `stat -f %m FILE` prints a "File: ..." dump instead of failing — so we must
  # not lead with it. The numeric guard then makes sure no stray output can reach
  # the arithmetic (which under `set -u` would abort the whole hook).
  MTIME="$(stat -c %Y "${ACCOUNT_CACHE}" 2>/dev/null || stat -f %m "${ACCOUNT_CACHE}" 2>/dev/null || echo 0)"
  case "${MTIME}" in ''|*[!0-9]*) MTIME=0 ;; esac
  CACHE_AGE=$(( $(date -u +%s) - MTIME ))
  [[ "${CACHE_AGE}" -gt "${ACCOUNT_TTL}" ]] && refresh_account=1
fi

# Account switch: the cache is keyed by age, not by token, so a re-login would
# otherwise keep stamping the old email until the TTL expires. If the credentials
# file is newer than the cached profile, force a refresh. Best-effort — covers
# Linux / dev containers (the creds file); macOS uses the keychain.
if [[ -f "${HOME}/.claude/.credentials.json" \
      && "${HOME}/.claude/.credentials.json" -nt "${ACCOUNT_CACHE}" ]]; then
  refresh_account=1
fi

if [[ "${refresh_account}" -eq 1 ]]; then
  # Token for the account-profile lookup. macOS: the keychain. Linux (incl. dev
  # containers): the keychain command doesn't exist, so fall back to Claude's
  # credentials file — without this, account_email stays "unknown" on Linux and
  # S3 keys read sessions/unknown/...
  TOKEN="$(security find-generic-password -s 'Claude Code-credentials' -w 2>/dev/null \
    | jq -r '.claudeAiOauth.accessToken // empty' 2>/dev/null || echo '')"
  if [[ -z "${TOKEN}" && -f "${HOME}/.claude/.credentials.json" ]]; then
    TOKEN="$(jq -r '.claudeAiOauth.accessToken // empty' "${HOME}/.claude/.credentials.json" 2>/dev/null || echo '')"
  fi
  if [[ -n "${TOKEN}" ]]; then
    curl -fsS --max-time 5 \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "anthropic-beta: oauth-2025-04-20" \
      "https://api.anthropic.com/api/oauth/profile" \
      -o "${ACCOUNT_CACHE}.tmp" 2>/dev/null \
      && mv "${ACCOUNT_CACHE}.tmp" "${ACCOUNT_CACHE}" \
      || rm -f "${ACCOUNT_CACHE}.tmp" 2>/dev/null || true
  fi
fi

if [[ -f "${ACCOUNT_CACHE}" ]]; then
  ACCOUNT_EMAIL="$(jq -r '.account.email // "unknown"' "${ACCOUNT_CACHE}" 2>/dev/null || echo unknown)"
  ACCOUNT_UUID="$(jq -r  '.account.uuid  // "unknown"' "${ACCOUNT_CACHE}" 2>/dev/null || echo unknown)"
  ACCOUNT_NAME="$(jq -r  '.account.full_name // "unknown"' "${ACCOUNT_CACHE}" 2>/dev/null || echo unknown)"
  ORG_NAME="$(jq -r      '.organization.name // "unknown"' "${ACCOUNT_CACHE}" 2>/dev/null || echo unknown)"
  ORG_UUID="$(jq -r      '.organization.uuid // "unknown"' "${ACCOUNT_CACHE}" 2>/dev/null || echo unknown)"
fi

# Token totals — sum across assistant message.usage objects in the transcript JSONL.
INPUT_TOKENS=0
OUTPUT_TOKENS=0
CACHE_CREATION_TOKENS=0
CACHE_READ_TOKENS=0
PEAK_CONTEXT_TOKENS=0

if [[ -n "${TRANSCRIPT_PATH}" && -f "${TRANSCRIPT_PATH}" ]]; then
  # peak_context_tokens = largest single-request prompt size (input +
  # cache-create + cache-read for one turn). This is the context-window
  # high-water mark, distinct from the summed token flow.
  TOKEN_JSON="$(jq -s '
    [ .[] | select(.message?.usage?) | .message.usage ]
    | {
        input_tokens:                (map(.input_tokens // 0) | add // 0),
        output_tokens:               (map(.output_tokens // 0) | add // 0),
        cache_creation_input_tokens: (map(.cache_creation_input_tokens // 0) | add // 0),
        cache_read_input_tokens:     (map(.cache_read_input_tokens // 0) | add // 0),
        peak_context_tokens:         (map((.input_tokens // 0) + (.cache_creation_input_tokens // 0) + (.cache_read_input_tokens // 0)) | max // 0)
      }
  ' "${TRANSCRIPT_PATH}" 2>/dev/null || echo '{"input_tokens":0,"output_tokens":0,"cache_creation_input_tokens":0,"cache_read_input_tokens":0,"peak_context_tokens":0}')"
  INPUT_TOKENS="$(printf '%s' "${TOKEN_JSON}" | jq -r '.input_tokens')"
  OUTPUT_TOKENS="$(printf '%s' "${TOKEN_JSON}" | jq -r '.output_tokens')"
  CACHE_CREATION_TOKENS="$(printf '%s' "${TOKEN_JSON}" | jq -r '.cache_creation_input_tokens')"
  CACHE_READ_TOKENS="$(printf '%s' "${TOKEN_JSON}" | jq -r '.cache_read_input_tokens')"
  PEAK_CONTEXT_TOKENS="$(printf '%s' "${TOKEN_JSON}" | jq -r '.peak_context_tokens')"
fi

RECORD="$(jq -nc \
  --arg session_id "${SESSION_ID}" \
  --arg source "${SOURCE}" \
  --arg model "${MODEL}" \
  --arg cwd "${CWD}" \
  --arg repo_root "${REPO_ROOT}" \
  --arg repo_remote "${REPO_REMOTE}" \
  --argjson onboarded "${ONBOARDED}" \
  --arg start_iso "${START_ISO}" \
  --arg end_iso "${END_ISO}" \
  --arg reason "${REASON}" \
  --arg transcript_path "${TRANSCRIPT_PATH}" \
  --arg account_email "${ACCOUNT_EMAIL}" \
  --arg account_uuid "${ACCOUNT_UUID}" \
  --arg account_name "${ACCOUNT_NAME}" \
  --arg org_name "${ORG_NAME}" \
  --arg org_uuid "${ORG_UUID}" \
  --argjson start_ts "${START_TS}" \
  --argjson end_ts "${END_TS}" \
  --argjson duration_secs "${DURATION_SECS}" \
  --argjson input_tokens "${INPUT_TOKENS}" \
  --argjson output_tokens "${OUTPUT_TOKENS}" \
  --argjson cache_creation_input_tokens "${CACHE_CREATION_TOKENS}" \
  --argjson cache_read_input_tokens "${CACHE_READ_TOKENS}" \
  --argjson peak_context_tokens "${PEAK_CONTEXT_TOKENS}" \
  '{session_id:$session_id, source:$source, model:$model, cwd:$cwd,
    repo_root:$repo_root, repo_remote:$repo_remote, onboarded:$onboarded,
    start_ts:$start_ts, start_iso:$start_iso, end_ts:$end_ts, end_iso:$end_iso,
    duration_secs:$duration_secs, reason:$reason, transcript_path:$transcript_path,
    account_email:$account_email, account_uuid:$account_uuid, account_name:$account_name,
    org_name:$org_name, org_uuid:$org_uuid,
    input_tokens:$input_tokens, output_tokens:$output_tokens,
    cache_creation_input_tokens:$cache_creation_input_tokens,
    cache_read_input_tokens:$cache_read_input_tokens,
    peak_context_tokens:$peak_context_tokens}')"

printf '%s\n' "${RECORD}" >> "${USAGE_LOG}" 2>/dev/null || true

# Fire-and-forget S3 upload. No-op if ~/.claude/metrics/monitoring.config.json
# is missing or has no s3_bucket configured. Detached so it never blocks shutdown.
#
# Prefer `uv run --script`: uv reads ship_session.py's inline dependency block
# and provisions boto3 in its own cache (no global install). Fall back to a bare
# python3 if uv isn't on PATH — the shipper then queues the record if boto3 is
# absent and drains it on a later run.
# Locate the shipper: prefer the copy vendored alongside this script (so the hook
# works when it runs straight from the repo's claude_code_monitoring/ — no
# dependency on ~/.claude/hooks being seeded by a dev-container bootstrap), then
# fall back to the machine-level copy.
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
SHIP_SCRIPT="${_HERE}/ship_session.py"
[[ -f "${SHIP_SCRIPT}" ]] || SHIP_SCRIPT="${HOME}/.claude/hooks/ship_session.py"
if [[ -f "${SHIP_SCRIPT}" ]]; then
  if command -v uv >/dev/null 2>&1; then
    ( uv run --script "${SHIP_SCRIPT}" "${RECORD}" \
        >> "${METRICS_DIR}/.ship.log" 2>&1 < /dev/null ) &
    disown 2>/dev/null || true
  elif command -v python3 >/dev/null 2>&1; then
    ( python3 "${SHIP_SCRIPT}" "${RECORD}" \
        >> "${METRICS_DIR}/.ship.log" 2>&1 < /dev/null ) &
    disown 2>/dev/null || true
  fi
fi

rm -f "${STATE_FILE}" 2>/dev/null || true

exit 0
