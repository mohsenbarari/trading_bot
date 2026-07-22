"""Crash-visible, fail-closed local journal for one staging migration role."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from core.secure_file_io import read_secure_bytes, write_secure_atomic_bytes


SCHEMA = "three-site-staging-role-migration-journal-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
ROLE_PHASES = {
    "bot_fi": (
        "seed_restored", "database_configured", "private_ready",
        "workers_ready", "public_ready", "accepted",
    ),
    "webapp_fi": (
        "seed_restored", "database_configured", "private_ready",
        "writer_initialized", "workers_ready", "public_ready", "accepted",
    ),
    "webapp_ir": (
        "seed_restored", "database_configured", "private_ready",
        "standby_fenced", "workers_ready", "public_ready", "accepted",
    ),
    "witness": ("empty_seed_verified", "database_configured", "private_ready", "accepted"),
}


class MigrationJournalError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_hash(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "state_sha256"}
    return hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _validate(payload: dict[str, Any]) -> None:
    fields = {
        "schema", "campaign_id", "release_sha", "plan_sha256", "role",
        "role_compose_sha256", "role_env_sha256", "image_inventory_sha256",
        "status", "completed_phases", "started_phase", "rollback_reason",
        "acceptance_evidence_sha256", "created_at", "updated_at", "state_sha256",
    }
    role = payload.get("role") if isinstance(payload, dict) else None
    phases = ROLE_PHASES.get(str(role))
    if (
        not isinstance(payload, dict)
        or set(payload) != fields
        or payload.get("schema") != SCHEMA
        or not isinstance(payload.get("release_sha"), str)
        or re.fullmatch(r"[0-9a-f]{40}", payload["release_sha"]) is None
        or not isinstance(payload.get("plan_sha256"), str)
        or SHA256_RE.fullmatch(payload["plan_sha256"]) is None
        or not isinstance(payload.get("role_compose_sha256"), str)
        or SHA256_RE.fullmatch(payload["role_compose_sha256"]) is None
        or not isinstance(payload.get("role_env_sha256"), str)
        or SHA256_RE.fullmatch(payload["role_env_sha256"]) is None
        or not isinstance(payload.get("image_inventory_sha256"), str)
        or SHA256_RE.fullmatch(payload["image_inventory_sha256"]) is None
        or phases is None
        or payload.get("status") not in {
            "active", "rollback_required", "rolled_back", "committed", "finished"
        }
        or not isinstance(payload.get("completed_phases"), list)
        or len(payload["completed_phases"]) != len(set(payload["completed_phases"]))
        or any(phase not in phases for phase in payload["completed_phases"])
        or payload["completed_phases"] != list(phases[: len(payload["completed_phases"])])
        or payload.get("started_phase") not in {None, *phases}
        or payload.get("state_sha256") != _state_hash(payload)
    ):
        raise MigrationJournalError("migration journal schema/state/hash is invalid")
    next_index = len(payload["completed_phases"])
    if payload["started_phase"] is not None and (
        next_index >= len(phases) or payload["started_phase"] != phases[next_index]
    ):
        raise MigrationJournalError("migration journal started phase is out of order")
    status = payload["status"]
    if status == "active" and payload["started_phase"] is not None:
        raise MigrationJournalError("incomplete phase must require rollback")
    if status == "rollback_required" and payload["started_phase"] is None:
        raise MigrationJournalError("rollback-required journal lacks interrupted phase")
    if status in {"rolled_back", "committed", "finished"} and payload["started_phase"] is not None:
        raise MigrationJournalError("terminal migration journal has an active phase")
    if status in {"committed", "finished"} and payload["completed_phases"] != list(phases):
        raise MigrationJournalError("committed migration journal is incomplete")
    if status == "finished" and not payload["acceptance_evidence_sha256"]:
        raise MigrationJournalError("finished migration journal lacks acceptance evidence")


class MigrationJournal:
    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")

    def _lock(self):  # noqa: ANN202
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            os.close(descriptor)
            raise MigrationJournalError("migration journal lock is unsafe")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor

    def _read(self) -> dict[str, Any]:
        try:
            payload = json.loads(
                read_secure_bytes(
                    self.path, label="migration journal", max_size=1024 * 1024
                ).decode("utf-8")
            )
        except Exception as exc:
            raise MigrationJournalError("migration journal is unreadable") from exc
        _validate(payload)
        return payload

    def _write(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["updated_at"] = _now()
        payload["state_sha256"] = _state_hash(payload)
        _validate(payload)
        write_secure_atomic_bytes(
            self.path,
            (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode(),
            label="migration journal",
            mode=0o600,
            max_size=1024 * 1024,
        )
        return payload

    def create(
        self,
        *,
        campaign_id: str,
        release_sha: str,
        plan_sha256: str,
        role: str,
        role_compose_sha256: str,
        role_env_sha256: str,
        image_inventory_sha256: str,
    ):
        descriptor = self._lock()
        try:
            if self.path.exists():
                raise MigrationJournalError("migration journal already exists")
            now = _now()
            payload = {
                "schema": SCHEMA,
                "campaign_id": campaign_id,
                "release_sha": release_sha,
                "plan_sha256": plan_sha256,
                "role": role,
                "role_compose_sha256": role_compose_sha256,
                "role_env_sha256": role_env_sha256,
                "image_inventory_sha256": image_inventory_sha256,
                "status": "active",
                "completed_phases": [],
                "started_phase": None,
                "rollback_reason": None,
                "acceptance_evidence_sha256": None,
                "created_at": now,
                "updated_at": now,
                "state_sha256": "",
            }
            return self._write(payload)
        finally:
            os.close(descriptor)

    def load(self) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            return self._read()
        finally:
            os.close(descriptor)

    def begin_phase(self, phase: str) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            payload = self._read()
            phases = ROLE_PHASES[payload["role"]]
            if payload["status"] != "active" or payload["started_phase"] is not None:
                raise MigrationJournalError("migration phase cannot begin from current state")
            index = len(payload["completed_phases"])
            if index >= len(phases) or phase != phases[index]:
                raise MigrationJournalError("migration phase is out of order")
            payload["started_phase"] = phase
            payload["status"] = "rollback_required"
            payload["rollback_reason"] = "phase_started_without_durable_completion"
            return self._write(payload)
        finally:
            os.close(descriptor)

    def complete_phase(self, phase: str) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            payload = self._read()
            if payload["status"] != "rollback_required" or payload["started_phase"] != phase:
                raise MigrationJournalError("migration phase completion has no matching start")
            payload["completed_phases"].append(phase)
            payload["started_phase"] = None
            payload["rollback_reason"] = None
            payload["status"] = "active"
            return self._write(payload)
        finally:
            os.close(descriptor)

    def require_rollback(self, reason: str) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            payload = self._read()
            if payload["status"] != "rollback_required":
                raise MigrationJournalError("rollback reason requires an interrupted phase")
            payload["rollback_reason"] = str(reason)[:256] or "phase_failed"
            return self._write(payload)
        finally:
            os.close(descriptor)

    def complete_rollback(self) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            payload = self._read()
            if payload["status"] not in {"active", "rollback_required", "rolled_back"}:
                raise MigrationJournalError("committed migration cannot be rolled back locally")
            payload["status"] = "rolled_back"
            payload["started_phase"] = None
            payload["rollback_reason"] = None
            return self._write(payload)
        finally:
            os.close(descriptor)

    def commit(self, *, acceptance_evidence_sha256: str) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            payload = self._read()
            if (
                payload["status"] != "active"
                or payload["completed_phases"] != list(ROLE_PHASES[payload["role"]])
                or not isinstance(acceptance_evidence_sha256, str)
                or SHA256_RE.fullmatch(acceptance_evidence_sha256) is None
            ):
                raise MigrationJournalError("migration cannot commit before all role phases")
            payload["acceptance_evidence_sha256"] = acceptance_evidence_sha256
            payload["status"] = "committed"
            return self._write(payload)
        finally:
            os.close(descriptor)

    def finish(self) -> dict[str, Any]:
        descriptor = self._lock()
        try:
            payload = self._read()
            if payload["status"] != "committed":
                raise MigrationJournalError("only a committed migration can finish")
            payload["status"] = "finished"
            return self._write(payload)
        finally:
            os.close(descriptor)
