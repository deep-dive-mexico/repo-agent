#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["boto3"]
# ///
"""Async S3 uploader for Claude Code session telemetry.

**No third-party dependencies required.** If boto3 is importable (e.g. run via
`uv run --script`, which provisions it from the inline block above) it's used;
otherwise the S3 PutObject is signed with AWS Signature V4 using only the Python
standard library (hmac/hashlib/urllib). So a bare `python3` ships successfully —
no `uv`, no boto3, nothing for a developer to install — which means the retry
queue actually drains instead of waiting forever for a dependency that never
arrives.

Reads ~/.claude/metrics/monitoring.config.json for the bucket/prefix/region. If
no s3_bucket is configured, exits 0. Otherwise:

1. Drains the retry queue (~/.claude/metrics/.retry_queue.jsonl)
2. Ships the JSON record passed on argv[1] (or stdin if no argv)

AWS credentials are NOT taken from the default chain / `aws configure` / ~/.aws.
They live per-repo in a gitignored file:
    <repo>/claude_code_monitoring/.aws-credentials.json
    {"aws_access_key_id": "...", "aws_secret_access_key": "..."}
Each session record carries its repo `cwd`; the shipper walks up from there to
find that file and passes the keys to boto3 explicitly. A record with no
resolvable credentials file is left queued (and drains once creds are added).

Each session is uploaded to:
    s3://<bucket>/<prefix>/<account_email>/<end_iso>_<session_id>.json

Idempotent: re-shipping the same session_id overwrites the same key.

Failures are appended to the retry queue and drained on the next invocation.
Never raises — exit code is always 0 so this can't break the session-end hook.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import hmac
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# boto3 is OPTIONAL: if it's importable (e.g. run via `uv run --script`) we use
# it; otherwise we sign the S3 request ourselves with stdlib (SigV4) so shipping
# works on a bare python3 — no uv, no boto3, nothing for a developer to install.
try:
    import boto3 as _BOTO3
except Exception:  # noqa: BLE001
    _BOTO3 = None

METRICS = Path.home() / ".claude" / "metrics"
CONFIG = METRICS / "monitoring.config.json"
RETRY_QUEUE = METRICS / ".retry_queue.jsonl"
SHIP_LOG = METRICS / ".ship.log"

# Per-repo, gitignored credentials file (relative to a repo root). Resolved by
# walking up from each session record's `cwd` — never from ~/.aws.
CREDS_RELPATH = Path("claude_code_monitoring") / ".aws-credentials.json"

# Machine-level fallback, seeded by the dev-container bootstrap. Used when the
# walk-up from `cwd` can't reach a repo creds file — e.g. Claude is launched from
# a parent of the onboarded repo (monorepo / multi-root workspace), so the
# session's cwd is above (or beside) the repo that holds the per-repo creds.
METRICS_CREDS = METRICS / ".aws-credentials.json"



def log(msg: str) -> None:
    SHIP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with SHIP_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


def load_config() -> dict | None:
    if not CONFIG.exists():
        return None
    try:
        cfg = json.loads(CONFIG.read_text())
    except json.JSONDecodeError as e:
        log(f"config parse error: {e}")
        return None
    if not cfg.get("s3_bucket"):
        return None
    return cfg


def _read_creds(f: Path) -> dict | None:
    try:
        data = json.loads(f.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log(f"credentials parse error at {f}: {e}")
        return None
    ak = data.get("aws_access_key_id")
    sk = data.get("aws_secret_access_key")
    if ak and sk:
        return {"aws_access_key_id": ak, "aws_secret_access_key": sk}
    log(f"credentials file missing keys: {f}")
    return None


def find_credentials(cwd) -> dict | None:
    """Resolve a session's AWS credentials. Tries, in order:

    1. Walk up from the session's `cwd` to a repo-level
       claude_code_monitoring/.aws-credentials.json (the per-repo creds).
    2. The machine-level fallback (~/.claude/metrics/.aws-credentials.json),
       seeded by the dev-container bootstrap — this is what lets sessions run
       from a parent of the repo, a sibling dir, or entirely outside any
       onboarded project still ship.

    Deliberately never falls back to the default AWS chain / `aws configure`.
    Returns {aws_access_key_id, aws_secret_access_key} or None.
    """
    if cwd:
        try:
            start = Path(cwd)
        except (TypeError, ValueError):
            start = None
        if start is not None:
            for d in (start, *start.parents):
                f = d / CREDS_RELPATH
                if f.is_file():
                    creds = _read_creds(f)
                    if creds:
                        return creds
                    break  # found but unusable — try the machine-level fallback
    if METRICS_CREDS.is_file():
        return _read_creds(METRICS_CREDS)
    return None


def ship_record(bucket: str, prefix: str, region, record: dict, raw: str) -> bool:
    """Upload one record using its repo-level credentials. Returns True on success.
    A record whose creds can't be resolved is treated as a (retryable) failure."""
    creds = find_credentials(record.get("cwd"))
    if creds is None:
        log(f"no repo credentials for cwd={record.get('cwd')!r}; "
            f"queueing session {record.get('session_id')}")
        return False
    return _put(bucket, region, build_key(prefix, record), raw.encode("utf-8"), creds)


def build_key(prefix: str, record: dict) -> str:
    email = record.get("account_email") or "unknown"
    session_id = record.get("session_id") or "unknown"
    end_iso = record.get("end_iso") or "unknown"
    safe_email = email.replace("/", "_")
    safe_id = session_id.replace("/", "_")
    safe_iso = end_iso.replace(":", "").replace("/", "_")
    prefix = prefix.strip("/")
    return f"{prefix}/{safe_email}/{safe_iso}_{safe_id}.json"


def _put(bucket: str, region, key: str, body: bytes, creds: dict) -> bool:
    """Upload one object. Uses boto3 if available, else a stdlib SigV4 PUT."""
    if _BOTO3 is not None:
        try:
            _BOTO3.session.Session(
                aws_access_key_id=creds["aws_access_key_id"],
                aws_secret_access_key=creds["aws_secret_access_key"],
                region_name=region or None,
            ).client("s3").put_object(
                Bucket=bucket, Key=key, Body=body, ContentType="application/json")
            return True
        except Exception as e:  # noqa: BLE001
            log(f"put (boto3) failed key={key}: {type(e).__name__}: {e}")
            return False
    try:
        return _sigv4_put(bucket, region or "us-east-1", key, body, creds)
    except urllib.error.HTTPError as e:  # noqa: BLE001
        detail = ""
        try:
            detail = e.read()[:300].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        log(f"put (sigv4) HTTP {e.code} key={key}: {detail}")
        return False
    except Exception as e:  # noqa: BLE001 — network/SSL/etc — retryable
        log(f"put (sigv4) failed key={key}: {type(e).__name__}: {e}")
        return False


def _sigv4_put(bucket: str, region: str, key: str, body: bytes, creds: dict) -> bool:
    """S3 PutObject signed with AWS Signature V4 using only the standard library —
    so the shipper needs neither boto3 nor uv. Virtual-hosted-style endpoint;
    long-lived IAM user keys (no session token)."""
    ak = creds["aws_access_key_id"]
    sk = creds["aws_secret_access_key"]
    host = f"{bucket}.s3.{region}.amazonaws.com"
    canonical_uri = "/" + "/".join(urllib.parse.quote(seg, safe="") for seg in key.split("/"))
    now = datetime.datetime.now(datetime.timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    canonical_headers = (f"host:{host}\n"
                         f"x-amz-content-sha256:{payload_hash}\n"
                         f"x-amz-date:{amzdate}\n")
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = (f"PUT\n{canonical_uri}\n\n{canonical_headers}\n"
                         f"{signed_headers}\n{payload_hash}")
    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = (f"AWS4-HMAC-SHA256\n{amzdate}\n{scope}\n"
                      f"{hashlib.sha256(canonical_request.encode()).hexdigest()}")

    def _h(key_: bytes, msg: str) -> bytes:
        return hmac.new(key_, msg.encode(), hashlib.sha256).digest()

    k = _h(("AWS4" + sk).encode(), datestamp)
    k = _h(k, region)
    k = _h(k, "s3")
    k = _h(k, "aws4_request")
    signature = hmac.new(k, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (f"AWS4-HMAC-SHA256 Credential={ak}/{scope}, "
                     f"SignedHeaders={signed_headers}, Signature={signature}")
    req = urllib.request.Request(
        f"https://{host}{canonical_uri}", data=body, method="PUT",
        headers={
            "x-amz-date": amzdate,
            "x-amz-content-sha256": payload_hash,
            "Authorization": authorization,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20, context=_ssl_context()) as r:
        return 200 <= r.status < 300


def _ssl_context() -> ssl.SSLContext:
    """System default CA (works in Linux containers). If this Python has no usable
    CA store (some macOS framework builds), fall back to certifi or a common CA
    bundle path so the stdlib uploader can still verify TLS."""
    ctx = ssl.create_default_context()
    try:
        if ctx.cert_store_stats().get("x509_ca", 0) > 0:
            return ctx
    except Exception:  # noqa: BLE001
        return ctx
    candidates = []
    try:
        import certifi  # noqa: PLC0415
        candidates.append(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    candidates += [
        "/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt", "/usr/local/etc/openssl/cert.pem",
    ]
    for ca in candidates:
        try:
            if ca and Path(ca).is_file():
                c = ssl.create_default_context(cafile=ca)
                if c.cert_store_stats().get("x509_ca", 0) > 0:
                    return c
        except Exception:  # noqa: BLE001
            continue
    return ctx


@contextlib.contextmanager
def queue_lock():
    RETRY_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = RETRY_QUEUE.with_suffix(".lock")
    with lock_path.open("a+") as lf:
        try:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def drain_retry_queue(bucket: str, prefix: str, region) -> None:
    if not RETRY_QUEUE.exists():
        return
    with queue_lock():
        try:
            lines = [l for l in RETRY_QUEUE.read_text().splitlines() if l.strip()]
        except OSError as e:
            log(f"queue read error: {e}")
            return
        if not lines:
            return
        remaining: list[str] = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log("dropping malformed queue line")
                continue
            if not ship_record(bucket, prefix, region, record, line):
                remaining.append(line)
        if remaining:
            RETRY_QUEUE.write_text("\n".join(remaining) + "\n")
            log(f"drain: {len(lines) - len(remaining)} ok, {len(remaining)} still queued")
        else:
            RETRY_QUEUE.unlink(missing_ok=True)
            log(f"drain: all {len(lines)} flushed")


def enqueue(line: str) -> None:
    with queue_lock():
        with RETRY_QUEUE.open("a") as f:
            f.write(line.rstrip("\n") + "\n")


def main() -> int:
    cfg = load_config()
    if cfg is None:
        return 0  # S3 shipping disabled — no-op

    payload = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    payload = payload.strip()

    bucket = cfg["s3_bucket"]
    prefix = cfg.get("s3_prefix", "sessions")
    region = cfg.get("aws_region")

    # No boto3 gate any more — _put signs with stdlib SigV4 when boto3 is absent,
    # so a bare python3 ships fine and the retry queue actually drains.
    drain_retry_queue(bucket, prefix, region)

    if not payload:
        return 0

    try:
        record = json.loads(payload)
    except json.JSONDecodeError as e:
        log(f"argv payload not JSON: {e}")
        return 0

    if not ship_record(bucket, prefix, region, record, payload):
        enqueue(payload)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — never propagate
        log(f"fatal: {type(e).__name__}: {e}")
        sys.exit(0)
