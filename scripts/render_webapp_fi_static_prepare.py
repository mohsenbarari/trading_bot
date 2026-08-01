#!/usr/bin/env python3
"""Render and verify the local WebApp-FI static-asset preparation control.

This controller-only helper is the predecessor of
``render_webapp_fi_initial_static_upload.py``.  It validates the immutable
source-adoption package/install receipt, the canonical campaign binding, and
the controller's root-only v3 FI role config, then renders one pinned SSH
command for the package's fixed static preparer.  The remote command has no
operator-selected program, source root, or output path:

* the production runtime source is exactly ``/srv/trading-bot/current``;
* the preparer is exactly the installed candidate's packaged helper; and
* the new output directory is derived from the package's bound initial-static
  object ID.

The renderer neither opens SSH nor contacts Object Storage, Docker, or a
service.  The remote helper verifies the fixed runtime ``mini_app_dist``
against the controller-bound manifest installed with its source-adoption
candidate before it creates an archive.  A later operator may capture its
small URL-free JSON result and pass it to
``verify-receipt`` before using the existing initial-static upload renderer.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import sys
from typing import Any, Mapping, Sequence


FI_RUNTIME_SOURCE_ROOT = PurePosixPath("/srv/trading-bot/current")
STATIC_PREPARER_MEMBER = "scripts/prepare_webapp_fi_static_assets.py"
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
STATIC_OUTPUT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_RECEIPT_MARKERS = (
    b"://",
    b'"url"',
    b"presigned",
    b"credential",
    b"access_key",
    b"secret",
    b"private_key",
    b"session_token",
    b"password",
)


class StaticPreparationControlError(RuntimeError):
    """One static-preparation control input is unsafe or unbound."""


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    if not path.is_absolute():
        raise RuntimeError(f"{field} parent must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except OSError as exc:  # pragma: no cover - repository layout invariant.
            raise RuntimeError(f"cannot inspect {field} parent") from exc
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or ((mode & 0o022) and not (metadata.st_mode & stat.S_ISVTX))
        ):
            raise RuntimeError(f"{field} parent is not root-controlled")


def _require_root_controlled_code_file(path: Path, *, field: str) -> Path:
    if not path.is_absolute():
        raise RuntimeError(f"{field} must be absolute")
    _require_root_controlled_directory_chain(path.parent, field=field)
    try:
        before = path.lstat()
        resolved = path.resolve(strict=True)
        opened = resolved.lstat()
    except OSError as exc:  # pragma: no cover - repository layout invariant.
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
    if not isinstance(filename, str) or Path(filename).name != filename or filename in {"", ".", ".."}:
        raise RuntimeError("required sibling filename is unsafe")
    source = _require_root_controlled_code_file(Path(__file__), field="FI static preparation renderer source")
    path = _require_root_controlled_code_file(source.with_name(filename), field=f"required sibling {filename}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - repository invariant.
        raise RuntimeError(f"cannot load required sibling {filename}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        loaded = getattr(module, "__file__", None)
        if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != path:
            raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


initial = _load_exact_sibling(
    "render_webapp_fi_initial_static_upload.py", "_fi_static_preparation_initial_static"
)
role_config = _load_exact_sibling(
    "render_webapp_fi_source_role_config.py", "_fi_static_preparation_role_config"
)
static_preparer = _load_exact_sibling(
    "prepare_webapp_fi_static_assets.py", "_fi_static_preparation_helper"
)


@dataclasses.dataclass(frozen=True)
class StaticPreparationControl:
    """All controller-verified facts used by one fixed FI preparation call."""

    initial_control: Any
    source_role_config_path: Path
    source_role_config_sha256: str
    static_output_id: str
    runtime_source_root: PurePosixPath
    static_output_directory: Path


def canonical_json_bytes(value: Mapping[str, Any] | Sequence[Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StaticPreparationControlError("static preparation receipt contains duplicate JSON keys")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise StaticPreparationControlError("static preparation receipt contains an unsupported JSON constant")


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticPreparationControlError("FI static preparation controls must run as root")


def _same_binding(left: Any, right: Any) -> bool:
    return all(
        getattr(left, field, None) == getattr(right, field, None)
        for field in (
            "campaign_id",
            "application_release_sha",
            "application_release_tree",
            "expected_alembic_revision",
            "control_commit",
            "control_tree",
            "binding_sha256",
        )
    )


def _load_role_config(*, campaign_binding_path: Path, source_role_config_path: Path, expected_binding: Any) -> str:
    """Read a v3 config through its own binding class and compare identities."""

    try:
        campaign_binding_path = Path(campaign_binding_path)
        source_role_config_path = Path(source_role_config_path)
        if source_role_config_path != campaign_binding_path.with_name("source-role-config.json"):
            raise StaticPreparationControlError("FI source role config is not at the campaign-bound fixed path")
        role_binding = role_config.binding.load_campaign_binding(Path(campaign_binding_path))
        if not _same_binding(role_binding, expected_binding):
            raise StaticPreparationControlError("source role config campaign binding differs from initial static binding")
        normalized = role_config.load_source_role_config(
            path=source_role_config_path, campaign_binding=role_binding
        )
        payload = role_config.canonical_json_bytes(normalized) + b"\n"
        # The public loader re-reads and validates the root-only file.  Its
        # canonical normalized form must be the exact persisted payload.
        actual = role_config._read_private_file(
            source_role_config_path, field="WebApp-FI source role config"
        )
    except StaticPreparationControlError:
        raise
    except Exception as exc:
        raise StaticPreparationControlError("root-only campaign-bound FI source role config is invalid") from exc
    if actual != payload:
        raise StaticPreparationControlError("FI source role config changed while being validated")
    return sha256_bytes(payload)


def _require_static_output_id(value: object, *, expected: str) -> str:
    if not isinstance(value, str) or not STATIC_OUTPUT_ID_RE.fullmatch(value):
        raise StaticPreparationControlError("static output ID is invalid")
    if value != expected:
        raise StaticPreparationControlError("static output ID is not the package-bound initial static object")
    return value


def build_static_preparation_control(
    *,
    source_transport_config: Path,
    campaign_binding: Path,
    source_adoption_package_directory: Path,
    preparation_receipt: Path,
    fi_install_receipt: Path,
    source_role_config: Path,
    static_output_id: str,
) -> StaticPreparationControl:
    """Bind the only allowed FI static-preparation command locally."""

    _require_root_execution()
    try:
        prepared = initial.build_initial_static_control(
            source_transport_config=Path(source_transport_config),
            campaign_binding=Path(campaign_binding),
            source_adoption_package_directory=Path(source_adoption_package_directory),
            preparation_receipt=Path(preparation_receipt),
            fi_install_receipt=Path(fi_install_receipt),
        )
    except Exception as exc:
        raise StaticPreparationControlError("initial static package or canonical campaign binding is invalid") from exc
    output_id = _require_static_output_id(static_output_id, expected=prepared.request.object_id)
    output_directory = prepared.static_archive.parent
    expected_output_directory = prepared.policy.workspace / ("initial-static-assets-" + output_id)
    if (
        output_directory != expected_output_directory
        or output_directory.parent != prepared.policy.workspace
        or output_directory.name != "initial-static-assets-" + output_id
        or prepared.static_archive.name != static_preparer.STATIC_ARCHIVE_NAME
    ):
        raise StaticPreparationControlError("static output path is not package-derived")
    role_sha = _load_role_config(
        campaign_binding_path=Path(campaign_binding),
        source_role_config_path=Path(source_role_config),
        expected_binding=prepared.campaign_binding,
    )
    return StaticPreparationControl(
        initial_control=prepared,
        source_role_config_path=Path(source_role_config),
        source_role_config_sha256=role_sha,
        static_output_id=output_id,
        runtime_source_root=FI_RUNTIME_SOURCE_ROOT,
        static_output_directory=output_directory,
    )


def render_prepare_command(*, control: StaticPreparationControl, fi_known_hosts: Path) -> str:
    """Render, but never execute, one fixed FI static-preparation command."""

    if not isinstance(control, StaticPreparationControl):
        raise StaticPreparationControlError("static preparation control is unsupported")
    prepared = control.initial_control
    application = prepared.campaign_binding
    remote = [
        "/usr/bin/python3",
        "-I",
        "-B",
        str(prepared.candidate_directory / STATIC_PREPARER_MEMBER),
        "--runtime-source-root",
        str(control.runtime_source_root),
        "--output-directory",
        str(control.static_output_directory),
        "--campaign-id",
        application.campaign_id,
        "--release-sha",
        application.application_release_sha,
        "--expected-alembic-revision",
        application.expected_alembic_revision,
        "--apply",
    ]
    try:
        return initial._render_pinned_ssh(known_hosts=Path(fi_known_hosts), remote_arguments=remote)
    except Exception as exc:
        raise StaticPreparationControlError("pinned FI SSH control cannot be rendered") from exc


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        payload = initial._read_root_controlled_file(
            Path(path),
            field="FI static preparation receipt",
            maximum_bytes=MAX_RECEIPT_BYTES,
            private=True,
        )
    except Exception as exc:
        raise StaticPreparationControlError("FI static preparation receipt is unsafe") from exc
    if any(marker in payload.lower() for marker in FORBIDDEN_RECEIPT_MARKERS):
        raise StaticPreparationControlError("FI static preparation receipt is not URL-free and nonsecret")
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaticPreparationControlError("FI static preparation receipt is not strict JSON") from exc
    if not isinstance(value, dict) or payload != canonical_json_bytes(value) + b"\n":
        raise StaticPreparationControlError("FI static preparation receipt is not canonical JSON")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise StaticPreparationControlError(f"{field} is invalid")
    return value


def _require_nonnegative_int(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StaticPreparationControlError(f"{field} is invalid")
    return value


def _expected_actions() -> dict[str, bool]:
    return {
        "object_storage_action": False,
        "age_action": False,
        "ssh_action": False,
        "docker_action": False,
        "service_changed": False,
    }


def _validate_capacity(value: object, *, archive_bytes: int, file_count: int) -> dict[str, int]:
    fields = {
        "archive_upper_bound_bytes",
        "file_manifest_bytes",
        "source_bytes",
        "file_count",
        "receipt_reserve_bytes",
        "margin_bytes",
        "required_free_bytes",
        "available_free_bytes",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise StaticPreparationControlError("FI static preparation receipt capacity is invalid")
    normalized = {
        key: _require_nonnegative_int(value.get(key), field=f"FI static preparation capacity {key}")
        for key in fields
    }
    if (
        normalized["archive_upper_bound_bytes"] < archive_bytes
        or normalized["archive_upper_bound_bytes"] > static_preparer.MAX_STATIC_ARCHIVE_BYTES
        or normalized["file_manifest_bytes"] < 1
        or normalized["file_manifest_bytes"] > static_preparer.MAX_FILE_MANIFEST_BYTES
        or normalized["file_count"] != file_count
        or normalized["receipt_reserve_bytes"] != static_preparer.RECEIPT_RESERVE_BYTES
        or normalized["margin_bytes"] != static_preparer.CAPACITY_MARGIN_BYTES
        or normalized["required_free_bytes"]
        != normalized["archive_upper_bound_bytes"]
        + normalized["file_manifest_bytes"]
        + normalized["receipt_reserve_bytes"]
        + normalized["margin_bytes"]
        or normalized["available_free_bytes"] < normalized["required_free_bytes"]
    ):
        raise StaticPreparationControlError("FI static preparation receipt capacity is inconsistent")
    return normalized


def validate_preparation_receipt(*, control: StaticPreparationControl, receipt: Path) -> dict[str, Any]:
    """Validate the fixed preparer's compact URL-free/nonsecret result."""

    if not isinstance(control, StaticPreparationControl):
        raise StaticPreparationControlError("static preparation control is unsupported")
    # Re-open the config before accepting remote evidence so a stale control
    # object cannot outlive a changed local campaign config.
    role_sha = _load_role_config(
        campaign_binding_path=Path(control.source_role_config_path).parent / "campaign-binding.json",
        source_role_config_path=control.source_role_config_path,
        expected_binding=control.initial_control.campaign_binding,
    )
    if role_sha != control.source_role_config_sha256:
        raise StaticPreparationControlError("FI source role config changed after static preparation was rendered")
    value = _read_receipt(Path(receipt))
    expected_fields = {
        "status",
        "campaign_id",
        "application",
        "source_site",
        "runtime_source_root",
        "static_source_root",
        "output_directory",
        "archive_name",
        "files_sha256",
        "file_count",
        "capacity_preflight",
        "object_storage_action",
        "age_action",
        "ssh_action",
        "docker_action",
        "service_changed",
        "archive",
        "file_manifest_path",
        "preparation_receipt_path",
        "file_manifest_sha256",
        "preparation_receipt_sha256",
        "verification",
    }
    if set(value) != expected_fields or value.get("status") != "prepared":
        raise StaticPreparationControlError("FI static preparation receipt is unsupported")
    binding = control.initial_control.campaign_binding
    application = {
        "release_sha": binding.application_release_sha,
        "expected_alembic_revision": binding.expected_alembic_revision,
    }
    output_directory = control.static_output_directory
    static_source_root = Path(str(control.runtime_source_root)) / static_preparer.RUNTIME_STATIC_ASSET_RELATIVE
    if (
        value.get("campaign_id") != binding.campaign_id
        or value.get("application") != application
        or value.get("source_site") != "webapp_fi"
        or value.get("runtime_source_root") != str(control.runtime_source_root)
        or value.get("static_source_root") != str(static_source_root)
        or value.get("output_directory") != str(output_directory)
        or value.get("archive_name") != static_preparer.STATIC_ARCHIVE_NAME
        or any(value.get(key) is not expected for key, expected in _expected_actions().items())
    ):
        raise StaticPreparationControlError("FI static preparation receipt is not bound to the fixed control")
    files_sha = _require_sha256(value.get("files_sha256"), field="FI static preparation files checksum")
    file_manifest_sha = _require_sha256(value.get("file_manifest_sha256"), field="FI static file manifest checksum")
    preparation_sha = _require_sha256(
        value.get("preparation_receipt_sha256"), field="FI static preparation receipt checksum"
    )
    file_count = _require_nonnegative_int(
        value.get("file_count"), field="FI static preparation file count", minimum=1
    )
    if file_count > static_preparer.MAX_STATIC_FILES:
        raise StaticPreparationControlError("FI static preparation file count is invalid")
    if (
        files_sha != control.initial_control.expected_static_files_sha256
        or file_count != control.initial_control.expected_static_file_count
    ):
        raise StaticPreparationControlError(
            "FI static preparation receipt does not match the controller-bound expected static manifest"
        )
    archive = value.get("archive")
    if not isinstance(archive, Mapping) or set(archive) != {"name", "sha256", "bytes"}:
        raise StaticPreparationControlError("FI static preparation archive is invalid")
    archive_bytes = _require_nonnegative_int(archive.get("bytes"), field="FI static archive bytes", minimum=1)
    if archive.get("name") != static_preparer.STATIC_ARCHIVE_NAME or archive_bytes > static_preparer.MAX_STATIC_ARCHIVE_BYTES:
        raise StaticPreparationControlError("FI static preparation archive is invalid")
    archive_sha = _require_sha256(archive.get("sha256"), field="FI static archive checksum")
    capacity = _validate_capacity(value.get("capacity_preflight"), archive_bytes=archive_bytes, file_count=file_count)
    if (
        value.get("file_manifest_path") != str(output_directory / static_preparer.STATIC_FILE_MANIFEST_NAME)
        or value.get("preparation_receipt_path")
        != str(output_directory / static_preparer.STATIC_PREPARATION_RECEIPT_NAME)
    ):
        raise StaticPreparationControlError("FI static preparation receipt output paths are invalid")
    verification = value.get("verification")
    expected_verification = {
        "status": "verified",
        "output_directory": str(output_directory),
        "archive": {"name": static_preparer.STATIC_ARCHIVE_NAME, "sha256": archive_sha, "bytes": archive_bytes},
        "files_sha256": files_sha,
        "file_count": file_count,
        "file_manifest_sha256": file_manifest_sha,
        "preparation_receipt_sha256": preparation_sha,
        **_expected_actions(),
    }
    if verification != expected_verification:
        raise StaticPreparationControlError("FI static preparation verification is not bound to the receipt")
    return {
        "status": "verified",
        "campaign_id": binding.campaign_id,
        "static_output_id": control.static_output_id,
        "output_directory": str(output_directory),
        "archive": dict(expected_verification["archive"]),
        "files_sha256": files_sha,
        "file_count": file_count,
        "file_manifest_sha256": file_manifest_sha,
        "preparation_receipt_sha256": preparation_sha,
        "capacity_preflight": capacity,
    }


def _base_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-transport-config", required=True, type=Path)
    parser.add_argument("--campaign-binding", required=True, type=Path)
    parser.add_argument("--source-adoption-package-directory", required=True, type=Path)
    parser.add_argument("--preparation-receipt", required=True, type=Path)
    parser.add_argument("--fi-install-receipt", required=True, type=Path)
    parser.add_argument("--source-role-config", required=True, type=Path)
    parser.add_argument("--static-output-id", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    render = actions.add_parser("render", help="render one fixed pinned-SSH FI static preparation command")
    _base_arguments(render)
    render.add_argument("--fi-known-hosts", required=True, type=Path)
    verify = actions.add_parser("verify-receipt", help="verify one URL-free FI static preparation result")
    _base_arguments(verify)
    verify.add_argument("--receipt", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        control = build_static_preparation_control(
            source_transport_config=args.source_transport_config,
            campaign_binding=args.campaign_binding,
            source_adoption_package_directory=args.source_adoption_package_directory,
            preparation_receipt=args.preparation_receipt,
            fi_install_receipt=args.fi_install_receipt,
            source_role_config=args.source_role_config,
            static_output_id=args.static_output_id,
        )
        if args.action == "render":
            print(render_prepare_command(control=control, fi_known_hosts=args.fi_known_hosts))
        elif args.action == "verify-receipt":
            print(json.dumps(validate_preparation_receipt(control=control, receipt=args.receipt), sort_keys=True))
        else:  # pragma: no cover - argparse dispatch invariant.
            raise StaticPreparationControlError("unsupported static preparation control action")
        return 0
    except StaticPreparationControlError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
