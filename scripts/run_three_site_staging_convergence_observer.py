#!/usr/bin/env python3
"""Collect and build initial routing-hold convergence evidence safely.

Finland site snapshots return through the approved Finland command path.  The
redacted WebApp-IR snapshot never returns through SSH: a one-shot exporter
uploads it to the private, versioned Arvan bucket using a short-lived presigned
PUT URL, and this controller performs a version-pinned read-back before any
evidence is built.  The resulting files are suitable for the existing private
routing-hold coordinator; this command never enables a public route.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import read_secure_text, write_secure_atomic_bytes
from scripts.build_three_site_staging_convergence_evidence import (
    SITES,
    SHA256,
    SHA40,
    ConvergenceEvidenceError,
    build,
)
from scripts.publish_wa_ir_object_storage_preflight import (
    _client,
    require_private_versioned_bucket,
)
from scripts.publish_wa_ir_object_storage_transfer import _config as object_storage_config
from scripts.run_three_site_sync_timing_observer import (
    SITE_PROFILE,
    SITE_SERVICE,
    _run_json,
    _secure_regular_file,
    _ssh_base,
)
from scripts.verify_three_site_staging_inventory import verify_inventory


CONFIG_SCHEMA = "three-site-staging-convergence-observer-config-v1"
EXPORT_SCHEMA = "three-site-staging-convergence-snapshot-export-v1"
WEBAPP_IR = "webapp_ir"
MAX_REMOTE_SNAPSHOT_BYTES = 16 * 1024 * 1024
SAFE_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,62}$")
SAFE_PATH = re.compile(r"^/[A-Za-z0-9_./-]{1,511}$")


class ConvergenceObserverError(RuntimeError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ConvergenceObserverError("convergence observer JSON has duplicate fields")
        result[key] = value
    return result


def _secure_json(path: Path, *, label: str, max_size: int) -> dict[str, Any]:
    try:
        value = json.loads(
            read_secure_text(path, label=label, max_size=max_size),
            object_pairs_hook=_strict_object,
        )
    except Exception as exc:
        raise ConvergenceObserverError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ConvergenceObserverError(f"{label} must be a JSON object")
    return value


def _private_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ConvergenceObserverError(f"{label} path is unsafe")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise ConvergenceObserverError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ConvergenceObserverError(f"{label} must be an owner-only directory")
    return path.resolve()


def _inventory(binding: Any, *, campaign_id: str, release_sha: str) -> dict[str, Any]:
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
        raise ConvergenceObserverError("convergence observer inventory binding is invalid")
    path = Path(str(binding["path"]))
    payload = _secure_regular_file(path, private=True, label="provisioned staging inventory")
    if (
        SHA256.fullmatch(str(binding["sha256"])) is None
        or hashlib.sha256(payload).hexdigest() != binding["sha256"]
    ):
        raise ConvergenceObserverError("convergence observer inventory hash differs")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_strict_object)
        verified = verify_inventory(value, host_destructive=None)
    except Exception as exc:
        raise ConvergenceObserverError("convergence observer inventory is invalid") from exc
    if (
        value.get("inventory_stage") != "provisioned"
        or value.get("campaign_id") != campaign_id
        or verified.get("release_sha") != release_sha
    ):
        raise ConvergenceObserverError("convergence observer inventory identity differs")
    return value


def _validate_sites(value: Any, *, inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(SITES):
        raise ConvergenceObserverError("convergence observer requires exactly three sites")
    inventory_hosts = {
        str(item["role"]): str(item["host_ip"])
        for item in inventory["roles"]
        if item["role"] in SITES
    }
    output: dict[str, dict[str, Any]] = {}
    for site in SITES:
        item = value[site]
        if not isinstance(item, dict) or set(item) != {
            "host", "port", "user", "repo_root", "compose_file", "env_file"
        }:
            raise ConvergenceObserverError(f"{site} observer site config is invalid")
        if str(item["host"]) != inventory_hosts.get(site):
            raise ConvergenceObserverError(f"{site} observer host differs from signed inventory")
        if type(item["port"]) is not int or not 1 <= item["port"] <= 65535:
            raise ConvergenceObserverError(f"{site} observer SSH port is invalid")
        if SAFE_NAME.fullmatch(str(item["user"])) is None:
            raise ConvergenceObserverError(f"{site} observer SSH user is invalid")
        repo_root = str(item["repo_root"]).rstrip("/")
        for key in ("repo_root", "compose_file", "env_file"):
            if SAFE_PATH.fullmatch(str(item[key])) is None or ".." in Path(str(item[key])).parts:
                raise ConvergenceObserverError(f"{site} observer remote path is unsafe")
        if str(item["compose_file"]) != f"{repo_root}/deploy/staging/docker-compose.three-site.yml":
            raise ConvergenceObserverError(f"{site} observer Compose path is not canonical")
        output[site] = dict(item)
    return output


def load_config(path: Path, *, campaign_id: str, release_sha: str, plan_sha256: str, output: Path) -> dict[str, Any]:
    config = _secure_json(path, label="convergence observer config", max_size=1024 * 1024)
    fields = {
        "schema", "campaign_id", "release_sha", "plan_sha256", "production_forbidden",
        "artifact_root", "inventory", "ssh", "sites", "object_storage", "limits",
    }
    if (
        set(config) != fields
        or config.get("schema") != CONFIG_SCHEMA
        or config.get("campaign_id") != campaign_id
        or config.get("release_sha") != release_sha
        or config.get("plan_sha256") != plan_sha256
        or config.get("production_forbidden") is not True
    ):
        raise ConvergenceObserverError("convergence observer identity/scope is invalid")
    root = _private_directory(Path(str(config["artifact_root"])), label="convergence artifact root")
    if output.resolve().parent != root:
        raise ConvergenceObserverError("convergence observer output is outside its approved artifact root")
    inventory = _inventory(config["inventory"], campaign_id=campaign_id, release_sha=release_sha)
    config["sites"] = _validate_sites(config["sites"], inventory=inventory)
    ssh = config["ssh"]
    if not isinstance(ssh, dict) or set(ssh) != {
        "binary", "identity_file", "known_hosts_file", "connect_timeout_seconds"
    }:
        raise ConvergenceObserverError("convergence observer SSH config is invalid")
    if ssh["binary"] != "/usr/bin/ssh":
        raise ConvergenceObserverError("convergence observer requires the fixed OpenSSH client")
    _secure_regular_file(Path(ssh["binary"]), private=False, label="OpenSSH client")
    _secure_regular_file(Path(ssh["identity_file"]), private=True, label="SSH identity")
    _secure_regular_file(Path(ssh["known_hosts_file"]), private=False, label="known_hosts")
    if type(ssh["connect_timeout_seconds"]) is not int or not 1 <= ssh["connect_timeout_seconds"] <= 30:
        raise ConvergenceObserverError("convergence observer SSH timeout is invalid")
    storage = config["object_storage"]
    if not isinstance(storage, dict) or set(storage) != {"transport_config"}:
        raise ConvergenceObserverError("convergence observer Object Storage config is invalid")
    _secure_regular_file(Path(str(storage["transport_config"])), private=True, label="Object Storage transport config")
    limits = config["limits"]
    if not isinstance(limits, dict) or set(limits) != {
        "command_timeout_seconds", "max_rows_per_table", "url_ttl_seconds"
    }:
        raise ConvergenceObserverError("convergence observer limits are invalid")
    if (
        type(limits["command_timeout_seconds"]) is not int
        or not 10 <= limits["command_timeout_seconds"] <= 900
        or type(limits["max_rows_per_table"]) is not int
        or not 1 <= limits["max_rows_per_table"] <= 100_000
        or type(limits["url_ttl_seconds"]) is not int
        or not 60 <= limits["url_ttl_seconds"] <= 900
    ):
        raise ConvergenceObserverError("convergence observer limits are invalid")
    return config


def _remote_command(config: dict[str, Any], site: str, script: str, *arguments: str) -> list[str]:
    remote = config["sites"][site]
    service = (
        "webapp_ir_convergence_exporter" if site == WEBAPP_IR else SITE_SERVICE[site]
    )
    return [
        *_ssh_base(config, site),
        "/usr/bin/docker", "compose",
        "--project-directory", str(remote["repo_root"]),
        "-f", str(remote["compose_file"]),
        "--env-file", str(remote["env_file"]),
        "--profile", SITE_PROFILE[site],
        "run", "--rm", "--no-deps", "-T", service,
        "python", script, *arguments,
    ]


def _finland_snapshot(config: dict[str, Any], site: str) -> dict[str, Any]:
    return _run_json(
        _remote_command(
            config, site, "scripts/collect_three_site_staging_convergence_snapshot.py",
            "--campaign-id", config["campaign_id"],
            "--release-sha", config["release_sha"],
            "--plan-sha256", config["plan_sha256"],
            "--max-rows-per-table", str(config["limits"]["max_rows_per_table"]),
        ),
        timeout=config["limits"]["command_timeout_seconds"],
        label=f"{site} read-only convergence snapshot",
    )


def _put_descriptor(config: dict[str, Any], *, client, bucket: str, key: str) -> str:  # noqa: ANN001
    try:
        url = client.generate_presigned_url(
            "put_object",
            Params={"Bucket": bucket, "Key": key, "ContentType": "application/json"},
            ExpiresIn=int(config["limits"]["url_ttl_seconds"]),
            HttpMethod="PUT",
        )
    except Exception as exc:
        raise ConvergenceObserverError("cannot issue the short-lived WebApp-IR upload URL") from exc
    payload = {
        "schema": EXPORT_SCHEMA,
        "campaign_id": config["campaign_id"],
        "release_sha": config["release_sha"],
        "plan_sha256": config["plan_sha256"],
        "upload": {
            "url": url,
            "method": "PUT",
            "headers": {"Content-Type": "application/json"},
            "expected_status": [200, 201],
        },
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")


def _iran_snapshot(config: dict[str, Any], *, client, bucket: str, key: str) -> tuple[dict[str, Any], bytes, str]:  # noqa: ANN001
    descriptor = _put_descriptor(config, client=client, bucket=bucket, key=key)
    receipt = _run_json(
        _remote_command(
            config, WEBAPP_IR, "scripts/export_three_site_staging_convergence_snapshot.py",
            "--campaign-id", config["campaign_id"],
            "--release-sha", config["release_sha"],
            "--plan-sha256", config["plan_sha256"],
            "--max-rows-per-table", str(config["limits"]["max_rows_per_table"]),
            "--upload-json-base64", descriptor,
        ),
        timeout=config["limits"]["command_timeout_seconds"],
        label="webapp_ir Object Storage convergence export",
    )
    fields = {
        "status", "site", "campaign_id", "release_sha", "plan_sha256",
        "snapshot_sha256", "snapshot_bytes", "upload_http_status",
    }
    if (
        set(receipt) != fields
        or receipt.get("status") != "uploaded"
        or receipt.get("site") != WEBAPP_IR
        or receipt.get("campaign_id") != config["campaign_id"]
        or receipt.get("release_sha") != config["release_sha"]
        or receipt.get("plan_sha256") != config["plan_sha256"]
        or SHA256.fullmatch(str(receipt.get("snapshot_sha256") or "")) is None
        or type(receipt.get("snapshot_bytes")) is not int
        or not 1 <= int(receipt["snapshot_bytes"]) <= MAX_REMOTE_SNAPSHOT_BYTES
        or int(receipt.get("upload_http_status") or 0) not in {200, 201}
    ):
        raise ConvergenceObserverError("WebApp-IR Object Storage export receipt is invalid")
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        version_id = str(head.get("VersionId") or "")
        size = int(head.get("ContentLength") or 0)
        if not version_id or size != int(receipt["snapshot_bytes"]):
            raise ValueError
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version_id)
        payload = response["Body"].read(MAX_REMOTE_SNAPSHOT_BYTES + 1)
    except Exception as exc:
        raise ConvergenceObserverError("WebApp-IR Object Storage read-back failed") from exc
    digest = hashlib.sha256(payload).hexdigest()
    if len(payload) != size or digest != receipt["snapshot_sha256"]:
        raise ConvergenceObserverError("WebApp-IR Object Storage read-back differs from export receipt")
    try:
        snapshot = json.loads(payload, object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ConvergenceObserverError) as exc:
        raise ConvergenceObserverError("WebApp-IR Object Storage snapshot is invalid JSON") from exc
    if not isinstance(snapshot, dict):
        raise ConvergenceObserverError("WebApp-IR Object Storage snapshot is not an object")
    return snapshot, payload, version_id


def _write_snapshot(root: Path, site: str, snapshot: dict[str, Any]) -> Path:
    directory = root / "raw-snapshots"
    directory.mkdir(mode=0o700, exist_ok=True)
    _private_directory(directory, label="convergence raw-snapshot directory")
    path = directory / f"{site}.json"
    write_secure_atomic_bytes(
        path,
        (json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode(),
        label=f"{site} redacted convergence snapshot",
        mode=0o600,
        max_size=MAX_REMOTE_SNAPSHOT_BYTES,
    )
    return path


def observe(*, config: dict[str, Any], output: Path) -> dict[str, Any]:
    values = object_storage_config(Path(str(config["object_storage"]["transport_config"])))
    credentials = (
        values["ARVAN_S3_ACCESS_KEY"], values["ARVAN_S3_SECRET_KEY"],
        values["ARVAN_S3_ENDPOINT"], values["ARVAN_S3_REGION"],
    )
    client = _client(credentials)
    bucket = values["WA_IR_OBJECT_STORAGE_BUCKET"]
    try:
        require_private_versioned_bucket(client, bucket=bucket)
    except Exception as exc:
        raise ConvergenceObserverError("convergence observation requires a private versioned bucket") from exc
    key = (
        f"{values['WA_IR_OBJECT_STORAGE_PREFIX']}/convergence/"
        f"{config['campaign_id']}/{config['release_sha']}/{uuid4()}.json"
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {site: pool.submit(_finland_snapshot, config, site) for site in ("bot_fi", "webapp_fi")}
        iran_snapshot, _iran_payload, iran_version = _iran_snapshot(
            config, client=client, bucket=bucket, key=key
        )
        raw = {site: future.result() for site, future in futures.items()}
    raw[WEBAPP_IR] = iran_snapshot
    root = _private_directory(Path(str(config["artifact_root"])), label="convergence artifact root")
    for site in SITES:
        _write_snapshot(root, site, raw[site])
    try:
        artifacts = build(
            raw_snapshots=raw,
            campaign_id=config["campaign_id"],
            release_sha=config["release_sha"],
            plan_sha256=config["plan_sha256"],
            now=datetime.now(timezone.utc),
        )
    except ConvergenceEvidenceError as exc:
        raise ConvergenceObserverError("three-site convergence did not pass") from exc
    outputs: dict[str, dict[str, str]] = {}
    for label, artifact in artifacts.items():
        path = root / f"{label}.json"
        write_secure_atomic_bytes(
            path,
            (json.dumps(artifact, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode(),
            label=f"three-site {label} evidence",
            mode=0o600,
            max_size=32 * 1024 * 1024,
        )
        outputs[label] = {
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    summary = {
        "status": "passed",
        "campaign_id": config["campaign_id"],
        "release_sha": config["release_sha"],
        "plan_sha256": config["plan_sha256"],
        "webapp_ir_object_storage": {
            "bucket": bucket,
            "object_key": key,
            "version_id": iran_version,
            "transport": "private_versioned_object_storage",
            "ssh_payload_transfer": False,
        },
        "artifacts": outputs,
    }
    write_secure_atomic_bytes(
        output,
        (json.dumps(summary, sort_keys=True, ensure_ascii=False) + "\n").encode(),
        label="three-site convergence observer result",
        mode=0o600,
        max_size=128 * 1024,
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if (
            str(UUID(args.campaign_id)) != args.campaign_id
            or SHA40.fullmatch(args.release_sha) is None
            or SHA256.fullmatch(args.plan_sha256) is None
        ):
            raise ValueError
        config = load_config(
            args.config,
            campaign_id=args.campaign_id,
            release_sha=args.release_sha,
            plan_sha256=args.plan_sha256,
            output=args.output,
        )
        summary = observe(config=config, output=args.output)
        print(json.dumps({
            "status": summary["status"],
            "artifact_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            "artifacts": summary["artifacts"],
        }, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
