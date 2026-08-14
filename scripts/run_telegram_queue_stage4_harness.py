#!/usr/bin/env python3
"""Create/verify Stage 4 inputs or run owner-authorized staging drivers."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.telegram_queue_stage4_harness import (
    STAGE4_ATTESTATION_FILES,
    STAGE4_LIVE_EVIDENCE_FILES,
    STAGE4_PLAN_FILES,
    STAGE4_ROLE_EVIDENCE_FILES,
    Stage4HarnessValidationError,
    canonical_json,
    sha256_file,
    sha256_text,
    stage4_bound_value_fingerprint,
    stage4_config_binding_sha256,
    validate_stage4_run_config,
    verify_stage4_live_evidence,
    verify_stage4_plan,
    verify_stage4_role_attestation,
    write_stage4_plan,
)
from scripts.scan_telegram_queue_artifacts import scan_paths


_AUTHORIZATION_MAX_LIFETIME_SECONDS = 3600
STAGE4_TRUST_POLICY_PATH = Path(
    "/etc/trading-bot/stage4/stage4-trust-policy.json"
)
_SAFE_BASE_ENVIRONMENT = (
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "TZ",
)
_SENDER_STAGING_ENVIRONMENT = (
    "STAGE4_STAGING_DATABASE_URL",
    "STAGE4_STAGING_REDIS_URL",
    "STAGE4_STAGING_PRIMARY_BOT_TOKEN",
    "STAGE4_STAGING_CHANNEL_ID",
)
_OBSERVER_STAGING_ENVIRONMENT = (
    "STAGE4_STAGING_OBSERVER_DATABASE_URL",
    "STAGE4_STAGING_RECEIVER_SESSION",
    "STAGE4_STAGING_CHANNEL_ID",
)
_ENVIRONMENT_FINGERPRINT_BINDINGS = {
    "sender": {
        "database": "STAGE4_STAGING_DATABASE_URL",
        "redis": "STAGE4_STAGING_REDIS_URL",
        "primary_bot": "STAGE4_STAGING_PRIMARY_BOT_TOKEN",
        "channel_editor_bot": "STAGE4_STAGING_CHANNEL_EDITOR_BOT_TOKEN",
        "channel": "STAGE4_STAGING_CHANNEL_ID",
    },
    "observer": {
        "observer_database": "STAGE4_STAGING_OBSERVER_DATABASE_URL",
        "receiver_session": "STAGE4_STAGING_RECEIVER_SESSION",
        "channel": "STAGE4_STAGING_CHANNEL_ID",
    },
}
_TRUST_POLICY_FIELDS = {
    "schema_version",
    "authorization_public_key",
    "sender_evidence_public_key",
    "observer_evidence_public_key",
    "sender_uid",
    "sender_gid",
    "observer_uid",
    "observer_gid",
    "sender_signing_key_path",
    "observer_signing_key_path",
    "authorization_registry_path",
    "evidence_workspace_root",
    "driver_root",
    "host_identity_sha256",
}


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _git_commit(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _git_clean(repo: Path) -> bool:
    return not subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repo, text=True
    ).strip()


def _public_key(value: Any, *, name: str) -> bytes:
    try:
        key = base64.b64decode(str(value), validate=True)
    except (ValueError, TypeError) as exc:
        raise Stage4HarnessValidationError(
            f"stage4_trust_policy_{name}_invalid"
        ) from exc
    if len(key) != 32:
        raise Stage4HarnessValidationError(
            f"stage4_trust_policy_{name}_invalid"
        )
    return key


def _secure_root_parent_chain(path: Path, *, name: str) -> None:
    current = path
    while True:
        if current.is_symlink() or not current.is_dir():
            raise Stage4HarnessValidationError(
                f"stage4_{name}_parent_permissions_invalid"
            )
        info = current.stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
            raise Stage4HarnessValidationError(
                f"stage4_{name}_parent_permissions_invalid"
            )
        if current.parent == current:
            return
        current = current.parent


def _secure_root_owned_file(path: Path, *, name: str) -> os.stat_result:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Stage4HarnessValidationError(f"stage4_{name}_file_invalid")
    info = path.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise Stage4HarnessValidationError(
            f"stage4_{name}_ownership_or_permissions_invalid"
        )
    _secure_root_parent_chain(path.parent, name=name)
    return info


def _secure_root_directory(path: Path, *, name: str) -> Path:
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise Stage4HarnessValidationError(f"stage4_{name}_directory_invalid")
    info = path.stat()
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
        raise Stage4HarnessValidationError(
            f"stage4_{name}_directory_permissions_invalid"
        )
    _secure_root_parent_chain(path.parent, name=name)
    return path.resolve()


def _host_identity_sha256() -> str:
    machine_id_path = Path("/etc/machine-id")
    _secure_root_owned_file(machine_id_path, name="machine_identity")
    machine_id = machine_id_path.read_text(encoding="ascii").strip()
    if not machine_id:
        raise Stage4HarnessValidationError("stage4_machine_identity_missing")
    return "sha256:" + sha256_text(
        "telegram-stage4-authorized-host-v1:" + machine_id
    )


def _load_trust_policy(path: Path = STAGE4_TRUST_POLICY_PATH) -> dict[str, Any]:
    """Load the fixed deployment-owned trust policy.

    The CLI intentionally exposes no trust-policy path.  Tests may call this
    helper with a provisioned temporary root-owned policy, but live execution
    always uses the constant path above.
    """
    _secure_root_owned_file(path, name="trust_policy")
    raw = _json(path)
    if (
        not isinstance(raw, Mapping)
        or set(raw) != _TRUST_POLICY_FIELDS
        or raw.get("schema_version") != 1
    ):
        raise Stage4HarnessValidationError("stage4_trust_policy_schema_invalid")
    ids = {
        name: raw.get(name)
        for name in ("sender_uid", "sender_gid", "observer_uid", "observer_gid")
    }
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in ids.values()
    ) or ids["sender_uid"] == ids["observer_uid"] or ids["sender_gid"] == ids["observer_gid"]:
        raise Stage4HarnessValidationError("stage4_trust_policy_identity_invalid")
    if raw.get("host_identity_sha256") != _host_identity_sha256():
        raise Stage4HarnessValidationError(
            "stage4_trust_policy_host_identity_mismatch"
        )
    registry_path = Path(str(raw["authorization_registry_path"]))
    workspace_root = Path(str(raw["evidence_workspace_root"]))
    driver_root = Path(str(raw["driver_root"]))
    if not registry_path.is_absolute() or registry_path.is_symlink():
        raise Stage4HarnessValidationError(
            "stage4_authorization_registry_path_invalid"
        )
    _secure_root_directory(registry_path.parent, name="authorization_registry")
    _secure_root_directory(workspace_root, name="evidence_workspace")
    _secure_root_directory(driver_root, name="driver_root")
    signing_keys: dict[str, Path] = {}
    evidence_public_keys: dict[str, bytes] = {}
    for role in ("sender", "observer"):
        key_path = Path(str(raw[f"{role}_signing_key_path"]))
        if not key_path.is_absolute() or key_path.is_symlink() or not key_path.is_file():
            raise Stage4HarnessValidationError(
                f"stage4_{role}_signing_key_file_invalid"
            )
        key_info = key_path.stat()
        if (
            key_info.st_uid != ids[f"{role}_uid"]
            or stat.S_IMODE(key_info.st_mode) != 0o600
        ):
            raise Stage4HarnessValidationError(
                f"stage4_{role}_signing_key_permissions_invalid"
            )
        _secure_root_directory(key_path.parent, name=f"{role}_signing_key_parent")
        try:
            private_key = Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())
        except (OSError, ValueError) as exc:
            raise Stage4HarnessValidationError(
                f"stage4_{role}_signing_key_material_invalid"
            ) from exc
        configured_public_key = _public_key(
            raw[f"{role}_evidence_public_key"], name=f"{role}_public_key"
        )
        derived_public_key = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if configured_public_key != derived_public_key:
            raise Stage4HarnessValidationError(
                f"stage4_{role}_signing_key_pair_mismatch"
            )
        signing_keys[role] = key_path.resolve()
        evidence_public_keys[role] = configured_public_key
    if (
        signing_keys["sender"] == signing_keys["observer"]
        or len(
            {
                _public_key(
                    raw["authorization_public_key"],
                    name="authorization_public_key",
                ),
                evidence_public_keys["sender"],
                evidence_public_keys["observer"],
            }
        )
        != 3
    ):
        raise Stage4HarnessValidationError("stage4_trust_policy_key_separation_invalid")
    canonical = canonical_json(raw)
    return {
        "raw": dict(raw),
        "sha256": "sha256:" + sha256_text(canonical),
        "public_keys": {
            "authorization": _public_key(
                raw["authorization_public_key"], name="authorization_public_key"
            ),
            "sender": evidence_public_keys["sender"],
            "observer": evidence_public_keys["observer"],
        },
        "identities": ids,
        "signing_keys": signing_keys,
        "authorization_registry_path": registry_path.resolve(),
        "evidence_workspace_root": workspace_root,
        "driver_root": driver_root,
    }


def _command(
    value: Any,
    *,
    name: str,
    driver_root: Path,
) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or len(value) != 1
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise Stage4HarnessValidationError(f"stage4_{name}_command_invalid")
    executable = Path(value[0])
    if (
        not executable.is_absolute()
        or executable.is_symlink()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise Stage4HarnessValidationError(f"stage4_{name}_executable_invalid")
    resolved = executable.resolve()
    _secure_root_parent_chain(resolved.parent, name=f"{name}_executable")
    info = resolved.stat()
    if (
        info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o222
        or not (resolved == driver_root or driver_root in resolved.parents)
    ):
        raise Stage4HarnessValidationError(
            f"stage4_{name}_executable_not_immutable_or_trusted"
        )
    return (str(resolved), *value[1:])


def _validate_authorization(
    *,
    authorization: Mapping[str, Any],
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    git_commit: str,
    sender: Sequence[str],
    observer: Sequence[str],
    driver_executables_sha256: Mapping[str, str],
    trusted_public_key: bytes,
) -> None:
    now = datetime.now(timezone.utc)
    try:
        not_before = datetime.fromisoformat(
            str(authorization["not_before"]).replace("Z", "+00:00")
        )
        expires = datetime.fromisoformat(
            str(authorization["expires_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise Stage4HarnessValidationError(
            "stage4_authorization_expiry_invalid"
        ) from exc
    expected = {
        "schema_version": 2,
        "allow_live_staging": True,
        "run_id": manifest["run_id"],
        "plan_nonce": manifest["plan_nonce"],
        "git_commit": git_commit,
        "trace_sha256": manifest["trace_sha256"],
        "config_sha256": stage4_config_binding_sha256(config),
        "trust_policy_sha256": manifest["trust_policy_sha256"],
        "driver_commands_sha256": sha256_text(
            canonical_json({"sender": list(sender), "observer": list(observer)})
        ),
        "driver_executables_sha256": dict(driver_executables_sha256),
    }
    allowed_keys = {
        *expected,
        "authorization_id",
        "not_before",
        "expires_at",
        "signature",
    }
    if set(authorization) != allowed_keys:
        raise Stage4HarnessValidationError("stage4_authorization_fields_invalid")
    for key, value in expected.items():
        if authorization.get(key) != value:
            raise Stage4HarnessValidationError(f"stage4_authorization_mismatch:{key}")
    if (
        not_before.tzinfo is None
        or expires.tzinfo is None
        or not_before.astimezone(timezone.utc) > now
        or expires.astimezone(timezone.utc) <= now
        or (expires - not_before).total_seconds()
        > _AUTHORIZATION_MAX_LIFETIME_SECONDS
        or not str(authorization.get("authorization_id") or "").startswith(
            "stage4-auth-"
        )
    ):
        raise Stage4HarnessValidationError("stage4_authorization_expired")
    signed_payload = {
        key: value for key, value in authorization.items() if key != "signature"
    }
    try:
        signature = base64.b64decode(
            str(authorization["signature"]), validate=True
        )
        Ed25519PublicKey.from_public_bytes(trusted_public_key).verify(
            signature, canonical_json(signed_payload).encode("utf-8")
        )
    except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
        raise Stage4HarnessValidationError(
            "stage4_authorization_signature_invalid"
        ) from exc


def _consume_authorization_once(
    *,
    registry_path: Path,
    authorization: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    """Atomically consume an authorization outside the mutable run directory."""
    connection = sqlite3.connect(str(registry_path), timeout=30.0)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS consumed_authorizations (
                authorization_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                plan_nonce TEXT NOT NULL,
                authorization_sha256 TEXT NOT NULL,
                consumed_at TEXT NOT NULL
            )
            """
        )
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO consumed_authorizations VALUES (?, ?, ?, ?, ?)",
                (
                    str(authorization["authorization_id"]),
                    str(manifest["run_id"]),
                    str(manifest["plan_nonce"]),
                    sha256_text(canonical_json(authorization)),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise Stage4HarnessValidationError(
                "stage4_authorization_already_consumed"
            ) from exc
        connection.commit()
    finally:
        connection.close()
    os.chmod(registry_path, 0o600)


def _driver_environment(
    *,
    profile: str,
    output_dir: Path,
    plan_dir: Path,
    manifest: Mapping[str, Any],
    signing_key_path: Path,
) -> dict[str, str]:
    required = (
        _SENDER_STAGING_ENVIRONMENT
        if profile == "sender"
        else _OBSERVER_STAGING_ENVIRONMENT
    )
    if manifest["bot_mode"] == "primary-and-channel-editor" and profile == "sender":
        required = (*required, "STAGE4_STAGING_CHANNEL_EDITOR_BOT_TOKEN")
    missing = [name for name in required if not str(os.environ.get(name) or "")]
    if missing:
        raise Stage4HarnessValidationError(
            f"stage4_{profile}_staging_environment_incomplete"
        )
    fingerprint_bindings = _ENVIRONMENT_FINGERPRINT_BINDINGS[profile]
    enabled_bindings = {
        key: environment_name
        for key, environment_name in fingerprint_bindings.items()
        if environment_name in required
    }
    if any(
        stage4_bound_value_fingerprint(str(os.environ[environment_name]))
        != str(manifest["staging_fingerprints"][key])
        for key, environment_name in enabled_bindings.items()
    ):
        raise Stage4HarnessValidationError(
            f"stage4_{profile}_staging_environment_fingerprint_mismatch"
        )
    run_env = {
        name: str(os.environ[name])
        for name in (*_SAFE_BASE_ENVIRONMENT, *required)
        if name in os.environ
    }
    run_env.update(
        {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "STAGE4_OUTPUT_DIR": str(output_dir.resolve()),
            "STAGE4_PLAN_DIR": str(plan_dir.resolve()),
            "STAGE4_MANIFEST": str((plan_dir / "manifest.json").resolve()),
            "STAGE4_EVENT_TRACE": str((plan_dir / "event_trace.jsonl").resolve()),
            "STAGE4_NETWORK_PROFILE": profile,
            "STAGE4_PLAN_NONCE": str(manifest["plan_nonce"]),
            "STAGE4_EVIDENCE_SIGNING_KEY_FILE": str(signing_key_path),
            "STAGE4_EXPECTED_NETWORK_POLICY_SHA256": str(
                manifest["network_policy_fingerprints"][profile]
            ),
            "STAGE4_EXPECTED_TRUST_POLICY_SHA256": str(
                manifest["trust_policy_sha256"]
            ),
        }
    )
    return run_env


def _isolated_output_directory(
    *,
    workspace_root: Path,
    run_id: str,
    role: str,
    uid: int,
    gid: int,
) -> Path:
    path = Path(
        tempfile.mkdtemp(prefix=f".{run_id}-{role}-", dir=workspace_root)
    )
    os.chown(path, uid, gid)
    os.chmod(path, 0o700)
    return path


def _verify_ready_receipt(
    path: Path,
    *,
    manifest: Mapping[str, Any],
    process_pid: int,
    observer_executable_sha256: str,
    observer_public_key: bytes,
    observer_uid: int,
    not_before: datetime,
) -> bool:
    try:
        if path.is_symlink() or not path.is_file():
            return False
        path_info = path.stat()
        if (
            path_info.st_uid != observer_uid
            or stat.S_IMODE(path_info.st_mode) != 0o600
        ):
            return False
        envelope = _json(path)
        if not isinstance(envelope, Mapping) or set(envelope) != {
            "payload",
            "signature",
        }:
            return False
        payload = envelope["payload"]
        if not isinstance(payload, Mapping) or set(payload) != {
            "schema_version",
            "role",
            "run_id",
            "plan_nonce",
            "trace_sha256",
            "trust_policy_sha256",
            "network_policy_sha256",
            "driver_executable_sha256",
            "process_pid",
            "emitted_at",
        }:
            return False
        emitted_at = datetime.fromisoformat(
            str(payload["emitted_at"]).replace("Z", "+00:00")
        )
        if (
            payload["schema_version"] != 1
            or payload["role"] != "observer"
            or payload["run_id"] != manifest["run_id"]
            or payload["plan_nonce"] != manifest["plan_nonce"]
            or payload["trace_sha256"] != manifest["trace_sha256"]
            or payload["trust_policy_sha256"] != manifest["trust_policy_sha256"]
            or payload["network_policy_sha256"]
            != manifest["network_policy_fingerprints"]["observer"]
            or payload["driver_executable_sha256"]
            != observer_executable_sha256
            or payload["process_pid"] != process_pid
            or emitted_at.tzinfo is None
            or emitted_at < not_before
            or abs((datetime.now(timezone.utc) - emitted_at).total_seconds()) > 60
        ):
            return False
        signature = base64.b64decode(str(envelope["signature"]), validate=True)
        Ed25519PublicKey.from_public_bytes(observer_public_key).verify(
            signature, canonical_json(payload).encode("utf-8")
        )
        return True
    except (OSError, ValueError, TypeError, KeyError, InvalidSignature):
        return False


def _collect_role_evidence(
    *,
    role: str,
    source: Path,
    destination: Path,
    manifest: Mapping[str, Any],
    public_key: bytes,
    authorization: Mapping[str, Any],
) -> None:
    expected = {
        *STAGE4_ROLE_EVIDENCE_FILES[role],
        f"{role}_attestation.json",
    }
    if role == "observer":
        expected.add("observer.ready.json")
    actual = {entry.name for entry in source.iterdir()}
    if actual != expected or any(
        not (source / name).is_file() or (source / name).is_symlink()
        for name in expected
    ):
        raise Stage4HarnessValidationError(
            f"stage4_{role}_output_inventory_invalid"
        )
    # The shared verifier needs the signed authorization only to bind the
    # executable digest while validating the temporary role envelope.
    _write_json(source / "execution_authorization.json", authorization)
    try:
        verify_stage4_role_attestation(
            source,
            role=role,
            manifest=manifest,
            public_key=public_key,
        )
    finally:
        (source / "execution_authorization.json").unlink(missing_ok=True)
    for name in (*STAGE4_ROLE_EVIDENCE_FILES[role], f"{role}_attestation.json"):
        target = destination / name
        if target.exists():
            raise Stage4HarnessValidationError(
                "stage4_evidence_destination_preexists"
            )
        shutil.copyfile(source / name, target)
        os.chmod(target, 0o444)


def _write_security_scan(output_dir: Path) -> None:
    targets = [
        entry
        for entry in sorted(output_dir.iterdir(), key=lambda item: item.name)
        if entry.name != "security_scan.json"
    ]
    if any(not path.is_file() or path.is_symlink() for path in targets):
        raise Stage4HarnessValidationError("stage4_evidence_inventory_invalid")
    report = scan_paths(targets)
    report["self_excluded_file"] = "security_scan.json"
    report["self_excluded_reason"] = "verifier_generated_recursive_manifest"
    if report.get("status") != "clean":
        raise Stage4HarnessValidationError("stage4_security_scan_blocked")
    _write_json(output_dir / "security_scan.json", report)
    os.chmod(output_dir / "security_scan.json", 0o444)


def _execute_live(args: argparse.Namespace, repo: Path) -> dict[str, Any]:
    if not args.authorize_live_staging:
        raise Stage4HarnessValidationError("stage4_live_confirmation_flag_required")
    if os.geteuid() != 0:
        raise Stage4HarnessValidationError("stage4_live_runner_requires_root_isolation")
    trust = _load_trust_policy()
    config = _json(args.config)
    validate_stage4_run_config(config, live_execution=True)
    manifest = verify_stage4_plan(args.output_dir)
    if (
        config.get("trust_policy_sha256") != trust["sha256"]
        or manifest.get("trust_policy_sha256") != trust["sha256"]
    ):
        raise Stage4HarnessValidationError("stage4_trust_policy_fingerprint_mismatch")
    commit = _git_commit(repo)
    if not _git_clean(repo) or commit != manifest.get("git_commit"):
        raise Stage4HarnessValidationError(
            "stage4_live_requires_clean_pinned_git_commit"
        )
    if (
        manifest.get("environment") != "staging"
        or manifest.get("run_id") != config.get("run_id")
        or manifest.get("plan_nonce") != config.get("plan_nonce")
    ):
        raise Stage4HarnessValidationError("stage4_live_plan_config_mismatch")
    if manifest.get("config_sha256") != stage4_config_binding_sha256(config):
        raise Stage4HarnessValidationError("stage4_live_plan_config_hash_mismatch")
    root_info = args.output_dir.stat()
    if (
        root_info.st_uid != 0
        or stat.S_IMODE(root_info.st_mode) & 0o022
        or {entry.name for entry in args.output_dir.iterdir()}
        != {"manifest.json", *STAGE4_PLAN_FILES}
    ):
        raise Stage4HarnessValidationError("stage4_live_plan_directory_not_immutable")
    for filename in ("manifest.json", *STAGE4_PLAN_FILES):
        _secure_root_owned_file(
            args.output_dir / filename,
            name=f"plan_{filename.replace('.', '_')}",
        )
    drivers = config.get("drivers")
    if not isinstance(drivers, Mapping):
        raise Stage4HarnessValidationError("stage4_live_drivers_missing")
    sender = _command(
        drivers.get("sender"), name="sender", driver_root=trust["driver_root"]
    )
    observer = _command(
        drivers.get("observer"), name="observer", driver_root=trust["driver_root"]
    )
    driver_hashes = {
        "sender": sha256_file(Path(sender[0])),
        "observer": sha256_file(Path(observer[0])),
    }
    authorization = _json(args.authorization)
    _validate_authorization(
        authorization=authorization,
        config=config,
        manifest=manifest,
        git_commit=commit,
        sender=sender,
        observer=observer,
        driver_executables_sha256=driver_hashes,
        trusted_public_key=trust["public_keys"]["authorization"],
    )
    _consume_authorization_once(
        registry_path=trust["authorization_registry_path"],
        authorization=authorization,
        manifest=manifest,
    )
    _write_json(args.output_dir / "execution_authorization.json", authorization)
    os.chmod(args.output_dir / "execution_authorization.json", 0o444)
    sender_output = _isolated_output_directory(
        workspace_root=trust["evidence_workspace_root"],
        run_id=manifest["run_id"],
        role="sender",
        uid=trust["identities"]["sender_uid"],
        gid=trust["identities"]["sender_gid"],
    )
    observer_output = _isolated_output_directory(
        workspace_root=trust["evidence_workspace_root"],
        run_id=manifest["run_id"],
        role="observer",
        uid=trust["identities"]["observer_uid"],
        gid=trust["identities"]["observer_gid"],
    )
    sender_env = _driver_environment(
        profile="sender",
        output_dir=sender_output,
        plan_dir=args.output_dir,
        manifest=manifest,
        signing_key_path=trust["signing_keys"]["sender"],
    )
    observer_env = _driver_environment(
        profile="observer",
        output_dir=observer_output,
        plan_dir=args.output_dir,
        manifest=manifest,
        signing_key_path=trust["signing_keys"]["observer"],
    )
    observer_process: subprocess.Popen[Any] | None = None
    try:
        if driver_hashes != {
            "sender": sha256_file(Path(sender[0])),
            "observer": sha256_file(Path(observer[0])),
        }:
            raise Stage4HarnessValidationError("stage4_driver_executable_changed")
        observer_started_at = datetime.now(timezone.utc)
        observer_process = subprocess.Popen(
            observer,
            cwd=repo,
            env=observer_env,
            user=trust["identities"]["observer_uid"],
            group=trust["identities"]["observer_gid"],
            extra_groups=(),
        )
        ready = observer_output / "observer.ready.json"
        deadline = time.monotonic() + float(args.observer_ready_timeout)
        while time.monotonic() < deadline and observer_process.poll() is None:
            if ready.is_file() and _verify_ready_receipt(
                ready,
                manifest=manifest,
                process_pid=observer_process.pid,
                observer_executable_sha256=driver_hashes["observer"],
                observer_public_key=trust["public_keys"]["observer"],
                observer_uid=trust["identities"]["observer_uid"],
                not_before=observer_started_at,
            ):
                break
            time.sleep(0.1)
        else:
            raise Stage4HarnessValidationError("stage4_observer_not_ready")
        if sha256_file(Path(sender[0])) != driver_hashes["sender"]:
            raise Stage4HarnessValidationError("stage4_driver_executable_changed")
        sender_result = subprocess.run(
            sender,
            cwd=repo,
            env=sender_env,
            check=False,
            user=trust["identities"]["sender_uid"],
            group=trust["identities"]["sender_gid"],
            extra_groups=(),
        )
        if sender_result.returncode != 0:
            raise Stage4HarnessValidationError("stage4_sender_failed")
        observer_returncode = observer_process.wait(
            timeout=float(args.observer_exit_timeout)
        )
        if observer_returncode != 0:
            raise Stage4HarnessValidationError("stage4_observer_failed")
        # Neither unprivileged role may mutate the root-owned plan. Re-run the
        # exact plan oracle before any output is promoted to final evidence.
        verify_stage4_plan(args.output_dir)
        _collect_role_evidence(
            role="sender",
            source=sender_output,
            destination=args.output_dir,
            manifest=manifest,
            public_key=trust["public_keys"]["sender"],
            authorization=authorization,
        )
        _collect_role_evidence(
            role="observer",
            source=observer_output,
            destination=args.output_dir,
            manifest=manifest,
            public_key=trust["public_keys"]["observer"],
            authorization=authorization,
        )
        _write_security_scan(args.output_dir)
        return verify_stage4_live_evidence(
            args.output_dir,
            evidence_public_keys=trust["public_keys"],
        )
    finally:
        if observer_process is not None and observer_process.poll() is None:
            observer_process.terminate()
            try:
                observer_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                observer_process.kill()
                observer_process.wait(timeout=5)
        shutil.rmtree(sender_output, ignore_errors=True)
        shutil.rmtree(observer_output, ignore_errors=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("plan", "verify-plan", "execute", "verify-live")
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--authorize-live-staging", action="store_true")
    parser.add_argument("--observer-ready-timeout", type=float, default=30.0)
    parser.add_argument("--observer-exit-timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.command == "plan":
            if args.config is None or args.fixture is None or args.seed is None:
                raise Stage4HarnessValidationError("stage4_plan_arguments_missing")
            config = _json(args.config)
            if config.get("environment") == "staging":
                trust = _load_trust_policy()
                if config.get("trust_policy_sha256") != trust["sha256"]:
                    raise Stage4HarnessValidationError(
                        "stage4_trust_policy_fingerprint_mismatch"
                    )
            result = write_stage4_plan(
                output_dir=args.output_dir,
                config=config,
                fixture=_json(args.fixture),
                seed=args.seed,
                git_commit=_git_commit(repo),
            )
        elif args.command == "verify-plan":
            result = verify_stage4_plan(args.output_dir)
        elif args.command == "execute":
            if args.config is None or args.authorization is None:
                raise Stage4HarnessValidationError("stage4_execute_arguments_missing")
            result = _execute_live(args, repo)
        else:
            trust = _load_trust_policy()
            manifest = verify_stage4_plan(args.output_dir)
            if manifest.get("trust_policy_sha256") != trust["sha256"]:
                raise Stage4HarnessValidationError(
                    "stage4_trust_policy_fingerprint_mismatch"
                )
            result = verify_stage4_live_evidence(
                args.output_dir,
                evidence_public_keys=trust["public_keys"],
            )
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(canonical_json({"status": "blocked", "reason": str(exc)}))
        return 2
    print(canonical_json({"status": "ok", "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
