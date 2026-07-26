#!/usr/bin/env python3
"""Prove one WebApp session survives a FI-to-IR Writer transition.

``prepare`` runs only on the active WA-FI Writer. It creates two bounded
sessions for one synthetic user and proves the primary token through the
running API. ``verify`` runs only on the active WA-IR Writer after the
scheduled promotion. It uses the same session identities, so it detects a
missing private WebApp-replica row or a mismatched JWT signing secret before
testing WebSocket re-authentication and session revocation/promotion.

The caller must treat the ``fixture`` returned by ``prepare`` as transient
private control material: it is intended only for the signed, encrypted
Object-Storage request to WA-IR and must not be retained in campaign evidence.
All visible results are identity-minimized booleans and a descriptor hash.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
import websockets
from websockets.exceptions import ConnectionClosed


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import settings  # noqa: E402
from core.runtime_identity import resolve_runtime_identity  # noqa: E402
from core.security import create_access_token  # noqa: E402
from core.server_routing import SERVER_IRAN  # noqa: E402
from core.services.session_service import create_session, logout_session  # noqa: E402
from core.utils import publish_user_event  # noqa: E402
from core.webapp_writer_control import load_writer_snapshot, snapshot_is_local_active  # noqa: E402
from core.writer_fencing import writer_fence_scope  # noqa: E402
from scripts import trading_core_probe_worker as worker  # noqa: E402


SCHEMA = "three-site-full-matrix-cross-writer-session-probe-v1"
PREFIX_RE = re.compile(r"FMX_[A-Za-z0-9_]{12,96}")
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
API_SERVICE_BY_ROLE = {
    "webapp_fi": "webapp_fi_api",
    "webapp_ir": "webapp_ir_api",
}
REQUEST_TIMEOUT_SECONDS = 15.0
WEBSOCKET_TIMEOUT_SECONDS = 8.0


class CrossWriterSessionProbeError(RuntimeError):
    """The bounded cross-Writer session proof failed closed."""


@dataclass(frozen=True)
class Fixture:
    user_id: int
    primary_session_id: str
    backup_session_id: str
    descriptor_sha256: str


def _json(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _prefix(value: str) -> str:
    normalized = str(value or "").strip()
    if PREFIX_RE.fullmatch(normalized) is None:
        raise CrossWriterSessionProbeError("cross-Writer session prefix is unsafe")
    return normalized


def _session_id(value: str, *, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if UUID_RE.fullmatch(normalized) is None:
        raise CrossWriterSessionProbeError(f"{label} is invalid")
    return normalized


def _user_id(value: int) -> int:
    if type(value) is not int or value < 1:
        raise CrossWriterSessionProbeError("fixture user id is invalid")
    return value


def _fixture_hash(*, user_id: int, primary_session_id: str, backup_session_id: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "user_id": user_id,
                "primary_session_id": primary_session_id,
                "backup_session_id": backup_session_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _token(*, user_id: int, session_id: str) -> str:
    return create_access_token(
        subject=user_id,
        session_id=session_id,
        expires_delta=timedelta(minutes=10),
    )


def _api_base(role: str) -> str:
    service = API_SERVICE_BY_ROLE.get(role)
    if service is None:
        raise CrossWriterSessionProbeError("WebApp role is unsupported")
    return f"http://{service}:8000"


def _websocket_base(role: str) -> str:
    service = API_SERVICE_BY_ROLE.get(role)
    if service is None:
        raise CrossWriterSessionProbeError("WebApp role is unsupported")
    return f"ws://{service}:8000/api/realtime/ws"


@asynccontextmanager
async def _writer_capability(*, expected_role: str):
    identity = resolve_runtime_identity(settings)
    if (
        not identity.is_webapp_authority
        or identity.physical_site != expected_role
        or identity.legacy_server_mode != SERVER_IRAN
        or str(getattr(settings, "server_mode", "") or "") != SERVER_IRAN
    ):
        raise CrossWriterSessionProbeError("cross-Writer probe is not on its pinned WebApp authority")
    async with worker.AsyncSessionLocal() as db:
        snapshot = await load_writer_snapshot(db)
        await db.rollback()
    active, _reasons = snapshot_is_local_active(
        identity,
        snapshot,
        require_witness_lease=True,
    )
    if not active:
        raise CrossWriterSessionProbeError("cross-Writer probe requires the Witness-leased Writer")
    with writer_fence_scope(
        identity,
        snapshot,
        source="three_site_full_matrix_cross_writer_session",
        require_witness_lease=True,
    ):
        yield {"writer_epoch": int(snapshot.writer_epoch)}


async def _get_json(role: str, path: str, *, token: str) -> tuple[int, Any]:
    async with httpx.AsyncClient(
        base_url=_api_base(role),
        timeout=REQUEST_TIMEOUT_SECONDS,
        trust_env=False,
    ) as client:
        response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise CrossWriterSessionProbeError("cross-Writer API returned non-JSON response") from exc
    return int(response.status_code), payload


async def _require_me(role: str, *, user_id: int, session_id: str) -> None:
    status, payload = await _get_json(role, "/api/auth/me", token=_token(user_id=user_id, session_id=session_id))
    if (
        status != 200
        or not isinstance(payload, dict)
        or type(payload.get("id")) is not int
        or int(payload["id"]) != user_id
    ):
        raise CrossWriterSessionProbeError("session token was not accepted by the pinned WebApp API")


async def _prepare(prefix: str) -> tuple[Fixture, dict[str, bool]]:
    fixture: Fixture | None = None
    try:
        users = await worker.create_load_fixture_users(prefix, user_count=1)
        if len(users) != 1:
            raise CrossWriterSessionProbeError("session fixture user is incomplete")
        user_ref = users[0]
        async with worker.AsyncSessionLocal() as db:
            user = await worker.load_user(db, user_ref.user_id)
            user.max_sessions = 3
            user.must_change_password = False
            primary = await create_session(
                db,
                user.id,
                f"{prefix}primary-{uuid.uuid4().hex}",
                device_name="FMX FI primary browser",
                device_ip="198.18.0.10",
                is_primary=True,
                home_server=SERVER_IRAN,
            )
            backup = await create_session(
                db,
                user.id,
                f"{prefix}backup-{uuid.uuid4().hex}",
                device_name="FMX FI backup browser",
                device_ip="198.18.0.11",
                is_primary=False,
                home_server=SERVER_IRAN,
            )
            await db.commit()
        fixture = Fixture(
            user_id=_user_id(int(user.id)),
            primary_session_id=_session_id(
                str(primary.id), label="primary session id"
            ),
            backup_session_id=_session_id(
                str(backup.id), label="backup session id"
            ),
            descriptor_sha256="",
        )
        fixture = replace(
            fixture,
            descriptor_sha256=_fixture_hash(
                user_id=fixture.user_id,
                primary_session_id=fixture.primary_session_id,
                backup_session_id=fixture.backup_session_id,
            ),
        )
        await _require_me(
            "webapp_fi",
            user_id=fixture.user_id,
            session_id=fixture.primary_session_id,
        )
    except Exception:
        # A failed user/session creation may leave only a subset of the
        # cohort behind.  In that case there is no complete descriptor yet,
        # but the prefix is still exact and is the only safe cleanup scope.
        if fixture is None:
            await worker.cleanup_prefix(prefix)
        else:
            await _cleanup(prefix, fixture)
        raise
    if fixture is None:  # Defensive: keep the returned private descriptor total.
        raise CrossWriterSessionProbeError("session fixture descriptor is absent")
    return fixture, {"pre_promotion_primary_session_authorized": True}


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
            and value.get("type") == "full_matrix:session-continuity"
            and isinstance(value.get("data"), dict)
            and value["data"].get("probe_nonce") == nonce
        ):
            return
    raise CrossWriterSessionProbeError("post-promotion WebSocket did not receive its exact event")


async def _verify_post_promotion(fixture: Fixture) -> dict[str, bool]:
    await _require_me(
        "webapp_ir",
        user_id=fixture.user_id,
        session_id=fixture.primary_session_id,
    )
    token = _token(user_id=fixture.user_id, session_id=fixture.primary_session_id)
    uri = _websocket_base("webapp_ir") + "?" + urlencode({"token": token})
    async with websockets.connect(uri, open_timeout=WEBSOCKET_TIMEOUT_SECONDS, close_timeout=3) as socket:
        await asyncio.sleep(0.5)
        nonce = uuid.uuid4().hex
        await publish_user_event(
            fixture.user_id,
            "full_matrix:session-continuity",
            {"probe_nonce": nonce},
        )
        await _receive_exact_event(socket, nonce=nonce)
        async with worker.AsyncSessionLocal() as db:
            primary = await db.get(worker.UserSession, uuid.UUID(fixture.primary_session_id))
            if primary is None or not primary.is_active or not primary.is_primary:
                raise CrossWriterSessionProbeError("replicated primary session is invalid on WA-IR")
            promoted = await logout_session(db, primary)
        if (
            promoted is None
            or str(promoted.id) != fixture.backup_session_id
            or not promoted.is_primary
        ):
            raise CrossWriterSessionProbeError("replicated backup session was not promoted on WA-IR")
        try:
            await socket.send("ping")
            await asyncio.wait_for(socket.recv(), timeout=WEBSOCKET_TIMEOUT_SECONDS)
            await asyncio.wait_for(socket.wait_closed(), timeout=WEBSOCKET_TIMEOUT_SECONDS)
        except ConnectionClosed:
            pass
        if socket.close_code != 4003:
            raise CrossWriterSessionProbeError("revoked cross-Writer WebSocket did not close fail-closed")
    status, _payload = await _get_json(
        "webapp_ir",
        "/api/auth/me",
        token=token,
    )
    if status != 401:
        raise CrossWriterSessionProbeError("revoked primary token remained authorized on WA-IR")
    await _require_me(
        "webapp_ir",
        user_id=fixture.user_id,
        session_id=fixture.backup_session_id,
    )
    return {
        "pre_promotion_session_accepted_after_ir_writer_activation": True,
        "post_promotion_websocket_reauthenticated_and_received_exact_event": True,
        "ir_writer_revoked_primary_session_fail_closed": True,
        "ir_writer_promoted_backup_session_and_authorized_it": True,
    }


async def _cleanup(prefix: str, fixture: Fixture) -> dict[str, bool]:
    client = worker.redis.Redis(connection_pool=worker.pool)
    try:
        await client.delete(
            f"session_blacklist:{fixture.primary_session_id}",
            f"session_blacklist:{fixture.backup_session_id}",
        )
    finally:
        await client.aclose()
    await worker.cleanup_prefix(prefix)
    residue = await worker.cleanup_prefix(prefix, dry_run=True)
    planned = residue.get("planned_counts") if isinstance(residue, dict) else None
    if not isinstance(planned, dict) or any(int(value) != 0 for value in planned.values()):
        raise CrossWriterSessionProbeError("cross-Writer session cleanup left fixture residue")
    return {
        "only_prefixed_session_fixture_rows_deleted": True,
        "exact_session_blacklist_keys_removed": True,
        "fixture_residue_zero": True,
    }


def _fixture_from_args(args: argparse.Namespace) -> Fixture:
    user_id = _user_id(args.user_id)
    primary = _session_id(args.primary_session_id, label="primary session id")
    backup = _session_id(args.backup_session_id, label="backup session id")
    if primary == backup:
        raise CrossWriterSessionProbeError("primary and backup session identities must differ")
    return Fixture(
        user_id=user_id,
        primary_session_id=primary,
        backup_session_id=backup,
        descriptor_sha256=_fixture_hash(
            user_id=user_id,
            primary_session_id=primary,
            backup_session_id=backup,
        ),
    )


async def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    prefix = _prefix(args.prefix)
    worker.assert_production_full_matrix_allowed(prefix, allow_flag=bool(args.allow_production_execution))
    if worker.is_production_runtime():
        worker.allow_production_cleanup_hard_delete(prefix, allow_flag=bool(args.allow_production_cleanup))
    worker.setup_event_listeners()
    if args.mode == "prepare":
        async with _writer_capability(expected_role="webapp_fi") as writer:
            await worker.cleanup_prefix(prefix)
            fixture, observation = await _prepare(prefix)
        return {
            "schema": SCHEMA,
            "status": "passed",
            "mode": "prepare",
            "role": "webapp_fi",
            "prefix": prefix,
            "writer_epoch": writer["writer_epoch"],
            "observation": observation,
            "fixture": {
                "user_id": fixture.user_id,
                "primary_session_id": fixture.primary_session_id,
                "backup_session_id": fixture.backup_session_id,
                "descriptor_sha256": fixture.descriptor_sha256,
            },
        }
    fixture = _fixture_from_args(args)
    async with _writer_capability(expected_role="webapp_ir") as writer:
        failure: Exception | None = None
        observation: dict[str, bool] | None = None
        cleanup: dict[str, bool] | None = None
        try:
            observation = await _verify_post_promotion(fixture)
        except Exception as exc:
            failure = exc
        finally:
            cleanup = await _cleanup(prefix, fixture)
    if failure is not None:
        raise CrossWriterSessionProbeError("cross-Writer session verification failed") from failure
    if observation is None or cleanup is None:
        raise CrossWriterSessionProbeError("cross-Writer session verification has no result")
    return {
        "schema": SCHEMA,
        "status": "passed",
        "mode": "verify",
        "role": "webapp_ir",
        "prefix": prefix,
        "writer_epoch": writer["writer_epoch"],
        "descriptor_sha256": fixture.descriptor_sha256,
        "observation": observation,
        "cleanup": cleanup,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("prepare", "verify"), required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--user-id", type=int)
    parser.add_argument("--primary-session-id")
    parser.add_argument("--backup-session-id")
    parser.add_argument("--allow-production-execution", action="store_true")
    parser.add_argument("--allow-production-cleanup", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "verify" and (
        args.user_id is None
        or args.primary_session_id is None
        or args.backup_session_id is None
    ):
        return 1
    if args.mode == "prepare" and any(
        value is not None
        for value in (args.user_id, args.primary_session_id, args.backup_session_id)
    ):
        return 1
    try:
        _json(asyncio.run(run_probe(args)))
        return 0
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
