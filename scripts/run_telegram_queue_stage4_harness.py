#!/usr/bin/env python3
"""Create/verify Stage 4 inputs or run explicitly authorized staging drivers."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core.telegram_queue_stage4_harness import (
    Stage4HarnessValidationError,
    canonical_json,
    sha256_text,
    sha256_file,
    stage4_bound_value_fingerprint,
    stage4_config_binding_sha256,
    validate_stage4_run_config,
    verify_stage4_live_evidence,
    verify_stage4_plan,
    write_stage4_plan,
)


_AUTHORIZATION_MAX_LIFETIME_SECONDS = 3600
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


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git_commit(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()


def _git_clean(repo: Path) -> bool:
    return not subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repo, text=True
    ).strip()


def _command(value: Any, *, name: str) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise Stage4HarnessValidationError(f"stage4_{name}_command_invalid")
    executable = Path(value[0])
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or executable.is_symlink()
        or not os.access(executable, os.X_OK)
        or stat.S_IMODE(executable.stat().st_mode) & 0o022
    ):
        raise Stage4HarnessValidationError(f"stage4_{name}_executable_invalid")
    return tuple(value)


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
        expires = datetime.fromisoformat(str(authorization["expires_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise Stage4HarnessValidationError("stage4_authorization_expiry_invalid") from exc
    expected = {
        "schema_version": 1,
        "allow_live_staging": True,
        "run_id": manifest["run_id"],
        "git_commit": git_commit,
        "trace_sha256": manifest["trace_sha256"],
        "config_sha256": stage4_config_binding_sha256(config),
        "driver_commands_sha256": sha256_text(canonical_json({"sender": list(sender), "observer": list(observer)})),
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
        or (expires - not_before).total_seconds() > _AUTHORIZATION_MAX_LIFETIME_SECONDS
        or not str(authorization.get("authorization_id") or "").startswith("stage4-auth-")
    ):
        raise Stage4HarnessValidationError("stage4_authorization_expired")
    signature_text = str(authorization.get("signature") or "")
    signed_payload = {
        key: value for key, value in authorization.items() if key != "signature"
    }
    try:
        signature = base64.b64decode(signature_text, validate=True)
        configured_public_key = base64.b64decode(
            str(config["authorization_public_key"]), validate=True
        )
        if not hmac.compare_digest(configured_public_key, trusted_public_key):
            raise ValueError("untrusted authorization key")
        Ed25519PublicKey.from_public_bytes(trusted_public_key).verify(
            signature,
            canonical_json(signed_payload).encode("utf-8"),
        )
    except (ValueError, TypeError, KeyError, InvalidSignature) as exc:
        raise Stage4HarnessValidationError(
            "stage4_authorization_signature_invalid"
        ) from exc


def _trusted_authorization_public_key(
    path: Path,
    *,
    output_dir: Path,
) -> bytes:
    """Load the deployment trust anchor, never a key declared by the run.

    The key file must be provisioned outside the mutable run directory and
    must not be writable by group/other.  The config carries a copy only so
    its identity is bound into the signed plan; it is not its own trust root.
    """
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Stage4HarnessValidationError(
            "stage4_trusted_authorization_public_key_file_invalid"
        )
    resolved = path.resolve()
    output = output_dir.resolve()
    if resolved == output or output in resolved.parents:
        raise Stage4HarnessValidationError(
            "stage4_trusted_authorization_public_key_file_invalid"
        )
    file_stat = resolved.stat()
    if stat.S_IMODE(file_stat.st_mode) & 0o022:
        raise Stage4HarnessValidationError(
            "stage4_trusted_authorization_public_key_permissions_invalid"
        )
    raw = resolved.read_bytes()
    try:
        key = (
            raw
            if len(raw) == 32
            else base64.b64decode(raw.strip(), validate=True)
        )
    except (ValueError, TypeError) as exc:
        raise Stage4HarnessValidationError(
            "stage4_trusted_authorization_public_key_invalid"
        ) from exc
    if len(key) != 32:
        raise Stage4HarnessValidationError(
            "stage4_trusted_authorization_public_key_invalid"
        )
    return key


def _driver_environment(
    *,
    profile: str,
    output_dir: Path,
    manifest: Mapping[str, Any],
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
            "STAGE4_RUN_DIR": str(output_dir.resolve()),
            "STAGE4_MANIFEST": str((output_dir / "manifest.json").resolve()),
            "STAGE4_EVENT_TRACE": str((output_dir / "event_trace.jsonl").resolve()),
            "STAGE4_NETWORK_PROFILE": profile,
            "STAGE4_EXPECTED_NETWORK_POLICY_SHA256": str(
                manifest["network_policy_fingerprints"][profile]
            ),
        }
    )
    return run_env


def _execute_live(args: argparse.Namespace, repo: Path) -> dict[str, Any]:
    if not args.authorize_live_staging:
        raise Stage4HarnessValidationError("stage4_live_confirmation_flag_required")
    if args.trusted_authorization_public_key is None:
        raise Stage4HarnessValidationError(
            "stage4_trusted_authorization_public_key_required"
        )
    config = _json(args.config)
    validate_stage4_run_config(config, live_execution=True)
    manifest = verify_stage4_plan(args.output_dir)
    commit = _git_commit(repo)
    if not _git_clean(repo) or commit != manifest.get("git_commit"):
        raise Stage4HarnessValidationError("stage4_live_requires_clean_pinned_git_commit")
    if manifest.get("environment") != "staging" or manifest.get("run_id") != config.get("run_id"):
        raise Stage4HarnessValidationError("stage4_live_plan_config_mismatch")
    if manifest.get("config_sha256") != stage4_config_binding_sha256(config):
        raise Stage4HarnessValidationError("stage4_live_plan_config_hash_mismatch")
    drivers = config.get("drivers")
    if not isinstance(drivers, Mapping):
        raise Stage4HarnessValidationError("stage4_live_drivers_missing")
    sender = _command(drivers.get("sender"), name="sender")
    observer = _command(drivers.get("observer"), name="observer")
    driver_hashes = {
        "sender": sha256_file(Path(sender[0])),
        "observer": sha256_file(Path(observer[0])),
    }
    trusted_public_key = _trusted_authorization_public_key(
        args.trusted_authorization_public_key,
        output_dir=args.output_dir,
    )
    _validate_authorization(
        authorization=_json(args.authorization),
        config=config,
        manifest=manifest,
        git_commit=commit,
        sender=sender,
        observer=observer,
        driver_executables_sha256=driver_hashes,
        trusted_public_key=trusted_public_key,
    )
    sender_env = _driver_environment(
        profile="sender", output_dir=args.output_dir, manifest=manifest
    )
    observer_env = _driver_environment(
        profile="observer", output_dir=args.output_dir, manifest=manifest
    )
    if driver_hashes != {
        "sender": sha256_file(Path(sender[0])),
        "observer": sha256_file(Path(observer[0])),
    }:
        raise Stage4HarnessValidationError("stage4_driver_executable_changed")
    authorization_marker = args.output_dir / ".stage4-authorization-consumed"
    try:
        with authorization_marker.open("x", encoding="utf-8") as handle:
            handle.write(
                sha256_text(canonical_json(_json(args.authorization))) + "\n"
            )
    except FileExistsError as exc:
        raise Stage4HarnessValidationError(
            "stage4_authorization_already_consumed"
        ) from exc
    ready = args.output_dir / "observer.ready.json"
    if ready.exists():
        raise Stage4HarnessValidationError("stage4_observer_ready_preexists")
    if sha256_file(Path(observer[0])) != driver_hashes["observer"]:
        raise Stage4HarnessValidationError("stage4_driver_executable_changed")
    observer_process = subprocess.Popen(observer, cwd=repo, env=observer_env)
    try:
        deadline = time.monotonic() + float(args.observer_ready_timeout)
        while time.monotonic() < deadline and observer_process.poll() is None:
            if ready.is_file():
                receipt = _json(ready)
                if receipt.get("run_id") == manifest["run_id"] and receipt.get("trace_sha256") == manifest["trace_sha256"]:
                    break
            time.sleep(0.1)
        else:
            raise Stage4HarnessValidationError("stage4_observer_not_ready")
        if sha256_file(Path(sender[0])) != driver_hashes["sender"]:
            raise Stage4HarnessValidationError("stage4_driver_executable_changed")
        sender_result = subprocess.run(sender, cwd=repo, env=sender_env, check=False)
        if sender_result.returncode != 0:
            raise Stage4HarnessValidationError("stage4_sender_failed")
        observer_returncode = observer_process.wait(timeout=float(args.observer_exit_timeout))
        if observer_returncode != 0:
            raise Stage4HarnessValidationError("stage4_observer_failed")
    finally:
        if observer_process.poll() is None:
            observer_process.terminate()
            try:
                observer_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                observer_process.kill()
                observer_process.wait(timeout=5)
    return verify_stage4_live_evidence(args.output_dir)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("plan", "verify-plan", "execute", "verify-live"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--trusted-authorization-public-key", type=Path)
    parser.add_argument("--authorize-live-staging", action="store_true")
    parser.add_argument("--observer-ready-timeout", type=float, default=30.0)
    parser.add_argument("--observer-exit-timeout", type=float, default=900.0)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.command == "plan":
            if args.config is None or args.fixture is None or args.seed is None:
                raise Stage4HarnessValidationError("stage4_plan_arguments_missing")
            result = write_stage4_plan(
                output_dir=args.output_dir,
                config=_json(args.config),
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
            result = verify_stage4_live_evidence(args.output_dir)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(canonical_json({"status": "blocked", "reason": str(exc)}))
        return 2
    print(canonical_json({"status": "ok", "result": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
