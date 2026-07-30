#!/usr/bin/env python3
"""Bootstrap and verify the controller's campaign-scoped source-receive age key.

The controller must decrypt WebApp-FI source artifacts with a key that is
bound to exactly one immutable campaign binding.  This helper deliberately
does not accept an identity path, output directory, recipient, or executable
path from a caller.  The only production layout is::

    /etc/trading-bot-three-site/campaigns/<campaign>/controller/
        source-receive-age/identity.agekey
        source-receive-age/receipt.json

``bootstrap`` is a dry plan unless ``--apply`` is present.  An apply creates
the identity and its non-secret receipt once, with O_EXCL files, and preserves
all partial artifacts on failure.  ``verify`` re-reads the receipt and derives
the public recipient from the fixed private identity before returning any
non-secret result.  Neither command prints, returns, or persists the private
key outside its root-only identity file.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require an immutable lookup path before importing a sibling helper."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - deployment layout invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not metadata.st_mode & stat.S_ISVTX)
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    """Return one exact root-owned, non-writable sibling source file."""

    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    _require_root_controlled_directory_chain(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        after = resolved.lstat()
    except OSError as exc:  # pragma: no cover - deployment layout invariant.
        raise RuntimeError(f"cannot inspect {field}") from exc
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
    if (
        resolved != path
        or stat.S_ISLNK(before.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or after.st_uid != 0
        or after.st_nlink != 1
        or stat.S_IMODE(after.st_mode) & 0o022
        or after.st_mode & unsafe_bits
    ):
        raise RuntimeError(f"{field} is not a root-owned non-writable regular non-symlink file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load one named root-controlled sibling without consulting ``sys.path``."""

    if (
        not isinstance(filename, str)
        or not filename
        or Path(filename).name != filename
        or filename in {".", ".."}
    ):
        raise RuntimeError("required sibling filename is not a safe leaf name")
    source = _require_root_controlled_code_file(
        Path(__file__),
        field="controller source receive identity source",
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


binding = _load_exact_sibling(
    "webapp_fi_source_campaign_binding.py",
    "_controller_source_receive_identity_binding",
)


CAMPAIGNS_ROOT = Path("/etc/trading-bot-three-site/campaigns")
AGE_KEYGEN_BINARY = Path("/usr/bin/age-keygen")
CONTROLLER_DIRECTORY_NAME = "controller"
IDENTITY_DIRECTORY_NAME = "source-receive-age"
IDENTITY_FILENAME = "identity.agekey"
IDENTITY_RECEIPT_FILENAME = "receipt.json"
IDENTITY_RECEIPT_SCHEMA = "gold-trade-controller-source-receive-age-identity-receipt-v1"
IDENTITY_KEY_ID_DOMAIN = b"gold-trade-controller-source-receive-age-key-id-v1\x00"
MAXIMUM_IDENTITY_BYTES = 256 * 1024
MAXIMUM_RECEIPT_BYTES = 16 * 1024
MAXIMUM_AGE_KEYGEN_OUTPUT_BYTES = 1024
# Keep the receipt grammar aligned with the pinned controller transport
# policy.  ``age-keygen -y`` still has to emit exactly one canonical line.
AGE_RECIPIENT_RE = re.compile(r"^age1[ac-hj-np-z02-9]{20,128}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ControllerSourceReceiveIdentityError(RuntimeError):
    """The controller's campaign identity could not be safely proven."""


@dataclasses.dataclass(frozen=True)
class IdentityLayout:
    """The non-negotiable private layout derived from one campaign binding."""

    campaign_id: str
    campaign_binding_sha256: str
    campaign_directory: Path
    controller_directory: Path
    identity_directory: Path
    identity_path: Path
    receipt_path: Path


@dataclasses.dataclass(frozen=True)
class VerifiedIdentity:
    """A verified fixed identity and its public campaign-bound recipient."""

    layout: IdentityLayout
    recipient: str
    key_id: str
    receipt_sha256: str


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Encode immutable non-secret records in exactly one ASCII form."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControllerSourceReceiveIdentityError("identity receipt contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ControllerSourceReceiveIdentityError("identity receipt contains an unsupported JSON constant")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise ControllerSourceReceiveIdentityError("controller source receive identity operations must run as root")


def _raise_binding_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except binding.CampaignBindingError as exc:
        raise ControllerSourceReceiveIdentityError(message) from exc


def _require_exact_campaign_binding_path(path: Path) -> tuple[Any, Path]:
    """Load a binding and require its path to live under the fixed campaign root."""

    campaign = _raise_binding_error(
        lambda: binding.load_campaign_binding(Path(path)),
        message="canonical campaign binding is invalid",
    )
    expected = (
        CAMPAIGNS_ROOT
        / campaign.campaign_id
        / binding.SOURCE_PHASE_DIRECTORY
        / binding.CAMPAIGN_BINDING_FILENAME
    )
    actual = Path(path)
    if actual != expected:
        raise ControllerSourceReceiveIdentityError("campaign binding is not installed at its fixed campaign path")
    return campaign, expected.parent.parent


def identity_layout_for_campaign_binding(campaign_binding_path: Path) -> IdentityLayout:
    """Derive every private path from the canonical binding, never caller text."""

    campaign, campaign_directory = _require_exact_campaign_binding_path(Path(campaign_binding_path))
    controller_directory = campaign_directory / CONTROLLER_DIRECTORY_NAME
    identity_directory = controller_directory / IDENTITY_DIRECTORY_NAME
    return IdentityLayout(
        campaign_id=campaign.campaign_id,
        campaign_binding_sha256=campaign.binding_sha256,
        campaign_directory=campaign_directory,
        controller_directory=controller_directory,
        identity_directory=identity_directory,
        identity_path=identity_directory / IDENTITY_FILENAME,
        receipt_path=identity_directory / IDENTITY_RECEIPT_FILENAME,
    )


def _require_private_directory(path: Path, *, field: str) -> Path:
    return _raise_binding_error(
        lambda: binding._require_root_private_directory(path, field=field),
        message=f"{field} is unsafe",
    )


def _require_private_file(path: Path, *, field: str, maximum_bytes: int) -> Path:
    result = _raise_binding_error(
        lambda: binding._require_root_private_file(path, field=field),
        message=f"{field} is unsafe",
    )
    try:
        size = result.lstat().st_size
    except OSError as exc:  # pragma: no cover - verifier already inspected the path.
        raise ControllerSourceReceiveIdentityError(f"cannot recheck {field}") from exc
    if size > maximum_bytes:
        raise ControllerSourceReceiveIdentityError(f"{field} is too large")
    return result


def _read_private_file(path: Path, *, field: str, maximum_bytes: int) -> bytes:
    path = _require_private_file(path, field=field, maximum_bytes=maximum_bytes)
    try:
        return binding._read_root_private_file(path, field=field)
    except binding.CampaignBindingError as exc:
        raise ControllerSourceReceiveIdentityError(f"cannot securely read {field}") from exc


def _mkdir_private_child(parent: Path, name: str, *, field: str) -> Path:
    """Create or verify exactly one root-only child without traversal input."""

    if not isinstance(name, str) or not name or Path(name).name != name or name in {".", ".."}:
        raise ControllerSourceReceiveIdentityError(f"{field} name is invalid")
    parent = _require_private_directory(parent, field=field + " parent")
    child = parent / name
    try:
        os.mkdir(child, 0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError(f"cannot create {field}") from exc
    _require_private_directory(child, field=field)
    _fsync_directory(parent, field=field + " parent")
    return child


def _fsync_directory(path: Path, *, field: str) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError(f"cannot open {field}") from exc
    try:
        os.fsync(descriptor)
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError(f"cannot durably sync {field}") from exc
    finally:
        os.close(descriptor)


def _assert_absent(path: Path, *, field: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError(f"cannot inspect {field}") from exc
    raise ControllerSourceReceiveIdentityError(f"{field} already exists and will not be reused")


def _require_if_present_private_directory(path: Path, *, field: str) -> None:
    """Validate a pre-existing fixed directory without creating it during plan."""

    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError(f"cannot inspect {field}") from exc
    _require_private_directory(path, field=field)


def _assert_identity_outputs_creatable(layout: IdentityLayout) -> None:
    """Reject unsafe or reused fixed outputs before either plan or apply mutates."""

    _require_if_present_private_directory(
        layout.controller_directory,
        field="controller campaign directory",
    )
    _require_if_present_private_directory(
        layout.identity_directory,
        field="controller source receive identity directory",
    )
    _assert_absent(layout.identity_path, field="controller source receive identity")
    _assert_absent(layout.receipt_path, field="controller source receive identity receipt")


def _require_trusted_age_keygen() -> Path:
    """Return the fixed production generator after root-control validation."""

    # There is intentionally no CLI or function parameter for this path.  The
    # module constant is the production pin; unit tests patch the constant
    # directly while exercising local-only fake generation.
    binary = AGE_KEYGEN_BINARY
    return _raise_binding_error(
        lambda: binding._require_root_controlled_executable(binary, field="age-keygen binary"),
        message="age-keygen binary is unsafe",
    )


def _identity_stat(path: Path, *, field: str) -> os.stat_result:
    checked = _require_private_file(path, field=field, maximum_bytes=MAXIMUM_IDENTITY_BYTES)
    try:
        return checked.lstat()
    except OSError as exc:  # pragma: no cover - verifier already inspected the path.
        raise ControllerSourceReceiveIdentityError(f"cannot recheck {field}") from exc


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_uid == right.st_uid
        and left.st_nlink == right.st_nlink
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ctime_ns == right.st_ctime_ns
    )


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Best-effort cleanup after a bounded child-output failure."""

    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError:
        return
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _write_descriptor(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    try:
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - regular-file writes do not normally return zero.
                raise OSError("short age-keygen identity write")
            view = view[written:]
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError("cannot write controller source receive identity") from exc


def _run_bounded_age_keygen_stdout(
    *,
    command: Sequence[str],
    maximum_bytes: int,
    action: str,
    overflow_error: str,
    on_chunk: Callable[[bytes], None],
) -> None:
    """Run trusted age-keygen while capping output before it reaches memory/disk.

    A pipe bounds the child itself through backpressure.  The parent requests
    at most one byte beyond the allowed total and kills the child before that
    byte reaches a private identity descriptor or a larger in-memory buffer.
    """

    if not command or maximum_bytes < 1 or not callable(on_chunk):  # pragma: no cover - internal invariants.
        raise ControllerSourceReceiveIdentityError("age-keygen bounded output invocation is invalid")
    try:
        process = subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            close_fds=True,
        )
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError(f"{action} failed") from exc
    stream = process.stdout
    if stream is None:  # pragma: no cover - Popen stdout=PIPE invariant.
        _stop_process(process)
        raise ControllerSourceReceiveIdentityError(f"{action} output is unavailable")
    descriptor = stream.fileno()
    deadline = time.monotonic() + 30
    total = 0
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                raise ControllerSourceReceiveIdentityError(f"{action} timed out")
            try:
                readable, _writable, _exceptional = select.select([descriptor], [], [], remaining)
            except (OSError, ValueError) as exc:
                _stop_process(process)
                raise ControllerSourceReceiveIdentityError(f"cannot read {action} output") from exc
            if not readable:
                _stop_process(process)
                raise ControllerSourceReceiveIdentityError(f"{action} timed out")
            try:
                chunk = os.read(descriptor, min(4096, maximum_bytes + 1 - total))
            except OSError as exc:
                _stop_process(process)
                raise ControllerSourceReceiveIdentityError(f"cannot read {action} output") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                _stop_process(process)
                raise ControllerSourceReceiveIdentityError(overflow_error)
            try:
                on_chunk(chunk)
            except ControllerSourceReceiveIdentityError:
                _stop_process(process)
                raise
            except Exception as exc:  # pragma: no cover - the only production sink is FD writing.
                _stop_process(process)
                raise ControllerSourceReceiveIdentityError(f"cannot consume {action} output") from exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process)
            raise ControllerSourceReceiveIdentityError(f"{action} timed out")
        try:
            returncode = process.wait(timeout=remaining)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _stop_process(process)
            raise ControllerSourceReceiveIdentityError(f"{action} failed") from exc
        if returncode != 0:
            raise ControllerSourceReceiveIdentityError(f"{action} failed")
    finally:
        try:
            stream.close()
        finally:
            _stop_process(process)


def _run_age_keygen_to_descriptor(binary: Path, descriptor: int) -> None:
    """Generate only into the caller's O_EXCL private descriptor, never stdout."""

    _run_bounded_age_keygen_stdout(
        command=(str(binary),),
        maximum_bytes=MAXIMUM_IDENTITY_BYTES,
        action="age-keygen identity generation",
        overflow_error="generated controller source receive identity is unsafe",
        on_chunk=lambda chunk: _write_descriptor(descriptor, chunk),
    )


def _create_identity_file(path: Path, *, binary: Path) -> Path:
    """Create one non-reusable private identity via an FD owned by this process."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity already exists and will not be reused"
        ) from exc
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError("cannot create controller source receive identity") from exc
    try:
        os.fchmod(descriptor, 0o600)
        _run_age_keygen_to_descriptor(binary, descriptor)
        state = os.fstat(descriptor)
        if not (
            stat.S_ISREG(state.st_mode)
            and state.st_uid == 0
            and stat.S_IMODE(state.st_mode) == 0o600
            and state.st_nlink == 1
            and 1 <= state.st_size <= MAXIMUM_IDENTITY_BYTES
        ):
            raise ControllerSourceReceiveIdentityError("generated controller source receive identity is unsafe")
        os.fsync(descriptor)
    except ControllerSourceReceiveIdentityError:
        raise
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError("cannot durably create controller source receive identity") from exc
    finally:
        os.close(descriptor)
    _require_private_file(path, field="controller source receive identity", maximum_bytes=MAXIMUM_IDENTITY_BYTES)
    _fsync_directory(path.parent, field="controller source receive identity directory")
    return path


def _parse_age_recipient(payload: bytes) -> str:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAXIMUM_AGE_KEYGEN_OUTPUT_BYTES:
        raise ControllerSourceReceiveIdentityError("age-keygen recipient output is unsafe")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ControllerSourceReceiveIdentityError("age-keygen recipient output is not ASCII") from exc
    if not text.endswith("\n") or text.count("\n") != 1 or "\r" in text:
        raise ControllerSourceReceiveIdentityError("age-keygen recipient output is not one line")
    recipient = text[:-1]
    if not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise ControllerSourceReceiveIdentityError("age-keygen recipient output is invalid")
    return recipient


def _derive_recipient_output(binary: Path, identity: Path) -> bytes:
    """Read one public recipient through the same bounded FD discipline."""

    payload = bytearray()
    _run_bounded_age_keygen_stdout(
        command=(str(binary), "-y", str(identity)),
        maximum_bytes=MAXIMUM_AGE_KEYGEN_OUTPUT_BYTES,
        action="age-keygen recipient derivation",
        overflow_error="age-keygen recipient output is unsafe",
        on_chunk=payload.extend,
    )
    return bytes(payload)


def derive_recipient(identity_path: Path) -> str:
    """Derive one public recipient using only the trusted fixed binary.

    The private file is checked before and after the subprocess.  Its lookup
    parent is root-only, and no key bytes cross this function's boundary.
    """

    binary = _require_trusted_age_keygen()
    identity = _require_private_file(
        Path(identity_path),
        field="controller source receive identity",
        maximum_bytes=MAXIMUM_IDENTITY_BYTES,
    )
    before = _identity_stat(identity, field="controller source receive identity")
    output = _derive_recipient_output(binary, identity)
    after = _identity_stat(identity, field="controller source receive identity")
    if not _same_file(before, after):
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity changed while deriving recipient"
        )
    return _parse_age_recipient(output)


def key_id_for_recipient(recipient: str) -> str:
    if not isinstance(recipient, str) or not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise ControllerSourceReceiveIdentityError("controller source receive recipient is invalid")
    return sha256_bytes(IDENTITY_KEY_ID_DOMAIN + recipient.encode("ascii"))


def _receipt_value(*, layout: IdentityLayout, recipient: str) -> dict[str, str]:
    return {
        "schema": IDENTITY_RECEIPT_SCHEMA,
        "status": "bound",
        "campaign_id": layout.campaign_id,
        "campaign_binding_sha256": layout.campaign_binding_sha256,
        "recipient": recipient,
        "key_id": key_id_for_recipient(recipient),
    }


def _parse_receipt(payload: bytes, *, layout: IdentityLayout) -> tuple[str, str]:
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAXIMUM_RECEIPT_BYTES:
        raise ControllerSourceReceiveIdentityError("controller source receive identity receipt has an unsafe size")
    lowered = payload.lower()
    if b"://" in lowered or b'"url"' in lowered or b"presigned" in lowered:
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity receipt persists a forbidden URL"
        )
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity receipt is not strict JSON"
        ) from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise ControllerSourceReceiveIdentityError("controller source receive identity receipt is not canonical JSON")
    expected = {"schema", "status", "campaign_id", "campaign_binding_sha256", "recipient", "key_id"}
    if set(value) != expected or value.get("schema") != IDENTITY_RECEIPT_SCHEMA or value.get("status") != "bound":
        raise ControllerSourceReceiveIdentityError("controller source receive identity receipt is unsupported")
    if value.get("campaign_id") != layout.campaign_id:
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity receipt campaign does not match binding"
        )
    if value.get("campaign_binding_sha256") != layout.campaign_binding_sha256:
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity receipt binding does not match campaign"
        )
    recipient = value.get("recipient")
    if not isinstance(recipient, str) or not AGE_RECIPIENT_RE.fullmatch(recipient):
        raise ControllerSourceReceiveIdentityError("controller source receive identity receipt recipient is invalid")
    key_id = value.get("key_id")
    if not isinstance(key_id, str) or not SHA256_RE.fullmatch(key_id) or key_id != key_id_for_recipient(recipient):
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity receipt key identifier is invalid"
        )
    return recipient, key_id


def _write_new_receipt(path: Path, *, layout: IdentityLayout, recipient: str) -> str:
    payload = canonical_json_bytes(_receipt_value(layout=layout, recipient=recipient)) + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity receipt already exists and will not be reused"
        ) from exc
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError("cannot create controller source receive identity receipt") from exc
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:  # pragma: no cover - regular file writes do not normally return zero.
                raise OSError("short controller source receive identity receipt write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise ControllerSourceReceiveIdentityError(
            "cannot durably create controller source receive identity receipt"
        ) from exc
    finally:
        os.close(descriptor)
    _require_private_file(
        path,
        field="controller source receive identity receipt",
        maximum_bytes=MAXIMUM_RECEIPT_BYTES,
    )
    _fsync_directory(path.parent, field="controller source receive identity directory")
    return sha256_bytes(payload)


def plan_or_apply_identity_bootstrap(*, campaign_binding_path: Path, apply: bool = False) -> dict[str, str]:
    """Plan or explicitly create a fresh campaign-derived controller identity."""

    _require_root_execution()
    if not isinstance(apply, bool):
        raise ControllerSourceReceiveIdentityError("identity bootstrap apply flag is invalid")
    layout = identity_layout_for_campaign_binding(Path(campaign_binding_path))
    _require_trusted_age_keygen()
    _require_private_directory(layout.campaign_directory, field="campaign directory")
    _assert_identity_outputs_creatable(layout)
    if not apply:
        return {
            "status": "planned",
            "campaign_id": layout.campaign_id,
            "campaign_binding_sha256": layout.campaign_binding_sha256,
            "identity_path": str(layout.identity_path),
            "receipt_path": str(layout.receipt_path),
        }
    controller_directory = _mkdir_private_child(
        layout.campaign_directory,
        CONTROLLER_DIRECTORY_NAME,
        field="controller campaign directory",
    )
    if controller_directory != layout.controller_directory:  # pragma: no cover - fixed child invariant.
        raise ControllerSourceReceiveIdentityError("controller campaign directory changed while being created")
    identity_directory = _mkdir_private_child(
        controller_directory,
        IDENTITY_DIRECTORY_NAME,
        field="controller source receive identity directory",
    )
    if identity_directory != layout.identity_directory:  # pragma: no cover - fixed child invariant.
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity directory changed while being created"
        )
    _assert_identity_outputs_creatable(layout)
    _create_identity_file(layout.identity_path, binary=_require_trusted_age_keygen())
    recipient = derive_recipient(layout.identity_path)
    receipt_sha256 = _write_new_receipt(layout.receipt_path, layout=layout, recipient=recipient)
    verified = load_verified_identity(campaign_binding_path=campaign_binding_path)
    if verified.recipient != recipient or verified.receipt_sha256 != receipt_sha256:
        raise ControllerSourceReceiveIdentityError(
            "created controller source receive identity changed while being verified"
        )
    return {
        "status": "created",
        "campaign_id": layout.campaign_id,
        "campaign_binding_sha256": layout.campaign_binding_sha256,
        "recipient": verified.recipient,
        "key_id": verified.key_id,
        "receipt_sha256": verified.receipt_sha256,
    }


def load_verified_identity(*, campaign_binding_path: Path) -> VerifiedIdentity:
    """Re-read the exact receipt and prove it matches the fixed private key."""

    _require_root_execution()
    layout = identity_layout_for_campaign_binding(Path(campaign_binding_path))
    _require_trusted_age_keygen()
    _require_private_directory(layout.campaign_directory, field="campaign directory")
    _require_private_directory(layout.controller_directory, field="controller campaign directory")
    _require_private_directory(layout.identity_directory, field="controller source receive identity directory")
    _require_private_file(
        layout.identity_path,
        field="controller source receive identity",
        maximum_bytes=MAXIMUM_IDENTITY_BYTES,
    )
    payload = _read_private_file(
        layout.receipt_path,
        field="controller source receive identity receipt",
        maximum_bytes=MAXIMUM_RECEIPT_BYTES,
    )
    receipt_recipient, key_id = _parse_receipt(payload, layout=layout)
    derived_recipient = derive_recipient(layout.identity_path)
    if derived_recipient != receipt_recipient:
        raise ControllerSourceReceiveIdentityError(
            "controller source receive identity recipient does not match its receipt"
        )
    return VerifiedIdentity(
        layout=layout,
        recipient=derived_recipient,
        key_id=key_id,
        receipt_sha256=sha256_bytes(payload),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    bootstrap = actions.add_parser("bootstrap", help="plan or explicitly create a fresh fixed controller identity")
    bootstrap.add_argument("--campaign-binding", required=True, type=Path)
    bootstrap.add_argument(
        "--apply",
        action="store_true",
        help="create the identity only after the dry plan is approved",
    )
    verify = actions.add_parser("verify", help="verify the fixed controller identity and non-secret receipt")
    verify.add_argument("--campaign-binding", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.action == "bootstrap":
            result = plan_or_apply_identity_bootstrap(
                campaign_binding_path=args.campaign_binding,
                apply=args.apply,
            )
        elif args.action == "verify":
            verified = load_verified_identity(campaign_binding_path=args.campaign_binding)
            result = {
                "status": "verified",
                "campaign_id": verified.layout.campaign_id,
                "campaign_binding_sha256": verified.layout.campaign_binding_sha256,
                "recipient": verified.recipient,
                "key_id": verified.key_id,
                "receipt_sha256": verified.receipt_sha256,
            }
        else:  # pragma: no cover - argparse dispatch invariant.
            raise ControllerSourceReceiveIdentityError("unsupported action")
    except ControllerSourceReceiveIdentityError as exc:
        print(
            canonical_json_bytes(
                {"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}
            ).decode("ascii"),
            file=sys.stderr,
        )
        return 2
    print(canonical_json_bytes(result).decode("ascii"))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper.
    raise SystemExit(main())
