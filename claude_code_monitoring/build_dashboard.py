#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["boto3"]
# ///
"""Regenerate usage_dashboard.html from session telemetry.

Default source: ~/.claude/metrics/usage.jsonl (local).

Set CC_MONITORING_SOURCE=s3 (or pass --source s3) to read all session objects
from the S3 bucket configured in ~/.claude/metrics/monitoring.config.json.

The local source needs no third-party deps, so `python3 build_dashboard.py`
works. The S3 source needs boto3 — run it with `uv run --script
build_dashboard.py --source s3` and uv provisions boto3 from the block above.

Pricing (USD per 1M tokens) is read from the shared S3 bucket for s3/merge
builds — key `pricing.json` at the bucket root (override with `pricing_key` in
the config) — falling back to a local ~/.claude/metrics/pricing.json. A pure
local build reads only the local file. This builder runs centrally, not in dev
containers, which is why repos no longer carry a pricing file of their own.

S3 reads (sessions + pricing) use a dedicated read-only credential at
~/.claude/metrics/.aws-reader-credentials.json if present — created from a
separate IAM user with GetObject/ListBucket and kept only on the build machine,
so the shipping key distributed into every repo can stay write-only. Falls back
to the repo-level shipping creds (walk-up from cwd) when no reader key exists.
Format: {"aws_access_key_id": "...", "aws_secret_access_key": "..."}.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

METRICS = Path.home() / ".claude" / "metrics"
LOG = METRICS / "usage.jsonl"
PRICING = METRICS / "pricing.json"
CONFIG = METRICS / "monitoring.config.json"
HTML = Path(__file__).parent / "usage_dashboard.html"

# Dedicated read-only credential for building the dashboard. Lives only on the
# machine that builds the dashboard — NOT distributed into repos. This lets the
# per-repo shipping key stay write-only (PutObject) while a separate IAM user
# holds GetObject/ListBucket. If absent, we fall back to the repo-level shipping
# creds (single-key setups still work).
READER_CREDS = METRICS / ".aws-reader-credentials.json"

# Bedrock users run Claude Code through application inference profiles, so their
# `model` arrives as an opaque ARN (.../application-inference-profile/<id>) that
# encodes neither the model nor the user. This map (profile id -> {model, name})
# resolves both — the profile NAME typically carries the owner (e.g.
# "opus-4-6-esteban"). Regenerate it from Bedrock with:
#   aws bedrock list-inference-profiles --type-equals APPLICATION
# (see the writer note in the repo). Absent -> ARNs are left as-is.
BEDROCK_PROFILES = METRICS / "bedrock_profiles.json"
_PROFILE_RE = re.compile(r"application-inference-profile/([a-z0-9]+)")

KEEP = {
    "session_id", "source", "model", "cwd",
    "repo_root", "repo_remote", "onboarded",
    "start_ts", "start_iso", "end_ts", "end_iso",
    "duration_secs", "reason",
    "input_tokens", "output_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens",
    "peak_context_tokens",
    "account_email", "account_name", "org_name",
    "inference_profile",
}


def _load_profiles() -> dict:
    try:
        data = json.loads(BEDROCK_PROFILES.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


_PROFILES = _load_profiles()


def resolve_model(model):
    """Map a Bedrock inference-profile ARN to (real_model, profile_name). The
    '[1m]' context suffix, if present on the ARN, is preserved so these group
    with directly-reported models like 'claude-opus-4-8[1m]'. Non-ARN models
    pass through unchanged."""
    if not model:
        return model, None
    m = _PROFILE_RE.search(model)
    if not m:
        return model, None
    info = _PROFILES.get(m.group(1))
    if not info:
        return model, None
    resolved = info.get("model", model)
    if model.rstrip().endswith("[1m]") and not resolved.endswith("[1m]"):
        resolved += "[1m]"
    return resolved, info.get("name")


def project(obj: dict) -> dict:
    row = {k: obj.get(k) for k in KEEP}
    row["model"], row["inference_profile"] = resolve_model(row.get("model"))
    return row


def load_local() -> list[dict]:
    if not LOG.exists():
        return []
    rows: list[dict] = []
    with LOG.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(project(json.loads(line)))
            except json.JSONDecodeError as e:
                print(f"warn: skipping local line {i}: {e}", file=sys.stderr)
    return rows


def _find_credentials(start: Path) -> dict | None:
    """Walk up from `start` to the gitignored repo-level AWS credentials file
    (<repo>/claude_code_monitoring/.aws-credentials.json). No ~/.aws fallback."""
    for d in (start, *start.parents):
        f = d / "claude_code_monitoring" / ".aws-credentials.json"
        if f.is_file():
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                return None
            if data.get("aws_access_key_id") and data.get("aws_secret_access_key"):
                return {"aws_access_key_id": data["aws_access_key_id"],
                        "aws_secret_access_key": data["aws_secret_access_key"]}
            return None
    return None


def _reader_credentials() -> dict | None:
    """Credentials for READING the bucket. Prefer the dedicated read-only key at
    READER_CREDS so the distributed shipping key never needs GetObject; fall back
    to the repo-level shipping creds (walk-up from cwd) for single-key setups."""
    if READER_CREDS.is_file():
        try:
            data = json.loads(READER_CREDS.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        if data.get("aws_access_key_id") and data.get("aws_secret_access_key"):
            return {"aws_access_key_id": data["aws_access_key_id"],
                    "aws_secret_access_key": data["aws_secret_access_key"]}
    return _find_credentials(Path.cwd())


def load_s3() -> list[dict]:
    if not CONFIG.exists():
        sys.exit(f"S3 source requested but {CONFIG} not found")
    cfg = json.loads(CONFIG.read_text())
    bucket = cfg.get("s3_bucket")
    if not bucket:
        sys.exit("S3 source requested but s3_bucket not set in config")
    prefix = cfg.get("s3_prefix", "sessions").strip("/") + "/"

    try:
        import boto3
    except ImportError:
        sys.exit("S3 source requires boto3 — run via `uv run --script build_dashboard.py --source s3`")

    # Read-only reader key if present, else the repo-level shipping creds. Never
    # `aws configure` / the default chain.
    creds = _reader_credentials()
    if creds is None:
        sys.exit("no credentials found — expected a read-only key at "
                 f"{READER_CREDS} or a repo-level claude_code_monitoring/"
                 ".aws-credentials.json")
    s3 = boto3.session.Session(
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
        region_name=cfg.get("aws_region") or None,
    ).client("s3")

    rows: list[dict] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".json"):
                continue
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            try:
                rows.append(project(json.loads(body)))
            except json.JSONDecodeError as e:
                print(f"warn: skipping s3 key {key}: {e}", file=sys.stderr)
    return rows


def fetch_pricing_s3() -> dict | None:
    """Load pricing from the shared S3 bucket (uploaded centrally — the canonical
    copy). Key defaults to 'pricing.json' at the bucket root, overridable via
    `pricing_key` in the config. Best-effort: returns None on any problem so the
    caller can fall back to a local pricing file."""
    if not CONFIG.exists():
        return None
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    bucket = cfg.get("s3_bucket")
    if not bucket:
        return None
    key = cfg.get("pricing_key", "pricing.json")
    try:
        import boto3
        creds = _reader_credentials()
        if creds is None:
            return None
        s3 = boto3.session.Session(
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
            region_name=cfg.get("aws_region") or None,
        ).client("s3")
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except Exception as e:  # noqa: BLE001 — fall back to local on any failure
        print(f"warn: could not read s3://{bucket}/{key} ({type(e).__name__}); "
              f"falling back to {PRICING}", file=sys.stderr)
        return None


def load_pricing(source: str) -> dict:
    """Pricing now lives in S3. For s3/merge builds, read it from the bucket and
    fall back to the local file; a pure-local build reads only the local file."""
    local = json.loads(PRICING.read_text()) if PRICING.exists() else {}
    if source == "local":
        return local
    remote = fetch_pricing_s3()
    return remote if remote is not None else local


def dedupe(rows: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for r in rows:
        sid = r.get("session_id")
        if not sid:
            continue
        seen[sid] = r  # last write wins; S3 + local should agree on session_id keys
    return list(seen.values())


def replace_block(html: str, marker: str, new_value: str) -> str:
    pattern = re.compile(rf"const {marker} = .*?;\s*//\s*<<{marker}_END>>", re.DOTALL)
    if not pattern.search(html):
        sys.exit(f"could not find '{marker}' block in dashboard")
    return pattern.sub(f"const {marker} = {new_value}; // <<{marker}_END>>", html)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=("local", "s3", "merge"),
        default=os.environ.get("CC_MONITORING_SOURCE", "local"),
        help="Where to read session data from (default: local, or $CC_MONITORING_SOURCE)",
    )
    args = parser.parse_args()

    if not HTML.exists():
        sys.exit(f"dashboard not found: {HTML}")

    if args.source == "local":
        rows = load_local()
    elif args.source == "s3":
        rows = load_s3()
    else:  # merge
        rows = dedupe(load_local() + load_s3())

    if not rows and args.source == "local":
        sys.exit(f"log not found or empty: {LOG}")

    data_js = ",\n  ".join(json.dumps(r, separators=(",", ":")) for r in rows)
    data_value = f"[\n  {data_js}\n]" if rows else "[]"

    pricing = load_pricing(args.source)
    pricing_value = json.dumps(pricing, indent=2)

    built_value = json.dumps(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

    html = HTML.read_text()
    html = replace_block(html, "DATA", data_value)
    html = replace_block(html, "PRICING", pricing_value)
    html = replace_block(html, "BUILT_AT", built_value)
    HTML.write_text(html)
    print(f"updated {HTML} with {len(rows)} sessions (source={args.source})")


if __name__ == "__main__":
    main()
