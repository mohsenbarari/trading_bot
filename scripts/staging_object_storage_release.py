#!/usr/bin/env python3
"""Package metadata, upload, and fetch staging deploy releases via Object Storage.

This is a staging-only bridge for deploy artifacts. It intentionally keeps
runtime sync out of scope. The manifest records the transfer contract so the
pull side can mirror the old direct rsync/scp/docker-save behavior.
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


SCHEMA = "trading_bot_staging_object_storage_release_v1"
DEFAULT_PREFIX = "staging/deploy-bridge"
ALLOWED_ARTIFACTS = {
    "project_payload",
    "frontend_dist",
    "pip_packages",
    "docker_images",
    "nginx_artifact",
    "runtime_env_encrypted",
}


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return slug.strip(".-") or "unknown"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--endpoint", default=os.getenv("ARVAN_OBJECT_STORAGE_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--bucket", default=os.getenv("ARVAN_OBJECT_STORAGE_BUCKET"))
    parser.add_argument("--prefix", default=os.getenv("ARVAN_OBJECT_STORAGE_PREFIX", DEFAULT_PREFIX))
    parser.add_argument("--release-sha", default=os.getenv("STAGING_RELEASE_SHA", "staging"))
    parser.add_argument("--access-key-env", default=DEFAULT_ACCESS_KEY_ENV)
    parser.add_argument("--secret-key-env", default=DEFAULT_SECRET_KEY_ENV)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--execute", action="store_true", help="Perform network writes/reads. Default is dry-run.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Staging Object Storage release bridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    upload = subparsers.add_parser("upload", help="Write a manifest and optionally upload artifacts.")
    add_common_args(upload)
    upload.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Artifact to include. Allowed names: " + ", ".join(sorted(ALLOWED_ARTIFACTS)),
    )
    upload.add_argument("--manifest-out", required=True, type=Path)
    upload.add_argument("--project-exclude", action="append", default=[])
    upload.add_argument("--receiver-protect", action="append", default=[])
    upload.add_argument("--transfer-note", action="append", default=[])

    fetch = subparsers.add_parser("fetch", help="Fetch a release manifest and artifacts.")
    add_common_args(fetch)
    fetch.add_argument("--download-dir", required=True, type=Path)
    fetch.add_argument("--manifest-key")

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


def parse_artifact_specs(specs: list[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"Invalid artifact spec '{spec}', expected NAME=PATH")
        name, raw_path = spec.split("=", 1)
        name = name.strip()
        path = Path(raw_path).expanduser()
        if name not in ALLOWED_ARTIFACTS:
            raise ValueError(f"Unsupported artifact name '{name}'")
        if name in artifacts:
            raise ValueError(f"Duplicate artifact name '{name}'")
        if not path.exists() or not path.is_file():
            raise ValueError(f"Artifact file does not exist: {path}")
        artifacts[name] = path
    if not artifacts:
        raise ValueError("At least one --artifact NAME=PATH is required")
    return artifacts


def build_manifest(args: argparse.Namespace, config: dict[str, str], artifact_paths: dict[str, Path]) -> dict[str, Any]:
    release_slug = safe_slug(args.release_sha)
    manifest_key = f"{config['prefix']}/releases/{release_slug}/manifest.json"
    artifacts: dict[str, Any] = {}
    for name, path in sorted(artifact_paths.items()):
        filename = path.name
        key = f"{config['prefix']}/releases/{release_slug}/{filename}"
        artifacts[name] = {
            "key": key,
            "filename": filename,
            "local_path": str(path.resolve()),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        }

    return {
        "schema": SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "release_sha": args.release_sha,
        "manifest": {"key": manifest_key},
        "artifacts": artifacts,
        "object_storage": {
            "endpoint": config["endpoint"],
            "bucket": config["bucket"],
            "prefix": config["prefix"],
        },
        "transfer_contract": {
            "project_payload_excludes": args.project_exclude,
            "receiver_protected_paths": args.receiver_protect,
            "notes": args.transfer_note,
        },
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def upload_release(args: argparse.Namespace) -> int:
    try:
        config = validate_config(args, require_credentials=args.execute)
        artifact_paths = parse_artifact_specs(args.artifact)
        manifest = build_manifest(args, config, artifact_paths)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2

    write_json(args.manifest_out, manifest)
    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": not args.execute,
        "config": redacted_config(config, args),
        "manifest": manifest["manifest"],
        "artifacts": {
            name: {
                "key": item["key"],
                "size_bytes": item["size_bytes"],
                "sha256": item["sha256"],
            }
            for name, item in manifest["artifacts"].items()
        },
    }
    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    client = s3_client(config, args.timeout)
    upload_summary: dict[str, Any] = {}
    try:
        for name, item in manifest["artifacts"].items():
            path = artifact_paths[name]
            with path.open("rb") as body:
                put_response = client.put_object(
                    Bucket=config["bucket"],
                    Key=item["key"],
                    Body=body,
                    Metadata={
                        "release-sha": safe_slug(str(manifest["release_sha"])),
                        "sha256": str(item["sha256"]),
                        "artifact-name": name,
                        "schema": "staging-release-v1",
                    },
                )
            head_response = client.head_object(Bucket=config["bucket"], Key=item["key"])
            content_length = int(head_response.get("ContentLength") or -1)
            if content_length != int(item["size_bytes"]):
                raise ValueError(f"Uploaded size mismatch for {name}: expected {item['size_bytes']}, got {content_length}")
            upload_summary[name] = {
                "put_status": put_response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
                "head_status": head_response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
                "content_length": content_length,
            }

        manifest_body = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        manifest_put = client.put_object(
            Bucket=config["bucket"],
            Key=manifest["manifest"]["key"],
            Body=manifest_body,
            ContentType="application/json",
            Metadata={"schema": "staging-release-manifest-v1"},
        )
        manifest_head = client.head_object(Bucket=config["bucket"], Key=manifest["manifest"]["key"])
    except ClientError as exc:
        error = exc.response.get("Error", {})
        print(
            json.dumps(
                {**summary, "ok": False, "dry_run": False, "error_code": error.get("Code"), "error_message": error.get("Message")},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {**summary, "ok": False, "dry_run": False, "error_type": type(exc).__name__, "error_message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                **summary,
                "dry_run": False,
                "upload": upload_summary,
                "manifest_upload": {
                    "put_status": manifest_put.get("ResponseMetadata", {}).get("HTTPStatusCode"),
                    "head_status": manifest_head.get("ResponseMetadata", {}).get("HTTPStatusCode"),
                    "content_length": manifest_head.get("ContentLength"),
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def manifest_key_for(config: dict[str, str], args: argparse.Namespace) -> str:
    if args.manifest_key:
        return args.manifest_key
    release_slug = safe_slug(args.release_sha)
    return f"{config['prefix']}/releases/{release_slug}/manifest.json"


def validate_manifest(payload: dict[str, Any]) -> None:
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"Unsupported manifest schema: {payload.get('schema')}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Manifest has no artifacts")
    for name, item in artifacts.items():
        if name not in ALLOWED_ARTIFACTS:
            raise ValueError(f"Unsupported artifact in manifest: {name}")
        for key in ("key", "filename", "size_bytes", "sha256"):
            if key not in item:
                raise ValueError(f"Artifact {name} is missing {key}")


def fetch_release(args: argparse.Namespace) -> int:
    try:
        config = validate_config(args, require_credentials=args.execute)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2

    key = manifest_key_for(config, args)
    release_slug = safe_slug(args.release_sha)
    release_dir = args.download_dir / release_slug
    summary: dict[str, Any] = {
        "ok": True,
        "dry_run": not args.execute,
        "config": redacted_config(config, args),
        "manifest": {"key": key, "download_dir": str(release_dir)},
    }
    if not args.execute:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    client = s3_client(config, args.timeout)
    try:
        release_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = release_dir / "manifest.json"
        client.download_file(config["bucket"], key, str(manifest_path))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)

        downloads: dict[str, Any] = {}
        for name, item in manifest["artifacts"].items():
            local_path = release_dir / item["filename"]
            client.download_file(config["bucket"], item["key"], str(local_path))
            actual_sha = file_sha256(local_path)
            if actual_sha != item["sha256"]:
                raise ValueError(f"SHA256 mismatch for {name}: expected {item['sha256']}, got {actual_sha}")
            if local_path.stat().st_size != int(item["size_bytes"]):
                raise ValueError(f"Size mismatch for {name}: expected {item['size_bytes']}, got {local_path.stat().st_size}")
            downloads[name] = {
                "path": str(local_path),
                "key": item["key"],
                "sha256": actual_sha,
                "size_bytes": local_path.stat().st_size,
            }
    except ClientError as exc:
        error = exc.response.get("Error", {})
        print(
            json.dumps(
                {**summary, "ok": False, "dry_run": False, "error_code": error.get("Code"), "error_message": error.get("Message")},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {**summary, "ok": False, "dry_run": False, "error_type": type(exc).__name__, "error_message": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps({**summary, "dry_run": False, "downloads": downloads}, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "upload":
        return upload_release(args)
    if args.command == "fetch":
        return fetch_release(args)
    print(json.dumps({"ok": False, "error": f"Unknown command: {args.command}"}, ensure_ascii=False, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
