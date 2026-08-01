"""Opt-in disposable-loopback PostgreSQL test for V2 strict-writer rows.

This module is deliberately skipped during ordinary test runs.  It never
consults a project ``DATABASE_URL`` or ``SYNC_DATABASE_URL``.  Running it
requires its own dedicated environment variable, an exact confirmation, a
literal loopback PostgreSQL address, and a database name reserved for this
one disposable harness.  It leaves its append-only evidence rows in place so
the child migration's downgrade guard can be exercised.

Enable only against a disposable local database, for example::

    export V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_URL='postgresql://postgres@127.0.0.1:55433/v2wsrc_scratch_ci_20260801'
    export V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_CONFIRM='I_CONFIRM_V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_ONLY'
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \\
      tests.test_physical_wal_v2_witness_roundtrip_strict_writer_postgres_integration

Do not point this at a project, staging, or production database.  If a local
disposable PostgreSQL server is reached through a tunnel, keep the URL's host
literal ``127.0.0.1`` or ``::1``; this harness intentionally rejects DNS and
``localhost``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from uuid import UUID, uuid4

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_postgres_contract as contract
from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as adapter_module
from models.operational_writer_admission import (
    OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
    PhysicalWalV2WitnessRoundtripStrictWriterCommit,
)
from models.physical_wal_v2_witness_roundtrip_attestation_consumption import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1,
    PhysicalWalV2WitnessRoundtripAttestationConsumption,
)
from models.physical_wal_v2_witness_roundtrip_strict_writer_bound import (
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
    PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
    PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
# Keep this disposable integration harness on the actual reviewed head.  The
# Gen2 base-pin child is deliberately non-null/no-backfill, so testing only
# the older registry head would leave its live migration edge unexercised.
MIGRATION_HEAD = "0v2basepin01"
MIGRATION_PARENT = "0writeradm01"
SCRATCH_URL_ENV = "V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_URL"
SCRATCH_CONFIRM_ENV = "V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_CONFIRM"
SCRATCH_CONFIRM_VALUE = "I_CONFIRM_V2_WITNESS_STRICT_WRITER_POSTGRES_SCRATCH_ONLY"
_SCRATCH_DATABASE_RE = re.compile(r"^v2wsrc_scratch_[a-z0-9_]{8,40}$", re.ASCII)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_ALLOWED_DRIVERS = frozenset(
    {"postgresql", "postgresql+psycopg2", "postgresql+psycopg", "postgresql+asyncpg"}
)
_ZERO_SHA256 = "0" * 64
_SCRATCH_SEARCH_PATH_OPTION = "-csearch_path=public"


@dataclass(frozen=True)
class _ScratchTarget:
    database_name: str
    sync_url: URL
    async_url: URL


def _configured_scratch_target() -> _ScratchTarget | None:
    """Return only a separately confirmed, loopback-only V2 scratch target.

    There is intentionally no fallback to any application environment
    variable.  An absent dedicated variable is an ordinary-test skip, and an
    invalid explicit value fails before any connection or migration is tried.
    """

    raw = str(os.getenv(SCRATCH_URL_ENV, "")).strip()
    confirmation = str(os.getenv(SCRATCH_CONFIRM_ENV, "")).strip()
    if not raw or confirmation != SCRATCH_CONFIRM_VALUE:
        return None
    try:
        parsed = make_url(raw)
    except Exception as exc:  # pragma: no cover - explicit operator typo
        raise RuntimeError("V2 strict-writer PostgreSQL scratch URL is invalid") from exc
    if parsed.drivername not in _ALLOWED_DRIVERS:
        raise RuntimeError("V2 strict-writer scratch URL must use a PostgreSQL driver")
    if str(parsed.host or "").lower() not in _LOOPBACK_HOSTS:
        raise RuntimeError("V2 strict-writer integration requires a literal loopback scratch host")
    database_name = str(parsed.database or "")
    if database_name != database_name.lower() or _SCRATCH_DATABASE_RE.fullmatch(database_name) is None:
        raise RuntimeError(
            "V2 strict-writer scratch database must be named "
            "v2wsrc_scratch_<8-40 lowercase chars>"
        )
    if parsed.query:
        raise RuntimeError("V2 strict-writer scratch URL must not carry query options")
    return _ScratchTarget(
        database_name=database_name,
        sync_url=parsed.set(drivername="postgresql+psycopg2"),
        async_url=parsed.set(drivername="postgresql+asyncpg"),
    )


SCRATCH_TARGET = _configured_scratch_target()


def _verify_connected_target(target: _ScratchTarget) -> None:
    """Pin the selected disposable database and public schema before writes."""

    engine = create_engine(
        target.sync_url,
        pool_pre_ping=True,
        connect_args={"options": _SCRATCH_SEARCH_PATH_OPTION},
    )
    try:
        with engine.connect() as connection:
            actual_database, actual_schema = connection.execute(
                text("SELECT current_database(), current_schema()")
            ).one()
    finally:
        engine.dispose()
    if str(actual_database) != target.database_name:
        raise RuntimeError("V2 strict-writer scratch connection selected the wrong database")
    if str(actual_schema) != "public":
        raise RuntimeError("V2 strict-writer scratch connection selected the wrong schema")


def _alembic_environment(target: _ScratchTarget) -> dict[str, str]:
    """Give Alembic only the validated scratch URL, never inherited app DSNs."""

    rendered = target.sync_url.render_as_string(hide_password=False)
    # Do not copy os.environ here: that could pass a project connection
    # variable, PGHOST, or a credentials file lookup into the Alembic child.
    # The interpreter is already absolute (sys.executable), and migration
    # imports resolve from REPO_ROOT, so this intentionally tiny environment
    # is sufficient and fails closed if a machine requires anything else.
    return {
        "PATH": os.defpath,
        "DATABASE_URL": rendered,
        "SYNC_DATABASE_URL": rendered,
        "PGOPTIONS": _SCRATCH_SEARCH_PATH_OPTION,
    }


def _run_alembic(target: _ScratchTarget, direction: str, revision: str) -> subprocess.CompletedProcess[str]:
    if (direction, revision) not in {
        ("upgrade", MIGRATION_HEAD),
        ("downgrade", MIGRATION_PARENT),
    }:
        raise AssertionError("V2 strict-writer scratch harness permits only its direct migration edge")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", direction, revision],
        cwd=REPO_ROOT,
        env=_alembic_environment(target),
        capture_output=True,
        text=True,
        check=False,
    )


@dataclass(frozen=True)
class _Evidence:
    cluster_id: str
    holder_site: str
    writer_epoch: int
    writer_lease_id: str
    release_sha: str
    generation_id: str
    evidence_id: str
    revalidation_id: str
    issued_at: datetime
    expires_at: datetime


class _Revalidator:
    def __init__(self, evidence: _Evidence) -> None:
        self._evidence = evidence

    def revalidate_writer_term(self, *, request: object) -> _Evidence:
        del request
        return self._evidence


def _binding_mapping(binding: admission.PhysicalOperationalFailoverV1WriterAdmissionBinding) -> dict[str, str]:
    return {
        "cluster_id": binding.cluster_id,
        "local_site": binding.local_site,
        "release_sha": binding.release_sha,
        "generation_id": binding.generation_id,
    }


def _term_mapping(
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
) -> dict[str, object] | None:
    term = state.active_term
    if term is None:
        return None
    return {
        "holder_site": term.holder_site,
        "writer_epoch": term.writer_epoch,
        "writer_lease_id": term.writer_lease_id,
        "evidence_id": term.evidence_id,
        "revalidation_id": term.revalidation_id,
        "issued_at": term.issued_at,
        "expires_at": term.expires_at,
    }


def _state_mapping(
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
) -> dict[str, object]:
    return {
        "revision": state.revision,
        "highest_writer_epoch": state.highest_writer_epoch,
        "active_term": _term_mapping(state),
        "revalidated_runtime_instance_id": state.revalidated_runtime_instance_id,
        "clock_floor": state.clock_floor,
        "fence_generation": state.fence_generation,
        "fenced": state.fenced,
        "fence_reason": state.fence_reason,
        "requires_fresh_witness_revalidation": state.requires_fresh_witness_revalidation,
    }


def _state_columns(
    state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
) -> dict[str, object]:
    term = state.active_term
    return {
        "revision": state.revision,
        "prior_revision": state.revision - 1,
        "highest_writer_epoch": state.highest_writer_epoch,
        "holder_site": None if term is None else term.holder_site,
        "writer_epoch": None if term is None else term.writer_epoch,
        "writer_lease_id": None if term is None else term.writer_lease_id,
        "evidence_id": None if term is None else term.evidence_id,
        "revalidation_id": None if term is None else term.revalidation_id,
        "term_issued_at": None if term is None else term.issued_at,
        "term_expires_at": None if term is None else term.expires_at,
        "revalidated_runtime_instance_id": state.revalidated_runtime_instance_id,
        "clock_floor": state.clock_floor,
        "fence_generation": state.fence_generation,
        "fenced": state.fenced,
        "fence_reason": state.fence_reason,
        "requires_fresh_witness_revalidation": state.requires_fresh_witness_revalidation,
    }


@unittest.skipUnless(
    SCRATCH_TARGET is not None,
    "set dedicated V2 strict-writer PostgreSQL scratch URL and exact scratch-only confirmation to run",
)
class PhysicalWalV2WitnessRoundtripStrictWriterPostgresIntegrationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Exercise V2's actual PostgreSQL constraints on a disposable DB only."""

    target: _ScratchTarget

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        assert SCRATCH_TARGET is not None
        cls.target = SCRATCH_TARGET
        _verify_connected_target(cls.target)
        result = _run_alembic(cls.target, "upgrade", MIGRATION_HEAD)
        if result.returncode != 0:
            raise AssertionError(result.stderr or result.stdout)

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            self.target.async_url,
            pool_pre_ping=True,
            connect_args={"server_settings": {"search_path": "public"}},
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)
        self.nonce = uuid4().hex
        self.now = datetime.now(timezone.utc).replace(microsecond=123456)
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id=f"v2wsrc-integration-{self.nonce}",
            local_site="webapp_fi",
            release_sha="a" * 40,
            generation_id=f"v2wsrc-generation-{self.nonce}",
        )
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=f"v2wsrc-runtime-{self.nonce}",
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        self.control = {
            "control_boundary": OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
            "control_role_label": "v2wsrc-postgres-harness",
            "control_policy_sha256": "c" * 64,
        }

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def _state_digest(
        self,
        state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
    ) -> str:
        return contract.operational_writer_admission_postgres_state_sha256_v1(
            binding=_binding_mapping(self.binding),
            state=_state_mapping(state),
        )

    def _v1_commit(
        self,
        *,
        commit_id: UUID,
        head_id: UUID,
        transition_kind: str,
        prior_state: admission.PhysicalOperationalFailoverV1WriterAdmissionState | None,
        next_state: admission.PhysicalOperationalFailoverV1WriterAdmissionState,
        prior_state_sha256: str,
        previous_commit_sha256: str,
        committed_at: datetime,
    ) -> OperationalWriterAdmissionCommit:
        next_state_sha256 = self._state_digest(next_state)
        prior_revision = -1 if prior_state is None else prior_state.revision
        prior_fence = 0 if prior_state is None else prior_state.fence_generation
        receipt_sha256 = contract.operational_writer_admission_postgres_receipt_sha256_v1(
            binding=_binding_mapping(self.binding),
            transition_kind=transition_kind,
            prior_revision=prior_revision,
            prior_fence_generation=prior_fence,
            prior_state_sha256=prior_state_sha256,
            previous_commit_sha256=previous_commit_sha256,
            next_state_sha256=next_state_sha256,
            next_fence_generation=next_state.fence_generation,
            operation=None,
            control=self.control,
            committed_at=committed_at,
        )
        commit_sha256 = contract.operational_writer_admission_postgres_commit_sha256_v1(
            commit_id=commit_id,
            head_id=head_id,
            receipt_sha256=receipt_sha256,
            previous_commit_sha256=previous_commit_sha256,
            state_sha256=next_state_sha256,
            committed_at=committed_at,
        )
        return OperationalWriterAdmissionCommit(
            id=commit_id,
            head_id=head_id,
            **_binding_mapping(self.binding),
            transition_kind=transition_kind,
            prior_revision=prior_revision,
            next_revision=next_state.revision,
            prior_fence_generation=prior_fence,
            next_fence_generation=next_state.fence_generation,
            prior_state_sha256=prior_state_sha256,
            previous_commit_sha256=previous_commit_sha256,
            **{
                key: value
                for key, value in _state_columns(next_state).items()
                if key not in {"revision", "prior_revision", "fence_generation"}
            },
            state_sha256=next_state_sha256,
            receipt_sha256=receipt_sha256,
            commit_sha256=commit_sha256,
            operation_kind=None,
            operation_opened_state_revision=None,
            operation_fence_generation=None,
            operation_evidence_id=None,
            operation_writer_epoch=None,
            operation_writer_lease_id=None,
            operation_opened_at=None,
            admitted_at=None,
            **self.control,
            committed_at=committed_at,
        )

    async def _seed_valid_v1_transaction_commit(
        self,
    ) -> adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt:
        """Materialize one authentic V1 transaction-commit parent row.

        The V2 migration deliberately relies on V1's immutable receipt
        trigger.  This test reaches it through the reviewed V1 adapter rather
        than hand-inserting a lookalike parent record.
        """

        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        head_id = uuid4()
        bootstrap_commit_id = uuid4()
        bootstrap_state_sha256 = self._state_digest(startup)
        bootstrap_commit = self._v1_commit(
            commit_id=bootstrap_commit_id,
            head_id=head_id,
            transition_kind="bootstrap",
            prior_state=None,
            next_state=startup,
            prior_state_sha256=_ZERO_SHA256,
            previous_commit_sha256=_ZERO_SHA256,
            committed_at=self.now,
        )
        head = OperationalWriterAdmissionHead(
            id=head_id,
            **_binding_mapping(self.binding),
            **_state_columns(startup),
            state_sha256=bootstrap_state_sha256,
            receipt_sha256=bootstrap_commit.receipt_sha256,
            current_commit_id=bootstrap_commit_id,
            current_commit_sha256=bootstrap_commit.commit_sha256,
            **self.control,
            committed_at=self.now,
        )
        async with self.Session() as session:
            async with session.begin():
                session.add(head)
                await session.flush()
                session.add(bootstrap_commit)
                await session.flush()

        evidence = _Evidence(
            cluster_id=self.binding.cluster_id,
            holder_site=self.binding.local_site,
            writer_epoch=7,
            writer_lease_id="writer-lease-0007",
            release_sha=self.binding.release_sha,
            generation_id=self.binding.generation_id,
            evidence_id="witness-evidence-0007",
            revalidation_id="revalidation-id-0007",
            issued_at=self.now - timedelta(seconds=10),
            expires_at=self.now + timedelta(seconds=60),
        )
        transition = admission.revalidate_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=startup,
            evidence_revalidator=_Revalidator(evidence),
            revalidation_id=evidence.revalidation_id,
            now=self.now,
        )
        self.assertIsNotNone(transition)
        assert transition is not None
        active = admission.apply_physical_operational_failover_v1_writer_admission_state_transition(
            state=startup,
            transition=transition,
        )
        revalidation_commit_id = uuid4()
        revalidation_commit = self._v1_commit(
            commit_id=revalidation_commit_id,
            head_id=head_id,
            transition_kind="witness_revalidation",
            prior_state=startup,
            next_state=active,
            prior_state_sha256=bootstrap_state_sha256,
            previous_commit_sha256=bootstrap_commit.commit_sha256,
            committed_at=self.now,
        )
        async with self.Session() as session:
            async with session.begin():
                current = await session.scalar(
                    select(OperationalWriterAdmissionHead)
                    .where(OperationalWriterAdmissionHead.id == head_id)
                    .with_for_update()
                )
                self.assertIsNotNone(current)
                assert current is not None
                session.add(revalidation_commit)
                await session.flush()
                for key, value in _state_columns(active).items():
                    setattr(current, key, value)
                current.state_sha256 = self._state_digest(active)
                current.receipt_sha256 = revalidation_commit.receipt_sha256
                current.current_commit_id = revalidation_commit_id
                current.current_commit_sha256 = revalidation_commit.commit_sha256
                current.committed_at = self.now
                await session.flush()

        operation = admission.begin_physical_operational_failover_v1_writer_operation(
            config=self.writer_config,
            state=active,
            operation_kind=admission.PHYSICAL_OPERATIONAL_FAILOVER_V1_WRITER_OPERATION_TRANSACTION_COMMIT,
            now=self.now + timedelta(seconds=1),
        )
        self.assertIsNotNone(operation)
        assert operation is not None
        writer_admission = admission.require_physical_operational_failover_v1_writer_admission(
            config=self.writer_config,
            state=active,
            operation=operation,
            now=self.now + timedelta(seconds=2),
        )
        self.assertIsNotNone(writer_admission)
        assert writer_admission is not None
        adapter = adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionAdapter(
            adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionConfig(
                enabled=True,
                writer_admission_config=self.writer_config,
                control_role_label=self.control["control_role_label"],
                control_policy_sha256=self.control["control_policy_sha256"],
            )
        )
        async with self.Session() as session:
            async with session.begin():
                receipt = await adapter.persist_writer_admission(
                    session=session,
                    writer_admission=writer_admission,
                )
        self.assertIsNotNone(receipt)
        assert receipt is not None
        return receipt

    def _hash(self, label: str) -> str:
        return hashlib.sha256(f"{self.nonce}:{label}".encode("ascii")).hexdigest()

    def _v2_commit(
        self,
        parent: adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt,
        *,
        label: str,
        attestation_sha256: str | None = None,
        writer_epoch: int | None = None,
        writer_lease_id: str | None = None,
        writer_admission_commit_sha256: str | None = None,
    ) -> PhysicalWalV2WitnessRoundtripStrictWriterCommit:
        attestation = attestation_sha256 or self._hash(f"{label}:attestation")
        return PhysicalWalV2WitnessRoundtripStrictWriterCommit(
            id=uuid4(),
            instruction_schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_RESPONSE_SCHEMA,
            configuration_sha256=self._hash(f"{label}:configuration"),
            atomic_commit_boundary=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_ATOMIC_COMMIT_BOUNDARY,
            commit_id=f"v2-witness-strict-writer-{self._hash(f'{label}:commit')}",
            attestation_sha256=attestation,
            attestation_consumption_id=f"v2-witness-consume-{attestation}",
            ir_durable_assertion_sha256=self._hash(f"{label}:ir-durable"),
            context_certificate_sha256=self._hash(f"{label}:certificate"),
            context_sha256=self._hash(f"{label}:context"),
            source_envelope_sha256=self._hash(f"{label}:source-envelope"),
            source_request_sha256=self._hash(f"{label}:source-request"),
            destination_receipt_sha256=self._hash(f"{label}:destination-receipt"),
            durable_ledger_entry_sha256=self._hash(f"{label}:durable-ledger"),
            target_recovery_evidence_sha256=self._hash(f"{label}:recovery-evidence"),
            readback_attestation_sha256=self._hash(f"{label}:readback"),
            stage_receipt_sha256=self._hash(f"{label}:stage"),
            witness_sequence=1,
            witness_ledger_entry_sha256=self._hash(f"{label}:witness-ledger"),
            witness_ledger_previous_head_sha256=_ZERO_SHA256,
            witness_ledger_binding_sha256=self._hash(f"{label}:witness-binding"),
            writer_holder_site=self.binding.local_site,
            writer_epoch=parent.writer_epoch if writer_epoch is None else writer_epoch,
            writer_lease_id=parent.writer_lease_id if writer_lease_id is None else writer_lease_id,
            witnessed_term_proof_sha256=self._hash(f"{label}:term-proof"),
            witness_transition_id=f"witness-transition-{self.nonce}-{label}",
            activation_mode="normal_fi_writer",
            activation_stream_generation_id=f"stream-generation-{self.nonce}",
            activation_route_artifact_sha256=self._hash(f"{label}:route"),
            activation_source_cutover_attestation_sha256=self._hash(f"{label}:cutover"),
            activation_receiver_permit_sha256=self._hash(f"{label}:receiver-permit"),
            writer_admission_commit_id=parent.commit_id,
            writer_admission_commit_sha256=(
                parent.commit_sha256
                if writer_admission_commit_sha256 is None
                else writer_admission_commit_sha256
            ),
            local_commit_record_id=f"v2-local-commit-{self.nonce}-{label}",
            local_response_id=f"v2-local-response-{self.nonce}-{label}",
            canonical_runtime_receipt=(f"canonical-runtime-receipt:{self.nonce}:{label}").encode("ascii"),
            runtime_commit_receipt_sha256=self._hash(f"{label}:runtime-receipt"),
            committed_at=parent.admitted_at + timedelta(seconds=1),
        )

    def _v2_bound_commit(
        self,
        parent: adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyCommitReceipt,
        *,
        label: str,
        attestation_sha256: str | None = None,
    ) -> PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit:
        """Build a structurally valid Gen2 row for registry-trigger testing.

        The Gen2 migration deliberately leaves signature verification to the
        future strict adapter.  This disposable DDL harness therefore uses
        bounded non-secret canonical placeholders while preserving every
        source-table and V1-parent scalar required by the actual trigger.
        """

        attestation = attestation_sha256 or self._hash(f"{label}:attestation")
        committed_at = parent.admitted_at + timedelta(seconds=1)
        return PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit(
            id=uuid4(),
            instruction_schema=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_RESPONSE_SCHEMA,
            configuration_sha256=self._hash(f"{label}:configuration"),
            v2_base_configuration_sha256=self._hash(
                f"{label}:base-configuration"
            ),
            atomic_commit_boundary=PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_STRICT_WRITER_BOUND_ATOMIC_COMMIT_BOUNDARY,
            commit_id=f"v2-witness-strict-writer-g2-{self._hash(f'{label}:commit')}",
            v2_base_commit_id=(
                "v2-witness-strict-writer-"
                + self._hash(f"{label}:base-commit")
            ),
            attestation_sha256=attestation,
            attestation_consumption_id=f"v2-witness-consume-g2-{attestation}",
            ir_durable_assertion_sha256=self._hash(f"{label}:ir-durable"),
            context_certificate_sha256=self._hash(f"{label}:certificate"),
            context_sha256=self._hash(f"{label}:context"),
            source_envelope_sha256=self._hash(f"{label}:source-envelope"),
            source_request_sha256=self._hash(f"{label}:source-request"),
            destination_receipt_sha256=self._hash(f"{label}:destination-receipt"),
            durable_ledger_entry_sha256=self._hash(f"{label}:durable-ledger"),
            target_recovery_evidence_sha256=self._hash(f"{label}:recovery-evidence"),
            readback_attestation_sha256=self._hash(f"{label}:readback"),
            stage_receipt_sha256=self._hash(f"{label}:stage"),
            witness_sequence=1,
            witness_ledger_entry_sha256=self._hash(f"{label}:witness-ledger"),
            witness_ledger_previous_head_sha256=_ZERO_SHA256,
            witness_ledger_binding_sha256=self._hash(f"{label}:witness-binding"),
            writer_holder_site=self.binding.local_site,
            writer_epoch=parent.writer_epoch,
            writer_lease_id=parent.writer_lease_id,
            witnessed_term_proof_sha256=self._hash(f"{label}:term-proof"),
            witness_transition_id=f"witness-transition-{self.nonce}-{label}",
            activation_mode="normal_fi_writer",
            activation_stream_generation_id=f"stream-generation-{self.nonce}",
            activation_route_artifact_sha256=self._hash(f"{label}:route"),
            activation_source_cutover_attestation_sha256=self._hash(f"{label}:cutover"),
            activation_receiver_permit_sha256=self._hash(f"{label}:receiver-permit"),
            v1_parent_cluster_id=parent.cluster_id,
            v1_parent_local_site=parent.local_site,
            v1_parent_release_sha=parent.release_sha,
            v1_parent_generation_id=parent.generation_id,
            v1_writer_admission_commit_id=parent.commit_id,
            v1_writer_admission_commit_sha256=parent.commit_sha256,
            v1_writer_admission_receipt_sha256=parent.receipt_sha256,
            v1_parent_prior_revision=parent.prior_revision,
            v1_parent_next_revision=parent.next_revision,
            v1_parent_fence_generation=parent.fence_generation,
            v1_parent_holder_site=parent.local_site,
            v1_parent_evidence_id=parent.evidence_id,
            v1_parent_revalidation_id=parent.revalidation_id,
            v1_parent_writer_epoch=parent.writer_epoch,
            v1_parent_writer_lease_id=parent.writer_lease_id,
            v1_parent_term_issued_at=self.now - timedelta(seconds=10),
            v1_parent_term_expires_at=self.now + timedelta(seconds=60),
            v1_parent_admitted_at=parent.admitted_at,
            v1_v2_writer_term_bridge_certificate_id=(
                f"v1-v2-bridge-certificate-{self.nonce}-{label}"
            ),
            v1_v2_writer_term_bridge_intent_sha256=self._hash(f"{label}:bridge-intent"),
            v1_v2_writer_term_bridge_certificate_sha256=self._hash(f"{label}:bridge-certificate"),
            v1_v2_writer_term_bridge_parent_binding_sha256=self._hash(f"{label}:bridge-parent"),
            canonical_v1_v2_writer_term_bridge_certificate=(
                f"canonical-bridge-certificate:{self.nonce}:{label}".encode("ascii")
            ),
            local_commit_record_id=f"v2-g2-local-commit-{self.nonce}-{label}",
            local_response_id=f"v2-g2-local-response-{self.nonce}-{label}",
            canonical_runtime_receipt=(
                f"canonical-gen2-runtime-receipt:{self.nonce}:{label}".encode("ascii")
            ),
            runtime_commit_receipt_sha256=self._hash(f"{label}:runtime-receipt"),
            committed_at=committed_at,
        )

    async def _insert(self, row: PhysicalWalV2WitnessRoundtripStrictWriterCommit) -> None:
        async with self.Session() as session:
            async with session.begin():
                session.add(row)
                await session.flush()

    async def _insert_bound(
        self,
        row: PhysicalWalV2WitnessRoundtripStrictWriterBoundCommit,
    ) -> None:
        async with self.Session() as session:
            async with session.begin():
                session.add(row)
                await session.flush()

    async def test_v2_strict_row_replay_immutability_v1_link_and_downgrade_guards(self) -> None:
        parent = await self._seed_valid_v1_transaction_commit()
        valid = self._v2_commit(parent, label="valid")
        await self._insert(valid)

        async with self.Session() as session:
            stored = await session.scalar(
                select(PhysicalWalV2WitnessRoundtripStrictWriterCommit).where(
                    PhysicalWalV2WitnessRoundtripStrictWriterCommit.id == valid.id
                )
            )
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(parent.commit_id, stored.writer_admission_commit_id)
        self.assertEqual(parent.commit_sha256, stored.writer_admission_commit_sha256)
        self.assertEqual(parent.writer_epoch, stored.writer_epoch)
        self.assertEqual(parent.writer_lease_id, stored.writer_lease_id)

        # A new local record cannot consume the same Witness attestation a
        # second time.  Seed a *different* valid V1 parent first, otherwise
        # V2's separate one-parent-one-response key could mask the precise
        # attestation-consumption replay constraint we intend to exercise.
        original_binding = self.binding
        original_writer_config = self.writer_config
        alternate_binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id=f"v2wsrc-replay-{self.nonce}",
            local_site="webapp_fi",
            release_sha="b" * 40,
            generation_id=f"v2wsrc-replay-generation-{self.nonce}",
        )
        self.binding = alternate_binding
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=alternate_binding,
            runtime_instance_id=f"v2wsrc-replay-runtime-{self.nonce}",
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        try:
            replay_parent = await self._seed_valid_v1_transaction_commit()
            replay = self._v2_commit(
                replay_parent,
                label="replay",
                attestation_sha256=valid.attestation_sha256,
            )
        finally:
            self.binding = original_binding
            self.writer_config = original_writer_config
        async with self.Session() as session:
            with self.assertRaises(DBAPIError) as caught:
                async with session.begin():
                    session.add(replay)
                    await session.flush()
        self.assertRegex(
            str(caught.exception),
            r"physical_wal_v2_witness_roundtrip_attestation_consumptions",
        )
        async with self.Session() as session:
            row_count = await session.scalar(
                select(func.count()).select_from(
                    PhysicalWalV2WitnessRoundtripStrictWriterCommit
                ).where(
                    PhysicalWalV2WitnessRoundtripStrictWriterCommit.attestation_sha256
                    == valid.attestation_sha256
                )
            )
        self.assertEqual(1, row_count)

        # PostgreSQL, rather than ORM convention, must reject both mutable
        # forms after a valid V2 local response/consumption becomes durable.
        for statement, parameters in (
            (
                text(
                    "UPDATE physical_wal_v2_witness_roundtrip_strict_writer_commits "
                    "SET configuration_sha256 = :replacement WHERE id = :row_id"
                ),
                {"replacement": self._hash("mutation"), "row_id": valid.id},
            ),
            (
                text(
                    "DELETE FROM physical_wal_v2_witness_roundtrip_strict_writer_commits "
                    "WHERE id = :row_id"
                ),
                {"row_id": valid.id},
            ),
        ):
            with self.subTest(sql=str(statement)):
                async with self.Session() as session:
                    with self.assertRaisesRegex(
                        DBAPIError,
                        "V2 Witness strict writer commit rows are append-only",
                    ):
                        async with session.begin():
                            await session.execute(statement, parameters)

        # The row's scalar writer term and its immutable V1 receipt digest
        # must both match the referenced V1 transaction-commit admission.  A
        # BEFORE INSERT trigger runs before the parent-row uniqueness guard,
        # so the explicit trigger error proves this is not merely a duplicate
        # key failure from reusing the same V1 parent.
        for invalid in (
            self._v2_commit(parent, label="wrong-epoch", writer_epoch=parent.writer_epoch + 1),
            self._v2_commit(
                parent,
                label="wrong-parent-digest",
                writer_admission_commit_sha256=self._hash("wrong-parent-digest"),
            ),
        ):
            with self.subTest(commit_id=invalid.commit_id):
                async with self.Session() as session:
                    with self.assertRaisesRegex(
                        DBAPIError,
                        "V2 Witness strict writer commit is inconsistent with its active V1 writer admission",
                    ):
                        async with session.begin():
                            session.add(invalid)
                            await session.flush()

        # Evidence deliberately remains in the disposable DB.  At the actual
        # head the newest base-pin child must refuse first; it is therefore
        # impossible to reach the older registry downgrade and erase either
        # the exact base identity or its global one-time fence.
        result = _run_alembic(self.target, "downgrade", MIGRATION_PARENT)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "refusing destructive Gen2 V2 base-pin downgrade: durable bound rows exist",
            f"{result.stdout}\n{result.stderr}",
        )

    async def test_global_registry_blocks_a_stale_gen1_to_gen2_replay_and_is_append_only(self) -> None:
        parent = await self._seed_valid_v1_transaction_commit()
        valid = self._v2_commit(parent, label="global-registry-gen1")
        await self._insert(valid)

        async with self.Session() as session:
            claim = await session.scalar(
                select(PhysicalWalV2WitnessRoundtripAttestationConsumption).where(
                    PhysicalWalV2WitnessRoundtripAttestationConsumption.attestation_sha256
                    == valid.attestation_sha256
                )
            )
        self.assertIsNotNone(claim)
        assert claim is not None
        self.assertEqual(
            PHYSICAL_WAL_V2_WITNESS_ROUNDTRIP_ATTESTATION_CONSUMPTION_SOURCE_GEN1,
            claim.source_generation,
        )
        self.assertEqual(valid.commit_id, claim.source_commit_id)
        self.assertEqual(valid.committed_at, claim.consumed_at)

        # A stale direct Gen1 implementation and the new Gen2 path both claim
        # the same primary-key registry before their source-specific checks.
        # This intentionally incomplete Gen2 row reaches its BEFORE trigger;
        # the registry conflict must win before nullable/shape validation can
        # obscure the cross-generation replay failure.
        with self.assertRaisesRegex(
            DBAPIError,
            "physical_wal_v2_witness_roundtrip_attestation_consumptions",
        ):
            async with self.Session() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO physical_wal_v2_witness_roundtrip_strict_writer_bound_commits "
                            "(id, commit_id, attestation_sha256, committed_at) "
                            "VALUES (:id, :commit_id, :attestation_sha256, :committed_at)"
                        ),
                        {
                            "id": uuid4(),
                            "commit_id": "v2-witness-strict-writer-g2-"
                            + self._hash("global-registry-gen2"),
                            "attestation_sha256": valid.attestation_sha256,
                            "committed_at": valid.committed_at,
                        },
                    )

        # Exercise the reciprocal direction with an authentic Gen2 source
        # row.  A later stale Gen1 process cannot consume its attestation even
        # though the old source table remains structurally available.
        original_binding = self.binding
        original_writer_config = self.writer_config
        alternate_binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id=f"v2wsrc-registry-g2-{self.nonce}",
            local_site="webapp_fi",
            release_sha="d" * 40,
            generation_id=f"v2wsrc-registry-g2-generation-{self.nonce}",
        )
        self.binding = alternate_binding
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=alternate_binding,
            runtime_instance_id=f"v2wsrc-registry-g2-runtime-{self.nonce}",
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        try:
            gen2_parent = await self._seed_valid_v1_transaction_commit()
            gen2 = self._v2_bound_commit(gen2_parent, label="global-registry-gen2")
            await self._insert_bound(gen2)
        finally:
            self.binding = original_binding
            self.writer_config = original_writer_config

        async with self.Session() as session:
            gen2_claim = await session.scalar(
                select(PhysicalWalV2WitnessRoundtripAttestationConsumption).where(
                    PhysicalWalV2WitnessRoundtripAttestationConsumption.attestation_sha256
                    == gen2.attestation_sha256
                )
            )
        self.assertIsNotNone(gen2_claim)
        assert gen2_claim is not None
        self.assertEqual("strict_writer_gen2", gen2_claim.source_generation)
        self.assertEqual(gen2.commit_id, gen2_claim.source_commit_id)

        with self.assertRaisesRegex(
            DBAPIError,
            "physical_wal_v2_witness_roundtrip_attestation_consumptions",
        ):
            async with self.Session() as session:
                async with session.begin():
                    await session.execute(
                        text(
                            "INSERT INTO physical_wal_v2_witness_roundtrip_strict_writer_commits "
                            "(id, commit_id, attestation_sha256, committed_at) "
                            "VALUES (:id, :commit_id, :attestation_sha256, :committed_at)"
                        ),
                        {
                            "id": uuid4(),
                            "commit_id": "v2-witness-strict-writer-"
                            + self._hash("global-registry-stale-gen1"),
                            "attestation_sha256": gen2.attestation_sha256,
                            "committed_at": gen2.committed_at,
                        },
                    )

        async with self.Session() as session:
            count = await session.scalar(
                select(func.count()).select_from(
                    PhysicalWalV2WitnessRoundtripAttestationConsumption
                )
                .where(
                    PhysicalWalV2WitnessRoundtripAttestationConsumption.attestation_sha256
                    == valid.attestation_sha256
                )
            )
        self.assertEqual(1, count)

        for statement in (
            text(
                "UPDATE physical_wal_v2_witness_roundtrip_attestation_consumptions "
                "SET source_generation = 'strict_writer_gen2' "
                "WHERE attestation_sha256 = :attestation_sha256"
            ),
            text(
                "DELETE FROM physical_wal_v2_witness_roundtrip_attestation_consumptions "
                "WHERE attestation_sha256 = :attestation_sha256"
            ),
            text("TRUNCATE physical_wal_v2_witness_roundtrip_attestation_consumptions"),
        ):
            with self.subTest(sql=str(statement)):
                with self.assertRaisesRegex(
                    DBAPIError,
                    "V2 Witness global attestation consumption registry rows are append-only",
                ):
                    async with self.Session() as session:
                        async with session.begin():
                            if "TRUNCATE" in str(statement):
                                await session.execute(statement)
                            else:
                                await session.execute(
                                    statement,
                                    {"attestation_sha256": valid.attestation_sha256},
                                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
