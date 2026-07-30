#!/usr/bin/env python3
"""FI-only exchange for the immutable WebApp-FI source phase.

This helper is intentionally unable to read Object Storage credentials or to
use a controller transport client.  WebApp-FI receives only short-lived,
version-bound presigned URLs as one-shot control arguments.  It can:

* encrypt one locally prepared static archive, raw application image, or
  source-evidence file for the exact recipient pins in the transport contract
  and publish it with a create-only direct PUT; and
* receive one controller-published static-provenance object by exact
  VersionId, decrypt it with WebApp-FI's local age identity, and write a
  URL-free receipt.

It never invokes Docker, changes ``current``, starts or stops a service,
captures application data, loads an image, or creates an S3 client.  The
only network operation is ``curl`` against a contract-validated Arvan
presigned URL.  A failed upload deliberately leaves an immutable local
attempt marker: this helper never retries an ambiguous PUT automatically.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require the complete code lookup path to be root-controlled.

    This helper can run as root on WebApp-FI, so loading the pure contract is
    still an executable trust boundary.  A root-owned sticky ancestor is
    acceptable for an already-root-owned child; other writable ancestors are
    not.
    """

    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    components = (current,)
    for component in path.parts[1:]:
        current = current / component
        components += (current,)
    for current in components:
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - deployment invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        writable_by_group_or_other = bool(mode & 0o022)
        root_owned_sticky_directory = bool(metadata.st_mode & stat.S_ISVTX)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (writable_by_group_or_other and not root_owned_sticky_directory)
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Return one exact root-owned, non-writable code file without symlinks."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    _require_root_controlled_directory_chain(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        opened = resolved.lstat()
    except OSError as exc:  # pragma: no cover - deployment invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(opened.st_mode)
        or not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != 0
        or opened.st_nlink != 1
        or stat.S_IMODE(opened.st_mode) & 0o022
        or opened.st_mode & unsafe_bits
    ):
        raise RuntimeError(f"{field} is not a root-owned non-writable regular non-symlink file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load one root-controlled sibling without consulting ``sys.path``."""

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise RuntimeError("required sibling filename is not a safe leaf name")
    source = _require_root_controlled_code_file(
        Path(__file__),
        field="WebApp-FI source exchange source",
    )
    path = _require_root_controlled_code_file(
        source.with_name(filename),
        field=f"required sibling {filename}",
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded_path = getattr(module, "__file__", None)
        if not isinstance(loaded_path, str) or Path(loaded_path).resolve(strict=True) != path:
            raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


def _load_contract() -> Any:
    """Load only the exact root-controlled pure contract sibling."""

    return _load_exact_sibling(
        "webapp_fi_source_transport_contract.py",
        "_webapp_fi_source_transport_contract",
    )


def _load_static_provenance_packet_contract() -> Any:
    """Load the URL-free controller packet policy projection by exact path."""

    return _load_exact_sibling(
        "webapp_fi_static_provenance_control_packet.py",
        "_webapp_fi_static_provenance_control_packet_exchange",
    )


contract = _load_contract()
packet_control = _load_static_provenance_packet_contract()


EXCHANGE_CONFIG_SCHEMA = contract.CONFIG_SCHEMA
STATIC_PROVENANCE_POLICY_SCHEMA = packet_control.SOURCE_TRANSPORT_POLICY_SCHEMA
PREPARED_UPLOAD_SCHEMA = "gold-trade-webapp-fi-source-exchange-prepared-upload-v1"
UPLOAD_ATTEMPT_SCHEMA = "gold-trade-webapp-fi-source-exchange-upload-attempt-v1"
UPLOAD_REPORT_SCHEMA = "gold-trade-webapp-fi-source-exchange-upload-report-v1"
RECEIVE_RECEIPT_SCHEMA = "gold-trade-webapp-fi-source-exchange-receive-receipt-v1"

PREPARED_RECEIPT_NAME = "prepared-upload.json"
PREPARED_CIPHERTEXT_NAME = "payload.age"
UPLOAD_ATTEMPT_NAME = "upload-attempt.json"
UPLOAD_REPORT_NAME = "upload-report.json"
RECEIVED_PROVENANCE_NAME = "static-provenance.json"
RECEIVE_RECEIPT_NAME = "receive-receipt.json"

CURL_BINARY = "/usr/bin/curl"
MAX_JSON_BYTES = 1024 * 1024
MAX_AGE_IDENTITY_BYTES = 256 * 1024
MAX_HTTP_HEADERS_BYTES = 128 * 1024
CHUNK_BYTES = 1024 * 1024
SAFE_CHILD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SourceExchangeError(RuntimeError):
    """A WebApp-FI-only source exchange operation could not be proven safe."""


CommandRunner = Callable[[Sequence[str]], None]


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    """Encode strict ASCII JSON used for all local receipts and reports."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceExchangeError("JSON input contains duplicate keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise SourceExchangeError(f"JSON input contains unsupported constant: {value}")


def _parse_canonical_json(payload: bytes, *, field: str, reject_url: bool = False) -> dict[str, Any]:
    if not isinstance(payload, bytes) or not payload or len(payload) > MAX_JSON_BYTES:
        raise SourceExchangeError(f"{field} has an unsafe size")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceExchangeError(f"{field} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise SourceExchangeError(f"{field} is not canonical JSON")
    if reject_url:
        lowered = payload.lower()
        if b"://" in lowered or b'"url"' in lowered:
            raise SourceExchangeError(f"{field} persists a forbidden transient URL")
    return value


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise SourceExchangeError("WebApp-FI source exchange operations must run as root")


def _require_absolute(path: Path, *, field: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise SourceExchangeError(f"{field} must be an absolute canonical path")
    return path


def _require_safe_ancestors(path: Path, *, field: str) -> None:
    """Reject symlinked or attacker-replaceable ancestors.

    A root-owned sticky ancestor such as ``/tmp`` is acceptable for an
    isolated root-owned test workspace: another user cannot remove or rename
    the root-owned child below it.  The workspace itself is always stricter
    (root-only mode 0700).
    """

    path = _require_absolute(path, field=field)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            state = current.lstat()
        except OSError as exc:
            raise SourceExchangeError(f"{field} ancestor does not exist") from exc
        mode = stat.S_IMODE(state.st_mode)
        writable = mode & 0o022
        if (
            stat.S_ISLNK(state.st_mode)
            or not stat.S_ISDIR(state.st_mode)
            or state.st_uid != 0
            or (writable and not (state.st_mode & stat.S_ISVTX))
        ):
            raise SourceExchangeError(f"{field} has an unsafe ancestor")


def _require_root_private_directory(path: Path, *, field: str) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise SourceExchangeError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & 0o077
    ):
        raise SourceExchangeError(f"{field} must be one root-only non-symlink directory")
    return resolved


def _require_root_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise SourceExchangeError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & 0o077
        or target.st_nlink != 1
        or not 1 <= target.st_size <= maximum_bytes
    ):
        raise SourceExchangeError(f"{field} must be one bounded root-only regular file")
    return resolved


def _read_private_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    path = _require_root_private_file(path, field=field, maximum_bytes=maximum_bytes)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceExchangeError(f"cannot open {field}") from exc
    try:
        opened = os.fstat(descriptor)
        expected = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
            or opened.st_size != expected.st_size
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_nlink != 1
        ):
            raise SourceExchangeError(f"{field} changed while being opened")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(CHUNK_BYTES, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise SourceExchangeError(f"{field} exceeds its configured size bound")
        if total != opened.st_size:
            raise SourceExchangeError(f"{field} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _new_private_json(path: Path, value: Mapping[str, Any], *, field: str) -> bytes:
    path = _require_absolute(path, field=field)
    _require_root_private_directory(path.parent, field=field + " parent")
    if path.exists() or path.is_symlink():
        raise SourceExchangeError(f"refusing to overwrite {field}")
    payload = canonical_json_bytes(value) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise SourceExchangeError(f"cannot create {field}") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - defensive only.
                raise OSError("short JSON write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise SourceExchangeError(f"cannot write {field}") from exc
    finally:
        os.close(descriptor)
    return payload


def _require_new_workspace_child(workspace: Path, child: Path, *, field: str) -> Path:
    workspace = _require_root_private_directory(workspace, field="source exchange workspace")
    child = _require_absolute(child, field=field)
    if child.parent != workspace or not SAFE_CHILD_RE.fullmatch(child.name):
        raise SourceExchangeError(f"{field} must be a new simple child of the root-only workspace")
    if child.exists() or child.is_symlink():
        raise SourceExchangeError(f"refusing to overwrite {field}")
    return child


@contextlib.contextmanager
def _temporary_private_directory(workspace: Path, *, label: str) -> Iterator[Path]:
    workspace = _require_root_private_directory(workspace, field="source exchange workspace")
    old_umask = os.umask(0o077)
    try:
        temporary = Path(tempfile.mkdtemp(prefix="." + label + "-", dir=workspace))
    except OSError as exc:
        raise SourceExchangeError("cannot create source exchange private workspace") from exc
    finally:
        os.umask(old_umask)
    try:
        temporary.chmod(0o700)
        yield temporary
    finally:
        # This only removes a fresh directory created by this process under a
        # root-only workspace.  An interrupted process retains it for manual
        # inspection rather than silently retrying or altering an object.
        if temporary.exists():
            try:
                for entry in sorted(temporary.iterdir(), key=lambda item: item.name):
                    if entry.is_dir() and not entry.is_symlink():
                        for nested in entry.iterdir():
                            nested.unlink()
                        entry.rmdir()
                    else:
                        entry.unlink()
                temporary.rmdir()
            except OSError:
                pass


def _secure_hash_file(path: Path, *, field: str, maximum_bytes: int) -> tuple[str, int]:
    path = _require_root_private_file(path, field=field, maximum_bytes=maximum_bytes)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SourceExchangeError(f"cannot open {field}") from exc
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise SourceExchangeError(f"{field} exceeds its configured size bound")
            digest.update(chunk)
        after = os.fstat(descriptor)
        if total != before.st_size or before.st_dev != after.st_dev or before.st_ino != after.st_ino:
            raise SourceExchangeError(f"{field} changed while being hashed")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def _copy_private_snapshot(source: Path, destination: Path, *, maximum_bytes: int) -> tuple[str, int]:
    """Copy one immutable source FD so ``age`` never reopens a mutable input."""

    source = _require_root_private_file(source, field="source plaintext", maximum_bytes=maximum_bytes)
    destination = _require_absolute(destination, field="plaintext snapshot")
    _require_root_private_directory(destination.parent, field="plaintext snapshot parent")
    if destination.exists() or destination.is_symlink():
        raise SourceExchangeError("refusing to overwrite plaintext snapshot")
    source_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_fd: int | None = None
    destination_fd: int | None = None
    try:
        source_fd = os.open(source, source_flags)
        opened = os.fstat(source_fd)
        expected = source.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
            or opened.st_size != expected.st_size
            or opened.st_uid != 0
            or stat.S_IMODE(opened.st_mode) & 0o077
            or opened.st_nlink != 1
        ):
            raise SourceExchangeError("source plaintext changed while being opened")
        destination_fd = os.open(destination, destination_flags, 0o600)
        os.fchmod(destination_fd, 0o600)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_fd, CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise SourceExchangeError("source plaintext exceeds its configured size bound")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:  # pragma: no cover - defensive only.
                    raise OSError("short plaintext snapshot write")
                view = view[written:]
        if total != opened.st_size:
            raise SourceExchangeError("source plaintext changed while being copied")
        os.fsync(destination_fd)
        return digest.hexdigest(), total
    except OSError as exc:
        raise SourceExchangeError("cannot create immutable plaintext snapshot") from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if destination_fd is not None:
            os.close(destination_fd)


def _require_safe_executable(path: Path, *, field: str) -> Path:
    path = _require_absolute(path, field=field)
    _require_safe_ancestors(path.parent, field=field)
    try:
        state = path.lstat()
        resolved = path.resolve(strict=True)
        target = resolved.lstat()
    except OSError as exc:
        raise SourceExchangeError(f"cannot inspect {field}") from exc
    if (
        resolved != path
        or stat.S_ISLNK(state.st_mode)
        or stat.S_ISLNK(target.st_mode)
        or not stat.S_ISREG(target.st_mode)
        or target.st_uid != 0
        or stat.S_IMODE(target.st_mode) & 0o022
        or not (target.st_mode & stat.S_IXUSR)
    ):
        raise SourceExchangeError(f"{field} must be a root-owned non-writable executable")
    return resolved


def _run_command(command: Sequence[str]) -> None:
    """Run a fixed executable without leaking an argv URL into an exception."""

    try:
        subprocess.run(
            list(command),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            timeout=330,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise SourceExchangeError("external source exchange command failed") from exc


def _require_hash(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SourceExchangeError(f"{field} is invalid")
    return value


def _require_positive_int(value: object, *, field: str, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise SourceExchangeError(f"{field} is invalid")
    return value


def _request_to_value(request: Any) -> dict[str, Any]:
    return {
        "campaign_id": request.campaign_id,
        "release_sha": request.release_sha,
        "control_commit": request.control_commit,
        "control_tree": request.control_tree,
        "source_site": request.source_site,
        "destination_site": request.destination_site,
        "object_kind": request.object_kind,
        "object_id": request.object_id,
        "recipient_mode": request.mode,
        "recipients": list(request.recipients),
    }


def _request_from_value(value: object, *, policy: Any, field: str) -> Any:
    expected = {
        "campaign_id",
        "release_sha",
        "control_commit",
        "control_tree",
        "source_site",
        "destination_site",
        "object_kind",
        "object_id",
        "recipient_mode",
        "recipients",
    }
    if not isinstance(value, Mapping) or set(value) != expected or not isinstance(value.get("recipients"), list):
        raise SourceExchangeError(f"{field} is unsupported")
    request = contract.SourceObjectRequest(
        campaign_id=value.get("campaign_id"),
        release_sha=value.get("release_sha"),
        control_commit=value.get("control_commit"),
        control_tree=value.get("control_tree"),
        source_site=value.get("source_site"),
        destination_site=value.get("destination_site"),
        object_kind=value.get("object_kind"),
        object_id=value.get("object_id"),
        mode=value.get("recipient_mode"),
        recipients=tuple(value.get("recipients")),
    )
    try:
        contract.validate_request(policy, request)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError(f"{field} violates the FI source transport contract") from exc
    return request


def _require_outbound_request(policy: Any, request: Any) -> tuple[str, ...]:
    try:
        recipients = contract.validate_request(policy, request)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("outbound request violates the FI source transport contract") from exc
    allowed = {
        contract.STATIC_OBJECT_KIND,
        contract.RAW_APP_IMAGE_OBJECT_KIND,
        contract.SOURCE_EVIDENCE_OBJECT_KIND,
    }
    if request.source_site != "webapp_fi" or request.object_kind not in allowed:
        raise SourceExchangeError("FI may publish only static, raw-app-image, or source-evidence objects")
    return tuple(recipients)


def _require_generic_cli_initial_static_request(policy: Any, request: Any) -> None:
    """Keep the generic CLI from bypassing post-packet artifact derivation.

    The generic exchange remains the immutable transport primitive shared by
    the initial-static renderer and the narrow post-packet helper.  Its CLI,
    however, must not accept a caller-selected raw image or evidence request:
    those two routes require their fixed plaintext paths and IDs to be
    derived by ``prepare_webapp_fi_post_packet_upload.py``.  The only
    caller-supplied request file accepted by this CLI is the initial static
    archive's canonical dual-recipient route.
    """

    recipients = _require_outbound_request(policy, request)
    if (
        request.source_site != "webapp_fi"
        or request.destination_site != contract.STATIC_DESTINATION_SITE
        or request.object_kind != contract.STATIC_OBJECT_KIND
        or request.mode != contract.STATIC_MODE
        or tuple(request.recipients)
        != (policy.controller_age_recipient, policy.webapp_ir_age_recipient)
        or recipients != (policy.controller_age_recipient, policy.webapp_ir_age_recipient)
    ):
        raise SourceExchangeError(
            "generic FI source exchange CLI may handle only the initial dual-recipient static route"
        )


def _require_generic_cli_initial_static_prepared_upload(policy: Any, prepared_dir: Path) -> None:
    """Reject raw/evidence prepared uploads before the generic CLI can PUT.

    ``upload_prepared`` remains an internal transport primitive for the
    packet-derived helper.  The generic CLI must nevertheless re-open the
    immutable prepared receipt before delegating, because a pre-existing
    raw-image or evidence directory would otherwise bypass that helper.
    This performs only root-private local reads; it creates no marker and
    makes no network request.
    """

    try:
        policy = contract.validate_policy(policy)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("FI source exchange policy is invalid") from exc
    workspace = _require_root_private_directory(policy.workspace, field="source exchange workspace")
    directory = _require_absolute(Path(prepared_dir), field="prepared FI upload directory")
    if directory.parent != workspace:
        raise SourceExchangeError("prepared FI upload directory must be a direct workspace child")
    directory = _require_root_private_directory(directory, field="prepared FI upload directory")
    try:
        request, _recipients, _plaintext, _ciphertext, _prepared_sha256 = _verify_prepared_receipt(
            policy=policy,
            payload=_read_private_file(
                directory / PREPARED_RECEIPT_NAME,
                field="prepared FI upload receipt",
                maximum_bytes=MAX_JSON_BYTES,
            ),
        )
    except SourceExchangeError:
        raise
    except Exception as exc:  # pragma: no cover - defensive boundary for a malformed local receipt.
        raise SourceExchangeError("prepared FI upload receipt is invalid") from exc
    _require_generic_cli_initial_static_request(policy, request)


def _require_inbound_static_provenance_request(policy: Any, request: Any) -> None:
    try:
        contract.validate_request(policy, request)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("inbound request violates the FI source transport contract") from exc
    if (
        request.source_site != "controller"
        or request.destination_site != "webapp_fi"
        or request.object_kind != contract.STATIC_PROVENANCE_OBJECT_KIND
        or request.mode != contract.SINGLE_MODE
    ):
        raise SourceExchangeError("FI may receive only controller static-provenance in this source phase")


def load_policy(path: Path) -> Any:
    """Load one root-only, non-secret FI policy with no credential field."""

    _require_root_execution()
    payload = _read_private_file(path, field="FI source exchange policy", maximum_bytes=MAX_JSON_BYTES)
    raw = _parse_canonical_json(
        payload,
        field="FI source exchange policy",
    )
    expected = {
        "schema",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "age_binary",
        "workspace",
        "controller_age_recipient",
        "webapp_fi_age_recipient",
        "webapp_ir_age_recipient",
        "maximum_plaintext_bytes",
    }
    packet_expected = {
        "schema",
        "endpoint_host",
        "region",
        "bucket",
        "prefix",
        "age_binary",
        "workspace",
        "controller_age_recipient",
        "webapp_fi_age_recipient",
        "webapp_ir_age_recipient",
        "maximum_plaintext_bytes",
    }
    if set(raw) == expected and raw.get("schema") == EXCHANGE_CONFIG_SCHEMA:
        policy_value = dict(raw)
    elif set(raw) == packet_expected and raw.get("schema") == STATIC_PROVENANCE_POLICY_SCHEMA:
        # The controller packet must never persist even the public endpoint
        # URL.  Validate its exact packet contract, then reconstruct that
        # deterministic HTTPS origin in memory only for curl/transport use.
        try:
            projected, projected_raw, _ = packet_control.source_transport_policy_from_payload(payload)
        except Exception as exc:
            raise SourceExchangeError("FI source exchange packet policy is invalid") from exc
        if projected_raw != payload:
            raise SourceExchangeError("FI source exchange packet policy is not canonical")
        policy_value = {
            "endpoint": "https" + ":" + "//" + projected["endpoint_host"],
            "region": projected["region"],
            "bucket": projected["bucket"],
            "prefix": projected["prefix"],
            "age_binary": projected["age_binary"],
            "workspace": projected["workspace"],
            "controller_age_recipient": projected["controller_age_recipient"],
            "webapp_fi_age_recipient": projected["webapp_fi_age_recipient"],
            "webapp_ir_age_recipient": projected["webapp_ir_age_recipient"],
            "maximum_plaintext_bytes": projected["maximum_plaintext_bytes"],
        }
    else:
        raise SourceExchangeError("FI source exchange policy is unsupported")
    # The strict field allowlist is deliberate: credentials, tokens, and any
    # endpoint override beyond the public contract are rejected before use.
    try:
        policy = contract.SourceTransportPolicy(
            endpoint=policy_value.get("endpoint"),
            region=policy_value.get("region"),
            bucket=policy_value.get("bucket"),
            prefix=policy_value.get("prefix"),
            age_binary=policy_value.get("age_binary"),
            workspace=Path(policy_value.get("workspace")),
            controller_age_recipient=policy_value.get("controller_age_recipient"),
            webapp_fi_age_recipient=policy_value.get("webapp_fi_age_recipient"),
            webapp_ir_age_recipient=policy_value.get("webapp_ir_age_recipient"),
            maximum_plaintext_bytes=policy_value.get("maximum_plaintext_bytes"),
        )
        policy = contract.validate_policy(policy)
    except (TypeError, ValueError, contract.SourceTransportError) as exc:
        raise SourceExchangeError("FI source exchange policy is invalid") from exc
    _require_root_private_directory(policy.workspace, field="FI source exchange workspace")
    _require_safe_executable(Path(policy.age_binary), field="FI source exchange age binary")
    _require_safe_executable(Path(CURL_BINARY), field="FI source exchange curl binary")
    return policy


def load_request(path: Path, *, policy: Any) -> Any:
    """Read one URL-free typed request supplied through a root-only file."""

    _require_root_execution()
    payload = _read_private_file(path, field="FI source exchange request", maximum_bytes=MAX_JSON_BYTES)
    raw = _parse_canonical_json(payload, field="FI source exchange request", reject_url=True)
    return _request_from_value(raw, policy=policy, field="FI source exchange request")


def _prepared_unsigned(*, request: Any, policy: Any, recipients: Sequence[str], plaintext: Mapping[str, Any], ciphertext: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PREPARED_UPLOAD_SCHEMA,
        "status": "prepared",
        "request": _request_to_value(request),
        "object_key": contract.source_object_key(policy, request),
        "recipients": list(recipients),
        "plaintext": dict(plaintext),
        "ciphertext": dict(ciphertext),
    }


def _build_prepared_receipt(*, request: Any, policy: Any, recipients: Sequence[str], plaintext: Mapping[str, Any], ciphertext: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = _prepared_unsigned(
        request=request,
        policy=policy,
        recipients=recipients,
        plaintext=plaintext,
        ciphertext=ciphertext,
    )
    return {**unsigned, "prepared_sha256": sha256_bytes(canonical_json_bytes(unsigned))}


def _verify_prepared_receipt(*, policy: Any, payload: bytes) -> tuple[Any, tuple[str, ...], dict[str, Any], dict[str, Any], str]:
    value = _parse_canonical_json(payload, field="prepared FI upload receipt", reject_url=True)
    expected = {
        "schema",
        "status",
        "request",
        "object_key",
        "recipients",
        "plaintext",
        "ciphertext",
        "prepared_sha256",
    }
    if set(value) != expected or value.get("schema") != PREPARED_UPLOAD_SCHEMA or value.get("status") != "prepared":
        raise SourceExchangeError("prepared FI upload receipt is unsupported")
    request = _request_from_value(value.get("request"), policy=policy, field="prepared FI upload request")
    recipients = _require_outbound_request(policy, request)
    if value.get("recipients") != list(recipients) or value.get("object_key") != contract.source_object_key(policy, request):
        raise SourceExchangeError("prepared FI upload receipt is not bound to its exact contract request")
    expected_plaintext = {"sha256", "bytes"}
    expected_ciphertext = {"sha256", "bytes", "name"}
    plaintext = value.get("plaintext")
    ciphertext = value.get("ciphertext")
    if not isinstance(plaintext, Mapping) or set(plaintext) != expected_plaintext:
        raise SourceExchangeError("prepared FI upload plaintext binding is invalid")
    if not isinstance(ciphertext, Mapping) or set(ciphertext) != expected_ciphertext:
        raise SourceExchangeError("prepared FI upload ciphertext binding is invalid")
    normalized_plaintext = {
        "sha256": _require_hash(plaintext.get("sha256"), field="prepared plaintext SHA-256"),
        "bytes": _require_positive_int(
            plaintext.get("bytes"), field="prepared plaintext bytes", maximum=policy.maximum_plaintext_bytes
        ),
    }
    normalized_ciphertext = {
        "sha256": _require_hash(ciphertext.get("sha256"), field="prepared ciphertext SHA-256"),
        "bytes": _require_positive_int(
            ciphertext.get("bytes"),
            field="prepared ciphertext bytes",
            maximum=policy.maximum_plaintext_bytes + contract.MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
        ),
        "name": PREPARED_CIPHERTEXT_NAME,
    }
    if ciphertext.get("name") != PREPARED_CIPHERTEXT_NAME:
        raise SourceExchangeError("prepared FI upload ciphertext name is unsupported")
    unsigned = _prepared_unsigned(
        request=request,
        policy=policy,
        recipients=recipients,
        plaintext=normalized_plaintext,
        ciphertext=normalized_ciphertext,
    )
    if value.get("prepared_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise SourceExchangeError("prepared FI upload receipt checksum is invalid")
    return request, recipients, normalized_plaintext, normalized_ciphertext, value["prepared_sha256"]


def prepare_upload(
    *,
    policy: Any,
    request: Any,
    plaintext_path: Path,
    prepared_dir: Path,
    command_runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    """Encrypt one immutable FI source object before a controller presigns PUT.

    The resulting candidate contains only the ciphertext and a URL-free
    expectation receipt.  The caller sends that non-secret expectation to the
    controller; a distinct one-shot ``upload_prepared`` call receives the
    controller's transient PUT URL later.
    """

    _require_root_execution()
    try:
        policy = contract.validate_policy(policy)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("FI source exchange policy is invalid") from exc
    workspace = _require_root_private_directory(policy.workspace, field="FI source exchange workspace")
    _require_safe_executable(Path(policy.age_binary), field="FI source exchange age binary")
    recipients = _require_outbound_request(policy, request)
    output = _require_new_workspace_child(workspace, prepared_dir, field="prepared FI upload directory")
    _require_root_private_file(
        plaintext_path,
        field="source plaintext",
        maximum_bytes=policy.maximum_plaintext_bytes,
    )
    with _temporary_private_directory(workspace, label="prepare") as temporary:
        snapshot = temporary / "plaintext.snapshot"
        plaintext_sha256, plaintext_bytes = _copy_private_snapshot(
            plaintext_path,
            snapshot,
            maximum_bytes=policy.maximum_plaintext_bytes,
        )
        ciphertext = temporary / PREPARED_CIPHERTEXT_NAME
        command = [str(policy.age_binary)]
        for recipient in recipients:
            command.extend(("-r", recipient))
        command.extend(("-o", str(ciphertext), str(snapshot)))
        old_umask = os.umask(0o077)
        try:
            command_runner(command)
        except Exception as exc:
            raise SourceExchangeError("age encryption of FI source object failed") from exc
        finally:
            os.umask(old_umask)
        try:
            ciphertext.chmod(0o600)
        except OSError as exc:
            raise SourceExchangeError("cannot protect FI source ciphertext") from exc
        ciphertext_sha256, ciphertext_bytes = _secure_hash_file(
            ciphertext,
            field="prepared FI ciphertext",
            maximum_bytes=policy.maximum_plaintext_bytes + contract.MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
        )
        try:
            snapshot.unlink()
        except OSError as exc:
            raise SourceExchangeError("cannot remove transient FI plaintext snapshot") from exc
        plaintext = {"sha256": plaintext_sha256, "bytes": plaintext_bytes}
        cipher_binding = {
            "sha256": ciphertext_sha256,
            "bytes": ciphertext_bytes,
            "name": PREPARED_CIPHERTEXT_NAME,
        }
        receipt = _build_prepared_receipt(
            request=request,
            policy=policy,
            recipients=recipients,
            plaintext=plaintext,
            ciphertext=cipher_binding,
        )
        _new_private_json(temporary / PREPARED_RECEIPT_NAME, receipt, field="prepared FI upload receipt")
        try:
            os.rename(temporary, output)
        except OSError as exc:
            raise SourceExchangeError("cannot commit prepared FI upload directory") from exc
    return receipt


def _attempt_unsigned(*, request: Any, object_key: str, ciphertext: Mapping[str, Any], prepared_sha256: str) -> dict[str, Any]:
    return {
        "schema": UPLOAD_ATTEMPT_SCHEMA,
        "status": "put-attempted",
        "request": _request_to_value(request),
        "object_key": object_key,
        "ciphertext": dict(ciphertext),
        "prepared_sha256": prepared_sha256,
    }


def _create_upload_attempt(*, prepared_dir: Path, request: Any, object_key: str, ciphertext: Mapping[str, Any], prepared_sha256: str) -> None:
    marker = prepared_dir / UPLOAD_ATTEMPT_NAME
    unsigned = _attempt_unsigned(
        request=request,
        object_key=object_key,
        ciphertext=ciphertext,
        prepared_sha256=prepared_sha256,
    )
    _new_private_json(
        marker,
        {**unsigned, "attempt_sha256": sha256_bytes(canonical_json_bytes(unsigned))},
        field="FI source upload attempt marker",
    )


def _curl_upload(
    *,
    url: str,
    ciphertext: Path,
    header_output: Path,
    required_headers: Mapping[str, str],
    command_runner: CommandRunner,
) -> str:
    _require_safe_executable(Path(CURL_BINARY), field="FI source exchange curl binary")
    command: list[str] = [
        CURL_BINARY,
        "--disable",
        "--fail",
        "--silent",
        "--show-error",
        "--request",
        "PUT",
        "--upload-file",
        str(ciphertext),
        "--dump-header",
        str(header_output),
        "--output",
        "/dev/null",
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--max-redirs",
        "0",
        "--connect-timeout",
        "15",
        "--max-time",
        "300",
    ]
    for name, value in required_headers.items():
        command.extend(("--header", name + ": " + value))
    command.append(url)
    try:
        command_runner(command)
    except Exception as exc:
        raise SourceExchangeError("FI source Object Storage PUT failed; retry is intentionally blocked") from exc
    return _parse_response_version(header_output, field="FI source Object Storage PUT response")


def _require_upload_url_signs_headers(*, url: str, required_headers: Mapping[str, str]) -> None:
    """Require the presigned SigV4 envelope to bind every FI-supplied header.

    A syntactically valid presigned URL is insufficient: without these header
    names in ``X-Amz-SignedHeaders``, a proxy or a malformed controller plan
    could accept a PUT without the create-only precondition or the metadata
    the controller read-back verifies.  Values remain protected by the SigV4
    signature itself; this FI process does not possess credentials and cannot
    recompute that signature.
    """

    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:  # The contract already parses it; retain a local defensive check.
        raise SourceExchangeError("transient FI upload URL has malformed signed headers") from exc
    values = query.get("X-Amz-SignedHeaders")
    if values is None or len(values) != 1:
        raise SourceExchangeError("transient FI upload URL does not bind required request headers")
    names = values[0].split(";")
    if (
        not names
        or any(not name or name != name.lower() or not re.fullmatch(r"[a-z0-9-]+", name) for name in names)
        or len(names) != len(set(names))
    ):
        raise SourceExchangeError("transient FI upload URL has unsafe signed-header names")
    required = {"host", *(name.lower() for name in required_headers)}
    if not required.issubset(set(names)):
        raise SourceExchangeError("transient FI upload URL does not bind required create-only metadata headers")


def _split_header_blocks(payload: bytes, *, field: str) -> list[tuple[int, dict[str, list[str]]]]:
    if not payload or len(payload) > MAX_HTTP_HEADERS_BYTES:
        raise SourceExchangeError(f"{field} is missing or oversized")
    blocks = re.split(br"\r?\n\r?\n", payload)
    parsed: list[tuple[int, dict[str, list[str]]]] = []
    for block in blocks:
        if not block:
            continue
        lines = block.splitlines()
        if not lines or not lines[0].startswith(b"HTTP/"):
            raise SourceExchangeError(f"{field} is malformed")
        parts = lines[0].split(b" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            raise SourceExchangeError(f"{field} status is malformed")
        headers: dict[str, list[str]] = {}
        for raw_line in lines[1:]:
            if b":" not in raw_line or raw_line.startswith((b" ", b"\t")):
                raise SourceExchangeError(f"{field} is malformed")
            raw_name, raw_value = raw_line.split(b":", 1)
            try:
                name = raw_name.decode("ascii").lower()
                value = raw_value.strip().decode("ascii")
            except UnicodeDecodeError as exc:
                raise SourceExchangeError(f"{field} is not ASCII") from exc
            if not name or not re.fullmatch(r"[a-z0-9-]+", name) or any(ord(item) < 0x21 or ord(item) == 0x7F for item in value):
                raise SourceExchangeError(f"{field} is malformed")
            headers.setdefault(name, []).append(value)
        parsed.append((int(parts[1]), headers))
    if not parsed:
        raise SourceExchangeError(f"{field} is missing")
    return parsed


def _parse_response_version(header_path: Path, *, field: str) -> str:
    payload = _read_private_file(header_path, field=field, maximum_bytes=MAX_HTTP_HEADERS_BYTES)
    blocks = _split_header_blocks(payload, field=field)
    for status, _headers in blocks[:-1]:
        if status != 100:
            raise SourceExchangeError(f"{field} contains an unsupported intermediate response")
    status, headers = blocks[-1]
    if status not in {200, 201}:
        raise SourceExchangeError(f"{field} did not return a successful immutable object response")
    if "x-amz-server-side-encryption" in headers:
        raise SourceExchangeError(f"{field} enabled forbidden provider-side encryption")
    values = headers.get("x-amz-version-id")
    if values is None or len(values) != 1:
        raise SourceExchangeError(f"{field} lacks one exact VersionId")
    version_id = values[0]
    # Contract owns the VersionId grammar; validate it through a descriptor
    # rather than duplicating that security-sensitive pattern here.
    try:
        contract.validate_object_descriptor(
            {
                "object_key": "abc",
                "version_id": version_id,
                "ciphertext_sha256": "0" * 64,
                "ciphertext_bytes": 1,
                "plaintext_sha256": "0" * 64,
                "plaintext_bytes": 1,
            },
            maximum_plaintext_bytes=1,
        )
    except contract.SourceTransportError as exc:
        raise SourceExchangeError(f"{field} VersionId is invalid") from exc
    return version_id


def _upload_report_unsigned(*, request: Any, descriptor: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": UPLOAD_REPORT_SCHEMA,
        "status": "uploaded-awaiting-controller-readback",
        "request": _request_to_value(request),
        "object": dict(descriptor),
        "transport": {
            "create_only": True,
            "private_bucket": True,
            "provider_side_sse": False,
            "controller_readback_required": True,
        },
    }


def verify_upload_report(*, policy: Any, payload: bytes) -> dict[str, Any]:
    """Verify a URL-free FI upload report before a controller finalizes it."""

    try:
        policy = contract.validate_policy(policy)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("FI source exchange policy is invalid") from exc
    value = _parse_canonical_json(payload, field="FI source upload report", reject_url=True)
    expected = {"schema", "status", "request", "object", "transport", "report_sha256"}
    if set(value) != expected or value.get("schema") != UPLOAD_REPORT_SCHEMA or value.get("status") != "uploaded-awaiting-controller-readback":
        raise SourceExchangeError("FI source upload report is unsupported")
    request = _request_from_value(value.get("request"), policy=policy, field="FI source upload report request")
    _require_outbound_request(policy, request)
    try:
        descriptor = contract.validate_object_descriptor(
            value.get("object"), maximum_plaintext_bytes=policy.maximum_plaintext_bytes
        )
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("FI source upload report object binding is invalid") from exc
    if descriptor["object_key"] != contract.source_object_key(policy, request):
        raise SourceExchangeError("FI source upload report object key is not contract-bound")
    transport = {
        "create_only": True,
        "private_bucket": True,
        "provider_side_sse": False,
        "controller_readback_required": True,
    }
    if value.get("transport") != transport:
        raise SourceExchangeError("FI source upload report transport policy is unsupported")
    unsigned = _upload_report_unsigned(request=request, descriptor=descriptor)
    if value.get("report_sha256") != sha256_bytes(canonical_json_bytes(unsigned)):
        raise SourceExchangeError("FI source upload report checksum is invalid")
    return {**unsigned, "report_sha256": value["report_sha256"]}


def upload_prepared(
    *,
    policy: Any,
    prepared_dir: Path,
    upload_url: str,
    command_runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    """Upload a prepared ciphertext once, returning a URL-free VersionId report."""

    _require_root_execution()
    try:
        policy = contract.validate_policy(policy)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("FI source exchange policy is invalid") from exc
    workspace = _require_root_private_directory(policy.workspace, field="FI source exchange workspace")
    prepared_dir = _require_absolute(prepared_dir, field="prepared FI upload directory")
    if prepared_dir.parent != workspace:
        raise SourceExchangeError("prepared FI upload directory must be a direct workspace child")
    prepared_dir = _require_root_private_directory(prepared_dir, field="prepared FI upload directory")
    if (prepared_dir / UPLOAD_ATTEMPT_NAME).exists() or (prepared_dir / UPLOAD_ATTEMPT_NAME).is_symlink():
        raise SourceExchangeError("prepared FI upload already has an immutable PUT-attempt marker")
    if (prepared_dir / UPLOAD_REPORT_NAME).exists() or (prepared_dir / UPLOAD_REPORT_NAME).is_symlink():
        raise SourceExchangeError("prepared FI upload already has an immutable upload report")
    request, _recipients, plaintext, ciphertext, prepared_sha256 = _verify_prepared_receipt(
        policy=policy,
        payload=_read_private_file(
            prepared_dir / PREPARED_RECEIPT_NAME,
            field="prepared FI upload receipt",
            maximum_bytes=MAX_JSON_BYTES,
        ),
    )
    object_key = contract.source_object_key(policy, request)
    try:
        url = contract.require_create_only_presigned_put_url(upload_url, policy=policy, object_key=object_key)
        expectation = contract.SourceObjectExpectation(
            plaintext_sha256=plaintext["sha256"],
            plaintext_bytes=plaintext["bytes"],
            ciphertext_sha256=ciphertext["sha256"],
            ciphertext_bytes=ciphertext["bytes"],
        )
        headers = contract.required_upload_headers(expectation=expectation, mode=request.mode)
        _require_upload_url_signs_headers(url=url, required_headers=headers)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("transient FI upload URL or expectation is invalid") from exc
    payload_path = _require_root_private_file(
        prepared_dir / PREPARED_CIPHERTEXT_NAME,
        field="prepared FI ciphertext",
        maximum_bytes=policy.maximum_plaintext_bytes + contract.MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
    )
    actual_sha256, actual_bytes = _secure_hash_file(
        payload_path,
        field="prepared FI ciphertext",
        maximum_bytes=policy.maximum_plaintext_bytes + contract.MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
    )
    if actual_sha256 != ciphertext["sha256"] or actual_bytes != ciphertext["bytes"]:
        raise SourceExchangeError("prepared FI ciphertext differs from its immutable expectation")
    _create_upload_attempt(
        prepared_dir=prepared_dir,
        request=request,
        object_key=object_key,
        ciphertext=ciphertext,
        prepared_sha256=prepared_sha256,
    )
    with _temporary_private_directory(workspace, label="put") as temporary:
        headers_path = temporary / "response.headers"
        _new_private_json(headers_path, {"placeholder": True}, field="FI source curl header reservation")
        # curl replaces this root-only reservation only inside our private
        # temporary directory.  The existing file avoids an attacker-selected
        # output pathname and retains mode 0600 under the inherited umask.
        version_id = _curl_upload(
            url=url,
            ciphertext=payload_path,
            header_output=headers_path,
            required_headers=headers,
            command_runner=command_runner,
        )
    descriptor = {
        "object_key": object_key,
        "version_id": version_id,
        "ciphertext_sha256": ciphertext["sha256"],
        "ciphertext_bytes": ciphertext["bytes"],
        "plaintext_sha256": plaintext["sha256"],
        "plaintext_bytes": plaintext["bytes"],
    }
    try:
        descriptor = contract.validate_object_descriptor(
            descriptor, maximum_plaintext_bytes=policy.maximum_plaintext_bytes
        )
    except contract.SourceTransportError as exc:  # pragma: no cover - all fields were independently validated.
        raise SourceExchangeError("FI source upload descriptor is invalid") from exc
    unsigned = _upload_report_unsigned(request=request, descriptor=descriptor)
    report = {**unsigned, "report_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
    _new_private_json(prepared_dir / UPLOAD_REPORT_NAME, report, field="FI source upload report")
    return report


def _receive_unsigned(*, request: Any, descriptor: Mapping[str, Any], controller_receipt_sha256: str, plaintext_sha256: str, plaintext_bytes: int) -> dict[str, Any]:
    return {
        "schema": RECEIVE_RECEIPT_SCHEMA,
        "status": "received",
        "request": _request_to_value(request),
        "object": dict(descriptor),
        "controller_publish_receipt_sha256": controller_receipt_sha256,
        "plaintext": {
            "name": RECEIVED_PROVENANCE_NAME,
            "sha256": plaintext_sha256,
            "bytes": plaintext_bytes,
        },
        "transport": {
            "private_bucket": True,
            "provider_side_sse": False,
            "version_bound_get": True,
        },
    }


def _curl_download(
    *,
    url: str,
    output: Path,
    header_output: Path,
    command_runner: CommandRunner,
) -> str:
    _require_safe_executable(Path(CURL_BINARY), field="FI source exchange curl binary")
    command = [
        CURL_BINARY,
        "--disable",
        "--fail",
        "--silent",
        "--show-error",
        "--request",
        "GET",
        "--output",
        str(output),
        "--dump-header",
        str(header_output),
        "--proto",
        "=https",
        "--proto-redir",
        "=https",
        "--max-redirs",
        "0",
        "--connect-timeout",
        "15",
        "--max-time",
        "300",
        url,
    ]
    try:
        command_runner(command)
    except Exception as exc:
        raise SourceExchangeError("FI source Object Storage GET failed") from exc
    return _parse_response_version(header_output, field="FI source Object Storage GET response")


def receive_static_provenance(
    *,
    policy: Any,
    controller_publish_receipt_path: Path,
    download_url: str,
    age_identity_file: Path,
    destination_dir: Path,
    command_runner: CommandRunner = _run_command,
) -> dict[str, Any]:
    """Download, decrypt, and bind one controller static-provenance object."""

    _require_root_execution()
    try:
        policy = contract.validate_policy(policy)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("FI source exchange policy is invalid") from exc
    workspace = _require_root_private_directory(policy.workspace, field="FI source exchange workspace")
    _require_safe_executable(Path(policy.age_binary), field="FI source exchange age binary")
    controller_payload = _read_private_file(
        controller_publish_receipt_path,
        field="controller static-provenance publish receipt",
        maximum_bytes=MAX_JSON_BYTES,
    )
    try:
        controller_receipt = contract.verify_publish_receipt(config=policy, payload=controller_payload)
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("controller static-provenance publish receipt is invalid") from exc
    request = _request_from_value(
        {
            "campaign_id": controller_receipt["campaign_id"],
            "release_sha": controller_receipt["release_sha"],
            "control_commit": controller_receipt["control_commit"],
            "control_tree": controller_receipt["control_tree"],
            "source_site": controller_receipt["source_site"],
            "destination_site": controller_receipt["destination_site"],
            "object_kind": controller_receipt["object_kind"],
            "object_id": controller_receipt["object_id"],
            "recipient_mode": controller_receipt["recipient_mode"],
            "recipients": controller_receipt["recipients"],
        },
        policy=policy,
        field="controller static-provenance receipt request",
    )
    _require_inbound_static_provenance_request(policy, request)
    descriptor = controller_receipt["object"]
    try:
        url = contract.require_version_bound_presigned_get_url(
            download_url,
            policy=policy,
            object_key=descriptor["object_key"],
            version_id=descriptor["version_id"],
        )
    except contract.SourceTransportError as exc:
        raise SourceExchangeError("transient FI static-provenance download URL is invalid") from exc
    age_identity_file = _require_root_private_file(
        age_identity_file,
        field="FI age identity",
        maximum_bytes=MAX_AGE_IDENTITY_BYTES,
    )
    output = _require_new_workspace_child(workspace, destination_dir, field="received static-provenance directory")
    with _temporary_private_directory(workspace, label="get") as temporary:
        ciphertext = temporary / "payload.age"
        headers_path = temporary / "response.headers"
        # The files are already in a private directory; reserve them before
        # invoking curl/age so neither tool follows a caller-controlled name.
        _new_private_json(headers_path, {"placeholder": True}, field="FI source curl header reservation")
        _new_private_json(ciphertext, {"placeholder": True}, field="FI source ciphertext reservation")
        response_version = _curl_download(
            url=url,
            output=ciphertext,
            header_output=headers_path,
            command_runner=command_runner,
        )
        if response_version != descriptor["version_id"]:
            raise SourceExchangeError("FI static-provenance GET returned a different VersionId")
        ciphertext.chmod(0o600)
        cipher_sha256, cipher_bytes = _secure_hash_file(
            ciphertext,
            field="downloaded FI static-provenance ciphertext",
            maximum_bytes=policy.maximum_plaintext_bytes + contract.MAXIMUM_CIPHERTEXT_OVERHEAD_BYTES,
        )
        if cipher_sha256 != descriptor["ciphertext_sha256"] or cipher_bytes != descriptor["ciphertext_bytes"]:
            raise SourceExchangeError("downloaded FI static-provenance ciphertext differs from its exact receipt")
        plaintext = temporary / RECEIVED_PROVENANCE_NAME
        command = [
            str(policy.age_binary),
            "-d",
            "-i",
            str(age_identity_file),
            "-o",
            str(plaintext),
            str(ciphertext),
        ]
        old_umask = os.umask(0o077)
        try:
            command_runner(command)
        except Exception as exc:
            raise SourceExchangeError("age decryption of FI static-provenance failed") from exc
        finally:
            os.umask(old_umask)
        try:
            plaintext.chmod(0o600)
        except OSError as exc:
            raise SourceExchangeError("cannot protect decrypted FI static-provenance") from exc
        plaintext_sha256, plaintext_bytes = _secure_hash_file(
            plaintext,
            field="decrypted FI static-provenance",
            maximum_bytes=policy.maximum_plaintext_bytes,
        )
        if plaintext_sha256 != descriptor["plaintext_sha256"] or plaintext_bytes != descriptor["plaintext_bytes"]:
            raise SourceExchangeError("decrypted FI static-provenance differs from its exact receipt")
        # Static provenance is a JSON control artifact.  It is retained only
        # after ensuring it cannot be used to persist a transient transport URL.
        _parse_canonical_json(
            _read_private_file(
                plaintext,
                field="decrypted FI static-provenance",
                maximum_bytes=policy.maximum_plaintext_bytes,
            ),
            field="decrypted FI static-provenance",
            reject_url=True,
        )
        controller_receipt_sha256 = sha256_bytes(controller_payload)
        unsigned = _receive_unsigned(
            request=request,
            descriptor=descriptor,
            controller_receipt_sha256=controller_receipt_sha256,
            plaintext_sha256=plaintext_sha256,
            plaintext_bytes=plaintext_bytes,
        )
        receipt = {**unsigned, "receive_receipt_sha256": sha256_bytes(canonical_json_bytes(unsigned))}
        _new_private_json(temporary / RECEIVE_RECEIPT_NAME, receipt, field="FI static-provenance receive receipt")
        try:
            ciphertext.unlink()
            headers_path.unlink()
        except OSError as exc:
            raise SourceExchangeError("cannot remove transient FI static-provenance ciphertext") from exc
        try:
            os.rename(temporary, output)
        except OSError as exc:
            raise SourceExchangeError("cannot commit received FI static-provenance directory") from exc
    return receipt


def _print_result(value: Mapping[str, Any]) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-upload", help="encrypt one FI object before controller presigning")
    prepare.add_argument("--policy", required=True, type=Path)
    prepare.add_argument("--request", required=True, type=Path)
    prepare.add_argument("--plaintext", required=True, type=Path)
    prepare.add_argument("--prepared-dir", required=True, type=Path)

    upload = subparsers.add_parser("upload-prepared", help="perform exactly one direct FI PUT")
    upload.add_argument("--policy", required=True, type=Path)
    upload.add_argument("--prepared-dir", required=True, type=Path)
    upload.add_argument("--upload-url", required=True)

    receive = subparsers.add_parser("receive-static-provenance", help="GET/decrypt one controller provenance object")
    receive.add_argument("--policy", required=True, type=Path)
    receive.add_argument("--controller-publish-receipt", required=True, type=Path)
    receive.add_argument("--download-url", required=True)
    receive.add_argument("--age-identity-file", required=True, type=Path)
    receive.add_argument("--destination-dir", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if args.command == "prepare-upload":
            request = load_request(args.request, policy=policy)
            _require_generic_cli_initial_static_request(policy, request)
            result = prepare_upload(
                policy=policy,
                request=request,
                plaintext_path=args.plaintext,
                prepared_dir=args.prepared_dir,
            )
        elif args.command == "upload-prepared":
            _require_generic_cli_initial_static_prepared_upload(policy, args.prepared_dir)
            result = upload_prepared(policy=policy, prepared_dir=args.prepared_dir, upload_url=args.upload_url)
        elif args.command == "receive-static-provenance":
            result = receive_static_provenance(
                policy=policy,
                controller_publish_receipt_path=args.controller_publish_receipt,
                download_url=args.download_url,
                age_identity_file=args.age_identity_file,
                destination_dir=args.destination_dir,
            )
        else:  # pragma: no cover - argparse makes this unreachable.
            raise SourceExchangeError("unsupported FI source exchange command")
        _print_result(result)
        return 0
    except SourceExchangeError as exc:
        _print_result({"status": "blocked", "error": str(exc), "error_class": exc.__class__.__name__})
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
