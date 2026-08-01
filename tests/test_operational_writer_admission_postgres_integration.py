"""Explicitly opt-in PostgreSQL integration matrix for V1 writer admission.

This module is deliberately skipped in ordinary test runs.  It never reads a
project ``DATABASE_URL``/``SYNC_DATABASE_URL``: enabling it requires a
separate loopback PostgreSQL URL, a narrowly named disposable database, and
an exact human confirmation value.  The harness leaves durable rows in that
scratch database so the migration's downgrade-refusal path can be exercised.

Enable only against a disposable local database, for example::

    export OPERATIONAL_WRITER_ADMISSION_POSTGRES_SCRATCH_URL='postgresql://.../owa_admission_scratch_ci_12345678'
    export OPERATIONAL_WRITER_ADMISSION_POSTGRES_SCRATCH_CONFIRM='I_CONFIRM_OWA_POSTGRES_SCRATCH_ONLY'
    PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -v \
      tests.test_operational_writer_admission_postgres_integration

The required database name and loopback host are intentionally restrictive;
use a local tunnel if the disposable PostgreSQL service is elsewhere.  Do not
point this at any project, staging, or production database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core import physical_operational_failover_v1_writer_admission as admission
from core import physical_operational_failover_v1_writer_admission_sqlalchemy_transaction as adapter_module
from core import physical_operational_failover_v1_writer_admission_postgres_contract as contract
from models.operational_writer_admission import (
    OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
    OperationalWriterAdmissionCommit,
    OperationalWriterAdmissionHead,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_HEAD = "0writeradm01"
MIGRATION_PARENT = "0promauthop01"
SCRATCH_URL_ENV = "OPERATIONAL_WRITER_ADMISSION_POSTGRES_SCRATCH_URL"
SCRATCH_CONFIRM_ENV = "OPERATIONAL_WRITER_ADMISSION_POSTGRES_SCRATCH_CONFIRM"
SCRATCH_CONFIRM_VALUE = "I_CONFIRM_OWA_POSTGRES_SCRATCH_ONLY"
_SCRATCH_DATABASE_RE = re.compile(r"^owa_admission_scratch_[a-z0-9_]{8,40}$", re.ASCII)
# Literal addresses only: an /etc/hosts or DNS override for ``localhost``
# must not be able to redirect this deliberately destructive scratch harness.
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
    """Return only an explicitly confirmed, loopback, named scratch target.

    Absence of either dedicated variable is a safe ordinary-test skip.  No
    generic application/project DSN is consulted as a fallback.
    """

    raw = str(os.getenv(SCRATCH_URL_ENV, "")).strip()
    confirmation = str(os.getenv(SCRATCH_CONFIRM_ENV, "")).strip()
    if not raw or confirmation != SCRATCH_CONFIRM_VALUE:
        return None
    try:
        parsed = make_url(raw)
    except Exception as exc:  # pragma: no cover - exercised only by an operator typo
        raise RuntimeError("OWA PostgreSQL scratch URL is invalid") from exc
    if parsed.drivername not in _ALLOWED_DRIVERS:
        raise RuntimeError("OWA PostgreSQL scratch URL must use a PostgreSQL driver")
    if str(parsed.host or "").lower() not in _LOOPBACK_HOSTS:
        raise RuntimeError("OWA PostgreSQL integration requires a loopback scratch host")
    database_name = str(parsed.database or "")
    if database_name != database_name.lower():
        raise RuntimeError("OWA PostgreSQL scratch database name must be lowercase")
    if _SCRATCH_DATABASE_RE.fullmatch(database_name) is None:
        raise RuntimeError(
            "OWA PostgreSQL scratch database must be named owa_admission_scratch_<8-40 lowercase chars>"
        )
    if parsed.query:
        raise RuntimeError("OWA PostgreSQL scratch URL must not carry query options")
    return _ScratchTarget(
        database_name=database_name,
        sync_url=parsed.set(drivername="postgresql+psycopg2"),
        async_url=parsed.set(drivername="postgresql+asyncpg"),
    )


SCRATCH_TARGET = _configured_scratch_target()


def _verify_connected_target(target: _ScratchTarget) -> None:
    """Pin both the database and schema before the harness can mutate it."""

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
        raise RuntimeError("OWA PostgreSQL scratch connection did not select the named scratch database")
    if str(actual_schema) != "public":
        raise RuntimeError("OWA PostgreSQL scratch connection did not select the public scratch schema")


def _alembic_environment(target: _ScratchTarget) -> dict[str, str]:
    """Pass only the validated scratch DSN to Alembic, never inherited app DSNs."""

    environment = os.environ.copy()
    for variable in (
        "DATABASE_URL",
        "SYNC_DATABASE_URL",
        "STAGE1_MIGRATION_TEST_DATABASE_URL",
        "TRADING_BOT_MIGRATION_MODE",
        "TRADING_BOT_EXPECTED_CHECKOUT",
    ):
        environment.pop(variable, None)
    rendered = target.sync_url.render_as_string(hide_password=False)
    environment["DATABASE_URL"] = rendered
    environment["SYNC_DATABASE_URL"] = rendered
    # Alembic migrations use unqualified names.  Override any inherited role
    # default or PGOPTIONS value so they can only target the scratch public
    # schema after the database-name pin above has succeeded.
    environment["PGOPTIONS"] = _SCRATCH_SEARCH_PATH_OPTION
    return environment


def _run_alembic(target: _ScratchTarget, *arguments: str) -> subprocess.CompletedProcess[str]:
    if not arguments or arguments[0] not in {"upgrade", "downgrade"} or len(arguments) != 2:
        raise AssertionError("OWA scratch harness accepts only one-revision Alembic upgrade/downgrade")
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
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
    "set dedicated OWA PostgreSQL scratch URL and exact scratch-only confirmation to run",
)
class OperationalWriterAdmissionPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Runs only against an explicitly named local disposable PostgreSQL DB."""

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
        nonce = uuid4().hex
        self.now = datetime.now(timezone.utc).replace(microsecond=123456)
        self.binding = admission.PhysicalOperationalFailoverV1WriterAdmissionBinding(
            cluster_id=f"owa-integration-{nonce}",
            local_site="webapp_fi",
            release_sha="a" * 40,
            generation_id=f"owa-generation-{nonce}",
        )
        self.writer_config = admission.PhysicalOperationalFailoverV1WriterAdmissionConfig(
            enabled=True,
            binding=self.binding,
            runtime_instance_id=f"owa-runtime-{nonce}",
            safety_margin_seconds=5,
            maximum_term_duration_seconds=90,
            maximum_evidence_age_seconds=60,
        )
        self.control = {
            "control_boundary": OPERATIONAL_WRITER_ADMISSION_CONTROL_BOUNDARY_SCHEMA,
            "control_role_label": "owa-postgres-harness",
            "control_policy_sha256": "c" * 64,
        }

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def _state_digest(self, state: admission.PhysicalOperationalFailoverV1WriterAdmissionState) -> str:
        return contract.operational_writer_admission_postgres_state_sha256_v1(
            binding=_binding_mapping(self.binding),
            state=_state_mapping(state),
        )

    def _commit(
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

    async def _bootstrap_and_revalidate(
        self,
    ) -> admission.PhysicalOperationalFailoverV1WriterAdmissionState:
        startup = admission.new_physical_operational_failover_v1_writer_admission_state(
            binding=self.binding
        )
        head_id = uuid4()
        bootstrap_commit_id = uuid4()
        bootstrap_state_sha256 = self._state_digest(startup)
        bootstrap_commit = self._commit(
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
                # Required order: the deferrable head->commit FK permits this
                # head first, then the trigger validates the bootstrap commit.
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
        revalidation_commit = self._commit(
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
        return active

    async def test_bootstrap_successor_cas_replay_append_only_and_downgrade_refusal(self) -> None:
        active = await self._bootstrap_and_revalidate()
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
        self.assertEqual(active.revision + 1, receipt.next_revision)

        async with self.Session() as session:
            head = await session.scalar(
                select(OperationalWriterAdmissionHead).where(
                    *[
                        getattr(OperationalWriterAdmissionHead, key) == value
                        for key, value in _binding_mapping(self.binding).items()
                    ]
                )
            )
            self.assertIsNotNone(head)
            assert head is not None
            self.assertEqual(receipt.next_revision, head.revision)
            self.assertEqual(receipt.commit_id, head.current_commit_id)
            self.assertEqual(receipt.commit_sha256, head.current_commit_sha256)
            commit_rows = list(
                (
                    await session.scalars(
                        select(OperationalWriterAdmissionCommit)
                        .where(OperationalWriterAdmissionCommit.head_id == head.id)
                        .order_by(OperationalWriterAdmissionCommit.next_revision)
                    )
                ).all()
            )
            self.assertEqual(["bootstrap", "witness_revalidation", "writer_admission"], [row.transition_kind for row in commit_rows])

        # Retrying the exact original admission sees a stale head before any
        # new receipt is inserted, so old term evidence cannot be replayed.
        async with self.Session() as session:
            with self.assertRaisesRegex(
                adapter_module.PhysicalOperationalFailoverV1WriterAdmissionSqlAlchemyTransactionError,
                "HEAD_STALE_OR_MISMATCH",
            ):
                async with session.begin():
                    await adapter.persist_writer_admission(
                        session=session,
                        writer_admission=writer_admission,
                    )

        # Both mutation forms must be rejected by PostgreSQL's append-only
        # trigger.  Each failed statement runs in its own rollback scope.
        for statement, parameters in (
            (
                text(
                    "UPDATE operational_writer_admission_commits "
                    "SET receipt_sha256 = :replacement WHERE id = :commit_id"
                ),
                {"replacement": "e" * 64, "commit_id": receipt.commit_id},
            ),
            (
                text("DELETE FROM operational_writer_admission_commits WHERE id = :commit_id"),
                {"commit_id": receipt.commit_id},
            ),
        ):
            with self.subTest(sql=str(statement)):
                async with self.Session() as session:
                    with self.assertRaises(DBAPIError):
                        async with session.begin():
                            await session.execute(
                                statement,
                                parameters,
                            )

        # Rows deliberately remain, so the migration must refuse to downgrade
        # and silently discard the audit trail.  This subprocess inherits only
        # the validated dedicated scratch DSN.
        result = _run_alembic(self.target, "downgrade", MIGRATION_PARENT)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "refusing destructive operational writer-admission downgrade",
            f"{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
