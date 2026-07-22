#!/usr/bin/env python3
"""Deterministic four-database failure drill for the WebApp writer witness.

This program refuses non-guarded databases. It is designed for the isolated
Docker Compose topology in deploy/writer-witness-drill and never reads product
data or production credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import asyncpg
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.runtime_identity import RuntimeIdentity
from core.webapp_writer_control import (
    ACTION_ACTIVATE,
    ACTION_LEASE_REFRESH,
    load_writer_snapshot,
    snapshot_is_local_active,
    transition_writer_state,
    validate_readiness_evidence,
)
from core.writer_witness_auth import WitnessClientCredential
from core.writer_witness_client import (
    WriterWitnessClient,
    WriterWitnessClientConfig,
    WriterWitnessClientError,
    renew_local_writer_lease_once,
)
from core.writer_witness_contract import validate_witness_lease_proof
from core.writer_witness_control import load_witness_snapshot
from writer_witness_app import WriterWitnessServiceRuntime, create_writer_witness_app


GUARDED_DATABASE_PREFIX = "stage4_writer_witness_drill_"
LEASE_SECONDS = 180
SAFETY_MARGIN_SECONDS = 15
MAX_CLOCK_SKEW_SECONDS = 5
BASE_TIME = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
URL_ENVIRONMENTS = {
    "bot": "WRITER_WITNESS_DRILL_BOT_DATABASE_URL",
    "webapp_fi": "WRITER_WITNESS_DRILL_FI_DATABASE_URL",
    "webapp_ir": "WRITER_WITNESS_DRILL_IR_DATABASE_URL",
    "witness": "WRITER_WITNESS_DRILL_DATABASE_URL",
}
FI_CREDENTIAL = WitnessClientCredential(
    key_id="drill-fi-v1",
    site="webapp_fi",
    secret="drill-only-fi-hmac-0123456789abcdef-0123456789abcdef",
)
IR_CREDENTIAL = WitnessClientCredential(
    key_id="drill-ir-v1",
    site="webapp_ir",
    secret="drill-only-ir-hmac-0123456789abcdef-0123456789abcdef",
)
FI_IDENTITY = RuntimeIdentity(
    logical_authority="webapp",
    physical_site="webapp_fi",
    legacy_server_mode="iran",
    compatibility_inferred=False,
)
IR_IDENTITY = RuntimeIdentity(
    logical_authority="webapp",
    physical_site="webapp_ir",
    legacy_server_mode="iran",
    compatibility_inferred=False,
)


class DrillFailure(RuntimeError):
    """Raised when a safety invariant does not hold."""


@dataclass
class MutableClock:
    value: datetime

    async def __call__(self, _session):
        return self.value


class BlockedTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("deterministic asymmetric partition", request=request)


class LoseFirstResponseTransport(httpx.AsyncBaseTransport):
    """Commit through ASGI, then discard exactly the first response."""

    def __init__(self, app) -> None:
        self._inner = httpx.ASGITransport(app=app)
        self._lost = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        await response.aread()
        if not self._lost:
            self._lost = True
            await response.aclose()
            raise httpx.ReadTimeout(
                "deterministic response loss after witness commit",
                request=request,
            )
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DrillFailure(message)


def _database_urls() -> dict[str, URL]:
    urls: dict[str, URL] = {}
    for label, environment in URL_ENVIRONMENTS.items():
        raw = str(os.getenv(environment, "")).strip()
        if not raw:
            raise DrillFailure(f"missing {environment}")
        try:
            parsed = make_url(raw)
        except Exception as exc:
            raise DrillFailure(f"invalid guarded database URL for {label}") from exc
        if parsed.drivername != "postgresql+asyncpg":
            raise DrillFailure(f"{label} database must use postgresql+asyncpg")
        database = str(parsed.database or "")
        if not database.startswith(GUARDED_DATABASE_PREFIX):
            raise DrillFailure(
                f"refusing non-drill database for {label}: database name is not guarded"
            )
        if not parsed.host or not parsed.username or parsed.password is None:
            raise DrillFailure(f"{label} database URL must be explicit")
        urls[label] = parsed
    endpoints = {(value.host, value.port, value.database) for value in urls.values()}
    if len(endpoints) != len(urls):
        raise DrillFailure("all four drill databases must use distinct endpoints")
    return urls


def _asyncpg_kwargs(url: URL) -> dict[str, Any]:
    return {
        "host": url.host,
        "port": url.port or 5432,
        "user": url.username,
        "password": url.password,
        "database": url.database,
        "timeout": 5,
    }


async def _raw_execute(url: URL, statement: str) -> None:
    connection = await asyncpg.connect(**_asyncpg_kwargs(url))
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


def _private_and_public_key() -> tuple[str, str]:
    # This deterministic drill-only key never leaves the isolated topology and
    # is deliberately unrelated to runtime or production custody.
    private_raw = hashlib.sha256(b"writer-witness-four-db-drill-only").digest()
    private = Ed25519PrivateKey.from_private_bytes(private_raw)
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_raw).decode("ascii"),
        base64.b64encode(public_raw).decode("ascii"),
    )


def _sessions(url: URL):
    engine = create_async_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=2)
    return engine, async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def _runtime(session_factory, clock: MutableClock) -> WriterWitnessServiceRuntime:
    private_key, _ = _private_and_public_key()
    return WriterWitnessServiceRuntime(
        session_factory=session_factory,
        private_key_base64=private_key,
        credentials={
            FI_CREDENTIAL.key_id: FI_CREDENTIAL,
            IR_CREDENTIAL.key_id: IR_CREDENTIAL,
        },
        lease_duration_seconds=LEASE_SECONDS,
        auth_max_age_seconds=15,
        auth_max_future_skew_seconds=MAX_CLOCK_SKEW_SECONDS,
        clock=clock,
    )


def _client(credential: WitnessClientCredential) -> WriterWitnessClient:
    return WriterWitnessClient(
        WriterWitnessClientConfig(
            base_url="http://writer-witness.drill",
            credential=credential,
            timeout_seconds=3,
            verify=False,
        )
    )


async def _http_for_app(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://writer-witness.drill",
    )


async def _bootstrap(urls: dict[str, URL]) -> None:
    for url in urls.values():
        await _raw_execute(url, "DROP SCHEMA public CASCADE; CREATE SCHEMA public;")

    local_schema = (ROOT / "deploy/writer-witness-drill/001_webapp_writer_local.sql").read_text(
        encoding="utf-8"
    )
    witness_schema = (ROOT / "deploy/writer-witness/001_initial.sql").read_text(
        encoding="utf-8"
    ) + (ROOT / "deploy/writer-witness/002_failover_operation_ledger.sql").read_text(
        encoding="utf-8"
    )
    await asyncio.gather(
        _raw_execute(urls["webapp_fi"], local_schema),
        _raw_execute(urls["webapp_ir"], local_schema),
        _raw_execute(urls["witness"], witness_schema),
        _raw_execute(
            urls["bot"],
            """
            CREATE TABLE drill_isolation_marker (
                marker VARCHAR(64) PRIMARY KEY,
                value VARCHAR(64) NOT NULL
            );
            INSERT INTO drill_isolation_marker(marker, value)
            VALUES ('bot-db', 'untouched-by-witness');
            """,
        ),
    )
    await _raw_execute(
        urls["webapp_fi"],
        """
        INSERT INTO webapp_writer_state (
            authority, active_site, writer_epoch, control_state,
            transition_id, updated_by, reason
        ) VALUES (
            'webapp', 'webapp_fi', 1, 'active',
            '00000000-0000-0000-0000-000000000001',
            'drill-bootstrap', 'initial Finland writer'
        );
        """,
    )
    await _raw_execute(
        urls["webapp_ir"],
        """
        INSERT INTO webapp_writer_state (
            authority, active_site, writer_epoch, control_state,
            transition_id, updated_by, reason
        ) VALUES (
            'webapp', NULL, 1, 'fenced',
            '00000000-0000-0000-0000-000000000001',
            'drill-bootstrap', 'initial Iran standby'
        );
        """,
    )


async def _reset_witness(session_factory) -> None:
    async with session_factory() as session:
        await session.execute(text("DELETE FROM webapp_writer_witness_receipts"))
        await session.execute(
            text(
                """
                UPDATE webapp_writer_witness_state
                SET holder_site = NULL,
                    writer_epoch = 0,
                    lease_id = NULL,
                    lease_status = 'vacant',
                    issued_at = NULL,
                    expires_at = NULL,
                    transition_id = 'drill-reset',
                    updated_by = 'drill',
                    reason = 'reset after concurrent acquisition scenario'
                WHERE authority = 'webapp'
                """
            )
        )
        await session.commit()


async def _receipt_count(session_factory) -> int:
    async with session_factory() as session:
        return int(
            (
                await session.execute(
                    text("SELECT count(*) FROM webapp_writer_witness_receipts")
                )
            ).scalar_one()
        )


async def _bot_marker(session_factory) -> str:
    async with session_factory() as session:
        return str(
            (
                await session.execute(
                    text(
                        "SELECT value FROM drill_isolation_marker WHERE marker = 'bot-db'"
                    )
                )
            ).scalar_one()
        )


async def _assert_database_boundaries(session_factories) -> None:
    bot, fi, ir, witness = (
        session_factories[name] for name in ("bot", "webapp_fi", "webapp_ir", "witness")
    )
    async with bot() as session:
        bot_writer = (
            await session.execute(text("SELECT to_regclass('webapp_writer_state')"))
        ).scalar_one()
        bot_witness = (
            await session.execute(text("SELECT to_regclass('webapp_writer_witness_state')"))
        ).scalar_one()
    require(bot_writer is None and bot_witness is None, "bot DB received writer tables")
    for label, factory in (("webapp_fi", fi), ("webapp_ir", ir)):
        async with factory() as session:
            local_writer = (
                await session.execute(text("SELECT to_regclass('webapp_writer_state')"))
            ).scalar_one()
            local_witness = (
                await session.execute(
                    text("SELECT to_regclass('webapp_writer_witness_state')")
                )
            ).scalar_one()
        require(local_writer is not None, f"{label} local writer table is missing")
        require(local_witness is None, f"{label} received the dedicated witness table")
    async with witness() as session:
        witness_table = (
            await session.execute(text("SELECT to_regclass('webapp_writer_witness_state')"))
        ).scalar_one()
        product_table = (
            await session.execute(text("SELECT to_regclass('offers')"))
        ).scalar_one()
    require(witness_table is not None, "witness state table is missing")
    require(product_table is None, "witness DB received a product table")


async def _import_proof(
    session_factory,
    *,
    identity: RuntimeIdentity,
    proof_payload: dict[str, Any],
    epoch: int,
    expected_active_site: str | None,
    now: datetime,
    action: str = ACTION_LEASE_REFRESH,
) -> None:
    _, public_key = _private_and_public_key()
    proof = validate_witness_lease_proof(
        proof_payload,
        public_key_base64=public_key,
        expected_site=identity.physical_site,
        expected_epoch=epoch,
        now=now,
        safety_margin_seconds=SAFETY_MARGIN_SECONDS,
        max_clock_skew_seconds=MAX_CLOCK_SKEW_SECONDS,
        max_lifetime_seconds=LEASE_SECONDS,
    )
    evidence = None
    if action == ACTION_ACTIVATE:
        evidence = validate_readiness_evidence(
            {
                "evidence_id": f"drill-ready-{identity.physical_site}-{epoch}",
                "target_site": identity.physical_site,
                "writer_epoch": epoch,
                "generated_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=120)).isoformat(),
                "schema_compatible": True,
                "release_compatible": True,
                "database_ready": True,
                "storage_ready": True,
                "sync_checkpoint_ready": True,
                "no_critical_conflicts": True,
                "background_jobs_ready": True,
                "fencing_acknowledged": True,
            },
            target_site=identity.physical_site,
            writer_epoch=epoch,
            now=now,
            max_age_seconds=120,
        )
    async with session_factory() as session:
        await transition_writer_state(
            session,
            action=action,
            identity=identity,
            expected_epoch=(epoch - 1 if action == ACTION_ACTIVATE else epoch),
            expected_active_site=expected_active_site,
            operator="writer-witness-drill",
            reason="validated drill witness proof",
            evidence=evidence,
            witness_proof=proof,
            now=now,
        )
        await session.commit()


async def _core_phase(urls: dict[str, URL]) -> dict[str, Any]:
    await _bootstrap(urls)
    engines_and_sessions = {name: _sessions(url) for name, url in urls.items()}
    engines = {name: value[0] for name, value in engines_and_sessions.items()}
    sessions = {name: value[1] for name, value in engines_and_sessions.items()}
    clock = MutableClock(BASE_TIME)
    runtime = _runtime(sessions["witness"], clock)
    app = create_writer_witness_app(runtime)
    fi_client = _client(FI_CREDENTIAL)
    ir_client = _client(IR_CREDENTIAL)
    results: dict[str, Any] = {}
    try:
        await _assert_database_boundaries(sessions)
        require(
            await _bot_marker(sessions["bot"]) == "untouched-by-witness",
            "bot isolation marker changed before the drill",
        )

        # Two independent sites race for epoch 1. The witness row lock and
        # durable receipts must produce exactly one winner.
        async with await _http_for_app(app) as http:
            async def attempt(client, request_id):
                try:
                    return await client.transition(
                        action="acquire",
                        expected_epoch=0,
                        expected_lease_id=None,
                        request_id=request_id,
                        reason="concurrent four-database acquisition",
                        lease_duration_seconds=LEASE_SECONDS,
                        now=clock.value,
                        client=http,
                    )
                except WriterWitnessClientError as exc:
                    return exc

            outcomes = await asyncio.gather(
                attempt(fi_client, "drill-concurrent-fi"),
                attempt(ir_client, "drill-concurrent-ir"),
            )
        winners = [outcome for outcome in outcomes if isinstance(outcome, dict)]
        losers = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        require(len(winners) == 1 and len(losers) == 1, "concurrent acquire was not exclusive")
        require(await _receipt_count(sessions["witness"]) == 2, "race receipts are incomplete")
        results["concurrent_acquire"] = "exactly_one_winner"

        # Reset only the guarded witness DB so the remaining deterministic
        # failover sequence starts from the accepted Finland bootstrap.
        await _reset_witness(sessions["witness"])
        async with await _http_for_app(app) as http:
            initial = await fi_client.transition(
                action="acquire",
                expected_epoch=0,
                expected_lease_id=None,
                request_id="drill-fi-initial-acquire",
                reason="establish Finland epoch one",
                lease_duration_seconds=LEASE_SECONDS,
                now=clock.value,
                client=http,
            )
        require(initial["state"]["holder_site"] == "webapp_fi", "FI did not acquire epoch 1")
        lease_id = str(initial["state"]["lease_id"])
        await _import_proof(
            sessions["webapp_fi"],
            identity=FI_IDENTITY,
            proof_payload=initial["proof"],
            epoch=1,
            expected_active_site="webapp_fi",
            now=clock.value,
        )

        # The IR packet is validly authenticated but rejected while FI's lease
        # is live. Its negative receipt must remain one-shot after expiry.
        clock.value = BASE_TIME + timedelta(seconds=10)
        async with await _http_for_app(app) as http:
            try:
                await ir_client.transition(
                    action="acquire",
                    expected_epoch=1,
                    expected_lease_id=lease_id,
                    request_id="drill-delayed-ir-acquire",
                    reason="delayed acquisition packet",
                    lease_duration_seconds=LEASE_SECONDS,
                    now=clock.value,
                    client=http,
                )
            except WriterWitnessClientError as exc:
                require(not exc.retryable, "live-lease rejection was incorrectly retryable")
            else:
                raise DrillFailure("IR acquired while FI lease was live")
        delayed_receipt_count = await _receipt_count(sessions["witness"])

        # Lose the HTTP response after the database commit, recreate the whole
        # ASGI service process, and replay the exact request id.
        clock.value = BASE_TIME + timedelta(seconds=30)
        lost_transport = LoseFirstResponseTransport(app)
        async with httpx.AsyncClient(
            transport=lost_transport,
            base_url="http://writer-witness.drill",
        ) as lost_http:
            try:
                await fi_client.transition(
                    action="renew",
                    expected_epoch=1,
                    expected_lease_id=lease_id,
                    request_id="drill-lost-renew-response",
                    reason="renew with response loss after commit",
                    lease_duration_seconds=LEASE_SECONDS,
                    now=clock.value,
                    client=lost_http,
                )
            except WriterWitnessClientError as exc:
                require(exc.retryable, "lost response was not treated as ambiguous/retryable")
            else:
                raise DrillFailure("response-loss transport did not lose the first response")

        restarted_app = create_writer_witness_app(_runtime(sessions["witness"], clock))
        async with await _http_for_app(restarted_app) as restarted_http:
            replay = await fi_client.transition(
                action="renew",
                expected_epoch=1,
                expected_lease_id=lease_id,
                request_id="drill-lost-renew-response",
                reason="renew with response loss after commit",
                lease_duration_seconds=LEASE_SECONDS,
                now=clock.value,
                client=restarted_http,
            )
        require(replay.get("replayed") is True, "committed response was not replayed after restart")
        await _import_proof(
            sessions["webapp_fi"],
            identity=FI_IDENTITY,
            proof_payload=replay["proof"],
            epoch=1,
            expected_active_site="webapp_fi",
            now=clock.value,
        )
        results["lost_response_and_restart"] = "durable_exact_replay"

        # Exercise the production renewal/import function against FI's own DB.
        clock.value = BASE_TIME + timedelta(seconds=60)
        async with await _http_for_app(restarted_app) as restarted_http:
            _, public_key = _private_and_public_key()
            auto_proof = await renew_local_writer_lease_once(
                client=fi_client,
                request_id="drill-fi-auto-renew",
                identity=FI_IDENTITY,
                now=clock.value,
                session_factory=sessions["webapp_fi"],
                http_client=restarted_http,
                public_key_base64=public_key,
                lease_duration_seconds=LEASE_SECONDS,
                safety_margin_seconds=SAFETY_MARGIN_SECONDS,
                max_clock_skew_seconds=MAX_CLOCK_SKEW_SECONDS,
            )
        require(auto_proof.writer_epoch == 1, "automatic renewal changed the writer epoch")

        # Only FI -> witness is partitioned. A failed renewal must not update
        # FI's local proof. FI becomes ineligible at the safety deadline.
        async with sessions["webapp_fi"]() as session:
            fi_before_partition = await load_writer_snapshot(session)
        clock.value = BASE_TIME + timedelta(seconds=226)
        async with httpx.AsyncClient(
            transport=BlockedTransport(),
            base_url="http://writer-witness.drill",
        ) as blocked_http:
            try:
                await renew_local_writer_lease_once(
                    client=fi_client,
                    request_id="drill-fi-partitioned-renew",
                    identity=FI_IDENTITY,
                    now=clock.value,
                    session_factory=sessions["webapp_fi"],
                    http_client=blocked_http,
                    public_key_base64=public_key,
                    lease_duration_seconds=LEASE_SECONDS,
                    safety_margin_seconds=SAFETY_MARGIN_SECONDS,
                    max_clock_skew_seconds=MAX_CLOCK_SKEW_SECONDS,
                )
            except WriterWitnessClientError as exc:
                require(exc.retryable, "partition failure was not retryable")
            else:
                raise DrillFailure("partitioned FI renewal unexpectedly succeeded")
        async with sessions["webapp_fi"]() as session:
            fi_after_partition = await load_writer_snapshot(session)
        require(
            fi_after_partition.witness_lease_expires_at
            == fi_before_partition.witness_lease_expires_at,
            "partition extended FI local lease",
        )
        fi_eligible, fi_reasons = snapshot_is_local_active(
            FI_IDENTITY,
            fi_after_partition,
            now=clock.value,
            require_witness_lease=True,
            witness_safety_margin_seconds=SAFETY_MARGIN_SECONDS,
            current_boottime=(
                float(fi_after_partition.witness_observed_boottime)
                + (
                    clock.value
                    - fi_after_partition.witness_observed_wall_at
                ).total_seconds()
            ),
        )
        require(not fi_eligible, "FI remained eligible inside the safety margin")
        require(
            "writer_witness_monotonic_deadline_expired" in fi_reasons,
            "FI failed closed for wrong monotonic-clock reason",
        )
        results["asymmetric_partition"] = "fi_failed_closed_without_local_extension"

        # The prior rejected packet is replayed after expiry and must stay
        # rejected. A fresh request id can then acquire epoch 2 for IR.
        clock.value = BASE_TIME + timedelta(seconds=241)
        before_delayed_replay = await _receipt_count(sessions["witness"])
        async with await _http_for_app(restarted_app) as restarted_http:
            try:
                await ir_client.transition(
                    action="acquire",
                    expected_epoch=1,
                    expected_lease_id=lease_id,
                    request_id="drill-delayed-ir-acquire",
                    reason="delayed acquisition packet",
                    lease_duration_seconds=LEASE_SECONDS,
                    now=clock.value,
                    client=restarted_http,
                )
            except WriterWitnessClientError as exc:
                require(not exc.retryable, "negative receipt replay became retryable")
            else:
                raise DrillFailure("delayed rejected request became valid after lease expiry")
            require(
                await _receipt_count(sessions["witness"]) == before_delayed_replay,
                "negative replay created a second receipt",
            )
            ir_acquire = await ir_client.transition(
                action="acquire",
                expected_epoch=1,
                expected_lease_id=lease_id,
                request_id="drill-ir-epoch-two-acquire",
                reason="fresh Iran acquisition after Finland expiry",
                lease_duration_seconds=LEASE_SECONDS,
                now=clock.value,
                client=restarted_http,
            )
        require(ir_acquire["state"]["writer_epoch"] == 2, "IR did not receive epoch 2")
        await _import_proof(
            sessions["webapp_ir"],
            identity=IR_IDENTITY,
            proof_payload=ir_acquire["proof"],
            epoch=2,
            expected_active_site=None,
            now=clock.value,
            action=ACTION_ACTIVATE,
        )
        async with sessions["webapp_ir"]() as session:
            ir_snapshot = await load_writer_snapshot(session)
        ir_eligible, ir_reasons = snapshot_is_local_active(
            IR_IDENTITY,
            ir_snapshot,
            now=clock.value,
            require_witness_lease=True,
            witness_safety_margin_seconds=SAFETY_MARGIN_SECONDS,
        )
        require(ir_eligible, f"IR epoch 2 was not eligible: {ir_reasons}")
        require(not fi_eligible and ir_eligible, "more than one local writer was eligible")
        require(
            delayed_receipt_count <= before_delayed_replay,
            "delayed receipt accounting regressed",
        )
        results["delayed_negative_replay"] = "remained_rejected"
        results["failover"] = "only_webapp_ir_epoch_2_eligible"

        require(
            await _bot_marker(sessions["bot"]) == "untouched-by-witness",
            "witness flow mutated the Bot-FI database",
        )
        await _assert_database_boundaries(sessions)
        results["database_isolation"] = "bot_fi_webapp_fi_webapp_ir_witness_distinct"
        return results
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines.values()))


async def _pause_phase(urls: dict[str, URL]) -> dict[str, Any]:
    # The shell orchestrator has paused the actual witness PostgreSQL container.
    # Product DBs remain reachable, so local fail-closed behavior is observable.
    ir_engine, ir_sessions = _sessions(urls["webapp_ir"])
    bot_engine, bot_sessions = _sessions(urls["bot"])
    witness_engine, witness_sessions = _sessions(urls["witness"])
    try:
        async with ir_sessions() as session:
            before = await load_writer_snapshot(session)
        require(before.active_site == "webapp_ir" and before.writer_epoch == 2, "IR phase state missing")
        require(before.witness_lease_expires_at is not None, "IR lease expiry missing")
        clock = MutableClock(before.witness_lease_expires_at - timedelta(seconds=10))
        app = create_writer_witness_app(_runtime(witness_sessions, clock))
        async with await _http_for_app(app) as http:
            _, public_key = _private_and_public_key()
            try:
                await asyncio.wait_for(
                    renew_local_writer_lease_once(
                        client=_client(IR_CREDENTIAL),
                        request_id="drill-ir-witness-db-paused",
                        identity=IR_IDENTITY,
                        now=clock.value,
                        session_factory=ir_sessions,
                        http_client=http,
                        public_key_base64=public_key,
                        lease_duration_seconds=LEASE_SECONDS,
                        safety_margin_seconds=SAFETY_MARGIN_SECONDS,
                        max_clock_skew_seconds=MAX_CLOCK_SKEW_SECONDS,
                    ),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                pass
            except WriterWitnessClientError as exc:
                raise DrillFailure(
                    "witness request returned instead of blocking on the paused database: "
                    f"{exc.code}"
                ) from exc
            else:
                raise DrillFailure("renewal succeeded while witness DB container was paused")
        async with ir_sessions() as session:
            after = await load_writer_snapshot(session)
        require(
            after.witness_lease_expires_at == before.witness_lease_expires_at,
            "paused witness DB extended the local lease",
        )
        fail_closed_at = before.witness_lease_expires_at - timedelta(seconds=14)
        eligible, reasons = snapshot_is_local_active(
            IR_IDENTITY,
            after,
            now=fail_closed_at,
            require_witness_lease=True,
            witness_safety_margin_seconds=SAFETY_MARGIN_SECONDS,
            current_boottime=(
                float(after.witness_observed_boottime)
                + (fail_closed_at - after.witness_observed_wall_at).total_seconds()
            ),
        )
        require(not eligible, "IR did not fail closed while witness DB was unavailable")
        require(
            "writer_witness_monotonic_deadline_expired" in reasons,
            "pause failed closed for wrong monotonic-clock reason",
        )
        require(
            await _bot_marker(bot_sessions) == "untouched-by-witness",
            "witness DB pause mutated Bot-FI",
        )
        return {
            "witness_database_pause": "real_container_pause_detected",
            "local_lease": "not_extended",
            "safety_deadline": "failed_closed",
        }
    finally:
        await asyncio.gather(
            ir_engine.dispose(),
            bot_engine.dispose(),
            witness_engine.dispose(),
        )


async def _recovery_phase(urls: dict[str, URL]) -> dict[str, Any]:
    engines_and_sessions = {name: _sessions(url) for name, url in urls.items()}
    engines = {name: value[0] for name, value in engines_and_sessions.items()}
    sessions = {name: value[1] for name, value in engines_and_sessions.items()}
    try:
        async with sessions["webapp_ir"]() as session:
            before = await load_writer_snapshot(session)
        require(before.witness_lease_expires_at is not None, "recovery lease is missing")
        clock = MutableClock(before.witness_lease_expires_at - timedelta(seconds=10))
        app = create_writer_witness_app(_runtime(sessions["witness"], clock))
        async with await _http_for_app(app) as http:
            _, public_key = _private_and_public_key()
            proof = await renew_local_writer_lease_once(
                client=_client(IR_CREDENTIAL),
                request_id="drill-ir-after-db-resume",
                identity=IR_IDENTITY,
                now=clock.value,
                session_factory=sessions["webapp_ir"],
                http_client=http,
                public_key_base64=public_key,
                lease_duration_seconds=LEASE_SECONDS,
                safety_margin_seconds=SAFETY_MARGIN_SECONDS,
                max_clock_skew_seconds=MAX_CLOCK_SKEW_SECONDS,
            )
        require(proof.expires_at > before.witness_lease_expires_at, "resume did not advance lease")
        async with sessions["witness"]() as session:
            durable = await load_witness_snapshot(session)
            negative = (
                await session.execute(
                    text(
                        "SELECT response_json FROM webapp_writer_witness_receipts "
                        "WHERE request_id = 'drill-delayed-ir-acquire'"
                    )
                )
            ).scalar_one()
        require(durable.holder_site == "webapp_ir", "witness holder changed across DB pause")
        require(durable.writer_epoch == 2, "witness epoch changed across DB pause")
        require(json.loads(str(negative))["accepted"] is False, "negative receipt was lost")
        async with sessions["webapp_ir"]() as session:
            after = await load_writer_snapshot(session)
        eligible, reasons = snapshot_is_local_active(
            IR_IDENTITY,
            after,
            now=clock.value,
            require_witness_lease=True,
            witness_safety_margin_seconds=SAFETY_MARGIN_SECONDS,
        )
        require(eligible, f"IR did not recover after DB resume: {reasons}")
        require(
            await _bot_marker(sessions["bot"]) == "untouched-by-witness",
            "recovery mutated Bot-FI",
        )
        return {
            "witness_database_resume": "state_and_receipts_persisted",
            "writer_epoch": durable.writer_epoch,
            "holder_site": durable.holder_site,
            "renewal_after_resume": "eligible",
        }
    finally:
        await asyncio.gather(*(engine.dispose() for engine in engines.values()))


async def _run(phase: str) -> dict[str, Any]:
    urls = _database_urls()
    if phase == "core":
        return await _core_phase(urls)
    if phase == "pause":
        return await _pause_phase(urls)
    if phase == "recovery":
        return await _recovery_phase(urls)
    raise DrillFailure(f"unsupported phase={phase}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("core", "pause", "recovery"))
    arguments = parser.parse_args()
    try:
        result = asyncio.run(_run(arguments.phase))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "phase": arguments.phase,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"status": "passed", "phase": arguments.phase, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
