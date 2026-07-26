#!/usr/bin/env python3
"""Run one bounded WebApp HTTP/WebSocket regression on the active Writer.

This probe uses a unique ``FMX_`` fixture cohort and the running WebApp API,
not an in-process ASGI test client.  Offer/trade setup reuses the production
router/service path under the existing local side-effect boundary; the checks
then make real authenticated HTTP and WebSocket requests to ``webapp_fi_api``.
All relational fixtures are removed afterwards. It never contacts an external
provider or deletes a volume, Object Storage version, or non-fixture row.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import re
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets


REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402
from core.runtime_identity import resolve_runtime_identity  # noqa: E402
from core.security import create_access_token  # noqa: E402
from core.server_routing import SERVER_IRAN  # noqa: E402
from core.services.session_service import create_session  # noqa: E402
from core.utils import publish_user_event  # noqa: E402
from core.webapp_writer_control import load_writer_snapshot, snapshot_is_local_active  # noqa: E402
from core.writer_fencing import writer_fence_scope  # noqa: E402
from models.user import UserRole  # noqa: E402
from scripts import trading_core_probe_worker as worker  # noqa: E402


SCHEMA = "three-site-full-matrix-application-regression-probe-v1"
PREFIX_RE = re.compile(r"FMX_[A-Za-z0-9_]{12,96}")
SCENARIO_IDS = frozenset(
    {
        "market_trade_account_admin_regression",
        "websocket_reconnect_and_cursor_reconcile",
    }
)
API_BASE_URL = "http://webapp_fi_api:8000"
WS_BASE_URL = "ws://webapp_fi_api:8000/api/realtime/ws"
REQUEST_TIMEOUT_SECONDS = 15.0
WEBSOCKET_TIMEOUT_SECONDS = 8.0


class ApplicationRegressionProbeError(RuntimeError):
    """A bounded application regression did not meet its closed contract."""


@dataclass(frozen=True)
class Fixture:
    admin_user_id: int
    buyer_user_id: int
    buyer_session_id: str
    offer_ids: tuple[int, ...]
    trade_id: int


def _json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _prefix(value: str) -> str:
    normalized = str(value or "").strip()
    if PREFIX_RE.fullmatch(normalized) is None:
        raise ApplicationRegressionProbeError("application regression prefix is unsafe")
    return normalized


def _token(*, user_id: int, session_id: str | None = None) -> str:
    return create_access_token(
        subject=user_id,
        session_id=session_id,
        expires_delta=timedelta(minutes=10),
    )


@asynccontextmanager
async def _writer_capability():
    identity = resolve_runtime_identity(settings)
    if (
        not identity.is_webapp_authority
        or identity.physical_site != "webapp_fi"
        or identity.legacy_server_mode != SERVER_IRAN
        or str(getattr(settings, "server_mode", "") or "") != SERVER_IRAN
    ):
        raise ApplicationRegressionProbeError("application regression must run on the WebApp-FI authority")
    async with worker.AsyncSessionLocal() as db:
        snapshot = await load_writer_snapshot(db)
        await db.rollback()
    active, _reasons = snapshot_is_local_active(
        identity,
        snapshot,
        require_witness_lease=True,
    )
    if not active:
        raise ApplicationRegressionProbeError("application regression requires the Witness-leased WebApp-FI Writer")
    with writer_fence_scope(
        identity,
        snapshot,
        source="three_site_full_matrix_application_regression",
        require_witness_lease=True,
    ):
        yield {"writer_epoch": int(snapshot.writer_epoch)}


async def _prepare_fixture(prefix: str) -> Fixture:
    """Create a small, exact cohort through the ordinary business path."""

    users = await worker.create_load_fixture_users(prefix, user_count=3)
    if len(users) != 3:
        raise ApplicationRegressionProbeError("application regression fixture users are incomplete")
    admin_ref, buyer_ref, seller_ref = users
    async with worker.AsyncSessionLocal() as db:
        admin = await worker.load_user(db, admin_ref.user_id)
        buyer = await worker.load_user(db, buyer_ref.user_id)
        admin.role = UserRole.SUPER_ADMIN
        admin.max_sessions = 1
        admin.must_change_password = False
        buyer.max_sessions = 1
        buyer.must_change_password = False
        primary = await create_session(
            db,
            buyer.id,
            f"{prefix}primary-{uuid.uuid4().hex}",
            device_name="FMX primary browser",
            device_ip="198.18.0.10",
            is_primary=True,
            home_server=SERVER_IRAN,
        )
        await db.commit()

    commodity_id, _commodity_name = await worker.resolve_commodity()
    async with worker.patched_trading_boundaries():
        offer_ids = (
            await worker.create_offer_for_user(
                user_id=seller_ref.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=1,
                quantity=10,
                price=100_000,
            ),
            await worker.create_offer_for_user(
                user_id=seller_ref.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=2,
                quantity=10,
                price=100_100,
            ),
            await worker.create_offer_for_user(
                user_id=seller_ref.user_id,
                commodity_id=commodity_id,
                prefix=prefix,
                index=3,
                quantity=10,
                price=100_200,
            ),
        )
        trade = await worker.execute_trade_for_user(
            user_id=buyer_ref.user_id,
            offer_id=offer_ids[0],
            quantity=5,
            idempotency_key=f"{prefix}trade-{uuid.uuid4().hex[:16]}",
        )
    trade_id = getattr(trade, "id", None)
    if type(trade_id) is not int or trade_id < 1:
        raise ApplicationRegressionProbeError("application regression trade was not persisted")
    return Fixture(
        admin_user_id=int(admin_ref.user_id),
        buyer_user_id=int(buyer_ref.user_id),
        buyer_session_id=str(primary.id),
        offer_ids=tuple(int(value) for value in offer_ids),
        trade_id=int(trade_id),
    )


async def _get_json(path: str, *, token: str) -> tuple[int, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(
        base_url=API_BASE_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        trust_env=False,
    ) as client:
        response = await client.get(path, headers=headers)
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApplicationRegressionProbeError("application API returned non-JSON response") from exc
    return int(response.status_code), payload


async def _require_json(path: str, *, token: str) -> Any:
    status, payload = await _get_json(path, token=token)
    if status != 200:
        raise ApplicationRegressionProbeError("application API route did not return HTTP 200")
    return payload


def _item_ids(payload: Any) -> set[int]:
    if not isinstance(payload, list):
        return set()
    values: set[int] = set()
    for item in payload:
        if isinstance(item, dict) and type(item.get("id")) is int:
            values.add(int(item["id"]))
    return values


async def _cursor_pages(fixture: Fixture) -> dict[str, bool]:
    buyer_token = _token(user_id=fixture.buyer_user_id, session_id=fixture.buyer_session_id)
    offers_one = await _require_json("/api/offers/page?limit=1", token=buyer_token)
    if (
        not isinstance(offers_one, dict)
        or not isinstance(offers_one.get("items"), list)
        or offers_one.get("has_more") is not True
        or not isinstance(offers_one.get("next_cursor"), str)
        or not offers_one["next_cursor"]
    ):
        raise ApplicationRegressionProbeError("market cursor first page is invalid")
    offers_two = await _require_json(
        "/api/offers/page?" + urlencode({"limit": "1", "cursor": offers_one["next_cursor"]}),
        token=buyer_token,
    )
    if not isinstance(offers_two, dict) or not isinstance(offers_two.get("items"), list):
        raise ApplicationRegressionProbeError("market cursor second page is invalid")
    offer_first_ids = _item_ids(offers_one["items"])
    offer_second_ids = _item_ids(offers_two["items"])
    if (
        not offer_first_ids
        or not offer_second_ids
        or offer_first_ids & offer_second_ids
        or not set(fixture.offer_ids).intersection(offer_first_ids | offer_second_ids)
    ):
        raise ApplicationRegressionProbeError("market cursor did not return the bounded fixture without duplication")

    trades_one = await _require_json("/api/trades/my/page?limit=1", token=buyer_token)
    if not isinstance(trades_one, dict) or not isinstance(trades_one.get("items"), list):
        raise ApplicationRegressionProbeError("trade cursor first page is invalid")
    trade_first_ids = _item_ids(trades_one["items"])
    if fixture.trade_id not in trade_first_ids:
        raise ApplicationRegressionProbeError("trade history did not return the bounded fixture")
    next_cursor = trades_one.get("next_cursor")
    if trades_one.get("has_more") is True:
        if not isinstance(next_cursor, str) or not next_cursor:
            raise ApplicationRegressionProbeError("trade cursor continuation is missing")
        trades_two = await _require_json(
            "/api/trades/my/page?" + urlencode({"limit": "1", "cursor": next_cursor}),
            token=buyer_token,
        )
        if not isinstance(trades_two, dict) or not isinstance(trades_two.get("items"), list):
            raise ApplicationRegressionProbeError("trade cursor second page is invalid")
        if trade_first_ids & _item_ids(trades_two["items"]):
            raise ApplicationRegressionProbeError("trade cursor duplicated a prior page")
    return {
        "market_cursor_pages_exact": True,
        "trade_history_cursor_pages_exact": True,
    }


async def _market_trade_account_admin(fixture: Fixture, prefix: str) -> dict[str, bool]:
    buyer_token = _token(user_id=fixture.buyer_user_id, session_id=fixture.buyer_session_id)
    admin_token = _token(user_id=fixture.admin_user_id)
    me = await _require_json("/api/auth/me", token=buyer_token)
    if not isinstance(me, dict) or type(me.get("id")) is not int or int(me["id"]) != fixture.buyer_user_id:
        raise ApplicationRegressionProbeError("authenticated account identity differs")
    users = await _require_json("/api/users/?" + urlencode({"search": prefix, "limit": "10"}), token=admin_token)
    if not isinstance(users, list):
        raise ApplicationRegressionProbeError("admin users response is malformed")
    matched = [item for item in users if isinstance(item, dict) and str(item.get("account_name") or "").startswith(prefix)]
    if len(matched) < 3:
        raise ApplicationRegressionProbeError("admin listing did not expose the exact synthetic cohort")
    return {
        "fixture_trade_created_by_real_router": True,
        **(await _cursor_pages(fixture)),
        "account_identity_endpoint_authorized": True,
        "admin_listing_route_authorized": True,
    }


async def _receive_exact_event(socket: Any, *, nonce: str) -> None:
    deadline = time.monotonic() + WEBSOCKET_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=max(0.1, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            break
        try:
            value = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if (
            isinstance(value, dict)
            and value.get("type") == "full_matrix:reconnect"
            and isinstance(value.get("data"), dict)
            and value["data"].get("probe_nonce") == nonce
        ):
            return
    raise ApplicationRegressionProbeError("WebSocket did not receive its exact user event")


async def _websocket_round(*, fixture: Fixture) -> None:
    token = _token(user_id=fixture.buyer_user_id, session_id=fixture.buyer_session_id)
    uri = WS_BASE_URL + "?" + urlencode({"token": token})
    nonce = uuid.uuid4().hex
    async with websockets.connect(uri, open_timeout=WEBSOCKET_TIMEOUT_SECONDS, close_timeout=3) as socket:
        # The only readiness signal is successful receipt of an event published
        # after the connection has had time to install its Redis subscription.
        await asyncio.sleep(0.5)
        await publish_user_event(
            fixture.buyer_user_id,
            "full_matrix:reconnect",
            {"probe_nonce": nonce},
        )
        await _receive_exact_event(socket, nonce=nonce)
        try:
            await asyncio.wait_for(socket.recv(), timeout=0.35)
        except asyncio.TimeoutError:
            return
        raise ApplicationRegressionProbeError("WebSocket duplicated an exact one-shot user event")


async def _websocket_reconnect_and_cursor(fixture: Fixture) -> dict[str, bool]:
    await _websocket_round(fixture=fixture)
    await _websocket_round(fixture=fixture)
    return {
        "fixture_trade_created_by_real_router": True,
        "first_websocket_receives_exact_user_event": True,
        "reconnect_websocket_receives_new_exact_user_event": True,
        **(await _cursor_pages(fixture)),
    }


async def _cleanup(prefix: str) -> dict[str, bool]:
    await worker.cleanup_prefix(prefix)
    residue = await worker.cleanup_prefix(prefix, dry_run=True)
    planned = residue.get("planned_counts") if isinstance(residue, dict) else None
    if not isinstance(planned, dict) or any(int(value) != 0 for value in planned.values()):
        raise ApplicationRegressionProbeError("application regression cleanup left bounded residue")
    return {
        "only_prefixed_fixture_rows_deleted": True,
        "fixture_residue_zero": True,
    }


async def run_probe(
    *,
    scenario_id: str,
    prefix: str,
    allow_production: bool,
    allow_cleanup: bool,
) -> dict[str, Any]:
    if scenario_id not in SCENARIO_IDS:
        raise ApplicationRegressionProbeError("application regression scenario is unsupported")
    normalized = _prefix(prefix)
    worker.assert_production_full_matrix_allowed(normalized, allow_flag=allow_production)
    if worker.is_production_runtime():
        worker.allow_production_cleanup_hard_delete(normalized, allow_flag=allow_cleanup)
    worker.setup_event_listeners()
    fixture: Fixture | None = None
    cleanup: dict[str, bool] | None = None
    observation: dict[str, bool] | None = None
    failure: Exception | None = None
    async with _writer_capability() as writer_state:
        await worker.cleanup_prefix(normalized)
        try:
            fixture = await _prepare_fixture(normalized)
            if scenario_id == "market_trade_account_admin_regression":
                observation = await _market_trade_account_admin(fixture, normalized)
            else:
                observation = await _websocket_reconnect_and_cursor(fixture)
        except Exception as exc:
            failure = exc
        finally:
            cleanup = await _cleanup(normalized)
    if failure is not None:
        raise ApplicationRegressionProbeError("application regression execution raised") from failure
    if observation is None or cleanup is None:
        raise ApplicationRegressionProbeError("application regression has no result")
    return {
        "schema": SCHEMA,
        "status": "passed",
        "scenario_id": scenario_id,
        "role": "webapp_fi",
        "prefix": normalized,
        "writer_epoch": writer_state["writer_epoch"],
        "observation": observation,
        "cleanup": cleanup,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario-id", choices=sorted(SCENARIO_IDS), required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--allow-production-execution", action="store_true")
    parser.add_argument("--allow-production-cleanup", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        _json(
            asyncio.run(
                run_probe(
                    scenario_id=args.scenario_id,
                    prefix=args.prefix,
                    allow_production=bool(args.allow_production_execution),
                    allow_cleanup=bool(args.allow_production_cleanup),
                )
            )
        )
        return 0
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
