#!/usr/bin/env python3
"""Immutable Writer Witness credential-rotation state.

The paired live-attestation verifier keeps this state machine local and
root-controlled. A current policy is never edited in place:

* policies are immutable, versioned files;
* selector candidates are immutable files;
* activation records form an append-only, hash-linked ledger; and
* the small current-selector pointer is replaced atomically and must match the
  newest immutable activation record.

The mutable pointer is not the audit record. A simple rollback to a previous
pointer, a changed selector, or a changed policy fails closed while the
immutable ledger is intact. A user with unrestricted root access can delete
every local record and is outside this host-control trust boundary.

This module has no network, Object Storage, SSH, Docker, service-manager, or
secret handling. It stores only policy hashes, key-id hashes, public trust
hashes, and timestamps supplied by its caller.
"""

from __future__ import annotations

import contextlib
import dataclasses
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from typing import Any, Iterator, Mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import prepare_writer_witness_immutable_release as control  # noqa: E402


STATE_DIRECTORY_NAME = "writer-witness-credential-rotation-v1"
POLICIES_DIRECTORY_NAME = "policies"
SELECTORS_DIRECTORY_NAME = "selectors"
ACTIVATIONS_DIRECTORY_NAME = "activations"
CURRENT_SELECTOR_FILENAME = "current-selector.json"
LOCK_FILENAME = "rotation.lock"

POLICY_SCHEMA = "gold-trade-writer-witness-credential-rotation-policy-v2"
SELECTOR_SCHEMA = "gold-trade-writer-witness-credential-rotation-selector-v1"
ACTIVATION_SCHEMA = "gold-trade-writer-witness-credential-rotation-activation-v1"
CURRENT_SELECTOR_SCHEMA = "gold-trade-writer-witness-credential-rotation-current-v1"

MAXIMUM_STATE_FILE_BYTES = 128 * 1024
MAXIMUM_ACTIVATIONS = 4096
POLICY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SELECTOR_FILENAME_RE = re.compile(r"^selector-([0-9]{20})-([0-9a-f]{16})[.]json$")
ACTIVATION_FILENAME_RE = re.compile(r"^activation-([0-9]{20})-([0-9a-f]{16})[.]json$")
NL = bytes((10,))

DEFAULT_STATE_DIRECTORY = Path("/etc/trading-bot-three-site") / STATE_DIRECTORY_NAME


class WriterWitnessRotationLifecycleError(RuntimeError):
    """The local immutable credential-rotation state is not safe."""


@dataclasses.dataclass(frozen=True)
class RotationStatePaths:
    root: Path
    policies: Path
    selectors: Path
    activations: Path
    current_selector: Path
    lock: Path


@dataclasses.dataclass(frozen=True)
class CurrentPolicySnapshot:
    policy_path: Path
    policy_raw: bytes
    policy_id: str
    policy_sha256: str
    selector_filename: str
    selector_sha256: str
    activation_filename: str
    activation_sha256: str
    sequence: int


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WriterWitnessRotationLifecycleError(
                "credential rotation JSON contains duplicate keys"
            )
        result[key] = value
    return result


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise WriterWitnessRotationLifecycleError(f"{field} is invalid")
    return value


def _require_policy_id(value: object) -> str:
    if not isinstance(value, str) or not POLICY_ID_RE.fullmatch(value):
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness credential rotation policy id is invalid"
        )
    return value


def _require_sequence(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAXIMUM_ACTIVATIONS:
        raise WriterWitnessRotationLifecycleError(f"{field} is invalid")
    return value


def _require_timestamp(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) < 20 or len(value) > 64:
        raise WriterWitnessRotationLifecycleError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WriterWitnessRotationLifecycleError(f"{field} is invalid") from exc
    if parsed.tzinfo is None:
        raise WriterWitnessRotationLifecycleError(f"{field} lacks a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def policy_filename(policy_id: str) -> str:
    return f"policy-{_require_policy_id(policy_id)}.json"


def selector_filename(*, sequence: int, selector_sha256: str) -> str:
    _require_sequence(sequence, field="credential rotation selector sequence")
    digest = _require_sha256(selector_sha256, field="credential rotation selector hash")
    return f"selector-{sequence:020d}-{digest[:16]}.json"


def activation_filename(*, sequence: int, activation_sha256: str) -> str:
    _require_sequence(sequence, field="credential rotation activation sequence")
    digest = _require_sha256(activation_sha256, field="credential rotation activation hash")
    return f"activation-{sequence:020d}-{digest[:16]}.json"


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot fsync credential rotation state directory"
        ) from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot fsync credential rotation state directory"
        ) from exc
    finally:
        os.close(descriptor)


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Reject a writable or indirect parent before any privileged lookup.

    A root-owned sticky directory is allowed as an ancestor. This permits
    root-owned private test/workspace children below the temporary directory
    without allowing an unprivileged account to replace that root-owned child.
    """

    if not path.is_absolute():
        raise WriterWitnessRotationLifecycleError(f"{field} must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        if component in {"", ".", ".."}:
            raise WriterWitnessRotationLifecycleError(f"{field} is not canonical")
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise WriterWitnessRotationLifecycleError(
                f"cannot inspect {field} parent chain"
            ) from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (
                mode & 0o022
                and not (
                    metadata.st_uid == 0
                    and bool(metadata.st_mode & stat.S_ISVTX)
                )
            )
        ):
            raise WriterWitnessRotationLifecycleError(
                f"{field} parent chain is not root-controlled"
            )


def _require_private_directory(path: Path, *, field: str) -> Path:
    _require_root_controlled_directory_chain(path, field=field)
    try:
        return control._require_root_owned_directory(path, field=field, private=True)
    except control.WitnessReleasePreparationError as exc:
        raise WriterWitnessRotationLifecycleError(f"{field} is unsafe") from exc


def _ensure_private_child(parent: Path, name: str, *, field: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise WriterWitnessRotationLifecycleError(f"{field} name is invalid")
    parent = _require_private_directory(parent, field=f"{field} parent")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(f"cannot create {field}") from exc
    result = _require_private_directory(child, field=field)
    _fsync_directory(parent)
    return result


def _state_paths(state_directory: Path | None, *, create: bool) -> RotationStatePaths:
    root = Path(state_directory or DEFAULT_STATE_DIRECTORY)
    if not root.is_absolute() or root.name != STATE_DIRECTORY_NAME:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness credential rotation state directory is invalid"
        )
    if create:
        root = _ensure_private_child(
            root.parent,
            root.name,
            field="Writer Witness credential rotation state directory",
        )
        policies = _ensure_private_child(
            root,
            POLICIES_DIRECTORY_NAME,
            field="Writer Witness credential rotation policy directory",
        )
        selectors = _ensure_private_child(
            root,
            SELECTORS_DIRECTORY_NAME,
            field="Writer Witness credential rotation selector directory",
        )
        activations = _ensure_private_child(
            root,
            ACTIVATIONS_DIRECTORY_NAME,
            field="Writer Witness credential rotation activation directory",
        )
    else:
        root = _require_private_directory(
            root,
            field="Writer Witness credential rotation state directory",
        )
        policies = _require_private_directory(
            root / POLICIES_DIRECTORY_NAME,
            field="Writer Witness credential rotation policy directory",
        )
        selectors = _require_private_directory(
            root / SELECTORS_DIRECTORY_NAME,
            field="Writer Witness credential rotation selector directory",
        )
        activations = _require_private_directory(
            root / ACTIVATIONS_DIRECTORY_NAME,
            field="Writer Witness credential rotation activation directory",
        )
    return RotationStatePaths(
        root=root,
        policies=policies,
        selectors=selectors,
        activations=activations,
        current_selector=root / CURRENT_SELECTOR_FILENAME,
        lock=root / LOCK_FILENAME,
    )


@contextlib.contextmanager
def _state_lock(paths: RotationStatePaths, *, exclusive: bool) -> Iterator[None]:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(paths.lock, flags, 0o600)
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot open Writer Witness credential rotation lock"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness credential rotation lock is unsafe"
            )
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot lock Writer Witness credential rotation state"
        ) from exc
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _write_immutable(path: Path, payload: bytes, *, field: str) -> None:
    if not payload or len(payload) > MAXIMUM_STATE_FILE_BYTES:
        raise WriterWitnessRotationLifecycleError(f"{field} has an unsafe size")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(f"cannot create {field}") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise WriterWitnessRotationLifecycleError(f"cannot write {field}")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(f"cannot write {field}") from exc
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _atomic_replace_current(path: Path, payload: bytes) -> None:
    if not payload or len(payload) > MAXIMUM_STATE_FILE_BYTES:
        raise WriterWitnessRotationLifecycleError("current selector has an unsafe size")
    temporary = path.parent / f".{path.name}.pending-{secrets.token_hex(16)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot create pending Writer Witness current selector"
        ) from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise WriterWitnessRotationLifecycleError(
                    "cannot write pending Writer Witness current selector"
                )
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot write pending Writer Witness current selector"
        ) from exc
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot atomically advance Writer Witness current selector"
        ) from exc
    _fsync_directory(path.parent)


def _read_file(path: Path, *, field: str) -> bytes:
    try:
        payload = control._read_controlled_file(path, field=field, root_only=True)
    except control.WitnessReleasePreparationError as exc:
        raise WriterWitnessRotationLifecycleError(f"{field} is invalid") from exc
    if len(payload) > MAXIMUM_STATE_FILE_BYTES:
        raise WriterWitnessRotationLifecycleError(f"{field} has an unsafe size")
    return payload


def _parse_canonical_json(raw: bytes, *, field: str) -> dict[str, Any]:
    if not raw or len(raw) > MAXIMUM_STATE_FILE_BYTES or not raw.endswith(NL):
        raise WriterWitnessRotationLifecycleError(f"{field} is not canonical")
    try:
        value = json.loads(raw[:-1].decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WriterWitnessRotationLifecycleError(f"{field} is invalid") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) + NL != raw:
        raise WriterWitnessRotationLifecycleError(f"{field} is not canonical")
    return value


def _read_optional_current(paths: RotationStatePaths) -> tuple[bytes, dict[str, Any]] | None:
    try:
        paths.current_selector.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot inspect Writer Witness current selector"
        ) from exc
    raw = _read_file(paths.current_selector, field="Writer Witness current selector")
    return raw, _parse_current(raw)


def _parse_selector(raw: bytes) -> dict[str, Any]:
    payload = _parse_canonical_json(raw, field="Writer Witness immutable selector")
    if set(payload) != {
        "schema",
        "sequence",
        "policy_id",
        "policy_filename",
        "policy_sha256",
        "previous_activation_sha256",
        "profile_sha256",
        "created_at",
    } or payload.get("schema") != SELECTOR_SCHEMA:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness immutable selector schema is invalid"
        )
    sequence = _require_sequence(payload.get("sequence"), field="credential rotation selector sequence")
    policy_id = _require_policy_id(payload.get("policy_id"))
    if payload.get("policy_filename") != policy_filename(policy_id):
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness immutable selector policy filename is invalid"
        )
    _require_sha256(payload.get("policy_sha256"), field="credential rotation selector policy hash")
    previous = payload.get("previous_activation_sha256")
    if sequence == 1:
        if previous is not None:
            raise WriterWitnessRotationLifecycleError(
                "initial Writer Witness selector has a previous activation"
            )
    else:
        _require_sha256(
            previous,
            field="Writer Witness selector previous activation hash",
        )
    _require_sha256(payload.get("profile_sha256"), field="Writer Witness selector profile hash")
    _require_timestamp(payload.get("created_at"), field="Writer Witness selector creation time")
    return payload


def _parse_activation(raw: bytes) -> dict[str, Any]:
    payload = _parse_canonical_json(raw, field="Writer Witness immutable activation")
    if set(payload) != {
        "schema",
        "sequence",
        "selector_filename",
        "selector_sha256",
        "policy_id",
        "policy_filename",
        "policy_sha256",
        "previous_activation_sha256",
        "profile_sha256",
        "activated_at",
    } or payload.get("schema") != ACTIVATION_SCHEMA:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness immutable activation schema is invalid"
        )
    sequence = _require_sequence(payload.get("sequence"), field="credential rotation activation sequence")
    selector_digest = _require_sha256(
        payload.get("selector_sha256"),
        field="Writer Witness activation selector hash",
    )
    if payload.get("selector_filename") != selector_filename(
        sequence=sequence,
        selector_sha256=selector_digest,
    ):
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness activation selector filename does not bind its hash"
        )
    policy_id = _require_policy_id(payload.get("policy_id"))
    if payload.get("policy_filename") != policy_filename(policy_id):
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness activation policy filename is invalid"
        )
    _require_sha256(payload.get("policy_sha256"), field="Writer Witness activation policy hash")
    previous = payload.get("previous_activation_sha256")
    if sequence == 1:
        if previous is not None:
            raise WriterWitnessRotationLifecycleError(
                "initial Writer Witness activation has a previous activation"
            )
    else:
        _require_sha256(
            previous,
            field="Writer Witness activation previous activation hash",
        )
    _require_sha256(payload.get("profile_sha256"), field="Writer Witness activation profile hash")
    _require_timestamp(payload.get("activated_at"), field="Writer Witness activation time")
    return payload


def _parse_current(raw: bytes) -> dict[str, Any]:
    payload = _parse_canonical_json(raw, field="Writer Witness current selector")
    if set(payload) != {
        "schema",
        "status",
        "sequence",
        "activation_filename",
        "activation_sha256",
        "selector_filename",
        "selector_sha256",
        "policy_id",
        "policy_filename",
        "policy_sha256",
        "profile_sha256",
        "activated_at",
    } or payload.get("schema") != CURRENT_SELECTOR_SCHEMA or payload.get("status") != "current":
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness current selector schema is invalid"
        )
    sequence = _require_sequence(payload.get("sequence"), field="current selector sequence")
    activation_digest = _require_sha256(
        payload.get("activation_sha256"),
        field="current selector activation hash",
    )
    if payload.get("activation_filename") != activation_filename(
        sequence=sequence,
        activation_sha256=activation_digest,
    ):
        raise WriterWitnessRotationLifecycleError(
            "current selector activation filename does not bind its hash"
        )
    selector_digest = _require_sha256(
        payload.get("selector_sha256"),
        field="current selector selector hash",
    )
    if payload.get("selector_filename") != selector_filename(
        sequence=sequence,
        selector_sha256=selector_digest,
    ):
        raise WriterWitnessRotationLifecycleError(
            "current selector selector filename does not bind its hash"
        )
    policy_id = _require_policy_id(payload.get("policy_id"))
    if payload.get("policy_filename") != policy_filename(policy_id):
        raise WriterWitnessRotationLifecycleError(
            "current selector policy filename is invalid"
        )
    _require_sha256(payload.get("policy_sha256"), field="current selector policy hash")
    _require_sha256(payload.get("profile_sha256"), field="current selector profile hash")
    _require_timestamp(payload.get("activated_at"), field="current selector activation time")
    return payload


def _read_immutable(
    directory: Path,
    filename: str,
    *,
    field: str,
    pattern: re.Pattern[str] | None = None,
) -> bytes:
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        raise WriterWitnessRotationLifecycleError(f"{field} filename is invalid")
    if pattern is not None and not pattern.fullmatch(filename):
        raise WriterWitnessRotationLifecycleError(f"{field} filename is invalid")
    return _read_file(directory / filename, field=field)


def _activation_records(
    paths: RotationStatePaths,
    *,
    profile_sha256: str,
) -> list[tuple[str, bytes, dict[str, Any]]]:
    try:
        entries = list(paths.activations.iterdir())
    except OSError as exc:
        raise WriterWitnessRotationLifecycleError(
            "cannot inspect Writer Witness activation ledger"
        ) from exc
    if len(entries) > MAXIMUM_ACTIVATIONS:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness activation ledger has too many records"
        )
    result: list[tuple[str, bytes, dict[str, Any]]] = []
    for entry in entries:
        name = entry.name
        if not ACTIVATION_FILENAME_RE.fullmatch(name):
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness activation ledger contains an unexpected artifact"
            )
        raw = _read_immutable(
            paths.activations,
            name,
            field="Writer Witness immutable activation",
            pattern=ACTIVATION_FILENAME_RE,
        )
        payload = _parse_activation(raw)
        digest = sha256_bytes(raw)
        if name != activation_filename(
            sequence=payload["sequence"],
            activation_sha256=digest,
        ):
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness activation filename does not bind its content"
            )
        if payload["profile_sha256"] != profile_sha256:
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness activation does not bind the trusted control profile"
            )
        result.append((name, raw, payload))
    result.sort(key=lambda item: item[2]["sequence"])
    previous_hash: str | None = None
    for expected_sequence, (_name, raw, payload) in enumerate(result, start=1):
        if payload["sequence"] != expected_sequence:
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness activation ledger sequence is discontinuous"
            )
        if payload["previous_activation_sha256"] != previous_hash:
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness activation ledger hash chain is invalid"
            )
        previous_hash = sha256_bytes(raw)
    return result


def _build_activation(
    selector: Mapping[str, Any],
    *,
    selector_sha256: str,
) -> tuple[str, bytes, dict[str, Any]]:
    activation: dict[str, Any] = {
        "schema": ACTIVATION_SCHEMA,
        "sequence": selector["sequence"],
        "selector_filename": selector_filename(
            sequence=selector["sequence"],
            selector_sha256=selector_sha256,
        ),
        "selector_sha256": selector_sha256,
        "policy_id": selector["policy_id"],
        "policy_filename": selector["policy_filename"],
        "policy_sha256": selector["policy_sha256"],
        "previous_activation_sha256": selector["previous_activation_sha256"],
        "profile_sha256": selector["profile_sha256"],
        "activated_at": selector["created_at"],
    }
    raw = canonical_json_bytes(activation) + NL
    digest = sha256_bytes(raw)
    return (
        activation_filename(sequence=selector["sequence"], activation_sha256=digest),
        raw,
        activation,
    )


def _build_current(
    *,
    selector: Mapping[str, Any],
    selector_sha256: str,
    activation_filename_value: str,
    activation_sha256: str,
) -> tuple[bytes, dict[str, Any]]:
    value: dict[str, Any] = {
        "schema": CURRENT_SELECTOR_SCHEMA,
        "status": "current",
        "sequence": selector["sequence"],
        "activation_filename": activation_filename_value,
        "activation_sha256": activation_sha256,
        "selector_filename": selector_filename(
            sequence=selector["sequence"],
            selector_sha256=selector_sha256,
        ),
        "selector_sha256": selector_sha256,
        "policy_id": selector["policy_id"],
        "policy_filename": selector["policy_filename"],
        "policy_sha256": selector["policy_sha256"],
        "profile_sha256": selector["profile_sha256"],
        "activated_at": selector["created_at"],
    }
    return canonical_json_bytes(value) + NL, value


def _load_selector_for_activation(
    paths: RotationStatePaths,
    activation: Mapping[str, Any],
    *,
    profile_sha256: str,
) -> tuple[bytes, dict[str, Any]]:
    raw = _read_immutable(
        paths.selectors,
        activation["selector_filename"],
        field="Writer Witness immutable selector",
        pattern=SELECTOR_FILENAME_RE,
    )
    if sha256_bytes(raw) != activation["selector_sha256"]:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness immutable selector changed after activation"
        )
    selector = _parse_selector(raw)
    if selector["profile_sha256"] != profile_sha256 or selector != {
        "schema": SELECTOR_SCHEMA,
        "sequence": activation["sequence"],
        "policy_id": activation["policy_id"],
        "policy_filename": activation["policy_filename"],
        "policy_sha256": activation["policy_sha256"],
        "previous_activation_sha256": activation["previous_activation_sha256"],
        "profile_sha256": profile_sha256,
        "created_at": activation["activated_at"],
    }:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness activation does not bind its exact immutable selector"
        )
    return raw, selector


def _current_matches_activation(
    current: Mapping[str, Any],
    activation: Mapping[str, Any],
    *,
    activation_sha256: str,
) -> bool:
    return current == {
        "schema": CURRENT_SELECTOR_SCHEMA,
        "status": "current",
        "sequence": activation["sequence"],
        "activation_filename": activation_filename(
            sequence=activation["sequence"],
            activation_sha256=activation_sha256,
        ),
        "activation_sha256": activation_sha256,
        "selector_filename": activation["selector_filename"],
        "selector_sha256": activation["selector_sha256"],
        "policy_id": activation["policy_id"],
        "policy_filename": activation["policy_filename"],
        "policy_sha256": activation["policy_sha256"],
        "profile_sha256": activation["profile_sha256"],
        "activated_at": activation["activated_at"],
    }


def _recover_pending_current(
    paths: RotationStatePaths,
    *,
    profile_sha256: str,
    allow_recover: bool,
) -> list[tuple[str, bytes, dict[str, Any]]]:
    """Finalize only the deterministic pointer-to-ledger crash window."""

    activations = _activation_records(paths, profile_sha256=profile_sha256)
    current_entry = _read_optional_current(paths)
    if current_entry is None:
        if activations:
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness committed activation ledger has no current selector"
            )
        return activations
    _current_raw, current = current_entry
    if current["profile_sha256"] != profile_sha256:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness current selector does not bind the trusted control profile"
        )
    if activations:
        _latest_name, latest_raw, latest = activations[-1]
        latest_sha = sha256_bytes(latest_raw)
        if _current_matches_activation(current, latest, activation_sha256=latest_sha):
            return activations
        expected_sequence = latest["sequence"] + 1
        expected_previous = latest_sha
    else:
        expected_sequence = 1
        expected_previous = None
    if current["sequence"] != expected_sequence:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness current selector does not match the committed lifecycle head"
        )
    selector_raw = _read_immutable(
        paths.selectors,
        current["selector_filename"],
        field="Writer Witness pending immutable selector",
        pattern=SELECTOR_FILENAME_RE,
    )
    selector_sha = sha256_bytes(selector_raw)
    if selector_sha != current["selector_sha256"]:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness pending current selector hash is invalid"
        )
    selector = _parse_selector(selector_raw)
    if (
        selector["sequence"] != expected_sequence
        or selector["previous_activation_sha256"] != expected_previous
        or selector["profile_sha256"] != profile_sha256
    ):
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness pending selector does not extend the committed lifecycle"
        )
    expected_name, expected_raw, activation = _build_activation(
        selector,
        selector_sha256=selector_sha,
    )
    expected_sha = sha256_bytes(expected_raw)
    if (
        current["activation_filename"] != expected_name
        or current["activation_sha256"] != expected_sha
        or not _current_matches_activation(
            current,
            activation,
            activation_sha256=expected_sha,
        )
    ):
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness pending current selector is not atomically attested"
        )
    if not allow_recover:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness current selector has an uncommitted activation"
        )
    _write_immutable(
        paths.activations / expected_name,
        expected_raw,
        field="Writer Witness recovered immutable activation",
    )
    return _activation_records(paths, profile_sha256=profile_sha256)


def _resolve_current_locked(
    paths: RotationStatePaths,
    *,
    profile_sha256: str,
) -> CurrentPolicySnapshot:
    activations = _recover_pending_current(
        paths,
        profile_sha256=profile_sha256,
        allow_recover=False,
    )
    if not activations:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness credential rotation has no committed current policy"
        )
    latest_name, latest_raw, latest = activations[-1]
    latest_sha = sha256_bytes(latest_raw)
    current_entry = _read_optional_current(paths)
    if current_entry is None:
        raise WriterWitnessRotationLifecycleError("Writer Witness current selector is missing")
    _current_raw, current = current_entry
    if not _current_matches_activation(current, latest, activation_sha256=latest_sha):
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness current selector does not match the committed lifecycle head"
        )
    selector_raw, _selector = _load_selector_for_activation(
        paths,
        latest,
        profile_sha256=profile_sha256,
    )
    selector_sha = sha256_bytes(selector_raw)
    policy_raw = _read_immutable(
        paths.policies,
        latest["policy_filename"],
        field="Writer Witness immutable credential rotation policy",
    )
    policy_sha = sha256_bytes(policy_raw)
    if policy_sha != latest["policy_sha256"]:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness immutable credential rotation policy changed after activation"
        )
    return CurrentPolicySnapshot(
        policy_path=paths.policies / latest["policy_filename"],
        policy_raw=policy_raw,
        policy_id=latest["policy_id"],
        policy_sha256=policy_sha,
        selector_filename=latest["selector_filename"],
        selector_sha256=selector_sha,
        activation_filename=latest_name,
        activation_sha256=latest_sha,
        sequence=latest["sequence"],
    )


def resolve_current_policy(
    *,
    profile_sha256: str,
    state_directory: Path | None = None,
) -> CurrentPolicySnapshot:
    """Return only the exact policy selected by the committed immutable head."""

    profile_sha256 = _require_sha256(
        profile_sha256,
        field="Writer Witness credential rotation profile hash",
    )
    paths = _state_paths(state_directory, create=False)
    with _state_lock(paths, exclusive=False):
        return _resolve_current_locked(paths, profile_sha256=profile_sha256)


def install_policy_and_activate(
    *,
    policy_id: str,
    policy_raw: bytes,
    policy_profile_sha256: str,
    issued_at: str,
    state_directory: Path | None = None,
) -> CurrentPolicySnapshot:
    """Create a versioned policy, atomically select it, and append its ledger."""

    policy_id = _require_policy_id(policy_id)
    profile_sha256 = _require_sha256(
        policy_profile_sha256,
        field="Writer Witness credential rotation policy profile hash",
    )
    issued_at = _require_timestamp(
        issued_at,
        field="Writer Witness credential rotation policy issue time",
    )
    if not policy_raw or len(policy_raw) > MAXIMUM_STATE_FILE_BYTES:
        raise WriterWitnessRotationLifecycleError(
            "Writer Witness credential rotation policy has an unsafe size"
        )
    paths = _state_paths(state_directory, create=True)
    with _state_lock(paths, exclusive=True):
        existing = _recover_pending_current(
            paths,
            profile_sha256=profile_sha256,
            allow_recover=True,
        )
        if existing:
            _resolve_current_locked(paths, profile_sha256=profile_sha256)
            previous_sha = sha256_bytes(existing[-1][1])
            sequence = existing[-1][2]["sequence"] + 1
        else:
            previous_sha = None
            sequence = 1
        policy_name = policy_filename(policy_id)
        _write_immutable(
            paths.policies / policy_name,
            policy_raw,
            field="Writer Witness immutable credential rotation policy",
        )
        observed_policy = _read_immutable(
            paths.policies,
            policy_name,
            field="Writer Witness immutable credential rotation policy",
        )
        if observed_policy != policy_raw:
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness immutable credential rotation policy changed after creation"
            )
        policy_sha = sha256_bytes(observed_policy)
        selector: dict[str, Any] = {
            "schema": SELECTOR_SCHEMA,
            "sequence": sequence,
            "policy_id": policy_id,
            "policy_filename": policy_name,
            "policy_sha256": policy_sha,
            "previous_activation_sha256": previous_sha,
            "profile_sha256": profile_sha256,
            "created_at": issued_at,
        }
        selector_raw = canonical_json_bytes(selector) + NL
        selector_sha = sha256_bytes(selector_raw)
        selector_name = selector_filename(
            sequence=sequence,
            selector_sha256=selector_sha,
        )
        _write_immutable(
            paths.selectors / selector_name,
            selector_raw,
            field="Writer Witness immutable selector",
        )
        observed_selector = _read_immutable(
            paths.selectors,
            selector_name,
            field="Writer Witness immutable selector",
            pattern=SELECTOR_FILENAME_RE,
        )
        if observed_selector != selector_raw:
            raise WriterWitnessRotationLifecycleError(
                "Writer Witness immutable selector changed after creation"
            )
        activation_name, activation_raw, _activation = _build_activation(
            selector,
            selector_sha256=selector_sha,
        )
        activation_sha = sha256_bytes(activation_raw)
        current_raw, _current = _build_current(
            selector=selector,
            selector_sha256=selector_sha,
            activation_filename_value=activation_name,
            activation_sha256=activation_sha,
        )
        _atomic_replace_current(paths.current_selector, current_raw)
        _write_immutable(
            paths.activations / activation_name,
            activation_raw,
            field="Writer Witness immutable activation",
        )
        return _resolve_current_locked(paths, profile_sha256=profile_sha256)
