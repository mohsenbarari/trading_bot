#!/usr/bin/env python3
"""Freeze, prove, and restore the exact legacy production writer set.

The command is plan-only by default. Apply mode is deliberately host-local and
accepts only a frozen-final source binding plus the installed, release-bound
Nginx generation material. It can stop or restart only the canonical legacy
writer container IDs captured in its root-only journal. PostgreSQL and Redis
remain running throughout.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import secrets
import stat
import subprocess
import sys
import time
from typing import Any, BinaryIO, Callable, Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.secure_file_io import (  # noqa: E402
    SecureFileError,
    read_secure_bytes,
    write_secure_atomic_bytes,
    write_secure_new_bytes,
)
from scripts import produce_production_shadow_source_snapshot as SOURCE  # noqa: E402
from scripts import (  # noqa: E402
    orchestrate_production_shadow_nginx_generations as NGINX_COORDINATOR,
)
from scripts import production_shadow_nginx_generation as NGINX  # noqa: E402


JOURNAL_SCHEMA = "production-shadow-legacy-writer-freeze-journal-v3"
RESULT_SCHEMA = "production-shadow-legacy-writer-freeze-result-v3"
PLAN_SCHEMA = "production-shadow-legacy-writer-freeze-plan-v3"
LIVE_CHECKPOINT_CHALLENGE_SCHEMA = (
    "production-shadow-legacy-writer-live-checkpoint-challenge-v1"
)
LIVE_CHECKPOINT_RESPONSE_SCHEMA = (
    "production-shadow-legacy-writer-live-checkpoint-response-v1"
)
CAPTURE_OWNER_ACTION = "capture-frozen-final-snapshots"
RESTORE_OWNER_ACTION = "restore-legacy-writers"
SECRET_ROOT_PREFIX = Path(
    "/root/secure-envs/trading-bot/three-site-production-shadow"
)
STATE_DIRECTORY_NAME = "legacy-writer-freeze"
JOURNAL_FILENAME = "journal.json"
LOCK_FILENAME = "lock"
EVIDENCE_FILENAME = "source-freeze-evidence.json"
DOCKER = "/usr/bin/docker"
CURL = "/usr/bin/curl"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_COMMAND_ERROR_BYTES = 256 * 1024
READINESS_HTTP_ATTEMPTS = 20
READINESS_STABILITY_ATTEMPTS = 6
READINESS_STABLE_SAMPLES = 3
READINESS_RETRY_SECONDS = 2.0
LEGACY_API_READY_URL = "http://127.0.0.1:8000/api/config"
ZERO_SHA256 = "0" * 64
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
PROCESS_COMM_RE = re.compile(r"^[!-~]{1,256}$")
CHECKPOINT_RE = re.compile(
    r"^(?:before-stop|after-stop|before-start|after-start):"
    r"(?:application|bot|sync_worker)$"
    r"|^readiness-(?:http|stability):[1-9][0-9]{0,2}$"
    r"|^before-result$"
)
LIVE_CHECKPOINT_RESPONSE_TIMEOUT_SECONDS = 30.0
MAX_LIVE_CHECKPOINT_LINE_BYTES = 64 * 1024

ROLE_WRITERS: Mapping[str, tuple[tuple[str, str, str], ...]] = {
    "bot_fi": (
        ("application", "trading_bot_app", "app"),
        ("bot", "trading_bot_bot", "bot"),
        ("sync_worker", "trading_bot_sync_worker", "sync_worker"),
    ),
    "webapp_fi": (
        ("application", "trading_bot_app", "app"),
        ("sync_worker", "trading_bot_sync_worker", "sync_worker"),
    ),
}
ROLE_SERVICE_ENV = {
    "application": "api",
    "bot": "bot",
    "sync_worker": "sync_worker",
}
DATA_KINDS = ("database", "redis")
ALLOWED_RUNNING_SERVICES: Mapping[str, frozenset[str]] = {
    role: frozenset(
        {"db", "redis", *(service for _kind, _name, service in writers)}
    )
    for role, writers in ROLE_WRITERS.items()
}

JOURNAL_FIELDS = frozenset(
    {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "legacy_release_sha",
        "role",
        "source_project",
        "binding_sha256",
        "controller_manifest_sha256",
        "approval_sha256",
        "release_tree_sha",
        "nginx_aggregate_sha256",
        "nginx_manifest_sha256",
        "coordinated_state_receipt_sha256",
        "coordinated_state_receipt_history",
        "live_lease_claim_sha256",
        "live_lease_claim_history",
        "live_lease_claim_epoch",
        "live_lease_claim_epoch_history",
        "role_freeze_generation_sha256",
        "freeze_generation_sha256",
        "source_container_ids",
        "writer_containers",
        "previously_running",
        "stopped",
        "last_error_sha256",
        "failure_history",
        "interactive_lease_checkpoint_count",
        "interactive_lease_transcript",
        "interactive_lease_transcript_sha256",
        "interactive_lease_authority_handoff_complete",
        "sequence",
        "state_sha256",
    }
)
WRITER_IDENTITY_FIELDS = frozenset(
    {"id", "name", "service", "image_id", "release_sha"}
)
JOURNAL_STATUSES = frozenset(
    {
        "prepared",
        "freezing",
        "frozen",
        "restoring",
        "active",
        "compensation-failed",
        "reconciliation-required",
        "restore-readiness-failed",
    }
)
SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "DOCKER_CONFIG": "/nonexistent",
    "PYTHONDONTWRITEBYTECODE": "1",
}
DB_CLIENT_SQL = (
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE pid <> pg_backend_pid() "
    "AND datname = current_database() "
    "AND backend_type = 'client backend'"
)


class LegacyWriterFreezeError(RuntimeError):
    """The legacy writer surface could not be proven or changed safely."""


Runner = Callable[[Sequence[str], int], str]
SleepFn = Callable[[float], None]
CheckpointExchange = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LegacyWriterFreezeError("value is not canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid constant: {token}")
            ),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise LegacyWriterFreezeError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise LegacyWriterFreezeError(f"{label} must contain one JSON object")
    if canonical_json(value) != raw:
        raise LegacyWriterFreezeError(f"{label} is not canonical JSON")
    return value


def _nonzero_sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or SHA256_RE.fullmatch(value) is None
        or value == ZERO_SHA256
    ):
        raise LegacyWriterFreezeError(f"{label} is invalid")
    return value


def _checkpoint_entry_sha256(entry: Mapping[str, Any]) -> str:
    unsigned = dict(entry)
    unsigned["entry_sha256"] = ZERO_SHA256
    return hashlib.sha256(canonical_json(unsigned)).hexdigest()


class LiveLeaseCheckpointProtocol:
    """Host-owned nonce protocol proving on-demand controller liveness."""

    def __init__(
        self,
        *,
        binding: SOURCE.SnapshotBinding,
        claim_sha256: str,
        claim_epoch: int,
        exchange: CheckpointExchange,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        if type(claim_epoch) is not int or claim_epoch < 1:
            raise LegacyWriterFreezeError(
                "interactive live lease claim epoch is invalid"
            )
        self._binding = binding
        self._claim_sha256 = _nonzero_sha256(
            claim_sha256,
            label="interactive live lease claim digest",
        )
        self._claim_epoch = claim_epoch
        self._exchange = exchange
        self._nonce_factory = (
            nonce_factory
            if nonce_factory is not None
            else lambda: secrets.token_hex(32)
        )
        self._transcript: list[dict[str, Any]] = []
        self._tail = ZERO_SHA256

    def checkpoint(self, checkpoint: str) -> None:
        if (
            not isinstance(checkpoint, str)
            or CHECKPOINT_RE.fullmatch(checkpoint) is None
            or (
                checkpoint.endswith(":bot")
                and self._binding.role != "bot_fi"
            )
            or (
                checkpoint == "before-result"
                and any(
                    entry["challenge"]["checkpoint"] == "before-result"
                    for entry in self._transcript
                )
            )
        ):
            raise LegacyWriterFreezeError(
                "interactive live lease checkpoint is invalid"
            )
        nonce = self._nonce_factory()
        if (
            not isinstance(nonce, str)
            or SHA256_RE.fullmatch(nonce) is None
            or nonce == ZERO_SHA256
        ):
            raise LegacyWriterFreezeError(
                "interactive live lease challenge nonce is invalid"
            )
        sequence = len(self._transcript) + 1
        challenge = {
            "schema": LIVE_CHECKPOINT_CHALLENGE_SCHEMA,
            "status": "controller-response-required",
            "operation_id": self._binding.operation_id,
            "release_sha": self._binding.release_sha,
            "role": self._binding.role,
            "live_lease_claim_sha256": self._claim_sha256,
            "live_lease_claim_epoch": self._claim_epoch,
            "sequence": sequence,
            "checkpoint": checkpoint,
            "challenge_nonce": nonce,
            "previous_transcript_sha256": self._tail,
        }
        challenge_sha256 = hashlib.sha256(
            canonical_json(challenge)
        ).hexdigest()
        try:
            raw_response = self._exchange(challenge)
        except LegacyWriterFreezeError:
            raise
        except BaseException as exc:
            raise LegacyWriterFreezeError(
                "interactive live lease exchange failed"
            ) from exc
        response = dict(raw_response)
        expected = {
            "schema": LIVE_CHECKPOINT_RESPONSE_SCHEMA,
            "status": "controller-flock-verified",
            "operation_id": self._binding.operation_id,
            "release_sha": self._binding.release_sha,
            "role": self._binding.role,
            "live_lease_claim_sha256": self._claim_sha256,
            "live_lease_claim_epoch": self._claim_epoch,
            "sequence": sequence,
            "checkpoint": checkpoint,
            "challenge_nonce": nonce,
            "challenge_sha256": challenge_sha256,
            "previous_transcript_sha256": self._tail,
            "controller_flock_verified": True,
        }
        response_nonce = response.get("response_nonce")
        if (
            set(response) != {*expected, "response_nonce"}
            or any(response.get(key) != value for key, value in expected.items())
            or not isinstance(response_nonce, str)
            or SHA256_RE.fullmatch(response_nonce) is None
            or response_nonce == ZERO_SHA256
        ):
            raise LegacyWriterFreezeError(
                "interactive live lease response differs"
            )
        entry = {
            "challenge": challenge,
            "response": response,
            "entry_sha256": ZERO_SHA256,
        }
        entry["entry_sha256"] = _checkpoint_entry_sha256(entry)
        self._transcript.append(entry)
        self._tail = entry["entry_sha256"]

    def summary(self, *, require_final: bool) -> dict[str, Any]:
        finalized = bool(
            self._transcript
            and self._transcript[-1]["challenge"]["checkpoint"]
            == "before-result"
        )
        if require_final and not finalized:
            raise LegacyWriterFreezeError(
                "interactive live lease transcript is not finalized"
            )
        return {
            "interactive_lease_checkpoint_count": len(self._transcript),
            "interactive_lease_transcript": json.loads(
                canonical_json(self._transcript).decode("ascii")
            ),
            "interactive_lease_transcript_sha256": self._tail,
            "interactive_lease_authority_handoff_complete": finalized,
        }


class StdioCheckpointExchange:
    """Exchange one unpredictable challenge at a time over SSH stdio."""

    def __init__(
        self,
        *,
        input_stream: BinaryIO,
        output_stream: BinaryIO,
        timeout: float = LIVE_CHECKPOINT_RESPONSE_TIMEOUT_SECONDS,
    ) -> None:
        self._input = input_stream
        self._output = output_stream
        self._timeout = timeout

    def __call__(self, challenge: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            descriptor = self._input.fileno()
            readable, _writable, _exceptional = select.select(
                [descriptor],
                [],
                [],
                0,
            )
        except (AttributeError, OSError, ValueError) as exc:
            raise LegacyWriterFreezeError(
                "interactive live lease stdin is unavailable"
            ) from exc
        if readable:
            raise LegacyWriterFreezeError(
                "prebuffered interactive live lease input is forbidden"
            )
        try:
            self._output.write(canonical_json(challenge) + b"\n")
            self._output.flush()
            readable, _writable, _exceptional = select.select(
                [descriptor],
                [],
                [],
                self._timeout,
            )
            if not readable:
                raise LegacyWriterFreezeError(
                    "interactive live lease response timed out"
                )
            raw = self._input.readline(MAX_LIVE_CHECKPOINT_LINE_BYTES + 1)
        except LegacyWriterFreezeError:
            raise
        except (OSError, ValueError) as exc:
            raise LegacyWriterFreezeError(
                "interactive live lease stdio failed"
            ) from exc
        if (
            not raw
            or len(raw) > MAX_LIVE_CHECKPOINT_LINE_BYTES
            or not raw.endswith(b"\n")
        ):
            raise LegacyWriterFreezeError(
                "interactive live lease response is missing or oversized"
            )
        return _strict_json(
            raw[:-1],
            label="interactive live lease response",
        )


def controller_checkpoint_response(
    challenge: Mapping[str, Any],
    *,
    live_lease_verify: Callable[[], Mapping[str, Any]],
    expected_operation_id: str,
    expected_release_sha: str,
    expected_role: str,
    expected_claim_sha256: str,
    expected_claim_epoch: int,
    response_nonce: str | None = None,
) -> dict[str, Any]:
    """Build a response only after the controller re-proves its held flock."""
    fields = {
        "schema",
        "status",
        "operation_id",
        "release_sha",
        "role",
        "live_lease_claim_sha256",
        "live_lease_claim_epoch",
        "sequence",
        "checkpoint",
        "challenge_nonce",
        "previous_transcript_sha256",
    }
    if (
        not isinstance(challenge, Mapping)
        or set(challenge) != fields
        or challenge.get("schema") != LIVE_CHECKPOINT_CHALLENGE_SCHEMA
        or challenge.get("status") != "controller-response-required"
        or challenge.get("operation_id") != expected_operation_id
        or challenge.get("release_sha") != expected_release_sha
        or challenge.get("role") != expected_role
        or challenge.get("live_lease_claim_sha256")
        != expected_claim_sha256
        or challenge.get("live_lease_claim_epoch")
        != expected_claim_epoch
        or type(challenge.get("sequence")) is not int
        or challenge["sequence"] < 1
        or not isinstance(challenge.get("checkpoint"), str)
        or CHECKPOINT_RE.fullmatch(challenge["checkpoint"]) is None
        or not isinstance(challenge.get("challenge_nonce"), str)
        or SHA256_RE.fullmatch(challenge["challenge_nonce"]) is None
        or challenge["challenge_nonce"] == ZERO_SHA256
        or not isinstance(
            challenge.get("previous_transcript_sha256"),
            str,
        )
        or SHA256_RE.fullmatch(
            challenge["previous_transcript_sha256"]
        )
        is None
    ):
        raise LegacyWriterFreezeError(
            "controller live lease challenge differs"
        )
    live_lease_verify()
    nonce = response_nonce if response_nonce is not None else secrets.token_hex(32)
    if (
        not isinstance(nonce, str)
        or SHA256_RE.fullmatch(nonce) is None
        or nonce == ZERO_SHA256
    ):
        raise LegacyWriterFreezeError(
            "controller live lease response nonce is invalid"
        )
    return {
        "schema": LIVE_CHECKPOINT_RESPONSE_SCHEMA,
        "status": "controller-flock-verified",
        "operation_id": expected_operation_id,
        "release_sha": expected_release_sha,
        "role": expected_role,
        "live_lease_claim_sha256": expected_claim_sha256,
        "live_lease_claim_epoch": expected_claim_epoch,
        "sequence": challenge["sequence"],
        "checkpoint": challenge["checkpoint"],
        "challenge_nonce": challenge["challenge_nonce"],
        "challenge_sha256": hashlib.sha256(
            canonical_json(challenge)
        ).hexdigest(),
        "previous_transcript_sha256": challenge[
            "previous_transcript_sha256"
        ],
        "controller_flock_verified": True,
        "response_nonce": nonce,
    }


def validate_live_checkpoint_transcript(
    value: Any,
    *,
    operation_id: str,
    release_sha: str,
    role: str,
    claim_sha256: str,
    claim_epoch: int,
    require_final: bool,
) -> tuple[int, str, bool]:
    if not isinstance(value, list) or len(value) > 10_000:
        raise LegacyWriterFreezeError(
            "interactive live lease transcript is invalid"
        )
    previous = ZERO_SHA256
    finalized = False
    for sequence, entry in enumerate(value, 1):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"challenge", "response", "entry_sha256"}
            or not isinstance(entry["challenge"], dict)
            or not isinstance(entry["response"], dict)
        ):
            raise LegacyWriterFreezeError(
                "interactive live lease transcript entry differs"
            )
        challenge = entry["challenge"]
        response = entry["response"]
        checkpoint = challenge.get("checkpoint")
        challenge_fields = {
            "schema",
            "status",
            "operation_id",
            "release_sha",
            "role",
            "live_lease_claim_sha256",
            "live_lease_claim_epoch",
            "sequence",
            "checkpoint",
            "challenge_nonce",
            "previous_transcript_sha256",
        }
        response_fields = {
            *challenge_fields,
            "challenge_sha256",
            "controller_flock_verified",
            "response_nonce",
        }
        if (
            set(challenge) != challenge_fields
            or challenge["schema"] != LIVE_CHECKPOINT_CHALLENGE_SCHEMA
            or challenge["status"] != "controller-response-required"
            or challenge["operation_id"] != operation_id
            or challenge["release_sha"] != release_sha
            or challenge["role"] != role
            or challenge["live_lease_claim_sha256"] != claim_sha256
            or challenge["live_lease_claim_epoch"] != claim_epoch
            or challenge["sequence"] != sequence
            or not isinstance(checkpoint, str)
            or CHECKPOINT_RE.fullmatch(checkpoint) is None
            or not isinstance(challenge["challenge_nonce"], str)
            or SHA256_RE.fullmatch(challenge["challenge_nonce"]) is None
            or challenge["challenge_nonce"] == ZERO_SHA256
            or challenge["previous_transcript_sha256"] != previous
            or set(response) != response_fields
            or response["schema"] != LIVE_CHECKPOINT_RESPONSE_SCHEMA
            or response["status"] != "controller-flock-verified"
            or any(
                response[field] != challenge[field]
                for field in challenge_fields
                - {"schema", "status"}
            )
            or response["challenge_sha256"]
            != hashlib.sha256(canonical_json(challenge)).hexdigest()
            or response["controller_flock_verified"] is not True
            or not isinstance(response["response_nonce"], str)
            or SHA256_RE.fullmatch(response["response_nonce"]) is None
            or response["response_nonce"] == ZERO_SHA256
            or entry["entry_sha256"] != _checkpoint_entry_sha256(entry)
        ):
            raise LegacyWriterFreezeError(
                "interactive live lease transcript binding differs"
            )
        if finalized:
            raise LegacyWriterFreezeError(
                "interactive live lease transcript continues after finalization"
            )
        finalized = checkpoint == "before-result"
        previous = entry["entry_sha256"]
    if require_final and not finalized:
        raise LegacyWriterFreezeError(
            "interactive live lease transcript is not finalized"
        )
    return len(value), previous, finalized


def run_command(arguments: Sequence[str], timeout: int) -> str:
    try:
        result = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LegacyWriterFreezeError(
            f"required command is unavailable: {Path(arguments[0]).name}"
        ) from exc
    if (
        result.returncode != 0
        or len(result.stdout) > MAX_COMMAND_OUTPUT_BYTES
        or len(result.stderr) > MAX_COMMAND_ERROR_BYTES
    ):
        raise LegacyWriterFreezeError(
            f"required command failed closed: {Path(arguments[0]).name}"
        )
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise LegacyWriterFreezeError(
            "required command returned non-UTF-8 output"
        ) from exc


def _state_hash(document: Mapping[str, Any]) -> str:
    unsigned = {
        key: value for key, value in document.items() if key != "state_sha256"
    }
    return hashlib.sha256(canonical_json(unsigned)).hexdigest()


def _file_sha256(path: Path, *, label: str) -> str:
    try:
        payload = read_secure_bytes(
            path,
            label=label,
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise LegacyWriterFreezeError(str(exc)) from exc
    return hashlib.sha256(payload).hexdigest()


def state_directory(
    binding: SOURCE.SnapshotBinding,
    *,
    secret_root: Path = SECRET_ROOT_PREFIX,
) -> Path:
    return (
        secret_root
        / binding.operation_id
        / STATE_DIRECTORY_NAME
        / binding.role
    )


def coordinated_receipt_path(
    binding: SOURCE.SnapshotBinding,
    receipt_sha256: str,
    *,
    secret_root: Path = SECRET_ROOT_PREFIX,
) -> Path:
    receipt_sha256 = _nonzero_sha256(
        receipt_sha256,
        label="coordinated Nginx receipt digest",
    )
    return (
        secret_root
        / binding.operation_id
        / "nginx-coordinator"
        / "receipts"
        / f"legacy-frozen-{receipt_sha256}.json"
    )


def live_lease_claim_path(
    binding: SOURCE.SnapshotBinding,
    claim_sha256: str,
    *,
    secret_root: Path = SECRET_ROOT_PREFIX,
) -> Path:
    claim_sha256 = _nonzero_sha256(
        claim_sha256,
        label="live lease claim digest",
    )
    return (
        secret_root
        / binding.operation_id
        / "nginx-coordinator"
        / "live-leases"
        / "claims"
        / f"{claim_sha256}.json"
    )


def _load_coordinated_receipt(
    path: Path,
    *,
    binding: SOURCE.SnapshotBinding,
    release_tree_sha: str,
    nginx_aggregate_sha256: str,
    expected_sha256: str,
    secret_root: Path,
) -> tuple[dict[str, Any], str]:
    expected_path = coordinated_receipt_path(
        binding,
        expected_sha256,
        secret_root=secret_root,
    )
    if path != expected_path:
        raise LegacyWriterFreezeError(
            "coordinated Nginx receipt path is not canonical"
        )
    operation_root = secret_root / binding.operation_id
    coordinator_root = operation_root / "nginx-coordinator"
    receipts_root = coordinator_root / "receipts"
    for directory in (operation_root, coordinator_root, receipts_root):
        _verify_private_directory(directory)
    if (
        _file_sha256(
            path,
            label="coordinated Nginx state receipt",
        )
        != expected_sha256
    ):
        raise LegacyWriterFreezeError(
            "coordinated Nginx receipt digest differs"
        )
    try:
        receipt, observed_sha256 = NGINX_COORDINATOR.load_state_receipt(
            path,
            "legacy-frozen",
            binding.operation_id,
            binding.release_sha,
            release_tree_sha,
            nginx_aggregate_sha256,
        )
    except NGINX_COORDINATOR.NginxCoordinatorError as exc:
        raise LegacyWriterFreezeError(
            "coordinated Nginx receipt verification failed"
        ) from exc
    if observed_sha256 != expected_sha256:
        raise LegacyWriterFreezeError(
            "coordinated Nginx receipt readback digest differs"
        )
    return receipt, observed_sha256


def _load_live_lease_claim(
    path: Path,
    *,
    binding: SOURCE.SnapshotBinding,
    release_tree_sha: str,
    nginx_aggregate_sha256: str,
    coordinated_state_receipt: Path,
    coordinated_state_receipt_sha256: str,
    expected_sha256: str,
    secret_root: Path,
) -> dict[str, Any]:
    expected_path = live_lease_claim_path(
        binding,
        expected_sha256,
        secret_root=secret_root,
    )
    if path != expected_path:
        raise LegacyWriterFreezeError(
            "live lease claim path is not canonical"
        )
    operation_root = secret_root / binding.operation_id
    coordinator_root = operation_root / "nginx-coordinator"
    live_leases_root = coordinator_root / "live-leases"
    claims_root = live_leases_root / "claims"
    for directory in (
        operation_root,
        coordinator_root,
        live_leases_root,
        claims_root,
    ):
        _verify_private_directory(directory)
    try:
        claim, observed_sha256 = (
            NGINX_COORDINATOR.load_live_lease_claim_material(
                path,
                state_receipt_path=coordinated_state_receipt,
                expected_claim_sha256=expected_sha256,
                expected_state_receipt_sha256=(
                    coordinated_state_receipt_sha256
                ),
                operation_id=binding.operation_id,
                release_sha=binding.release_sha,
                release_tree_sha=release_tree_sha,
                aggregate_sha256=nginx_aggregate_sha256,
            )
        )
    except NGINX_COORDINATOR.NginxCoordinatorError as exc:
        raise LegacyWriterFreezeError(
            "live lease claim material verification failed"
        ) from exc
    if (
        observed_sha256 != expected_sha256
        or claim.get("legacy_frozen_receipt_sha256")
        != coordinated_state_receipt_sha256
        or claim.get("legacy_frozen_receipt_path")
        != str(coordinated_state_receipt)
    ):
        raise LegacyWriterFreezeError(
            "live lease claim binding differs"
        )
    return claim


def _bind_local_readback_to_receipt(
    receipt: Mapping[str, Any],
    *,
    binding: SOURCE.SnapshotBinding,
    readback: Mapping[str, Any],
    nginx_manifest_sha256: str,
) -> tuple[str, str]:
    role_binding = receipt["role_bindings"][binding.role]
    receipt_readback = receipt["readbacks"][binding.role]
    if (
        role_binding["manifest_sha256"] != nginx_manifest_sha256
        or readback != receipt_readback
    ):
        raise LegacyWriterFreezeError(
            "local Nginx readback differs from coordinated state receipt"
        )
    role_digest = _nonzero_sha256(
        readback.get("generation_sha256"),
        label="role freeze generation digest",
    )
    global_digest = _nonzero_sha256(
        receipt.get("global_generation_sha256"),
        label="global freeze generation digest",
    )
    return role_digest, global_digest


def _verify_private_directory(path: Path, *, exact_mode: int = 0o700) -> None:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise LegacyWriterFreezeError(
            f"required private directory is unavailable: {path}"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != exact_mode
    ):
        raise LegacyWriterFreezeError(
            f"required private directory is unsafe: {path}"
        )


def _ensure_private_children(operation_root: Path, role: str) -> Path:
    _verify_private_directory(operation_root)
    current = operation_root
    for component in (STATE_DIRECTORY_NAME, role):
        descriptor = -1
        try:
            descriptor = os.open(
                current,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                os.mkdir(component, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
            except FileExistsError:
                pass
        except OSError as exc:
            raise LegacyWriterFreezeError(
                "cannot create the operation freeze directory safely"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        current = current / component
        _verify_private_directory(current)
    return current


@contextmanager
def _exclusive_lock(directory: Path) -> Iterator[None]:
    descriptor = -1
    directory_fd = -1
    try:
        directory_fd = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            LOCK_FILENAME,
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise LegacyWriterFreezeError("freeze lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    except LegacyWriterFreezeError:
        raise
    except OSError as exc:
        raise LegacyWriterFreezeError("cannot acquire the freeze lock") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


def _environment_value(
    document: Mapping[str, Any],
    key: str,
    *,
    label: str,
) -> str:
    config = document.get("Config")
    environment = config.get("Env") if isinstance(config, Mapping) else None
    if (
        not isinstance(environment, list)
        or any(not isinstance(row, str) for row in environment)
    ):
        raise LegacyWriterFreezeError(f"{label} environment is invalid")
    matches = [
        row[len(key) + 1 :]
        for row in environment
        if row.startswith(f"{key}=")
    ]
    if len(matches) != 1 or not matches[0]:
        raise LegacyWriterFreezeError(f"{label} environment binding differs")
    return matches[0]


def _writer_identity(
    document: Mapping[str, Any],
    *,
    binding: SOURCE.SnapshotBinding,
    source_image_id: str,
    kind: str,
    expected_name: str,
    expected_service: str,
) -> tuple[dict[str, str], bool]:
    identifier = document.get("Id")
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    state = document.get("State")
    image_id = document.get("Image")
    if (
        not isinstance(identifier, str)
        or CONTAINER_ID_RE.fullmatch(identifier) is None
        or identifier == ZERO_SHA256
        or document.get("Name") != f"/{expected_name}"
        or image_id != source_image_id
        or not isinstance(config, Mapping)
        or config.get("Image") != binding.images["application"]
        or not isinstance(labels, Mapping)
        or labels.get("com.docker.compose.project") != binding.source_project
        or labels.get("com.docker.compose.service") != expected_service
        or labels.get("com.docker.compose.oneoff") not in (None, "False")
        or not isinstance(state, Mapping)
        or type(state.get("Running")) is not bool
        or state.get("Paused") is True
        or state.get("Restarting") is True
        or state.get("Dead") is True
        or type(document.get("RestartCount")) is not int
        or document["RestartCount"] < 0
    ):
        raise LegacyWriterFreezeError(
            f"legacy {kind} container identity differs"
        )
    release = _environment_value(
        document,
        "RELEASE_SHA",
        label=f"legacy {kind} container",
    )
    service_env = _environment_value(
        document,
        "TRADING_BOT_SERVICE",
        label=f"legacy {kind} container",
    )
    if (
        release != binding.legacy_release_sha
        or service_env != ROLE_SERVICE_ENV[kind]
    ):
        raise LegacyWriterFreezeError(
            f"legacy {kind} release or service identity differs"
        )
    return (
        {
            "id": identifier,
            "name": expected_name,
            "service": expected_service,
            "image_id": str(image_id),
            "release_sha": release,
        },
        bool(state["Running"]),
    )


def inspect_writer_set(
    binding: SOURCE.SnapshotBinding,
    inventory: SOURCE.SourceInventory,
) -> tuple[dict[str, dict[str, str]], dict[str, bool]]:
    source_image_id = inventory.images["application"].image_id
    identities: dict[str, dict[str, str]] = {}
    running: dict[str, bool] = {}
    for kind, name, service in ROLE_WRITERS[binding.role]:
        try:
            document = SOURCE._inspect_required("container", name)
        except SOURCE.SourceSnapshotError as exc:
            raise LegacyWriterFreezeError(
                f"legacy {kind} container is unavailable"
            ) from exc
        identity, is_running = _writer_identity(
            document,
            binding=binding,
            source_image_id=source_image_id,
            kind=kind,
            expected_name=name,
            expected_service=service,
        )
        identities[kind] = identity
        running[kind] = is_running
    application_id = inventory.containers["application"]["id"]
    if identities["application"]["id"] != application_id:
        raise LegacyWriterFreezeError(
            "legacy application identity differs from source binding"
        )
    return dict(sorted(identities.items())), dict(sorted(running.items()))


def _running_project_services(
    binding: SOURCE.SnapshotBinding,
    *,
    runner: Runner,
) -> dict[str, str]:
    output = runner(
        [
            DOCKER,
            "container",
            "ls",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"label=com.docker.compose.project={binding.source_project}",
        ],
        30,
    )
    identifiers = [row for row in output.splitlines() if row]
    if (
        len(identifiers) != len(set(identifiers))
        or any(CONTAINER_ID_RE.fullmatch(row) is None for row in identifiers)
    ):
        raise LegacyWriterFreezeError(
            "running legacy project container inventory is invalid"
        )
    services: dict[str, str] = {}
    for identifier in identifiers:
        try:
            document = SOURCE._inspect_required("container", identifier)
        except SOURCE.SourceSnapshotError as exc:
            raise LegacyWriterFreezeError(
                "running legacy project container disappeared"
            ) from exc
        config = document.get("Config")
        labels = config.get("Labels") if isinstance(config, Mapping) else None
        state = document.get("State")
        service = (
            labels.get("com.docker.compose.service")
            if isinstance(labels, Mapping)
            else None
        )
        if (
            not isinstance(service, str)
            or service not in ALLOWED_RUNNING_SERVICES[binding.role]
            or service in services
            or not isinstance(state, Mapping)
            or state.get("Running") is not True
        ):
            raise LegacyWriterFreezeError(
                "running legacy project service closure differs"
            )
        services[service] = identifier
    return dict(sorted(services.items()))


def _assert_data_running(
    binding: SOURCE.SnapshotBinding,
    inventory: SOURCE.SourceInventory,
) -> None:
    for kind in DATA_KINDS:
        row = inventory.containers[kind]
        if row["running"] is not True:
            raise LegacyWriterFreezeError(
                f"legacy {kind} container must remain running"
            )
    if inventory.containers["database"]["id"] == inventory.containers["redis"]["id"]:
        raise LegacyWriterFreezeError("legacy data container identities collide")


def _path_within(path: str, roots: Sequence[str]) -> bool:
    if not path.startswith("/"):
        return False
    normalized = os.path.normpath(path.removesuffix(" (deleted)"))
    for root in roots:
        root_normalized = os.path.normpath(root)
        try:
            if os.path.commonpath((normalized, root_normalized)) == root_normalized:
                return True
        except ValueError:
            continue
    return False


def _running_volume_mutator_containers(
    volume_names: frozenset[str],
    volume_roots: tuple[str, ...],
    *,
    runner: Runner,
) -> set[str]:
    output = runner(
        [DOCKER, "container", "ls", "--quiet", "--no-trunc"],
        30,
    )
    identifiers = [row for row in output.splitlines() if row]
    if (
        len(identifiers) != len(set(identifiers))
        or any(CONTAINER_ID_RE.fullmatch(row) is None for row in identifiers)
    ):
        raise LegacyWriterFreezeError(
            "running container inventory is invalid"
        )
    mutators: set[str] = set()
    for identifier in identifiers:
        try:
            document = SOURCE._inspect_required("container", identifier)
        except SOURCE.SourceSnapshotError as exc:
            raise LegacyWriterFreezeError(
                "running container disappeared during file-plane inspection"
            ) from exc
        mounts = document.get("Mounts")
        if not isinstance(mounts, list):
            raise LegacyWriterFreezeError(
                "running container mount inventory is invalid"
            )
        for mount in mounts:
            if not isinstance(mount, Mapping):
                raise LegacyWriterFreezeError(
                    "running container mount row is invalid"
                )
            name = mount.get("Name")
            source = mount.get("Source")
            if (
                mount.get("RW") is True
                and (
                    (isinstance(name, str) and name in volume_names)
                    or (
                        isinstance(source, str)
                        and _path_within(source, volume_roots)
                    )
                )
            ):
                mutators.add(identifier)
    return mutators


def _writable_fd(flags_payload: str) -> bool:
    for row in flags_payload.splitlines():
        key, separator, raw = row.partition(":")
        if key == "flags" and separator:
            try:
                flags = int(raw.strip(), 8)
            except ValueError as exc:
                raise LegacyWriterFreezeError(
                    "process fd flags are invalid"
                ) from exc
            return flags & os.O_ACCMODE in {os.O_WRONLY, os.O_RDWR}
    raise LegacyWriterFreezeError("process fd flags are missing")


def _host_file_mutator_processes(
    volume_roots: tuple[str, ...],
    *,
    proc_root: Path,
) -> set[int]:
    try:
        process_names = [
            name for name in os.listdir(proc_root) if name.isdigit()
        ]
    except OSError as exc:
        raise LegacyWriterFreezeError(
            "process inventory is unavailable"
        ) from exc
    mutators: set[int] = set()
    for name in process_names:
        pid = int(name)
        process_root = proc_root / name
        fd_root = process_root / "fd"
        try:
            descriptors = os.listdir(fd_root)
        except FileNotFoundError:
            continue
        except PermissionError as exc:
            raise LegacyWriterFreezeError(
                "process fd inventory is not readable as root"
            ) from exc
        for descriptor_name in descriptors:
            if not descriptor_name.isdigit():
                raise LegacyWriterFreezeError(
                    "process fd inventory contains an invalid entry"
                )
            try:
                target = os.readlink(fd_root / descriptor_name)
                flags_payload = (
                    process_root / "fdinfo" / descriptor_name
                ).read_text(encoding="ascii")
            except FileNotFoundError:
                continue
            except (OSError, UnicodeError) as exc:
                raise LegacyWriterFreezeError(
                    "process fd inventory changed ambiguously"
                ) from exc
            if _path_within(target, volume_roots) and _writable_fd(
                flags_payload
            ):
                mutators.add(pid)
        maps_path = process_root / "maps"
        try:
            maps = maps_path.read_text(encoding="utf-8", errors="strict")
        except FileNotFoundError:
            continue
        except (OSError, UnicodeError) as exc:
            raise LegacyWriterFreezeError(
                "process memory map inventory is unreadable"
            ) from exc
        for row in maps.splitlines():
            fields = row.split(maxsplit=5)
            if (
                len(fields) == 6
                and "w" in fields[1]
                and _path_within(fields[5], volume_roots)
            ):
                mutators.add(pid)
    return mutators


def _database_client_count(
    binding: SOURCE.SnapshotBinding,
    *,
    database_container_id: str,
    runner: Runner,
) -> int:
    try:
        document = SOURCE._inspect_required(
            "container",
            database_container_id,
        )
    except SOURCE.SourceSnapshotError as exc:
        raise LegacyWriterFreezeError(
            "legacy database identity cannot be read"
        ) from exc
    config = document.get("Config")
    labels = config.get("Labels") if isinstance(config, Mapping) else None
    state = document.get("State")
    if (
        document.get("Id") != database_container_id
        or document.get("Name") != f"/{binding.containers['database']}"
        or not isinstance(labels, Mapping)
        or labels.get("com.docker.compose.project") != binding.source_project
        or labels.get("com.docker.compose.service") != "db"
        or not isinstance(state, Mapping)
        or state.get("Running") is not True
    ):
        raise LegacyWriterFreezeError(
            "legacy database container identity differs"
        )
    user = _environment_value(
        document,
        "POSTGRES_USER",
        label="legacy database container",
    )
    database = _environment_value(
        document,
        "POSTGRES_DB",
        label="legacy database container",
    )
    if (
        SOURCE.IDENTIFIER_RE.fullmatch(user) is None
        or SOURCE.IDENTIFIER_RE.fullmatch(database) is None
    ):
        raise LegacyWriterFreezeError(
            "legacy database identifiers are invalid"
        )
    output = runner(
        [
            DOCKER,
            "exec",
            "--env",
            f"PGOPTIONS={SOURCE.DATABASE_FINGERPRINT_PGOPTIONS}",
            "--env",
            f"PGCLIENTENCODING={SOURCE.DATABASE_FINGERPRINT_CLIENT_ENCODING}",
            database_container_id,
            "psql",
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--username",
            user,
            "--dbname",
            database,
            "--command",
            DB_CLIENT_SQL,
        ],
        30,
    )
    if not output.isdigit():
        raise LegacyWriterFreezeError(
            "legacy database client count is invalid"
        )
    count = int(output)
    if count > 1_000_000:
        raise LegacyWriterFreezeError(
            "legacy database client count exceeds its bound"
        )
    return count


def _zero_writer_readback(
    binding: SOURCE.SnapshotBinding,
    inventory: SOURCE.SourceInventory,
    *,
    runner: Runner,
    sleep_fn: SleepFn,
    proc_root: Path,
) -> dict[str, int]:
    identities, running = inspect_writer_set(binding, inventory)
    del identities
    writer_count = sum(1 for value in running.values() if value)
    project_services = _running_project_services(binding, runner=runner)
    expected_data = {
        "db": str(inventory.containers["database"]["id"]),
        "redis": str(inventory.containers["redis"]["id"]),
    }
    if project_services != expected_data:
        raise LegacyWriterFreezeError(
            "legacy running data service identity differs"
        )

    volume_names = frozenset(
        {
            binding.volumes["uploads"],
            binding.volumes["audit"],
        }
    )
    volume_roots = tuple(
        str(inventory.volumes[kind]["mountpoint"])
        for kind in ("uploads", "audit")
    )
    container_mutators = _running_volume_mutator_containers(
        volume_names,
        volume_roots,
        runner=runner,
    )
    process_mutators = _host_file_mutator_processes(
        volume_roots,
        proc_root=proc_root,
    )
    client_samples: list[int] = []
    for index in range(2):
        client_samples.append(
            _database_client_count(
                binding,
                database_container_id=expected_data["db"],
                runner=runner,
            )
        )
        if index == 0:
            sleep_fn(0.25)
    result = {
        "legacy_writer_process_count": writer_count,
        "writer_database_client_count": max(client_samples),
        "file_mutator_process_count": (
            len(container_mutators) + len(process_mutators)
        ),
    }
    if any(result.values()):
        raise LegacyWriterFreezeError(
            "legacy zero-writer proof failed closed"
        )
    return result


def _nginx_readback(
    *,
    binding: SOURCE.SnapshotBinding,
    release_tree_sha: str,
    nginx_manifest: Path,
    nginx_manifest_sha256: str,
    nginx_archive: Path,
) -> dict[str, Any]:
    try:
        result = NGINX.execute_host_action(
            manifest_path=nginx_manifest,
            expected_manifest_sha256=nginx_manifest_sha256,
            archive_path=nginx_archive,
            role=binding.role,
            expected_host=NGINX.ROLE_HOSTS[binding.role],
            operation_id=binding.operation_id,
            release_sha=binding.release_sha,
            release_tree_sha=release_tree_sha,
            action="readback",
            apply=True,
        )
    except NGINX.NginxGenerationError as exc:
        raise LegacyWriterFreezeError(
            "legacy freeze Nginx generation cannot be read back"
        ) from exc
    if (
        result.get("schema")
        != "production-shadow-nginx-host-readback-v1"
        or result.get("status") != "read-back"
        or result.get("state") != "legacy-frozen"
        or result.get("operation_id") != binding.operation_id
        or result.get("role") != binding.role
        or result.get("release_sha") != binding.release_sha
        or result.get("release_tree_sha") != release_tree_sha
        or result.get("manifest_sha256") != nginx_manifest_sha256
        or result.get("expected_host") != NGINX.ROLE_HOSTS[binding.role]
        or result.get("active_configuration_mutated") is not False
        or result.get("service_reloaded") is not False
    ):
        raise LegacyWriterFreezeError(
            "legacy freeze Nginx readback identity differs"
        )
    _nonzero_sha256(
        result.get("generation_sha256"),
        label="freeze generation digest",
    )
    _nonzero_sha256(
        result.get("journal_sha256"),
        label="Nginx journal digest",
    )
    return result


def _installed_nginx_freeze_readback(
    binding: SOURCE.SnapshotBinding,
    journal: Mapping[str, Any],
    *,
    secret_root: Path,
) -> dict[str, Any]:
    root = (
        NGINX.DEFAULT_OPERATION_BASE
        / binding.operation_id
        / binding.role.replace("_", "-")
    )
    manifest_path = root / "manifest.json"
    archive_path = root / "archive.tar"
    try:
        manifest, _payload = NGINX._read_strict_canonical_json(
            manifest_path,
            label="installed Nginx role manifest",
            owner_uid=0,
        )
    except NGINX.NginxGenerationError as exc:
        raise LegacyWriterFreezeError(
            "installed Nginx role manifest is unavailable"
        ) from exc
    release_tree_sha = manifest.get("release_tree_sha")
    try:
        NGINX._release_sha(
            release_tree_sha,
            label="installed release_tree_sha",
        )
    except NGINX.NginxGenerationError as exc:
        raise LegacyWriterFreezeError(
            "installed Nginx release tree identity is invalid"
        ) from exc
    if release_tree_sha != journal["release_tree_sha"]:
        raise LegacyWriterFreezeError(
            "installed Nginx release tree differs from freeze journal"
        )
    receipt_path = coordinated_receipt_path(
        binding,
        journal["coordinated_state_receipt_sha256"],
        secret_root=secret_root,
    )
    receipt, _receipt_sha256 = _load_coordinated_receipt(
        receipt_path,
        binding=binding,
        release_tree_sha=release_tree_sha,
        nginx_aggregate_sha256=journal["nginx_aggregate_sha256"],
        expected_sha256=journal["coordinated_state_receipt_sha256"],
        secret_root=secret_root,
    )
    result = _nginx_readback(
        binding=binding,
        release_tree_sha=release_tree_sha,
        nginx_manifest=manifest_path,
        nginx_manifest_sha256=journal["nginx_manifest_sha256"],
        nginx_archive=archive_path,
    )
    role_digest, global_digest = _bind_local_readback_to_receipt(
        receipt,
        binding=binding,
        readback=result,
        nginx_manifest_sha256=journal["nginx_manifest_sha256"],
    )
    if (
        role_digest != journal["role_freeze_generation_sha256"]
        or global_digest != journal["freeze_generation_sha256"]
    ):
        raise LegacyWriterFreezeError(
            "installed Nginx freeze generation differs from freeze journal"
        )
    return result


def confirmation_phrase(
    action: str,
    binding: SOURCE.SnapshotBinding,
    *,
    nginx_aggregate_sha256: str,
    nginx_manifest_sha256: str,
    coordinated_state_receipt_sha256: str,
    live_lease_claim_sha256: str,
) -> str:
    if action not in {"freeze", "restore"}:
        raise LegacyWriterFreezeError(
            "only freeze and restore require a confirmation"
        )
    return (
        f"{action}-production-legacy-writers:"
        f"{binding.operation_id}:{binding.role}:{binding.canonical_sha256}:"
        f"{nginx_aggregate_sha256}:{nginx_manifest_sha256}:"
        f"{coordinated_state_receipt_sha256}:"
        f"{live_lease_claim_sha256}"
    )


def _base_journal(
    binding: SOURCE.SnapshotBinding,
    *,
    release_tree_sha: str,
    nginx_aggregate_sha256: str,
    nginx_manifest_sha256: str,
    coordinated_state_receipt_sha256: str,
    live_lease_claim_sha256: str,
    live_lease_claim_epoch: int,
    role_freeze_generation_sha256: str,
    freeze_generation_sha256: str,
    source_container_ids: Mapping[str, str],
    writer_containers: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "status": "prepared",
        "operation_id": binding.operation_id,
        "release_sha": binding.release_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "role": binding.role,
        "source_project": binding.source_project,
        "binding_sha256": binding.canonical_sha256,
        "controller_manifest_sha256": binding.controller_manifest_sha256,
        "approval_sha256": binding.approval_sha256,
        "release_tree_sha": release_tree_sha,
        "nginx_aggregate_sha256": nginx_aggregate_sha256,
        "nginx_manifest_sha256": nginx_manifest_sha256,
        "coordinated_state_receipt_sha256": (
            coordinated_state_receipt_sha256
        ),
        "coordinated_state_receipt_history": [
            coordinated_state_receipt_sha256
        ],
        "live_lease_claim_sha256": live_lease_claim_sha256,
        "live_lease_claim_history": [live_lease_claim_sha256],
        "live_lease_claim_epoch": live_lease_claim_epoch,
        "live_lease_claim_epoch_history": [live_lease_claim_epoch],
        "role_freeze_generation_sha256": role_freeze_generation_sha256,
        "freeze_generation_sha256": freeze_generation_sha256,
        "source_container_ids": dict(sorted(source_container_ids.items())),
        "writer_containers": {
            key: dict(value)
            for key, value in sorted(writer_containers.items())
        },
        "previously_running": sorted(writer_containers),
        "stopped": [],
        "last_error_sha256": ZERO_SHA256,
        "failure_history": [],
        "interactive_lease_checkpoint_count": 0,
        "interactive_lease_transcript": [],
        "interactive_lease_transcript_sha256": ZERO_SHA256,
        "interactive_lease_authority_handoff_complete": False,
        "sequence": 0,
        "state_sha256": ZERO_SHA256,
    }
    document["state_sha256"] = _state_hash(document)
    return document


def _validate_journal(
    document: Mapping[str, Any],
    *,
    binding: SOURCE.SnapshotBinding,
    release_tree_sha: str,
    nginx_aggregate_sha256: str,
    nginx_manifest_sha256: str,
    coordinated_state_receipt_sha256: str,
    live_lease_claim_sha256: str,
    live_lease_claim_epoch: int,
    role_freeze_generation_sha256: str,
    freeze_generation_sha256: str,
    source_container_ids: Mapping[str, str],
    writer_containers: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    expected_static = {
        "schema": JOURNAL_SCHEMA,
        "operation_id": binding.operation_id,
        "release_sha": binding.release_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "role": binding.role,
        "source_project": binding.source_project,
        "binding_sha256": binding.canonical_sha256,
        "controller_manifest_sha256": binding.controller_manifest_sha256,
        "approval_sha256": binding.approval_sha256,
        "release_tree_sha": release_tree_sha,
        "nginx_aggregate_sha256": nginx_aggregate_sha256,
        "nginx_manifest_sha256": nginx_manifest_sha256,
        "coordinated_state_receipt_sha256": (
            coordinated_state_receipt_sha256
        ),
        "live_lease_claim_sha256": live_lease_claim_sha256,
        "live_lease_claim_epoch": live_lease_claim_epoch,
        "role_freeze_generation_sha256": role_freeze_generation_sha256,
        "freeze_generation_sha256": freeze_generation_sha256,
        "source_container_ids": dict(sorted(source_container_ids.items())),
        "writer_containers": {
            key: dict(value)
            for key, value in sorted(writer_containers.items())
        },
        "previously_running": sorted(writer_containers),
    }
    transcript_count, transcript_sha256, transcript_finalized = (
        validate_live_checkpoint_transcript(
            document.get("interactive_lease_transcript"),
            operation_id=binding.operation_id,
            release_sha=binding.release_sha,
            role=binding.role,
            claim_sha256=live_lease_claim_sha256,
            claim_epoch=live_lease_claim_epoch,
            require_final=False,
        )
    )
    if (
        set(document) != JOURNAL_FIELDS
        or any(document.get(key) != value for key, value in expected_static.items())
        or document.get("status") not in JOURNAL_STATUSES
        or not isinstance(document.get("stopped"), list)
        or any(not isinstance(value, str) for value in document["stopped"])
        or document["stopped"] != sorted(set(document["stopped"]))
        or not set(document["stopped"]).issubset(set(writer_containers))
        or not isinstance(
            document.get("coordinated_state_receipt_history"),
            list,
        )
        or not 1
        <= len(document["coordinated_state_receipt_history"])
        <= 1_000
        or any(
            not isinstance(value, str)
            or SHA256_RE.fullmatch(value) is None
            or value == ZERO_SHA256
            for value in document["coordinated_state_receipt_history"]
        )
        or len(document["coordinated_state_receipt_history"])
        != len(set(document["coordinated_state_receipt_history"]))
        or document["coordinated_state_receipt_history"][-1]
        != document["coordinated_state_receipt_sha256"]
        or not isinstance(document.get("live_lease_claim_history"), list)
        or not 1 <= len(document["live_lease_claim_history"]) <= 1_000
        or any(
            not isinstance(value, str)
            or SHA256_RE.fullmatch(value) is None
            or value == ZERO_SHA256
            for value in document["live_lease_claim_history"]
        )
        or len(document["live_lease_claim_history"])
        != len(set(document["live_lease_claim_history"]))
        or document["live_lease_claim_history"][-1]
        != document["live_lease_claim_sha256"]
        or not isinstance(
            document.get("live_lease_claim_epoch_history"),
            list,
        )
        or len(document["live_lease_claim_epoch_history"])
        != len(document["live_lease_claim_history"])
        or any(
            type(value) is not int or value < 1
            for value in document["live_lease_claim_epoch_history"]
        )
        or document["live_lease_claim_epoch_history"]
        != sorted(set(document["live_lease_claim_epoch_history"]))
        or document["live_lease_claim_epoch_history"][-1]
        != document["live_lease_claim_epoch"]
        or not isinstance(document.get("last_error_sha256"), str)
        or SHA256_RE.fullmatch(document["last_error_sha256"]) is None
        or not isinstance(document.get("failure_history"), list)
        or len(document["failure_history"]) > 1_000
        or any(
            not isinstance(value, str)
            or SHA256_RE.fullmatch(value) is None
            or value == ZERO_SHA256
            for value in document["failure_history"]
        )
        or type(document.get("interactive_lease_checkpoint_count"))
        is not int
        or not 0
        <= document["interactive_lease_checkpoint_count"]
        <= 10_000
        or not isinstance(
            document.get("interactive_lease_transcript"),
            list,
        )
        or transcript_count
        != document["interactive_lease_checkpoint_count"]
        or not isinstance(
            document.get("interactive_lease_transcript_sha256"),
            str,
        )
        or SHA256_RE.fullmatch(
            document["interactive_lease_transcript_sha256"]
        )
        is None
        or type(
            document.get(
                "interactive_lease_authority_handoff_complete"
            )
        )
        is not bool
        or (
            document["interactive_lease_checkpoint_count"] == 0
            and (
                document["interactive_lease_transcript_sha256"]
                != ZERO_SHA256
                or document[
                    "interactive_lease_authority_handoff_complete"
                ]
                is not False
            )
        )
        or (
            document["interactive_lease_checkpoint_count"] > 0
            and (
                document["interactive_lease_transcript_sha256"]
                != transcript_sha256
                or document[
                    "interactive_lease_authority_handoff_complete"
                ]
                is not transcript_finalized
            )
        )
        or (
            document["last_error_sha256"] != ZERO_SHA256
            and (
                not document["failure_history"]
                or document["failure_history"][-1]
                != document["last_error_sha256"]
            )
        )
        or type(document.get("sequence")) is not int
        or not 0 <= document["sequence"] <= 1_000_000
        or document.get("state_sha256") != _state_hash(document)
    ):
        raise LegacyWriterFreezeError(
            "legacy writer freeze journal binding or state differs"
        )
    for identity in document["writer_containers"].values():
        if (
            not isinstance(identity, Mapping)
            or set(identity) != WRITER_IDENTITY_FIELDS
        ):
            raise LegacyWriterFreezeError(
                "legacy writer journal container identity differs"
            )
    return json.loads(canonical_json(document).decode("ascii"))


def _load_existing_journal(
    path: Path,
    *,
    binding: SOURCE.SnapshotBinding,
    release_tree_sha: str | None,
    nginx_aggregate_sha256: str | None,
    nginx_manifest_sha256: str | None,
    coordinated_state_receipt_sha256: str | None,
    live_lease_claim_sha256: str | None,
    live_lease_claim_epoch: int | None,
    role_freeze_generation_sha256: str | None,
    freeze_generation_sha256: str | None,
    source_container_ids: Mapping[str, str],
    writer_containers: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    try:
        raw = read_secure_bytes(
            path,
            label="legacy writer freeze journal",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise LegacyWriterFreezeError(
            "legacy writer freeze journal is unavailable"
        ) from exc
    document = _strict_json(raw, label="legacy writer freeze journal")
    observed_release_tree_sha = document.get("release_tree_sha")
    try:
        NGINX._release_sha(
            observed_release_tree_sha,
            label="journal release tree SHA",
        )
    except NGINX.NginxGenerationError as exc:
        raise LegacyWriterFreezeError(
            "legacy writer release tree binding differs"
        ) from exc
    observed_aggregate_sha256 = _nonzero_sha256(
        document.get("nginx_aggregate_sha256"),
        label="Nginx aggregate digest",
    )
    observed_nginx_manifest_sha256 = _nonzero_sha256(
        document.get("nginx_manifest_sha256"),
        label="Nginx manifest digest",
    )
    observed_receipt_sha256 = _nonzero_sha256(
        document.get("coordinated_state_receipt_sha256"),
        label="coordinated Nginx receipt digest",
    )
    observed_live_lease_claim_sha256 = _nonzero_sha256(
        document.get("live_lease_claim_sha256"),
        label="live lease claim digest",
    )
    observed_live_lease_claim_epoch = document.get("live_lease_claim_epoch")
    if (
        type(observed_live_lease_claim_epoch) is not int
        or observed_live_lease_claim_epoch < 1
    ):
        raise LegacyWriterFreezeError("live lease claim epoch is invalid")
    observed_role_generation_sha256 = _nonzero_sha256(
        document.get("role_freeze_generation_sha256"),
        label="role freeze generation digest",
    )
    observed_freeze_generation_sha256 = _nonzero_sha256(
        document.get("freeze_generation_sha256"),
        label="global freeze generation digest",
    )
    if (
        (
            release_tree_sha is not None
            and observed_release_tree_sha != release_tree_sha
        )
        or (
            nginx_aggregate_sha256 is not None
            and observed_aggregate_sha256 != nginx_aggregate_sha256
        )
        or (
            nginx_manifest_sha256 is not None
            and observed_nginx_manifest_sha256 != nginx_manifest_sha256
        )
        or (
            coordinated_state_receipt_sha256 is not None
            and observed_receipt_sha256
            != coordinated_state_receipt_sha256
        )
        or (
            role_freeze_generation_sha256 is not None
            and observed_role_generation_sha256
            != role_freeze_generation_sha256
        )
        or (
            live_lease_claim_sha256 is not None
            and observed_live_lease_claim_sha256
            != live_lease_claim_sha256
        )
        or (
            live_lease_claim_epoch is not None
            and observed_live_lease_claim_epoch != live_lease_claim_epoch
        )
        or (
            freeze_generation_sha256 is not None
            and observed_freeze_generation_sha256
            != freeze_generation_sha256
        )
    ):
        raise LegacyWriterFreezeError(
            "legacy writer coordinated Nginx binding differs"
        )
    return _validate_journal(
        document,
        binding=binding,
        release_tree_sha=observed_release_tree_sha,
        nginx_aggregate_sha256=observed_aggregate_sha256,
        nginx_manifest_sha256=observed_nginx_manifest_sha256,
        coordinated_state_receipt_sha256=observed_receipt_sha256,
        live_lease_claim_sha256=observed_live_lease_claim_sha256,
        live_lease_claim_epoch=observed_live_lease_claim_epoch,
        role_freeze_generation_sha256=observed_role_generation_sha256,
        freeze_generation_sha256=observed_freeze_generation_sha256,
        source_container_ids=source_container_ids,
        writer_containers=writer_containers,
    )


def _load_or_create_journal(
    path: Path,
    *,
    binding: SOURCE.SnapshotBinding,
    release_tree_sha: str,
    nginx_aggregate_sha256: str,
    nginx_manifest_sha256: str,
    coordinated_state_receipt_sha256: str,
    live_lease_claim_sha256: str,
    live_lease_claim_epoch: int,
    role_freeze_generation_sha256: str,
    freeze_generation_sha256: str,
    source_container_ids: Mapping[str, str],
    writer_containers: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    expected = _base_journal(
        binding,
        release_tree_sha=release_tree_sha,
        nginx_aggregate_sha256=nginx_aggregate_sha256,
        nginx_manifest_sha256=nginx_manifest_sha256,
        coordinated_state_receipt_sha256=coordinated_state_receipt_sha256,
        live_lease_claim_sha256=live_lease_claim_sha256,
        live_lease_claim_epoch=live_lease_claim_epoch,
        role_freeze_generation_sha256=role_freeze_generation_sha256,
        freeze_generation_sha256=freeze_generation_sha256,
        source_container_ids=source_container_ids,
        writer_containers=writer_containers,
    )
    try:
        read_secure_bytes(
            path,
            label="legacy writer freeze journal",
            owner_uid=0,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError:
        try:
            write_secure_new_bytes(
                path,
                canonical_json(expected),
                label="legacy writer freeze journal",
                mode=0o600,
                max_size=MAX_JSON_BYTES,
            )
            return expected
        except SecureFileError as exc:
            try:
                read_secure_bytes(
                    path,
                    label="legacy writer freeze journal",
                    owner_uid=0,
                    max_size=MAX_JSON_BYTES,
                )
            except SecureFileError as read_exc:
                raise LegacyWriterFreezeError(str(exc)) from read_exc
    return _load_existing_journal(
        path,
        binding=binding,
        release_tree_sha=release_tree_sha,
        nginx_aggregate_sha256=nginx_aggregate_sha256,
        nginx_manifest_sha256=nginx_manifest_sha256,
        coordinated_state_receipt_sha256=None,
        live_lease_claim_sha256=None,
        live_lease_claim_epoch=None,
        role_freeze_generation_sha256=None,
        freeze_generation_sha256=None,
        source_container_ids=source_container_ids,
        writer_containers=writer_containers,
    )


def _write_journal(path: Path, document: dict[str, Any]) -> None:
    document["sequence"] += 1
    document["state_sha256"] = _state_hash(document)
    try:
        write_secure_atomic_bytes(
            path,
            canonical_json(document),
            label="legacy writer freeze journal",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError as exc:
        raise LegacyWriterFreezeError(str(exc)) from exc


def _reset_interactive_transcript(journal: dict[str, Any]) -> None:
    journal["interactive_lease_checkpoint_count"] = 0
    journal["interactive_lease_transcript"] = []
    journal["interactive_lease_transcript_sha256"] = ZERO_SHA256
    journal["interactive_lease_authority_handoff_complete"] = False


def _store_interactive_transcript(
    journal: dict[str, Any],
    protocol: LiveLeaseCheckpointProtocol,
    *,
    require_final: bool,
) -> None:
    summary = protocol.summary(require_final=require_final)
    journal.update(summary)


def _bind_journal_live_lease_epoch(
    journal_path: Path,
    journal: dict[str, Any],
    *,
    action: str,
    coordinated_state_receipt_sha256: str,
    live_lease_claim: Mapping[str, Any],
    live_lease_claim_sha256: str,
    role_freeze_generation_sha256: str,
    freeze_generation_sha256: str,
) -> None:
    claim_epoch = live_lease_claim.get("claim_epoch")
    previous_claim_sha256 = live_lease_claim.get(
        "previous_claim_sha256"
    )
    if type(claim_epoch) is not int or claim_epoch < 1:
        raise LegacyWriterFreezeError("live lease claim epoch is invalid")
    if (
        journal["coordinated_state_receipt_sha256"]
        == coordinated_state_receipt_sha256
    ):
        if journal["live_lease_claim_sha256"] == live_lease_claim_sha256:
            if (
                journal["live_lease_claim_epoch"] != claim_epoch
                or journal["role_freeze_generation_sha256"]
                != role_freeze_generation_sha256
                or journal["freeze_generation_sha256"]
                != freeze_generation_sha256
            ):
                raise LegacyWriterFreezeError(
                    "current legacy freeze epoch binding differs"
                )
            return
        if (
            action != "restore"
            or journal["status"]
            not in {
                "frozen",
                "freezing",
                "reconciliation-required",
                "restoring",
                "restore-readiness-failed",
                "compensation-failed",
            }
            or live_lease_claim_sha256
            in journal["live_lease_claim_history"]
            or claim_epoch <= journal["live_lease_claim_epoch"]
        ):
            raise LegacyWriterFreezeError(
                "live lease claim epoch is stale for current receipt"
            )
        if len(journal["live_lease_claim_history"]) >= 1_000:
            raise LegacyWriterFreezeError(
                "legacy writer live lease history is exhausted"
            )
        journal["live_lease_claim_sha256"] = live_lease_claim_sha256
        journal["live_lease_claim_history"].append(
            live_lease_claim_sha256
        )
        journal["live_lease_claim_epoch"] = claim_epoch
        journal["live_lease_claim_epoch_history"].append(claim_epoch)
        _reset_interactive_transcript(journal)
        _write_journal(journal_path, journal)
        return
    if (
        journal["live_lease_claim_sha256"]
        == live_lease_claim_sha256
        or claim_epoch <= journal["live_lease_claim_epoch"]
    ):
        raise LegacyWriterFreezeError(
            "new coordinated receipt has a stale live lease claim"
        )
    if action not in {"freeze", "restore"}:
        raise LegacyWriterFreezeError(
            "read-only verification cannot advance the freeze epoch"
        )
    allowed_statuses = (
        {"active"}
        if action == "freeze"
        else {
            "frozen",
            "freezing",
            "reconciliation-required",
            "restoring",
            "restore-readiness-failed",
            "compensation-failed",
        }
    )
    if journal["status"] not in allowed_statuses:
        raise LegacyWriterFreezeError(
            "legacy writer journal state cannot advance the freeze epoch"
        )
    if (
        coordinated_state_receipt_sha256
        in journal["coordinated_state_receipt_history"]
        or live_lease_claim_sha256 in journal["live_lease_claim_history"]
        or (
            claim_epoch == journal["live_lease_claim_epoch"] + 1
            and previous_claim_sha256
            != journal["live_lease_claim_sha256"]
        )
    ):
        raise LegacyWriterFreezeError(
            "live lease claim or coordinated receipt epoch is stale"
        )
    if len(journal["coordinated_state_receipt_history"]) >= 1_000:
        raise LegacyWriterFreezeError(
            "legacy writer freeze epoch history is exhausted"
        )
    journal["coordinated_state_receipt_sha256"] = (
        coordinated_state_receipt_sha256
    )
    journal["coordinated_state_receipt_history"].append(
        coordinated_state_receipt_sha256
    )
    journal["live_lease_claim_sha256"] = live_lease_claim_sha256
    journal["live_lease_claim_history"].append(live_lease_claim_sha256)
    journal["live_lease_claim_epoch"] = claim_epoch
    journal["live_lease_claim_epoch_history"].append(claim_epoch)
    journal["role_freeze_generation_sha256"] = (
        role_freeze_generation_sha256
    )
    journal["freeze_generation_sha256"] = freeze_generation_sha256
    _reset_interactive_transcript(journal)
    _write_journal(journal_path, journal)


def _record_failure(document: dict[str, Any], message: str) -> str:
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()
    history = document["failure_history"]
    if len(history) >= 1_000:
        raise LegacyWriterFreezeError(
            "legacy writer failure history is exhausted"
        )
    history.append(digest)
    document["last_error_sha256"] = digest
    return digest


def _revoke_freeze_evidence(path: Path, *, required: bool) -> bool:
    directory_fd = -1
    descriptor = -1
    try:
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            descriptor = os.open(
                path.name,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            if required:
                raise LegacyWriterFreezeError(
                    "frozen state has no revocable freeze evidence"
                )
            return False
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= MAX_JSON_BYTES
        ):
            raise LegacyWriterFreezeError(
                "legacy source freeze evidence is unsafe to revoke"
            )
        os.close(descriptor)
        descriptor = -1
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return True
    except LegacyWriterFreezeError:
        raise
    except OSError as exc:
        raise LegacyWriterFreezeError(
            "legacy source freeze evidence revocation failed"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_fd >= 0:
            os.close(directory_fd)


def _refresh_runtime(
    binding: SOURCE.SnapshotBinding,
) -> tuple[
    SOURCE.SourceInventory,
    dict[str, dict[str, str]],
    dict[str, bool],
]:
    try:
        inventory = SOURCE.inspect_source(binding)
    except SOURCE.SourceSnapshotError as exc:
        raise LegacyWriterFreezeError(
            "legacy source inventory verification failed"
        ) from exc
    _assert_data_running(binding, inventory)
    writers, running = inspect_writer_set(binding, inventory)
    return inventory, writers, running


def _assert_frozen_runtime_unchanged(
    binding: SOURCE.SnapshotBinding,
    *,
    inventory: SOURCE.SourceInventory,
    writers: Mapping[str, Mapping[str, str]],
) -> None:
    after, after_writers, after_running = _refresh_runtime(binding)
    if (
        after.canonical_sha256 != inventory.canonical_sha256
        or after_writers != writers
        or any(after_running.values())
    ):
        raise LegacyWriterFreezeError(
            "legacy frozen runtime changed during zero-writer proof"
        )


def _set_container_running(
    identity: Mapping[str, str],
    *,
    running: bool,
    runner: Runner,
) -> None:
    action = "start" if running else "stop"
    arguments = [DOCKER, action]
    if not running:
        arguments.extend(("--time", "30"))
    arguments.append(identity["id"])
    runner(arguments, 180)
    try:
        document = SOURCE._inspect_required("container", identity["id"])
    except SOURCE.SourceSnapshotError as exc:
        raise LegacyWriterFreezeError(
            f"legacy writer container disappeared after {action}"
        ) from exc
    state = document.get("State")
    if (
        document.get("Id") != identity["id"]
        or document.get("Name") != f"/{identity['name']}"
        or not isinstance(state, Mapping)
        or state.get("Running") is not running
        or state.get("Paused") is True
        or state.get("Restarting") is True
        or state.get("Dead") is True
    ):
        raise LegacyWriterFreezeError(
            f"legacy writer container {action} readback differs"
        )


def _writer_readiness_sample(
    binding: SOURCE.SnapshotBinding,
    writer_containers: Mapping[str, Mapping[str, str]],
    *,
    runner: Runner,
) -> dict[str, dict[str, Any]]:
    sample: dict[str, dict[str, Any]] = {}
    expected_rows = {
        kind: (name, service)
        for kind, name, service in ROLE_WRITERS[binding.role]
    }
    if set(writer_containers) != set(expected_rows):
        raise LegacyWriterFreezeError(
            "recorded legacy writer readiness set differs"
        )
    for kind, identity in sorted(writer_containers.items()):
        expected_name, expected_service = expected_rows[kind]
        try:
            document = SOURCE._inspect_required("container", identity["id"])
        except SOURCE.SourceSnapshotError as exc:
            raise LegacyWriterFreezeError(
                "recorded legacy writer is unavailable during readiness"
            ) from exc
        observed, running = _writer_identity(
            document,
            binding=binding,
            source_image_id=identity["image_id"],
            kind=kind,
            expected_name=expected_name,
            expected_service=expected_service,
        )
        state = document.get("State")
        config = document.get("Config")
        health = state.get("Health") if isinstance(state, Mapping) else None
        healthcheck = (
            config.get("Healthcheck") if isinstance(config, Mapping) else None
        )
        started_at = state.get("StartedAt") if isinstance(state, Mapping) else None
        container_pid = state.get("Pid") if isinstance(state, Mapping) else None
        restart_count = document.get("RestartCount")
        if healthcheck is None:
            healthcheck_configured = False
        elif isinstance(healthcheck, Mapping):
            healthcheck_test = healthcheck.get("Test")
            if (
                not isinstance(healthcheck_test, list)
                or not healthcheck_test
                or any(not isinstance(value, str) for value in healthcheck_test)
                or healthcheck_test[0] not in {"NONE", "CMD", "CMD-SHELL"}
            ):
                raise LegacyWriterFreezeError(
                    f"legacy {kind} healthcheck configuration differs"
                )
            healthcheck_configured = healthcheck_test[0] != "NONE"
        else:
            raise LegacyWriterFreezeError(
                f"legacy {kind} healthcheck configuration differs"
            )
        if (
            observed != identity
            or not running
            or not isinstance(started_at, str)
            or not 1 <= len(started_at) <= 128
            or type(container_pid) is not int
            or container_pid <= 0
            or type(restart_count) is not int
            or restart_count < 0
            or (
                healthcheck_configured
                and (
                    not isinstance(health, Mapping)
                    or health.get("Status") != "healthy"
                )
            )
            or (not healthcheck_configured and health is not None)
        ):
            raise LegacyWriterFreezeError(
                f"legacy {kind} readiness state differs"
            )
        process_output = runner(
            [DOCKER, "top", identity["id"], "-eo", "pid=,comm="],
            5,
        )
        process_lines = [
            line.strip() for line in process_output.splitlines() if line.strip()
        ]
        process_ids: list[int] = []
        process_commands: dict[int, str] = {}
        for line in process_lines:
            fields = line.split(None, 1)
            if (
                len(fields) != 2
                or not fields[0].isdigit()
                or PROCESS_COMM_RE.fullmatch(fields[1]) is None
            ):
                raise LegacyWriterFreezeError(
                    f"legacy {kind} process readback is invalid"
                )
            process_id = int(fields[0])
            process_ids.append(process_id)
            process_commands[process_id] = fields[1]
        if (
            not process_ids
            or len(process_ids) != len(set(process_ids))
            or any(identifier <= 0 for identifier in process_ids)
            or container_pid not in process_ids
        ):
            raise LegacyWriterFreezeError(
                f"legacy {kind} has no stable process surface"
            )
        sample[kind] = {
            "container_id": identity["id"],
            "container_pid": container_pid,
            "container_command": process_commands[container_pid],
            "started_at": started_at,
            "restart_count": restart_count,
        }
        if kind != "application":
            sample[kind]["process_surface"] = [
                [process_id, process_commands[process_id]]
                for process_id in sorted(process_commands)
            ]
    return sample


def _legacy_api_readiness(*, runner: Runner) -> int:
    status = runner(
        [
            CURL,
            "--silent",
            "--show-error",
            "--fail",
            "--output",
            "/dev/null",
            "--write-out",
            "%{http_code}",
            "--max-time",
            "5",
            LEGACY_API_READY_URL,
        ],
        7,
    )
    if (
        len(status) != 3
        or not status.isdigit()
        or not 200 <= int(status) <= 299
    ):
        raise LegacyWriterFreezeError(
            "legacy application readiness HTTP status differs"
        )
    return int(status)


def _await_writer_readiness(
    binding: SOURCE.SnapshotBinding,
    writer_containers: Mapping[str, Mapping[str, str]],
    *,
    runner: Runner,
    sleep_fn: SleepFn,
    checkpoint: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    live_checkpoint = checkpoint if checkpoint is not None else lambda _name: None
    http_status: int | None = None
    last_error: LegacyWriterFreezeError | None = None
    for attempt in range(READINESS_HTTP_ATTEMPTS):
        try:
            live_checkpoint(f"readiness-http:{attempt + 1}")
            http_status = _legacy_api_readiness(runner=runner)
            break
        except LegacyWriterFreezeError as exc:
            last_error = exc
            if attempt + 1 < READINESS_HTTP_ATTEMPTS:
                sleep_fn(READINESS_RETRY_SECONDS)
    if http_status is None:
        raise LegacyWriterFreezeError(
            "legacy application did not become HTTP-ready"
        ) from last_error

    stable: list[dict[str, dict[str, Any]]] = []
    last_error = None
    for attempt in range(READINESS_STABILITY_ATTEMPTS):
        try:
            live_checkpoint(f"readiness-stability:{attempt + 1}")
            sample = _writer_readiness_sample(
                binding,
                writer_containers,
                runner=runner,
            )
        except LegacyWriterFreezeError as exc:
            stable = []
            last_error = exc
        else:
            try:
                http_status = _legacy_api_readiness(runner=runner)
            except LegacyWriterFreezeError as exc:
                stable = []
                last_error = exc
                if attempt + 1 < READINESS_STABILITY_ATTEMPTS:
                    sleep_fn(READINESS_RETRY_SECONDS)
                continue
            if stable and stable[-1] != sample:
                stable = [sample]
            else:
                stable.append(sample)
            if len(stable) == READINESS_STABLE_SAMPLES:
                return {
                    "application_http_status": http_status,
                    "legacy_ready_for_nginx_restore": True,
                    "ready_writer_container_count": len(sample),
                    "stable_sample_count": len(stable),
                    "readiness_sha256": hashlib.sha256(
                        canonical_json(sample)
                    ).hexdigest(),
                }
        if attempt + 1 < READINESS_STABILITY_ATTEMPTS:
            sleep_fn(READINESS_RETRY_SECONDS)
    raise LegacyWriterFreezeError(
        "legacy writer processes did not become stably ready"
    ) from last_error


def _restore_recorded(
    journal_path: Path,
    journal: dict[str, Any],
    *,
    binding: SOURCE.SnapshotBinding,
    evidence_path: Path,
    live_lease_verify: Callable[[], tuple[str, str]],
    protocol: LiveLeaseCheckpointProtocol,
    start_authorized: set[str],
    runner: Runner,
    sleep_fn: SleepFn,
    proc_root: Path,
) -> dict[str, Any]:
    live_lease_verify()
    _revoke_freeze_evidence(
        evidence_path,
        required=False,
    )
    journal["status"] = "restoring"
    _write_journal(journal_path, journal)
    live_lease_verify()

    # A killed restore may leave any prefix of the exact writer set running.
    # Re-establish the durable all-stopped boundary before every fresh start
    # transcript so a resumed result never omits a start checkpoint.
    for kind in journal["previously_running"]:
        live_lease_verify()
        identity = journal["writer_containers"][kind]
        try:
            document = SOURCE._inspect_required("container", identity["id"])
        except SOURCE.SourceSnapshotError as exc:
            message = (
                "recorded legacy writer container is unavailable for restore"
            )
            journal["status"] = "compensation-failed"
            _record_failure(journal, message)
            _write_journal(journal_path, journal)
            raise LegacyWriterFreezeError(message) from exc
        state = document.get("State")
        if (
            document.get("Id") != identity["id"]
            or document.get("Name") != f"/{identity['name']}"
            or not isinstance(state, Mapping)
        ):
            message = (
                "recorded legacy writer identity differs during restore"
            )
            journal["status"] = "compensation-failed"
            _record_failure(journal, message)
            _write_journal(journal_path, journal)
            raise LegacyWriterFreezeError(message)
        if state.get("Running") is True:
            protocol.checkpoint(f"before-stop:{kind}")
            live_lease_verify()
            try:
                _set_container_running(identity, running=False, runner=runner)
            except LegacyWriterFreezeError as exc:
                journal["status"] = "compensation-failed"
                _record_failure(journal, str(exc))
                _write_journal(journal_path, journal)
                raise
            protocol.checkpoint(f"after-stop:{kind}")
        live_lease_verify()

    normalized_inventory, normalized_writers, normalized_running = (
        _refresh_runtime(binding)
    )
    if (
        normalized_writers != journal["writer_containers"]
        or any(normalized_running.values())
    ):
        message = "legacy writer normalization did not stop all exact writers"
        journal["status"] = "compensation-failed"
        _record_failure(journal, message)
        _write_journal(journal_path, journal)
        raise LegacyWriterFreezeError(message)
    live_lease_verify()
    _zero_writer_readback(
        binding,
        normalized_inventory,
        runner=runner,
        sleep_fn=sleep_fn,
        proc_root=proc_root,
    )
    live_lease_verify()
    journal["stopped"] = sorted(journal["previously_running"])
    _write_journal(journal_path, journal)

    for kind in journal["previously_running"]:
        live_lease_verify()
        identity = journal["writer_containers"][kind]
        protocol.checkpoint(f"before-start:{kind}")
        live_lease_verify()
        start_authorized.add(kind)
        try:
            _set_container_running(identity, running=True, runner=runner)
        except LegacyWriterFreezeError as exc:
            journal["status"] = "compensation-failed"
            _record_failure(journal, str(exc))
            _write_journal(journal_path, journal)
            raise
        protocol.checkpoint(f"after-start:{kind}")
        live_lease_verify()
    try:
        live_lease_verify()
        readiness = _await_writer_readiness(
            binding,
            journal["writer_containers"],
            runner=runner,
            sleep_fn=sleep_fn,
            checkpoint=protocol.checkpoint,
        )
        live_lease_verify()
    except LegacyWriterFreezeError as exc:
        journal["status"] = "restore-readiness-failed"
        _record_failure(journal, str(exc))
        _write_journal(journal_path, journal)
        raise
    return readiness


def _compensate_interactive_restore_failure(
    journal_path: Path,
    journal: dict[str, Any],
    *,
    binding: SOURCE.SnapshotBinding,
    protocol: LiveLeaseCheckpointProtocol,
    start_authorized: set[str],
    original_error: BaseException,
    runner: Runner,
    sleep_fn: SleepFn,
    proc_root: Path,
) -> None:
    compensation_error: BaseException | None = None
    try:
        for kind in journal["previously_running"]:
            if kind not in start_authorized:
                continue
            identity = journal["writer_containers"][kind]
            document = SOURCE._inspect_required("container", identity["id"])
            state = document.get("State")
            if (
                document.get("Id") != identity["id"]
                or document.get("Name") != f"/{identity['name']}"
                or not isinstance(state, Mapping)
            ):
                raise LegacyWriterFreezeError(
                    "interactive restore compensation identity differs"
                )
            if state.get("Running") is True:
                _set_container_running(
                    identity,
                    running=False,
                    runner=runner,
                )
        inventory, writers, running = _refresh_runtime(binding)
        if writers != journal["writer_containers"]:
            raise LegacyWriterFreezeError(
                "interactive restore compensation identity differs"
            )
        if any(running.values()):
            if any(running[kind] for kind in start_authorized):
                raise LegacyWriterFreezeError(
                    "interactive restore compensation did not stop "
                    "authorized starts"
                )
            journal["stopped"] = sorted(
                kind for kind, is_running in running.items() if not is_running
            )
            journal["status"] = "restoring"
        else:
            _zero_writer_readback(
                binding,
                inventory,
                runner=runner,
                sleep_fn=sleep_fn,
                proc_root=proc_root,
            )
            journal["stopped"] = sorted(journal["previously_running"])
            journal["status"] = "reconciliation-required"
    except BaseException as exc:
        compensation_error = exc
        journal["status"] = "compensation-failed"
    _store_interactive_transcript(
        journal,
        protocol,
        require_final=False,
    )
    _record_failure(
        journal,
        f"{type(original_error).__name__}:{str(original_error)}",
    )
    if compensation_error is not None:
        _record_failure(
            journal,
            f"{type(compensation_error).__name__}:{str(compensation_error)}",
        )
    _write_journal(journal_path, journal)
    if compensation_error is not None:
        raise LegacyWriterFreezeError(
            "interactive restore failed and all-stopped compensation "
            "could not be proven"
        ) from compensation_error


@contextmanager
def hold_verified_freeze(
    binding: SOURCE.SnapshotBinding,
    *,
    freeze_path: Path,
    live_lease_claim: Path,
    live_lease_claim_sha256: str,
    secret_root: Path = SECRET_ROOT_PREFIX,
    runner: Runner = run_command,
    sleep_fn: SleepFn = time.sleep,
    proc_root: Path = Path("/proc"),
) -> Iterator[Callable[[], dict[str, int]]]:
    """Hold the operation freeze lock and provide a repeatable live verifier.

    The frozen-final snapshot producer uses this guard across source capture
    and invokes the returned verifier again immediately before publishing its
    final directory. This prevents a stale evidence file from authorizing a
    new final snapshot after writer restoration.
    """

    if freeze_path.name != EVIDENCE_FILENAME:
        raise LegacyWriterFreezeError(
            "freeze evidence path is not the canonical operation file"
        )
    expected_directory = state_directory(binding, secret_root=secret_root)
    if freeze_path.parent != expected_directory:
        raise LegacyWriterFreezeError(
            "freeze evidence path is outside the canonical operation directory"
        )
    _verify_private_directory(expected_directory)
    live_lease_claim_sha256 = _nonzero_sha256(
        live_lease_claim_sha256,
        label="live lease claim digest",
    )
    if live_lease_claim != live_lease_claim_path(
        binding,
        live_lease_claim_sha256,
        secret_root=secret_root,
    ):
        raise LegacyWriterFreezeError(
            "live lease claim path is not canonical"
        )
    with _exclusive_lock(expected_directory):
        try:
            evidence, evidence_sha256 = SOURCE.load_freeze_evidence(
                freeze_path,
                binding,
                live_lease_claim_sha256=live_lease_claim_sha256,
            )
        except SOURCE.SourceSnapshotError as exc:
            raise LegacyWriterFreezeError(
                "legacy source freeze evidence is invalid"
            ) from exc

        def verify_live() -> dict[str, int]:
            try:
                current_evidence, current_evidence_sha256 = (
                    SOURCE.load_freeze_evidence(
                        freeze_path,
                        binding,
                        live_lease_claim_sha256=(
                            live_lease_claim_sha256
                        ),
                    )
                )
            except SOURCE.SourceSnapshotError as exc:
                raise LegacyWriterFreezeError(
                    "legacy source freeze evidence changed"
                ) from exc
            if (
                current_evidence != evidence
                or current_evidence_sha256 != evidence_sha256
            ):
                raise LegacyWriterFreezeError(
                    "legacy source freeze evidence changed"
                )
            inventory, writers, running = _refresh_runtime(binding)
            source_container_ids = {
                kind: str(inventory.containers[kind]["id"])
                for kind in SOURCE.SOURCE_CONTAINERS
            }
            if (
                evidence["source_container_ids"] != source_container_ids
                or any(running.values())
            ):
                raise LegacyWriterFreezeError(
                    "legacy frozen runtime identity or state differs"
                )
            journal = _load_existing_journal(
                expected_directory / JOURNAL_FILENAME,
                binding=binding,
                release_tree_sha=None,
                nginx_aggregate_sha256=None,
                nginx_manifest_sha256=None,
                coordinated_state_receipt_sha256=None,
                live_lease_claim_sha256=live_lease_claim_sha256,
                live_lease_claim_epoch=None,
                role_freeze_generation_sha256=None,
                freeze_generation_sha256=evidence[
                    "freeze_generation_sha256"
                ],
                source_container_ids=source_container_ids,
                writer_containers=writers,
            )
            if journal["status"] != "frozen":
                raise LegacyWriterFreezeError(
                    "legacy writer journal is not durably frozen"
                )
            current_receipt_path = coordinated_receipt_path(
                binding,
                journal["coordinated_state_receipt_sha256"],
                secret_root=secret_root,
            )
            claim = _load_live_lease_claim(
                live_lease_claim,
                binding=binding,
                release_tree_sha=journal["release_tree_sha"],
                nginx_aggregate_sha256=journal[
                    "nginx_aggregate_sha256"
                ],
                coordinated_state_receipt=current_receipt_path,
                coordinated_state_receipt_sha256=journal[
                    "coordinated_state_receipt_sha256"
                ],
                expected_sha256=live_lease_claim_sha256,
                secret_root=secret_root,
            )
            if (
                claim.get("owner_action") != CAPTURE_OWNER_ACTION
                or claim.get("claim_epoch")
                != journal["live_lease_claim_epoch"]
            ):
                raise LegacyWriterFreezeError(
                    "live lease claim owner or epoch differs from freeze journal"
                )
            _installed_nginx_freeze_readback(
                binding,
                journal,
                secret_root=secret_root,
            )
            result = _zero_writer_readback(
                binding,
                inventory,
                runner=runner,
                sleep_fn=sleep_fn,
                proc_root=proc_root,
            )
            _assert_frozen_runtime_unchanged(
                binding,
                inventory=inventory,
                writers=writers,
            )
            return result

        verify_live()
        yield verify_live


def _publish_freeze_evidence(
    path: Path,
    *,
    binding: SOURCE.SnapshotBinding,
    freeze_generation_sha256: str,
    live_lease_claim_sha256: str,
    source_container_ids: Mapping[str, str],
) -> tuple[dict[str, Any], str]:
    document = {
        "schema": SOURCE.FREEZE_SCHEMA,
        "operation_id": binding.operation_id,
        "release_sha": binding.release_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "role": binding.role,
        "source_project": binding.source_project,
        "controller_manifest_sha256": binding.controller_manifest_sha256,
        "approval_sha256": binding.approval_sha256,
        "production_vhosts": SOURCE._expected_vhosts(),
        "source_container_ids": dict(sorted(source_container_ids.items())),
        "freeze_generation_sha256": freeze_generation_sha256,
        "live_lease_claim_sha256": live_lease_claim_sha256,
        "freeze_active": True,
        "write_capable_route_count": 0,
        "legacy_writer_process_count": 0,
        "writer_database_client_count": 0,
        "file_mutator_process_count": 0,
    }
    payload = canonical_json(document)
    try:
        write_secure_new_bytes(
            path,
            payload,
            label="legacy source freeze evidence",
            mode=0o600,
            max_size=MAX_JSON_BYTES,
        )
    except SecureFileError:
        try:
            existing, digest = SOURCE.load_freeze_evidence(
                path,
                binding,
                source_container_ids=source_container_ids,
                live_lease_claim_sha256=live_lease_claim_sha256,
            )
        except SOURCE.SourceSnapshotError as exc:
            raise LegacyWriterFreezeError(
                "existing legacy source freeze evidence differs"
            ) from exc
        if existing != document:
            raise LegacyWriterFreezeError(
                "existing legacy source freeze evidence is not idempotent"
            )
        return existing, digest
    return document, hashlib.sha256(payload).hexdigest()


def build_plan(
    binding: SOURCE.SnapshotBinding,
    *,
    action: str,
    release_tree_sha: str,
    nginx_aggregate_sha256: str,
    nginx_manifest_sha256: str,
    coordinated_state_receipt: Path,
    coordinated_state_receipt_sha256: str,
    live_lease_claim: Path,
    live_lease_claim_sha256: str,
    secret_root: Path,
) -> dict[str, Any]:
    directory = state_directory(binding, secret_root=secret_root)
    required = (
        confirmation_phrase(
            action,
            binding,
            nginx_aggregate_sha256=nginx_aggregate_sha256,
            nginx_manifest_sha256=nginx_manifest_sha256,
            coordinated_state_receipt_sha256=(
                coordinated_state_receipt_sha256
            ),
            live_lease_claim_sha256=live_lease_claim_sha256,
        )
        if action in {"freeze", "restore"}
        else None
    )
    return {
        "schema": PLAN_SCHEMA,
        "status": "planned",
        "action": action,
        "operation_id": binding.operation_id,
        "release_sha": binding.release_sha,
        "release_tree_sha": release_tree_sha,
        "legacy_release_sha": binding.legacy_release_sha,
        "role": binding.role,
        "source_project": binding.source_project,
        "binding_sha256": binding.canonical_sha256,
        "nginx_aggregate_sha256": nginx_aggregate_sha256,
        "nginx_manifest_sha256": nginx_manifest_sha256,
        "coordinated_state_receipt_path": str(
            coordinated_state_receipt
        ),
        "coordinated_state_receipt_sha256": (
            coordinated_state_receipt_sha256
        ),
        "live_lease_claim_path": str(live_lease_claim),
        "live_lease_claim_sha256": live_lease_claim_sha256,
        "writer_containers": [
            name for _kind, name, _service in ROLE_WRITERS[binding.role]
        ],
        "data_containers_preserved": [
            binding.containers[kind] for kind in DATA_KINDS
        ],
        "journal_path": str(directory / JOURNAL_FILENAME),
        "freeze_evidence_path": str(directory / EVIDENCE_FILENAME),
        "required_confirmation": required,
        "docker_contacted": False,
        "nginx_contacted": False,
        "production_mutated": False,
    }


def execute(
    *,
    binding_path: Path,
    action: str,
    release_tree_sha: str,
    nginx_aggregate_sha256: str,
    nginx_manifest: Path,
    nginx_manifest_sha256: str,
    nginx_archive: Path,
    coordinated_state_receipt: Path,
    coordinated_state_receipt_sha256: str,
    live_lease_claim: Path,
    live_lease_claim_sha256: str,
    apply: bool,
    confirm: str | None,
    secret_root: Path = SECRET_ROOT_PREFIX,
    runner: Runner = run_command,
    sleep_fn: SleepFn = time.sleep,
    proc_root: Path = Path("/proc"),
    checkpoint_exchange: CheckpointExchange | None = None,
) -> dict[str, Any]:
    try:
        binding = SOURCE.load_binding(binding_path)
    except SOURCE.SourceSnapshotError as exc:
        raise LegacyWriterFreezeError(
            "frozen-final source binding is invalid"
        ) from exc
    if binding.mode != "frozen-final" or binding.role not in ROLE_WRITERS:
        raise LegacyWriterFreezeError(
            "legacy writer freeze requires a canonical frozen-final binding"
        )
    try:
        NGINX._release_sha(release_tree_sha, label="release_tree_sha")
    except NGINX.NginxGenerationError as exc:
        raise LegacyWriterFreezeError("release tree SHA is invalid") from exc
    if release_tree_sha == binding.release_sha:
        raise LegacyWriterFreezeError(
            "release commit and tree identities must differ"
        )
    nginx_manifest_sha256 = _nonzero_sha256(
        nginx_manifest_sha256,
        label="Nginx manifest digest",
    )
    nginx_aggregate_sha256 = _nonzero_sha256(
        nginx_aggregate_sha256,
        label="Nginx aggregate digest",
    )
    coordinated_state_receipt_sha256 = _nonzero_sha256(
        coordinated_state_receipt_sha256,
        label="coordinated Nginx receipt digest",
    )
    expected_receipt_path = coordinated_receipt_path(
        binding,
        coordinated_state_receipt_sha256,
        secret_root=secret_root,
    )
    if coordinated_state_receipt != expected_receipt_path:
        raise LegacyWriterFreezeError(
            "coordinated Nginx receipt path is not canonical"
        )
    live_lease_claim_sha256 = _nonzero_sha256(
        live_lease_claim_sha256,
        label="live lease claim digest",
    )
    expected_claim_path = live_lease_claim_path(
        binding,
        live_lease_claim_sha256,
        secret_root=secret_root,
    )
    if live_lease_claim != expected_claim_path:
        raise LegacyWriterFreezeError(
            "live lease claim path is not canonical"
        )
    if action not in {"plan", "freeze", "verify", "restore"}:
        raise LegacyWriterFreezeError("legacy writer action is not allowlisted")
    if checkpoint_exchange is not None and (
        not apply or action != "restore"
    ):
        raise LegacyWriterFreezeError(
            "interactive controller live lease protocol is restore-apply-only"
        )
    if not apply:
        if confirm is not None:
            raise LegacyWriterFreezeError(
                "--confirm is valid only with --apply"
            )
        return build_plan(
            binding,
            action=action,
            release_tree_sha=release_tree_sha,
            nginx_aggregate_sha256=nginx_aggregate_sha256,
            nginx_manifest_sha256=nginx_manifest_sha256,
            coordinated_state_receipt=coordinated_state_receipt,
            coordinated_state_receipt_sha256=(
                coordinated_state_receipt_sha256
            ),
            live_lease_claim=live_lease_claim,
            live_lease_claim_sha256=live_lease_claim_sha256,
            secret_root=secret_root,
        )
    if action == "plan":
        raise LegacyWriterFreezeError("plan cannot execute with --apply")
    if action == "restore" and checkpoint_exchange is None:
        raise LegacyWriterFreezeError(
            "restore requires the interactive controller live lease protocol"
        )
    if action in {"freeze", "restore"}:
        required = confirmation_phrase(
            action,
            binding,
            nginx_aggregate_sha256=nginx_aggregate_sha256,
            nginx_manifest_sha256=nginx_manifest_sha256,
            coordinated_state_receipt_sha256=(
                coordinated_state_receipt_sha256
            ),
            live_lease_claim_sha256=live_lease_claim_sha256,
        )
        if confirm != required:
            raise LegacyWriterFreezeError(
                "exact legacy writer action confirmation is required"
            )
    elif confirm is not None:
        raise LegacyWriterFreezeError(
            "read-only verification does not accept a confirmation"
        )

    operation_root = secret_root / binding.operation_id
    directory = _ensure_private_children(operation_root, binding.role)
    journal_path = directory / JOURNAL_FILENAME
    evidence_path = directory / EVIDENCE_FILENAME

    with _exclusive_lock(directory):
        observed_nginx_manifest_sha256 = _file_sha256(
            nginx_manifest,
            label="Nginx role manifest",
        )
        if observed_nginx_manifest_sha256 != nginx_manifest_sha256:
            raise LegacyWriterFreezeError("Nginx role manifest digest differs")

        live_lease_claim_document = _load_live_lease_claim(
            live_lease_claim,
            binding=binding,
            release_tree_sha=release_tree_sha,
            nginx_aggregate_sha256=nginx_aggregate_sha256,
            coordinated_state_receipt=coordinated_state_receipt,
            coordinated_state_receipt_sha256=(
                coordinated_state_receipt_sha256
            ),
            expected_sha256=live_lease_claim_sha256,
            secret_root=secret_root,
        )
        expected_owner_action = (
            RESTORE_OWNER_ACTION
            if action == "restore"
            else CAPTURE_OWNER_ACTION
        )
        if (
            live_lease_claim_document.get("owner_action")
            != expected_owner_action
        ):
            raise LegacyWriterFreezeError(
                "live lease claim owner action differs"
            )

        def readback_freeze_generations() -> tuple[str, str]:
            current_claim = _load_live_lease_claim(
                live_lease_claim,
                binding=binding,
                release_tree_sha=release_tree_sha,
                nginx_aggregate_sha256=nginx_aggregate_sha256,
                coordinated_state_receipt=coordinated_state_receipt,
                coordinated_state_receipt_sha256=(
                    coordinated_state_receipt_sha256
                ),
                expected_sha256=live_lease_claim_sha256,
                secret_root=secret_root,
            )
            if current_claim != live_lease_claim_document:
                raise LegacyWriterFreezeError(
                    "live lease claim changed during legacy writer action"
                )
            receipt, _receipt_sha256 = _load_coordinated_receipt(
                coordinated_state_receipt,
                binding=binding,
                release_tree_sha=release_tree_sha,
                nginx_aggregate_sha256=nginx_aggregate_sha256,
                expected_sha256=coordinated_state_receipt_sha256,
                secret_root=secret_root,
            )
            readback = _nginx_readback(
                binding=binding,
                release_tree_sha=release_tree_sha,
                nginx_manifest=nginx_manifest,
                nginx_manifest_sha256=nginx_manifest_sha256,
                nginx_archive=nginx_archive,
            )
            return _bind_local_readback_to_receipt(
                receipt,
                binding=binding,
                readback=readback,
                nginx_manifest_sha256=nginx_manifest_sha256,
            )

        (
            role_freeze_generation_sha256,
            freeze_generation_sha256,
        ) = readback_freeze_generations()
        inventory, writers, running = _refresh_runtime(binding)
        source_container_ids = {
            kind: str(inventory.containers[kind]["id"])
            for kind in SOURCE.SOURCE_CONTAINERS
        }
        if action == "freeze":
            journal = _load_or_create_journal(
                journal_path,
                binding=binding,
                release_tree_sha=release_tree_sha,
                nginx_aggregate_sha256=nginx_aggregate_sha256,
                nginx_manifest_sha256=nginx_manifest_sha256,
                coordinated_state_receipt_sha256=(
                    coordinated_state_receipt_sha256
                ),
                live_lease_claim_sha256=live_lease_claim_sha256,
                live_lease_claim_epoch=live_lease_claim_document[
                    "claim_epoch"
                ],
                role_freeze_generation_sha256=(
                    role_freeze_generation_sha256
                ),
                freeze_generation_sha256=freeze_generation_sha256,
                source_container_ids=source_container_ids,
                writer_containers=writers,
            )
        else:
            exact_epoch = action == "verify"
            journal = _load_existing_journal(
                journal_path,
                binding=binding,
                release_tree_sha=release_tree_sha,
                nginx_aggregate_sha256=nginx_aggregate_sha256,
                nginx_manifest_sha256=nginx_manifest_sha256,
                coordinated_state_receipt_sha256=(
                    coordinated_state_receipt_sha256
                    if exact_epoch
                    else None
                ),
                live_lease_claim_sha256=(
                    live_lease_claim_sha256 if exact_epoch else None
                ),
                live_lease_claim_epoch=(
                    live_lease_claim_document["claim_epoch"]
                    if exact_epoch
                    else None
                ),
                role_freeze_generation_sha256=(
                    role_freeze_generation_sha256
                    if exact_epoch
                    else None
                ),
                freeze_generation_sha256=(
                    freeze_generation_sha256 if exact_epoch else None
                ),
                source_container_ids=source_container_ids,
                writer_containers=writers,
            )
        _bind_journal_live_lease_epoch(
            journal_path,
            journal,
            action=action,
            coordinated_state_receipt_sha256=(
                coordinated_state_receipt_sha256
            ),
            live_lease_claim=live_lease_claim_document,
            live_lease_claim_sha256=live_lease_claim_sha256,
            role_freeze_generation_sha256=(
                role_freeze_generation_sha256
            ),
            freeze_generation_sha256=freeze_generation_sha256,
        )
        if journal["writer_containers"] != writers:
            raise LegacyWriterFreezeError(
                "legacy writer container identity changed"
            )

        if action == "restore":
            if journal["status"] not in {
                "frozen",
                "freezing",
                "reconciliation-required",
                "restoring",
                "restore-readiness-failed",
                "compensation-failed",
                "active",
            }:
                raise LegacyWriterFreezeError(
                    "legacy writer journal is not restorable"
                )
            if readback_freeze_generations() != (
                role_freeze_generation_sha256,
                freeze_generation_sha256,
            ):
                raise LegacyWriterFreezeError(
                    "legacy freeze Nginx generation changed before restore"
                )
            assert checkpoint_exchange is not None
            protocol = LiveLeaseCheckpointProtocol(
                binding=binding,
                claim_sha256=live_lease_claim_sha256,
                claim_epoch=journal["live_lease_claim_epoch"],
                exchange=checkpoint_exchange,
            )
            start_authorized: set[str] = set()
            try:
                readiness = _restore_recorded(
                    journal_path,
                    journal,
                    binding=binding,
                    evidence_path=evidence_path,
                    live_lease_verify=readback_freeze_generations,
                    protocol=protocol,
                    start_authorized=start_authorized,
                    runner=runner,
                    sleep_fn=sleep_fn,
                    proc_root=proc_root,
                )
                (
                    _inventory,
                    refreshed_writers,
                    refreshed_running,
                ) = _refresh_runtime(binding)
                if (
                    refreshed_writers != writers
                    or not all(refreshed_running.values())
                ):
                    raise LegacyWriterFreezeError(
                        "legacy writer restore readback differs"
                    )
                protocol.checkpoint("before-result")
            except BaseException as exc:
                _compensate_interactive_restore_failure(
                    journal_path,
                    journal,
                    binding=binding,
                    protocol=protocol,
                    start_authorized=start_authorized,
                    original_error=exc,
                    runner=runner,
                    sleep_fn=sleep_fn,
                    proc_root=proc_root,
                )
                raise
            journal["stopped"] = []
            journal["status"] = "active"
            journal["last_error_sha256"] = ZERO_SHA256
            _store_interactive_transcript(
                journal,
                protocol,
                require_final=True,
            )
            _write_journal(journal_path, journal)
            transcript = protocol.summary(require_final=True)
            return {
                "schema": RESULT_SCHEMA,
                "status": "restored-ready",
                "action": action,
                "operation_id": binding.operation_id,
                "release_sha": binding.release_sha,
                "legacy_release_sha": binding.legacy_release_sha,
                "role": binding.role,
                "binding_sha256": binding.canonical_sha256,
                "nginx_manifest_sha256": nginx_manifest_sha256,
                "nginx_aggregate_sha256": nginx_aggregate_sha256,
                "coordinated_state_receipt_sha256": (
                    coordinated_state_receipt_sha256
                ),
                "live_lease_claim_sha256": live_lease_claim_sha256,
                "live_lease_claim_epoch": journal[
                    "live_lease_claim_epoch"
                ],
                "role_freeze_generation_sha256": (
                    role_freeze_generation_sha256
                ),
                "freeze_generation_sha256": freeze_generation_sha256,
                "journal_sha256": journal["state_sha256"],
                "freeze_evidence_sha256": None,
                "freeze_evidence_revoked": True,
                "all_exact_writer_containers_ready": True,
                "expected_writer_container_count": len(writers),
                "legacy_writer_process_count": None,
                "writer_database_client_count": None,
                "file_mutator_process_count": None,
                "database_container_running": True,
                "redis_container_running": True,
                **readiness,
                **transcript,
                "production_mutated": True,
            }

        if action == "verify":
            if journal["status"] != "frozen":
                raise LegacyWriterFreezeError(
                    "legacy writers are not in the durable frozen state"
                )
            if any(running.values()):
                raise LegacyWriterFreezeError(
                    "a legacy writer container is running"
                )
            zero = _zero_writer_readback(
                binding,
                inventory,
                runner=runner,
                sleep_fn=sleep_fn,
                proc_root=proc_root,
            )
            _assert_frozen_runtime_unchanged(
                binding,
                inventory=inventory,
                writers=writers,
            )
            if readback_freeze_generations() != (
                role_freeze_generation_sha256,
                freeze_generation_sha256,
            ):
                raise LegacyWriterFreezeError(
                    "legacy freeze Nginx generation changed during verification"
                )
            try:
                _evidence, evidence_sha256 = SOURCE.load_freeze_evidence(
                    evidence_path,
                    binding,
                    source_container_ids=source_container_ids,
                    live_lease_claim_sha256=live_lease_claim_sha256,
                )
            except SOURCE.SourceSnapshotError as exc:
                raise LegacyWriterFreezeError(
                    "legacy source freeze evidence verification failed"
                ) from exc
            return {
                "schema": RESULT_SCHEMA,
                "status": "verified-frozen",
                "action": action,
                "operation_id": binding.operation_id,
                "release_sha": binding.release_sha,
                "legacy_release_sha": binding.legacy_release_sha,
                "role": binding.role,
                "binding_sha256": binding.canonical_sha256,
                "nginx_manifest_sha256": nginx_manifest_sha256,
                "nginx_aggregate_sha256": nginx_aggregate_sha256,
                "coordinated_state_receipt_sha256": (
                    coordinated_state_receipt_sha256
                ),
                "live_lease_claim_sha256": live_lease_claim_sha256,
                "live_lease_claim_epoch": journal[
                    "live_lease_claim_epoch"
                ],
                "role_freeze_generation_sha256": (
                    role_freeze_generation_sha256
                ),
                "freeze_generation_sha256": freeze_generation_sha256,
                "journal_sha256": journal["state_sha256"],
                "freeze_evidence_sha256": evidence_sha256,
                **zero,
                "database_container_running": True,
                "redis_container_running": True,
                "production_mutated": False,
            }

        if journal["status"] == "frozen":
            if any(running.values()):
                raise LegacyWriterFreezeError(
                    "durably frozen legacy writer restarted unexpectedly"
                )
            zero = _zero_writer_readback(
                binding,
                inventory,
                runner=runner,
                sleep_fn=sleep_fn,
                proc_root=proc_root,
            )
            _assert_frozen_runtime_unchanged(
                binding,
                inventory=inventory,
                writers=writers,
            )
            if readback_freeze_generations() != (
                role_freeze_generation_sha256,
                freeze_generation_sha256,
            ):
                raise LegacyWriterFreezeError(
                    "legacy freeze Nginx generation changed before evidence readback"
                )
            _evidence, evidence_sha256 = _publish_freeze_evidence(
                evidence_path,
                binding=binding,
                freeze_generation_sha256=freeze_generation_sha256,
                live_lease_claim_sha256=live_lease_claim_sha256,
                source_container_ids=source_container_ids,
            )
            return {
                "schema": RESULT_SCHEMA,
                "status": "already-frozen",
                "action": action,
                "operation_id": binding.operation_id,
                "release_sha": binding.release_sha,
                "legacy_release_sha": binding.legacy_release_sha,
                "role": binding.role,
                "binding_sha256": binding.canonical_sha256,
                "nginx_manifest_sha256": nginx_manifest_sha256,
                "nginx_aggregate_sha256": nginx_aggregate_sha256,
                "coordinated_state_receipt_sha256": (
                    coordinated_state_receipt_sha256
                ),
                "live_lease_claim_sha256": live_lease_claim_sha256,
                "live_lease_claim_epoch": journal[
                    "live_lease_claim_epoch"
                ],
                "role_freeze_generation_sha256": (
                    role_freeze_generation_sha256
                ),
                "freeze_generation_sha256": freeze_generation_sha256,
                "journal_sha256": journal["state_sha256"],
                "freeze_evidence_sha256": evidence_sha256,
                **zero,
                "database_container_running": True,
                "redis_container_running": True,
                "production_mutated": False,
            }
        if journal["status"] in {
            "restoring",
            "compensation-failed",
            "reconciliation-required",
            "restore-readiness-failed",
        }:
            raise LegacyWriterFreezeError(
                "legacy writer restore must be reconciled before freeze"
            )
        if journal["status"] in {"prepared", "active"}:
            if not all(running.values()):
                raise LegacyWriterFreezeError(
                    "legacy writer baseline is not fully running"
                )
            journal["stopped"] = []
            journal["status"] = "freezing"
            journal["last_error_sha256"] = ZERO_SHA256
            _write_journal(journal_path, journal)
        elif journal["status"] == "freezing":
            for kind in journal["stopped"]:
                if running[kind]:
                    raise LegacyWriterFreezeError(
                        "a journaled stopped legacy writer restarted"
                    )

        try:
            for kind in journal["previously_running"]:
                if kind in journal["stopped"]:
                    continue
                readback_freeze_generations()
                _set_container_running(
                    journal["writer_containers"][kind],
                    running=False,
                    runner=runner,
                )
                journal["stopped"] = sorted({*journal["stopped"], kind})
                _write_journal(journal_path, journal)
                readback_freeze_generations()
            inventory, refreshed_writers, refreshed_running = _refresh_runtime(
                binding
            )
            if refreshed_writers != writers or any(refreshed_running.values()):
                raise LegacyWriterFreezeError(
                    "legacy writer stop readback differs"
                )
            zero = _zero_writer_readback(
                binding,
                inventory,
                runner=runner,
                sleep_fn=sleep_fn,
                proc_root=proc_root,
            )
            _assert_frozen_runtime_unchanged(
                binding,
                inventory=inventory,
                writers=writers,
            )
            if readback_freeze_generations() != (
                role_freeze_generation_sha256,
                freeze_generation_sha256,
            ):
                raise LegacyWriterFreezeError(
                    "legacy freeze Nginx generation changed before evidence publication"
                )
            journal["status"] = "frozen"
            journal["last_error_sha256"] = ZERO_SHA256
            _write_journal(journal_path, journal)
            _evidence, evidence_sha256 = _publish_freeze_evidence(
                evidence_path,
                binding=binding,
                freeze_generation_sha256=freeze_generation_sha256,
                live_lease_claim_sha256=live_lease_claim_sha256,
                source_container_ids=source_container_ids,
            )
        except LegacyWriterFreezeError as exc:
            _record_failure(journal, str(exc))
            journal["status"] = "reconciliation-required"
            _write_journal(journal_path, journal)
            raise LegacyWriterFreezeError(
                "legacy writer freeze failed; stopped writers require explicit "
                "reconciliation"
            ) from exc

        return {
            "schema": RESULT_SCHEMA,
            "status": "frozen",
            "action": action,
            "operation_id": binding.operation_id,
            "release_sha": binding.release_sha,
            "legacy_release_sha": binding.legacy_release_sha,
            "role": binding.role,
            "binding_sha256": binding.canonical_sha256,
            "nginx_manifest_sha256": nginx_manifest_sha256,
            "nginx_aggregate_sha256": nginx_aggregate_sha256,
            "coordinated_state_receipt_sha256": (
                coordinated_state_receipt_sha256
            ),
            "live_lease_claim_sha256": live_lease_claim_sha256,
            "live_lease_claim_epoch": journal[
                "live_lease_claim_epoch"
            ],
            "role_freeze_generation_sha256": (
                role_freeze_generation_sha256
            ),
            "freeze_generation_sha256": freeze_generation_sha256,
            "journal_sha256": journal["state_sha256"],
            "freeze_evidence_sha256": evidence_sha256,
            **zero,
            "database_container_running": True,
            "redis_container_running": True,
            "production_mutated": True,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--release-tree-sha", required=True)
    parser.add_argument("--nginx-aggregate-sha256", required=True)
    parser.add_argument("--nginx-manifest", type=Path, required=True)
    parser.add_argument("--nginx-manifest-sha256", required=True)
    parser.add_argument("--nginx-archive", type=Path, required=True)
    parser.add_argument(
        "--coordinated-state-receipt",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--coordinated-state-receipt-sha256",
        required=True,
    )
    parser.add_argument(
        "--live-lease-claim",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--live-lease-claim-sha256",
        required=True,
    )
    parser.add_argument(
        "--action",
        choices=("plan", "freeze", "verify", "restore"),
        default="plan",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    parser.add_argument(
        "--interactive-live-lease-stdio",
        action="store_true",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        if os.geteuid() != 0:
            raise LegacyWriterFreezeError(
                "legacy writer freeze worker must run as root"
            )
        args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
        checkpoint_exchange = (
            StdioCheckpointExchange(
                input_stream=sys.stdin.buffer,
                output_stream=sys.stdout.buffer,
            )
            if args.interactive_live_lease_stdio
            else None
        )
        result = execute(
            binding_path=args.binding,
            action=args.action,
            release_tree_sha=args.release_tree_sha,
            nginx_aggregate_sha256=args.nginx_aggregate_sha256,
            nginx_manifest=args.nginx_manifest,
            nginx_manifest_sha256=args.nginx_manifest_sha256,
            nginx_archive=args.nginx_archive,
            coordinated_state_receipt=args.coordinated_state_receipt,
            coordinated_state_receipt_sha256=(
                args.coordinated_state_receipt_sha256
            ),
            live_lease_claim=args.live_lease_claim,
            live_lease_claim_sha256=args.live_lease_claim_sha256,
            apply=args.apply,
            confirm=args.confirm,
            checkpoint_exchange=checkpoint_exchange,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except LegacyWriterFreezeError as exc:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "error": str(exc),
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
