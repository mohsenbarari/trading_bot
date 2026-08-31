"""Role service wrapper for Stage 4 durable Telegram capture."""

from __future__ import annotations

import asyncio
from datetime import datetime
import os
from pathlib import Path
import threading
import time

from .private_capture import (
    CaptureEngine,
    CaptureRuntimeError,
    CaptureState,
    DurableEventSpool,
    ROLE_ACCOUNT,
    atomic_json,
    load_fixture_events,
    parse_utc,
    process_fixture_events,
    utc_now,
    utc_text,
)
from .private_capture_telegram import (
    EXACT_CATCHUP_SOURCES,
    TelegramCaptureProvider,
    load_capture_config,
    load_hmac_key,
    validate_authority_marker,
    validate_session_file,
)


DEFAULT_CAPTURE_ROOT = Path("/var/lib/market-data/capture")
DEFAULT_SESSION_ROOT = Path("/var/lib/market-data/session")


def _boolean(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CaptureRuntimeError(f"{name.lower()}_invalid")


def _fixture_now() -> datetime:
    raw = os.environ.get("MARKET_CAPTURE_FIXTURE_NOW_UTC")
    if not raw:
        return utc_now()
    return parse_utc(raw, field="capture_fixture_now_utc")


def _crash_settings() -> tuple[str | None, int]:
    point = os.environ.get("MARKET_CAPTURE_FIXTURE_CRASH_POINT", "").strip()
    if point and point not in {"after_stage", "after_append"}:
        raise CaptureRuntimeError("capture_fixture_crash_point_invalid")
    raw_sequence = os.environ.get("MARKET_CAPTURE_FIXTURE_CRASH_SEQUENCE", "0")
    try:
        sequence = int(raw_sequence)
    except ValueError as exc:
        raise CaptureRuntimeError("capture_fixture_crash_sequence_invalid") from exc
    if bool(point) != (sequence > 0):
        raise CaptureRuntimeError("capture_fixture_crash_contract_invalid")
    return point or None, sequence


def _backfill_settings() -> tuple[datetime | None, int, frozenset[str]]:
    backfill_raw = os.environ.get(
        "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC", ""
    ).strip()
    backfill_not_before = (
        parse_utc(backfill_raw, field="capture_backfill_not_before_utc")
        if backfill_raw
        else None
    )
    try:
        backfill_max_messages = int(
            os.environ.get("MARKET_CAPTURE_BACKFILL_MAX_MESSAGES", "100000")
        )
    except ValueError as exc:
        raise CaptureRuntimeError("capture_backfill_max_messages_invalid") from exc
    if not 2_000 <= backfill_max_messages <= 1_000_000:
        raise CaptureRuntimeError("capture_backfill_max_messages_invalid")
    backfill_sources_raw = os.environ.get(
        "MARKET_CAPTURE_BACKFILL_SOURCE_CODES", ""
    ).strip()
    backfill_source_items = tuple(
        item.strip() for item in backfill_sources_raw.split(",") if item.strip()
    )
    backfill_source_codes = frozenset(backfill_source_items)
    if len(backfill_source_items) != len(backfill_source_codes):
        raise CaptureRuntimeError("capture_backfill_source_codes_duplicate")
    if backfill_not_before is not None:
        if backfill_source_codes != EXACT_CATCHUP_SOURCES:
            raise CaptureRuntimeError("capture_backfill_source_codes_mismatch")
    elif backfill_source_codes:
        raise CaptureRuntimeError("capture_backfill_cutoff_required")
    return backfill_not_before, backfill_max_messages, backfill_source_codes


def run_capture_service(
    *,
    role: str,
    mode: str,
    release_sha: str,
    state_directory: Path,
    stop: threading.Event,
) -> int:
    account = ROLE_ACCOUNT.get(role)
    if account is None:
        raise CaptureRuntimeError("capture_role_invalid")
    state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    capture_root = Path(
        os.environ.get("MARKET_PIPELINE_CAPTURE_ROOT", str(DEFAULT_CAPTURE_ROOT))
    )
    session_root = Path(
        os.environ.get("MARKET_PIPELINE_SESSION_ROOT", str(DEFAULT_SESSION_ROOT))
    )
    if not capture_root.is_dir() or not os.access(capture_root, os.W_OK):
        raise CaptureRuntimeError("capture_spool_mount_not_writable")
    state = CaptureState(state_directory / "capture-state.sqlite", account=account)
    spool = DurableEventSpool(capture_root, account=account)
    engine = CaptureEngine(state, spool)
    started_at = utc_text()
    health_path = state_directory / "health.json"
    last_retention = time.monotonic()

    def write_health(*, status: str | None = None) -> None:
        nonlocal last_retention
        now = utc_now()
        if time.monotonic() - last_retention >= 3600:
            engine.retention(now=now)
            last_retention = time.monotonic()
        document = state.heartbeat(
            role=role,
            release_sha=release_sha,
            mode=mode,
            started_at_utc=started_at,
            last_durable_append=spool.last_durable_append,
            now=now,
            status=status,
        )
        atomic_json(health_path, document)

    try:
        engine.drain()
        if mode == "fixture":
            fixture_path = os.environ.get("MARKET_CAPTURE_FIXTURE_INPUT", "").strip()
            if fixture_path:
                events = load_fixture_events(Path(fixture_path))
                crash_point, crash_sequence = _crash_settings()
                process_fixture_events(
                    engine,
                    events,
                    crash_point=crash_point,
                    crash_sequence=crash_sequence,
                    now=_fixture_now(),
                )
            if _boolean("MARKET_CAPTURE_FIXTURE_RETENTION"):
                engine.retention(now=_fixture_now())
            write_health(status="fixture-ready")
            if _boolean("MARKET_CAPTURE_FIXTURE_ONESHOT"):
                return 0
            while not stop.wait(1.0):
                write_health(status="fixture-ready")
            write_health(status="fixture-stopped")
            return 0

        if mode != "live":
            raise CaptureRuntimeError("capture_runtime_mode_invalid")
        config_path = Path(
            os.environ.get(
                "MARKET_CAPTURE_CONFIG_FILE", "/run/secrets/market_capture_config"
            )
        )
        config = load_capture_config(config_path, expected_account=account)
        validate_authority_marker(session_root, role=role, release_sha=release_sha)
        session_path = session_root / config.session_filename
        validate_session_file(session_path)
        hmac_key = None
        if account == "account2":
            hmac_key = load_hmac_key(
                Path(
                    os.environ.get(
                        "MARKET_CAPTURE_HMAC_KEY_FILE",
                        "/run/secrets/market_capture_hmac_key",
                    )
                )
            )
        (
            backfill_not_before,
            backfill_max_messages,
            backfill_source_codes,
        ) = _backfill_settings()
        provider = TelegramCaptureProvider(
            config,
            engine,
            session_path=session_path,
            hmac_key=hmac_key,
            stop=stop,
            backfill_not_before=backfill_not_before,
            backfill_max_messages=backfill_max_messages,
            backfill_source_codes=backfill_source_codes,
            release_sha=release_sha,
            heartbeat=lambda: write_health(
                status=(
                    "live-starting"
                    if provider.backfill_in_progress
                    else (
                        "live-degraded"
                        if provider.reconciliation_truncated
                        else "live-ready"
                    )
                )
            ),
        )
        write_health(status="live-starting")
        asyncio.run(provider.run())
        write_health(status="live-stopped")
        return 0
    finally:
        state.close()


__all__ = ["run_capture_service"]
