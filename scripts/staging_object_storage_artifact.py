#!/usr/bin/env python3
"""Upload a staging deploy artifact to Arvan Object Storage.

This helper is intentionally staging-only. It does not know how to deploy,
apply sync, or touch production. It uploads an already-built tarball plus a
small JSON manifest under a staging prefix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.arvan_object_storage_probe import (
    DEFAULT_ACCESS_KEY_ENV,
    DEFAULT_ENDPOINT,
    DEFAULT_SECRET_KEY_ENV,
    mask_secret,
    normalize_prefix,
)


DEFAULT_PREFIX = "staging/deploy-bridge"


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return slug.strip(".-") or "unknown"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload a staging deploy artifact to Object Storage.")
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--endpoint", default=os.getenv("ARVAN_OBJECT_STORAGE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--bucket", default=os.getenv("ARVAN_OBJECT_STORAGE_BUCKET"))
    parser.add_argument("--prefix", default=os.getenv("ARVAN_OBJECT_STORAGE_PREFIX", DEFAULT_PREFIX))
    parser.add_argument("--release-sha", default=os.getenv("STAGING_RELEASE_SHA", "staging"))
    parser.add_argument("--access-key-env", default=DEFAULT_ACCESS_KEY_ENV)
    parser.add_argument("--secret-key-env", default=DEFAULT_SECRET_KEY_ENV)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--execute", action="store_true", help="Actually upload. Default is dry-run.")
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace, *, require_credentials: bool) -> dict[str, str]:
    artifact = args.artifact
    if not artifact.exists() or not artifact.is_file():
        raise ValueError(f"Artifact file does not exist: {artifact}")
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


def build_manifest(args: argparse.Namespace, config: dict[str, str]) -> dict[str, Any]:
    artifact = args.artifact.resolve()
    release_slug = safe_slug(args.release_sha)
    artifact_name = f"trading-bot-staging-{release_slug}.tar.gz"
    artifact_key = f"{config['prefix']}/releases/{release_slug}/{artifact_name}"
    manifest_key = f"{config['prefix']}/releases/{release_slug}/manifest.json"
    return {
        "schema": "trading_bot_staging_object_storage_artifact_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_sha": args.release_sha,
        "artifact": {
            "local_path": str(artifact),
            "key": artifact_key,
            "size_bytes": artifact.stat().st_size,
            "sha256": file_sha256(artifact),
        },
        "manifest": {
            "key": manifest_key,
        },
        "object_storage": {
            "endpoint": config["endpoint"],
            "bucket": config["bucket"],
            "prefix": config["prefix"],
        },
    }


def redacted_config(config: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
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


def write_manifest(path: Path | None, manifest: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def s3_client(config: dict[str, str], timeout: float):
    return boto3.client(
        "s3",
        endpoint_url=config["endpoint"],
        aws_access_key_id=config["access_key"],
        aws_secret_access_key=config["secret_key"],
        config=Config(
            signature_version="s3v4",
            connect_timeout=timeout,
            read_timeout=timeout,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
    )


def upload_artifact(args: argparse.Namespace, config: dict[str, str], manifest: dict[str, Any]) -> dict[str, Any]:
    client = s3_client(config, args.timeout)
    artifact_info = manifest["artifact"]
    manifest_info = manifest["manifest"]
    manifest_body = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

    with args.artifact.open("rb") as body:
        artifact_response = client.put_object(
            Bucket=config["bucket"],
            Key=artifact_info["key"],
            Body=body,
            ContentType="application/gzip",
            Metadata={
                "release-sha": safe_slug(str(manifest["release_sha"])),
                "sha256": str(artifact_info["sha256"]),
                "schema": "staging-artifact-v1",
            },
        )
    manifest_response = client.put_object(
        Bucket=config["bucket"],
        Key=manifest_info["key"],
        Body=manifest_body,
        ContentType="application/json",
        Metadata={"schema": "staging-artifact-manifest-v1"},
    )
    artifact_head = client.head_object(Bucket=config["bucket"], Key=artifact_info["key"])
    manifest_head = client.head_object(Bucket=config["bucket"], Key=manifest_info["key"])
    return {
        "artifact_put_status": artifact_response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
        "manifest_put_status": manifest_response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
        "artifact_head_status": artifact_head.get("ResponseMetadata", {}).get("HTTPStatusCode"),
        "manifest_head_status": manifest_head.get("ResponseMetadata", {}).get("HTTPStatusCode"),
        "artifact_content_length": artifact_head.get("ContentLength"),
        "manifest_content_length": manifest_head.get("ContentLength"),
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = validate_args(args, require_credentials=args.execute)
        manifest = build_manifest(args, config)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2

    write_manifest(args.manifest_out, manifest)
    summary = {
        "ok": True,
        "dry_run": not args.execute,
        "config": redacted_config(config, args),
        "artifact": {
            "key": manifest["artifact"]["key"],
            "size_bytes": manifest["artifact"]["size_bytes"],
            "sha256": manifest["artifact"]["sha256"],
        },
        "manifest": manifest["manifest"],
    }
    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    try:
        upload = upload_artifact(args, config, manifest)
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

    print(json.dumps({**summary, "dry_run": False, "upload": upload}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
