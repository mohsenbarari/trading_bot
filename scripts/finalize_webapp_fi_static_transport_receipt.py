#!/usr/bin/env python3
"""Finalize one controller-verified FI static receipt for WA-IR consumption.

WebApp-FI emits a signed upload report for its direct, immutable Object
Storage upload.  The controller receiver consumes that report, reads back the
exact VersionId, decrypts the static archive into its fixed candidate, and
writes ``readback.json``.  WA-IR deliberately consumes the generic source
transport receipt instead.  This small controller-local bridge creates that
generic receipt only after independently revalidating all three facts:

* the root-only canonical campaign binding and FI upload report;
* the exact static-only dual-recipient transport route; and
* the successful exact controller read-back candidate, ciphertext, archive,
  and canonical readback record.

The receipt has one fixed name beside the verified candidate and is created
once with ``O_EXCL``.  This helper has no S3 client, no presigning, no SSH,
no Docker, and no deployment or service behavior.
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Mapping, Sequence


TRANSPORT_RECEIPT_NAME = "transport-publish-receipt.json"
MAX_RECEIPT_BYTES = 1024 * 1024


class StaticTransportReceiptFinalizationError(RuntimeError):
    """A generic WA-IR static receipt cannot be proven from controller state."""


def _require_root_controlled_directory_chain(path: Path, *, field: str) -> None:
    """Require a stable root-owned code lookup path before importing a sibling."""

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
    """Return one exact root-owned, non-writable code file without symlinks."""

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
        or stat.S_IMODE(after.st_mode) & (0o022 | unsafe_bits)
    ):
        raise RuntimeError(f"{field} is not a root-controlled regular file")
    return path


def _load_exact_sibling(filename: str, module_name: str) -> Any:
    """Load a named sibling without accepting an ambient ``sys.path`` copy."""

    source = _require_root_controlled_code_file(
        Path(__file__),
        field="FI static transport receipt finalizer source",
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
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    loaded = getattr(module, "__file__", None)
    if not isinstance(loaded, str) or Path(loaded).resolve(strict=True) != path:
        raise RuntimeError(f"required sibling {filename} did not load from its exact path")
    return module


receiver = _load_exact_sibling(
    "receive_webapp_fi_source_object.py",
    "_finalize_webapp_fi_static_transport_receiver",
)


@dataclasses.dataclass(frozen=True)
class StaticTransportReceiptPlan:
    """One fully local, fixed-path finalization plan."""

    controller_config: Any
    campaign_binding_path: Path
    upload_report_path: Path
    receive_plan: Any
    readback_record_path: Path
    ciphertext_path: Path
    static_archive_path: Path
    receipt_path: Path
    receipt: Mapping[str, Any]


def _require_root_execution() -> None:
    if os.geteuid() != 0:
        raise StaticTransportReceiptFinalizationError(
            "controller static transport receipt finalization must run as root"
        )


def _raise_receiver_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except receiver.SourceObjectReceiveError as exc:
        raise StaticTransportReceiptFinalizationError(message) from exc


def _raise_exchange_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except receiver.exchange.SourceExchangeError as exc:
        raise StaticTransportReceiptFinalizationError(message) from exc


def _raise_transport_error(action: Callable[[], Any], *, message: str) -> Any:
    try:
        return action()
    except receiver.transport.SourceTransportError as exc:
        raise StaticTransportReceiptFinalizationError(message) from exc


def _require_static_receive_plan(plan: Any) -> None:
    """Pin this bridge to the only FI-to-controller/WA-IR static route."""

    policy = plan.controller_config.policy
    if (
        plan.kind.object_kind != receiver.contract.STATIC_OBJECT_KIND
        or plan.kind.readback_schema != receiver.STATIC_READBACK_SCHEMA
        or plan.kind.plaintext_name != receiver.STATIC_PAYLOAD_NAME
        or plan.request.source_site != "webapp_fi"
        or plan.request.destination_site != receiver.contract.STATIC_DESTINATION_SITE
        or plan.request.object_kind != receiver.contract.STATIC_OBJECT_KIND
        or plan.request.mode != receiver.contract.STATIC_MODE
        or tuple(plan.request.recipients)
        != (policy.controller_age_recipient, policy.webapp_ir_age_recipient)
    ):
        raise StaticTransportReceiptFinalizationError(
            "FI upload report is not the exact static dual-recipient route"
        )


def _read_exact_readback(plan: Any, *, path: Path) -> None:
    """Require the receiver's complete static candidate before publishing a bridge."""

    candidate = plan.candidate_directory
    try:
        if os.stat(plan.data_root).st_dev != os.stat(candidate).st_dev:
            raise StaticTransportReceiptFinalizationError(
                "controller static receive candidate is not on the fixed staging-volume filesystem"
            )
    except OSError as exc:
        raise StaticTransportReceiptFinalizationError(
            "controller static receive candidate filesystem cannot be inspected"
        ) from exc
    expected_names = {
        receiver.CIPHERTEXT_NAME,
        receiver.STATIC_PAYLOAD_NAME,
        receiver.READBACK_RECORD_NAME,
    }
    try:
        entries = {entry.name for entry in candidate.iterdir()}
    except OSError as exc:
        raise StaticTransportReceiptFinalizationError(
            "controller static receive candidate cannot be enumerated"
        ) from exc
    if entries != expected_names:
        raise StaticTransportReceiptFinalizationError(
            "controller static receive candidate has an unsupported artifact set"
        )
    if path != candidate / receiver.READBACK_RECORD_NAME:
        raise StaticTransportReceiptFinalizationError(
            "controller static readback record path is not fixed"
        )
    payload = _raise_exchange_error(
        lambda: receiver.exchange._read_private_file(
            path,
            field="controller static readback record",
            maximum_bytes=MAX_RECEIPT_BYTES,
        ),
        message="controller static readback record is not a root-only canonical input",
    )
    record = _raise_exchange_error(
        lambda: receiver.exchange._parse_canonical_json(
            payload,
            field="controller static readback record",
            reject_url=True,
        ),
        message="controller static readback record is invalid",
    )
    expected = receiver._build_readback_record(plan)
    if record != expected:
        raise StaticTransportReceiptFinalizationError(
            "controller static readback record is not bound to the verified FI upload report"
        )

    ciphertext_sha256, ciphertext_bytes = _raise_exchange_error(
        lambda: receiver.exchange._secure_hash_file(
            candidate / receiver.CIPHERTEXT_NAME,
            field="controller static read-back ciphertext",
            maximum_bytes=plan.descriptor["ciphertext_bytes"],
        ),
        message="controller static read-back ciphertext is unsafe",
    )
    if (
        ciphertext_sha256 != plan.descriptor["ciphertext_sha256"]
        or ciphertext_bytes != plan.descriptor["ciphertext_bytes"]
    ):
        raise StaticTransportReceiptFinalizationError(
            "controller static read-back ciphertext does not match the verified FI upload report"
        )
    archive_sha256, archive_bytes = _raise_exchange_error(
        lambda: receiver.exchange._secure_hash_file(
            candidate / receiver.STATIC_PAYLOAD_NAME,
            field="controller static decrypted archive",
            maximum_bytes=plan.descriptor["plaintext_bytes"],
        ),
        message="controller static decrypted archive is unsafe",
    )
    if (
        archive_sha256 != plan.descriptor["plaintext_sha256"]
        or archive_bytes != plan.descriptor["plaintext_bytes"]
    ):
        raise StaticTransportReceiptFinalizationError(
            "controller static decrypted archive does not match the verified FI upload report"
        )


def _require_absent_receipt(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise StaticTransportReceiptFinalizationError(
            "controller static transport receipt already exists and will not be reused"
        )


def prepare_static_transport_receipt(
    *,
    controller_config: Any,
    campaign_binding_path: Path,
    upload_report_path: Path,
) -> StaticTransportReceiptPlan:
    """Build one receipt only from a verified existing static read-back candidate."""

    _require_root_execution()
    receive_plan = _raise_receiver_error(
        lambda: receiver.prepare_receive(
            controller_config=controller_config,
            campaign_binding_path=Path(campaign_binding_path),
            upload_report_path=Path(upload_report_path),
            candidate_state="existing",
        ),
        message="canonical campaign binding, FI upload report, or controller receive identity is invalid",
    )
    _require_static_receive_plan(receive_plan)
    readback_record_path = receive_plan.candidate_directory / receiver.READBACK_RECORD_NAME
    receipt_path = receive_plan.candidate_directory / TRANSPORT_RECEIPT_NAME
    _require_absent_receipt(receipt_path)
    _read_exact_readback(receive_plan, path=readback_record_path)
    receipt = _raise_transport_error(
        lambda: receiver.transport.build_publish_receipt(
            config=receive_plan.controller_config.policy,
            request=receive_plan.request,
            descriptor=receive_plan.descriptor,
        ),
        message="generic static source transport receipt cannot be built from the verified FI report",
    )
    verified = _raise_transport_error(
        lambda: receiver.transport.verify_publish_receipt(
            config=receive_plan.controller_config.policy,
            payload=receiver.transport.canonical_json_bytes(receipt) + b"\n",
        ),
        message="generic static source transport receipt is invalid",
    )
    if verified != receipt:
        raise StaticTransportReceiptFinalizationError(
            "generic static source transport receipt changed during verification"
        )
    return StaticTransportReceiptPlan(
        controller_config=receive_plan.controller_config,
        campaign_binding_path=Path(campaign_binding_path),
        upload_report_path=Path(upload_report_path),
        receive_plan=receive_plan,
        readback_record_path=readback_record_path,
        ciphertext_path=receive_plan.candidate_directory / receiver.CIPHERTEXT_NAME,
        static_archive_path=receive_plan.candidate_directory / receiver.STATIC_PAYLOAD_NAME,
        receipt_path=receipt_path,
        receipt=receipt,
    )


def _plan_summary(plan: StaticTransportReceiptPlan, *, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "campaign_id": plan.receive_plan.campaign_binding.campaign_id,
        "object": dict(plan.receive_plan.descriptor),
        "candidate_directory": str(plan.receive_plan.candidate_directory),
        "readback_record": str(plan.readback_record_path),
        "transport_publish_receipt": str(plan.receipt_path),
    }


def finalize_static_transport_receipt(*, plan: StaticTransportReceiptPlan) -> dict[str, Any]:
    """Create and re-read one fixed generic receipt after a fresh local recheck."""

    _require_root_execution()
    if not isinstance(plan, StaticTransportReceiptPlan):
        raise StaticTransportReceiptFinalizationError("controller static transport receipt plan is unsupported")
    refreshed = prepare_static_transport_receipt(
        controller_config=plan.controller_config,
        campaign_binding_path=plan.campaign_binding_path,
        upload_report_path=plan.upload_report_path,
    )
    if refreshed != plan:
        raise StaticTransportReceiptFinalizationError(
            "controller static receipt inputs changed after preflight"
        )
    _raise_transport_error(
        lambda: receiver.transport.write_create_only_receipt(
            plan.receipt_path,
            plan.receipt,
            config=plan.controller_config.policy,
        ),
        message="cannot create controller static transport receipt",
    )
    _raise_receiver_error(
        lambda: receiver._fsync_directory(
            plan.receipt_path.parent,
            field="controller static receive candidate",
        ),
        message="cannot durably sync controller static transport receipt",
    )
    payload = _raise_exchange_error(
        lambda: receiver.exchange._read_private_file(
            plan.receipt_path,
            field="controller static transport receipt",
            maximum_bytes=MAX_RECEIPT_BYTES,
        ),
        message="created controller static transport receipt is unsafe",
    )
    verified = _raise_transport_error(
        lambda: receiver.transport.verify_publish_receipt(
            config=plan.controller_config.policy,
            payload=payload,
        ),
        message="created controller static transport receipt is invalid",
    )
    if verified != plan.receipt:
        raise StaticTransportReceiptFinalizationError(
            "created controller static transport receipt does not match the verified FI upload report"
        )
    return _plan_summary(plan, status="finalized")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    finalize = subparsers.add_parser(
        "finalize",
        help="create one generic WA-IR static transport receipt from an existing controller read-back",
    )
    finalize.add_argument("--config", required=True, type=Path)
    finalize.add_argument("--campaign-binding", required=True, type=Path)
    finalize.add_argument("--upload-report", required=True, type=Path)
    finalize.add_argument("--apply", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command != "finalize":  # pragma: no cover - argparse dispatch invariant.
            raise StaticTransportReceiptFinalizationError("unsupported command")
        controller_config = _raise_transport_error(
            lambda: receiver.transport.load_controller_config(args.config),
            message="controller source transport configuration is invalid",
        )
        plan = prepare_static_transport_receipt(
            controller_config=controller_config,
            campaign_binding_path=args.campaign_binding,
            upload_report_path=args.upload_report,
        )
        result = finalize_static_transport_receipt(plan=plan) if args.apply else _plan_summary(plan, status="planned")
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (StaticTransportReceiptFinalizationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point.
    raise SystemExit(main())
