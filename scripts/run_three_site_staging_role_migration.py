#!/usr/bin/env python3
"""Execute one target role's fail-closed staging migration state machine locally."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from core.secure_file_io import read_secure_bytes, sha256_secure_file
from scripts.run_three_site_staging_source_backup import DOCKER, verify_tar_artifact
from scripts.three_site_staging_migration_journal import (
    MigrationJournal,
    ROLE_PHASES,
    _validate as validate_migration_journal,
)
from scripts.verify_three_site_staging_host_identity import (
    verify_host_snapshot,
)
from scripts.verify_three_site_staging_inventory import load_inventory
from scripts.verify_three_site_staging_migration_plan import (
    TARGET_SEED_MAP,
    verify_migration_plan,
)
from scripts.verify_three_site_staging_role_bundle import (
    _verify_bundle_source,
    verify_role_bundle,
)


SAFE_ENV = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
EXPECTED_HEAD = "e653f4a5b7c8"
ROLE_DB = {
    "bot_fi": ("bot_fi_db", "BOT_FI_POSTGRES_USER", "BOT_FI_POSTGRES_DB"),
    "webapp_fi": ("webapp_fi_db", "WEBAPP_FI_POSTGRES_USER", "WEBAPP_FI_POSTGRES_DB"),
    "webapp_ir": ("webapp_ir_db", "WEBAPP_IR_POSTGRES_USER", "WEBAPP_IR_POSTGRES_DB"),
    "witness": ("witness_db", "WITNESS_POSTGRES_USER", "WITNESS_POSTGRES_DB"),
}
ROLE_VOLUME_SERVICE = {
    "bot_fi": "bot_fi_api",
    "webapp_fi": "webapp_fi_api",
    "webapp_ir": "webapp_ir_api",
}
ROLE_PRIVATE = {
    "bot_fi": ("bot_fi_dr_receiver", "bot_fi_dr_projection", "bot_fi_dr_tls"),
    "webapp_fi": ("webapp_fi_dr_receiver", "webapp_fi_dr_projection", "webapp_fi_dr_tls"),
    "webapp_ir": ("webapp_ir_dr_receiver", "webapp_ir_dr_projection", "webapp_ir_dr_tls"),
    "witness": ("witness_api", "witness_dr_tls"),
}
ROLE_WORKERS = {
    "bot_fi": ("bot_fi_dr_delivery",),
    "webapp_fi": ("webapp_fi_dr_delivery", "webapp_fi_blobs"),
    "webapp_ir": ("webapp_ir_writer_control", "webapp_ir_dr_delivery", "webapp_ir_blobs"),
}
ROLE_PUBLIC = {
    "bot_fi": ("bot_fi_api", "bot_fi_bot"),
    "webapp_fi": ("webapp_fi_api", "webapp_fi_effects"),
    "webapp_ir": ("webapp_ir_api", "webapp_ir_effects"),
}


class RoleMigrationError(RuntimeError):
    pass


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RoleMigrationError("security-sensitive JSON contains a duplicate key")
        result[key] = value
    return result


def _run(arguments: list[str], *, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            env=SAFE_ENV,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RoleMigrationError(f"role migration command unavailable: {arguments[0]}") from exc
    if result.returncode != 0:
        raise RoleMigrationError(f"role migration command failed closed: {Path(arguments[0]).name}")
    return result.stdout.strip()


def _compose(args: argparse.Namespace) -> list[str]:
    return [DOCKER, "compose", "-f", str(args.role_compose), "--env-file", str(args.env_file)]


def _mapping(values: list[str], roles: set[str], *, label: str) -> dict[str, dict[str, Any]]:
    result = {}
    for value in values:
        role, separator, raw_path = value.partition("=")
        if not separator or role not in roles or role in result or not raw_path:
            raise RoleMigrationError(f"{label} must use unique role=/path mappings")
        result[role] = load_inventory(Path(raw_path))
    if set(result) != roles:
        raise RoleMigrationError(f"{label} role set is incomplete")
    return result


def _secure_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            read_secure_bytes(path, label=label, max_size=4 * 1024 * 1024).decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_pairs,
        )
    except RoleMigrationError:
        raise
    except Exception as exc:
        raise RoleMigrationError(f"{label} is unreadable or unsafe") from exc
    if not isinstance(value, dict):
        raise RoleMigrationError(f"{label} must contain one JSON object")
    return value


def _target_seed(
    document: dict[str, Any],
    *,
    role: str,
    campaign_id: str,
    release_sha: str,
) -> dict[str, Any]:
    fields = {
        "schema", "campaign_id", "release_sha", "target_role", "source_role",
        "mode", "verified_at", "objects",
    }
    expected_source, expected_mode = TARGET_SEED_MAP[role]
    if (
        set(document) != fields
        or document["schema"] != "three-site-staging-target-seed-v1"
        or document["campaign_id"] != campaign_id
        or document["release_sha"] != release_sha
        or document["target_role"] != role
        or document["source_role"] != expected_source
        or document["mode"] != expected_mode
        or not isinstance(document["objects"], list)
        or len(document["objects"]) != (0 if role == "witness" else 3)
    ):
        raise RoleMigrationError("target seed evidence identity is invalid")
    kinds: set[str] = set()
    for item in document["objects"]:
        if not isinstance(item, dict) or set(item) != {
            "kind", "object_key", "version_id", "ciphertext_sha256",
            "plaintext_sha256", "plaintext_bytes", "path",
        }:
            raise RoleMigrationError("target seed object evidence fields are invalid")
        kind = str(item["kind"])
        path = Path(str(item["path"]))
        if kind not in {"postgres", "uploads", "audit"} or kind in kinds:
            raise RoleMigrationError("target seed object kind is invalid or duplicate")
        digest, size = sha256_secure_file(
            path, label=f"{kind} target seed", max_size=4 * 1024 * 1024 * 1024
        )
        if digest != item["plaintext_sha256"] or size != item["plaintext_bytes"]:
            raise RoleMigrationError("target seed artifact changed after verified download")
        if kind in {"uploads", "audit"}:
            verify_tar_artifact(path)
        kinds.add(kind)
    if kinds != (set() if role == "witness" else {"postgres", "uploads", "audit"}):
        raise RoleMigrationError("target seed artifact set is incomplete")
    return document


def verify_inputs(args: argparse.Namespace) -> dict[str, Any]:
    inventory = load_inventory(args.inventory)
    backups = _mapping(args.backup_manifest, {"bot_fi", "webapp_fi"}, label="--backup-manifest")
    seeds = _mapping(args.seed_manifest, {"bot_fi", "webapp_fi"}, label="--seed-manifest")
    images = _mapping(
        args.image_inventory,
        {"bot_fi", "webapp_fi", "webapp_ir", "witness"},
        label="--image-inventory",
    )
    verified = verify_migration_plan(
        load_inventory(args.plan),
        approval=load_inventory(args.plan_approval),
        inventory=inventory,
        inventory_approval=load_inventory(args.inventory_approval),
        signer_policy=load_inventory(args.signer_policy),
        freeze_evidence=[load_inventory(path) for path in args.freeze_evidence],
        image_inventories=images,
        backup_manifests=backups,
        seed_manifests=seeds,
    )
    role_cli = args.role.replace("_", "-")
    role_compose_bytes = _verify_bundle_source(args.role_compose, expected_mode=0o640)
    env_bytes = _verify_bundle_source(args.env_file, expected_mode=0o600)
    bundle = verify_role_bundle(
        role=role_cli,
        canonical_compose=yaml.safe_load(args.canonical_compose.read_text(encoding="utf-8")),
        role_compose_bytes=role_compose_bytes,
        env_bytes=env_bytes,
        inventory=inventory,
        approval=load_inventory(args.inventory_approval),
        signer_policy=load_inventory(args.signer_policy),
        verify_files=True,
        required_inventory_stage="provisioned",
    )
    role_inventory = next(row for row in inventory["roles"] if row["role"] == args.role)
    host = verify_host_snapshot(
        _secure_json(args.host_snapshot, label="host snapshot"),
        role=role_cli,
        role_inventory=role_inventory,
        release_sha=verified["release_sha"],
        stage="provisioned",
    )
    target_seed = _target_seed(
        _secure_json(args.target_seed, label="target seed evidence"),
        role=args.role,
        campaign_id=verified["campaign_id"],
        release_sha=verified["release_sha"],
    )
    return {
        "verified_plan": verified,
        "inventory": inventory,
        "role_inventory": role_inventory,
        "bundle": bundle,
        "host": host,
        "target_seed": target_seed,
        "env": {
            line.partition("=")[0]: line.partition("=")[2]
            for line in env_bytes.decode().splitlines()
            if line and not line.startswith("#") and "=" in line
        },
    }


class LocalRoleBackend:
    def __init__(self, args: argparse.Namespace, context: dict[str, Any]):
        self.args = args
        self.context = context
        self.role = args.role
        self.prefix = _compose(args)
        self.db_service, self.user_key, self.database_key = ROLE_DB[self.role]
        self.user = context["env"][self.user_key]
        self.database = context["env"][self.database_key]

    def _psql(self, sql: str, *, database: str | None = None) -> str:
        return _run(
            [
                *self.prefix, "exec", "-T", self.db_service,
                "psql", "-v", "ON_ERROR_STOP=1", "-U", self.user,
                "-d", database or self.database, "-Atqc", sql,
            ]
        )

    def _wait_db(self) -> None:
        for _attempt in range(30):
            result = subprocess.run(
                [
                    *self.prefix, "exec", "-T", self.db_service,
                    "pg_isready", "-U", self.user, "-d", self.database,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
                env=SAFE_ENV,
            )
            if result.returncode == 0:
                return
            time.sleep(1)
        raise RoleMigrationError("target PostgreSQL did not become ready")

    def _compose_run(self, service: str) -> None:
        _run([*self.prefix, "run", "--rm", "--no-deps", "-T", service], timeout=900)

    def _wait_services_ready(
        self,
        services: tuple[str, ...],
        *,
        stable_seconds: int = 5,
    ) -> None:
        deadline = time.monotonic() + 90.0
        stable_since: float | None = None
        while time.monotonic() < deadline:
            all_ready = True
            for service in services:
                container = _run([*self.prefix, "ps", "-q", service])
                if not container:
                    all_ready = False
                    break
                state_raw = _run(
                    [
                        DOCKER,
                        "inspect",
                        "--format",
                        "{{json .State}}",
                        container,
                    ]
                )
                try:
                    state = json.loads(state_raw)
                except json.JSONDecodeError as exc:
                    raise RoleMigrationError(
                        f"service state is unreadable: {service}"
                    ) from exc
                health = (state.get("Health") or {}).get("Status")
                if state.get("Running") is not True or health == "unhealthy":
                    all_ready = False
                    break
                if health is not None and health != "healthy":
                    all_ready = False
                    break
                observed_release = _run(
                    [
                        *self.prefix,
                        "exec",
                        "-T",
                        service,
                        "python",
                        "-c",
                        "from core.config import settings; "
                        "print(str(settings.release_sha or ''))",
                    ],
                    timeout=30,
                )
                if observed_release != self.context["verified_plan"]["release_sha"]:
                    raise RoleMigrationError(
                        f"service release identity mismatch: {service}"
                    )
            if all_ready:
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= stable_seconds:
                    return
            else:
                stable_since = None
            time.sleep(1)
        raise RoleMigrationError(
            "services did not retain exact running/healthy release identity: "
            + ",".join(services)
        )

    def restore_seed(self) -> None:
        _run([*self.prefix, "up", "-d", "--no-deps", self.db_service], timeout=180)
        self._wait_db()
        system_id = self._psql("SELECT system_identifier FROM pg_control_system()")
        if system_id != str(self.context["role_inventory"]["postgres_system_id"]):
            raise RoleMigrationError("target PostgreSQL system identity drifted before restore")
        if self.role == "witness":
            if self._psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'") != "0":
                raise RoleMigrationError("Witness seed target is not empty")
            return
        if self._psql("SELECT count(*) FROM pg_tables WHERE schemaname='public'") != "0":
            raise RoleMigrationError("product seed target database is not empty")
        objects = {item["kind"]: item for item in self.context["target_seed"]["objects"]}
        dump = Path(objects["postgres"]["path"])
        with dump.open("rb") as source:
            result = subprocess.run(
                [
                    *self.prefix, "exec", "-T", self.db_service,
                    "pg_restore", "-U", self.user, "-d", self.database,
                    "--exit-on-error", "--no-owner", "--no-acl",
                ],
                stdin=source,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=1800,
                env=SAFE_ENV,
            )
        if result.returncode != 0:
            raise RoleMigrationError("target PostgreSQL seed restore failed")
        service = ROLE_VOLUME_SERVICE[self.role]
        for kind, destination in (("uploads", "/app/uploads"), ("audit", "/app/audit_trail")):
            if _run(
                [
                    *self.prefix, "run", "--rm", "--no-deps", "-T",
                    "--entrypoint", "find", service,
                    destination, "-mindepth", "1", "-print", "-quit",
                ]
            ):
                raise RoleMigrationError(f"target {kind} volume is not empty")
            with Path(objects[kind]["path"]).open("rb") as source:
                result = subprocess.run(
                    [
                        *self.prefix, "run", "--rm", "--no-deps", "-T",
                        "--entrypoint", "tar", service,
                        "-C", destination, "-xzf", "-",
                    ],
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=900,
                    env=SAFE_ENV,
                )
            if result.returncode != 0:
                raise RoleMigrationError(f"target {kind} seed restore failed")

    def configure_database(self) -> None:
        if self.role == "bot_fi":
            self._compose_run("bot_fi_migration")
            self._compose_run("bot_fi_db_roles")
        elif self.role in {"webapp_fi", "webapp_ir"}:
            prefix = self.role
            self._compose_run(f"{prefix}_db_roles")
            self._compose_run(f"{prefix}_migration")
            self._compose_run(f"{prefix}_db_roles")
            self._compose_run(f"{prefix}_db_fencing")
            if self.role == "webapp_ir":
                campaign_id = self.context["verified_plan"]["campaign_id"]
                _run(
                    [
                        *self.prefix, "run", "--rm", "--no-deps", "-T",
                        "webapp_ir_writer_control", "python",
                        "scripts/manage_webapp_writer.py", "fence",
                        "--expected-epoch", "1",
                        "--expected-active-site", "webapp_fi",
                        "--operator", f"staging-migration:{campaign_id}",
                        "--reason", "initialize WebApp-IR as a locally fenced standby",
                        "--apply", "--confirm", "writer:fence:webapp_ir:1:1",
                    ],
                    timeout=300,
                )
        else:
            self._compose_run("witness_role_bootstrap")
            self._compose_run("witness_migration")
        if self.role == "witness":
            if self._psql("SELECT version_num FROM writer_witness_schema_version") != "002":
                raise RoleMigrationError("Witness schema did not reach version 002")
        elif self._psql("SELECT version_num FROM alembic_version") != EXPECTED_HEAD:
            raise RoleMigrationError("product database did not reach the integration migration head")
        if self._psql("SELECT system_identifier FROM pg_control_system()") != str(
            self.context["role_inventory"]["postgres_system_id"]
        ):
            raise RoleMigrationError("PostgreSQL cluster identity changed during configuration")

    def start_private(self) -> None:
        for service in ROLE_PRIVATE[self.role]:
            _run([*self.prefix, "up", "-d", "--no-deps", service], timeout=180)
        receiver = ROLE_PRIVATE[self.role][0]
        if self.role != "witness":
            for _attempt in range(30):
                state = _run(
                    [
                        DOCKER, "inspect", "--format",
                        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
                        _run([*self.prefix, "ps", "-q", receiver]),
                    ]
                )
                if state == "healthy":
                    return
                time.sleep(1)
            raise RoleMigrationError("private DR receiver did not become healthy")

    def start_workers(self) -> None:
        if self.role == "witness":
            raise RoleMigrationError("Witness has no product worker phase")
        for service in ROLE_WORKERS[self.role]:
            _run([*self.prefix, "up", "-d", "--no-deps", service], timeout=180)
        self._wait_services_ready(ROLE_WORKERS[self.role])

    def attest_writer_state(self) -> dict[str, Any]:
        if self.role not in {"webapp_fi", "webapp_ir"}:
            raise RoleMigrationError("writer-state attestation is valid only on a WebApp role")
        query = (
            "SELECT json_build_object("
            "'active_site', active_site, 'writer_epoch', writer_epoch, "
            "'control_state', control_state, 'witness_lease_id', witness_lease_id, "
            "'witness_proof_hash', witness_proof_hash, "
            "'witness_lease_expires_at', witness_lease_expires_at, "
            "'lease_seconds_remaining', CASE WHEN witness_lease_expires_at IS NULL THEN NULL "
            "ELSE floor(extract(epoch FROM (witness_lease_expires_at - clock_timestamp())))::bigint END"
            ")::text FROM webapp_writer_state WHERE authority='webapp'"
        )

        def snapshot() -> dict[str, Any]:
            try:
                value = json.loads(
                    self._psql(query), object_pairs_hook=_reject_duplicate_json_pairs
                )
            except Exception as exc:
                raise RoleMigrationError("local Writer state is unreadable") from exc
            if not isinstance(value, dict):
                raise RoleMigrationError("local Writer state is missing")
            return value

        state = snapshot()
        if self.role == "webapp_ir":
            if (
                state.get("active_site") is not None
                or state.get("writer_epoch") != 1
                or state.get("control_state") != "fenced"
                or state.get("witness_lease_id") is not None
            ):
                raise RoleMigrationError("WebApp-IR is not a locally fenced epoch-1 standby")
            return state

        if (
            state.get("active_site") != "webapp_fi"
            or state.get("writer_epoch") != 1
            or state.get("control_state") != "active"
        ):
            raise RoleMigrationError("initial Writer authority is not WebApp-FI epoch 1")

        # The compatibility migration creates FI/epoch-1 without a Witness
        # lease.  Operators must first acquire epoch 1 on the freshly seeded
        # Witness and import that signed proof into WebApp-FI.  Only then may
        # the isolated renewal agent start.
        remaining = state.get("lease_seconds_remaining")
        if (
            not isinstance(state.get("witness_lease_id"), str)
            or not state["witness_lease_id"]
            or type(remaining) is not int
            or remaining < 30
        ):
            raise RoleMigrationError(
                "WebApp-FI epoch 1 lacks a live imported Witness lease"
            )
        _run(
            [*self.prefix, "up", "-d", "--no-deps", "webapp_fi_writer_control"],
            timeout=180,
        )
        container = _run([*self.prefix, "ps", "-q", "webapp_fi_writer_control"])
        initial_proof_hash = state.get("witness_proof_hash")
        for _attempt in range(45):
            if (
                not container
                or _run(
                    [DOCKER, "inspect", "--format", "{{.State.Running}}", container]
                ) != "true"
            ):
                raise RoleMigrationError("WebApp-FI Writer control agent is not running")
            renewed = snapshot()
            if (
                isinstance(renewed.get("witness_proof_hash"), str)
                and renewed["witness_proof_hash"]
                and renewed["witness_proof_hash"] != initial_proof_hash
                and type(renewed.get("lease_seconds_remaining")) is int
                and renewed["lease_seconds_remaining"] >= 30
            ):
                return renewed
            time.sleep(1)
        raise RoleMigrationError(
            "WebApp-FI Writer control agent did not prove a live Witness renewal"
        )

    def start_public(self) -> None:
        if self.role == "witness":
            raise RoleMigrationError("Witness has no public application phase")
        for service in ROLE_PUBLIC[self.role]:
            _run([*self.prefix, "up", "-d", "--no-deps", service], timeout=180)
        self._wait_services_ready(ROLE_PUBLIC[self.role])

    def rollback_stop(self) -> None:
        # Preserve every target byte for forensics; rollback of user access is
        # performed by restoring the independently frozen legacy source.
        _run([*self.prefix, "stop", "--timeout", "30"], timeout=300)


def _evidence(
    path: Path,
    *,
    schema: str,
    context: dict[str, Any],
    role: str,
    journal_state_sha256: str,
) -> tuple[dict, str]:
    value = _secure_json(path, label=schema)
    schema_extra = {
        "three-site-staging-private-barrier-v1": {"campaign_journals_sha256"},
        "three-site-staging-routing-hold-v1": {
            "campaign_journals_sha256", "routing_observation_sha256",
        },
        "three-site-staging-role-acceptance-v1": {
            "campaign_journals_sha256", "acceptance_observation_sha256",
        },
    }
    common = {
        "schema", "status", "campaign_id", "release_sha", "plan_sha256",
        "role", "issued_at", "role_journal_state_sha256",
    }
    try:
        issued_at = datetime.fromisoformat(str(value["issued_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise RoleMigrationError(f"{schema} issued_at is invalid") from exc
    if (
        schema not in schema_extra
        or set(value) != common | schema_extra[schema]
        or value.get("schema") != schema
        or value.get("status") != "passed"
        or value.get("campaign_id") != context["verified_plan"]["campaign_id"]
        or value.get("release_sha") != context["verified_plan"]["release_sha"]
        or value.get("plan_sha256") != context["verified_plan"]["plan_sha256"]
        or value.get("role") != role
        or value.get("role_journal_state_sha256") != journal_state_sha256
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("campaign_journals_sha256", ""))) is None
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(field, ""))) is None
            for field in schema_extra[schema] - {"campaign_journals_sha256"}
        )
        or issued_at.tzinfo is None
        or datetime.now(timezone.utc) - issued_at.astimezone(timezone.utc) > timedelta(minutes=15)
        or issued_at.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=2)
    ):
        raise RoleMigrationError(f"{schema} identity/status is invalid")
    return value, hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _phase_for_action(role: str, action: str) -> str:
    fixed = {
        "restore-seed": "empty_seed_verified" if role == "witness" else "seed_restored",
        "configure-database": "database_configured",
        "start-private": "private_ready",
        "start-workers": "workers_ready",
        "start-public": "public_ready",
        "accept": "accepted",
    }
    if action == "attest-writer-state":
        return "writer_initialized" if role == "webapp_fi" else "standby_fenced"
    try:
        return fixed[action]
    except KeyError as exc:
        raise RoleMigrationError("action has no migration phase") from exc


def apply_action(
    *,
    action: str,
    journal: MigrationJournal,
    backend: LocalRoleBackend,
    context: dict[str, Any],
    evidence_path: Path | None,
) -> dict[str, Any]:
    role = backend.role
    phase = _phase_for_action(role, action)
    if phase not in ROLE_PHASES[role]:
        raise RoleMigrationError(f"action {action} is not valid for role {role}")
    journal_state = journal.load()
    if action in {"start-workers", "start-public", "accept"}:
        if evidence_path is None:
            raise RoleMigrationError(f"{action} requires campaign-controller evidence")
        schema = {
            "start-workers": "three-site-staging-private-barrier-v1",
            "start-public": "three-site-staging-routing-hold-v1",
            "accept": "three-site-staging-role-acceptance-v1",
        }[action]
        _evidence(
            evidence_path,
            schema=schema,
            context=context,
            role=role,
            journal_state_sha256=journal_state["state_sha256"],
        )
    journal.begin_phase(phase)
    try:
        evidence_hash = None
        if action == "restore-seed":
            backend.restore_seed()
        elif action == "configure-database":
            backend.configure_database()
        elif action == "start-private":
            backend.start_private()
        elif action == "start-workers":
            backend.start_workers()
        elif action == "start-public":
            backend.start_public()
        elif action == "attest-writer-state":
            evidence = backend.attest_writer_state()
            expected = "active" if role == "webapp_fi" else "standby"
            observed = (
                "active"
                if evidence.get("active_site") == role
                and evidence.get("control_state") == "active"
                else "standby"
            )
            expected_site = "webapp_fi" if role == "webapp_fi" else None
            if observed != expected or evidence.get("active_site") != expected_site:
                raise RoleMigrationError("initial WebApp Writer/standby state is incorrect")
        elif action == "accept":
            evidence_hash = hashlib.sha256(
                read_secure_bytes(evidence_path, label="role acceptance evidence")
            ).hexdigest()
        journal.complete_phase(phase)
        if action == "accept":
            return journal.commit(acceptance_evidence_sha256=str(evidence_hash))
        return journal.load()
    except Exception as exc:
        try:
            journal.require_rollback(type(exc).__name__)
        except Exception:
            pass
        raise


def confirmation_phrase(campaign_id: str, role: str, action: str, plan_hash: str) -> str:
    return f"migrate-role:{campaign_id}:{role}:{action}:{plan_hash}"


def _require_forward_inputs(args: argparse.Namespace) -> None:
    required = {
        "canonical-compose": args.canonical_compose,
        "role-compose": args.role_compose,
        "env-file": args.env_file,
        "host-snapshot": args.host_snapshot,
        "target-seed": args.target_seed,
        "inventory": args.inventory,
        "inventory-approval": args.inventory_approval,
        "signer-policy": args.signer_policy,
        "plan": args.plan,
        "plan-approval": args.plan_approval,
        "freeze-evidence": args.freeze_evidence,
        "backup-manifest": args.backup_manifest,
        "seed-manifest": args.seed_manifest,
        "image-inventory": args.image_inventory,
    }
    missing = sorted(name for name, value in required.items() if not value)
    if missing:
        raise RoleMigrationError(
            "forward role migration is missing required inputs: " + ", ".join(missing)
        )


def _require_rollback_inputs(args: argparse.Namespace) -> None:
    missing = sorted(
        name
        for name, value in {
            "role-compose": args.role_compose,
            "env-file": args.env_file,
        }.items()
        if value is None
    )
    if missing:
        raise RoleMigrationError(
            "role rollback is missing required inputs: " + ", ".join(missing)
        )


def _verify_global_commit(path: Path, *, role: str, state: dict[str, Any]) -> dict[str, Any]:
    value = _secure_json(path, label="global migration commit evidence")
    fields = {
        "schema", "status", "campaign_id", "release_sha", "plan_sha256",
        "issued_at", "campaign_journals_sha256", "role_journals",
        "committed_role_states", "all_roles_committed",
    }
    role_journals = value.get("role_journals")
    if (
        set(value) != fields
        or value.get("schema") != "three-site-staging-global-commit-v2"
        or value.get("status") != "passed"
        or value.get("campaign_id") != state["campaign_id"]
        or value.get("release_sha") != state["release_sha"]
        or value.get("plan_sha256") != state["plan_sha256"]
        or value.get("all_roles_committed") is not True
        or not isinstance(role_journals, dict)
        or set(role_journals) != set(ROLE_PHASES)
        or role_journals.get(role) != state["state_sha256"]
        or not isinstance(value.get("committed_role_states"), dict)
        or set(value["committed_role_states"]) != set(ROLE_PHASES)
        or value["committed_role_states"].get(role) != state
    ):
        raise RoleMigrationError("global migration commit evidence is invalid")
    expected_campaign_hash = hashlib.sha256(
        json.dumps(role_journals, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if value["campaign_journals_sha256"] != expected_campaign_hash:
        raise RoleMigrationError("global migration commit journal hash is invalid")
    for state_role, committed_state in value["committed_role_states"].items():
        try:
            validate_migration_journal(committed_state)
        except Exception as exc:
            raise RoleMigrationError("global commit embeds an invalid journal state") from exc
        if (
            committed_state.get("role") != state_role
            or committed_state.get("status") != "committed"
            or committed_state.get("state_sha256") != role_journals[state_role]
        ):
            raise RoleMigrationError("global commit journal state/hash is invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=(
        "plan", "begin", "status", "restore-seed", "configure-database",
        "start-private", "attest-writer-state", "start-workers", "start-public",
        "accept", "finish", "rollback",
    ))
    parser.add_argument("--role", choices=tuple(ROLE_PHASES), required=True)
    parser.add_argument("--canonical-compose", type=Path)
    parser.add_argument("--role-compose", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--host-snapshot", type=Path)
    parser.add_argument("--target-seed", type=Path)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--inventory-approval", type=Path)
    parser.add_argument("--signer-policy", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--plan-approval", type=Path)
    parser.add_argument("--freeze-evidence", action="append", type=Path, default=[])
    parser.add_argument("--backup-manifest", action="append", default=[])
    parser.add_argument("--seed-manifest", action="append", default=[])
    parser.add_argument("--image-inventory", action="append", default=[])
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    try:
        journal = MigrationJournal(args.journal)
        if args.action == "status":
            result = journal.load()
        elif args.action == "finish":
            state = journal.load()
            if args.evidence is None:
                raise RoleMigrationError("finish requires global commit evidence")
            _verify_global_commit(args.evidence, role=args.role, state=state)
            result = journal.finish()
        elif args.action == "rollback":
            _require_rollback_inputs(args)
            # Rollback remains possible after plan expiry. The exact Compose
            # bytes are still checked and all target data is retained.
            state = journal.load()
            if state["role"] != args.role:
                raise RoleMigrationError("rollback role differs from durable journal")
            role_compose_bytes = _verify_bundle_source(args.role_compose, expected_mode=0o640)
            env_bytes = _verify_bundle_source(args.env_file, expected_mode=0o600)
            if (
                hashlib.sha256(role_compose_bytes).hexdigest()
                != state["role_compose_sha256"]
                or hashlib.sha256(env_bytes).hexdigest() != state["role_env_sha256"]
            ):
                raise RoleMigrationError("rollback role bundle differs from the durable journal")
            backend = LocalRoleBackend(args, {"env": {
                line.partition("=")[0]: line.partition("=")[2]
                for line in env_bytes.decode("utf-8").splitlines()
                if line and not line.startswith("#") and "=" in line
            }, "role_inventory": {}})
            required = confirmation_phrase(state["campaign_id"], args.role, args.action, state["plan_sha256"])
            if not args.apply:
                result = {"status": "planned", "required_confirmation": required}
            else:
                if args.confirm != required:
                    raise RoleMigrationError("role rollback confirmation mismatch")
                backend.rollback_stop()
                result = journal.complete_rollback()
        else:
            _require_forward_inputs(args)
            context = verify_inputs(args)
            verified = context["verified_plan"]
            required = confirmation_phrase(
                verified["campaign_id"], args.role, args.action, verified["plan_sha256"]
            )
            if args.action == "plan" or not args.apply:
                result = {
                    "status": "planned",
                    "action": args.action,
                    "role": args.role,
                    "next_phase": (
                        ROLE_PHASES[args.role][0]
                        if args.action in {"plan", "begin"}
                        else _phase_for_action(args.role, args.action)
                    ),
                    "required_confirmation": required,
                }
            else:
                if args.confirm != required:
                    raise RoleMigrationError("role migration confirmation mismatch")
                if args.action == "begin":
                    result = journal.create(
                        campaign_id=verified["campaign_id"],
                        release_sha=verified["release_sha"],
                        plan_sha256=verified["plan_sha256"],
                        role=args.role,
                        role_compose_sha256=context["bundle"]["compose_sha256"],
                        role_env_sha256=context["bundle"]["environment_sha256"],
                        image_inventory_sha256=verified["image_inventory_sha256"][args.role],
                    )
                else:
                    state = journal.load()
                    if (
                        state["campaign_id"] != verified["campaign_id"]
                        or state["release_sha"] != verified["release_sha"]
                        or state["plan_sha256"] != verified["plan_sha256"]
                        or state["role"] != args.role
                    ):
                        raise RoleMigrationError("role journal differs from current signed campaign")
                    result = apply_action(
                        action=args.action,
                        journal=journal,
                        backend=LocalRoleBackend(args, context),
                        context=context,
                        evidence_path=args.evidence,
                    )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error": str(exc), "error_class": type(exc).__name__}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
