"""Fail-closed install contract for the FI-side preflight receipt agent.

This module contains only data validation and rendering for a very small SSH
receipt boundary.  It does not open a socket, create an account, modify
``sshd``/``sudoers``, run a command, or contact WA-IR.  A separately reviewed
root-side installer may materialize the rendered files on exactly one of
Bot-FI, WA-FI, or Witness.

The remotely reachable account is intentionally unprivileged.  Its forced
dispatcher accepts only the canonical bounded preflight request already used
by the controller, invokes one exact root collector through ``sudo``, and
returns only a validated canonical receipt.  WA-IR is not a selectable role:
its receipt evidence remains a separate Witness-local architecture.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Protocol
from uuid import UUID

from core.dedicated_host_preflight_receipt import (
    CAMPAIGN_ID,
    HEX40,
    HEX64,
    MAX_RECEIPT_BYTES,
    canonical_json_bytes,
    parse_preflight_receipt,
)
from core.dedicated_host_preflight_ir_witness_attestation import (
    DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_SCHEMA,
    MAX_WA_IR_WITNESS_ATTESTATION_BYTES,
)
from scripts.dedicated_host_preflight_manifest import READONLY_REQUEST_SCHEMA


__all__ = (
    "DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_DEFAULT_ENABLED",
    "DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_SCHEMA",
    "DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_RUNTIME_SCHEMA",
    "DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_SCHEMA",
    "FIXED_PREFLIGHT_ACCOUNT",
    "FIXED_PREFLIGHT_AGENT_RELEASES_ROOT",
    "FIXED_PREFLIGHT_COLLECTOR_COMMAND",
    "FIXED_PREFLIGHT_FORCE_SHELL",
    "FIXED_PREFLIGHT_ROOT_COLLECTOR_CONFIG",
    "FIXED_PREFLIGHT_SUDO_BINARY",
    "FIXED_PREFLIGHT_SYSTEM_PYTHON",
    "FIXED_WITNESS_EVIDENCE_ACCOUNT",
    "FIXED_WITNESS_EVIDENCE_COLLECTOR_COMMAND",
    "FIXED_WITNESS_EVIDENCE_FORCE_SHELL",
    "FIXED_WITNESS_EVIDENCE_ROOT_COLLECTOR_CONFIG",
    "ReceiptAgentDispatcher",
    "ReceiptAgentDispatcherInvocation",
    "ReceiptAgentDispatcherRunner",
    "ReceiptAgentDispatcherRunnerResult",
    "ReceiptAgentInstallationConfig",
    "ReceiptAgentRuntimeConfig",
    "ReceiptAgentBoundaryError",
    "RenderedReceiptAgentAssets",
    "RenderedReceiptAgentFile",
    "SUPPORTED_RECEIPT_AGENT_ROLES",
    "WitnessEvidenceAgentDispatcher",
    "WitnessEvidenceAgentDispatcherInvocation",
    "WitnessEvidenceAgentDispatcherRunner",
    "WitnessEvidenceAgentDispatcherRunnerResult",
    "WitnessEvidenceAgentRuntimeConfig",
    "agent_source_paths",
    "canonical_installation_config_bytes",
    "canonical_json_document",
    "parse_receipt_agent_installation_config",
    "parse_receipt_agent_authorized_key_bytes",
    "parse_receipt_agent_request_payload",
    "parse_receipt_agent_runtime_config",
    "parse_witness_evidence_agent_runtime_config",
    "render_receipt_agent_assets",
    "witness_evidence_agent_source_paths",
)


DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_SCHEMA = (
    "three-site-dedicated-host-preflight-receipt-agent-installation-v1"
)
DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_RUNTIME_SCHEMA = (
    "three-site-dedicated-host-preflight-receipt-agent-runtime-v1"
)
DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_SCHEMA = (
    "three-site-dedicated-host-preflight-witness-evidence-agent-runtime-v1"
)
DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_DEFAULT_ENABLED = False

SUPPORTED_RECEIPT_AGENT_ROLES = ("bot_fi", "webapp_fi", "witness")
FIXED_PREFLIGHT_ACCOUNT = "preflight"
FIXED_PREFLIGHT_ACCOUNT_HOME = "/nonexistent"
# OpenSSH invokes ForceCommand through the user's login shell with ``-c``.
# ``/usr/sbin/nologin`` would therefore reject the ForceCommand before the
# dispatcher runs.  This root-owned tiny shell is rendered below; it accepts
# only the one literal ForceCommand and rejects every other login/command.
FIXED_PREFLIGHT_FORCE_SHELL = Path(
    "/usr/local/libexec/trading-bot/dedicated-host-preflight/preflight-force-shell"
)
FIXED_PREFLIGHT_ACCOUNT_SHELL = str(FIXED_PREFLIGHT_FORCE_SHELL)
FIXED_PREFLIGHT_COLLECTOR_COMMAND = "collect-readonly-receipt"
FIXED_PREFLIGHT_AGENT_RELEASES_ROOT = Path("/srv/trading-bot-three-site/releases")
FIXED_PREFLIGHT_SYSTEM_PYTHON = Path("/usr/bin/python3")
FIXED_PREFLIGHT_SUDO_BINARY = Path("/usr/bin/sudo")
FIXED_PREFLIGHT_ROOT_COLLECTOR_CONFIG = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/receipt-agent.json"
)
FIXED_PREFLIGHT_AUTHORIZED_KEYS = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-authorized_keys"
)
FIXED_PREFLIGHT_SSHD_CONFIG = Path(
    "/etc/ssh/sshd_config.d/80-trading-bot-dedicated-host-preflight.conf"
)
FIXED_PREFLIGHT_SUDOERS = Path(
    "/etc/sudoers.d/80-trading-bot-dedicated-host-preflight"
)
FIXED_PREFLIGHT_ACCOUNT_POLICY = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-account.json"
)
FIXED_PREFLIGHT_INSTALLATION_ATTESTATION = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-installation.json"
)
FIXED_WITNESS_EVIDENCE_ACCOUNT = "preflight-witness-evidence"
FIXED_WITNESS_EVIDENCE_ACCOUNT_HOME = "/nonexistent"
FIXED_WITNESS_EVIDENCE_COLLECTOR_COMMAND = "collect-wa-ir-witness-preflight-evidence"
FIXED_WITNESS_EVIDENCE_FORCE_SHELL = Path(
    "/usr/local/libexec/trading-bot/dedicated-host-preflight/preflight-witness-evidence-force-shell"
)
FIXED_WITNESS_EVIDENCE_ACCOUNT_SHELL = str(FIXED_WITNESS_EVIDENCE_FORCE_SHELL)
FIXED_WITNESS_EVIDENCE_ROOT_COLLECTOR_CONFIG = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/witness-evidence-agent.json"
)
FIXED_WITNESS_EVIDENCE_AUTHORIZED_KEYS = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/witness-evidence-agent-authorized_keys"
)
FIXED_WITNESS_EVIDENCE_SSHD_CONFIG = Path(
    "/etc/ssh/sshd_config.d/81-trading-bot-dedicated-host-preflight-witness-evidence.conf"
)
FIXED_WITNESS_EVIDENCE_SUDOERS = Path(
    "/etc/sudoers.d/81-trading-bot-dedicated-host-preflight-witness-evidence"
)
FIXED_WITNESS_EVIDENCE_ACCOUNT_POLICY = Path(
    "/etc/trading-bot/security/dedicated-host-preflight/witness-evidence-agent-account.json"
)

_MODE = "read-only"
_TRANSPORT = "pinned-ssh-readonly-agent"
_DIRECT_FINLAND_TO_IR = "forbidden"
_ROOT_COLLECTOR_BOUNDARY = "sudo-fixed-root-collector-no-argv-v1"
_WITNESS_EVIDENCE_ROOT_COLLECTOR_BOUNDARY = "sudo-fixed-witness-evidence-root-collector-no-argv-v1"
_WITNESS_EVIDENCE_MODE = "read-only-witness-evidence"
_WITNESS_EVIDENCE_TRANSPORT = "pinned-ssh-witness-evidence-agent"
_MAX_REQUEST_BYTES = 4 * 1024
_MAX_RENDERED_FILE_BYTES = 64 * 1024
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$", re.ASCII)
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PUBLIC_KEY_TYPE = "ssh-ed25519"
_INSTALLATION_FIELDS = frozenset(
    {
        "schema",
        "enabled",
        "mode",
        "site_role",
        "agent_release_sha",
        "controller_public_key",
        "transport",
        "direct_finland_to_iran",
    }
)
_RUNTIME_FIELDS = frozenset(
    {
        "schema",
        "enabled",
        "mode",
        "site_role",
        "agent_release_sha",
        "transport",
        "direct_finland_to_iran",
    }
)
_WITNESS_EVIDENCE_RUNTIME_FIELDS = frozenset(
    {
        "schema",
        "enabled",
        "mode",
        "site_role",
        "agent_release_sha",
        "transport",
        "direct_finland_to_iran",
    }
)
_REQUEST_FIELDS = frozenset(
    {
        "schema",
        "campaign_id",
        "operation_id",
        "release_sha",
        "role",
        "manifest_sha256",
    }
)


class ReceiptAgentBoundaryError(ValueError):
    """A stable, redacted refusal from the receipt-agent boundary."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise ReceiptAgentBoundaryError(code)


@dataclass(frozen=True)
class ReceiptAgentInstallationConfig:
    """Non-secret, explicit policy for one FI-side receipt-agent install."""

    enabled: bool = DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_DEFAULT_ENABLED
    site_role: str = ""
    agent_release_sha: str = ""
    controller_public_key: str = ""
    schema: str = DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_SCHEMA
    mode: str = _MODE
    transport: str = _TRANSPORT
    direct_finland_to_iran: str = _DIRECT_FINLAND_TO_IR


@dataclass(frozen=True)
class ReceiptAgentRuntimeConfig:
    """The root-only config consumed by the fixed root collector."""

    enabled: bool
    site_role: str
    agent_release_sha: str
    schema: str = DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_RUNTIME_SCHEMA
    mode: str = _MODE
    transport: str = _TRANSPORT
    direct_finland_to_iran: str = _DIRECT_FINLAND_TO_IR


@dataclass(frozen=True)
class WitnessEvidenceAgentRuntimeConfig:
    """The separate Witness-only root collector policy.

    This is intentionally not the normal receipt-agent config: its distinct
    schema and fixed site role prevent a Bot-FI or WA-FI receipt account from
    acquiring a route to the Witness attestation ledger.
    """

    enabled: bool
    site_role: str
    agent_release_sha: str
    schema: str = DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_SCHEMA
    mode: str = _WITNESS_EVIDENCE_MODE
    transport: str = _WITNESS_EVIDENCE_TRANSPORT
    direct_finland_to_iran: str = _DIRECT_FINLAND_TO_IR


@dataclass(frozen=True)
class _RequestFacts:
    raw: bytes
    campaign_id: str
    operation_id: str
    release_sha: str
    role: str
    manifest_sha256: str


@dataclass(frozen=True)
class ReceiptAgentDispatcherInvocation:
    """Exact, no-shell handoff from the unprivileged dispatcher to sudo."""

    arguments: tuple[str, ...]
    stdin_bytes: bytes
    environment: tuple[tuple[str, str], ...]
    agent_release_sha: str
    request_role: str


@dataclass(frozen=True)
class ReceiptAgentDispatcherRunnerResult:
    """Only the bounded stdout and exit code may return from the runner."""

    exit_code: int
    stdout_bytes: bytes


@dataclass(frozen=True)
class WitnessEvidenceAgentDispatcherInvocation:
    """Exact no-input sudo handoff for the selector-free Witness evidence."""

    arguments: tuple[str, ...]
    stdin_bytes: bytes
    environment: tuple[tuple[str, str], ...]
    agent_release_sha: str


@dataclass(frozen=True)
class WitnessEvidenceAgentDispatcherRunnerResult:
    """Only bounded stdout and exit code may return from the local runner."""

    exit_code: int
    stdout_bytes: bytes


class ReceiptAgentDispatcherRunner(Protocol):
    """The concrete dispatcher supplies one no-shell local sudo runner."""

    def run(
        self, *, invocation: ReceiptAgentDispatcherInvocation
    ) -> ReceiptAgentDispatcherRunnerResult: ...


class WitnessEvidenceAgentDispatcherRunner(Protocol):
    """Concrete dispatcher seam for one no-input local sudo command."""

    def run(
        self, *, invocation: WitnessEvidenceAgentDispatcherInvocation
    ) -> WitnessEvidenceAgentDispatcherRunnerResult: ...


@dataclass(frozen=True)
class RenderedReceiptAgentFile:
    """One exact future installation file; rendering performs no write."""

    destination: Path
    content: bytes
    mode: int


@dataclass(frozen=True)
class RenderedReceiptAgentAssets:
    """A small complete set of default-off receipt-agent installation assets."""

    config: ReceiptAgentInstallationConfig
    files: tuple[RenderedReceiptAgentFile, ...]
    installation_sha256: str
    installation_authorized: bool = False
    execution_authorized: bool = False
    promotion_authorized: bool = False

    def file(self, destination: Path) -> bytes:
        for item in self.files:
            if item.destination == destination:
                return item.content
        raise KeyError(destination)


def canonical_json_document(value: object, *, code: str) -> bytes:
    """Canonical ASCII JSON without filesystem, process, or network I/O."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        _fail(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            _fail("PREFLIGHT_RECEIPT_AGENT_JSON_INVALID")
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    _fail("PREFLIGHT_RECEIPT_AGENT_JSON_INVALID")


def _mapping(value: object, *, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return dict(value)


def _sha40(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX40_RE.fullmatch(value) is None or value == "0" * 40:
        _fail(code)
    return value


def _sha64(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX64_RE.fullmatch(value) is None or value == "0" * 64:
        _fail(code)
    return value


def _role(value: object, *, code: str) -> str:
    if type(value) is not str:
        _fail(code)
    if value == "webapp_ir":
        _fail("PREFLIGHT_RECEIPT_AGENT_WEBAPP_IR_FORBIDDEN")
    if value not in SUPPORTED_RECEIPT_AGENT_ROLES:
        _fail(code)
    return value


def _campaign_id(value: object, *, code: str) -> str:
    if type(value) is not str or CAMPAIGN_ID.fullmatch(value) is None:
        _fail(code)
    return value


def _operation_id(value: object, *, code: str) -> str:
    if type(value) is not str:
        _fail(code)
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        _fail(code)
    if parsed.int == 0 or str(parsed) != value:
        _fail(code)
    return value


def _public_key(value: object, *, code: str) -> str:
    if type(value) is not str or value != value.strip() or "\x00" in value:
        _fail(code)
    parts = value.split(" ")
    if len(parts) != 2 or parts[0] != _PUBLIC_KEY_TYPE or not parts[1]:
        _fail(code)
    try:
        wire = base64.b64decode(parts[1].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        _fail(code)
    expected_type = _PUBLIC_KEY_TYPE.encode("ascii")
    if len(wire) < 4:
        _fail(code)
    type_length = int.from_bytes(wire[:4], "big")
    type_end = 4 + type_length
    if (
        type_end + 4 > len(wire)
        or wire[4:type_end] != expected_type
        or int.from_bytes(wire[type_end : type_end + 4], "big") != 32
        or len(wire) != type_end + 4 + 32
    ):
        _fail(code)
    return value


def parse_receipt_agent_authorized_key_bytes(value: object) -> str:
    """Validate the exact staged ``restrict`` key file without filesystem I/O."""

    if type(value) is not bytes or not value or len(value) > 4 * 1024:
        _fail("PREFLIGHT_RECEIPT_AGENT_AUTHORIZED_KEYS_INVALID")
    try:
        text = value.decode("ascii", "strict")
    except UnicodeDecodeError:
        _fail("PREFLIGHT_RECEIPT_AGENT_AUTHORIZED_KEYS_INVALID")
    if not text.endswith("\n") or text.count("\n") != 1 or not text.startswith("restrict "):
        _fail("PREFLIGHT_RECEIPT_AGENT_AUTHORIZED_KEYS_INVALID")
    return _public_key(
        text.removeprefix("restrict ").removesuffix("\n"),
        code="PREFLIGHT_RECEIPT_AGENT_AUTHORIZED_KEYS_INVALID",
    )


def _installation_facts(value: object) -> ReceiptAgentInstallationConfig:
    if type(value) is not ReceiptAgentInstallationConfig:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID")
    if type(value.enabled) is not bool:
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID")
    if (
        value.schema != DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_SCHEMA
        or value.mode != _MODE
        or value.transport != _TRANSPORT
        or value.direct_finland_to_iran != _DIRECT_FINLAND_TO_IR
    ):
        _fail("PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID")
    return ReceiptAgentInstallationConfig(
        enabled=value.enabled,
        site_role=_role(
            value.site_role, code="PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID"
        ),
        agent_release_sha=_sha40(
            value.agent_release_sha,
            code="PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID",
        ),
        controller_public_key=_public_key(
            value.controller_public_key,
            code="PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID",
        ),
    )


def parse_receipt_agent_installation_config(value: object) -> ReceiptAgentInstallationConfig:
    """Parse the exact non-secret root-owned install request document."""

    item = _mapping(
        value,
        fields=_INSTALLATION_FIELDS,
        code="PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID",
    )
    return _installation_facts(
        ReceiptAgentInstallationConfig(
            schema=item["schema"],
            enabled=item["enabled"],
            mode=item["mode"],
            site_role=item["site_role"],
            agent_release_sha=item["agent_release_sha"],
            controller_public_key=item["controller_public_key"],
            transport=item["transport"],
            direct_finland_to_iran=item["direct_finland_to_iran"],
        )
    )


def canonical_installation_config_bytes(value: ReceiptAgentInstallationConfig) -> bytes:
    """Encode one validated installer config for a root-only request file."""

    facts = _installation_facts(value)
    return canonical_json_document(
        {
            "schema": facts.schema,
            "enabled": facts.enabled,
            "mode": facts.mode,
            "site_role": facts.site_role,
            "agent_release_sha": facts.agent_release_sha,
            "controller_public_key": facts.controller_public_key,
            "transport": facts.transport,
            "direct_finland_to_iran": facts.direct_finland_to_iran,
        },
        code="PREFLIGHT_RECEIPT_AGENT_INSTALLATION_CONFIG_INVALID",
    ) + b"\n"


def parse_receipt_agent_runtime_config(value: object) -> ReceiptAgentRuntimeConfig:
    """Validate the fixed root-only runtime config read by the collector."""

    item = _mapping(
        value,
        fields=_RUNTIME_FIELDS,
        code="PREFLIGHT_RECEIPT_AGENT_RUNTIME_CONFIG_INVALID",
    )
    if (
        item["schema"] != DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_RUNTIME_SCHEMA
        or type(item["enabled"]) is not bool
        or item["mode"] != _MODE
        or item["transport"] != _TRANSPORT
        or item["direct_finland_to_iran"] != _DIRECT_FINLAND_TO_IR
    ):
        _fail("PREFLIGHT_RECEIPT_AGENT_RUNTIME_CONFIG_INVALID")
    return ReceiptAgentRuntimeConfig(
        enabled=item["enabled"],
        site_role=_role(item["site_role"], code="PREFLIGHT_RECEIPT_AGENT_RUNTIME_CONFIG_INVALID"),
        agent_release_sha=_sha40(
            item["agent_release_sha"],
            code="PREFLIGHT_RECEIPT_AGENT_RUNTIME_CONFIG_INVALID",
        ),
    )


def parse_witness_evidence_agent_runtime_config(value: object) -> WitnessEvidenceAgentRuntimeConfig:
    """Validate the fixed Witness-only root collector policy."""

    item = _mapping(
        value,
        fields=_WITNESS_EVIDENCE_RUNTIME_FIELDS,
        code="PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_CONFIG_INVALID",
    )
    if (
        item["schema"] != DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_SCHEMA
        or type(item["enabled"]) is not bool
        or item["mode"] != _WITNESS_EVIDENCE_MODE
        or item["transport"] != _WITNESS_EVIDENCE_TRANSPORT
        or item["direct_finland_to_iran"] != _DIRECT_FINLAND_TO_IR
        or item["site_role"] != "witness"
    ):
        _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_CONFIG_INVALID")
    return WitnessEvidenceAgentRuntimeConfig(
        enabled=item["enabled"],
        site_role="witness",
        agent_release_sha=_sha40(
            item["agent_release_sha"],
            code="PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_CONFIG_INVALID",
        ),
    )


def _runtime_config_bytes(config: ReceiptAgentInstallationConfig) -> bytes:
    return canonical_json_document(
        {
            "schema": DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_RUNTIME_SCHEMA,
            "enabled": config.enabled,
            "mode": _MODE,
            "site_role": config.site_role,
            "agent_release_sha": config.agent_release_sha,
            "transport": _TRANSPORT,
            "direct_finland_to_iran": _DIRECT_FINLAND_TO_IR,
        },
        code="PREFLIGHT_RECEIPT_AGENT_RUNTIME_CONFIG_INVALID",
    ) + b"\n"


def _witness_evidence_runtime_config_bytes(config: ReceiptAgentInstallationConfig) -> bytes:
    if config.site_role != "witness":
        _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_RENDER_INVALID")
    return canonical_json_document(
        {
            "schema": DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_SCHEMA,
            "enabled": config.enabled,
            "mode": _WITNESS_EVIDENCE_MODE,
            "site_role": "witness",
            "agent_release_sha": config.agent_release_sha,
            "transport": _WITNESS_EVIDENCE_TRANSPORT,
            "direct_finland_to_iran": _DIRECT_FINLAND_TO_IR,
        },
        code="PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_CONFIG_INVALID",
    ) + b"\n"


def agent_source_paths(agent_release_sha: object) -> tuple[Path, Path, Path]:
    """Derive all executable paths from one SHA; callers cannot pass a path."""

    release_sha = _sha40(agent_release_sha, code="PREFLIGHT_RECEIPT_AGENT_RELEASE_INVALID")
    root = FIXED_PREFLIGHT_AGENT_RELEASES_ROOT / release_sha
    return (
        root / "scripts" / "run_dedicated_host_preflight_receipt_dispatcher.py",
        root / "scripts" / "run_dedicated_host_preflight_root_collector.py",
        root / "scripts" / "run_dedicated_host_readonly_preflight.py",
    )


def witness_evidence_agent_source_paths(agent_release_sha: object) -> tuple[Path, Path]:
    """Derive only the separate Witness evidence dispatcher/collector paths."""

    release_sha = _sha40(
        agent_release_sha,
        code="PREFLIGHT_WITNESS_EVIDENCE_AGENT_RELEASE_INVALID",
    )
    root = FIXED_PREFLIGHT_AGENT_RELEASES_ROOT / release_sha
    return (
        root / "scripts" / "run_dedicated_host_preflight_witness_evidence_dispatcher.py",
        root / "scripts" / "run_dedicated_host_preflight_witness_evidence_root_collector.py",
    )


def parse_receipt_agent_request_payload(value: object) -> _RequestFacts:
    """Parse one exact FI-side canonical request before it can reach sudo."""

    if type(value) is not bytes or not 1 <= len(value) <= _MAX_REQUEST_BYTES:
        _fail("PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID")
    try:
        parsed = json.loads(
            value.decode("ascii", "strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except ReceiptAgentBoundaryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID")
    if type(parsed) is not dict or canonical_json_bytes(parsed) + b"\n" != value:
        _fail("PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID")
    item = _mapping(
        parsed,
        fields=_REQUEST_FIELDS,
        code="PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID",
    )
    if item["schema"] != READONLY_REQUEST_SCHEMA:
        _fail("PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID")
    return _RequestFacts(
        raw=value,
        campaign_id=_campaign_id(item["campaign_id"], code="PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID"),
        operation_id=_operation_id(item["operation_id"], code="PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID"),
        release_sha=_sha40(item["release_sha"], code="PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID"),
        role=_role(item["role"], code="PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID"),
        manifest_sha256=_sha64(
            item["manifest_sha256"], code="PREFLIGHT_RECEIPT_AGENT_REQUEST_INVALID"
        ),
    )


def _sshd_config(config: ReceiptAgentInstallationConfig) -> bytes:
    dispatcher, _root_collector, _collector = agent_source_paths(config.agent_release_sha)
    forced = f"exec {FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {dispatcher}"
    lines = (
        "# Generated by the dedicated-host preflight receipt-agent renderer.",
        "# This Match block is intentionally limited to the unprivileged account.",
        f"Match User {FIXED_PREFLIGHT_ACCOUNT}",
        "    AuthenticationMethods publickey",
        "    PubkeyAuthentication yes",
        "    PasswordAuthentication no",
        "    KbdInteractiveAuthentication no",
        "    ChallengeResponseAuthentication no",
        "    PermitEmptyPasswords no",
        f"    AuthorizedKeysFile {FIXED_PREFLIGHT_AUTHORIZED_KEYS}",
        "    PermitTTY no",
        "    PermitUserRC no",
        "    PermitUserEnvironment no",
        "    X11Forwarding no",
        "    AllowAgentForwarding no",
        "    AllowTcpForwarding no",
        "    AllowStreamLocalForwarding no",
        "    GatewayPorts no",
        "    PermitTunnel no",
        "    PermitOpen none",
        "    PermitListen none",
        "    DisableForwarding yes",
        "    MaxSessions 1",
        f"    ForceCommand {forced}",
        "Match all",
        "",
    )
    return "\n".join(lines).encode("ascii")


def _sudoers(config: ReceiptAgentInstallationConfig) -> bytes:
    _dispatcher, root_collector, _collector = agent_source_paths(config.agent_release_sha)
    # In sudoers, a trailing ``\"\"`` means that this exact command has no
    # caller arguments.  Do not replace it with a bare path: a bare command
    # permits arbitrary arguments in sudoers matching semantics.
    lines = (
        "# Generated by the dedicated-host preflight receipt-agent renderer.",
        f"Defaults:{FIXED_PREFLIGHT_ACCOUNT} env_reset",
        f"Defaults:{FIXED_PREFLIGHT_ACCOUNT} !setenv",
        f"Defaults:{FIXED_PREFLIGHT_ACCOUNT} secure_path=\"/usr/sbin:/usr/bin:/sbin:/bin\"",
        (
            "Cmnd_Alias TRADING_BOT_PREFLIGHT_ROOT_COLLECTOR = "
            f"{FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {root_collector} \"\""
        ),
        (
            f"{FIXED_PREFLIGHT_ACCOUNT} ALL=(root) "
            "NOPASSWD:NOSETENV: TRADING_BOT_PREFLIGHT_ROOT_COLLECTOR"
        ),
        "",
    )
    return "\n".join(lines).encode("ascii")


def _authorized_keys(config: ReceiptAgentInstallationConfig) -> bytes:
    # ``restrict`` denies forwarding, PTY, agent, X11, and user-rc features
    # even if a future sshd global setting regresses.  The sshd Match block is
    # a second independently rendered denial and supplies the only command.
    return ("restrict " + config.controller_public_key + "\n").encode("ascii")


def _force_shell(config: ReceiptAgentInstallationConfig) -> bytes:
    """Render the account shell that permits only the exact sshd command.

    sshd uses the login shell as ``shell -c <ForceCommand>``.  The script has
    no branch that evaluates client input; it compares the complete command
    string and directly execs the fixed Python dispatcher only on equality.
    """

    dispatcher, _root_collector, _collector = agent_source_paths(config.agent_release_sha)
    forced = f"exec {FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {dispatcher}"
    lines = (
        "#!/bin/sh",
        "# Root-owned login shell for the dedicated preflight SSH account.",
        "# Do not accept interactive login or a client-selected command.",
        f"if [ \"$#\" -eq 2 ] && [ \"$1\" = \"-c\" ] && [ \"$2\" = '{forced}' ]; then",
        f"    exec {FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {dispatcher}",
        "fi",
        "exit 126",
        "",
    )
    return "\n".join(lines).encode("ascii")


def _account_policy(config: ReceiptAgentInstallationConfig) -> bytes:
    return canonical_json_document(
        {
            "schema": DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_SCHEMA,
            "account": FIXED_PREFLIGHT_ACCOUNT,
            "home": FIXED_PREFLIGHT_ACCOUNT_HOME,
            "shell": FIXED_PREFLIGHT_ACCOUNT_SHELL,
            "shell_policy": "only-exact-sshd-forcecommand-v1",
            "supplementary_groups": [],
            "interactive_login": False,
            "site_role": config.site_role,
            "webapp_ir_supported": False,
            "host_mutation_authorized": False,
            "promotion_authorized": False,
        },
        code="PREFLIGHT_RECEIPT_AGENT_RENDER_INVALID",
    ) + b"\n"


def _witness_evidence_sshd_config(config: ReceiptAgentInstallationConfig) -> bytes:
    dispatcher, _root_collector = witness_evidence_agent_source_paths(config.agent_release_sha)
    forced = f"exec {FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {dispatcher}"
    lines = (
        "# Generated Witness-only preflight evidence SSH boundary.",
        "# This account cannot collect ordinary receipts or accept arbitrary commands.",
        f"Match User {FIXED_WITNESS_EVIDENCE_ACCOUNT}",
        "    AuthenticationMethods publickey",
        "    PubkeyAuthentication yes",
        "    PasswordAuthentication no",
        "    KbdInteractiveAuthentication no",
        "    ChallengeResponseAuthentication no",
        "    PermitEmptyPasswords no",
        f"    AuthorizedKeysFile {FIXED_WITNESS_EVIDENCE_AUTHORIZED_KEYS}",
        "    PermitTTY no",
        "    PermitUserRC no",
        "    PermitUserEnvironment no",
        "    X11Forwarding no",
        "    AllowAgentForwarding no",
        "    AllowTcpForwarding no",
        "    AllowStreamLocalForwarding no",
        "    GatewayPorts no",
        "    PermitTunnel no",
        "    PermitOpen none",
        "    PermitListen none",
        "    DisableForwarding yes",
        "    MaxSessions 1",
        f"    ForceCommand {forced}",
        "Match all",
        "",
    )
    return "\n".join(lines).encode("ascii")


def _witness_evidence_sudoers(config: ReceiptAgentInstallationConfig) -> bytes:
    _dispatcher, root_collector = witness_evidence_agent_source_paths(config.agent_release_sha)
    lines = (
        "# Generated Witness-only preflight evidence sudo boundary.",
        f"Defaults:{FIXED_WITNESS_EVIDENCE_ACCOUNT} env_reset",
        f"Defaults:{FIXED_WITNESS_EVIDENCE_ACCOUNT} !setenv",
        f"Defaults:{FIXED_WITNESS_EVIDENCE_ACCOUNT} secure_path=\"/usr/sbin:/usr/bin:/sbin:/bin\"",
        (
            "Cmnd_Alias TRADING_BOT_WITNESS_PREFLIGHT_EVIDENCE_ROOT_COLLECTOR = "
            f"{FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {root_collector} \"\""
        ),
        (
            f"{FIXED_WITNESS_EVIDENCE_ACCOUNT} ALL=(root) "
            "NOPASSWD:NOSETENV: TRADING_BOT_WITNESS_PREFLIGHT_EVIDENCE_ROOT_COLLECTOR"
        ),
        "",
    )
    return "\n".join(lines).encode("ascii")


def _witness_evidence_force_shell(config: ReceiptAgentInstallationConfig) -> bytes:
    dispatcher, _root_collector = witness_evidence_agent_source_paths(config.agent_release_sha)
    forced = f"exec {FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {dispatcher}"
    lines = (
        "#!/bin/sh",
        "# Root-owned login shell for the literal Witness evidence SSH account.",
        "# Do not accept interactive login or a client-selected command.",
        f"if [ \"$#\" -eq 2 ] && [ \"$1\" = \"-c\" ] && [ \"$2\" = '{forced}' ]; then",
        f"    exec {FIXED_PREFLIGHT_SYSTEM_PYTHON} -I {dispatcher}",
        "fi",
        "exit 126",
        "",
    )
    return "\n".join(lines).encode("ascii")


def _witness_evidence_account_policy(config: ReceiptAgentInstallationConfig) -> bytes:
    if config.site_role != "witness":
        _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_RENDER_INVALID")
    return canonical_json_document(
        {
            "schema": DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_AGENT_RUNTIME_SCHEMA,
            "account": FIXED_WITNESS_EVIDENCE_ACCOUNT,
            "home": FIXED_WITNESS_EVIDENCE_ACCOUNT_HOME,
            "shell": FIXED_WITNESS_EVIDENCE_ACCOUNT_SHELL,
            "shell_policy": "only-exact-sshd-forcecommand-v1",
            "supplementary_groups": [],
            "interactive_login": False,
            "site_role": "witness",
            "ordinary_receipt_supported": False,
            "webapp_ir_host_access": False,
            "witness_ledger_selector": "none",
            "host_mutation_authorized": False,
            "promotion_authorized": False,
        },
        code="PREFLIGHT_WITNESS_EVIDENCE_AGENT_RENDER_INVALID",
    ) + b"\n"


def _witness_evidence_assets(
    config: ReceiptAgentInstallationConfig,
) -> tuple[RenderedReceiptAgentFile, ...]:
    """Render an entirely separate account only on the Witness site."""

    if config.site_role != "witness":
        return ()
    return (
        RenderedReceiptAgentFile(
            destination=FIXED_WITNESS_EVIDENCE_ROOT_COLLECTOR_CONFIG,
            content=_witness_evidence_runtime_config_bytes(config),
            mode=0o600,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_WITNESS_EVIDENCE_AUTHORIZED_KEYS,
            content=_authorized_keys(config),
            mode=0o644,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_WITNESS_EVIDENCE_SSHD_CONFIG,
            content=_witness_evidence_sshd_config(config),
            mode=0o644,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_WITNESS_EVIDENCE_SUDOERS,
            content=_witness_evidence_sudoers(config),
            mode=0o440,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_WITNESS_EVIDENCE_ACCOUNT_POLICY,
            content=_witness_evidence_account_policy(config),
            mode=0o600,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_WITNESS_EVIDENCE_FORCE_SHELL,
            content=_witness_evidence_force_shell(config),
            mode=0o755,
        ),
    )


def _installation_attestation(
    *, config: ReceiptAgentInstallationConfig, rendered: Sequence[RenderedReceiptAgentFile]
) -> bytes:
    dispatcher, root_collector, collector = agent_source_paths(config.agent_release_sha)
    witness_dispatcher, witness_root_collector = witness_evidence_agent_source_paths(
        config.agent_release_sha
    )
    return canonical_json_document(
        {
            "schema": DEDICATED_HOST_PREFLIGHT_RECEIPT_AGENT_INSTALLATION_SCHEMA,
            "status": "default-off-rendered" if not config.enabled else "enabled-read-only-rendered",
            "site_role": config.site_role,
            "agent_release_sha": config.agent_release_sha,
            "dispatcher_path": str(dispatcher),
            "root_collector_path": str(root_collector),
            "readonly_collector_path": str(collector),
            "root_collector_boundary": _ROOT_COLLECTOR_BOUNDARY,
            "witness_evidence_endpoint": config.site_role == "witness",
            "witness_evidence_dispatcher_path": (
                str(witness_dispatcher) if config.site_role == "witness" else None
            ),
            "witness_evidence_root_collector_path": (
                str(witness_root_collector) if config.site_role == "witness" else None
            ),
            "witness_evidence_root_collector_boundary": (
                _WITNESS_EVIDENCE_ROOT_COLLECTOR_BOUNDARY
                if config.site_role == "witness"
                else None
            ),
            "direct_finland_to_iran": _DIRECT_FINLAND_TO_IR,
            "webapp_ir_supported": False,
            "not_a_host_mutation_authorization": True,
            "not_a_promotion_authorization": True,
            "files": [
                {
                    "path": str(item.destination),
                    "mode": format(item.mode, "04o"),
                    "sha256": hashlib.sha256(item.content).hexdigest(),
                }
                for item in rendered
            ],
        },
        code="PREFLIGHT_RECEIPT_AGENT_RENDER_INVALID",
    ) + b"\n"


def render_receipt_agent_assets(value: ReceiptAgentInstallationConfig) -> RenderedReceiptAgentAssets:
    """Render exact root-owned files without touching a host filesystem."""

    config = _installation_facts(value)
    primary = (
        RenderedReceiptAgentFile(
            destination=FIXED_PREFLIGHT_ROOT_COLLECTOR_CONFIG,
            content=_runtime_config_bytes(config),
            mode=0o600,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_PREFLIGHT_AUTHORIZED_KEYS,
            content=_authorized_keys(config),
            mode=0o644,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_PREFLIGHT_SSHD_CONFIG,
            content=_sshd_config(config),
            mode=0o644,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_PREFLIGHT_SUDOERS,
            content=_sudoers(config),
            mode=0o440,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_PREFLIGHT_ACCOUNT_POLICY,
            content=_account_policy(config),
            mode=0o600,
        ),
        RenderedReceiptAgentFile(
            destination=FIXED_PREFLIGHT_FORCE_SHELL,
            content=_force_shell(config),
            mode=0o755,
        ),
    ) + _witness_evidence_assets(config)
    if any(not 1 <= len(item.content) <= _MAX_RENDERED_FILE_BYTES for item in primary):
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INVALID")
    attestation = RenderedReceiptAgentFile(
        destination=FIXED_PREFLIGHT_INSTALLATION_ATTESTATION,
        content=_installation_attestation(config=config, rendered=primary),
        mode=0o600,
    )
    files = primary + (attestation,)
    if len({item.destination for item in files}) != len(files):
        _fail("PREFLIGHT_RECEIPT_AGENT_RENDER_INVALID")
    digest = hashlib.sha256(
        canonical_json_document(
            [
                {
                    "path": str(item.destination),
                    "mode": format(item.mode, "04o"),
                    "sha256": hashlib.sha256(item.content).hexdigest(),
                }
                for item in files
            ],
            code="PREFLIGHT_RECEIPT_AGENT_RENDER_INVALID",
        )
    ).hexdigest()
    return RenderedReceiptAgentAssets(
        config=config,
        files=files,
        installation_sha256=digest,
    )


class ReceiptAgentDispatcher:
    """Pure dispatcher policy with one injectable local sudo runner.

    The real dispatcher script supplies the process runner.  Tests inject a
    local fake runner, so this object itself has no subprocess or SSH ability.
    It deliberately does not open the root-only runtime config: only the root
    collector can decide whether the local agent is enabled or role-bound.
    """

    _CLEAN_ENV = (
        ("HOME", "/nonexistent"),
        ("LANG", "C"),
        ("LC_ALL", "C"),
        ("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
    )

    def __init__(
        self,
        *,
        agent_release_sha: str,
        runner: ReceiptAgentDispatcherRunner | None = None,
    ) -> None:
        self._agent_release_sha = _sha40(
            agent_release_sha, code="PREFLIGHT_RECEIPT_AGENT_RELEASE_INVALID"
        )
        self._runner = runner

    def dispatch(
        self,
        *,
        original_command: object,
        arguments: object,
        account_name: object,
        account_uid: object,
        request_bytes: object,
    ) -> bytes:
        """Validate one forced exchange and return only its canonical receipt."""

        if (
            original_command != FIXED_PREFLIGHT_COLLECTOR_COMMAND
            or type(arguments) is not tuple
            or arguments != ()
            or account_name != FIXED_PREFLIGHT_ACCOUNT
            or type(account_uid) is not int
            or account_uid <= 0
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_DISPATCH_FORBIDDEN")
        request = parse_receipt_agent_request_payload(request_bytes)
        if self._runner is None or not callable(getattr(self._runner, "run", None)):
            _fail("PREFLIGHT_RECEIPT_AGENT_DISPATCH_RUNNER_REQUIRED")
        _dispatcher, root_collector, _collector = agent_source_paths(
            self._agent_release_sha
        )
        invocation = ReceiptAgentDispatcherInvocation(
            arguments=(
                str(FIXED_PREFLIGHT_SUDO_BINARY),
                "-n",
                "-u",
                "root",
                "--",
                str(FIXED_PREFLIGHT_SYSTEM_PYTHON),
                "-I",
                str(root_collector),
            ),
            stdin_bytes=request.raw,
            environment=self._CLEAN_ENV,
            agent_release_sha=self._agent_release_sha,
            request_role=request.role,
        )
        try:
            result = self._runner.run(invocation=invocation)
        except Exception:
            _fail("PREFLIGHT_RECEIPT_AGENT_DISPATCH_RUNNER_FAILED")
        if (
            type(result) is not ReceiptAgentDispatcherRunnerResult
            or type(result.exit_code) is not int
            or result.exit_code != 0
            or type(result.stdout_bytes) is not bytes
            or not 1 <= len(result.stdout_bytes) <= MAX_RECEIPT_BYTES
        ):
            _fail("PREFLIGHT_RECEIPT_AGENT_DISPATCH_RUNNER_FAILED")
        try:
            parse_preflight_receipt(
                result.stdout_bytes,
                expected_role=request.role,
                expected_campaign_id=request.campaign_id,
                expected_operation_id=request.operation_id,
                expected_manifest_sha256=request.manifest_sha256,
            )
        except Exception:
            _fail("PREFLIGHT_RECEIPT_AGENT_DISPATCH_RECEIPT_INVALID")
        return result.stdout_bytes


class WitnessEvidenceAgentDispatcher:
    """Separate literal-only dispatcher for selector-free Witness evidence.

    It has no request parser and cannot route a WA-IR receipt request.  The
    sole accepted exchange has empty stdin and asks the fixed root collector
    to read one already persisted evidence record from the local Witness
    ledger.  The central verifier must still verify both signatures before it
    accepts the returned inner receipt.
    """

    _CLEAN_ENV = ReceiptAgentDispatcher._CLEAN_ENV

    def __init__(
        self,
        *,
        agent_release_sha: str,
        runner: WitnessEvidenceAgentDispatcherRunner | None = None,
    ) -> None:
        self._agent_release_sha = _sha40(
            agent_release_sha,
            code="PREFLIGHT_WITNESS_EVIDENCE_AGENT_RELEASE_INVALID",
        )
        self._runner = runner

    def dispatch(
        self,
        *,
        original_command: object,
        arguments: object,
        account_name: object,
        account_uid: object,
        stdin_bytes: object,
    ) -> bytes:
        """Run the one literal no-selector evidence collection handoff."""

        if (
            original_command != FIXED_WITNESS_EVIDENCE_COLLECTOR_COMMAND
            or type(arguments) is not tuple
            or arguments != ()
            or account_name != FIXED_WITNESS_EVIDENCE_ACCOUNT
            or type(account_uid) is not int
            or account_uid <= 0
            or stdin_bytes != b""
        ):
            _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_DISPATCH_FORBIDDEN")
        if self._runner is None or not callable(getattr(self._runner, "run", None)):
            _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_DISPATCH_RUNNER_REQUIRED")
        _dispatcher, root_collector = witness_evidence_agent_source_paths(
            self._agent_release_sha
        )
        invocation = WitnessEvidenceAgentDispatcherInvocation(
            arguments=(
                str(FIXED_PREFLIGHT_SUDO_BINARY),
                "-n",
                "-u",
                "root",
                "--",
                str(FIXED_PREFLIGHT_SYSTEM_PYTHON),
                "-I",
                str(root_collector),
            ),
            stdin_bytes=b"",
            environment=self._CLEAN_ENV,
            agent_release_sha=self._agent_release_sha,
        )
        try:
            result = self._runner.run(invocation=invocation)
        except Exception:
            _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_DISPATCH_RUNNER_FAILED")
        maximum = MAX_WA_IR_WITNESS_ATTESTATION_BYTES * 2
        if (
            type(result) is not WitnessEvidenceAgentDispatcherRunnerResult
            or type(result.exit_code) is not int
            or result.exit_code != 0
            or type(result.stdout_bytes) is not bytes
            or not 1 <= len(result.stdout_bytes) <= maximum
        ):
            _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_DISPATCH_RUNNER_FAILED")
        try:
            parsed = json.loads(
                result.stdout_bytes.decode("ascii", "strict"),
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except ReceiptAgentBoundaryError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_DISPATCH_EVIDENCE_INVALID")
        if (
            type(parsed) is not dict
            or parsed.get("schema") != DEDICATED_HOST_PREFLIGHT_WITNESS_EVIDENCE_SCHEMA
            or canonical_json_bytes(parsed) + b"\n" != result.stdout_bytes
        ):
            _fail("PREFLIGHT_WITNESS_EVIDENCE_AGENT_DISPATCH_EVIDENCE_INVALID")
        return result.stdout_bytes
