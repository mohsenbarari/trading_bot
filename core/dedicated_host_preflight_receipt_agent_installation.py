"""Pure staged-asset verification and installation-attestation contract.

The receipt-agent renderer intentionally does not touch a host.  This module
is the non-I/O counterpart of the later root-only installer: it reconstructs
the only allowed installation config from staged bytes, re-renders the exact
fixed asset set, and refuses every changed, missing, extra, or mode-mismatched
file.  It also builds the final non-authorizing local installation attestation.

It does not open files, run validators, create accounts, invoke SSH/sudo,
contact a server, or apply an installation.  The fixed-path installer script
is the only layer allowed to supply a securely read stage mapping and to make
an explicit local host mutation after this validation passes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from core import dedicated_host_preflight_receipt_agent_boundary as _boundary


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_SCHEMA",
    "DedicatedHostPreflightReceiptAgentInstallationError",
    "VerifiedReceiptAgentInstallationStage",
    "canonical_installation_attestation_bytes",
    "verify_staged_receipt_agent_assets",
)


DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_SCHEMA = (
    "three-site-dedicated-host-preflight-receipt-agent-installation-attestation-v1"
)

_VERSION = 1
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$",
    re.ASCII,
)


class DedicatedHostPreflightReceiptAgentInstallationError(ValueError):
    """A fixed, redacted staged-installation validation refusal."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedReceiptAgentInstallationStage:
    """A complete byte/mode-equal renderer output; not an apply permission."""

    config: _boundary.ReceiptAgentInstallationConfig
    installation_sha256: str
    files: tuple[_boundary.RenderedReceiptAgentFile, ...]
    _capability: object | None = None


_CAPABILITY = object()


def _fail(code: str) -> None:
    raise DedicatedHostPreflightReceiptAgentInstallationError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")


def _canonical_object(value: object, *, code: str) -> dict[str, Any]:
    if type(value) is not bytes or not value or len(value) > 64 * 1024:
        _fail(code)
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except DedicatedHostPreflightReceiptAgentInstallationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail(code)
    if type(parsed) is not dict or _boundary.canonical_json_document(parsed, code=code) + b"\n" != value:
        _fail(code)
    return parsed


def _stage_mapping(value: object) -> dict[Path, tuple[bytes, int]]:
    if not isinstance(value, Mapping):
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")
    result: dict[Path, tuple[bytes, int]] = {}
    for destination, item in value.items():
        if (
            not isinstance(destination, Path)
            or not destination.is_absolute()
            or ".." in destination.parts
            or not isinstance(item, tuple)
            or len(item) != 2
            or type(item[0]) is not bytes
            or type(item[1]) is not int
            or not 1 <= len(item[0]) <= 64 * 1024
            or item[1] not in {0o440, 0o600, 0o644, 0o755}
            or destination in result
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")
        result[destination] = (item[0], item[1])
    return result


def _installation_config_from_stage(
    files: Mapping[Path, tuple[bytes, int]],
) -> _boundary.ReceiptAgentInstallationConfig:
    try:
        runtime_raw, runtime_mode = files[_boundary.FIXED_PREFLIGHT_ROOT_COLLECTOR_CONFIG]
        authorized_raw, authorized_mode = files[_boundary.FIXED_PREFLIGHT_AUTHORIZED_KEYS]
    except KeyError:
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")
    if runtime_mode != 0o600 or authorized_mode != 0o644:
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")
    try:
        runtime = _boundary.parse_receipt_agent_runtime_config(
            _canonical_object(runtime_raw, code="PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID")
        )
        key = _boundary.parse_receipt_agent_authorized_key_bytes(authorized_raw)
        return _boundary.ReceiptAgentInstallationConfig(
            enabled=runtime.enabled,
            site_role=runtime.site_role,
            agent_release_sha=runtime.agent_release_sha,
            controller_public_key=key,
        )
    except _boundary.ReceiptAgentBoundaryError as exc:
        raise DedicatedHostPreflightReceiptAgentInstallationError(
            "PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID"
        ) from exc


def verify_staged_receipt_agent_assets(
    stage_files: Mapping[Path, tuple[bytes, int]],
) -> VerifiedReceiptAgentInstallationStage:
    """Require byte-for-byte equality with the fixed renderer output.

    A stage that merely contains a plausible config is insufficient: the
    installer must see exactly the full deterministic renderer output for the
    reconstructed config, including the installation digest file and, only on
    Witness, its distinct literal evidence-account assets.
    """

    observed = _stage_mapping(stage_files)
    config = _installation_config_from_stage(observed)
    try:
        expected = _boundary.render_receipt_agent_assets(config)
    except _boundary.ReceiptAgentBoundaryError as exc:
        raise DedicatedHostPreflightReceiptAgentInstallationError(
            "PREFLIGHT_RECEIPT_AGENT_STAGE_INVALID"
        ) from exc
    expected_mapping = {
        item.destination: (item.content, item.mode)
        for item in expected.files
    }
    if observed != expected_mapping:
        _fail("PREFLIGHT_RECEIPT_AGENT_STAGE_MISMATCH")
    result = VerifiedReceiptAgentInstallationStage(
        config=expected.config,
        installation_sha256=expected.installation_sha256,
        files=expected.files,
    )
    object.__setattr__(result, "_capability", _CAPABILITY)
    return result


def _timestamp(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_INVALID")
    rendered = value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    if _TIMESTAMP_RE.fullmatch(rendered) is None:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_INVALID")
    return rendered


def canonical_installation_attestation_bytes(
    *,
    stage: VerifiedReceiptAgentInstallationStage,
    installed_at: datetime,
) -> bytes:
    """Build the final root-local result after validators have passed.

    The caller must not use this document as an activation or permission
    signal: it attests only that a matching staged read-only endpoint was
    atomically installed and locally syntax-validated.
    """

    if (
        type(stage) is not VerifiedReceiptAgentInstallationStage
        or stage._capability is not _CAPABILITY
    ):
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_INVALID")
    value = {
        "schema": DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_SCHEMA,
        "version": _VERSION,
        "status": "installed-not-activated",
        "installed_at": _timestamp(installed_at),
        "installation_sha256": stage.installation_sha256,
        "site_role": stage.config.site_role,
        "agent_release_sha": stage.config.agent_release_sha,
        "enabled": stage.config.enabled,
        "direct_finland_to_iran": "forbidden",
        "file_count": len(stage.files),
        "files": [
            {
                "path": str(item.destination),
                "mode": format(item.mode, "04o"),
                "sha256": hashlib.sha256(item.content).hexdigest(),
            }
            for item in stage.files
        ],
        "sshd_syntax_validated": True,
        "sudoers_syntax_validated": True,
        "service_reloaded": False,
        "writer_authorized": False,
        "promotion_authorized": False,
        "execution_authorized": False,
    }
    try:
        return _boundary.canonical_json_document(
            value,
            code="PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_INVALID",
        ) + b"\n"
    except _boundary.ReceiptAgentBoundaryError as exc:
        raise DedicatedHostPreflightReceiptAgentInstallationError(
            "PREFLIGHT_RECEIPT_AGENT_INSTALLATION_ATTESTATION_INVALID"
        ) from exc
