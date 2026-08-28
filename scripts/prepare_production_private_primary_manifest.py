#!/usr/bin/env python3
"""Build a CAS-bound production deploy manifest for PRIVATE_PRIMARY.

The source manifest is never modified.  Only the legacy Market-pipeline
rollout toggles and the legacy Snapshot relay controls are normalized.  All
other approved keys and their values are preserved byte-for-byte.  The tool
does not read the runtime secret source, render runtime envs, or mutate a live
service.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA_SOURCE = REPO_ROOT / "deploy/production/online.env.example"
APPROVED_ROOT = Path("/root/secure-envs/trading-bot/release-control")
CONFIRMATION = "prepare-production-private-primary-deploy-manifest"
RECEIPT_SCHEMA = "production_private_primary_deploy_manifest/1.0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
MAXIMUM_MANIFEST_BYTES = 256 * 1024
MAXIMUM_REPO_EVIDENCE_BYTES = 2 * 1024 * 1024
LOCK_FILE_NAME = ".prepare-private-primary-manifest.lock"

# These are either recognized explicitly by ``load_manifest`` or declared as
# reviewed, commented optional controls in the production manifest template.
# Keeping them explicit prevents ordinary prose such as ``KEY=value`` examples
# for runtime-only configuration from silently expanding the deploy schema.
OPTIONAL_KNOWN_KEYS = frozenset(
    {
        "IRAN_SSH_PASSWORD",
        "IRAN_HOSTS_SYNC_ENABLED",
        "PRODUCTION_COIN_INFERENCE_SOURCE_ROOT",
        "PRODUCTION_COIN_INFERENCE_SOURCE_STORE",
        "PRODUCTION_COIN_INFERENCE_ESTIMATOR_ROOT",
        "PRODUCTION_BACKUP_RECEIPT_PATH",
        "PRODUCTION_BACKUP_RECEIPT_SHA256",
        "PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_PATH",
        "PRODUCTION_MIGRATION_REHEARSAL_RECEIPT_SHA256",
        "SMSIR_ACCOUNTANT_INVITATION_TEMPLATE_ID",
        "SMSIR_CUSTOMER_INVITATION_TEMPLATE_ID",
        "SMSIR_INVITATION_TEMPLATE_ID",
        "SMSIR_INVITATION_TEMPLATE_PARAMETER",
        "SMSIR_OTP_TEMPLATE_ID",
        "SMSIR_OTP_TEMPLATE_PARAMETER",
        "GRAFANA_ADMIN_USER",
        "GRAFANA_ADMIN_PASSWORD",
        "GRAFANA_ALERT_DEFAULT_RECEIVER",
        "GRAFANA_ALERT_CRITICAL_RECEIVER",
        "GRAFANA_ALERT_WARNING_RECEIVER",
        "GRAFANA_ALERT_WEBHOOK_URL",
        "GRAFANA_ALERT_EMAIL_ADDRESSES",
        "GF_SMTP_ENABLED",
        "GF_SMTP_HOST",
        "GF_SMTP_USER",
        "GF_SMTP_PASSWORD",
        "GF_SMTP_FROM_ADDRESS",
        "GF_SMTP_FROM_NAME",
        "TRUSTED_PROXY_CIDRS",
        "OBSERVABILITY_TELEGRAM_USER_HASH_SALT",
        "AUDIT_ANCHOR_HOST_OUTPUT_PATH",
        "AUDIT_ANCHOR_RELAY_OUTPUT_PATH",
        "AUDIT_ANCHOR_RELEASE_ID",
        "AUDIT_ANCHOR_REMOTE_TARGET",
        "AUDIT_ANCHOR_SOURCE_NAME",
    }
)

PRIVATE_PRIMARY_MANIFEST_UPDATES: Mapping[str, str] = {
    # The PRIVATE_PRIMARY stack is prepared and promoted by the dedicated
    # blue/green tools.  The ordinary two-host product deploy must not rerun
    # any historical PRIVATE_SHADOW rollout phase.
    "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_MIGRATION_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED": "0",
    "PRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_CONFIRM": "",
    "PRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED": "0",
    # PRIVATE_PRIMARY is receiver-acknowledged directly; the legacy product
    # relay must be explicitly and confirmation-bound disabled.
    "PRODUCTION_COIN_INFERENCE_RELAY_ENABLED": "0",
    "PRODUCTION_COIN_INFERENCE_RELAY_CONFIRM": "",
    "PRODUCTION_COIN_INFERENCE_RELAY_DISABLE_CONFIRM": (
        "disable-production-coin-inference-snapshot"
    ),
}

REQUIRED_SAFE_VALUES: Mapping[str, str] = {
    "ALLOW_PROJECT_ENV_SOURCE": "0",
    "IRAN_ALLOW_DIRTY_RELEASE": "0",
    "IRAN_ALLOW_NON_MAIN_RELEASE": "0",
    "IRAN_ALLOW_RELEASE_BRANCH_DRIFT": "0",
    "IRAN_SKIP_FOREIGN_DEPLOY": "0",
    "PRODUCTION_RELEASE_BRANCH": "main",
    "FOREIGN_COMPOSE_PROJECT_NAME": "trading_bot",
}

REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "LOCAL_PROJECT_DIR",
        "LOCAL_FRONTEND_DIR",
        "LOCAL_DIST_DIR",
        "FOREIGN_PUBLIC_IP",
        "FOREIGN_PUBLIC_DOMAIN",
        "FOREIGN_COMPOSE_PROJECT_NAME",
        "IRAN_HOST",
        "IRAN_SSH_USER",
        "IRAN_SSH_PORT",
        "IRAN_PROJECT_DIR",
        "IRAN_DEPLOY_BASE_DIR",
        "IRAN_PUBLIC_IP",
        "IRAN_APP_DOMAIN",
        "IRAN_PUBLIC_DOMAIN",
        "IRAN_CERTBOT_EMAIL",
        "RUNTIME_ENV_SOURCE_PATH",
        "FOREIGN_RUNTIME_ENV_PATH",
        "IRAN_RUNTIME_ENV_PATH",
    }
)


class ManifestPreparationError(RuntimeError):
    """Stable, value-free refusal."""


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _read_stable_regular(path: Path, *, label: str) -> bytes:
    """Read repo/tool evidence once from a stable, non-symlink descriptor."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestPreparationError(f"{label}_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        path_info = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAXIMUM_REPO_EVIDENCE_BYTES
            or path_info.st_dev != before.st_dev
            or path_info.st_ino != before.st_ino
        ):
            raise ManifestPreparationError(f"{label}_security_invalid")
        payload = b""
        while len(payload) <= MAXIMUM_REPO_EVIDENCE_BYTES:
            chunk = os.read(
                descriptor,
                min(131072, MAXIMUM_REPO_EVIDENCE_BYTES + 1 - len(payload)),
            )
            if not chunk:
                break
            payload += chunk
        after = os.fstat(descriptor)
        final_path = path.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or final_path.st_dev != before.st_dev
            or final_path.st_ino != before.st_ino
            or len(payload) != before.st_size
        ):
            raise ManifestPreparationError(f"{label}_changed_during_read")
        return payload
    except OSError as exc:
        raise ManifestPreparationError(f"{label}_read_failed") from exc
    finally:
        os.close(descriptor)


def _manifest_schema_contract() -> tuple[frozenset[str], str]:
    try:
        template_payload = _read_stable_regular(
            MANIFEST_SCHEMA_SOURCE, label="manifest_schema"
        )
        template = template_payload.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestPreparationError("manifest_schema_unavailable") from exc
    keys: set[str] = set(OPTIONAL_KNOWN_KEYS)
    active_seen: set[str] = set()
    for raw in template.splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", raw)
        if match is None:
            continue
        key = match.group(1)
        if key in active_seen:
            raise ManifestPreparationError("manifest_schema_duplicate_key")
        active_seen.add(key)
        keys.add(key)
    if not set(PRIVATE_PRIMARY_MANIFEST_UPDATES).issubset(keys):
        raise ManifestPreparationError("manifest_schema_incomplete")
    return frozenset(keys), _digest(template_payload)


def _known_keys() -> frozenset[str]:
    keys, _schema_digest = _manifest_schema_contract()
    return keys


def _require_root() -> None:
    if os.geteuid() != 0:
        raise ManifestPreparationError("root_execution_required")


def _require_under_approved_root(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        raise ManifestPreparationError(f"{label}_path_invalid")
    try:
        resolved = path.resolve(strict=False)
        approved = APPROVED_ROOT.resolve(strict=True)
    except OSError as exc:
        raise ManifestPreparationError(f"{label}_path_invalid") from exc
    if resolved != path or not (path.parent == approved or approved in path.parents):
        raise ManifestPreparationError(f"{label}_scope_invalid")
    return path


def _require_secure_directory(path: Path, *, label: str) -> None:
    try:
        approved = APPROVED_ROOT.resolve(strict=True)
        current = path.resolve(strict=True)
    except OSError as exc:
        raise ManifestPreparationError(f"{label}_directory_invalid") from exc
    if current != path or not (current == approved or approved in current.parents):
        raise ManifestPreparationError(f"{label}_directory_invalid")
    chain = [approved]
    relative = current.relative_to(approved)
    for part in relative.parts:
        chain.append(chain[-1] / part)
    for directory in chain:
        try:
            info = directory.lstat()
        except OSError as exc:
            raise ManifestPreparationError(f"{label}_directory_invalid") from exc
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ManifestPreparationError(f"{label}_directory_invalid")


@contextmanager
def _exclusive_preparation_lock():
    """Serialize the derived manifest and receipt as one recovery unit."""

    _require_secure_directory(APPROVED_ROOT, label="preparation_lock")
    lock_path = APPROVED_ROOT / LOCK_FILE_NAME
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ManifestPreparationError("preparation_lock_unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ManifestPreparationError("preparation_lock_security_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ManifestPreparationError("preparation_lock_busy") from exc
        locked = os.fstat(descriptor)
        if (
            locked.st_dev,
            locked.st_ino,
            locked.st_uid,
            stat.S_IMODE(locked.st_mode),
            locked.st_nlink,
        ) != (
            info.st_dev,
            info.st_ino,
            0,
            0o600,
            1,
        ):
            raise ManifestPreparationError("preparation_lock_changed")
        yield
    except OSError as exc:
        raise ManifestPreparationError("preparation_lock_failed") from exc
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_secure_file(path: Path, *, label: str) -> bytes:
    path = _require_under_approved_root(path, label=label)
    _require_secure_directory(path.parent, label=label)
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ManifestPreparationError(f"{label}_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        try:
            path_info = path.lstat()
        except OSError as exc:
            raise ManifestPreparationError(
                f"{label}_changed_during_read"
            ) from exc
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > MAXIMUM_MANIFEST_BYTES
            or path_info.st_dev != before.st_dev
            or path_info.st_ino != before.st_ino
        ):
            raise ManifestPreparationError(f"{label}_security_invalid")
        chunks: list[bytes] = []
        remaining = before.st_size + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after = os.fstat(descriptor)
        try:
            final_path = path.lstat()
        except OSError as exc:
            raise ManifestPreparationError(
                f"{label}_changed_during_read"
            ) from exc
        if len(payload) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or (
            final_path.st_dev != before.st_dev
            or final_path.st_ino != before.st_ino
        ):
            raise ManifestPreparationError(f"{label}_changed_during_read")
        return payload
    except OSError as exc:
        raise ManifestPreparationError(f"{label}_read_failed") from exc
    finally:
        os.close(descriptor)


def _parse_and_render(
    payload: bytes, *, known_keys: frozenset[str] | None = None
) -> tuple[bytes, list[str]]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestPreparationError("source_manifest_encoding_invalid") from exc
    if "\x00" in text:
        raise ManifestPreparationError("source_manifest_encoding_invalid")
    if any(
        character != "\n"
        and (
            ord(character) < 0x20
            or ord(character) == 0x7F
            or character in {"\u0085", "\u2028", "\u2029"}
        )
        for character in text
    ):
        raise ManifestPreparationError("source_manifest_line_separator_invalid")
    known = known_keys if known_keys is not None else _known_keys()
    lines = text.splitlines(keepends=True)
    seen: dict[str, int] = {}
    changed: list[str] = []
    values: dict[str, str] = {}
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ManifestPreparationError("source_manifest_syntax_invalid")
        key, value = line.split("=", 1)
        if not ENV_KEY.fullmatch(key):
            raise ManifestPreparationError("source_manifest_key_invalid")
        if key not in known:
            raise ManifestPreparationError("source_manifest_unknown_key")
        if key in seen:
            raise ManifestPreparationError("source_manifest_duplicate_key")
        seen[key] = index
        values[key] = value
        if key in PRIVATE_PRIMARY_MANIFEST_UPDATES:
            if raw.endswith("\r\n"):
                newline = "\r\n"
            elif raw.endswith("\n"):
                newline = "\n"
            else:
                newline = ""
            replacement = f"{key}={PRIVATE_PRIMARY_MANIFEST_UPDATES[key]}{newline}"
            if replacement != raw:
                lines[index] = replacement
                changed.append(key)
    missing = [
        key for key in PRIVATE_PRIMARY_MANIFEST_UPDATES if key not in seen
    ]
    if missing:
        # Older approved release manifests predate the private-pipeline gates.
        # Add only this reviewed normalization set; every other absent key stays
        # absent and every non-target byte stays unchanged.
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        for key in missing:
            lines.append(f"{key}={PRIVATE_PRIMARY_MANIFEST_UPDATES[key]}\n")
            changed.append(key)
    for key, expected in REQUIRED_SAFE_VALUES.items():
        if values.get(key) != expected:
            raise ManifestPreparationError("source_manifest_release_safety_invalid")
    if any(not values.get(key) for key in REQUIRED_MANIFEST_KEYS):
        raise ManifestPreparationError("source_manifest_identity_incomplete")
    source_path = Path(values.get("RUNTIME_ENV_SOURCE_PATH", ""))
    if (
        not source_path.is_absolute()
        or REPO_ROOT == source_path
        or REPO_ROOT in source_path.parents
        or "staging" in str(source_path).lower()
        or "production" not in str(source_path).lower()
    ):
        raise ManifestPreparationError("source_manifest_runtime_source_invalid")
    runtime_outputs = [
        Path(values["FOREIGN_RUNTIME_ENV_PATH"]),
        Path(values["IRAN_RUNTIME_ENV_PATH"]),
    ]
    if (
        any(
            not path.is_absolute()
            or REPO_ROOT == path
            or REPO_ROOT in path.parents
            or "staging" in str(path).lower()
            or "production" not in str(path).lower()
            for path in runtime_outputs
        )
        or source_path in runtime_outputs
        or runtime_outputs[0] == runtime_outputs[1]
    ):
        raise ManifestPreparationError("source_manifest_runtime_output_invalid")
    rendered = "".join(lines).encode("utf-8")
    if len(rendered) > MAXIMUM_MANIFEST_BYTES:
        raise ManifestPreparationError("rendered_manifest_too_large")
    return rendered, sorted(changed)


def _preflight_atomic_target(
    path: Path, payload: bytes, *, label: str
) -> tuple[Path, str]:
    path = _require_under_approved_root(path, label=label)
    _require_secure_directory(path.parent, label=label)
    if path.exists() or path.is_symlink():
        observed = _read_secure_file(path, label=label)
        if observed != payload:
            raise ManifestPreparationError(f"{label}_exists_with_different_bytes")
        return path, "ALREADY_CURRENT"
    return path, "ABSENT"


def _write_atomic_or_verify(path: Path, payload: bytes, *, label: str) -> str:
    path, preflight_state = _preflight_atomic_target(path, payload, label=label)
    if preflight_state == "ALREADY_CURRENT":
        return preflight_state
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    state = "CREATED"
    try:
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        # ``replace`` could clobber a file created by a concurrent operator
        # after the existence check.  A same-filesystem hard-link publish is
        # atomic and no-clobber; removing the temporary name immediately leaves
        # the installed regular file with exactly one link.
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            # A concurrent invocation may have published first.  It is
            # idempotent only when that independently secured file is exactly
            # the same artifact; different bytes remain a hard refusal.
            observed = _read_secure_file(path, label=label)
            if observed != payload:
                raise ManifestPreparationError(
                    f"{label}_exists_with_different_bytes"
                )
            state = "ALREADY_CURRENT"
        else:
            temporary.unlink()
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_CLOEXEC"):
                directory_flags |= os.O_CLOEXEC
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            if hasattr(os, "O_NOFOLLOW"):
                directory_flags |= os.O_NOFOLLOW
            directory = os.open(path.parent, directory_flags)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as exc:
        raise ManifestPreparationError(f"{label}_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)
    installed = _read_secure_file(path, label=label)
    if installed != payload:
        raise ManifestPreparationError(f"{label}_postwrite_mismatch")
    return state


def _prepare_locked(
    *, source: Path, output: Path, receipt: Path, expected_source_sha256: str
) -> dict[str, object]:
    before = _read_secure_file(source, label="source_manifest")
    before_digest = _digest(before)
    if before_digest != expected_source_sha256:
        raise ManifestPreparationError("source_manifest_cas_mismatch")
    known_keys, schema_digest = _manifest_schema_contract()
    rendered, changed = _parse_and_render(before, known_keys=known_keys)
    # Re-read before either output is installed.  A changed source invalidates
    # the whole operation and leaves no derived manifest or receipt behind.
    if _digest(_read_secure_file(source, label="source_manifest")) != before_digest:
        raise ManifestPreparationError("source_manifest_changed_during_prepare")
    output_digest = _digest(rendered)
    result = {
        "schema": RECEIPT_SCHEMA,
        "status": "PASS",
        "action": "PREPARE_PRIVATE_PRIMARY_DEPLOY_MANIFEST",
        "source_sha256": before_digest,
        "output_sha256": output_digest,
        "source_path_sha256": _digest(str(source).encode("utf-8")),
        "output_path_sha256": _digest(str(output).encode("utf-8")),
        "receipt_path_sha256": _digest(str(receipt).encode("utf-8")),
        "manifest_schema_sha256": schema_digest,
        "tool_sha256": _digest(
            _read_stable_regular(Path(__file__), label="preparation_tool")
        ),
        "changed_keys": changed,
        "normalized_keys": sorted(PRIVATE_PRIMARY_MANIFEST_UPDATES),
        "source_preserved_by_tool": True,
        "secrets_disclosed": False,
    }
    receipt_payload = (
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    # Refuse a tampered/insecure receipt before creating even a deterministic
    # orphan output.  The global lock keeps this pair preflight stable against
    # other invocations of this tool; no-clobber publication handles outsiders.
    _preflight_atomic_target(output, rendered, label="output_manifest")
    _preflight_atomic_target(receipt, receipt_payload, label="receipt")
    output_state = _write_atomic_or_verify(
        output, rendered, label="output_manifest"
    )
    # The receipt is the final publish in the transaction.  If an external
    # actor changed the immutable source, keep the deterministic orphan output
    # unusable (there is no PASS receipt) and refuse to attest it.
    if _digest(_read_secure_file(source, label="source_manifest")) != before_digest:
        raise ManifestPreparationError("source_manifest_changed_after_output")
    try:
        receipt_state = _write_atomic_or_verify(
            receipt, receipt_payload, label="receipt"
        )
    except ManifestPreparationError:
        # A newly-created manifest without a matching receipt is not silently
        # removed or overwritten.  Its digest is deterministic and a retry
        # with a corrected, previously absent receipt completes idempotently.
        raise
    return {
        **result,
        "output_state": output_state,
        "receipt_state": receipt_state,
        "receipt_sha256": _digest(receipt_payload),
    }


def prepare(args: argparse.Namespace) -> dict[str, object]:
    _require_root()
    if args.confirm != CONFIRMATION:
        raise ManifestPreparationError("confirmation_invalid")
    if not HEX64.fullmatch(args.expected_source_sha256 or ""):
        raise ManifestPreparationError("expected_source_sha256_invalid")
    source = _require_under_approved_root(Path(args.source), label="source_manifest")
    output = _require_under_approved_root(Path(args.output), label="output_manifest")
    receipt = _require_under_approved_root(Path(args.receipt), label="receipt")
    if len({source, output, receipt}) != 3:
        raise ManifestPreparationError("manifest_output_alias")
    with _exclusive_preparation_lock():
        return _prepare_locked(
            source=source,
            output=output,
            receipt=receipt,
            expected_source_sha256=args.expected_source_sha256,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = prepare(args)
    except (OSError, ManifestPreparationError) as exc:
        reason = str(exc) if isinstance(exc, ManifestPreparationError) else "os_error"
        print(
            json.dumps(
                {
                    "schema": RECEIPT_SCHEMA,
                    "status": "FAILED",
                    "reason_code": reason,
                    "secrets_disclosed": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
