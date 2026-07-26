#!/usr/bin/env python3
"""Emit bounded, traceable business events for Full Matrix sync timing.

This is a doer, not a timing oracle.  It creates ordinary synthetic offers
through the same authoritative creation paths as the Bot or WebApp, retaining
one exact API-valid idempotency key per sample.  A separate least-privilege
observer correlates those keys with the immutable DR journals.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402
from core.runtime_identity import resolve_runtime_identity  # noqa: E402
from core.webapp_writer_control import load_writer_snapshot, snapshot_is_local_active  # noqa: E402
from core.writer_fencing import writer_fence_scope  # noqa: E402
from core.offer_source import OfferSourceSurface  # noqa: E402
from scripts import trading_core_probe_worker as worker  # noqa: E402


SCHEMA = "three-site-full-matrix-timing-emitter-v1"
ROLE_ROUTES = {
    "bot_fi": (
        "bot_fi_to_webapp_fi",
        "bot_fi_to_webapp_ir_via_webapp_fi",
    ),
    "webapp_fi": (
        "webapp_fi_to_bot_fi",
        "webapp_fi_to_webapp_ir",
    ),
    "webapp_ir": (
        "webapp_ir_to_webapp_fi",
        "webapp_ir_to_bot_fi_via_webapp_fi",
    ),
}
# The longest route name leaves room for its delimiter and a four-digit sample
# sequence under the real API's 64-character idempotency-key ceiling.
SAFE_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,23}$")
SAFE_FIXTURE_PREFIX = re.compile(r"^FMX_[A-Za-z0-9_]{8,48}$")


class TimingProbeError(RuntimeError):
    """The bounded timing doer cannot make a safe business probe."""


def build_sample_plan(
    *,
    role: str,
    correlation_prefix: str,
    samples_per_route: int,
) -> list[dict[str, str]]:
    """Build exact, API-valid correlation identities without caller-provided SQL."""

    if role not in ROLE_ROUTES:
        raise TimingProbeError("timing emitter role is invalid")
    prefix = str(correlation_prefix).strip()
    if SAFE_PREFIX.fullmatch(prefix) is None:
        raise TimingProbeError("timing correlation prefix is invalid")
    if not 1 <= int(samples_per_route) <= 500:
        raise TimingProbeError("timing samples per route are outside the closed bound")
    result: list[dict[str, str]] = []
    for route in ROLE_ROUTES[role]:
        for index in range(int(samples_per_route)):
            correlation = f"{prefix}:{route}:{index:04d}"
            # The public OfferCreate contract is deliberately stricter than
            # the generic timing evidence identifier contract.
            worker.validate_probe_offer_idempotency_key(correlation)
            result.append(
                {
                    "sample_id": f"{role}:{route}:{index:04d}",
                    "route": route,
                    "correlation_id": correlation,
                }
            )
    return result


@asynccontextmanager
async def _writer_capability(*, role: str):
    if role not in {"webapp_fi", "webapp_ir"}:
        yield None
        return
    if worker.is_production_runtime():
        raise TimingProbeError("Full Matrix timing emitter is forbidden in production")
    identity = resolve_runtime_identity(settings)
    if (
        not identity.is_webapp_authority
        or not identity.is_webapp_site
        or identity.physical_site != role
    ):
        raise TimingProbeError("WebApp timing emitter lacks a WebApp site identity")
    async with worker.AsyncSessionLocal() as db:
        snapshot = await load_writer_snapshot(db)
        await db.rollback()
    active, reasons = snapshot_is_local_active(
        identity,
        snapshot,
        require_witness_lease=True,
    )
    if not active:
        raise TimingProbeError("WebApp timing emitter is not the Witness-leased Writer")
    with writer_fence_scope(
        identity,
        snapshot,
        source="three_site_full_matrix_timing",
        require_witness_lease=True,
    ) as capability:
        yield capability


async def emit(args: argparse.Namespace) -> dict[str, Any]:
    if worker.is_production_runtime():
        raise TimingProbeError("Full Matrix timing emitter is staging-only")
    fixture_prefix = str(args.fixture_prefix).strip()
    if SAFE_FIXTURE_PREFIX.fullmatch(fixture_prefix) is None:
        raise TimingProbeError("timing fixture prefix is invalid")
    samples = build_sample_plan(
        role=args.role,
        correlation_prefix=args.correlation_prefix,
        samples_per_route=args.samples_per_route,
    )
    if not 0.1 <= float(args.target_rps) <= 1000.0:
        raise TimingProbeError("timing target RPS is outside the closed bound")
    worker.setup_event_listeners()
    # The normal 300-rps case has 200 source events per origin.  A larger
    # per-role user pool prevents the ordinary per-user active-offer admission
    # policy from becoming the measurement under test.
    user_count = min(256, max(32, len(samples)))
    async with _writer_capability(role=args.role):
        async with worker.patched_trading_boundaries():
            users = (
                await worker.create_or_reuse_load_fixture_users(
                    fixture_prefix,
                    user_count=user_count,
                )
                if bool(getattr(args, "reuse_fixture_users", False))
                else await worker.create_load_fixture_users(
                    fixture_prefix,
                    user_count=user_count,
                )
            )
            commodity_id, _commodity_name = await worker.resolve_commodity()
            semaphore = asyncio.Semaphore(32)
            started_epoch = time.time()
            started_monotonic = time.monotonic()

            async def one(index: int, sample: dict[str, str]) -> dict[str, Any]:
                target = started_monotonic + index / float(args.target_rps)
                delay = target - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                began = time.monotonic()
                async with semaphore:
                    offer_id = await worker.create_offer_for_user(
                        user_id=users[index % len(users)].user_id,
                        commodity_id=commodity_id,
                        prefix=fixture_prefix,
                        index=index,
                        offer_type="sell" if index % 2 else "buy",
                        quantity=5,
                        price=100000 + (index % 10),
                        source_surface=(
                            OfferSourceSurface.TELEGRAM_BOT
                            if args.role == "bot_fi"
                            else OfferSourceSurface.WEBAPP
                        ),
                        idempotency_key=sample["correlation_id"],
                    )
                return {
                    **sample,
                    "offer_id": int(offer_id),
                    "controller_observed_duration_seconds": round(
                        time.monotonic() - began,
                        6,
                    ),
                }

            emitted = await asyncio.gather(
                *(one(index, sample) for index, sample in enumerate(samples))
            )
    elapsed = time.monotonic() - started_monotonic
    finished_epoch = time.time()
    return {
        "schema": SCHEMA,
        "status": "passed",
        "role": args.role,
        "fixture_prefix": fixture_prefix,
        "correlation_prefix": str(args.correlation_prefix),
        "samples": emitted,
        "sample_count": len(emitted),
        "target_rps": float(args.target_rps),
        "observed_emit_rps": round(len(emitted) / max(elapsed, 0.001), 6),
        "started_epoch": round(started_epoch, 6),
        "finished_epoch": round(finished_epoch, 6),
        "three_site_writer_fence": args.role in {"webapp_fi", "webapp_ir"},
        "production_touched": False,
    }


async def cleanup(args: argparse.Namespace) -> dict[str, Any]:
    if worker.is_production_runtime():
        raise TimingProbeError("Full Matrix timing cleanup is staging-only")
    fixture_prefix = str(args.fixture_prefix).strip()
    if SAFE_FIXTURE_PREFIX.fullmatch(fixture_prefix) is None:
        raise TimingProbeError("timing fixture prefix is invalid")
    worker.setup_event_listeners()
    async with _writer_capability(role=args.role):
        report = await worker.cleanup_prefix(fixture_prefix)
    return {
        "schema": SCHEMA,
        "status": "passed",
        "action": "cleanup",
        "role": args.role,
        "fixture_prefix": fixture_prefix,
        "cleanup": report,
        "production_touched": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=tuple(ROLE_ROUTES), required=True)
    parser.add_argument("--fixture-prefix", required=True)
    parser.add_argument("--correlation-prefix", default="fmxtiming:cleanup")
    parser.add_argument("--samples-per-route", type=int, default=1)
    parser.add_argument("--target-rps", type=float, default=1.0)
    parser.add_argument("--reuse-fixture-users", action="store_true")
    parser.add_argument("--cleanup-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = asyncio.run(cleanup(args) if args.cleanup_only else emit(args))
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"schema": SCHEMA, "status": "blocked", "error_class": type(exc).__name__},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
