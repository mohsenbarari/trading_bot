#!/usr/bin/env python3
"""Manual Arvan Object Storage probe for foreign staging experiments.

The probe is intentionally isolated from production deploy and sync paths. It
reads credentials from environment variables, prints only redacted summaries,
and defaults to dry-run mode unless --execute is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


DEFAULT_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
DEFAULT_PREFIX = "staging-probe/foreign-only"
DEFAULT_ACCESS_KEY_ENV = "ARVAN_OBJECT_STORAGE_ACCESS_KEY"
DEFAULT_SECRET_KEY_ENV = "ARVAN_OBJECT_STORAGE_SECRET_KEY"
ALLOWED_PREFIX_ROOTS = ("staging/", "staging-probe/")


def mask_secret(value: str | None, *, visible_tail: int = 4) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return ""
    if len(text) <= visible_tail:
        return "*" * len(text)
    return f"{'*' * (len(text) - visible_tail)}{text[-visible_tail:]}"


def normalize_prefix(prefix: str) -> str:
    normalized = str(prefix or "").strip().strip("/")
    if not normalized:
        raise ValueError("Object prefix is required")
    if not any(normalized == root.rstrip("/") or normalized.startswith(root) for root in ALLOWED_PREFIX_ROOTS):
        allowed = ", ".join(root.rstrip("/") for root in ALLOWED_PREFIX_ROOTS)
        raise ValueError(f"Refusing non-staging prefix {normalized!r}; expected one of: {allowed}")
    return normalized


def build_object_key(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{normalize_prefix(prefix)}/{timestamp}-{os.getpid()}.txt"


def build_probe_body() -> bytes:
    return (
        "trading-bot foreign staging arvan object storage probe\n"
        f"created_at={datetime.now(timezone.utc).isoformat()}\n"
    ).encode("utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe Arvan Object Storage from the foreign staging surface without touching production flows."
    )
    parser.add_argument("--endpoint", default=os.getenv("ARVAN_OBJECT_STORAGE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--bucket", default=os.getenv("ARVAN_OBJECT_STORAGE_BUCKET"))
    parser.add_argument("--prefix", default=os.getenv("ARVAN_OBJECT_STORAGE_PREFIX", DEFAULT_PREFIX))
    parser.add_argument("--access-key-env", default=DEFAULT_ACCESS_KEY_ENV)
    parser.add_argument("--secret-key-env", default=DEFAULT_SECRET_KEY_ENV)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--keep-object", action="store_true", help="Leave the probe object in the bucket.")
    parser.add_argument("--execute", action="store_true", help="Actually run PUT/GET/LIST/DELETE. Default is dry-run.")
    return parser.parse_args(argv)


def validate_config(args: argparse.Namespace, *, require_credentials: bool) -> dict[str, str]:
    endpoint = str(args.endpoint or "").strip().rstrip("/")
    bucket = str(args.bucket or "").strip()
    prefix = normalize_prefix(args.prefix)
    access_key = os.getenv(args.access_key_env)
    secret_key = os.getenv(args.secret_key_env)

    missing = []
    if not endpoint:
        missing.append("endpoint")
    if not bucket:
        missing.append("bucket")
    if require_credentials and not access_key:
        missing.append(args.access_key_env)
    if require_credentials and not secret_key:
        missing.append(args.secret_key_env)
    if missing:
        raise ValueError(f"Missing required values: {', '.join(missing)}")

    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "prefix": prefix,
        "access_key": access_key or "",
        "secret_key": secret_key or "",
    }


def redacted_config_summary(args: argparse.Namespace, config: dict[str, str]) -> dict[str, Any]:
    return {
        "endpoint": config["endpoint"],
        "bucket": config["bucket"],
        "prefix": config["prefix"],
        "access_key_env": args.access_key_env,
        "secret_key_env": args.secret_key_env,
        "access_key_present": bool(config["access_key"]),
        "secret_key_present": bool(config["secret_key"]),
        "access_key_hint": mask_secret(config["access_key"]) if config["access_key"] else None,
    }


def s3_client(config_values: dict[str, str], timeout: float):
    return boto3.client(
        "s3",
        endpoint_url=config_values["endpoint"],
        aws_access_key_id=config_values["access_key"],
        aws_secret_access_key=config_values["secret_key"],
        config=Config(
            signature_version="s3v4",
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )


def run_probe(args: argparse.Namespace, config_values: dict[str, str]) -> dict[str, Any]:
    client = s3_client(config_values, args.timeout)
    key = build_object_key(config_values["prefix"])
    body = build_probe_body()
    expected_sha = hashlib.sha256(body).hexdigest()
    report: dict[str, Any] = {
        "status": "running",
        "bucket": config_values["bucket"],
        "endpoint": config_values["endpoint"],
        "object_key": key,
        "keep_object": bool(args.keep_object),
    }

    put_response = client.put_object(
        Bucket=config_values["bucket"],
        Key=key,
        Body=body,
        ContentType="text/plain",
    )
    report["put_status"] = put_response.get("ResponseMetadata", {}).get("HTTPStatusCode")

    get_response = client.get_object(Bucket=config_values["bucket"], Key=key)
    got = get_response["Body"].read()
    report["get_status"] = get_response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    report["bytes"] = len(got)
    report["sha256_match"] = hashlib.sha256(got).hexdigest() == expected_sha

    list_response = client.list_objects_v2(
        Bucket=config_values["bucket"],
        Prefix=config_values["prefix"] + "/",
        MaxKeys=10,
    )
    report["list_status"] = list_response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    report["list_key_count"] = len(list_response.get("Contents", []) or [])

    if args.keep_object:
        report["deleted"] = False
    else:
        delete_response = client.delete_object(Bucket=config_values["bucket"], Key=key)
        report["delete_status"] = delete_response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        verify_response = client.list_objects_v2(Bucket=config_values["bucket"], Prefix=key, MaxKeys=1)
        report["deleted"] = len(verify_response.get("Contents", []) or []) == 0

    report["status"] = "ok" if report.get("sha256_match") and (args.keep_object or report.get("deleted")) else "failed"
    return report


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config_values = validate_config(args, require_credentials=args.execute)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2

    summary = {
        "ok": True,
        "dry_run": not args.execute,
        "config": redacted_config_summary(args, config_values),
    }
    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    try:
        report = run_probe(args, config_values)
    except ClientError as exc:
        error = exc.response.get("Error", {})
        print(
            json.dumps(
                {
                    **summary,
                    "ok": False,
                    "dry_run": False,
                    "error_code": error.get("Code"),
                    "error_message": error.get("Message"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    **summary,
                    "ok": False,
                    "dry_run": False,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    ok = report.get("status") == "ok"
    print(json.dumps({**summary, "ok": ok, "dry_run": False, "probe": report}, ensure_ascii=False, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
