#!/usr/bin/env python3
"""Build the canonical, release-bound PRIVATE_PRIMARY choreography plan.

This builder consumes only digest-pinned release evidence and root-owned
configuration files.  It does not inspect Git, Docker, SSH, a database, or a
live service.  Secret-bearing env/key files are never copied into either
output; only their approved absolute paths and document digests are used.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence


PLAN_SCHEMA = "production_private_primary_choreography_plan/1.0"
RECEIPT_SCHEMA = "production_private_primary_choreography_plan_build/1.0"
CONFIRMATION = "build-production-private-primary-choreography-plan"
APPROVED_RELEASE_REF = "refs/remotes/origin/main"
CONTROL_MANIFEST_NAME = "control-payload.sha256"
CONTROL_PAIR_NAME = "market-pipeline-release-pair-receipt.json"
CONTROLLER_LOCK_NAME = "private-primary-controller.lock"
SSH_BINARY = "/usr/bin/ssh"
MAXIMUM_DOCUMENT_BYTES = 2_000_000
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_ARGUMENT = re.compile(r"^[A-Za-z0-9_./:=,@+%-]+$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
TRANSACTION_ID = re.compile(r"^[a-z0-9][a-z0-9-]{7,95}$")

PHASES = (
    "bluegreen_workload_quiesce",
    "backup_restore_offhost",
    "bluegreen_database_quiesce",
    "migration",
    "base_services_start",
    "legacy_quiesce",
    "bluegreen_activate",
    "catchup_audit",
    "nine_source_evidence",
    "snapshot_outbox",
    "promotion_verification",
    "product_promotion",
)

# These files are not direct phase commands, but are imported, delegated to,
# or executed by the release-bound wrappers and deploy shell.  Omitting one
# from an otherwise self-consistent control manifest would defer the failure
# until after a forward-only production transition had started.
TRANSITIVE_RUNTIME_PAYLOADS = (
    "build_production_private_primary_choreography_plan.py",
    "check_production_coin_inference_readiness.py",
    "cutover_telegram_delivery_queue_production.py",
    "update_production_coin_inference_source.py",
    "prepare_production_private_primary_manifest.py",
    "run_fenced_production_deploy.py",
    "production_deploy_online.sh",
)
REQUIRED_INPUT_LABELS = (
    "bot_env",
    "bot_old_env",
    "control_manifest",
    "control_pair_receipt",
    "deployment_manifest",
    "market_image_receipt",
    "preflight_receipt",
    "primary_pair_receipt",
    "private_manifest",
    "private_manifest_receipt",
    "product_bot_image_receipt",
    "product_web_image_receipt",
    "runtime_source",
    "web_env",
    "web_old_env",
)


class PlanBuildError(RuntimeError):
    """A stable, secret-free refusal from the plan builder."""


@dataclass(frozen=True)
class BoundFile:
    label: str
    path: Path
    digest: str
    payload: bytes
    identity: tuple[int, int, int, int, int]


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical(document: Mapping[str, object]) -> bytes:
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _lexical_path(value: str | Path, *, label: str) -> Path:
    text = str(value)
    path = Path(text)
    if (
        not path.is_absolute()
        or text != str(path)
        or "\x00" in text
        or "\n" in text
        or "\r" in text
        or not SAFE_ARGUMENT.fullmatch(text)
        or path == Path("/")
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise PlanBuildError(f"{label}_path_invalid")
    return path


def _no_symlink_ancestors(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise PlanBuildError(f"{label}_ancestor_unavailable") from exc
        if current.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise PlanBuildError(f"{label}_ancestor_invalid")


def _secure_directory(path: Path, *, label: str) -> None:
    path = _lexical_path(path, label=label)
    _no_symlink_ancestors(path, label=label)
    try:
        info = path.lstat()
    except OSError as exc:
        raise PlanBuildError(f"{label}_directory_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise PlanBuildError(f"{label}_directory_invalid")


def _secure_secret_metadata(path: Path, *, label: str) -> None:
    path = _lexical_path(path, label=label)
    _no_symlink_ancestors(path.parent, label=label)
    try:
        info = path.lstat()
    except OSError as exc:
        raise PlanBuildError(f"{label}_unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}
        or info.st_nlink != 1
    ):
        raise PlanBuildError(f"{label}_security_invalid")


def _read_bound(path: Path, expected: str, *, label: str) -> BoundFile:
    path = _lexical_path(path, label=label)
    _no_symlink_ancestors(path.parent, label=label)
    if not HEX64.fullmatch(expected):
        raise PlanBuildError(f"{label}_digest_invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PlanBuildError(f"{label}_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        path_info = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAXIMUM_DOCUMENT_BYTES
            or (before.st_dev, before.st_ino) != (path_info.st_dev, path_info.st_ino)
        ):
            raise PlanBuildError(f"{label}_security_invalid")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if len(payload) != before.st_size or identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise PlanBuildError(f"{label}_changed_during_read")
        observed = _digest(payload)
        if observed != expected:
            raise PlanBuildError(f"{label}_digest_mismatch")
        return BoundFile(label, path, observed, payload, identity)
    except OSError as exc:
        raise PlanBuildError(f"{label}_read_failed") from exc
    finally:
        os.close(descriptor)


def _assert_stable(item: BoundFile) -> None:
    try:
        info = item.path.lstat()
    except OSError as exc:
        raise PlanBuildError(f"{item.label}_changed_after_read") from exc
    if (
        item.path.is_symlink()
        or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        != item.identity
    ):
        raise PlanBuildError(f"{item.label}_changed_after_read")


def _json(item: BoundFile) -> dict[str, object]:
    try:
        value = json.loads(item.payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanBuildError(f"{item.label}_json_invalid") from exc
    if not isinstance(value, dict):
        raise PlanBuildError(f"{item.label}_json_invalid")
    return value


def _env(item: BoundFile) -> dict[str, str]:
    try:
        text = item.payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PlanBuildError(f"{item.label}_encoding_invalid") from exc
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise PlanBuildError(f"{item.label}_syntax_invalid")
        key, value = line.split("=", 1)
        if not ENV_KEY.fullmatch(key) or key in result or "\x00" in value:
            raise PlanBuildError(f"{item.label}_syntax_invalid")
        result[key] = value
    return result


def _write_exclusive(path: Path, payload: bytes, *, label: str) -> None:
    path = _lexical_path(path, label=label)
    _secure_directory(path.parent, label=f"{label}_parent")
    if path.exists() or path.is_symlink():
        try:
            current = path.read_bytes()
        except OSError as exc:
            raise PlanBuildError(f"{label}_exists") from exc
        if current == payload and not path.is_symlink():
            return
        raise PlanBuildError(f"{label}_exists")
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise PlanBuildError(f"{label}_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise PlanBuildError(f"{label}_postwrite_invalid")


def _read_control_file(root: Path, relative: str, expected: str) -> bytes:
    if (
        not relative
        or relative.startswith("/")
        or ".." in Path(relative).parts
        or not HEX64.fullmatch(expected)
    ):
        raise PlanBuildError("control_manifest_entry_invalid")
    path = root / relative
    current = root
    for part in Path(relative).parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except OSError as exc:
            raise PlanBuildError("control_manifest_parent_unavailable") from exc
        if current.is_symlink() or not stat.S_ISDIR(info.st_mode):
            raise PlanBuildError("control_manifest_parent_invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PlanBuildError("control_manifest_file_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAXIMUM_DOCUMENT_BYTES
        ):
            raise PlanBuildError("control_manifest_file_invalid")
        payload = os.read(descriptor, before.st_size + 1)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise PlanBuildError("control_manifest_file_changed")
        if _digest(payload) != expected:
            raise PlanBuildError("control_manifest_file_digest_mismatch")
        return payload
    finally:
        os.close(descriptor)


def _control_manifest(root: Path, item: BoundFile, required_tools: Sequence[str]) -> dict[str, str]:
    if item.path != root / CONTROL_MANIFEST_NAME:
        raise PlanBuildError("control_manifest_decoy_path")
    entries: dict[str, str] = {}
    try:
        lines = item.payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise PlanBuildError("control_manifest_encoding_invalid") from exc
    for line in lines:
        match = re.fullmatch(r"([0-9a-f]{64})  \./([A-Za-z0-9_./-]+)", line)
        if match is None or match.group(2) in entries:
            raise PlanBuildError("control_manifest_entry_invalid")
        entries[match.group(2)] = match.group(1)
    required = {
        f"scripts/{tool}"
        for tool in (*required_tools, *TRANSITIVE_RUNTIME_PAYLOADS)
    }
    required.add("scripts/run_production_private_primary_choreography.py")
    if not required.issubset(entries):
        raise PlanBuildError("control_manifest_required_file_missing")
    # The manifest is an all-files commitment, not merely a lookup table for
    # the tools this builder happens to invoke.  Verify every listed file so a
    # drifted dependency cannot hide behind an unchanged tool entry.
    for relative in sorted(entries):
        _read_control_file(root, relative, entries[relative])
    return entries


def _control_relative(path: Path, root: Path, *, label: str) -> Path:
    """Return a safe path inside an exact SHA-named control release."""

    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise PlanBuildError(f"{label}_outside_control_release") from exc
    if not relative.parts or any(part in {".", ".."} for part in relative.parts):
        raise PlanBuildError(f"{label}_outside_control_release")
    return relative


def _require_release(document: Mapping[str, object], sha: str, tree: str, *, label: str) -> None:
    if (
        document.get("release_sha") != sha
        or document.get("release_tree") != tree
        or document.get("secrets_disclosed") is not False
    ):
        raise PlanBuildError(f"{label}_release_binding_invalid")


def _image_ids(document: Mapping[str, object], *, label: str) -> dict[str, str]:
    if "image_ids" in document:
        values = document.get("image_ids")
        if not isinstance(values, dict) or set(values) != {"bot", "web"}:
            raise PlanBuildError(f"{label}_image_identity_invalid")
        result = {role: str(values[role]) for role in ("bot", "web")}
    else:
        value = str(document.get("image_id") or "")
        result = {"bot": value, "web": value}
    if any(not IMAGE_ID.fullmatch(value) for value in result.values()):
        raise PlanBuildError(f"{label}_image_identity_invalid")
    return result


def _product_image_receipt(document: Mapping[str, object], sha: str, tree: str, *, role: str, web_host: str) -> str:
    fixed = {
        "schema_version": 1,
        "environment": "production",
        "release_sha": sha,
        "release_tree": tree,
        "secrets_disclosed": False,
    }
    if any(document.get(key) != value for key, value in fixed.items()):
        raise PlanBuildError(f"product_{role}_image_receipt_invalid")
    image = str(document.get("image_id") or "")
    signature = str(document.get("input_signature") or "")
    if not IMAGE_ID.fullmatch(image) or not HEX64.fullmatch(signature):
        raise PlanBuildError(f"product_{role}_image_receipt_invalid")
    if role == "bot":
        if set(document) != {*fixed, "image_id", "input_signature"}:
            raise PlanBuildError("product_bot_image_receipt_invalid")
    else:
        target = document.get("target")
        if (
            document.get("role") != "iran"
            or not HEX64.fullmatch(str(document.get("bundle_sha256") or ""))
            or not isinstance(target, dict)
            or target.get("host") != web_host
            or target.get("compose_project") != "current"
            or target.get("image") != "trading_bot_base_iran:latest"
            or not Path(str(target.get("project_dir") or "")).is_absolute()
            or set(document) != {*fixed, "role", "image_id", "input_signature", "bundle_sha256", "target"}
        ):
            raise PlanBuildError("product_web_image_receipt_invalid")
    return image


def _ssh_argv(deployment: Mapping[str, str], expected_target: str) -> list[str]:
    required = {
        "IRAN_SSH_AUTH_METHOD": "key",
        "IRAN_SSH_USER": None,
        "IRAN_HOST": None,
        "IRAN_SSH_PORT": None,
        "IRAN_SSH_CONNECT_TIMEOUT_SECONDS": None,
        "IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS": None,
        "IRAN_SSH_SERVER_ALIVE_COUNT_MAX": None,
        "IRAN_SSH_COMMAND_TIMEOUT_SECONDS": None,
        "IRAN_SSH_PRIVATE_KEY_PATH": None,
    }
    if any(key not in deployment for key in required) or deployment["IRAN_SSH_AUTH_METHOD"].lower() != "key":
        raise PlanBuildError("deployment_ssh_contract_invalid")
    user = deployment["IRAN_SSH_USER"]
    host = deployment["IRAN_HOST"]
    target = f"{user}@{host}"
    if target != expected_target or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,31}@[A-Za-z0-9.-]+", target):
        raise PlanBuildError("web_ssh_target_mismatch")
    numeric: dict[str, int] = {}
    for key in (
        "IRAN_SSH_PORT",
        "IRAN_SSH_CONNECT_TIMEOUT_SECONDS",
        "IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS",
        "IRAN_SSH_SERVER_ALIVE_COUNT_MAX",
        "IRAN_SSH_COMMAND_TIMEOUT_SECONDS",
    ):
        value = deployment[key]
        if not value.isdigit() or int(value) <= 0:
            raise PlanBuildError("deployment_ssh_contract_invalid")
        numeric[key] = int(value)
    if not (
        numeric["IRAN_SSH_PORT"] <= 65535
        and numeric["IRAN_SSH_CONNECT_TIMEOUT_SECONDS"] <= 60
        and numeric["IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS"] <= 60
        and numeric["IRAN_SSH_SERVER_ALIVE_COUNT_MAX"] <= 10
        and 60 <= numeric["IRAN_SSH_COMMAND_TIMEOUT_SECONDS"] <= 3600
    ):
        raise PlanBuildError("deployment_ssh_contract_invalid")
    identity = _lexical_path(deployment["IRAN_SSH_PRIVATE_KEY_PATH"], label="ssh_identity")
    return [
        SSH_BINARY, "-p", str(numeric["IRAN_SSH_PORT"]),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={numeric['IRAN_SSH_CONNECT_TIMEOUT_SECONDS']}",
        "-o", f"ServerAliveInterval={numeric['IRAN_SSH_SERVER_ALIVE_INTERVAL_SECONDS']}",
        "-o", f"ServerAliveCountMax={numeric['IRAN_SSH_SERVER_ALIVE_COUNT_MAX']}",
        "-o", "ConnectionAttempts=1",
        "-o", "BatchMode=yes", "-o", "PasswordAuthentication=no",
        "-o", "KbdInteractiveAuthentication=no", "-o", "IdentitiesOnly=yes",
        "-i", str(identity), target,
    ]


def _argv_digest(values: Sequence[str]) -> str:
    return _digest(b"\0".join(value.encode() for value in values) + b"\0")


def _path(root: Path, name: str) -> str:
    return str(_lexical_path(root / name, label="artifact"))


def _command(host: str, remote_root: Path, tool: str, arguments: list[str]) -> dict[str, object]:
    if host not in {"local", "web"} or any(
        not value or (not SAFE_ARGUMENT.fullmatch(value) and value != "RECONCILE PRODUCTION ESTIMATOR SNAPSHOT PUBLICATION OUTBOX")
        for value in arguments
    ):
        raise PlanBuildError("generated_command_invalid")
    result: dict[str, object] = {"host": host, "tool": tool, "arguments": arguments}
    if host == "web":
        result["remote_release_root"] = str(remote_root)
    return result


def _evidence(host: str, path: str, schema: str, statuses: Sequence[str]) -> dict[str, object]:
    return {"host": host, "path": path, "schema": schema, "statuses": list(statuses)}


def _phase(phase_id: str, commands: list[dict[str, object]], evidence: list[dict[str, object]]) -> dict[str, object]:
    return {"id": phase_id, "commands": commands, "evidence": evidence, "recovery_commands": [], "rollback_commands": []}


def _build_commands(
    *, sha: str, tree: str, local_root: Path, remote_root: Path,
    secure_root: Path, web_backup_root: Path, local_backup_root: Path,
    web_key: Path, local_key: Path, web_env: Path, remote_web_env: Path,
    bot_env: Path, web_old_env: Path, remote_web_old_env: Path,
    bot_old_env: Path, web_values: Mapping[str, str],
    bot_values: Mapping[str, str], web_old_values: Mapping[str, str],
    bot_old_values: Mapping[str, str], market_images: Mapping[str, str],
    market_signature: str, web_preflight_digest: str, control_digest: str,
    product_images: Mapping[str, str], runtime_source: Path,
    deployment_manifest: Path, private_manifest: Path,
    private_manifest_digest: str, private_manifest_receipt: Path,
    private_manifest_receipt_digest: str, runtime_source_digest: str,
    deployment_manifest_digest: str, release_checkout: Path,
    transaction_id: str,
) -> list[dict[str, object]]:
    web_bg = _path(secure_root, "web-bluegreen.json")
    bot_bg = _path(secure_root, "bot-bluegreen.json")
    web_rollout = _path(secure_root, "web-rollout.json")
    bot_rollout = _path(secure_root, "bot-rollout.json")
    web_legacy = _path(secure_root, "web-legacy-handoff.json")
    bot_legacy = _path(secure_root, "bot-legacy-handoff.json")
    backup_receipt = _path(web_backup_root, "market-pipeline-backup-receipt.json")
    offhost_receipt_web = _path(secure_root, "web-offhost-copy-receipt.json")
    migration_journal = _path(secure_root, "web-migration-journal.json")
    migration_receipt = _path(secure_root, "web-migration-receipt.json")
    web_before = _path(secure_root, "web-catchup-before.json")
    bot_before = _path(secure_root, "bot-catchup-before.json")
    web_after = _path(secure_root, "web-catchup-after.json")
    bot_after = _path(secure_root, "bot-catchup-after.json")
    web_before_mirror = _path(secure_root, "mirror-web-catchup-before.json")
    web_after_mirror = _path(secure_root, "mirror-web-catchup-after.json")
    settle_receipt = _path(secure_root, "catchup-settle.json")
    catchup_receipt = _path(secure_root, "catchup-verification.json")
    web_observation = _path(secure_root, "web-observation.json")
    bot_observation = _path(secure_root, "bot-observation.json")
    outbox_receipt = _path(secure_root, "web-outbox-reconciliation.json")
    promotion_receipt = _path(secure_root, "promotion-verification.json")
    product_receipt = _path(secure_root, "product-promotion.json")
    postdeploy_receipt = _path(secure_root / transaction_id, "post-deploy-verification.json")
    web_data = Path(web_values["MARKET_WEB_DATA_ROOT"])
    bot_data = Path(bot_values["MARKET_BOT_DATA_ROOT"])
    web_account1_state = web_data / "state/market-capture-account1/market-capture-account1"
    web_account2_state = web_data / "state/market-capture-account2/market-capture-account2"
    web_external_state = web_data / "state/market-capture-external/market-capture-external"
    web_processor_state = web_data / "state/market-processor/market-processor"
    web_receiver_state = web_data / "state/estimator-snapshot-receiver/estimator-snapshot-receiver"
    bot_receiver_state = bot_data / "state/market-fact-receiver/market-fact-receiver"
    bot_estimator_state = bot_data / "state/coin-estimator/coin-estimator"
    bot_sender_state = bot_data / "state/estimator-snapshot-sender/estimator-snapshot-sender"
    web_project = web_values["MARKET_PIPELINE_PROJECT_NAME"]
    bot_project = bot_values["MARKET_PIPELINE_PROJECT_NAME"]
    common_rollout = ["--release-sha", sha, "--feed-mode", "PRIVATE_PRIMARY", "--confirm", "rollout-production-market-pipeline-private-primary"]

    phases: list[dict[str, object]] = []
    phases.append(_phase(PHASES[0], [
        _command("web", remote_root, "upgrade_market_pipeline_bluegreen.py", ["plan", "--role", "web", "--release-sha", sha, "--old-env", str(remote_web_old_env), "--new-env", str(remote_web_env), "--old-project", web_old_values["MARKET_PIPELINE_PROJECT_NAME"], "--new-project", web_project, "--journal", web_bg, "--release-root", str(remote_root), "--release-tree", tree, "--confirm", "upgrade-market-pipeline-bluegreen"]),
        _command("local", remote_root, "upgrade_market_pipeline_bluegreen.py", ["plan", "--role", "bot", "--release-sha", sha, "--old-env", str(bot_old_env), "--new-env", str(bot_env), "--old-project", bot_old_values["MARKET_PIPELINE_PROJECT_NAME"], "--new-project", bot_project, "--journal", bot_bg, "--release-root", str(local_root), "--release-tree", tree, "--confirm", "upgrade-market-pipeline-bluegreen"]),
        _command("web", remote_root, "upgrade_market_pipeline_bluegreen.py", ["quiesce-workload", "--role", "web", "--release-sha", sha, "--journal", web_bg, "--confirm", "upgrade-market-pipeline-bluegreen"]),
        _command("local", remote_root, "upgrade_market_pipeline_bluegreen.py", ["quiesce-workload", "--role", "bot", "--release-sha", sha, "--journal", bot_bg, "--confirm", "upgrade-market-pipeline-bluegreen"]),
    ], [_evidence("web", web_bg, "market_pipeline_bluegreen_upgrade/1.0", ["workload_quiesced"]), _evidence("local", bot_bg, "market_pipeline_bluegreen_upgrade/1.0", ["workload_quiesced"])]))

    backup_common = ["--env-file", str(remote_web_env), "--receipt", backup_receipt, "--release-sha", sha, "--release-tree", tree, "--image-id", market_images["web"], "--image-input-signature", market_signature]
    phases.append(_phase(PHASES[1], [
        _command("web", remote_root, "backup_market_pipeline_archive.py", ["create", *backup_common, "--backup-dir", str(web_backup_root), "--confirm", "create-production-market-pipeline-archive-backup"]),
        _command("web", remote_root, "backup_market_pipeline_archive.py", ["verify", *backup_common, "--maximum-age-seconds", "3600"]),
        _command("web", remote_root, "crypt_market_pipeline_backup.py", ["encrypt", "--source", _path(web_backup_root, "runtime-placeholder.dump"), "--destination", _path(web_backup_root, "runtime-placeholder.dump.enc"), "--key-file", str(web_key), "--receipt", _path(web_backup_root, "runtime-placeholder.dump.encryption.json"), "--confirm", "encrypt-production-market-pipeline-offhost-backup"]),
        _command("local", remote_root, "crypt_market_pipeline_backup.py", ["verify", "--artifact", _path(local_backup_root, "runtime-placeholder.dump.enc"), "--key-file", str(local_key), "--receipt", _path(local_backup_root, "runtime-placeholder.dump.encryption.json")]),
    ], [_evidence("web", backup_receipt, "market_pipeline_backup_restore/1.2", ["PASS"])]))

    phases.append(_phase(PHASES[2], [
        _command("web", remote_root, "upgrade_market_pipeline_bluegreen.py", ["quiesce-database", "--role", "web", "--release-sha", sha, "--journal", web_bg, "--backup-receipt", backup_receipt, "--offhost-backup-receipt", offhost_receipt_web, "--release-tree", tree, "--image-id", market_images["web"], "--image-input-signature", market_signature, "--backup-maximum-age-seconds", "3600", "--confirm", "upgrade-market-pipeline-bluegreen"]),
    ], [_evidence("web", web_bg, "market_pipeline_bluegreen_upgrade/1.0", ["database_quiesced"])]))

    phases.append(_phase(PHASES[3], [
        _command("web", remote_root, "migrate_market_pipeline_archive.py", ["--release-root", str(remote_root), "--env-file", str(remote_web_env), "--backup-env-file", str(remote_web_env), "--backup-receipt", backup_receipt, "--release-sha", sha, "--release-tree", tree, "--image-id", market_images["web"], "--image-input-signature", market_signature, "--host-preflight-receipt-sha256", web_preflight_digest, "--backup-maximum-age-seconds", "3600", "--journal", migration_journal, "--receipt", migration_receipt, "--confirm", "run-production-market-pipeline-archive-migration"]),
    ], [_evidence("web", migration_receipt, "market_pipeline_migration_receipt/1.0", ["PASS"])]))

    starts = (
        ("web", "prepare", None), ("web", "start", "estimator-snapshot-receiver"),
        ("bot", "prepare", None), ("bot", "start", "market-fact-receiver"),
        ("web", "start", "market-processor"), ("web", "start", "market-fact-sync-worker"),
        ("bot", "start", "market-store-adapter"), ("bot", "start", "coin-estimator"),
        ("bot", "start", "estimator-snapshot-sender"), ("bot", "verify", None),
        ("web", "start", "estimator-snapshot-receiver"), ("web", "verify", None),
    )
    rollout_commands: list[dict[str, object]] = []
    for role, action, service in starts:
        host = "web" if role == "web" else "local"
        args = [action, "--role", role, "--release-root", str(remote_root if role == "web" else local_root), "--env-file", str(remote_web_env if role == "web" else bot_env), "--journal", web_rollout if role == "web" else bot_rollout, "--image-id", market_images[role], *common_rollout]
        if service is not None:
            args.extend(["--service", service])
        rollout_commands.append(_command(host, remote_root, "rollout_market_pipeline_shadow.py", args))
    phases.append(_phase(PHASES[4], rollout_commands, [_evidence("web", web_rollout, "market_pipeline_shadow_rollout/1.0", ["PASS"]), _evidence("local", bot_rollout, "market_pipeline_shadow_rollout/1.0", ["PASS"])]))

    phases.append(_phase(PHASES[5], [
        _command("web", remote_root, "quiesce_production_legacy_market_collectors.py", ["quiesce", "--host-role", "web", "--release-sha", sha, "--journal", web_legacy, "--confirm", "quiesce-production-legacy-market-collectors"]),
        _command("web", remote_root, "quiesce_production_legacy_market_collectors.py", ["verify", "--host-role", "web", "--release-sha", sha, "--journal", web_legacy, "--confirm", "quiesce-production-legacy-market-collectors"]),
        _command("local", remote_root, "quiesce_production_legacy_market_collectors.py", ["quiesce", "--host-role", "bot", "--release-sha", sha, "--journal", bot_legacy, "--confirm", "quiesce-production-legacy-market-collectors"]),
        _command("local", remote_root, "quiesce_production_legacy_market_collectors.py", ["verify", "--host-role", "bot", "--release-sha", sha, "--journal", bot_legacy, "--confirm", "quiesce-production-legacy-market-collectors"]),
        _command("web", remote_root, "upgrade_market_pipeline_bluegreen.py", ["prepare-capture-authority", "--role", "web", "--release-sha", sha, "--journal", web_bg, "--confirm", "upgrade-market-pipeline-bluegreen"]),
        _command("local", remote_root, "quiesce_production_legacy_market_collectors.py", ["prepare-authority", "--host-role", "bot", "--release-sha", sha, "--journal", bot_legacy, "--bluegreen-journal", _path(secure_root, "mirror-web-bluegreen-prepared.json"), "--confirm", "prepare-production-private-primary-capture-authority"]),
    ], [_evidence("web", web_bg, "market_pipeline_bluegreen_upgrade/1.0", ["capture_authority_prepared"]), _evidence("local", bot_legacy, "production_legacy_market_collector_handoff/1.1", ["AUTHORITY_TRANSFERRING"])]))

    phases.append(_phase(PHASES[6], [
        _command("web", remote_root, "upgrade_market_pipeline_bluegreen.py", ["authorize-captures", "--role", "web", "--release-sha", sha, "--journal", web_bg, "--bot-legacy-collector-receipt", _path(secure_root, "web-mirror-bot-legacy-authority.json"), "--confirm", "upgrade-market-pipeline-bluegreen"]),
        _command("local", remote_root, "quiesce_production_legacy_market_collectors.py", ["mark-authority-transferred", "--host-role", "bot", "--release-sha", sha, "--journal", bot_legacy, "--bluegreen-journal", _path(secure_root, "mirror-web-bluegreen-authorized.json"), "--confirm", "mark-production-private-primary-capture-authority-transferred"]),
        _command("web", remote_root, "upgrade_market_pipeline_bluegreen.py", ["start-captures", "--role", "web", "--release-sha", sha, "--journal", web_bg, "--confirm", "upgrade-market-pipeline-bluegreen"]),
        _command("web", remote_root, "upgrade_market_pipeline_bluegreen.py", ["verify", "--role", "web", "--release-sha", sha, "--journal", web_bg, "--confirm", "upgrade-market-pipeline-bluegreen"]),
        _command("local", remote_root, "upgrade_market_pipeline_bluegreen.py", ["verify", "--role", "bot", "--release-sha", sha, "--journal", bot_bg, "--confirm", "upgrade-market-pipeline-bluegreen"]),
    ], [_evidence("web", web_bg, "market_pipeline_bluegreen_upgrade/1.0", ["PASS"]), _evidence("local", bot_bg, "market_pipeline_bluegreen_upgrade/1.0", ["PASS"])]))

    web_args = ["--release-sha", sha, "--runtime-env", str(remote_web_env), "--account1-db", str(web_account1_state / "capture-state.sqlite"), "--account2-db", str(web_account2_state / "capture-state.sqlite"), "--external-db", str(web_external_state / "external-capture.sqlite3"), "--account1-spool", str(web_data / "capture/account1"), "--account2-spool", str(web_data / "capture/account2"), "--external-spool", str(web_data / "capture/external"), "--processor-staging", str(web_processor_state / "capture-staging.sqlite3"), "--processor-market", str(web_processor_state / "shadow-market.sqlite3"), "--account1-health", str(web_account1_state / "health.json"), "--account2-health", str(web_account2_state / "health.json"), "--external-health", str(web_external_state / "health.json"), "--processor-health", str(web_processor_state / "health.json"), "--postgres-container", f"{web_project}-market-postgres-1", "--postgres-user", web_values["MARKET_POSTGRES_USER"], "--postgres-database", web_values["MARKET_POSTGRES_DB"]]
    bot_args = ["--release-sha", sha, "--receiver-db", str(bot_receiver_state / "market-fact-receiver.sqlite3"), "--market-store-db", str(bot_data / "market-store/market-store.sqlite"), "--estimator-state-db", str(bot_estimator_state / "estimator-state.sqlite3"), "--snapshot", str(bot_data / "snapshots/latest-estimator-snapshot.json")]
    phases.append(_phase(PHASES[7], [
        _command("web", remote_root, "audit_production_market_catchup.py", ["web", *web_args, "--output", web_before]),
        _command("local", remote_root, "audit_production_market_catchup.py", ["bot", *bot_args, "--output", bot_before]),
        _command("local", remote_root, "audit_production_market_catchup.py", ["settle", "--release-sha", sha, "--previous-web", web_before_mirror, "--previous-bot", bot_before, "--maximum-window-seconds", "30", "--output", settle_receipt]),
        _command("web", remote_root, "audit_production_market_catchup.py", ["web", *web_args, "--output", web_after]),
        _command("local", remote_root, "audit_production_market_catchup.py", ["bot", *bot_args, "--output", bot_after]),
        _command("local", remote_root, "audit_production_market_catchup.py", ["verify", "--release-sha", sha, "--web", web_after_mirror, "--bot", bot_after, "--previous-web", web_before_mirror, "--previous-bot", bot_before, "--output", catchup_receipt]),
        _command("web", remote_root, "observe_production_private_primary.py", ["--role", "web", "--release-sha", sha, "--release-tree", tree, "--project", web_project, "--image-id", market_images["web"], "--snapshot", str(web_data / "snapshots/latest-estimator-snapshot.json"), "--receiver-db", str(web_receiver_state / "estimator-snapshot-receiver.sqlite3"), "--output", web_observation]),
        _command("local", remote_root, "observe_production_private_primary.py", ["--role", "bot", "--release-sha", sha, "--release-tree", tree, "--project", bot_project, "--image-id", market_images["bot"], "--snapshot", str(bot_data / "snapshots/latest-estimator-snapshot.json"), "--receiver-db", str(bot_receiver_state / "market-fact-receiver.sqlite3"), "--market-store-db", str(bot_data / "market-store/market-store.sqlite"), "--sender-db", str(bot_sender_state / "sender-state.sqlite3"), "--output", bot_observation]),
    ], [_evidence("local", catchup_receipt, "production_market_catchup_verification/1.2", ["PASS"]), _evidence("web", web_observation, "production_private_primary_observation/1.0", ["PASS"]), _evidence("local", bot_observation, "production_private_primary_observation/1.0", ["PASS"])]))

    phases.append(_phase(PHASES[8], [
        _command("web", remote_root, "run_release_bound_product_readiness.py", ["--role", "web", "--release-sha", sha, "--release-tree", tree, "--control-root", str(remote_root), "--expected-control-manifest-sha256", control_digest, "--container", "trading_bot_app", "--project", "current", "--expected-image-id", product_images["web"], "--confirm", "run-release-bound-product-readiness"]),
        _command("local", remote_root, "run_release_bound_product_readiness.py", ["--role", "bot", "--release-sha", sha, "--release-tree", tree, "--control-root", str(local_root), "--expected-control-manifest-sha256", control_digest, "--container", "trading_bot_bot", "--project", "trading_bot", "--expected-image-id", product_images["bot"], "--confirm", "run-release-bound-product-readiness"]),
    ], [_evidence("local", catchup_receipt, "production_market_catchup_verification/1.2", ["PASS"])]))

    outbox_common = ["--database", str(web_receiver_state / "estimator-snapshot-receiver.sqlite3"), "--snapshot-root", str(web_data / "snapshots"), "--publication-events", str(web_receiver_state / "snapshot-publication-events.jsonl"), "--lane", "PRIVATE_PRIMARY", "--expected-release-sha", sha, "--expected-release-tree", tree, "--receipt", outbox_receipt]
    phases.append(_phase(PHASES[9], [
        _command("web", remote_root, "reconcile_estimator_snapshot_publication_outbox.py", ["plan", *outbox_common]),
        _command("web", remote_root, "reconcile_estimator_snapshot_publication_outbox.py", ["apply", *outbox_common, "--preimage-backup", _path(secure_root, "web-outbox-preimage.sqlite3"), "--journal", _path(secure_root, "web-outbox-journal.json"), "--confirm", "RECONCILE PRODUCTION ESTIMATOR SNAPSHOT PUBLICATION OUTBOX"]),
    ], [_evidence("web", outbox_receipt, "estimator_snapshot_publication_reconciliation/1.0", ["APPLIED", "ALREADY_RECONCILED"])]))

    phases.append(_phase(PHASES[10], [
        _command("local", remote_root, "verify_production_private_primary_promotion.py", ["verify", "--release-sha", sha, "--release-tree", tree, "--bot-image-id", market_images["bot"], "--web-image-id", market_images["web"], "--bot-env", str(bot_env), "--web-env", _path(secure_root, "mirror-web-primary-env.env"), "--bot-journal", bot_bg, "--web-journal", _path(secure_root, "mirror-web-bluegreen-final.json"), "--bot-health", bot_observation, "--web-health", _path(secure_root, "mirror-web-observation.json"), "--bot-snapshot", str(bot_data / "snapshots/latest-estimator-snapshot.json"), "--web-snapshot", _path(secure_root, "mirror-web-snapshot.json"), "--catchup-receipt", catchup_receipt, "--maximum-age-seconds", "120", "--receipt", promotion_receipt, "--confirmation", "verify-production-private-primary-promotion"]),
        _command("web", remote_root, "quiesce_production_legacy_market_collectors.py", ["commit", "--host-role", "web", "--release-sha", sha, "--journal", web_legacy, "--primary-verification", _path(secure_root, "web-mirror-promotion-verification.json"), "--confirm", "commit-production-private-primary-capture-owner"]),
        _command("local", remote_root, "quiesce_production_legacy_market_collectors.py", ["commit", "--host-role", "bot", "--release-sha", sha, "--journal", bot_legacy, "--primary-verification", promotion_receipt, "--confirm", "commit-production-private-primary-capture-owner"]),
    ], [_evidence("local", promotion_receipt, "production_private_primary_promotion_verification/1.0", ["PASS"])]))

    phases.append(_phase(PHASES[11], [
        _command("local", remote_root, "promote_production_private_primary_product.py", ["--source-manifest", str(deployment_manifest), "--expected-source-manifest-sha256", deployment_manifest_digest, "--private-manifest", str(private_manifest), "--expected-private-manifest-sha256", private_manifest_digest, "--private-manifest-receipt", str(private_manifest_receipt), "--expected-private-manifest-receipt-sha256", private_manifest_receipt_digest, "--promotion-receipt", promotion_receipt, "--catchup-receipt", catchup_receipt, "--expected-source-sha256", runtime_source_digest, "--expected-release-sha", sha, "--expected-release-tree", tree, "--release-checkout", str(release_checkout), "--maintenance-journal", bot_legacy, "--web-maintenance-journal", _path(secure_root, "mirror-web-legacy-committed.json"), "--transaction-root", str(secure_root), "--queue-artifact-dir", _path(secure_root, "production-private-primary-queue-artifacts"), "--transaction-id", transaction_id, "--receipt", product_receipt, "--confirm", "promote-production-private-primary-product"]),
    ], [_evidence("local", product_receipt, "production_private_primary_product_promotion/1.0", ["PASS"]), _evidence("local", postdeploy_receipt, "production_private_primary_product_postdeploy_verification/1.0", ["PASS"])]))
    return phases


def build(args: argparse.Namespace) -> tuple[dict[str, object], dict[str, object]]:
    if args.confirm != CONFIRMATION:
        raise PlanBuildError("confirmation_invalid")
    sha, tree = str(args.release_sha), str(args.release_tree)
    if not HEX40.fullmatch(sha) or not HEX40.fullmatch(tree):
        raise PlanBuildError("release_identity_invalid")
    transaction_id = str(args.transaction_id)
    if not TRANSACTION_ID.fullmatch(transaction_id):
        raise PlanBuildError("transaction_id_invalid")
    local_control = _lexical_path(args.local_control_release_root, label="local_control_root")
    remote_control = _lexical_path(args.remote_control_release_root, label="remote_control_root")
    if local_control.name != sha or remote_control.name != sha:
        raise PlanBuildError("control_release_root_invalid")
    _secure_directory(local_control, label="local_control_root")
    secure_root = _lexical_path(args.secure_transaction_root, label="secure_transaction_root")
    _secure_directory(secure_root, label="secure_transaction_root")
    official_secure_root = Path("/root/secure-envs/trading-bot/release-control")
    if (
        secure_root != official_secure_root
        and not any("production" in part.lower() for part in secure_root.parts)
    ):
        raise PlanBuildError("secure_transaction_root_not_production_scoped")
    output = _lexical_path(args.output, label="plan_output")
    receipt_output = _lexical_path(args.receipt, label="receipt_output")
    if output == receipt_output or output.parent != secure_root or receipt_output.parent != secure_root:
        raise PlanBuildError("output_scope_invalid")

    specs = (
        ("runtime_source", args.runtime_source, args.expected_runtime_source_sha256),
        ("deployment_manifest", args.deployment_manifest, args.expected_deployment_manifest_sha256),
        ("control_manifest", local_control / CONTROL_MANIFEST_NAME, args.expected_control_manifest_sha256),
        ("control_pair_receipt", args.control_pair_receipt, args.expected_control_pair_receipt_sha256),
        ("primary_pair_receipt", args.primary_pair_receipt, args.expected_primary_pair_receipt_sha256),
        ("market_image_receipt", args.market_image_receipt, args.expected_market_image_receipt_sha256),
        ("preflight_receipt", args.preflight_receipt, args.expected_preflight_receipt_sha256),
        ("web_env", args.web_env, args.expected_web_env_sha256),
        ("bot_env", args.bot_env, args.expected_bot_env_sha256),
        ("web_old_env", args.web_old_env, args.expected_web_old_env_sha256),
        ("bot_old_env", args.bot_old_env, args.expected_bot_old_env_sha256),
        ("product_bot_image_receipt", args.product_bot_image_receipt, args.expected_product_bot_image_receipt_sha256),
        ("product_web_image_receipt", args.product_web_image_receipt, args.expected_product_web_image_receipt_sha256),
        ("private_manifest", args.private_manifest, args.expected_private_manifest_sha256),
        ("private_manifest_receipt", args.private_manifest_receipt, args.expected_private_manifest_receipt_sha256),
    )
    bound = {label: _read_bound(Path(path), str(digest), label=label) for label, path, digest in specs}
    tools = (
        "upgrade_market_pipeline_bluegreen.py", "backup_market_pipeline_archive.py",
        "crypt_market_pipeline_backup.py", "migrate_market_pipeline_archive.py",
        "rollout_market_pipeline_shadow.py", "quiesce_production_legacy_market_collectors.py",
        "audit_production_market_catchup.py", "observe_production_private_primary.py",
        "run_release_bound_product_readiness.py", "reconcile_estimator_snapshot_publication_outbox.py",
        "verify_production_private_primary_promotion.py", "promote_production_private_primary_product.py",
    )
    control_entries = _control_manifest(local_control, bound["control_manifest"], tools)
    builder_script_sha256 = control_entries.get(
        "scripts/build_production_private_primary_choreography_plan.py", ""
    )
    if not HEX64.fullmatch(builder_script_sha256):
        raise PlanBuildError("control_manifest_required_file_missing")
    if Path(args.control_pair_receipt) != local_control / CONTROL_PAIR_NAME:
        raise PlanBuildError("control_pair_receipt_decoy_path")

    runtime_values = _env(bound["runtime_source"])
    deployment_values = _env(bound["deployment_manifest"])
    web_values, bot_values = _env(bound["web_env"]), _env(bound["bot_env"])
    web_old_values, bot_old_values = _env(bound["web_old_env"]), _env(bound["bot_old_env"])
    if runtime_values.get("PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE", "LEGACY") != "LEGACY":
        raise PlanBuildError("runtime_source_not_legacy")
    for role, values, old_values in (("web", web_values, web_old_values), ("bot", bot_values, bot_old_values)):
        root_key = "MARKET_WEB_DATA_ROOT" if role == "web" else "MARKET_BOT_DATA_ROOT"
        required = (root_key, "MARKET_PRODUCT_SNAPSHOT_ROOT", "MARKET_PIPELINE_PROJECT_NAME", "MARKET_PIPELINE_IMAGE", "MARKET_PIPELINE_RELEASE_SHA", "MARKET_PIPELINE_FEED_MODE")
        if any(not values.get(key) for key in required) or values["MARKET_PIPELINE_RELEASE_SHA"] != sha or values["MARKET_PIPELINE_FEED_MODE"] != "PRIVATE_PRIMARY" or not IMAGE_ID.fullmatch(values["MARKET_PIPELINE_IMAGE"]):
            raise PlanBuildError(f"{role}_env_contract_invalid")
        if not old_values.get("MARKET_PIPELINE_PROJECT_NAME") or old_values.get("MARKET_PIPELINE_FEED_MODE") == "PRIVATE_PRIMARY":
            raise PlanBuildError(f"{role}_old_env_contract_invalid")
        for value in (values[root_key], values["MARKET_PRODUCT_SNAPSHOT_ROOT"]):
            _lexical_path(value, label=f"{role}_env")
        if not SAFE_NAME.fullmatch(values["MARKET_PIPELINE_PROJECT_NAME"]) or not SAFE_NAME.fullmatch(old_values["MARKET_PIPELINE_PROJECT_NAME"]):
            raise PlanBuildError(f"{role}_project_invalid")
    for key in ("MARKET_POSTGRES_USER", "MARKET_POSTGRES_DB"):
        if not SAFE_NAME.fullmatch(web_values.get(key, "")):
            raise PlanBuildError("web_postgres_identity_invalid")

    control_pair = _json(bound["control_pair_receipt"])
    _require_release(control_pair, sha, tree, label="control_pair_receipt")
    if control_pair.get("schema") not in {
        "market_pipeline_release_pair/1.0",
        "market_pipeline_release_pair/1.1",
        "market_pipeline_primary_release_pair/1.0",
        "market_pipeline_primary_release_pair/1.1",
    }:
        raise PlanBuildError("control_pair_receipt_schema_invalid")
    if str(control_pair.get("schema") or "").startswith("market_pipeline_primary_release_pair") and (
        control_pair.get("feed_mode") != "PRIVATE_PRIMARY"
        or control_pair.get("product_authority_changed") is not False
    ):
        raise PlanBuildError("control_pair_receipt_schema_invalid")
    market_image = _json(bound["market_image_receipt"])
    expected_market_fixed = {"schema": "market_pipeline_image_release/1.0", "environment": "production", "release_sha": sha, "release_tree": tree, "platform": "linux/amd64", "runtime_user": "10001:10001", "transport": "ssh_stream_then_verify_content_id", "secrets_disclosed": False}
    if any(market_image.get(k) != v for k, v in expected_market_fixed.items()) or set(market_image) != {*expected_market_fixed, "image_id", "input_signature"} or not IMAGE_ID.fullmatch(str(market_image.get("image_id") or "")) or not HEX64.fullmatch(str(market_image.get("input_signature") or "")):
        raise PlanBuildError("market_image_receipt_invalid")
    market_id, market_signature = str(market_image["image_id"]), str(market_image["input_signature"])

    primary_pair = _json(bound["primary_pair_receipt"])
    _require_release(primary_pair, sha, tree, label="primary_pair_receipt")
    if primary_pair.get("schema") not in {"market_pipeline_primary_release_pair/1.0", "market_pipeline_primary_release_pair/1.1"} or primary_pair.get("feed_mode") != "PRIVATE_PRIMARY" or primary_pair.get("private_primary_allowed") is not True or primary_pair.get("expected_snapshot_lane") != "PRIVATE_PRIMARY" or primary_pair.get("product_authority_changed") is not False or primary_pair.get("legacy_retirement_authorized") is not False:
        raise PlanBuildError("primary_pair_receipt_invalid")
    market_images = _image_ids(primary_pair, label="primary_pair_receipt")
    if set(market_images.values()) != {market_id}:
        raise PlanBuildError("primary_pair_market_image_mismatch")
    roles = primary_pair.get("roles")
    if not isinstance(roles, dict) or set(roles) != {"bot", "web"}:
        raise PlanBuildError("primary_pair_roles_invalid")
    for role, env_item, values in (("web", bound["web_env"], web_values), ("bot", bound["bot_env"], bot_values)):
        row = roles.get(role)
        if not isinstance(row, dict) or row.get("output_sha256") != env_item.digest or row.get("product_snapshot_root") != values["MARKET_PRODUCT_SNAPSHOT_ROOT"]:
            raise PlanBuildError("primary_pair_env_binding_invalid")

    preflight = _json(bound["preflight_receipt"])
    _require_release(preflight, sha, tree, label="preflight_receipt")
    if preflight.get("schema") != "market_pipeline_two_host_preflight/1.0" or preflight.get("environment") != "production" or preflight.get("image_id") != market_id or preflight.get("image_input_signature") != market_signature or preflight.get("control_payload_manifest_sha256") != bound["control_manifest"].digest or preflight.get("role_env_sha256") != {"bot": bound["bot_env"].digest, "web": bound["web_env"].digest} or preflight.get("private_shadow_only") is not True or preflight.get("image_loaded_on_both_hosts") is not True or preflight.get("services_started") is not False or preflight.get("database_mutated") is not False or preflight.get("product_authority_changed") is not False:
        raise PlanBuildError("preflight_receipt_invalid")
    host_preflight = preflight.get("host_preflight_sha256")
    if not isinstance(host_preflight, dict) or set(host_preflight) != {"bot", "web"} or any(not HEX64.fullmatch(str(v)) for v in host_preflight.values()):
        raise PlanBuildError("preflight_host_digest_invalid")

    ssh_target = str(args.expected_web_ssh_target)
    ssh_argv = _ssh_argv(deployment_values, ssh_target)
    if _argv_digest(ssh_argv) != args.expected_web_ssh_argv_sha256:
        raise PlanBuildError("web_ssh_argv_digest_mismatch")
    web_host = ssh_target.split("@", 1)[1]
    product_images = {
        "bot": _product_image_receipt(_json(bound["product_bot_image_receipt"]), sha, tree, role="bot", web_host=web_host),
        "web": _product_image_receipt(_json(bound["product_web_image_receipt"]), sha, tree, role="web", web_host=web_host),
    }
    private_receipt = _json(bound["private_manifest_receipt"])
    if private_receipt.get("schema") != "production_private_primary_deploy_manifest/1.0" or private_receipt.get("status") != "PASS" or private_receipt.get("source_sha256") != bound["deployment_manifest"].digest or private_receipt.get("output_sha256") != bound["private_manifest"].digest or private_receipt.get("source_preserved_by_tool") is not True or private_receipt.get("secrets_disclosed") is not False:
        raise PlanBuildError("private_manifest_receipt_invalid")

    web_backup_root = _lexical_path(args.web_backup_root, label="web_backup_root")
    local_backup_root = _lexical_path(args.local_offhost_backup_root, label="local_backup_root")
    web_key = _lexical_path(args.web_backup_key_file, label="web_backup_key")
    local_key = _lexical_path(args.local_backup_key_file, label="local_backup_key")
    _secure_directory(local_backup_root, label="local_backup_root")
    _secure_secret_metadata(local_key, label="local_backup_key")
    remote_web_env = _lexical_path(args.remote_web_env, label="remote_web_env")
    remote_web_old_env = _lexical_path(
        args.remote_web_old_env, label="remote_web_old_env"
    )
    # The new role envs are immutable release artifacts.  The web command must
    # consume the exact remote counterpart of the locally digest-bound file;
    # accepting an arbitrary remote path here would break that provenance.
    web_env_relative = _control_relative(
        bound["web_env"].path, local_control, label="web_env"
    )
    _control_relative(bound["bot_env"].path, local_control, label="bot_env")
    if remote_web_env != remote_control / web_env_relative:
        raise PlanBuildError("remote_web_env_control_mapping_invalid")
    release_checkout = _lexical_path(args.release_checkout, label="release_checkout")
    try:
        checkout_info = release_checkout.lstat()
    except OSError as exc:
        raise PlanBuildError("release_checkout_unavailable") from exc
    if release_checkout.is_symlink() or not stat.S_ISDIR(checkout_info.st_mode):
        raise PlanBuildError("release_checkout_invalid")
    phases = _build_commands(
        sha=sha, tree=tree, local_root=local_control, remote_root=remote_control,
        secure_root=secure_root, web_backup_root=web_backup_root,
        local_backup_root=local_backup_root, web_key=web_key, local_key=local_key,
        web_env=bound["web_env"].path, remote_web_env=remote_web_env,
        bot_env=bound["bot_env"].path,
        web_old_env=bound["web_old_env"].path,
        remote_web_old_env=remote_web_old_env,
        bot_old_env=bound["bot_old_env"].path,
        web_values=web_values, bot_values=bot_values, web_old_values=web_old_values,
        bot_old_values=bot_old_values, market_images=market_images,
        market_signature=market_signature, web_preflight_digest=str(host_preflight["web"]),
        control_digest=bound["control_manifest"].digest, product_images=product_images,
        runtime_source=bound["runtime_source"].path,
        deployment_manifest=bound["deployment_manifest"].path,
        private_manifest=bound["private_manifest"].path,
        private_manifest_digest=bound["private_manifest"].digest,
        private_manifest_receipt=bound["private_manifest_receipt"].path,
        private_manifest_receipt_digest=bound["private_manifest_receipt"].digest,
        runtime_source_digest=bound["runtime_source"].digest,
        deployment_manifest_digest=bound["deployment_manifest"].digest,
        release_checkout=release_checkout,
        transaction_id=transaction_id,
    )
    plan: dict[str, object] = {
        "schema": PLAN_SCHEMA, "release_sha": sha, "release_tree": tree,
        "approved_release_ref": APPROVED_RELEASE_REF,
        "source_manifest": str(bound["runtime_source"].path),
        "deployment_manifest": str(bound["deployment_manifest"].path),
        "expected_source_sha256": bound["runtime_source"].digest,
        "controller_lock": str(secure_root / CONTROLLER_LOCK_NAME),
        "local_control_release_root": str(local_control),
        "remote_control_release_root": str(remote_control),
        "control_payload_manifest_sha256": bound["control_manifest"].digest,
        "product_image_ids": product_images,
        "role_env_bindings": {
            "bot": {
                "new_path": str(bound["bot_env"].path),
                "new_sha256": bound["bot_env"].digest,
                "old_path": str(bound["bot_old_env"].path),
                "old_sha256": bound["bot_old_env"].digest,
            },
            "web": {
                "new_path": str(remote_web_env),
                "new_sha256": bound["web_env"].digest,
                "old_path": str(remote_web_old_env),
                "old_sha256": bound["web_old_env"].digest,
            },
        },
        "transaction_id": transaction_id,
        "builder_tool": "scripts/build_production_private_primary_choreography_plan.py",
        "web_ssh_argv": ssh_argv,
        "product_authority_initial": "LEGACY",
        "product_authority_final": "PRIVATE_PRIMARY",
        "legacy_collectors_restart_forbidden": True,
        "product_promotion_last": True,
        "secrets_disclosed": False,
        "phases": phases,
    }
    plan_payload = _canonical(plan)
    input_digests = {label: item.digest for label, item in sorted(bound.items())}
    if tuple(input_digests) != REQUIRED_INPUT_LABELS:
        raise PlanBuildError("required_input_inventory_invalid")
    receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA, "status": "PASS", "release_sha": sha,
        "release_tree": tree, "approved_release_ref": APPROVED_RELEASE_REF,
        "transaction_id": transaction_id,
        "plan_sha256": _digest(plan_payload), "phase_count": len(phases),
        "command_count": sum(len(phase["commands"]) for phase in phases),
        "plan_output_path_sha256": _digest(str(output).encode("utf-8")),
        "receipt_output_path_sha256": _digest(
            str(receipt_output).encode("utf-8")
        ),
        "required_input_labels": list(REQUIRED_INPUT_LABELS),
        "builder_tool": "scripts/build_production_private_primary_choreography_plan.py",
        "builder_script_sha256": builder_script_sha256,
        "input_sha256": input_digests,
        "input_paths": {label: str(item.path) for label, item in sorted(bound.items())},
        "path_sha256": {
            label: _digest(str(path).encode("utf-8"))
            for label, path in sorted(
                {
                    "local_control_release_root": local_control,
                    "remote_control_release_root": remote_control,
                    "release_checkout": release_checkout,
                    "remote_web_env": remote_web_env,
                    "remote_web_old_env": remote_web_old_env,
                    "web_backup_root": web_backup_root,
                    "local_offhost_backup_root": local_backup_root,
                    "web_backup_key_file": web_key,
                    "local_backup_key_file": local_key,
                    "secure_transaction_root": secure_root,
                }.items()
            )
        },
        "web_ssh_argv_sha256": _argv_digest(ssh_argv),
        "product_image_ids": product_images,
        "secret_values_included": False, "live_state_inspected": False,
        "git_inspected": False, "recovery_commands_embedded": False,
        "rollback_commands_embedded": False, "secrets_disclosed": False,
    }
    for item in bound.values():
        _assert_stable(item)
    _write_exclusive(output, plan_payload, label="plan_output")
    try:
        _write_exclusive(receipt_output, _canonical(receipt), label="receipt_output")
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return plan, receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-sha", required=True); parser.add_argument("--release-tree", required=True)
    for name in (
        "runtime-source", "deployment-manifest", "control-pair-receipt",
        "primary-pair-receipt", "market-image-receipt", "preflight-receipt",
        "web-env", "bot-env", "web-old-env", "bot-old-env",
        "product-bot-image-receipt", "product-web-image-receipt",
        "private-manifest", "private-manifest-receipt",
    ):
        parser.add_argument(f"--{name}", required=True)
        parser.add_argument(f"--expected-{name}-sha256", required=True)
    parser.add_argument("--local-control-release-root", required=True)
    parser.add_argument("--remote-control-release-root", required=True)
    parser.add_argument("--expected-control-manifest-sha256", required=True)
    parser.add_argument("--expected-web-ssh-target", required=True)
    parser.add_argument("--expected-web-ssh-argv-sha256", required=True)
    parser.add_argument("--web-backup-root", required=True)
    parser.add_argument("--local-offhost-backup-root", required=True)
    parser.add_argument("--web-backup-key-file", required=True)
    parser.add_argument("--local-backup-key-file", required=True)
    parser.add_argument("--remote-web-env", required=True)
    parser.add_argument("--remote-web-old-env", required=True)
    parser.add_argument("--release-checkout", required=True)
    parser.add_argument("--secure-transaction-root", required=True)
    parser.add_argument("--transaction-id", required=True)
    parser.add_argument("--output", required=True); parser.add_argument("--receipt", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        _plan, receipt = build(build_parser().parse_args(argv))
        print(json.dumps({"schema": RECEIPT_SCHEMA, "status": "PASS", "plan_sha256": receipt["plan_sha256"], "phase_count": receipt["phase_count"], "command_count": receipt["command_count"], "secrets_disclosed": False}, sort_keys=True))
        return 0
    except PlanBuildError as exc:
        print(json.dumps({"schema": RECEIPT_SCHEMA, "status": "BLOCKED", "reason": str(exc), "secrets_disclosed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
