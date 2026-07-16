#!/usr/bin/env python3
"""Exercise lease behavior across clock jumps in a disposable PostgreSQL."""

from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.writer_witness_control import WriterWitnessError, load_witness_snapshot, transition_witness_state


EXPECTED_PORT = 55440


def validate_database_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "postgresql+asyncpg":
        raise argparse.ArgumentTypeError("clock probe requires a postgresql+asyncpg URL")
    if parsed.hostname != "127.0.0.1" or parsed.port != EXPECTED_PORT:
        raise argparse.ArgumentTypeError("clock probe may connect only to its isolated localhost PostgreSQL")
    if parsed.path != "/postgres":
        raise argparse.ArgumentTypeError("clock probe database must be postgres")
    return value


def private_key_base64() -> str:
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(raw).decode("ascii")


async def rejected_transition(sessions, **kwargs) -> str:
    async with sessions() as session:
        try:
            await transition_witness_state(session, **kwargs)
        except WriterWitnessError as exc:
            await session.rollback()
            return str(exc)
        await session.rollback()
    raise RuntimeError("unsafe clock-jump transition unexpectedly succeeded")


async def run_probe(database_url: str) -> dict[str, object]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    key = private_key_base64()
    t0 = datetime(2035, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    try:
        async with sessions() as session:
            first = await transition_witness_state(
                session,
                action="acquire",
                requester_site="webapp_fi",
                expected_epoch=0,
                expected_lease_id=None,
                request_id="clock-probe-acquire-fi",
                operator="isolated-clock-probe",
                reason="establish the first isolated lease",
                private_key_base64=key,
                lease_duration_seconds=30,
                now=t0,
            )
            await session.commit()
        first_lease = first.state.lease_id
        backward_live_rejection = await rejected_transition(
            sessions,
            action="acquire",
            requester_site="webapp_ir",
            expected_epoch=1,
            expected_lease_id=first_lease,
            request_id="clock-probe-backward-ir",
            operator="isolated-clock-probe",
            reason="prove a backward clock cannot steal a live lease",
            private_key_base64=key,
            lease_duration_seconds=30,
            now=t0 - timedelta(seconds=60),
        )
        async with sessions() as session:
            second = await transition_witness_state(
                session,
                action="acquire",
                requester_site="webapp_ir",
                expected_epoch=1,
                expected_lease_id=first_lease,
                request_id="clock-probe-forward-ir",
                operator="isolated-clock-probe",
                reason="acquire only after the first lease is expired",
                private_key_base64=key,
                lease_duration_seconds=30,
                now=t0 + timedelta(seconds=31),
            )
            await session.commit()
        stale_epoch_rejection = await rejected_transition(
            sessions,
            action="renew",
            requester_site="webapp_fi",
            expected_epoch=1,
            expected_lease_id=first_lease,
            request_id="clock-probe-stale-renew-fi",
            operator="isolated-clock-probe",
            reason="prove a backward clock cannot revive an old epoch",
            private_key_base64=key,
            lease_duration_seconds=30,
            now=t0,
        )
        async with sessions() as session:
            final = await load_witness_snapshot(session)
        if final.writer_epoch != 2 or final.holder_site != "webapp_ir":
            raise RuntimeError("isolated clock probe ended with an unsafe writer state")
        if "live witness lease" not in backward_live_rejection:
            raise RuntimeError("backward-clock rejection was not caused by the live lease")
        if "stale witness epoch" not in stale_epoch_rejection:
            raise RuntimeError("old-epoch renewal was not fenced by the durable epoch")
        return {
            "status": "passed",
            "scenario": "isolated-postgresql-clock-jump",
            "backward_clock_steal_rejected": True,
            "forward_expiry_acquire_epoch": second.state.writer_epoch,
            "old_epoch_revival_rejected": True,
            "final_holder_site": final.holder_site,
            "final_writer_epoch": final.writer_epoch,
            "production_database_touched": False,
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True, type=validate_database_url)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_probe(args.database_url)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
