#!/usr/bin/env python3
"""Bridge the three reversible freeze Nginx actions into the cutover journal.

Plan mode is the default and never contacts production.  Apply mode accepts
only an anonymous controller-liveness pipe and invokes the existing Nginx
coordinator through its Python API.  Every action is followed by a distinct
external readback before normalized, create-only phase evidence is submitted
to the release-owned verifier.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import sys
import threading
from typing import Any, Callable, Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.canonical_json import canonical_json_bytes  # noqa: E402
from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    write_secure_new_bytes,
)
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX,
)
from scripts import production_shadow_cutover_controller as CONTROLLER  # noqa: E402
from scripts import verify_production_shadow_phase_evidence as VERIFY  # noqa: E402


REQUEST_SCHEMA = "production-shadow-nginx-cutover-phase-bridge-request-v1"
NORMALIZATION_SCHEMA = (
    "production-shadow-nginx-cutover-phase-normalization-v1"
)
RESULT_SCHEMA = "production-shadow-nginx-cutover-phase-bridge-result-v1"
ROLE_VALIDATION_SCHEMA = "production-shadow-host-agent-validation-v1"
CLAIM_SOURCE_SCHEMA = "production-shadow-phase-claim-source-v1"
PHASES = (
    "freeze_generation_install",
    "freeze_generation_test",
    "freeze_generation_activate",
)
PHASE_ACTIONS = {
    "freeze_generation_install": ("install", None, "legacy-normal"),
    "freeze_generation_test": ("test", "legacy-frozen", "legacy-normal"),
    "freeze_generation_activate": (
        "activate",
        "legacy-frozen",
        "legacy-frozen",
    ),
}
INITIAL_PRIOR_PHASES = (
    "pre_freeze_evidence",
    "shadow_startup_normalization",
)
ROLE_ORDER = ("bot_fi", "webapp_fi")
CONSTRAINT_FIELDS = frozenset(
    {
        "business_write_allowed",
        "caller_claims_accepted",
        "caller_readback_assertions_accepted",
        "postcommit_allowed",
        "rollback_allowed",
        "writable_generation_allowed",
    }
)
REQUEST_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "release_tree_sha",
        "controller_manifest_path",
        "controller_manifest_sha256",
        "plan_sha256",
        "approval_path",
        "approval_sha256",
        "approval_policy_path",
        "approval_policy_sha256",
        "nginx_paths",
        "nginx_aggregate_sha256",
        "nginx_role_manifest_sha256",
        "nginx_role_archive_sha256",
        "known_hosts_sha256",
        "ssh_identity_sha256",
        "prior_phase_evidence",
        "constraints",
    }
)
NGINX_PATH_FIELDS = frozenset(
    {
        "aggregate",
        "bot_fi_manifest",
        "bot_fi_archive",
        "webapp_fi_manifest",
        "webapp_fi_archive",
        "known_hosts",
        "ssh_identity",
    }
)
PRIOR_ROW_FIELDS = frozenset({"path", "sha256"})
MAX_REQUEST_BYTES = 1024 * 1024
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
ZERO_SHA256 = "0" * 64
SHA256_RE = CONTROLLER.SHA256_RE


class NginxCutoverPhaseBridgeError(RuntimeError):
    """The three-phase bridge cannot safely advance."""


class NginxCutoverPhaseBridgeCancellation(
    NginxCutoverPhaseBridgeError
):
    """Controller liveness or a one-shot signal cancelled the bridge."""


@dataclass(frozen=True)
class BridgePaths:
    aggregate: Path
    bot_fi_manifest: Path
    bot_fi_archive: Path
    webapp_fi_manifest: Path
    webapp_fi_archive: Path
    known_hosts: Path
    ssh_identity: Path


@dataclass(frozen=True)
class BridgeContext:
    request_path: Path
    request: dict[str, Any]
    manifest_path: Path
    manifest: dict[str, Any]
    manifest_sha256: str
    plan: dict[str, Any]
    approval_path: Path
    approval_policy_path: Path
    nginx_paths: BridgePaths
    nginx_inputs: NGINX.CoordinatorInputs
    prior_paths: dict[str, Path]
    output_root: Path


NginxExecutor = Callable[..., dict[str, Any]]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise NginxCutoverPhaseBridgeError(
                f"duplicate bridge request field: {key}"
            )
        result[key] = value
    return result


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise NginxCutoverPhaseBridgeError(
            f"{label} is not a nonzero SHA-256"
        )
    return value


def _absolute_path(value: Any, *, label: str) -> Path:
    if not isinstance(value, str):
        raise NginxCutoverPhaseBridgeError(f"{label} path is invalid")
    path = Path(value)
    if (
        not path.is_absolute()
        or ".." in path.parts
        or path != Path(os.path.abspath(value))
        or "\0" in value
    ):
        raise NginxCutoverPhaseBridgeError(
            f"{label} must be an absolute normalized path"
        )
    return path


def _read_private_bytes(
    path: Path,
    *,
    label: str,
    maximum: int,
) -> bytes:
    try:
        return NGINX._read_private_file(  # noqa: SLF001
            path,
            label=label,
            maximum=maximum,
            exact_mode=0o600,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise NginxCutoverPhaseBridgeError(
            f"{label} is unavailable or unsafe"
        ) from exc


def _read_request(path: Path) -> tuple[dict[str, Any], str]:
    payload = _read_private_bytes(
        path,
        label="Nginx cutover phase bridge request",
        maximum=MAX_REQUEST_BYTES,
    )
    try:
        document = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NginxCutoverPhaseBridgeError(
            "bridge request is not strict UTF-8 JSON"
        ) from exc
    if (
        not isinstance(document, dict)
        or set(document) != REQUEST_FIELDS
        or payload != canonical_json_bytes(document)
        or document["schema"] != REQUEST_SCHEMA
    ):
        raise NginxCutoverPhaseBridgeError(
            "bridge request fields or canonical encoding differ"
        )
    return document, _sha256(payload)


def _nginx_paths(value: Any) -> BridgePaths:
    if not isinstance(value, dict) or set(value) != NGINX_PATH_FIELDS:
        raise NginxCutoverPhaseBridgeError(
            "Nginx input path fields are not exact"
        )
    paths = {
        field: _absolute_path(value[field], label=f"Nginx {field}")
        for field in NGINX_PATH_FIELDS
    }
    if len(set(paths.values())) != len(paths):
        raise NginxCutoverPhaseBridgeError(
            "Nginx input paths must be distinct"
        )
    return BridgePaths(**paths)


def _load_nginx_inputs(paths: BridgePaths) -> NGINX.CoordinatorInputs:
    try:
        return NGINX.load_inputs(
            aggregate_path=paths.aggregate,
            bot_fi_manifest=paths.bot_fi_manifest,
            bot_fi_archive=paths.bot_fi_archive,
            webapp_fi_manifest=paths.webapp_fi_manifest,
            webapp_fi_archive=paths.webapp_fi_archive,
            known_hosts=paths.known_hosts,
            ssh_identity=paths.ssh_identity,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise NginxCutoverPhaseBridgeError(
            "release-bound Nginx inputs are invalid"
        ) from exc


def _nginx_binding(inputs: NGINX.CoordinatorInputs) -> dict[str, Any]:
    return {
        "operation_id": inputs.operation_id,
        "release_sha": inputs.release_sha,
        "release_tree_sha": inputs.release_tree_sha,
        "release_root": os.fspath(inputs.release_root),
        "aggregate_sha256": inputs.aggregate_sha256,
        "worker_path": os.fspath(inputs.worker_path),
        "worker_sha256": inputs.worker_sha256,
        "ssh_identity_sha256": inputs.ssh_identity_sha256,
        "role_manifest_sha256": {
            role: inputs.roles[role].manifest_sha256
            for role in ROLE_ORDER
        },
        "role_archive_sha256": {
            role: inputs.roles[role].manifest["archive"]["sha256"]
            for role in ROLE_ORDER
        },
        "generation_sha256": dict(
            inputs.aggregate["generation_sha256"]
        ),
    }


def _validate_request_bindings(
    request: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    plan: Mapping[str, Any],
    nginx_inputs: NGINX.CoordinatorInputs,
    approval_sha256: str,
    approval_policy_sha256: str,
    known_hosts_sha256: str,
) -> None:
    identity = {
        "campaign_id": manifest["campaign_id"],
        "operation_id": manifest["operation_id"],
        "release_sha": manifest["release_sha"],
        "release_tree_sha": manifest["release_tree_sha"],
        "controller_manifest_sha256": manifest_sha256,
        "plan_sha256": plan["plan_sha256"],
        "approval_sha256": approval_sha256,
        "approval_policy_sha256": approval_policy_sha256,
        "nginx_aggregate_sha256": nginx_inputs.aggregate_sha256,
        "known_hosts_sha256": known_hosts_sha256,
        "ssh_identity_sha256": nginx_inputs.ssh_identity_sha256,
    }
    if any(request.get(field) != value for field, value in identity.items()):
        raise NginxCutoverPhaseBridgeError(
            "bridge request identity or artifact binding differs"
        )
    if (
        request["approval_sha256"]
        != manifest["artifacts"]["cutover_approval_sha256"]
        or request["approval_policy_sha256"]
        != manifest["artifacts"]["human_approval_policy_sha256"]
        or request["nginx_role_manifest_sha256"]
        != {
            role: nginx_inputs.roles[role].manifest_sha256
            for role in ROLE_ORDER
        }
        or request["nginx_role_archive_sha256"]
        != {
            role: nginx_inputs.roles[role].manifest["archive"]["sha256"]
            for role in ROLE_ORDER
        }
    ):
        raise NginxCutoverPhaseBridgeError(
            "bridge request role or approval hashes differ"
        )
    expected_release_root = Path(
        CONTROLLER._operation_release_root(  # noqa: SLF001
            manifest["operation_id"],
            manifest["release_sha"],
        )
    )
    if (
        nginx_inputs.release_root != expected_release_root
        or nginx_inputs.worker_path
        != expected_release_root / NGINX.WORKER_RELATIVE_PATH
        or manifest["deployment"]["shadow_root"]
        != os.fspath(expected_release_root.parent.parent)
    ):
        raise NginxCutoverPhaseBridgeError(
            "Nginx worker or release root is not immutable-release bound"
        )
    generation_bindings = {
        "legacy-normal": "nginx_rollback_generation_sha256",
        "legacy-frozen": "nginx_freeze_generation_sha256",
        "shadow-readonly": "nginx_shadow_readonly_generation_sha256",
        "shadow-writable": "nginx_shadow_writable_generation_sha256",
    }
    if any(
        nginx_inputs.aggregate["generation_sha256"][state]
        != manifest["artifacts"][artifact]
        for state, artifact in generation_bindings.items()
    ):
        raise NginxCutoverPhaseBridgeError(
            "Nginx generation hashes differ from the cutover manifest"
        )
    constraints = request["constraints"]
    if (
        not isinstance(constraints, dict)
        or set(constraints) != CONSTRAINT_FIELDS
        or any(value is not False for value in constraints.values())
    ):
        raise NginxCutoverPhaseBridgeError(
            "caller asserted a forbidden bridge capability"
        )


def load_bridge_request(path: Path) -> BridgeContext:
    if os.geteuid() != 0 or os.getegid() != 0:
        raise NginxCutoverPhaseBridgeError(
            "Nginx cutover phase bridge requires root:root"
        )
    request_path = _absolute_path(
        os.fspath(path),
        label="bridge request",
    )
    request, _request_sha256 = _read_request(request_path)
    manifest_path = _absolute_path(
        request["controller_manifest_path"],
        label="controller manifest",
    )
    try:
        manifest, manifest_sha256 = CONTROLLER.read_root_only_manifest(
            manifest_path
        )
        plan = CONTROLLER.render_plan(
            manifest,
            manifest_sha256=manifest_sha256,
            manifest_path=manifest_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise NginxCutoverPhaseBridgeError(
            "production cutover manifest or plan is invalid"
        ) from exc
    approval_path = _absolute_path(
        request["approval_path"],
        label="approval",
    )
    approval_policy_path = _absolute_path(
        request["approval_policy_path"],
        label="approval policy",
    )
    approval_sha256 = _sha256(
        _read_private_bytes(
            approval_path,
            label="production cutover approval",
            maximum=16 * 1024 * 1024,
        )
    )
    approval_policy_sha256 = _sha256(
        _read_private_bytes(
            approval_policy_path,
            label="production approval policy",
            maximum=4 * 1024 * 1024,
        )
    )
    paths = _nginx_paths(request["nginx_paths"])
    inputs = _load_nginx_inputs(paths)
    known_hosts_sha256 = _sha256(
        _read_private_bytes(
            paths.known_hosts,
            label="pinned SSH known-hosts",
            maximum=NGINX.MAX_KEY_BYTES,
        )
    )
    _validate_request_bindings(
        request,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        nginx_inputs=inputs,
        approval_sha256=approval_sha256,
        approval_policy_sha256=approval_policy_sha256,
        known_hosts_sha256=known_hosts_sha256,
    )
    prior = request["prior_phase_evidence"]
    if (
        not isinstance(prior, dict)
        or set(prior) != set(INITIAL_PRIOR_PHASES)
    ):
        raise NginxCutoverPhaseBridgeError(
            "initial prior phase evidence mapping is not exact"
        )
    prior_paths: dict[str, Path] = {}
    for phase in INITIAL_PRIOR_PHASES:
        row = prior[phase]
        if not isinstance(row, dict) or set(row) != PRIOR_ROW_FIELDS:
            raise NginxCutoverPhaseBridgeError(
                f"prior phase {phase} fields are not exact"
            )
        path_value = _absolute_path(
            row["path"],
            label=f"prior phase {phase}",
        )
        expected = _nonzero_sha256(
            row["sha256"],
            label=f"prior phase {phase}",
        )
        try:
            document, observed = VERIFY.read_root_only_evidence(path_value)
        except VERIFY.PhaseEvidenceError as exc:
            raise NginxCutoverPhaseBridgeError(
                f"prior phase {phase} evidence is unsafe"
            ) from exc
        if (
            observed != expected
            or document.get("phase") != phase
            or document.get("campaign_id") != manifest["campaign_id"]
            or document.get("operation_id") != manifest["operation_id"]
            or document.get("release_sha") != manifest["release_sha"]
            or document.get("manifest_sha256") != manifest_sha256
            or document.get("plan_sha256") != plan["plan_sha256"]
            or document.get("status") != "passed"
            or document.get("business_write_observed") is not False
        ):
            raise NginxCutoverPhaseBridgeError(
                f"prior phase {phase} evidence binding differs"
            )
        prior_paths[phase] = path_value
    output_root = (
        _absolute_path(
            manifest["deployment"]["controller_evidence_root"],
            label="controller evidence root",
        )
        / "nginx-cutover-phases"
    )
    return BridgeContext(
        request_path=request_path,
        request=dict(request),
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        plan=plan,
        approval_path=approval_path,
        approval_policy_path=approval_policy_path,
        nginx_paths=paths,
        nginx_inputs=inputs,
        prior_paths=prior_paths,
        output_root=output_root,
    )


def confirmation_phrase(context: BridgeContext) -> str:
    return (
        "APPLY-PRODUCTION-SHADOW-NGINX-CUTOVER-PHASES:"
        f"{context.manifest['operation_id']}:"
        f"{context.manifest['release_sha']}"
    )


def _journal_bindings(context: BridgeContext) -> dict[str, str]:
    return {
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
    }


def _verify_authorization(context: BridgeContext) -> None:
    try:
        CONTROLLER._verify_runtime_authorization(  # noqa: SLF001
            context.manifest,
            approval_path=context.approval_path,
            approval_policy_path=context.approval_policy_path,
        )
    except CONTROLLER.CutoverContractError as exc:
        raise NginxCutoverPhaseBridgeError(
            "production cutover authorization is invalid or expired"
        ) from exc


def _ensure_private_directory(path: Path, *, create: bool) -> None:
    try:
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise NginxCutoverPhaseBridgeError(
            "bridge evidence directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise NginxCutoverPhaseBridgeError(
            "bridge evidence directory must be root-only mode 0700"
        )


def _prepare_output_root(context: BridgeContext) -> None:
    parent = context.output_root.parent
    _ensure_private_directory(parent, create=False)
    _ensure_private_directory(context.output_root, create=True)


def _prepare_phase_root(context: BridgeContext, phase: str) -> Path:
    phases_root = context.output_root / "phases"
    _ensure_private_directory(phases_root, create=True)
    phase_root = phases_root / phase
    _ensure_private_directory(phase_root, create=True)
    return phase_root


def _persist_document(
    directory: Path,
    *,
    prefix: str,
    document: Mapping[str, Any],
) -> tuple[Path, str]:
    if (
        not prefix
        or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-"
               for character in prefix)
    ):
        raise NginxCutoverPhaseBridgeError(
            "bridge evidence prefix is invalid"
        )
    _ensure_private_directory(directory.parent, create=False)
    _ensure_private_directory(directory, create=True)
    payload = canonical_json_bytes(dict(document))
    digest = _sha256(payload)
    path = directory / f"{prefix}-{digest}.json"
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="Nginx cutover bridge evidence",
            mode=0o600,
            max_size=MAX_EVIDENCE_BYTES,
        )
    except SecureFileError:
        existing = _read_private_bytes(
            path,
            label="existing Nginx cutover bridge evidence",
            maximum=MAX_EVIDENCE_BYTES,
        )
        if existing != payload:
            raise NginxCutoverPhaseBridgeError(
                "existing bridge evidence differs"
            )
    observed = _read_private_bytes(
        path,
        label="Nginx cutover bridge evidence readback",
        maximum=MAX_EVIDENCE_BYTES,
    )
    if observed != payload:
        raise NginxCutoverPhaseBridgeError(
            "bridge evidence readback differs"
        )
    return path, digest


_SIGNAL_GUARD_DEPTH = 0
_SIGNAL_SEEN = False
_SIGNAL_DEFER_DEPTH = 0
_DEFERRED_SIGNAL: str | None = None


@contextmanager
def _signal_reconciliation_scope() -> Iterator[None]:
    global _SIGNAL_DEFER_DEPTH, _DEFERRED_SIGNAL
    entry_exception = sys.exception()
    body_failed = False
    _SIGNAL_DEFER_DEPTH += 1
    try:
        yield
    except BaseException:
        body_failed = True
        raise
    finally:
        _SIGNAL_DEFER_DEPTH -= 1
        if (
            _SIGNAL_DEFER_DEPTH == 0
            and _DEFERRED_SIGNAL is not None
            and entry_exception is None
            and not body_failed
        ):
            reason = _DEFERRED_SIGNAL
            _DEFERRED_SIGNAL = None
            raise NginxCutoverPhaseBridgeCancellation(reason)


@contextmanager
def _signal_cancellation_guard() -> Iterator[None]:
    global _SIGNAL_GUARD_DEPTH, _SIGNAL_SEEN, _DEFERRED_SIGNAL
    if threading.current_thread() is not threading.main_thread():
        raise NginxCutoverPhaseBridgeError(
            "mutating Nginx bridge must run in the main thread"
        )
    if _SIGNAL_GUARD_DEPTH:
        _SIGNAL_GUARD_DEPTH += 1
        try:
            yield
        finally:
            _SIGNAL_GUARD_DEPTH -= 1
        return
    handled = (
        signal.SIGHUP,
        signal.SIGINT,
        signal.SIGTERM,
        signal.SIGUSR1,
    )
    previous = {signum: signal.getsignal(signum) for signum in handled}

    def cancel(signum: int, _frame: Any) -> None:
        global _SIGNAL_SEEN, _DEFERRED_SIGNAL
        if _SIGNAL_SEEN:
            return
        _SIGNAL_SEEN = True
        reason = f"Nginx bridge received {signal.Signals(signum).name}"
        if _SIGNAL_DEFER_DEPTH:
            _DEFERRED_SIGNAL = reason
            return
        raise NginxCutoverPhaseBridgeCancellation(reason)

    _SIGNAL_GUARD_DEPTH = 1
    _SIGNAL_SEEN = False
    _DEFERRED_SIGNAL = None
    installed: list[signal.Signals] = []
    try:
        for signum in handled:
            signal.signal(signum, cancel)
            installed.append(signum)
        yield
        if _DEFERRED_SIGNAL is not None:
            raise NginxCutoverPhaseBridgeCancellation(_DEFERRED_SIGNAL)
    finally:
        original = sys.exception()
        restoration_errors: list[BaseException] = []
        try:
            for signum in reversed(installed):
                try:
                    signal.signal(signum, previous[signum])
                except BaseException as exc:
                    restoration_errors.append(exc)
        finally:
            _SIGNAL_GUARD_DEPTH = 0
            _SIGNAL_SEEN = False
            _DEFERRED_SIGNAL = None
        if restoration_errors:
            if original is not None:
                for error in restoration_errors:
                    try:
                        original.add_note(
                            "signal handler restoration also failed: "
                            f"{type(error).__name__}: {error}"
                        )
                    except (AttributeError, TypeError):
                        pass
            else:
                raise restoration_errors[0]


def _assert_no_local_pipe_writer(
    control_fd: int,
    metadata: os.stat_result,
) -> None:
    try:
        descriptor_names = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise NginxCutoverPhaseBridgeError(
            "cannot prove controller liveness pipe ownership"
        ) from exc
    for name in descriptor_names:
        try:
            descriptor = int(name)
        except ValueError:
            continue
        if descriptor == control_fd:
            continue
        try:
            candidate = os.fstat(descriptor)
            if (
                candidate.st_dev != metadata.st_dev
                or candidate.st_ino != metadata.st_ino
            ):
                continue
            flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        except OSError as exc:
            # Descriptors can disappear between /proc enumeration and fstat.
            if exc.errno in {errno.EBADF, errno.ENOENT}:
                continue
            raise NginxCutoverPhaseBridgeError(
                "cannot prove controller liveness pipe ownership"
            ) from exc
        if flags & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}:
            raise NginxCutoverPhaseBridgeError(
                "controller process retains a liveness pipe writer"
            )


class ControllerLiveness:
    """Convert anonymous-pipe EOF/data into one SIGUSR1 cancellation."""

    def __init__(self, control_fd: int):
        if type(control_fd) is not int or control_fd < 0:
            raise NginxCutoverPhaseBridgeError(
                "apply requires a controller liveness descriptor"
            )
        try:
            metadata = os.fstat(control_fd)
            flags = fcntl.fcntl(control_fd, fcntl.F_GETFL)
            target = os.readlink(f"/proc/self/fd/{control_fd}")
        except OSError as exc:
            raise NginxCutoverPhaseBridgeError(
                "controller liveness pipe is unavailable"
            ) from exc
        if (
            not stat.S_ISFIFO(metadata.st_mode)
            or flags & os.O_ACCMODE != os.O_RDONLY
            or target != f"pipe:[{metadata.st_ino}]"
        ):
            raise NginxCutoverPhaseBridgeError(
                "controller liveness descriptor is not an anonymous read pipe"
            )
        _assert_no_local_pipe_writer(control_fd, metadata)
        try:
            descriptor = os.dup(control_fd)
        except OSError as exc:
            raise NginxCutoverPhaseBridgeError(
                "cannot duplicate controller liveness pipe"
            ) from exc
        try:
            os.set_inheritable(descriptor, False)
            os.set_blocking(descriptor, False)
        except BaseException as original:
            try:
                os.close(descriptor)
            except BaseException as cleanup_error:
                try:
                    original.add_note(
                        "controller liveness descriptor cleanup also "
                        f"failed: {type(cleanup_error).__name__}: "
                        f"{cleanup_error}"
                    )
                except (AttributeError, TypeError):
                    pass
            raise
        self._fd = descriptor
        self._closed = False
        self._stopping = threading.Event()
        self._cancelled = threading.Event()
        self._reason: str | None = None
        self._wake_sent = False
        self._thread = threading.Thread(
            target=self._watch,
            name="nginx-cutover-controller-liveness",
            daemon=True,
        )

    def _synchronous_probe(self) -> None:
        try:
            payload = os.read(self._fd, 1)
        except BlockingIOError:
            return
        except OSError as exc:
            self._reason = "controller liveness pipe failed"
            self._cancelled.set()
            raise NginxCutoverPhaseBridgeCancellation(
                self._reason
            ) from exc
        self._reason = (
            "controller liveness pipe carried forbidden data"
            if payload
            else "controller liveness pipe reached EOF"
        )
        self._cancelled.set()
        raise NginxCutoverPhaseBridgeCancellation(self._reason)

    def _cancel(self, reason: str) -> None:
        if self._wake_sent or self._stopping.is_set():
            return
        self._wake_sent = True
        self._reason = reason
        self._cancelled.set()
        os.kill(os.getpid(), signal.SIGUSR1)

    def _watch(self) -> None:
        try:
            while not self._stopping.is_set():
                readable, _, _ = select.select([self._fd], [], [], 0.05)
                if not readable:
                    continue
                try:
                    payload = os.read(self._fd, 1)
                except BlockingIOError:
                    continue
                self._cancel(
                    "controller liveness pipe carried forbidden data"
                    if payload
                    else "controller liveness pipe reached EOF"
                )
                return
        except (OSError, ValueError):
            if not self._stopping.is_set():
                self._cancel("controller liveness pipe failed")

    def __enter__(self) -> "ControllerLiveness":
        try:
            self._synchronous_probe()
            self._thread.start()
            self.check()
            return self
        except BaseException as original:
            self._shutdown(original=original)
            raise

    def check(self) -> None:
        if self._cancelled.is_set():
            raise NginxCutoverPhaseBridgeCancellation(
                self._reason or "controller liveness was lost"
            )

    def _shutdown(self, *, original: BaseException | None) -> None:
        errors: list[BaseException] = []
        self._stopping.set()
        if not self._closed:
            try:
                os.close(self._fd)
            except BaseException as exc:
                errors.append(exc)
            finally:
                self._closed = True
        if self._thread.ident is not None:
            try:
                self._thread.join(timeout=1.0)
            except BaseException as exc:
                errors.append(exc)
            try:
                alive = self._thread.is_alive()
            except BaseException as exc:
                errors.append(exc)
            else:
                if alive:
                    errors.append(
                        NginxCutoverPhaseBridgeError(
                            "controller liveness watcher did not stop"
                        )
                    )
        if errors:
            if original is not None:
                for error in errors:
                    try:
                        original.add_note(
                            "controller liveness cleanup also failed: "
                            f"{type(error).__name__}: {error}"
                        )
                    except (AttributeError, TypeError):
                        pass
            else:
                raise errors[0]

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self._shutdown(
            original=(
                _value
                if isinstance(_value, BaseException)
                else sys.exception()
            )
        )


def _revalidate_nginx_inputs(context: BridgeContext) -> None:
    refreshed = _load_nginx_inputs(context.nginx_paths)
    if _nginx_binding(refreshed) != _nginx_binding(context.nginx_inputs):
        raise NginxCutoverPhaseBridgeError(
            "release-bound Nginx inputs changed during orchestration"
        )


def _nginx_call(
    context: BridgeContext,
    *,
    action: str,
    target_state: str | None,
    executor: NginxExecutor,
) -> dict[str, Any]:
    _revalidate_nginx_inputs(context)
    confirmation = NGINX.confirmation_phrase(
        operation_id=context.manifest["operation_id"],
        release_sha=context.manifest["release_sha"],
        action=action,
        target_state=target_state,
    )
    result = executor(
        aggregate_path=context.nginx_paths.aggregate,
        bot_fi_manifest=context.nginx_paths.bot_fi_manifest,
        bot_fi_archive=context.nginx_paths.bot_fi_archive,
        webapp_fi_manifest=context.nginx_paths.webapp_fi_manifest,
        webapp_fi_archive=context.nginx_paths.webapp_fi_archive,
        action=action,
        target_state=target_state,
        apply=True,
        confirm=confirmation,
        known_hosts=context.nginx_paths.known_hosts,
        ssh_identity=context.nginx_paths.ssh_identity,
    )
    _revalidate_nginx_inputs(context)
    if not isinstance(result, dict):
        raise NginxCutoverPhaseBridgeError(
            "Nginx coordinator result is not an object"
        )
    return result


def _load_result_receipt(
    context: BridgeContext,
    result: Mapping[str, Any],
    *,
    action: str,
    target_state: str | None,
    expected_state: str,
) -> tuple[dict[str, Any], str, Path]:
    path_value = result.get("state_receipt_path")
    digest_value = result.get("state_receipt_sha256")
    if not isinstance(path_value, str):
        raise NginxCutoverPhaseBridgeError(
            "Nginx coordinator omitted its state receipt"
        )
    path = _absolute_path(path_value, label=f"Nginx {action} receipt")
    digest = _nonzero_sha256(
        digest_value,
        label=f"Nginx {action} receipt",
    )
    expected_path = (
        context.nginx_inputs.receipts_root
        / f"{expected_state}-{digest}.json"
    )
    if path != expected_path:
        raise NginxCutoverPhaseBridgeError(
            "Nginx state receipt path is not canonical"
        )
    try:
        receipt, observed = NGINX.load_state_receipt(
            path,
            expected_state,
            context.manifest["operation_id"],
            context.manifest["release_sha"],
            context.manifest["release_tree_sha"],
            context.nginx_inputs.aggregate_sha256,
        )
    except NGINX.NginxCoordinatorError as exc:
        raise NginxCutoverPhaseBridgeError(
            f"Nginx {action} state receipt is invalid"
        ) from exc
    expected_active_mutation = (
        action == "activate"
        and result.get("status") in {"activated", "compensated-failed"}
    )
    if (
        result.get("schema") != NGINX.RESULT_SCHEMA
        or observed != digest
        or receipt["source_action"] != action
        or receipt["requested_target_state"] != target_state
        or receipt["coordinator_status"] != result.get("status")
        or result.get("action") != action
        or result.get("target_state") != target_state
        or result.get("operation_id") != context.manifest["operation_id"]
        or result.get("release_sha") != context.manifest["release_sha"]
        or result.get("release_tree_sha")
        != context.manifest["release_tree_sha"]
        or result.get("aggregate_sha256")
        != context.nginx_inputs.aggregate_sha256
        or result.get("active_configuration_mutated")
        is not expected_active_mutation
        or result.get("current_mutated") is not False
        or result.get("container_mutated") is not False
        or result.get("volume_mutated") is not False
        or result.get("data_mutated") is not False
    ):
        raise NginxCutoverPhaseBridgeError(
            f"Nginx {action} result or receipt binding differs"
        )
    return receipt, digest, path


def _validate_receipt_pair(
    action_receipt: Mapping[str, Any],
    delayed_receipt: Mapping[str, Any],
    *,
    expected_state: str,
) -> None:
    if (
        action_receipt["state"] != expected_state
        or delayed_receipt["state"] != expected_state
        or delayed_receipt["source_action"] != "readback"
        or delayed_receipt["requested_target_state"] is not None
        or delayed_receipt["coordinator_status"] != "read-back"
        or delayed_receipt["evidence_count"]
        <= action_receipt["evidence_count"]
        or delayed_receipt["evidence_tail_sha256"]
        == action_receipt["evidence_tail_sha256"]
        or delayed_receipt["role_bindings"]
        != action_receipt["role_bindings"]
        or delayed_receipt["global_generation_sha256"]
        != action_receipt["global_generation_sha256"]
        or delayed_receipt["readbacks"] != action_receipt["readbacks"]
        or delayed_receipt["external_readback"]
        != action_receipt["external_readback"]
    ):
        raise NginxCutoverPhaseBridgeError(
            "delayed external readback does not close the action receipt"
        )


def _invoke_phase_action(
    context: BridgeContext,
    *,
    phase: str,
    executor: NginxExecutor,
) -> tuple[
    dict[str, Any],
    str,
    Path,
    dict[str, Any],
    str,
    Path,
    bool,
]:
    action, target_state, expected_state = PHASE_ACTIONS[phase]
    result = _nginx_call(
        context,
        action=action,
        target_state=target_state,
        executor=executor,
    )
    accepted = {
        "install": {"installed", "already-installed"},
        "test": {"tested"},
        "activate": {"activated", "already-active"},
    }[action]
    if action == "activate" and result.get("status") == "compensated-failed":
        (
            compensated_receipt,
            _compensated_digest,
            _compensated_path,
        ) = _load_result_receipt(
            context,
            result,
            action="activate",
            target_state="legacy-frozen",
            expected_state="legacy-normal",
        )
        readback = _nginx_call(
            context,
            action="readback",
            target_state=None,
            executor=executor,
        )
        receipt, _digest, _path = _load_result_receipt(
            context,
            readback,
            action="readback",
            target_state=None,
            expected_state="legacy-normal",
        )
        _validate_receipt_pair(
            compensated_receipt,
            receipt,
            expected_state="legacy-normal",
        )
        raise NginxCutoverPhaseBridgeError(
            "freeze activation was compensated and remains resumable"
        )
    if result.get("status") not in accepted:
        raise NginxCutoverPhaseBridgeError(
            f"Nginx {action} did not reach an accepted closure"
        )
    action_receipt, action_digest, action_path = _load_result_receipt(
        context,
        result,
        action=action,
        target_state=target_state,
        expected_state=expected_state,
    )
    delayed = _nginx_call(
        context,
        action="readback",
        target_state=None,
        executor=executor,
    )
    delayed_receipt, delayed_digest, delayed_path = _load_result_receipt(
        context,
        delayed,
        action="readback",
        target_state=None,
        expected_state=expected_state,
    )
    _validate_receipt_pair(
        action_receipt,
        delayed_receipt,
        expected_state=expected_state,
    )
    return (
        action_receipt,
        action_digest,
        action_path,
        delayed_receipt,
        delayed_digest,
        delayed_path,
        result["active_configuration_mutated"],
    )


def _load_prior_records(
    context: BridgeContext,
    *,
    phase: str,
    journal_state: Mapping[str, Any],
    evidence_paths: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    expected = CONTROLLER.PHASES[: CONTROLLER.PHASES.index(phase)]
    if set(evidence_paths) != set(expected):
        raise NginxCutoverPhaseBridgeError(
            "prior phase evidence path set is not exact"
        )
    records: dict[str, dict[str, Any]] = {}
    for prior_phase in expected:
        path = evidence_paths[prior_phase]
        try:
            document, digest = VERIFY.read_root_only_evidence(path)
        except VERIFY.PhaseEvidenceError as exc:
            raise NginxCutoverPhaseBridgeError(
                f"prior phase {prior_phase} evidence is unsafe"
            ) from exc
        if (
            digest
            != journal_state["phase_evidence_sha256"][prior_phase]
            or document.get("phase") != prior_phase
            or document.get("campaign_id")
            != context.manifest["campaign_id"]
            or document.get("operation_id")
            != context.manifest["operation_id"]
            or document.get("release_sha")
            != context.manifest["release_sha"]
            or document.get("manifest_sha256")
            != context.manifest_sha256
            or document.get("plan_sha256")
            != context.plan["plan_sha256"]
            or document.get("status") != "passed"
            or document.get("business_write_observed") is not False
        ):
            raise NginxCutoverPhaseBridgeError(
                f"prior phase {prior_phase} evidence differs from journal"
            )
        records[prior_phase] = {
            "document": document,
            "file_sha256": digest,
        }
    return records


def _claim_values(
    context: BridgeContext,
    *,
    phase: str,
    action_receipt: Mapping[str, Any],
    delayed_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    aggregate = context.nginx_inputs.aggregate["generation_sha256"]
    freeze = aggregate["legacy-frozen"]
    if (
        freeze
        != context.manifest["artifacts"][
            "nginx_freeze_generation_sha256"
        ]
    ):
        raise NginxCutoverPhaseBridgeError(
            "freeze generation differs from manifest"
        )
    vhost_count = len(delayed_receipt["vhost_generation_sha256"])
    if vhost_count != 3:
        raise NginxCutoverPhaseBridgeError(
            "Nginx receipt does not cover exactly three vhosts"
        )
    if phase == "freeze_generation_install":
        values = {
            "manifest_freeze_generation_sha256": freeze,
            "staged_generation_set_sha256": freeze,
            "previous_generation_set_sha256": aggregate[
                "legacy-normal"
            ],
            "active_generation_unchanged": (
                action_receipt["state"] == "legacy-normal"
                and delayed_receipt["state"] == "legacy-normal"
            ),
            "staged_vhost_count": vhost_count,
        }
    elif phase == "freeze_generation_test":
        values = {
            "manifest_freeze_generation_sha256": freeze,
            "nginx_test_failure_count": (
                0
                if action_receipt["coordinator_status"] == "tested"
                else 1
            ),
            "tested_vhost_count": vhost_count,
            "active_generation_unchanged": (
                action_receipt["state"] == "legacy-normal"
                and delayed_receipt["state"] == "legacy-normal"
            ),
        }
    elif phase == "freeze_generation_activate":
        blocked = delayed_receipt["external_readback"]
        blocked_count = sum(
            probes.get("post") == 503
            and probes.get("websocket") == 503
            for probes in blocked["vhosts"].values()
        )
        per_host = all(
            delayed_receipt["readbacks"][role]["state"]
            == "legacy-frozen"
            and delayed_receipt["readbacks"][role]["generation_sha256"]
            == action_receipt["readbacks"][role]["generation_sha256"]
            for role in ROLE_ORDER
        )
        values = {
            "manifest_freeze_generation_sha256": freeze,
            "write_blocked_vhost_count": blocked_count,
            "per_host_generation_readback_verified": per_host,
            "compensating_restore_ready": (
                aggregate["legacy-normal"] != freeze
                and action_receipt["source_action"] == "activate"
                and delayed_receipt["source_action"] == "readback"
            ),
        }
    else:
        raise NginxCutoverPhaseBridgeError(
            "bridge phase is outside the freeze corridor"
        )
    if set(values) != set(VERIFY.PHASE_CLAIM_RULES[phase]):
        raise NginxCutoverPhaseBridgeError(
            "derived Nginx claim fields are not exact"
        )
    for name, rule in VERIFY.PHASE_CLAIM_RULES[phase].items():
        try:
            VERIFY._validate_claim(  # noqa: SLF001
                name,
                {"value": values[name], "source_sha256": "1" * 64},
                rule,
            )
        except VERIFY.PhaseEvidenceError as exc:
            raise NginxCutoverPhaseBridgeError(
                f"derived Nginx claim {name} is invalid"
            ) from exc
    return values


def _prepare_phase_evidence(
    context: BridgeContext,
    *,
    phase: str,
    action_receipt: Mapping[str, Any],
    action_receipt_sha256: str,
    action_receipt_path: Path,
    delayed_receipt: Mapping[str, Any],
    delayed_receipt_sha256: str,
    delayed_receipt_path: Path,
    journal_state: Mapping[str, Any],
    evidence_paths: Mapping[str, Path],
    now: datetime,
) -> tuple[
    Path,
    dict[str, Path],
    dict[str, Path],
    dict[str, Any],
]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise NginxCutoverPhaseBridgeError(
            "phase observation time must include a timezone"
        )
    spec = next(
        item for item in CONTROLLER.PHASE_SPECS if item.phase == phase
    )
    observed_at = now.astimezone(timezone.utc).isoformat()
    root = _prepare_phase_root(context, phase)
    role_paths: dict[str, Path] = {}
    role_hashes: dict[str, str] = {}
    request_hashes: dict[str, str] = {}
    for role in ROLE_ORDER:
        request_source = {
            "schema": NORMALIZATION_SCHEMA,
            "phase": phase,
            "operation": spec.operation,
            "role": role,
            "action_receipt_sha256": action_receipt_sha256,
            "delayed_readback_receipt_sha256": delayed_receipt_sha256,
            "role_binding": delayed_receipt["role_bindings"][role],
            "readback": delayed_receipt["readbacks"][role],
        }
        request_sha256 = _sha256(canonical_json_bytes(request_source))
        validation = {
            "schema": ROLE_VALIDATION_SCHEMA,
            "status": "validated-request",
            "request_sha256": request_sha256,
            "operation": spec.operation,
            "role": role,
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "app_release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "approval_sha256": context.request["approval_sha256"],
            "expected_host": context.manifest["topology"][role]["host"],
            "observed_host": delayed_receipt["readbacks"][role][
                "expected_host"
            ],
            "required_journal_status": (
                CONTROLLER.PRECOMMIT_JOURNAL_STATUS
            ),
            "business_write_policy": "forbid",
            "agent_artifact_sha256": context.manifest["artifacts"][
                "host_agent_sha256"
            ],
            "host_agent_contract_sha256": context.manifest["artifacts"][
                "host_agent_contract_sha256"
            ],
            "transport": context.manifest["topology"][role]["transport"],
            "observed_at": observed_at,
            "host_identity_observed": True,
            "execution_supported": False,
            "production_contacted": False,
        }
        if set(validation) != VERIFY.HOST_AGENT_VALIDATION_FIELDS:
            raise NginxCutoverPhaseBridgeError(
                "normalized role validation fields differ"
            )
        path, digest = _persist_document(
            root / "role-validation",
            prefix=f"role-validation-{role.replace('_', '-')}",
            document=validation,
        )
        role_paths[role] = path
        role_hashes[role] = digest
        request_hashes[role] = request_sha256

    values = _claim_values(
        context,
        phase=phase,
        action_receipt=action_receipt,
        delayed_receipt=delayed_receipt,
    )
    claim_paths: dict[str, Path] = {}
    claim_hashes: dict[str, str] = {}
    for claim in VERIFY.PHASE_CLAIM_RULES[phase]:
        source = {
            "schema": CLAIM_SOURCE_SCHEMA,
            "campaign_id": context.manifest["campaign_id"],
            "operation_id": context.manifest["operation_id"],
            "release_sha": context.manifest["release_sha"],
            "manifest_sha256": context.manifest_sha256,
            "phase": phase,
            "operation": spec.operation,
            "claim": claim,
            "value": values[claim],
            "observed_at": observed_at,
            "status": "observed",
        }
        if set(source) != VERIFY.CLAIM_SOURCE_FIELDS:
            raise NginxCutoverPhaseBridgeError(
                "normalized claim source fields differ"
            )
        path, digest = _persist_document(
            root / "claim-sources",
            prefix=f"claim-{claim.replace('_', '-')}",
            document=source,
        )
        claim_paths[claim] = path
        claim_hashes[claim] = digest

    prior_records = _load_prior_records(
        context,
        phase=phase,
        journal_state=journal_state,
        evidence_paths=evidence_paths,
    )
    prior_rows = [
        {
            "phase": prior,
            "evidence_sha256": journal_state[
                "phase_evidence_sha256"
            ][prior],
        }
        for prior in CONTROLLER.PHASES[: CONTROLLER.PHASES.index(phase)]
    ]
    try:
        prior_claim_rows = VERIFY._derive_prior_claim_rows(  # noqa: SLF001
            phase=phase,
            prior_digests={
                row["phase"]: row["evidence_sha256"]
                for row in prior_rows
            },
            prior_records=prior_records,
            campaign_id=context.manifest["campaign_id"],
            operation_id=context.manifest["operation_id"],
            release_sha=context.manifest["release_sha"],
            legacy_release_sha=context.manifest["legacy_release_sha"],
            manifest_sha256=context.manifest_sha256,
            plan_sha256=context.plan["plan_sha256"],
        )
    except VERIFY.PhaseEvidenceError as exc:
        raise NginxCutoverPhaseBridgeError(
            "prior phase claim binding is invalid"
        ) from exc
    dynamic_values = {
        name: value
        for name, value in values.items()
        if VERIFY.PHASE_CLAIM_RULES[phase][name].kind != "exact"
    }
    phase_input = {
        "manifest_sha256": context.manifest_sha256,
        "manifest_artifacts_sha256": _sha256(
            canonical_json_bytes(context.manifest["artifacts"])
        ),
        "prior_phase_evidence": prior_rows,
        "prior_claim_bindings": prior_claim_rows,
        "dynamic_claim_values": dynamic_values,
        "claim_source_sha256": {
            name: claim_hashes[name] for name in sorted(claim_hashes)
        },
        "role_request_sha256": {
            role: request_hashes[role] for role in ROLE_ORDER
        },
        "role_source_artifact_sha256": {
            role: role_hashes[role] for role in ROLE_ORDER
        },
        "role_observed_at": {
            role: observed_at for role in ROLE_ORDER
        },
    }
    evidence = {
        "schema": VERIFY.EVIDENCE_SCHEMA,
        "phase_evidence_schema_sha256": context.manifest["artifacts"][
            "phase_evidence_schema_sha256"
        ],
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "legacy_release_sha": context.manifest["legacy_release_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "approval_sha256": context.request["approval_sha256"],
        "manifest_artifact_bindings": context.manifest["artifacts"],
        "phase": phase,
        "operation": spec.operation,
        "journal_status": CONTROLLER.PRECOMMIT_JOURNAL_STATUS,
        "status": "passed",
        "captured_at": observed_at,
        "business_write_observed": False,
        "prior_phase_evidence": prior_rows,
        "prior_phase_evidence_closure_sha256": _sha256(
            canonical_json_bytes(prior_rows)
        ),
        "prior_claim_bindings": prior_claim_rows,
        "phase_input_closure_sha256": _sha256(
            canonical_json_bytes(phase_input)
        ),
        "role_attestations": [
            {
                "role": role,
                "expected_host": context.manifest["topology"][role]["host"],
                "operation": spec.operation,
                "request_sha256": request_hashes[role],
                "app_release_sha": context.manifest["release_sha"],
                "agent_artifact_sha256": context.manifest["artifacts"][
                    "host_agent_sha256"
                ],
                "host_identity_observed": True,
                "observed_at": observed_at,
                "status": "verified",
                "transport": context.manifest["topology"][role][
                    "transport"
                ],
                "source_artifact_sha256": role_hashes[role],
            }
            for role in ROLE_ORDER
        ],
        "claims": {
            claim: {
                "value": values[claim],
                "source_sha256": claim_hashes[claim],
            }
            for claim in VERIFY.PHASE_CLAIM_RULES[phase]
        },
    }
    if set(evidence) != VERIFY.EVIDENCE_FIELDS:
        raise NginxCutoverPhaseBridgeError(
            "phase evidence fields are not exact"
        )
    evidence_path, evidence_sha256 = _persist_document(
        root / "evidence",
        prefix=phase.replace("_", "-"),
        document=evidence,
    )
    normalization = {
        "schema": NORMALIZATION_SCHEMA,
        "status": "normalized-from-coordinator-receipts",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "phase": phase,
        "operation": spec.operation,
        "action_receipt_path": os.fspath(action_receipt_path),
        "action_receipt_sha256": action_receipt_sha256,
        "delayed_readback_receipt_path": os.fspath(
            delayed_receipt_path
        ),
        "delayed_readback_receipt_sha256": delayed_receipt_sha256,
        "role_validation_sha256": role_hashes,
        "claim_source_sha256": claim_hashes,
        "phase_evidence_path": os.fspath(evidence_path),
        "phase_evidence_sha256": evidence_sha256,
        "caller_claim_sources_accepted": False,
        "caller_readback_assertions_accepted": False,
        "business_write_observed": False,
        "observed_at": observed_at,
    }
    normalization_path, normalization_sha256 = _persist_document(
        root / "normalization",
        prefix="normalization",
        document=normalization,
    )
    return evidence_path, role_paths, claim_paths, {
        "phase_evidence_sha256": evidence_sha256,
        "normalization_path": os.fspath(normalization_path),
        "normalization_sha256": normalization_sha256,
        "action_receipt_sha256": action_receipt_sha256,
        "delayed_readback_receipt_sha256": delayed_receipt_sha256,
    }


def _completed_evidence_path(
    context: BridgeContext,
    *,
    phase: str,
    digest: str,
) -> Path:
    path = (
        context.output_root
        / "phases"
        / phase
        / "evidence"
        / f"{phase.replace('_', '-')}-{digest}.json"
    )
    try:
        document, observed = VERIFY.read_root_only_evidence(path)
    except VERIFY.PhaseEvidenceError as exc:
        raise NginxCutoverPhaseBridgeError(
            f"completed {phase} evidence is unavailable"
        ) from exc
    if (
        observed != digest
        or document.get("phase") != phase
        or document.get("campaign_id") != context.manifest["campaign_id"]
        or document.get("operation_id")
        != context.manifest["operation_id"]
        or document.get("release_sha") != context.manifest["release_sha"]
        or document.get("manifest_sha256")
        != context.manifest_sha256
        or document.get("plan_sha256") != context.plan["plan_sha256"]
        or document.get("approval_sha256")
        != context.request["approval_sha256"]
        or document.get("business_write_observed") is not False
    ):
        raise NginxCutoverPhaseBridgeError(
            f"completed {phase} evidence binding differs"
        )
    return path


def _assert_corridor(state: Mapping[str, Any]) -> None:
    allowed = {
        tuple(CONTROLLER.PHASES[: len(INITIAL_PRIOR_PHASES) + count])
        for count in range(len(PHASES) + 1)
    }
    if (
        tuple(state["completed_phases"]) not in allowed
        or state.get("started_phase") not in {None, *PHASES}
        or state.get("first_business_write_allowed") is not False
        or state.get("status")
        not in {"active", "phase_started"}
    ):
        raise NginxCutoverPhaseBridgeError(
            "cutover journal is outside the three-phase Nginx corridor"
        )


def execute_bridge(
    context: BridgeContext,
    *,
    apply: bool = False,
    confirm: str | None = None,
    controller_liveness_fd: int | None = None,
    nginx_executor: NginxExecutor = NGINX.execute_coordinator,
    now_fn: Callable[[], datetime] = (
        lambda: datetime.now(timezone.utc)
    ),
) -> dict[str, Any]:
    plan_result = {
        "schema": RESULT_SCHEMA,
        "status": "planned",
        "campaign_id": context.manifest["campaign_id"],
        "operation_id": context.manifest["operation_id"],
        "release_sha": context.manifest["release_sha"],
        "release_tree_sha": context.manifest["release_tree_sha"],
        "manifest_sha256": context.manifest_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "phases": list(PHASES),
        "actions": {
            phase: {
                "action": PHASE_ACTIONS[phase][0],
                "target_state": PHASE_ACTIONS[phase][1],
            }
            for phase in PHASES
        },
        "required_confirmation": confirmation_phrase(context),
        "controller_liveness_pipe_required": True,
        "writable_generation_supported": False,
        "postcommit_supported": False,
        "rollback_supported": False,
        "production_contacted": False,
        "journal_mutated": False,
        "active_configuration_mutated": False,
        "business_write_observed": False,
        "current_mutated": False,
        "container_mutated": False,
        "volume_mutated": False,
        "data_mutated": False,
        "object_storage_mutated": False,
    }
    if not apply:
        if confirm is not None or controller_liveness_fd is not None:
            raise NginxCutoverPhaseBridgeError(
                "plan mode does not accept apply authority"
            )
        return plan_result
    if os.geteuid() != 0 or os.getegid() != 0:
        raise NginxCutoverPhaseBridgeError(
            "mutating Nginx bridge requires root:root"
        )
    if threading.current_thread() is not threading.main_thread():
        raise NginxCutoverPhaseBridgeError(
            "mutating Nginx bridge must run in the main thread"
        )
    if confirm != confirmation_phrase(context):
        raise NginxCutoverPhaseBridgeError(
            "Nginx bridge apply confirmation differs"
        )
    if controller_liveness_fd is None:
        raise NginxCutoverPhaseBridgeError(
            "apply requires an anonymous controller-liveness pipe"
        )
    _verify_authorization(context)
    _prepare_output_root(context)
    journal = CONTROLLER.ProductionCutoverJournal(
        Path(
            context.manifest["deployment"][
                "controller_journal_path"
            ]
        )
    )
    try:
        initial = journal.assert_bindings(**_journal_bindings(context))
    except CONTROLLER.CutoverContractError as exc:
        raise NginxCutoverPhaseBridgeError(
            "production cutover journal binding differs"
        ) from exc
    _assert_corridor(initial)
    evidence_paths = dict(context.prior_paths)
    for phase in PHASES:
        if phase not in initial["completed_phases"]:
            break
        evidence_paths[phase] = _completed_evidence_path(
            context,
            phase=phase,
            digest=initial["phase_evidence_sha256"][phase],
        )

    phase_results: dict[str, Any] = {}
    nginx_action_called = False
    journal_begin_called = False
    journal_complete_called = False
    active_configuration_mutated = False
    with _signal_cancellation_guard():
        with ControllerLiveness(controller_liveness_fd) as liveness:
            for phase in PHASES:
                liveness.check()
                _verify_authorization(context)
                try:
                    state = journal.assert_bindings(
                        **_journal_bindings(context)
                    )
                except CONTROLLER.CutoverContractError as exc:
                    raise NginxCutoverPhaseBridgeError(
                        f"{phase} journal binding failed"
                    ) from exc
                _assert_corridor(state)
                if phase in state["completed_phases"]:
                    evidence_paths[phase] = _completed_evidence_path(
                        context,
                        phase=phase,
                        digest=state["phase_evidence_sha256"][phase],
                    )
                    phase_results[phase] = {
                        "status": "reused-completed",
                        "phase_evidence_sha256": state[
                            "phase_evidence_sha256"
                        ][phase],
                    }
                    continue
                try:
                    with _signal_reconciliation_scope():
                        state = journal.begin_phase(phase)
                        journal_begin_called = True
                        if (
                            state["status"] != "phase_started"
                            or state["started_phase"] != phase
                            or state["completed_phases"]
                            != list(
                                CONTROLLER.PHASES[
                                    : CONTROLLER.PHASES.index(phase)
                                ]
                            )
                        ):
                            raise NginxCutoverPhaseBridgeError(
                                f"{phase} durable start readback differs"
                            )
                except CONTROLLER.CutoverContractError as exc:
                    raise NginxCutoverPhaseBridgeError(
                        f"{phase} cannot be durably started"
                    ) from exc
                liveness.check()
                with _signal_reconciliation_scope():
                    phase_action = _invoke_phase_action(
                        context,
                        phase=phase,
                        executor=nginx_executor,
                    )
                (
                    action_receipt,
                    action_digest,
                    action_path,
                    delayed_receipt,
                    delayed_digest,
                    delayed_path,
                    phase_active_configuration_mutated,
                ) = phase_action
                nginx_action_called = True
                active_configuration_mutated = (
                    active_configuration_mutated
                    or phase_active_configuration_mutated
                )
                liveness.check()
                state = journal.assert_bindings(
                    **_journal_bindings(context)
                )
                if (
                    state["status"] != "phase_started"
                    or state["started_phase"] != phase
                ):
                    raise NginxCutoverPhaseBridgeError(
                        f"{phase} journal changed during Nginx execution"
                    )
                (
                    evidence_path,
                    role_paths,
                    claim_paths,
                    aggregate,
                ) = _prepare_phase_evidence(
                    context,
                    phase=phase,
                    action_receipt=action_receipt,
                    action_receipt_sha256=action_digest,
                    action_receipt_path=action_path,
                    delayed_receipt=delayed_receipt,
                    delayed_receipt_sha256=delayed_digest,
                    delayed_receipt_path=delayed_path,
                    journal_state=state,
                    evidence_paths=evidence_paths,
                    now=now_fn(),
                )
                liveness.check()
                _verify_authorization(context)
                try:
                    verification, receipt = (
                        CONTROLLER._run_release_phase_verifier(  # noqa: SLF001
                            phase=phase,
                            manifest=context.manifest,
                            manifest_sha256=context.manifest_sha256,
                            plan=context.plan,
                            manifest_path=context.manifest_path,
                            approval_path=context.approval_path,
                            approval_policy_path=(
                                context.approval_policy_path
                            ),
                            evidence_path=evidence_path,
                            role_validation=[
                                f"{role}={role_paths[role]}"
                                for role in ROLE_ORDER
                            ],
                            claim_source=[
                                f"{claim}={claim_paths[claim]}"
                                for claim in VERIFY.PHASE_CLAIM_RULES[
                                    phase
                                ]
                            ],
                            prior_phase_evidence=[
                                f"{prior}={evidence_paths[prior]}"
                                for prior in CONTROLLER.PHASES[
                                    : CONTROLLER.PHASES.index(phase)
                                ]
                            ],
                        )
                    )
                    CONTROLLER._persist_phase_verification_receipt(  # noqa: SLF001
                        token=verification,
                        receipt=receipt,
                        evidence_root=Path(
                            context.manifest["deployment"][
                                "controller_evidence_root"
                            ]
                        ),
                    )
                    with _signal_reconciliation_scope():
                        completed = journal.complete_phase(
                            phase,
                            verification=verification,
                        )
                        journal_complete_called = True
                        if (
                            completed[
                                "phase_evidence_sha256"
                            ][phase]
                            != verification.evidence_sha256
                            or completed[
                                "phase_verification_sha256"
                            ][phase]
                            != verification.receipt_sha256
                        ):
                            raise NginxCutoverPhaseBridgeError(
                                f"{phase} journal completion readback "
                                "differs"
                            )
                except CONTROLLER.CutoverContractError as exc:
                    raise NginxCutoverPhaseBridgeError(
                        f"{phase} release-bound verification failed"
                    ) from exc
                evidence_paths[phase] = evidence_path
                phase_results[phase] = {
                    "status": "completed",
                    **aggregate,
                    "verification_sha256": (
                        verification.receipt_sha256
                    ),
                }

    final = journal.assert_bindings(**_journal_bindings(context))
    expected_completed = list(
        CONTROLLER.PHASES[
            : len(INITIAL_PRIOR_PHASES) + len(PHASES)
        ]
    )
    if (
        final["completed_phases"] != expected_completed
        or final["status"] != "active"
        or final["started_phase"] is not None
        or final["first_business_write_allowed"] is not False
    ):
        raise NginxCutoverPhaseBridgeError(
            "three-phase Nginx journal closure differs"
        )
    result = {
        **plan_result,
        "status": "completed",
        "phase_results": phase_results,
        "phase_evidence_sha256": {
            phase: final["phase_evidence_sha256"][phase]
            for phase in PHASES
        },
        "journal_state_sha256": final["state_sha256"],
        "journal_event_tail_sha256": final["event_tail_sha256"],
        "next_phase": CONTROLLER.PHASES[
            len(INITIAL_PRIOR_PHASES) + len(PHASES)
        ],
        "production_contacted": nginx_action_called,
        "journal_mutated": (
            journal_begin_called or journal_complete_called
        ),
        "active_configuration_mutated": active_configuration_mutated,
    }
    result.pop("required_confirmation")
    final_path, final_sha256 = _persist_document(
        context.output_root / "aggregates",
        prefix="three-phase-aggregate",
        document=result,
    )
    return {
        **result,
        "aggregate_path": os.fspath(final_path),
        "aggregate_sha256": final_sha256,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument("--controller-liveness-fd", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        context = load_bridge_request(args.request)
        result = execute_bridge(
            context,
            apply=args.apply,
            confirm=args.confirm,
            controller_liveness_fd=args.controller_liveness_fd,
        )
        print(
            json.dumps(
                result,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0
    except NginxCutoverPhaseBridgeError as exc:
        may_have_applied = bool(args.apply)
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                    "production_contacted": (
                        None if may_have_applied else False
                    ),
                    "journal_mutated": (
                        None if may_have_applied else False
                    ),
                    "reconciliation_required": may_have_applied,
                    "business_write_observed": False,
                    "current_mutated": False,
                    "container_mutated": False,
                    "volume_mutated": False,
                    "data_mutated": False,
                    "object_storage_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2
    except Exception:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": "Nginx cutover phase bridge failed closed",
                    "error_class": "NginxCutoverPhaseBridgeError",
                    "production_contacted": (
                        None if args.apply else False
                    ),
                    "journal_mutated": None if args.apply else False,
                    "reconciliation_required": bool(args.apply),
                    "business_write_observed": False,
                    "current_mutated": False,
                    "container_mutated": False,
                    "volume_mutated": False,
                    "data_mutated": False,
                    "object_storage_mutated": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
