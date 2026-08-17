#!/usr/bin/env python3
"""Live Telegram collector, one-minute estimator, and read-only web page."""

from __future__ import annotations

import argparse
import asyncio
import glob
import hmac
import html
import json
import math
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime, time as dt_time, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo
import jdatetime


APP_ROOT = Path(__file__).resolve().parent
REPO_ROOT = APP_ROOT.parents[1]
RUNTIME_ROOT = Path(
    os.environ.get("COIN_RATE_ESTIMATOR_RUNTIME_DIR", APP_ROOT / "runtime")
).expanduser()
COLLECTOR_ROOT = APP_ROOT
COLLECTOR_DEPS = COLLECTOR_ROOT / ".deps"
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(COLLECTOR_ROOT))
sys.path.insert(0, str(COLLECTOR_DEPS))

from coin_estimator import (  # noqa: E402
    DEFAULT_CALIBRATION_DB,
    DEFAULT_CONVERSATION_DB,
    DEFAULT_MARKET_DB,
    DEFAULT_MODEL,
    DEFAULT_REVIEW_DECISIONS_DB,
    COMMODITY_SPECS,
    GROUP_ANCHOR_WINDOW_SECONDS,
    NO_DATA_TOKEN,
    apply_low_date_family_band_separation,
    enforce_cash_tomorrow_term_structure,
    estimate_rates,
    iso_utc,
    live_point_value,
    load_model,
    parse_datetime,
    write_json_atomic,
)
from offer_text_parser import (  # noqa: E402
    SupervisedOfferParser,
    parse_reviewed_offer,
    strip_clock_from_raw_offer,
)
from telegram_price_collector.config import (  # noqa: E402
    DEFAULT_CHANNELS,
    Settings,
    source_code_for_channel,
)
from telegram_price_collector.db import (  # noqa: E402
    connect,
    infer_naghdp_trade_sides,
    initialize,
    replace_price_events,
    upsert_external_observations,
    upsert_raw_post,
)
from core.market_intelligence.input_health import (  # noqa: E402
    InputHealthConfig,
    build_estimator_input_health,
    update_probe_state,
)
from core.market_intelligence.coin_group_feedback import (  # noqa: E402
    AMBIGUOUS_FIELDS as COIN_GROUP_AMBIGUOUS_FIELDS,
    CoinGroupFeedbackError,
    ensure_coin_group_feedback_store,
    load_coin_group_parser_feedback,
    record_coin_group_parser_feedback,
)
from core.market_intelligence.coin_groups import (  # noqa: E402
    CoinGroupMessageInput,
    parse_coin_group_offers,
)
from core.market_intelligence.market_contracts import derive_event_key  # noqa: E402
from telegram_price_collector.external_collectors import (  # noqa: E402
    ExternalSourceError,
    fetch_binance_paxg_live,
    fetch_ime_live,
    fetch_wallex_live,
)
from telegram_price_collector.models import RawPost  # noqa: E402
from telegram_price_collector.parsers import parse_message  # noqa: E402
from online_recalibration import (  # noqa: E402
    COMPARISON_EVALUATION_ROLE,
    LEARNING_EVALUATION_ROLE,
    MAIN_COMPARISON_MODEL_ID,
    MAIN_MODEL_ID,
    apply_snapshot_calibration,
    apply_recent_realized_snapshot_calibration,
    ensure_schema as ensure_online_schema,
    maintain_prediction_ledger,
    reconcile_predictions,
    record_predictions,
    summarize_model_outcomes,
)
from shadow_parallel import run_shadow_parallel  # noqa: E402
from estimate_finalization import finalize_deterministic_book  # noqa: E402
from ml_residual_shadow import (  # noqa: E402
    DEFAULT_ML_ARTIFACT,
    DEFAULT_ML_SHADOW_STATE,
    run_ml_residual_shadow,
)
from shadow_cross_calibration import maybe_run_shadow_cross_calibration  # noqa: E402


TEHRAN = ZoneInfo("Asia/Tehran")
DEFAULT_STATE = RUNTIME_ROOT / "state.json"
DEFAULT_SHADOW_MODEL = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_SHADOW_MODEL",
        str(RUNTIME_ROOT / "model.main-candidate.slim.json"),
    )
)
DEFAULT_SHADOW_STATE = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_SHADOW_STATE",
        str(RUNTIME_ROOT / "state.shadow.json"),
    )
)
DEFAULT_RESEARCH_SHADOW_MODEL = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_MORNING_REOPEN_SHADOW_MODEL",
        str(RUNTIME_ROOT / "model.morning-reopen.candidate.json"),
    )
)
DEFAULT_RESEARCH_SHADOW_STATE = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_MORNING_REOPEN_SHADOW_STATE",
        str(RUNTIME_ROOT / "state.morning-reopen.shadow.json"),
    )
)
DEFAULT_ML_SHADOW_MODEL = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_ML_SHADOW_MODEL",
        str(DEFAULT_ML_ARTIFACT),
    )
)
DEFAULT_ML_SHADOW_STATE_PATH = Path(
    os.environ.get(
        "COIN_RATE_ESTIMATOR_ML_SHADOW_STATE",
        str(DEFAULT_ML_SHADOW_STATE),
    )
)
DEFAULT_WRITE_TOKEN_FILE = RUNTIME_ROOT / "manual-entry.token"
DEFAULT_GROUP_LIVE_CONTROL = RUNTIME_ROOT / "group-live-input-control.json"
DEFAULT_DASHBOARD_CREDENTIALS_FILE = RUNTIME_ROOT / "dashboard-credentials.json"
PUBLIC_COLLECTOR_HEALTH_NAME = "public-telegram-health.json"
EXTERNAL_MARKET_HEALTH_NAME = "external-market-health.json"
GROUP_PROJECTION_HEALTH_NAME = "group-event-health.json"
MAX_MANUAL_FORM_BYTES = 16_384
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def shadow_light_mode() -> bool:
    """Keep the 30-second cadence and defer only morning cross-calibration."""

    return _env_flag("COIN_RATE_ESTIMATOR_SHADOW_LIGHT", "0")


def estimate_refresh_seconds() -> int:
    raw = os.environ.get("COIN_RATE_ESTIMATOR_ESTIMATE_REFRESH_SECONDS", "").strip()
    if raw:
        try:
            return max(5, int(raw))
        except ValueError:
            pass
    return 30 if shadow_light_mode() else 5


def input_health_config(
    market_db: Path,
    conversation_db: Path,
) -> InputHealthConfig:
    """Resolve sidecar heartbeat paths without embedding host-specific roots."""

    return InputHealthConfig(
        public_telegram_state=Path(
            os.environ.get(
                "COIN_RATE_ESTIMATOR_PUBLIC_TELEGRAM_HEALTH",
                market_db.parent / PUBLIC_COLLECTOR_HEALTH_NAME,
            )
        ).expanduser(),
        external_market_state=Path(
            os.environ.get(
                "COIN_RATE_ESTIMATOR_EXTERNAL_MARKET_HEALTH",
                market_db.parent / EXTERNAL_MARKET_HEALTH_NAME,
            )
        ).expanduser(),
        group_projection_state=Path(
            os.environ.get(
                "COIN_RATE_ESTIMATOR_GROUP_PROJECTION_HEALTH",
                conversation_db.parent / GROUP_PROJECTION_HEALTH_NAME,
            )
        ).expanduser(),
        public_telegram_max_age_seconds=max(
            30,
            int(os.environ.get("COIN_RATE_ESTIMATOR_PUBLIC_HEARTBEAT_MAX_AGE", "60")),
        ),
        wallex_max_age_seconds=max(
            20,
            int(os.environ.get("COIN_RATE_ESTIMATOR_WALLEX_HEARTBEAT_MAX_AGE", "45")),
        ),
        binance_paxg_max_age_seconds=max(
            20,
            int(os.environ.get("COIN_RATE_ESTIMATOR_PAXG_HEARTBEAT_MAX_AGE", "45")),
        ),
        group_projection_max_age_seconds=max(
            45,
            int(os.environ.get("COIN_RATE_ESTIMATOR_GROUP_HEARTBEAT_MAX_AGE", "90")),
        ),
    )


def load_dashboard_credentials() -> tuple[str, str]:
    """Return (username, password) from env or a mode-0600 runtime file.

    Environment wins when both are set:
    ``COIN_ESTIMATOR_DASHBOARD_USER`` / ``COIN_ESTIMATOR_DASHBOARD_PASSWORD``.
    Otherwise ``COIN_ESTIMATOR_DASHBOARD_CREDENTIALS_FILE`` (default
    ``runtime/dashboard-credentials.json``) must contain
    ``{"username": "...", "password": "..."}``.
    """

    env_user = os.environ.get("COIN_ESTIMATOR_DASHBOARD_USER", "").strip()
    env_password = os.environ.get("COIN_ESTIMATOR_DASHBOARD_PASSWORD", "")
    if env_user and env_password:
        return env_user, env_password

    credentials_path = Path(
        os.environ.get(
            "COIN_ESTIMATOR_DASHBOARD_CREDENTIALS_FILE",
            str(DEFAULT_DASHBOARD_CREDENTIALS_FILE),
        )
    ).expanduser()
    if not credentials_path.is_file():
        raise RuntimeError(
            "Dashboard credentials missing: set COIN_ESTIMATOR_DASHBOARD_USER/"
            "COIN_ESTIMATOR_DASHBOARD_PASSWORD or provide "
            f"{credentials_path}"
        )
    mode = credentials_path.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError(
            f"Dashboard credentials file permissions must be 600: {credentials_path}"
        )
    payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Dashboard credentials file must contain a JSON object")
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        raise RuntimeError("Dashboard credentials file must include username and password")
    return username, password


def ensure_manual_entry_schema(conversation_db: Path) -> None:
    """Create the operator-entry tables without altering imported raw events."""

    conversation_db.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(conversation_db)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS manual_coin_offers (
                id INTEGER PRIMARY KEY,
                occurred_at_utc TEXT NOT NULL,
                is_live_at_entry INTEGER NOT NULL CHECK (is_live_at_entry IN (0, 1)),
                commodity TEXT NOT NULL,
                settlement TEXT NOT NULL CHECK (settlement IN ('CASH', 'TOMORROW')),
                trade_form TEXT NOT NULL CHECK (trade_form IN ('PHYSICAL', 'PAPER')),
                side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                price INTEGER NOT NULL CHECK (price > 0),
                quantity INTEGER CHECK (quantity IS NULL OR quantity > 0),
                description TEXT,
                created_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS manual_coin_offers_lookup_idx
                ON manual_coin_offers(commodity, settlement, trade_form, occurred_at_utc);
            CREATE TABLE IF NOT EXISTS manual_coin_confirmed_trades (
                id INTEGER PRIMARY KEY,
                offer_id INTEGER NOT NULL REFERENCES manual_coin_offers(id) ON DELETE RESTRICT,
                occurred_at_utc TEXT NOT NULL,
                is_live_at_entry INTEGER NOT NULL CHECK (is_live_at_entry IN (0, 1)),
                price INTEGER NOT NULL CHECK (price > 0),
                quantity INTEGER CHECK (quantity IS NULL OR quantity > 0),
                created_at_utc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS manual_coin_confirmed_trades_lookup_idx
                ON manual_coin_confirmed_trades(occurred_at_utc, offer_id);
            """
        )
        offer_columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(manual_coin_offers)"
            )
        }
        if "raw_offer_text" not in offer_columns:
            connection.execute(
                "ALTER TABLE manual_coin_offers "
                "ADD COLUMN raw_offer_text TEXT"
            )
        connection.commit()
    finally:
        connection.close()


_CALIBRATION_TABLES = (
    "coin_estimate_predictions",
    "coin_online_residual_state",
)


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(
        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
    )


def prepare_calibration_store(
    calibration_db: Path,
    conversation_db: Path,
) -> dict[str, Any]:
    """Create the mutable ledger outside the promotion-owned input database.

    Older deployments kept residual state and prediction rows beside the
    imported conversation data.  Copy those two runtime-only tables once into
    the sidecar so enabling the isolation neither loses learning history nor
    keeps rewriting the conversation database on every estimate refresh.
    """

    calibration_db.parent.mkdir(parents=True, exist_ok=True)
    if calibration_db.resolve() == conversation_db.resolve():
        raise ValueError(
            "COIN_RATE_ESTIMATOR_CALIBRATION_DB must differ from COIN_CONVERSATION_DB"
        )

    target = sqlite3.connect(calibration_db)
    target.row_factory = sqlite3.Row
    source: sqlite3.Connection | None = None
    copied: dict[str, int] = {}
    try:
        ensure_online_schema(target)
        source = sqlite3.connect(
            f"file:{conversation_db.resolve()}?mode=ro", uri=True
        )
        source.row_factory = sqlite3.Row
        source_tables = {
            str(row[0])
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in _CALIBRATION_TABLES:
            if table not in source_tables:
                copied[table] = 0
                continue
            if int(target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]):
                copied[table] = 0
                continue
            source_columns = set(_table_columns(source, table))
            columns = tuple(
                column
                for column in _table_columns(target, table)
                if column in source_columns
            )
            if not columns:
                copied[table] = 0
                continue
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            rows = source.execute(
                f'SELECT {quoted_columns} FROM "{table}" ORDER BY rowid'
            )
            copied[table] = 0
            batch: list[tuple[Any, ...]] = []
            for row in rows:
                batch.append(tuple(row[column] for column in columns))
                if len(batch) >= 1_000:
                    target.executemany(
                        f'INSERT OR IGNORE INTO "{table}" ({quoted_columns}) '
                        f"VALUES ({placeholders})",
                        batch,
                    )
                    copied[table] += len(batch)
                    batch.clear()
            if batch:
                target.executemany(
                    f'INSERT OR IGNORE INTO "{table}" ({quoted_columns}) '
                    f"VALUES ({placeholders})",
                    batch,
                )
                copied[table] += len(batch)
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        if source is not None:
            source.close()
        target.close()
    return {"status": "READY", "copied_rows": copied}


def open_calibration_connection(calibration_db: Path) -> sqlite3.Connection:
    """Open the isolated mutable calibration store with its schema ready."""

    connection = sqlite3.connect(calibration_db)
    connection.row_factory = sqlite3.Row
    ensure_online_schema(connection)
    return connection


def open_conversation_read_connection(conversation_db: Path) -> sqlite3.Connection:
    """Open group observations read-only so calibration cannot race promotion."""

    connection = sqlite3.connect(
        f"file:{conversation_db.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    return connection


def read_or_create_write_token(path: Path) -> str:
    """Return a local, 0600 token for the write-only operator form."""

    configured = os.environ.get("COIN_ESTIMATOR_WRITE_TOKEN", "").strip()
    if configured:
        if len(configured) < 24:
            raise RuntimeError("COIN_ESTIMATOR_WRITE_TOKEN must be at least 24 characters")
        return configured
    if path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if len(token) >= 24:
            return token
        raise RuntimeError(f"Manual-entry token file is invalid: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, (token + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    return token


def normalize_integer(value: str, *, field: str, required: bool = True) -> int | None:
    normalized = value.translate(PERSIAN_DIGITS).replace(",", "").replace("٬", "").strip()
    if not normalized:
        if required:
            raise ValueError(f"{field} الزامی است.")
        return None
    if not normalized.isdigit():
        raise ValueError(f"{field} باید فقط عدد صحیح باشد.")
    number = int(normalized)
    if number <= 0 or number > 10_000_000:
        raise ValueError(f"{field} خارج از محدوده مجاز است.")
    return number


def normalize_project_toman_price(
    value: str, *, field: str, required: bool = True
) -> int | None:
    """Accept only project-thousand-toman fields (UI label: تومان)."""

    number = normalize_integer(value, field=field, required=required)
    if number is None:
        return None
    try:
        from core.market_intelligence.price_magnitude_policy import (
            PriceUnitPolicyError,
            assert_project_toman_field,
        )

        return assert_project_toman_field(number, field=field)
    except PriceUnitPolicyError as exc:
        code = str(exc)
        if "full_toman" in code:
            raise ValueError(
                f"{field} نباید به تومان کامل باشد؛ واحد مجاز همان عدد پروژه‌ای "
                "(مثلاً ۱۸۵۰۰۰) است."
            ) from exc
        if "rial" in code:
            raise ValueError(
                f"{field} شبیه ریال است؛ فقط تومان با واحد پروژه‌ای مجاز است."
            ) from exc
        raise ValueError(f"{field} خارج از محدودهٔ مجاز تومان است.") from exc


def parse_tehran_form_datetime(value: str, *, field: str) -> datetime:
    normalized = value.translate(PERSIAN_DIGITS).strip().replace("/", "-")
    if not normalized:
        raise ValueError(f"{field} الزامی است.")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", normalized):
        normalized += "T00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field} معتبر نیست.") from exc
    if parsed.tzinfo is not None:
        raise ValueError(f"{field} باید با ساعت تهران وارد شود.")
    return parsed.replace(tzinfo=TEHRAN).astimezone(timezone.utc)


def parse_offer_text(
    text: str,
    *,
    conversation_db: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Parse one editable suggestion using reviewed labels when available."""

    if conversation_db is not None:
        return parse_reviewed_offer(
            text,
            conversation_db=conversation_db,
            now=now,
        )
    return SupervisedOfferParser([]).parse(text, now=now)


def insert_manual_entry(conversation_db: Path, form: dict[str, str]) -> dict[str, Any]:
    """Validate and persist one structured offer and an optional linked trade."""

    now = datetime.now(timezone.utc)
    commodity = form.get("commodity", "")
    if commodity not in COMMODITY_SPECS:
        raise ValueError("کالای انتخاب‌شده معتبر نیست.")
    settlement = form.get("settlement", "")
    if settlement not in {"CASH", "TOMORROW"}:
        raise ValueError("نوع تسویه معتبر نیست.")
    trade_form = form.get("trade_form", "")
    if trade_form not in {"PHYSICAL", "PAPER"}:
        raise ValueError("نوع معامله معتبر نیست.")
    side = form.get("side", "")
    if side not in {"BUY", "SELL"}:
        raise ValueError("سمت آفر معتبر نیست.")
    price = normalize_project_toman_price(form.get("price", ""), field="قیمت آفر")
    quantity = normalize_integer(form.get("quantity", ""), field="تعداد آفر", required=False)
    description = form.get("description", "").strip()
    # The clock embedded by the operator is input metadata, not part of a
    # normal market offer. Persist only the actual offer wording.
    raw_offer_text = strip_clock_from_raw_offer(
        form.get("raw_offer_text", "")
    )
    if len(description) > 2_000 or len(raw_offer_text) > 2_000:
        raise ValueError("توضیحات نباید بیش از ۲۰۰۰ کاراکتر باشد.")
    offer_live = form.get("offer_live") == "1"
    offer_time = now if offer_live else parse_tehran_form_datetime(
        form.get("offer_time", ""), field="زمان آفر"
    )
    if offer_time > now + timedelta(minutes=2):
        raise ValueError("زمان آفر نمی‌تواند بیش از دو دقیقه در آینده باشد.")

    trade_confirmed = form.get("trade_confirmed") == "1"
    trade_time: datetime | None = None
    trade_live = False
    trade_price: int | None = None
    trade_quantity: int | None = None
    if trade_confirmed:
        trade_live = form.get("trade_live") == "1"
        trade_time = now if trade_live else parse_tehran_form_datetime(
            form.get("trade_time", ""), field="زمان معامله"
        )
        if trade_time < offer_time:
            raise ValueError("زمان معامله نمی‌تواند قبل از زمان آفر باشد.")
        if trade_time > now + timedelta(minutes=2):
            raise ValueError("زمان معامله نمی‌تواند بیش از دو دقیقه در آینده باشد.")
        trade_price = normalize_project_toman_price(
            form.get("trade_price", ""), field="قیمت معامله", required=False
        ) or price
        trade_quantity = normalize_integer(
            form.get("trade_quantity", ""), field="تعداد معامله", required=False
        )
        if quantity is not None and trade_quantity is not None and trade_quantity > quantity:
            raise ValueError("تعداد معامله نمی‌تواند از تعداد آفر بیشتر باشد.")

    ensure_manual_entry_schema(conversation_db)
    connection = sqlite3.connect(conversation_db)
    try:
        cursor = connection.execute(
            """
            INSERT INTO manual_coin_offers(
                occurred_at_utc, is_live_at_entry, commodity, settlement, trade_form,
                side, price, quantity, description, raw_offer_text, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                iso_utc(offer_time), int(offer_live), commodity, settlement,
                trade_form, side, price, quantity, description or None,
                raw_offer_text or None, iso_utc(now),
            ),
        )
        offer_id = int(cursor.lastrowid)
        trade_id = None
        if trade_confirmed and trade_time is not None and trade_price is not None:
            cursor = connection.execute(
                """
                INSERT INTO manual_coin_confirmed_trades(
                    offer_id, occurred_at_utc, is_live_at_entry, price, quantity,
                    created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    offer_id, iso_utc(trade_time), int(trade_live), trade_price,
                    trade_quantity, iso_utc(now),
                ),
            )
            trade_id = int(cursor.lastrowid)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "offer_id": offer_id,
        "trade_id": trade_id,
        "offer_time_utc": iso_utc(offer_time),
        "trade_time_utc": iso_utc(trade_time) if trade_time else None,
    }


def list_open_manual_offers(conversation_db: Path, *, limit: int = 40) -> list[dict[str, Any]]:
    if not conversation_db.is_file():
        return []
    connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"manual_coin_offers", "manual_coin_confirmed_trades"}.issubset(tables):
            return []
        rows = connection.execute(
            """
            SELECT o.id, o.occurred_at_utc, o.commodity, o.settlement, o.trade_form,
                   o.side, o.price, o.quantity
            FROM manual_coin_offers AS o
            WHERE NOT EXISTS (
              SELECT 1 FROM manual_coin_confirmed_trades AS t WHERE t.offer_id=o.id
            )
            ORDER BY o.occurred_at_utc DESC, o.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def insert_manual_trade_for_open_offer(
    conversation_db: Path, form: dict[str, str]
) -> dict[str, Any]:
    offer_id = normalize_integer(form.get("offer_id", ""), field="آفر انتخاب‌شده")
    now = datetime.now(timezone.utc)
    trade_live = form.get("trade_live") == "1"
    trade_time = now if trade_live else parse_tehran_form_datetime(
        form.get("trade_time", ""), field="زمان معامله"
    )
    if trade_time > now + timedelta(minutes=2):
        raise ValueError("زمان معامله نمی‌تواند بیش از دو دقیقه در آینده باشد.")
    ensure_manual_entry_schema(conversation_db)
    connection = sqlite3.connect(conversation_db)
    connection.row_factory = sqlite3.Row
    try:
        offer = connection.execute(
            """
            SELECT id, occurred_at_utc, price, quantity
            FROM manual_coin_offers WHERE id=?
            """,
            (offer_id,),
        ).fetchone()
        if offer is None:
            raise ValueError("آفر انتخاب‌شده وجود ندارد.")
        existing = connection.execute(
            "SELECT 1 FROM manual_coin_confirmed_trades WHERE offer_id=?",
            (offer_id,),
        ).fetchone()
        if existing is not None:
            raise ValueError("برای این آفر قبلاً معامله ثبت شده است.")
        if trade_time < parse_datetime(str(offer["occurred_at_utc"])):
            raise ValueError("زمان معامله نمی‌تواند قبل از زمان آفر باشد.")
        trade_price = normalize_integer(
            form.get("trade_price", ""), field="قیمت معامله", required=False
        ) or int(offer["price"])
        trade_quantity = normalize_integer(
            form.get("trade_quantity", ""), field="تعداد معامله", required=False
        )
        if (
            offer["quantity"] is not None
            and trade_quantity is not None
            and trade_quantity > int(offer["quantity"])
        ):
            raise ValueError("تعداد معامله نمی‌تواند از تعداد آفر بیشتر باشد.")
        cursor = connection.execute(
            """
            INSERT INTO manual_coin_confirmed_trades(
                offer_id, occurred_at_utc, is_live_at_entry, price, quantity,
                created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                offer_id, iso_utc(trade_time), int(trade_live), trade_price,
                trade_quantity, iso_utc(now),
            ),
        )
        connection.commit()
        return {"offer_id": offer_id, "trade_id": int(cursor.lastrowid)}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def manual_entry_counts(conversation_db: Path) -> dict[str, int]:
    if not conversation_db.is_file():
        return {"offers": 0, "confirmed_trades": 0}
    connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
    try:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"manual_coin_offers", "manual_coin_confirmed_trades"}.issubset(tables):
            return {"offers": 0, "confirmed_trades": 0}
        return {
            "offers": int(connection.execute("SELECT COUNT(*) FROM manual_coin_offers").fetchone()[0]),
            "confirmed_trades": int(connection.execute("SELECT COUNT(*) FROM manual_coin_confirmed_trades").fetchone()[0]),
        }
    finally:
        connection.close()


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "service_status": "STARTING",
            "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
            "settlements": {},
        }

    def set(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._state = value

    def get(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state, ensure_ascii=False))


class GroupLiveInputControl:
    """Persist the operator gate for *new* group events only.

    The collector and conversation database are deliberately untouched.  A
    disabled gate supplies a timestamp boundary to the estimator so rows
    received after that moment stay queued in the database and become
    eligible when the gate is re-enabled.  Historical rows before the
    boundary remain usable for anchors and training.

    ``get()`` reloads from disk when the control file's mtime changes so an
    operator (or automation) can flip the gate by editing the JSON without a
    process restart.  In-process ``set_enabled`` remains the authoritative
    path for the authenticated UI.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._mtime_ns: int | None = None
        self._snapshot = self._load_and_track()

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "enabled": True,
            "disabled_since_utc": None,
            "changed_at_utc": None,
            "changed_by": None,
        }

    def _stat_mtime_ns(self) -> int | None:
        try:
            return self._path.stat().st_mtime_ns
        except OSError:
            return None

    def _load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return self._default()
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("enabled"), bool):
                return self._default()
            result = self._default()
            result.update(value)
            return result
        except (OSError, ValueError, TypeError):
            return self._default()

    def _load_and_track(self) -> dict[str, Any]:
        self._mtime_ns = self._stat_mtime_ns()
        return self._load()

    def get(self) -> dict[str, Any]:
        with self._lock:
            current_mtime = self._stat_mtime_ns()
            if current_mtime != self._mtime_ns:
                self._snapshot = self._load()
                self._mtime_ns = current_mtime
            return json.loads(json.dumps(self._snapshot, ensure_ascii=False))

    def set_enabled(self, enabled: bool, *, changed_by: str) -> dict[str, Any]:
        now = iso_utc(datetime.now(timezone.utc))
        with self._lock:
            # Pick up any external file edit before applying the UI mutation.
            current_mtime = self._stat_mtime_ns()
            if current_mtime != self._mtime_ns:
                self._snapshot = self._load()
                self._mtime_ns = current_mtime
            current = self._snapshot
            next_state = dict(current)
            next_state["schema_version"] = 1
            next_state["enabled"] = bool(enabled)
            next_state["changed_at_utc"] = now
            next_state["changed_by"] = changed_by
            if enabled:
                # Clearing this boundary replays every persisted event that
                # arrived while disconnected on the next estimate refresh.
                next_state["disabled_since_utc"] = None
            elif bool(current.get("enabled", True)) or not current.get(
                "disabled_since_utc"
            ):
                next_state["disabled_since_utc"] = now
            self._snapshot = next_state
            write_json_atomic(self._path, next_state, mode=0o600)
            self._mtime_ns = self._stat_mtime_ns()
            return json.loads(json.dumps(next_state, ensure_ascii=False))


def fa_number(value: Any, *, decimals: int = 0) -> str:
    if value is None:
        return NO_DATA_TOKEN
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))
    rendered = f"{numeric:,.{decimals}f}"
    return rendered.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def fa_datetime(value: str | None) -> str:
    if not value:
        return NO_DATA_TOKEN
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TEHRAN)
        j_dt = jdatetime.datetime.fromgregorian(datetime=parsed)
        formatted = j_dt.strftime("%Y/%m/%d %H:%M:%S")
        return formatted.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    except Exception:
        return NO_DATA_TOKEN


CONFIDENCE_FA = {
    "MEDIUM": "متوسط",
    "LOW": "کم",
    "VERY_LOW": "بسیار کم",
    "NONE": "بدون اطمینان",
    "HIGH_OBSERVED_TRADE": "بالا؛ معامله مشاهده‌شده",
    "MEDIUM_OBSERVED_BOOK": "متوسط؛ محدوده دفتر آفر",
    "MEDIUM_MORNING_REOPEN": "متوسط؛ رژیم بازگشایی صبح",
    "HIGH_FRESH_GROUP_TRANSFER": "بالا؛ انتقال تازه از گروه",
    "LOW_STALE_GROUP_TRANSFER": "پایین؛ انتقال کهنه از گروه",
}

SETTLEMENT_FA = {"CASH": "نقدی", "TOMORROW": "فردایی"}


def render_rate_rows(rates: list[dict[str, Any]]) -> str:
    rows = []
    for rate in rates:
        status = str(rate.get("status"))
        if status == "ESTIMATED":
            project_price = fa_number(rate.get("estimated_project_price"))
            full_price = fa_number(rate.get("estimated_price_toman"))
            bubble = fa_number(float(rate.get("bubble_ratio") or 0) * 100, decimals=2) + "٪"
            tolerance = rate.get("tolerance") or {}
            price_range = (
                f"{fa_number(tolerance.get('lower_project_price'))} تا "
                f"{fa_number(tolerance.get('upper_project_price'))}"
            )
            pressure = fa_number(
                float(rate.get("market_pressure_score") or 0) * 100,
                decimals=1,
            ) + "٪"
        else:
            project_price = NO_DATA_TOKEN
            full_price = NO_DATA_TOKEN
            bubble = NO_DATA_TOKEN
            price_range = NO_DATA_TOKEN
            pressure = NO_DATA_TOKEN
        confidence = CONFIDENCE_FA.get(str(rate.get("confidence")), str(rate.get("confidence")))
        samples = fa_number(rate.get("training_sample_count", 0))
        method = html.escape(str(rate.get("method") or rate.get("reason") or "—"))
        css = "ok" if status == "ESTIMATED" else "missing"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(rate['commodity_name']))}</td>"
            f"<td class='{css}'>{project_price}</td>"
            f"<td class='{css}'>{full_price}</td>"
            f"<td class='{css}'>{price_range}</td>"
            f"<td>{bubble}</td>"
            f"<td>{pressure}</td>"
            f"<td>{html.escape(confidence)}</td>"
            f"<td>{samples}</td>"
            f"<td class='method'>{method}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_combined_rate_rows(settlements: dict[str, dict[str, Any]]) -> str:
    """Pivot estimates: commodities are rows, cash/tomorrow are columns."""
    by_commodity: dict[str, dict[str, dict[str, Any]]] = {}
    ordered_names: list[str] = []
    for settlement in ("CASH", "TOMORROW"):
        for rate in settlements.get(settlement, {}).get("rates", []):
            name = str(rate.get("commodity_name") or "—")
            if name not in by_commodity:
                by_commodity[name] = {}
                ordered_names.append(name)
            by_commodity[name][settlement] = rate

    def cell(rate: dict[str, Any] | None) -> str:
        if not rate or str(rate.get("status")) != "ESTIMATED":
            return f"<span class='missing'>{NO_DATA_TOKEN}</span>"
        tolerance = rate.get("tolerance") or {}
        method = str(rate.get("method") or "")
        reopen_badge = (
            "<small style='color:#a5b4fc'>رژیم بازگشایی</small>"
            if "MORNING_REOPEN" in method
            else ""
        )
        return (
            f"<strong>{fa_number(rate.get('estimated_project_price'))}</strong>"
            f"<small>{fa_number(tolerance.get('lower_project_price'))} تا "
            f"{fa_number(tolerance.get('upper_project_price'))}</small>"
            f"{reopen_badge}"
        )

    return "".join(
        "<tr>"
        f"<td><strong>{html.escape(name)}</strong></td>"
        f"<td class='rate-cell cash'>{cell(by_commodity[name].get('CASH'))}</td>"
        f"<td class='rate-cell tomorrow'>{cell(by_commodity[name].get('TOMORROW'))}</td>"
        "</tr>"
        for name in ordered_names
    ) or f"<tr><td colspan='3' class='missing'>{NO_DATA_TOKEN}</td></tr>"


def read_melted_minute_averages(
    market_db: Path, end_value: str | None,
) -> dict[str, dict[str, Any]]:
    """Read paper/physical 30-second means solely for the dashboard."""
    empty = {form: {"average_price": None, "sample_count": 0} for form in ("PAPER", "PHYSICAL")}
    if not end_value or not market_db.exists():
        return empty
    try:
        end = parse_datetime(end_value)
        start = iso_utc(end - timedelta(seconds=30))
        connection = sqlite3.connect(f"file:{market_db.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        result: dict[str, dict[str, Any]] = {}
        for form in ("PAPER", "PHYSICAL"):
            row = connection.execute(
                """
                SELECT AVG(price_num) AS average_price, COUNT(*) AS sample_count,
                       MAX(event_time_utc) AS last_event_utc
                FROM price_events
                WHERE instrument='MELTED_GOLD' AND trade_form=?
                  AND event_time_utc > ? AND event_time_utc <= ?
                """,
                (form, start, iso_utc(end)),
            ).fetchone()
            result[form] = {
                "average_price": float(row["average_price"]) if row["average_price"] is not None else None,
                "sample_count": int(row["sample_count"] or 0),
                "last_event_utc": row["last_event_utc"],
            }
        connection.close()
        return result
    except (OSError, sqlite3.Error, ValueError):
        return empty


def read_recent_group_activity(conversation_db: Path) -> dict[str, list[dict[str, Any]]]:
    """Return the compact canonical audit feed, separated by source group.

    Reporting rows deliberately include audit-only detections.  Model-facing
    readers still require the realtime/training quality flags, so a delayed or
    pending fact can be visible to the operator without entering an estimate.
    """
    result = {"group_1_offers": [], "group_2_offers": [], "group_1_trades": [], "group_2_trades": []}
    if not conversation_db.exists():
        return result
    try:
        connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        canonical = "canonical_group_projection" in tables
        for group in ("group_1", "group_2"):
            if canonical:
                offer_rows = connection.execute(
                    """
                    SELECT COALESCE(
                               json_extract(m.relevance_json,'$.source_event_time_utc'),
                               m.event_time_utc
                           ) AS event_time_utc,
                           o.commodity,o.side,o.price,o.quantity,o.settlement,
                           COALESCE(q.realtime_eligible,0) AS model_eligible,
                           q.exclusion_reason
                    FROM canonical_group_projection p
                    JOIN offers o ON o.id=p.row_id AND p.event_type='OFFER'
                    JOIN messages m
                      ON m.import_id=o.import_id AND m.message_id=o.message_id
                    LEFT JOIN offer_market_quality q ON q.offer_id=o.id
                    WHERE m.source_html_file=?
                    ORDER BY event_time_utc DESC,o.id DESC LIMIT 10
                    """,
                    (group,),
                ).fetchall()
                trade_rows = connection.execute(
                    """
                    SELECT COALESCE(
                               json_extract(m.relevance_json,'$.source_event_time_utc'),
                               json_extract(t.context_json,'$.source_event_time_utc'),
                               t.event_time_utc
                           ) AS event_time_utc,
                           t.commodity,t.side,t.price,t.quantity,t.settlement,
                           COALESCE(q.realtime_eligible,0) AS model_eligible,
                           q.exclusion_reason
                    FROM canonical_group_projection p
                    JOIN confirmed_trades t
                      ON t.id=p.row_id AND p.event_type='TRADE'
                    JOIN messages m
                      ON m.import_id=t.import_id
                     AND m.message_id=t.confirmation_message_id
                    LEFT JOIN trade_market_quality q ON q.trade_id=t.id
                    WHERE m.source_html_file=?
                    ORDER BY event_time_utc DESC,t.id DESC LIMIT 10
                    """,
                    (group,),
                ).fetchall()
            else:
                offer_rows = connection.execute(
                    """
                    SELECT m.event_time_utc,o.commodity,o.side,o.price,o.quantity,o.settlement,
                           1 AS model_eligible,NULL AS exclusion_reason
                    FROM offers o JOIN messages m
                      ON m.import_id=o.import_id AND m.message_id=o.message_id
                    WHERE m.source_html_file=?
                    ORDER BY m.event_time_utc DESC LIMIT 10
                    """,
                    (group,),
                ).fetchall()
                trade_rows = connection.execute(
                    """
                    SELECT t.event_time_utc,t.commodity,t.side,t.price,t.quantity,t.settlement,
                           1 AS model_eligible,NULL AS exclusion_reason
                    FROM confirmed_trades t JOIN messages m
                      ON m.import_id=t.import_id
                     AND m.message_id=t.confirmation_message_id
                    WHERE m.source_html_file=?
                    ORDER BY t.event_time_utc DESC LIMIT 10
                    """,
                    (group,),
                ).fetchall()
            result[f"{group}_offers"] = [dict(row) for row in offer_rows]
            result[f"{group}_trades"] = [dict(row) for row in trade_rows]
        connection.close()
    except (OSError, sqlite3.Error):
        return result
    return result


def render_live_rows(items: list[dict[str, Any]], *, kind: str) -> str:
    if not items:
        return f"<li class='missing'>{NO_DATA_TOKEN}</li>"
    side = {"BUY": "خرید", "SELL": "فروش"}
    rows = []
    for item in items:
        quantity = f" / {fa_number(item.get('quantity'))} عدد" if item.get("quantity") is not None else ""
        reason = str(item.get("exclusion_reason") or "")
        if bool(item.get("model_eligible")):
            status = "<small class='fact-status model'>ورودی مدل</small>"
        elif reason == "CANONICAL_FACT_ARRIVED_TOO_LATE":
            status = "<small class='fact-status audit'>دیررس؛ فقط گزارش</small>"
        elif reason == "CONDITIONAL_GROUP_FACT":
            status = "<small class='fact-status audit'>شرطی؛ فقط گزارش</small>"
        else:
            status = "<small class='fact-status review'>نیازمند بررسی</small>"
        rows.append(
            "<li>"
            f"<time>{fa_datetime(item.get('event_time_utc'))}</time> "
            f"<strong>{html.escape(str(item.get('commodity') or '—'))}</strong> "
            f"{side.get(str(item.get('side')), '—')} {fa_number(item.get('price'))}{quantity}"
            f" <small>{SETTLEMENT_FA.get(str(item.get('settlement')), '—')}</small>"
            f" {status}"
            "</li>"
        )
    return "".join(rows)


def render_group_activity_fragment(
    conversation_db: Path,
    *,
    input_health: object | None = None,
) -> str:
    activity = read_recent_group_activity(conversation_db)

    def timestamp_with_age(value: object) -> str:
        if not value:
            return "ثبت نشده"
        try:
            observed = parse_datetime(str(value))
        except (TypeError, ValueError):
            return "نامعتبر"
        age_seconds = max(
            0,
            int((datetime.now(timezone.utc) - observed).total_seconds()),
        )
        return (
            f"{fa_datetime(iso_utc(observed))} "
            f"({fa_number(age_seconds // 60)} دقیقه پیش)"
        )

    def runtime_summary() -> str:
        health = input_health if isinstance(input_health, dict) else {}
        collectors = health.get("collectors")
        collectors = collectors if isinstance(collectors, dict) else {}
        projection = collectors.get("coin_group_projection")
        projection = projection if isinstance(projection, dict) else {}
        details = projection.get("details")
        details = details if isinstance(details, dict) else {}
        if not details:
            return ""
        collector_status = str(projection.get("status") or "UNKNOWN").upper()
        collector_text = "سالم" if collector_status == "HEALTHY" else "نیازمند توجه"
        price_outliers = int(details.get("live_book_price_outliers") or 0)
        causal_mismatches = int(details.get("causal_trade_mismatches") or 0)
        cards: list[str] = []
        for group_number in (1, 2):
            prefix = f"group_{group_number}"
            canonical = details.get(f"{prefix}_latest_canonical_event_utc")
            eligible = details.get(f"{prefix}_latest_eligible_event_utc")
            pending = int(details.get(f"{prefix}_pending_review_total") or 0)
            rejected = int(details.get(f"{prefix}_rejected_total") or 0)
            model_active = False
            if eligible:
                try:
                    model_active = (
                        datetime.now(timezone.utc)
                        - parse_datetime(str(eligible))
                    ).total_seconds() <= GROUP_ANCHOR_WINDOW_SECONDS
                except (TypeError, ValueError):
                    model_active = False
            model_text = "ورودی فعال دارد" if model_active else "ورودی فعال ندارد"
            cards.append(
                "<article class='group-runtime-card'>"
                f"<div><strong>گروه {fa_number(group_number)}</strong>"
                f"<span class='activity-freshness {'fresh' if model_active else 'stale'}'>"
                f"مدل {model_text}</span></div>"
                f"<small>وضعیت دریافت: {collector_text}</small>"
                f"<small>جدیدترین رویداد ثبت‌شده: {timestamp_with_age(canonical)}</small>"
                f"<small>جدیدترین ورودی پذیرفته‌شدهٔ canonical: "
                f"{timestamp_with_age(eligible)}</small>"
                f"<small>در انتظار بررسی: {fa_number(pending)} · "
                f"رد/نادیده: {fa_number(rejected)}</small>"
                "</article>"
            )
        return (
            "<div class='group-runtime-summary'>"
            "<h3>وضعیت واقعی دریافت و مصرف مدل</h3>"
            "<p>رویداد ثبت‌شده با داده‌ای که کنترل کیفیت پذیرفته و مدل مصرف می‌کند یکسان نیست.</p>"
            "<p class='group-safety-gates'>گیت‌های ایمنی مدل: "
            f"ناهمخوانی معامله با آفر ریشه {fa_number(causal_mismatches)} · "
            f"پرت قیمتی نسبت به آفرهای زنده {fa_number(price_outliers)}</p>"
            f"<div class='group-runtime-grid'>{''.join(cards)}</div>"
            "</div>"
        )

    def freshness(group: str, kind: str) -> str:
        plural = "offers" if kind == "offer" else "trades"
        kind_fa = "آفر" if kind == "offer" else "معامله"
        rows = list(activity.get(f"{group}_{plural}", []))
        observed: list[datetime] = []
        model_observed: list[datetime] = []
        for row in rows:
            try:
                parsed_time = parse_datetime(str(row.get("event_time_utc") or ""))
                observed.append(parsed_time)
                if bool(row.get("model_eligible")):
                    model_observed.append(parsed_time)
            except (TypeError, ValueError):
                continue
        if not observed:
            return (
                "<small class='activity-freshness stale'>"
                f"هیچ {kind_fa} تشخیص‌داده‌شده‌ای ثبت نشده</small>"
            )
        latest = max(observed)
        age_seconds = max(
            0,
            int((datetime.now(timezone.utc) - latest).total_seconds()),
        )
        latest_model = max(model_observed) if model_observed else None
        model_age = (
            max(0, int((datetime.now(timezone.utc) - latest_model).total_seconds()))
            if latest_model is not None
            else None
        )
        fresh = model_age is not None and model_age <= GROUP_ANCHOR_WINDOW_SECONDS
        if latest_model is None:
            model_status = f"بدون {kind_fa} فعال برای مدل؛ هیچ ردیف مدل‌پذیری در این فهرست نیست"
        elif fresh:
            model_status = (
                f"{kind_fa} فعال برای مدل"
                if latest_model == latest
                else f"{kind_fa} فعال برای مدل · آخرین ورودی مدل: {fa_datetime(iso_utc(latest_model))}"
            )
        else:
            model_status = (
                f"بدون {kind_fa} فعال برای مدل"
                if latest_model == latest
                else f"بدون {kind_fa} فعال برای مدل · آخرین ورودی مدل: {fa_datetime(iso_utc(latest_model))}"
            )
        return (
            f"<small class='activity-freshness {'fresh' if fresh else 'stale'}'>"
            f"آخرین {kind_fa} تشخیص‌داده‌شده: {fa_datetime(iso_utc(latest))} "
            f"({fa_number(age_seconds // 60)} دقیقه پیش) · {model_status}</small>"
        )
    return f"""<section><div class="section-head"><h2>آخرین آفرها و معاملات تشخیص‌داده‌شده</h2><span class="badge">زنده و تفکیک‌شده</span></div>
      <p class="activity-scope-note">این فهرست تشخیص‌های canonical را نشان می‌دهد؛ برچسب هر ردیف مشخص می‌کند داده وارد مدل شده یا فقط برای گزارش/بررسی نگه داشته شده است. پیام‌های غیرآفر و غیرمعامله در این فهرست نیستند.</p>
      {runtime_summary()}
      <div class="group-grid">
        <article class="feed-card"><h3>۱۰ آفر آخر — گروه ۱</h3>{freshness('group_1', 'offer')}<ul>{render_live_rows(activity.get('group_1_offers', []), kind='offer')}</ul></article>
        <article class="feed-card"><h3>۱۰ آفر آخر — گروه ۲</h3>{freshness('group_2', 'offer')}<ul>{render_live_rows(activity.get('group_2_offers', []), kind='offer')}</ul></article>
        <article class="feed-card"><h3>۱۰ معاملهٔ آخر — گروه ۱</h3>{freshness('group_1', 'trade')}<ul>{render_live_rows(activity.get('group_1_trades', []), kind='trade')}</ul></article>
        <article class="feed-card"><h3>۱۰ معاملهٔ آخر — گروه ۲</h3>{freshness('group_2', 'trade')}<ul>{render_live_rows(activity.get('group_2_trades', []), kind='trade')}</ul></article>
      </div>
    </section>"""


def find_analytics_db(conversation_db: Path) -> Path | None:
    if conversation_db.exists():
        try:
            connection = sqlite3.connect(
                f"file:{conversation_db.resolve()}?mode=ro", uri=True
            )
            canonical = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='canonical_group_projection'
                """
            ).fetchone()
            connection.close()
            if canonical is not None:
                return conversation_db
        except sqlite3.Error:
            pass
    analytics_root = Path(
        os.environ.get(
            "COIN_RATE_ESTIMATOR_ANALYTICS_DIR",
            str(RUNTIME_ROOT / "analytics"),
        )
    ).expanduser()
    snapshots = sorted(
        glob.glob(str(analytics_root / "training-snapshots" / "group-training-*.sqlite3"))
    )
    if snapshots:
        return Path(snapshots[-1])
    shadow = analytics_root / "group_training_dataset_shadow.sqlite3"
    if shadow.exists():
        return shadow
    if conversation_db.exists():
        return conversation_db
    return None


def parse_shamsi_to_utc_iso(shamsi_str: str, *, is_end: bool = False) -> str | None:
    if not shamsi_str:
        return None
    cleaned = shamsi_str.replace("/", "-").strip()
    parts = [int(p) for p in cleaned.split("-") if p.isdigit()]
    if len(parts) != 3:
        return None
    try:
        jdate = jdatetime.date(parts[0], parts[1], parts[2])
        gdate = jdate.togregorian()
        time_part = dt_time(23, 59, 59, 999999) if is_end else dt_time(0, 0, 0)
        dt = datetime.combine(gdate, time_part).replace(tzinfo=TEHRAN)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def calculate_time_bounds(range_type: str, start_shamsi: str | None = None, end_shamsi: str | None = None) -> tuple[str, str, str]:
    tehran_now = datetime.now(TEHRAN)
    if range_type == "7d":
        start_dt = (tehran_now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = tehran_now
        label = "۷ روز اخیر"
    elif range_type == "30d":
        start_dt = (tehran_now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = tehran_now
        label = "۳۰ روز اخیر (۱ ماهه)"
    elif range_type == "custom" and (start_shamsi or end_shamsi):
        iso_start = parse_shamsi_to_utc_iso(start_shamsi, is_end=False) if start_shamsi else None
        iso_end = parse_shamsi_to_utc_iso(end_shamsi, is_end=True) if end_shamsi else None
        start_str = iso_start or "1970-01-01T00:00:00+00:00"
        end_str = iso_end or tehran_now.astimezone(timezone.utc).isoformat()
        label = f"بازه شمسی ({start_shamsi or 'ابتدا'} تا {end_shamsi or 'اکنون'})"
        return start_str, end_str, label
    else:
        start_dt = tehran_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = tehran_now.replace(hour=23, minute=59, second=59, microsecond=999999)
        label = "امروز (روز جاری)"

    start_str = start_dt.astimezone(timezone.utc).isoformat()
    end_str = end_dt.astimezone(timezone.utc).isoformat()
    return start_str, end_str, label


def query_user_analytics(
    conversation_db: Path,
    *,
    range_type: str = "today",
    start_shamsi: str | None = None,
    end_shamsi: str | None = None,
) -> dict[str, Any]:
    db_path = find_analytics_db(conversation_db)
    start_utc, end_utc, label = calculate_time_bounds(range_type, start_shamsi, end_shamsi)

    def empty_group() -> dict[str, Any]:
        return {
            "summary": {
                "total_offer_count": 0,
                "total_offer_qty": 0,
                "total_trade_count": 0,
                "total_trade_qty": 0,
            },
            "top_offer_count": [],
            "top_trade_count": [],
            "top_offer_qty": [],
            "top_trade_qty": [],
        }

    empty_result = {
        "range_type": range_type,
        "range_label": label,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "start_shamsi": start_shamsi or "",
        "end_shamsi": end_shamsi or "",
        "data_source": "NO_DATA",
        "identity_analytics_available": False,
        "groups": {1: empty_group(), 2: empty_group()},
    }
    if not db_path or not db_path.exists():
        return empty_result

    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "canonical_group_projection" in tables:
            groups_data: dict[int, dict[str, Any]] = {}
            for group in (1, 2):
                source_file = f"group_{group}"
                offer_row = conn.execute(
                    """
                    SELECT COUNT(*) AS c,
                           SUM(COALESCE(o.quantity,1)) AS q,
                           SUM(CASE WHEN COALESCE(mq.realtime_eligible,0)=1 THEN 1 ELSE 0 END) AS model_c,
                           SUM(CASE WHEN COALESCE(mq.realtime_eligible,0)=0 THEN 1 ELSE 0 END) AS audit_c
                    FROM canonical_group_projection p
                    JOIN offers o ON o.id=p.row_id AND p.event_type='OFFER'
                    JOIN messages m
                      ON m.import_id=o.import_id AND m.message_id=o.message_id
                    LEFT JOIN offer_market_quality mq ON mq.offer_id=o.id
                    WHERE m.source_html_file=?
                      AND COALESCE(
                            json_extract(m.relevance_json,'$.source_event_time_utc'),
                            m.event_time_utc
                          ) >= ?
                      AND COALESCE(
                            json_extract(m.relevance_json,'$.source_event_time_utc'),
                            m.event_time_utc
                          ) <= ?
                    """,
                    (source_file, start_utc, end_utc),
                ).fetchone()
                trade_row = conn.execute(
                    """
                    SELECT COUNT(*) AS c,
                           SUM(COALESCE(t.quantity,1)) AS q,
                           SUM(CASE WHEN COALESCE(mq.realtime_eligible,0)=1 THEN 1 ELSE 0 END) AS model_c,
                           SUM(CASE WHEN COALESCE(mq.realtime_eligible,0)=0 THEN 1 ELSE 0 END) AS audit_c
                    FROM canonical_group_projection p
                    JOIN confirmed_trades t
                      ON t.id=p.row_id AND p.event_type='TRADE'
                    JOIN messages m
                      ON m.import_id=t.import_id
                     AND m.message_id=t.confirmation_message_id
                    LEFT JOIN trade_market_quality mq ON mq.trade_id=t.id
                    WHERE m.source_html_file=?
                      AND COALESCE(
                            json_extract(m.relevance_json,'$.source_event_time_utc'),
                            json_extract(t.context_json,'$.source_event_time_utc'),
                            t.event_time_utc
                          ) >= ?
                      AND COALESCE(
                            json_extract(m.relevance_json,'$.source_event_time_utc'),
                            json_extract(t.context_json,'$.source_event_time_utc'),
                            t.event_time_utc
                          ) <= ?
                    """,
                    (source_file, start_utc, end_utc),
                ).fetchone()
                groups_data[group] = {
                    "summary": {
                        "total_offer_count": int(offer_row["c"] or 0),
                        "total_offer_qty": int(offer_row["q"] or 0),
                        "total_trade_count": int(trade_row["c"] or 0),
                        "total_trade_qty": int(trade_row["q"] or 0),
                        "model_eligible_offer_count": int(offer_row["model_c"] or 0),
                        "audit_only_offer_count": int(offer_row["audit_c"] or 0),
                        "model_eligible_trade_count": int(trade_row["model_c"] or 0),
                        "audit_only_trade_count": int(trade_row["audit_c"] or 0),
                    },
                    "top_offer_count": [],
                    "top_trade_count": [],
                    "top_offer_qty": [],
                    "top_trade_qty": [],
                }
            conn.close()
            return {
                "range_type": range_type,
                "range_label": label,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "start_shamsi": start_shamsi or "",
                "end_shamsi": end_shamsi or "",
                "data_source": "CANONICAL_LIVE_AUDIT",
                "identity_analytics_available": False,
                "groups": groups_data,
            }
        if "offer_training_examples" not in tables or "confirmed_trade_training_examples" not in tables:
            conn.close()
            return empty_result

        groups_data = {}
        for group in (1, 2):
            top_offer_count = [
                dict(r) for r in conn.execute(
                    """
                    SELECT offerer_name AS username, COUNT(*) AS count
                    FROM offer_training_examples
                    WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                    GROUP BY offerer_name ORDER BY count DESC LIMIT 10
                    """,
                    (group, start_utc, end_utc),
                ).fetchall()
            ]

            top_trade_count = [
                dict(r) for r in conn.execute(
                    """
                    WITH user_trades AS (
                        SELECT offerer_name AS username FROM confirmed_trade_training_examples WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                        UNION ALL
                        SELECT counterparty_name AS username FROM confirmed_trade_training_examples WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ? AND counterparty_name IS NOT NULL AND counterparty_name != ''
                    )
                    SELECT username, COUNT(*) AS count FROM user_trades GROUP BY username ORDER BY count DESC LIMIT 10
                    """,
                    (group, start_utc, end_utc, group, start_utc, end_utc),
                ).fetchall()
            ]

            top_offer_qty = [
                dict(r) for r in conn.execute(
                    """
                    SELECT offerer_name AS username, SUM(COALESCE(quantity, 1)) AS total_qty
                    FROM offer_training_examples
                    WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                    GROUP BY offerer_name ORDER BY total_qty DESC LIMIT 10
                    """,
                    (group, start_utc, end_utc),
                ).fetchall()
            ]

            top_trade_qty = [
                dict(r) for r in conn.execute(
                    """
                    WITH user_trade_qty AS (
                        SELECT offerer_name AS username, COALESCE(quantity, 1) AS qty FROM confirmed_trade_training_examples WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                        UNION ALL
                        SELECT counterparty_name AS username, COALESCE(quantity, 1) AS qty FROM confirmed_trade_training_examples WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ? AND counterparty_name IS NOT NULL AND counterparty_name != ''
                    )
                    SELECT username, SUM(qty) AS total_qty FROM user_trade_qty GROUP BY username ORDER BY total_qty DESC LIMIT 10
                    """,
                    (group, start_utc, end_utc, group, start_utc, end_utc),
                ).fetchall()
            ]

            total_offer_row = conn.execute(
                """
                SELECT COUNT(*) AS c, SUM(COALESCE(quantity, 1)) AS q
                FROM offer_training_examples
                WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                """,
                (group, start_utc, end_utc),
            ).fetchone()

            total_trade_row = conn.execute(
                """
                SELECT COUNT(*) AS c, SUM(COALESCE(quantity, 1)) AS q
                FROM confirmed_trade_training_examples
                WHERE group_number = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                """,
                (group, start_utc, end_utc),
            ).fetchone()

            summary = {
                "total_offer_count": total_offer_row["c"] if total_offer_row else 0,
                "total_offer_qty": total_offer_row["q"] if (total_offer_row and total_offer_row["q"]) else 0,
                "total_trade_count": total_trade_row["c"] if total_trade_row else 0,
                "total_trade_qty": total_trade_row["q"] if (total_trade_row and total_trade_row["q"]) else 0,
            }

            groups_data[group] = {
                "summary": summary,
                "top_offer_count": top_offer_count,
                "top_trade_count": top_trade_count,
                "top_offer_qty": top_offer_qty,
                "top_trade_qty": top_trade_qty,
            }

        conn.close()
        return {
            "range_type": range_type,
            "range_label": label,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "start_shamsi": start_shamsi or "",
            "end_shamsi": end_shamsi or "",
            "data_source": "LEGACY_TRAINING_SNAPSHOT",
            "identity_analytics_available": True,
            "groups": groups_data,
        }
    except Exception:
        return empty_result


_EVENT_AUDIT_MODELS = (
    "MAIN_ONLINE",
    "SHADOW1_PREVIOUS",
    "SHADOW2_MORNING_REOPEN",
    "SHADOW3_ML_RESIDUAL",
)
_EVENT_AUDIT_MODEL_LABELS = {
    "MAIN_ONLINE": "مدل اصلی زنده",
    "SHADOW1_PREVIOUS": "Shadow قبلی",
    "SHADOW2_MORNING_REOPEN": "Shadow بازگشایی",
    "SHADOW3_ML_RESIDUAL": "Shadow یادگیری ماشین",
}
_FEEDBACK_COMMODITY_LABELS = {
    "UNRESOLVED": "نامشخص / نامرتبط",
    "IMAM": "امام",
    "BAHAR": "بهار آزادی",
    "QUARTER_BAHAR": "ربع بهار",
    "HALF_BAHAR": "نیم بهار",
    "QUARTER_LOW_DATE": "ربع تاریخ پایین",
    "HALF_LOW_DATE": "نیم تاریخ پایین",
    "ONE_GRAM": "یک گرمی مرکزی",
}
_FEEDBACK_LABEL_TO_COMMODITY = {
    label: code for code, label in _FEEDBACK_COMMODITY_LABELS.items()
}
_FEEDBACK_SETTLEMENT_LABELS = {
    "CASH": "نقدی",
    "TODAY": "امروزی کاغذی",
    "TOMORROW": "فردایی",
}
_PARSER_FEEDBACK_ERROR_FA = {
    "parser_feedback_event_not_found": "این رویداد دیگر در دفتر canonical موجود نیست.",
    "parser_feedback_ambiguous_fields_invalid": "حداقل یک فیلد مبهم معتبر انتخاب کنید.",
    "parser_feedback_boolean_field_invalid": "وضعیت رویداد یا شرطی‌بودن نامعتبر است.",
    "parser_feedback_numeric_field_invalid": "فی یا تعداد باید عدد معتبر باشد.",
    "parser_feedback_full_toman_price_invalid": "فی کامل باید به تومان و مضرب ۱۰۰۰ باشد.",
    "parser_feedback_commodity_invalid": "برای رویداد معتبر یک کالای مشخص انتخاب کنید.",
    "parser_feedback_side_invalid": "سمت خرید/فروش نامعتبر است.",
    "parser_feedback_settlement_invalid": "نوع تسویه نامعتبر است.",
    "parser_feedback_trade_form_invalid": "نوع بازار نامعتبر است.",
    "parser_feedback_price_outside_commodity_band": "فی خارج از بازهٔ معتبر کالای انتخابی است.",
    "parser_feedback_price_invalid": "فی رویداد نامعتبر است.",
    "parser_feedback_quantity_invalid": "تعداد باید بین ۱ تا ۱۰۰ باشد.",
}


def _parser_feedback_error_message(error: BaseException) -> str:
    return _PARSER_FEEDBACK_ERROR_FA.get(
        str(error), "بازخورد با قرارداد parser سازگار نیست."
    )


def _load_private_group_text_by_event(
    staging_db: Path | None,
    event_ids: set[bytes],
) -> dict[bytes, dict[str, str]]:
    """Resolve current raw text from bounded private staging, without copying it.

    Event keys are reconstructed at this private boundary.  No Telegram message
    identifier, sender identity, or text crosses into Market Store or feedback
    storage; returned text exists only for the authenticated response render.
    """

    if staging_db is None or not event_ids:
        return {}
    try:
        database = staging_db.expanduser().resolve()
        database.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    except (OSError, RuntimeError):
        return {}
    else:
        return {}
    if not database.is_file():
        return {}

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='coin_group_staged_messages'"
        ).fetchone() is None:
            return {}
        now = iso_utc(datetime.now(timezone.utc))
        rows = connection.execute(
            """
            SELECT group_number,message_id,event_time_utc,available_at_utc,
                   message_text,reply_to_message_id
            FROM coin_group_staged_messages
            WHERE available_at_utc<=? AND expires_at_utc>?
            ORDER BY group_number,event_time_utc,message_id
            """,
            (now, now),
        ).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return {}
    finally:
        if connection is not None:
            connection.close()

    by_message = {
        (int(row["group_number"]), int(row["message_id"])): row for row in rows
    }
    resolved: dict[bytes, dict[str, str]] = {}
    for row in rows:
        group_number = int(row["group_number"])
        message_id = int(row["message_id"])
        raw_text = str(row["message_text"] or "")
        source = CoinGroupMessageInput(
            group_number=group_number,
            source_event_id=message_id,
            published_at_utc=str(row["event_time_utc"]),
            available_at_utc=str(row["available_at_utc"]),
            text=raw_text,
        )
        try:
            offer_count = len(parse_coin_group_offers(source))
        except (TypeError, ValueError):
            offer_count = 0
        for offer_index in range(offer_count):
            event_key = derive_event_key(
                "coin-group-offer-v1", group_number, message_id, offer_index
            )
            if event_key in event_ids:
                resolved[event_key] = {
                    "raw_offer_text": raw_text,
                    "raw_event_text": raw_text,
                }

        reply_id = row["reply_to_message_id"]
        if reply_id is None:
            continue
        root = row
        seen = {message_id}
        while root["reply_to_message_id"] is not None:
            parent_id = int(root["reply_to_message_id"])
            if parent_id in seen:
                break
            parent = by_message.get((group_number, parent_id))
            if parent is None:
                break
            seen.add(parent_id)
            root = parent
        root_message_id = int(root["message_id"])
        if root_message_id == message_id:
            continue
        event_key = derive_event_key(
            "coin-group-trade-v1", group_number, root_message_id, message_id
        )
        if event_key in event_ids:
            resolved[event_key] = {
                "raw_offer_text": str(root["message_text"] or ""),
                "raw_event_text": raw_text,
            }
    return resolved


def query_model_event_audit(
    conversation_db: Path,
    calibration_db: Path,
    *,
    feedback_db: Path = DEFAULT_REVIEW_DECISIONS_DB,
    coin_group_staging_db: Path | None = None,
    range_type: str = "today",
    start_shamsi: str | None = None,
    end_shamsi: str | None = None,
) -> dict[str, Any]:
    """Join every canonical detection to the first model cycle that saw it.

    The join is intentionally one-way: only rows whose quality contract says
    ``realtime_eligible=1`` receive model prices.  Audit-only rows stay visible
    with their exclusion reason, but can never masquerade as model input.
    """

    start_utc, end_utc, label = calculate_time_bounds(
        range_type, start_shamsi, end_shamsi
    )
    empty = {
        "range_label": label,
        "generated_at_utc": iso_utc(datetime.now(timezone.utc)),
        "total_events": 0,
        "model_input_events": 0,
        "audit_only_events": 0,
        "missing_main_prediction_events": 0,
        "events": [],
        "model_labels": dict(_EVENT_AUDIT_MODEL_LABELS),
    }
    if not conversation_db.is_file():
        return empty
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{conversation_db.resolve()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "canonical_group_projection" not in tables:
            return empty
        offer_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(offers)")
        }
        trade_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(confirmed_trades)")
        }
        offer_trade_form = (
            "o.trade_form" if "trade_form" in offer_columns else "'PHYSICAL'"
        )
        trade_trade_form = (
            "t.trade_form" if "trade_form" in trade_columns else "'PHYSICAL'"
        )
        rows = connection.execute(
            f"""
            SELECT lower(hex(p.event_key)) AS event_id,
                   m.source_html_file AS source_group,'OFFER' AS event_type,
                   COALESCE(
                     json_extract(m.relevance_json,'$.source_event_time_utc'),
                     m.event_time_utc
                   ) AS source_event_time_utc,
                   m.event_time_utc AS available_at_utc,
                   o.commodity,o.side,o.price,o.quantity,
                   o.settlement AS model_settlement,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_settlement_term'),
                     o.settlement
                   ) AS canonical_settlement,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_trade_form'),
                     CASE WHEN {offer_trade_form}='PAPER' THEN 'PAPER_NORMAL' ELSE {offer_trade_form} END
                   ) AS trade_form,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_is_conditional'),0
                   ) AS is_conditional,
                   json_extract(
                     m.relevance_json,'$.canonical_resolution_reason'
                   ) AS resolution_reason,
                   COALESCE(q.realtime_eligible,0) AS model_eligible,
                   q.exclusion_reason
            FROM canonical_group_projection p
            JOIN offers o ON o.id=p.row_id AND p.event_type='OFFER'
            JOIN messages m
              ON m.import_id=o.import_id AND m.message_id=o.message_id
            LEFT JOIN offer_market_quality q ON q.offer_id=o.id
            WHERE COALESCE(
                    json_extract(m.relevance_json,'$.source_event_time_utc'),
                    m.event_time_utc
                  ) >= ?
              AND COALESCE(
                    json_extract(m.relevance_json,'$.source_event_time_utc'),
                    m.event_time_utc
                  ) <= ?
            UNION ALL
            SELECT lower(hex(p.event_key)) AS event_id,
                   m.source_html_file AS source_group,'TRADE' AS event_type,
                   COALESCE(
                     json_extract(m.relevance_json,'$.source_event_time_utc'),
                     json_extract(t.context_json,'$.source_event_time_utc'),
                     t.event_time_utc
                   ) AS source_event_time_utc,
                   t.event_time_utc AS available_at_utc,
                   t.commodity,t.side,t.price,t.quantity,
                   t.settlement AS model_settlement,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_settlement_term'),
                     t.settlement
                   ) AS canonical_settlement,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_trade_form'),
                     CASE WHEN {trade_trade_form}='PAPER' THEN 'PAPER_NORMAL' ELSE {trade_trade_form} END
                   ) AS trade_form,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_is_conditional'),0
                   ) AS is_conditional,
                   json_extract(
                     m.relevance_json,'$.canonical_resolution_reason'
                   ) AS resolution_reason,
                   COALESCE(q.realtime_eligible,0) AS model_eligible,
                   q.exclusion_reason
            FROM canonical_group_projection p
            JOIN confirmed_trades t
              ON t.id=p.row_id AND p.event_type='TRADE'
            JOIN messages m
              ON m.import_id=t.import_id
             AND m.message_id=t.confirmation_message_id
            LEFT JOIN trade_market_quality q ON q.trade_id=t.id
            WHERE COALESCE(
                    json_extract(m.relevance_json,'$.source_event_time_utc'),
                    json_extract(t.context_json,'$.source_event_time_utc'),
                    t.event_time_utc
                  ) >= ?
              AND COALESCE(
                    json_extract(m.relevance_json,'$.source_event_time_utc'),
                    json_extract(t.context_json,'$.source_event_time_utc'),
                    t.event_time_utc
                  ) <= ?
            ORDER BY source_event_time_utc DESC,event_type
            """,
            (start_utc, end_utc, start_utc, end_utc),
        ).fetchall()
    except (OSError, sqlite3.Error, ValueError):
        return empty
    finally:
        if connection is not None:
            connection.close()

    calibration: sqlite3.Connection | None = None
    if rows and calibration_db.is_file():
        try:
            calibration = sqlite3.connect(
                f"file:{calibration_db.resolve()}?mode=ro", uri=True
            )
            calibration.row_factory = sqlite3.Row
            calibration.execute(
                "SELECT 1 FROM coin_estimate_predictions LIMIT 1"
            ).fetchone()
        except (OSError, sqlite3.Error):
            if calibration is not None:
                calibration.close()
            calibration = None

    def first_prediction(
        *, commodity: str, settlement: str, model_id: str, available_at: datetime
    ) -> dict[str, Any] | None:
        if calibration is None:
            return None
        try:
            row = calibration.execute(
                """
                SELECT prediction_time_utc,model_version,
                       estimated_price_toman,lower_price_toman,upper_price_toman
                FROM coin_estimate_predictions
                WHERE model_id=? AND commodity=? AND settlement=?
                  AND prediction_time_utc>=?
                ORDER BY prediction_time_utc,id LIMIT 1
                """,
                (model_id, commodity, settlement, iso_utc(available_at)),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        candidate = {
            "prediction_time_utc": str(row["prediction_time_utc"]),
            "estimated_price_toman": int(round(float(row["estimated_price_toman"]))),
            "lower_price_toman": (
                int(round(float(row["lower_price_toman"])))
                if row["lower_price_toman"] is not None
                else None
            ),
            "upper_price_toman": (
                int(round(float(row["upper_price_toman"])))
                if row["upper_price_toman"] is not None
                else None
            ),
            "model_version": str(row["model_version"] or ""),
        }
        delay = (
            parse_datetime(candidate["prediction_time_utc"]) - available_at
        ).total_seconds()
        maximum_delay = 90 if model_id == "MAIN_ONLINE" else 180
        return candidate if 0 <= delay <= maximum_delay else None

    parser_feedback = load_coin_group_parser_feedback(feedback_db)
    private_text = _load_private_group_text_by_event(
        coin_group_staging_db,
        {
            bytes.fromhex(str(row["event_id"]))
            for row in rows
            if re.fullmatch(r"[0-9a-fA-F]{32,128}", str(row["event_id"]))
        },
    )
    events: list[dict[str, Any]] = []
    missing_main = 0
    model_input_count = 0
    for row in rows:
        event_id = str(row["event_id"])
        try:
            review = parser_feedback.get(bytes.fromhex(event_id))
        except ValueError:
            review = None
        raw_text = private_text.get(bytes.fromhex(event_id), {})
        model_eligible = bool(row["model_eligible"])
        model_input_count += int(model_eligible)
        available_at = parse_datetime(str(row["available_at_utc"]))
        commodity = str(row["commodity"] or "نامشخص")
        model_settlement = str(row["model_settlement"] or "")
        canonical_settlement = str(row["canonical_settlement"] or "")
        estimates = {
            model_id: first_prediction(
                commodity=commodity,
                settlement=model_settlement,
                model_id=model_id,
                available_at=available_at,
            )
            for model_id in _EVENT_AUDIT_MODELS
        } if model_eligible else {}
        if model_eligible and estimates.get("MAIN_ONLINE") is None:
            missing_main += 1
        reason = str(row["exclusion_reason"] or "")
        if model_eligible:
            status = "MODEL_INPUT"
        elif reason == "CANONICAL_FACT_ARRIVED_TOO_LATE":
            status = "AUDIT_ONLY_LATE"
        elif reason == "CONDITIONAL_GROUP_FACT":
            status = "AUDIT_ONLY_CONDITIONAL"
        else:
            status = "PENDING_REVIEW"
        events.append(
            {
                "group_number": int(str(row["source_group"]).rsplit("_", 1)[-1]),
                "event_id": event_id,
                "event_type": str(row["event_type"]),
                "source_event_time_utc": str(row["source_event_time_utc"]),
                "available_at_utc": str(row["available_at_utc"]),
                "commodity": commodity,
                "side": str(row["side"]),
                "price_toman": int(row["price"]) * 1_000,
                "quantity": int(row["quantity"]) if row["quantity"] is not None else None,
                "settlement": canonical_settlement,
                "model_settlement": model_settlement,
                "trade_form": str(row["trade_form"] or "UNKNOWN"),
                "is_conditional": bool(row["is_conditional"]),
                "resolution_reason": (
                    str(row["resolution_reason"])
                    if row["resolution_reason"] is not None
                    else None
                ),
                "raw_offer_text": raw_text.get("raw_offer_text"),
                "raw_event_text": raw_text.get("raw_event_text"),
                "status": status,
                "exclusion_reason": reason or None,
                "estimates": estimates,
                "parser_feedback": (
                    {
                        "ambiguous_fields": sorted(review.ambiguous_fields),
                        "event_confirmed": review.event_confirmed,
                        "review_revision": review.review_revision,
                        "reviewed_at_utc": review.reviewed_at_utc,
                        "applied": review.applied_revision >= review.review_revision,
                        "applied_at_utc": review.applied_at_utc,
                        "commodity_code": review.commodity_code,
                        "side": review.side,
                        "price_toman": review.price_project_thousand_toman * 1_000,
                        "quantity": review.quantity,
                        "settlement_term": review.settlement_term,
                        "trade_form": review.trade_form,
                        "is_conditional": review.is_conditional,
                    }
                    if review is not None
                    else None
                ),
            }
        )
    if calibration is not None:
        calibration.close()
    return {
        **empty,
        "total_events": len(events),
        "model_input_events": model_input_count,
        "audit_only_events": len(events) - model_input_count,
        "missing_main_prediction_events": missing_main,
        "events": events,
    }


def _canonical_event_for_feedback(
    conversation_db: Path,
    event_id: str,
) -> dict[str, Any] | None:
    try:
        event_key = bytes.fromhex(str(event_id or "").strip())
    except ValueError:
        return None
    if not 16 <= len(event_key) <= 64 or not conversation_db.is_file():
        return None
    connection = sqlite3.connect(
        f"file:{conversation_db.resolve()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        offer_columns = {
            str(item[1]) for item in connection.execute("PRAGMA table_info(offers)")
        }
        trade_columns = {
            str(item[1])
            for item in connection.execute("PRAGMA table_info(confirmed_trades)")
        }
        offer_trade_form = (
            "o.trade_form" if "trade_form" in offer_columns else "'PHYSICAL'"
        )
        trade_trade_form = (
            "t.trade_form" if "trade_form" in trade_columns else "'PHYSICAL'"
        )
        row = connection.execute(
            f"""
            SELECT p.event_key,p.event_type,m.source_html_file,
                   COALESCE(
                     json_extract(m.relevance_json,'$.source_event_time_utc'),
                     m.event_time_utc
                   ) AS source_event_time_utc,
                   CASE WHEN p.event_type='OFFER' THEN o.commodity ELSE t.commodity END AS commodity,
                   CASE WHEN p.event_type='OFFER' THEN o.side ELSE t.side END AS side,
                   CASE WHEN p.event_type='OFFER' THEN o.price ELSE t.price END AS price,
                   CASE WHEN p.event_type='OFFER' THEN o.quantity ELSE t.quantity END AS quantity,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_settlement_term'),
                     CASE WHEN p.event_type='OFFER' THEN o.settlement ELSE t.settlement END
                   ) AS settlement,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_trade_form'),
                     CASE
                       WHEN p.event_type='OFFER' AND {offer_trade_form}='PAPER' THEN 'PAPER_NORMAL'
                       WHEN p.event_type='TRADE' AND {trade_trade_form}='PAPER' THEN 'PAPER_NORMAL'
                       WHEN p.event_type='OFFER' THEN {offer_trade_form}
                       ELSE {trade_trade_form}
                     END
                   ) AS trade_form,
                   COALESCE(
                     json_extract(m.relevance_json,'$.canonical_is_conditional'),0
                   ) AS is_conditional
            FROM canonical_group_projection p
            LEFT JOIN offers o ON p.event_type='OFFER' AND o.id=p.row_id
            LEFT JOIN confirmed_trades t ON p.event_type='TRADE' AND t.id=p.row_id
            JOIN messages m ON m.import_id=COALESCE(o.import_id,t.import_id)
             AND m.message_id=COALESCE(o.message_id,t.confirmation_message_id)
            WHERE p.event_key=?
            """,
            (event_key,),
        ).fetchone()
        if row is None:
            return None
        source_group = str(row["source_html_file"] or "")
        try:
            group_number = int(source_group.rsplit("_", 1)[-1])
        except ValueError:
            return None
        return {
            "event_key": bytes(row["event_key"]),
            "event_type": str(row["event_type"]),
            "group_number": group_number,
            "source_event_time_utc": str(row["source_event_time_utc"]),
            "commodity": str(row["commodity"] or "نامشخص"),
            "side": str(row["side"] or ""),
            "price": int(row["price"]),
            "quantity": int(row["quantity"] or 1),
            "settlement": str(row["settlement"] or ""),
            "trade_form": str(row["trade_form"] or ""),
            "is_conditional": bool(row["is_conditional"]),
        }
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        connection.close()


def submit_coin_group_parser_feedback(
    conversation_db: Path,
    feedback_db: Path,
    payload: dict[str, Any],
    *,
    reviewer: str,
) -> dict[str, Any]:
    """Validate a complete operator correction against the canonical event."""

    event = _canonical_event_for_feedback(
        conversation_db, str(payload.get("event_id") or "")
    )
    if event is None:
        raise CoinGroupFeedbackError("parser_feedback_event_not_found")
    fields = payload.get("ambiguous_fields")
    if not isinstance(fields, list):
        raise CoinGroupFeedbackError("parser_feedback_ambiguous_fields_invalid")
    normalized_fields = [str(item).strip() for item in fields]
    if not set(normalized_fields).issubset(COIN_GROUP_AMBIGUOUS_FIELDS):
        raise CoinGroupFeedbackError("parser_feedback_ambiguous_fields_invalid")
    event_confirmed = payload.get("event_confirmed")
    is_conditional = payload.get("is_conditional")
    if not isinstance(event_confirmed, bool) or not isinstance(is_conditional, bool):
        raise CoinGroupFeedbackError("parser_feedback_boolean_field_invalid")
    try:
        full_toman_price = int(payload.get("price_toman"))
        quantity = int(payload.get("quantity"))
    except (TypeError, ValueError) as exc:
        raise CoinGroupFeedbackError("parser_feedback_numeric_field_invalid") from exc
    if full_toman_price <= 0 or full_toman_price % 1_000:
        raise CoinGroupFeedbackError("parser_feedback_full_toman_price_invalid")
    review = record_coin_group_parser_feedback(
        feedback_db,
        event_key=event["event_key"],
        event_type=event["event_type"],
        group_number=event["group_number"],
        source_event_time_utc=event["source_event_time_utc"],
        ambiguous_fields=normalized_fields,
        event_confirmed=event_confirmed,
        commodity_code=str(payload.get("commodity_code") or ""),
        side=str(payload.get("side") or ""),
        price_project_thousand_toman=full_toman_price // 1_000,
        quantity=quantity,
        settlement_term=str(payload.get("settlement_term") or ""),
        trade_form=str(payload.get("trade_form") or ""),
        is_conditional=is_conditional,
        reviewer=reviewer,
    )
    return {
        "status": "RECORDED_PENDING_PIPELINE",
        "event_id": review.event_key.hex(),
        "review_revision": review.review_revision,
        "ambiguous_fields": sorted(review.ambiguous_fields),
        "reviewed_at_utc": review.reviewed_at_utc,
        "expected_apply_seconds": 45,
    }


_EVENT_AUDIT_STATUS_FA = {
    "MODEL_INPUT": "ورودی واقعی مدل",
    "AUDIT_ONLY_LATE": "دیررس؛ فقط گزارش",
    "AUDIT_ONLY_CONDITIONAL": "شرطی؛ فقط گزارش",
    "PENDING_REVIEW": "نیازمند بررسی",
}


def _render_event_estimate_cell(prediction: object) -> str:
    if not isinstance(prediction, dict):
        return "<span class='event-no-estimate'>—</span>"
    estimated = fa_number(prediction.get("estimated_price_toman"))
    lower = prediction.get("lower_price_toman")
    upper = prediction.get("upper_price_toman")
    interval = (
        f"<small>بازه {fa_number(lower)} تا {fa_number(upper)}</small>"
        if lower is not None and upper is not None
        else "<small>بازه ثبت نشده</small>"
    )
    cycle_time = fa_datetime(prediction.get("prediction_time_utc"))
    return (
        f"<strong>{estimated} تومان</strong>{interval}"
        f"<time>چرخهٔ مدل: {cycle_time}</time>"
    )


def _render_private_event_text(event: dict[str, Any]) -> str:
    raw_offer = event.get("raw_offer_text")
    if not isinstance(raw_offer, str) or not raw_offer.strip():
        return (
            "<span class='raw-text-unavailable'>متن خام در staging سه‌روزه "
            "موجود نیست.</span>"
        )
    result = (
        "<div class='raw-event-text'><strong>آفر خام کاربر</strong>"
        f"<pre>{html.escape(raw_offer)}</pre>"
    )
    raw_event = event.get("raw_event_text")
    if (
        event.get("event_type") == "TRADE"
        and isinstance(raw_event, str)
        and raw_event.strip()
        and raw_event != raw_offer
    ):
        result += (
            "<strong>پیام خام تأیید معامله</strong>"
            f"<pre>{html.escape(raw_event)}</pre>"
        )
    return result + "</div>"


def render_model_event_audit(audit: dict[str, Any]) -> str:
    """Render canonical detections with ephemeral authenticated review text."""

    model_labels = audit.get("model_labels")
    model_labels = model_labels if isinstance(model_labels, dict) else {}
    events = audit.get("events")
    events = events if isinstance(events, list) else []
    rows: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        status = str(event.get("status") or "PENDING_REVIEW")
        status_text = _EVENT_AUDIT_STATUS_FA.get(status, status)
        reason = html.escape(str(event.get("exclusion_reason") or ""), quote=True)
        estimates = event.get("estimates")
        estimates = estimates if isinstance(estimates, dict) else {}
        existing_feedback = event.get("parser_feedback")
        existing_feedback = (
            existing_feedback if isinstance(existing_feedback, dict) else None
        )
        reviewed_values = existing_feedback or {}
        commodity_code = str(
            reviewed_values.get("commodity_code")
            or _FEEDBACK_LABEL_TO_COMMODITY.get(
                str(event.get("commodity") or ""), ""
            )
        )
        review_payload = html.escape(
            json.dumps(
                {
                    "event_id": event.get("event_id"),
                    "event_type": event.get("event_type"),
                    "commodity_code": commodity_code,
                    "side": reviewed_values.get("side") or event.get("side"),
                    "price_toman": reviewed_values.get("price_toman")
                    or event.get("price_toman"),
                    "quantity": reviewed_values.get("quantity")
                    or event.get("quantity") or 1,
                    "settlement_term": reviewed_values.get("settlement_term")
                    or event.get("settlement"),
                    "trade_form": reviewed_values.get("trade_form")
                    or event.get("trade_form"),
                    "is_conditional": bool(
                        reviewed_values.get(
                            "is_conditional", event.get("is_conditional")
                        )
                    ),
                    "event_confirmed": reviewed_values.get(
                        "event_confirmed", True
                    ),
                    "feedback": existing_feedback,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            quote=True,
        )
        if existing_feedback is not None:
            review_state = (
                "اعمال‌شده در parser"
                if existing_feedback.get("applied")
                else "ثبت‌شده؛ منتظر چرخهٔ parser"
            )
            review_summary = (
                f"<small class='feedback-state'>{review_state} · بازبینی "
                f"{fa_number(existing_feedback.get('review_revision'))}</small>"
            )
            review_label = "اصلاح بازخورد"
        else:
            review_summary = ""
            review_label = "بازبینی parser"
        model_cells = "".join(
            f"<td class='estimate-cell'>{_render_event_estimate_cell(estimates.get(model_id))}</td>"
            for model_id in _EVENT_AUDIT_MODELS
        )
        event_type = "آفر" if event.get("event_type") == "OFFER" else "معامله"
        side = "خرید" if event.get("side") == "BUY" else "فروش"
        settlement = _FEEDBACK_SETTLEMENT_LABELS.get(
            str(event.get("settlement")), "نامشخص"
        )
        rows.append(
            "<tr>"
            f"<td class='event-status-cell'><span class='event-status {status.lower()}' title='{reason}'>{status_text}</span>"
            f"{review_summary}<button type='button' class='parser-feedback-btn' data-review='{review_payload}' "
            f"onclick='openParserFeedback(this)'>{review_label}</button></td>"
            f"<td class='raw-text-cell'>{_render_private_event_text(event)}</td>"
            f"<td>گروه {fa_number(event.get('group_number'))}</td>"
            f"<td><strong>{event_type}</strong></td>"
            f"<td><time>{fa_datetime(event.get('source_event_time_utc'))}</time></td>"
            f"<td><time>{fa_datetime(event.get('available_at_utc'))}</time></td>"
            f"<td>{html.escape(str(event.get('commodity') or 'نامشخص'))}</td>"
            f"<td>{side} · {settlement}</td>"
            f"<td class='numeric'><strong>{fa_number(event.get('price_toman'))}</strong> تومان"
            f"<small>{fa_number(event.get('quantity')) if event.get('quantity') is not None else '—'} عدد</small></td>"
            f"{model_cells}"
            "</tr>"
        )

    if not rows:
        rows.append(
            "<tr><td colspan='13' class='missing'>در این بازه هیچ رویداد canonical "
            "تشخیص‌داده‌شده‌ای ثبت نشده است.</td></tr>"
        )
    model_headers = "".join(
        f"<th>{html.escape(str(model_labels.get(model_id) or model_id))}</th>"
        for model_id in _EVENT_AUDIT_MODELS
    )
    return f"""
    <section id='parser-review-ledger' class='event-audit-section'>
      <div class='section-head'>
        <div>
          <h2>بازبینی parser و دفتر کامل رویدادهای مدل</h2>
          <p>هر ردیف یک آفر یا معاملهٔ canonical است. فقط ردیف سبز واقعاً وارد مدل شده؛
          قیمت‌ها از دفتر ثبت پیش‌بینی همان چرخه خوانده شده‌اند و بازبرآورد امروزی نیستند.
          دکمهٔ بازبینی کنار وضعیت هر ردیف، فیلدهای مبهم یا اشتباه را برای کالیبراسیون parser ثبت می‌کند.
          متن خام فقط از staging خصوصی سه‌روزه خوانده می‌شود و در بازخورد یا مدل کپی نمی‌شود.</p>
        </div>
        <span class='badge'>آخرین خواندن: {fa_datetime(audit.get('generated_at_utc'))}</span>
      </div>
      <div class='event-audit-summary'>
        <span>کل رویدادها <strong>{fa_number(audit.get('total_events', 0))}</strong></span>
        <span>ورودی واقعی مدل <strong>{fa_number(audit.get('model_input_events', 0))}</strong></span>
        <span>فقط گزارش/بررسی <strong>{fa_number(audit.get('audit_only_events', 0))}</strong></span>
        <span class='{'warning' if audit.get('missing_main_prediction_events') else ''}'>بدون قیمت ثبت‌شدهٔ مدل اصلی <strong>{fa_number(audit.get('missing_main_prediction_events', 0))}</strong></span>
      </div>
      <div class='table-wrap event-audit-wrap'>
        <table class='event-audit-table'>
          <thead><tr>
            <th>وضعیت / بازبینی parser</th><th>آفر خام کاربر</th><th>گروه</th><th>رویداد</th><th>زمان پیام</th>
            <th>زمان دسترس‌پذیری برای مدل</th><th>کالا</th><th>سمت / تسویه</th>
            <th>فی واقعی رویداد / تعداد</th>{model_headers}
          </tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
      <div id='parser-feedback-modal' class='modal-overlay' onclick='closeParserFeedback(event)'>
        <form id='parser-feedback-form' class='modal-card feedback-modal-card' onclick='event.stopPropagation()'>
          <div class='modal-header'>
            <div><h2>بازبینی فیلدهای مبهم parser</h2>
            <small id='parser-feedback-context'>—</small></div>
            <button type='button' class='modal-close-btn' onclick='closeParserFeedback()'>&times;</button>
          </div>
          <div class='modal-body'>
            <input type='hidden' id='feedback-event-id'>
            <p class='feedback-help'>حداقل یک فیلد مبهم را انتخاب کنید و سپس مقدار صحیح کامل رویداد را ثبت کنید. تصحیح همان رویداد در چرخهٔ بعدی اعمال و برای کالیبراسیون موارد بعدی استفاده می‌شود.</p>
            <fieldset class='feedback-fields'><legend>کدام فیلدها مبهم یا اشتباه بوده‌اند؟</legend>
              <label><input type='checkbox' name='ambiguous_field' value='event_validity'> اصل آفر/معامله بودن</label>
              <label><input type='checkbox' name='ambiguous_field' value='commodity'> کالا</label>
              <label><input type='checkbox' name='ambiguous_field' value='side'> خرید/فروش</label>
              <label><input type='checkbox' name='ambiguous_field' value='price'> فی</label>
              <label><input type='checkbox' name='ambiguous_field' value='quantity'> تعداد</label>
              <label><input type='checkbox' name='ambiguous_field' value='settlement'> نقدی/فردایی</label>
              <label><input type='checkbox' name='ambiguous_field' value='trade_form'> نوع فیزیکی/کاغذی</label>
              <label><input type='checkbox' name='ambiguous_field' value='conditional'> شرطی بودن</label>
            </fieldset>
            <div class='feedback-grid'>
              <label class='feedback-toggle'><input id='feedback-event-confirmed' type='checkbox' checked> این رویداد واقعاً آفر/معامله است</label>
              <label>کالا<select id='feedback-commodity' required>
                <option value=''>انتخاب کنید</option>
                {''.join(f"<option value='{code}'>{label}</option>" for code, label in _FEEDBACK_COMMODITY_LABELS.items())}
              </select></label>
              <label>سمت<select id='feedback-side' required><option value='BUY'>خرید</option><option value='SELL'>فروش</option></select></label>
              <label>فی کامل (تومان)<input id='feedback-price' type='number' min='1000' step='1000' required></label>
              <label>تعداد<input id='feedback-quantity' type='number' min='1' max='100' required></label>
              <label>تسویه<select id='feedback-settlement' required><option value='CASH'>نقدی</option><option value='TODAY'>امروزی کاغذی</option><option value='TOMORROW'>فردایی</option></select></label>
              <label>نوع بازار<select id='feedback-trade-form' required><option value='PHYSICAL'>فیزیکی</option><option value='PAPER_NORMAL'>کاغذی عادی</option><option value='PAPER_REVERSE'>کاغذی معکوس</option><option value='PAPER_SWIM'>کاغذی شنا</option></select></label>
              <label class='feedback-toggle'><input id='feedback-conditional' type='checkbox'> آفر/معامله شرطی است</label>
            </div>
            <p id='parser-feedback-result' class='feedback-result' aria-live='polite'></p>
          </div>
          <div class='feedback-actions'><button type='button' class='nav-btn secondary' onclick='closeParserFeedback()'>انصراف</button><button id='parser-feedback-submit' type='submit' class='nav-btn'>ثبت و کالیبره‌کردن parser</button></div>
        </form>
      </div>
    </section>
    """


def query_user_details(
    conversation_db: Path,
    username: str,
    group: int,
    kind: str,
    *,
    range_type: str = "today",
    start_shamsi: str | None = None,
    end_shamsi: str | None = None,
) -> dict[str, Any]:
    db_path = find_analytics_db(conversation_db)
    start_utc, end_utc, label = calculate_time_bounds(range_type, start_shamsi, end_shamsi)

    empty_res = {
        "username": username,
        "group": group,
        "kind": kind,
        "range_label": label,
        "total_items": 0,
        "total_qty": 0,
        "items": [],
    }

    if not db_path or not db_path.exists():
        return empty_res

    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]

        items = []
        total_qty = 0

        if kind in ("offer", "offer_qty"):
            if "offer_training_examples" not in tables:
                conn.close()
                return empty_res
            rows = conn.execute(
                """
                SELECT occurred_at_utc, commodity, side, price, quantity, settlement, offer_text
                FROM offer_training_examples
                WHERE group_number = ? AND offerer_name = ? AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                ORDER BY occurred_at_utc DESC
                """,
                (group, username, start_utc, end_utc),
            ).fetchall()
            for r in rows:
                d = dict(r)
                q = d.get("quantity") or 1
                total_qty += q
                items.append({
                    "time": fa_datetime(d.get("occurred_at_utc")),
                    "commodity": html.escape(str(d.get("commodity") or "—")),
                    "side": "خرید" if d.get("side") == "BUY" else "فروش",
                    "price": fa_number(d.get("price")),
                    "quantity": fa_number(q),
                    "settlement": SETTLEMENT_FA.get(str(d.get("settlement")), "—"),
                    "text": html.escape(str(d.get("offer_text") or "")),
                })
        else:
            if "confirmed_trade_training_examples" not in tables:
                conn.close()
                return empty_res
            rows = conn.execute(
                """
                SELECT occurred_at_utc, offerer_name, counterparty_name, commodity, side, price, quantity, settlement
                FROM confirmed_trade_training_examples
                WHERE group_number = ? AND (offerer_name = ? OR counterparty_name = ?) AND occurred_at_utc >= ? AND occurred_at_utc <= ?
                ORDER BY occurred_at_utc DESC
                """,
                (group, username, username, start_utc, end_utc),
            ).fetchall()
            for r in rows:
                d = dict(r)
                q = d.get("quantity") or 1
                total_qty += q
                is_offerer = d.get("offerer_name") == username
                counterparty = d.get("counterparty_name") if is_offerer else d.get("offerer_name")
                role = "آفر دهنده" if is_offerer else "پاسخ‌دهنده"
                items.append({
                    "time": fa_datetime(d.get("occurred_at_utc")),
                    "role": role,
                    "counterparty": html.escape(str(counterparty or "—")),
                    "commodity": html.escape(str(d.get("commodity") or "—")),
                    "side": "خرید" if d.get("side") == "BUY" else "فروش",
                    "price": fa_number(d.get("price")),
                    "quantity": fa_number(q),
                    "settlement": SETTLEMENT_FA.get(str(d.get("settlement")), "—"),
                })

        conn.close()
        return {
            "username": username,
            "group": group,
            "kind": kind,
            "range_label": label,
            "total_items": len(items),
            "total_qty": total_qty,
            "items": items,
        }
    except Exception:
        return empty_res


def render_user_details_pdf_page(
    conversation_db: Path,
    username: str,
    group: int,
    kind: str,
    *,
    range_type: str = "today",
    start_shamsi: str | None = None,
    end_shamsi: str | None = None,
) -> bytes:
    data = query_user_details(
        conversation_db,
        username,
        group,
        kind,
        range_type=range_type,
        start_shamsi=start_shamsi,
        end_shamsi=end_shamsi,
    )

    is_trade = kind in ("trade", "trade_qty")
    kind_title = "معاملات تاییدشده" if is_trade else "آفرهای ثبت‌شده"
    now_fa = fa_datetime(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    rows_html = []
    if not data["items"]:
        rows_html.append("<tr><td colspan='8' style='text-align:center;padding:20px;color:#888'>هیچ داده‌ای در این بازه زمانی یافت نشد.</td></tr>")
    elif is_trade:
        for idx, item in enumerate(data["items"], 1):
            side_color = "#059669" if item["side"] == "خرید" else "#dc2626"
            rows_html.append(
                f"<tr>"
                f"<td style='text-align:center'>{fa_number(idx)}</td>"
                f"<td>{item['time']}</td>"
                f"<td><strong>{item['role']}</strong></td>"
                f"<td><strong style='color:#b45309'>{item['counterparty']}</strong></td>"
                f"<td>{item['commodity']}</td>"
                f"<td style='color:{side_color};font-weight:bold;text-align:center'>{item['side']}</td>"
                f"<td dir='ltr' style='text-align:left;font-weight:bold'>{item['price']}</td>"
                f"<td dir='ltr' style='text-align:left'>{item['quantity']}</td>"
                f"</tr>"
            )
    else:
        for idx, item in enumerate(data["items"], 1):
            side_color = "#059669" if item["side"] == "خرید" else "#dc2626"
            rows_html.append(
                f"<tr>"
                f"<td style='text-align:center'>{fa_number(idx)}</td>"
                f"<td>{item['time']}</td>"
                f"<td>{item['commodity']}</td>"
                f"<td style='color:{side_color};font-weight:bold;text-align:center'>{item['side']}</td>"
                f"<td dir='ltr' style='text-align:left;font-weight:bold'>{item['price']}</td>"
                f"<td dir='ltr' style='text-align:left'>{item['quantity']}</td>"
                f"<td>{item['settlement']}</td>"
                f"<td style='word-break:break-word'>{item['text']}</td>"
                f"</tr>"
            )

    if is_trade:
        colgroup_html = """
          <colgroup>
            <col style="width:5%">
            <col style="width:20%">
            <col style="width:13%">
            <col style="width:24%">
            <col style="width:13%">
            <col style="width:7%">
            <col style="width:11%">
            <col style="width:7%">
          </colgroup>
        """
        table_headers = """
          <tr>
            <th style="text-align:center">#</th>
            <th>زمان ثبت</th>
            <th>نقش کاربر</th>
            <th>طرف مقابل معامله</th>
            <th>کالا</th>
            <th style="text-align:center">سمت</th>
            <th style="text-align:left">قیمت (تومان)</th>
            <th style="text-align:left">تعداد</th>
          </tr>
        """
    else:
        colgroup_html = """
          <colgroup>
            <col style="width:5%">
            <col style="width:20%">
            <col style="width:12%">
            <col style="width:7%">
            <col style="width:12%">
            <col style="width:7%">
            <col style="width:10%">
            <col style="width:27%">
          </colgroup>
        """
        table_headers = """
          <tr>
            <th style="text-align:center">#</th>
            <th>زمان ثبت</th>
            <th>کالا</th>
            <th style="text-align:center">سمت</th>
            <th style="text-align:left">قیمت (تومان)</th>
            <th style="text-align:left">تعداد</th>
            <th>تسویه</th>
            <th>متن آفر</th>
          </tr>
        """

    doc = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<title>گزارش {kind_title} — {html.escape(username)}</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
@page {{
  size: A4 portrait;
  margin: 8mm 10mm;
}}
body {{
  font-family: Vazirmatn, system-ui, -apple-system, sans-serif;
  color: #0f172a;
  background: #ffffff;
  margin: 0;
  padding: 16px;
  line-height: 1.4;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}}
.header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 2px solid #f59e0b;
  padding-bottom: 10px;
  margin-bottom: 16px;
}}
.header h1 {{
  margin: 0 0 4px;
  font-size: 19px;
  color: #0f172a;
  font-weight: 800;
}}
.header p {{
  margin: 0;
  font-size: 12.5px;
  color: #475569;
}}
.meta-grid {{
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 16px;
  background: #f8fafc;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 12px;
}}
.meta-item strong {{
  color: #0f172a;
  display: block;
  font-size: 13.5px;
  margin-top: 2px;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 11.5px;
  margin-top: 8px;
}}
th, td {{
  padding: 7px 8px;
  border: 1px solid #cbd5e1;
  text-align: right;
  vertical-align: middle;
  word-wrap: break-word;
  overflow-wrap: break-word;
}}
th {{
  background: #0f172a !important;
  color: #ffffff !important;
  font-weight: 700;
  font-size: 11.5px;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}}
tr:nth-child(even) td {{
  background: #f8fafc;
}}
.footer {{
  margin-top: 24px;
  border-top: 1px solid #cbd5e1;
  padding-top: 10px;
  text-align: center;
  font-size: 11px;
  color: #64748b;
}}
@media print {{
  .no-print {{ display: none !important; }}
  body {{ padding: 0; margin: 0; }}
  @page {{ margin: 8mm 10mm; }}
}}
</style>
</head>
<body>
<div class="no-print" style="margin-bottom:16px;text-align:left">
  <button onclick="window.print()" style="background:#f59e0b;color:#0f172a;border:none;padding:9px 20px;font-family:inherit;font-weight:bold;font-size:13.5px;border-radius:8px;cursor:pointer;box-shadow:0 4px 12px rgba(245,158,11,0.3)">🖨️ چاپ / ذخیره مستقیم به عنوان PDF</button>
</div>

<div class="header">
  <div>
    <h1>گزارش آمار و فعالیت کاربر: {html.escape(username)}</h1>
    <p>نوع گزارش: {kind_title} — گروه معاملاتی {fa_number(group)}</p>
  </div>
  <div style="text-align:left;font-size:11.5px;color:#475569">
    <div>سامانه تحلیل و برآورد بازار سکه</div>
    <div>تاریخ تنظیم: {now_fa}</div>
  </div>
</div>

<div class="meta-grid">
  <div class="meta-item">نام کاربر: <strong>{html.escape(username)}</strong></div>
  <div class="meta-item">گروه معاملاتی: <strong>گروه {fa_number(group)}</strong></div>
  <div class="meta-item">بازه زمانی: <strong>{data['range_label']}</strong></div>
  <div class="meta-item">تعداد / حجم کل: <strong>{fa_number(data['total_items'])} رویداد ({fa_number(data['total_qty'])} کالا)</strong></div>
</div>

<table>
  {colgroup_html}
  <thead>{table_headers}</thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>

<div class="footer">
  این گزارش از سامانه هوشمند قیمت‌گذاری و تحلیل بازار سکه استخراج شده است.
</div>

<script>
window.onload = function() {{
  setTimeout(function() {{
    window.print();
  }}, 400);
}};
</script>
</body>
</html>"""
    return doc.encode("utf-8")


def render_analytics_leaderboard_table(
    title: str,
    subtitle: str,
    items: list[dict[str, Any]],
    val_key: str,
    val_unit: str,
    group: int = 1,
    kind: str = "offer",
) -> str:
    rows = []
    if not items:
        rows.append(f"<tr><td colspan='3' class='missing'>{NO_DATA_TOKEN}</td></tr>")
    else:
        for idx, item in enumerate(items, 1):
            raw_name = str(item.get("username") or item.get("offerer_name") or "—")
            escaped_name = html.escape(raw_name, quote=True)
            val = fa_number(item.get(val_key, 0))
            rows.append(
                f"<tr>"
                f"<td class='rank-col'>#{fa_number(idx)}</td>"
                f"<td><a class='user-link' href='javascript:void(0)' data-username='{escaped_name}' data-group='{group}' data-kind='{kind}' onclick='openUserModal(this.getAttribute(\"data-username\"), this.getAttribute(\"data-group\"), this.getAttribute(\"data-kind\"))'><strong>{escaped_name}</strong></a></td>"
                f"<td class='value-col'><strong>{val}</strong> <small>{val_unit}</small></td>"
                f"</tr>"
            )
    return f"""
    <div class='leaderboard-card'>
      <div class='card-header'>
        <h3>{title}</h3>
        <small>{subtitle}</small>
      </div>
      <div class='table-wrap'>
        <table>
          <thead>
            <tr><th style='width:50px'>رتبه</th><th>نام کاربر</th><th style='text-align:left'>تعداد / مقدار</th></tr>
          </thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
    </div>
    """


INPUT_STATUS_FA = {
    "OBSERVED": "مشاهده‌شده",
    "ESTIMATED": "برآورد فعال",
    "EXCLUDED": "داده موجود؛ خارج از قرارداد",
    "NO_DATA": "بدون دادهٔ معتبر",
}

INPUT_FORM_FA = {
    "PHYSICAL": "فیزیکی",
    "PAPER": "کاغذی",
    "PHYSICAL_BRIDGED_BY_PAPER": "فیزیکی با پل تغییرات کاغذی",
}

INPUT_SETTLEMENT_FA = {
    "TODAY": "امروز",
    "TOMORROW": "فردا",
    "UNKNOWN": "نامشخص",
}


def _settlement_inputs(
    settlements: dict[str, dict[str, Any]], settlement: str
) -> dict[str, dict[str, Any]]:
    payload = settlements.get(settlement)
    payload = payload if isinstance(payload, dict) else {}
    inputs = payload.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _input_active_value(payload: dict[str, Any]) -> float | None:
    """Mirror the estimator's exact point-then-mean selection contract."""

    if str(payload.get("status") or "").upper() not in {"OBSERVED", "ESTIMATED"}:
        return None
    return live_point_value(payload)


def _input_value_text(key: str, value: object) -> str:
    if value is None:
        return NO_DATA_TOKEN
    rendered = fa_number(value, decimals=2 if key == "xauusd" else 0)
    return f"{rendered} {'دلار' if key == 'xauusd' else 'تومان'}"


def _input_time(payload: dict[str, Any]) -> str | None:
    if payload.get("point_price") is not None:
        return str(
            payload.get("latest_event_utc")
            or payload.get("last_event_utc")
            or ""
        ) or None
    selected = str(
        payload.get("anchor_event_utc")
        or payload.get("latest_event_utc")
        or payload.get("last_event_utc")
        or ""
    ) or None
    if selected:
        return selected
    excluded = payload.get("excluded_observations")
    excluded = excluded if isinstance(excluded, list) else []
    excluded_times = [
        str(item.get("latest_event_utc"))
        for item in excluded
        if isinstance(item, dict) and item.get("latest_event_utc")
    ]
    return max(excluded_times) if excluded_times else None


def _fa_age(seconds: object) -> str | None:
    try:
        value = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return None
    if value < 120:
        return f"{fa_number(value)} ثانیه"
    if value < 7_200:
        return f"{fa_number(value // 60)} دقیقه"
    if value < 172_800:
        return f"{fa_number(value // 3_600)} ساعت"
    return f"{fa_number(value // 86_400)} روز"


def _input_source_text(key: str, payload: dict[str, Any]) -> str:
    parts: list[str] = []
    if key == "xauusd" and payload.get("is_proxy") is True:
        parts.append("پراکسی تأییدشدهٔ PAXG؛ اونس مستقیم در دسترس نیست")
    elif key == "usdt":
        parts.append("بازار عمومی USDT/IRT")
    elif key == "generic_coin":
        parts.append("نرخ عمومی سکه؛ مستقل از گروه‌های معاملاتی")
        excluded = payload.get("excluded_observations")
        excluded = excluded if isinstance(excluded, list) else []
        if excluded:
            labels = "، ".join(
                str(item.get("market_label") or "سکه")
                for item in excluded
                if isinstance(item, dict)
            )
            parts.append(
                f"{labels} تازه است، اما تسویهٔ صریح ندارد و وارد مدل نشده"
            )
    elif payload.get("price_source"):
        parts.append(str(payload["price_source"]))
    if payload.get("selected_market_label"):
        parts.append(str(payload["selected_market_label"]))
    if payload.get("selected_settlement_term"):
        settlement = str(payload["selected_settlement_term"])
        parts.append(INPUT_SETTLEMENT_FA.get(settlement, settlement))
    if payload.get("selected_trade_form"):
        trade_form = str(payload["selected_trade_form"])
        parts.append(INPUT_FORM_FA.get(trade_form, trade_form))
    if payload.get("market_movement_driver"):
        parts.append(f"محرک: {payload['market_movement_driver']}")
    if not parts and str(payload.get("status") or "").upper() == "NO_DATA":
        parts.append("هیچ مسیر مجازِ هم‌تسویه و هم‌فرم انتخاب نشده است")
    return " · ".join(parts) or "—"


def render_input_cards(settlements: dict[str, dict[str, Any]]) -> str:
    labels = {
        "melted_gold": "طلا آب‌شده",
        "generic_coin": "سکه عمومی (غیرگروهی)",
        "xauusd": "اونس جهانی",
        "usd": "دلار هرات",
        "usdt": "تتر / تومان",
    }
    icons = {
        "melted_gold": "◆",
        "generic_coin": "●",
        "xauusd": "◎",
        "usd": "$",
        "usdt": "₮",
    }
    cards = []
    for key, label in labels.items():
        cash = _settlement_inputs(settlements, "CASH").get(key)
        cash = cash if isinstance(cash, dict) else {}
        tomorrow = _settlement_inputs(settlements, "TOMORROW").get(key)
        tomorrow = tomorrow if isinstance(tomorrow, dict) else {}
        values = {
            "CASH": _input_active_value(cash),
            "TOMORROW": _input_active_value(tomorrow),
        }
        statuses = {
            str(cash.get("status") or "NO_DATA").upper(),
            str(tomorrow.get("status") or "NO_DATA").upper(),
        }
        estimated = "ESTIMATED" in statuses
        observed = bool(statuses & {"OBSERVED", "ESTIMATED"})
        css = "estimated" if estimated else ("observed" if observed else "no-data")
        if key == "xauusd" and (
            cash.get("is_proxy") is True or tomorrow.get("is_proxy") is True
        ):
            label = "پراکسی اونس جهانی (PAXG)"
        detail = "نقطهٔ زندهٔ مصرف‌شده؛ در نبود آن برآورد/میانگین فعال"
        cards.append(
            f"<article class='input-card {css}' data-source='{html.escape(key)}'>"
            f"<div class='input-card-head'><span class='input-icon' aria-hidden='true'>"
            f"{icons[key]}</span><span>{label}</span></div>"
            f"<strong><span class='settlement-value'>نقدی</span> "
            f"{_input_value_text(key, values['CASH'])}<br>"
            f"<span class='settlement-value'>فردایی</span> "
            f"{_input_value_text(key, values['TOMORROW'])}</strong>"
            f"<small>{html.escape(detail)}</small></article>"
        )
    return "".join(cards)


def render_model_input_audit(
    settlements: dict[str, dict[str, Any]],
) -> str:
    labels = {
        "melted_gold": "آب‌شدهٔ انتخاب‌شده",
        "usd": "دلار هرات",
        "usdt": "تتر",
        "xauusd": "اونس / پراکسی اونس",
        "generic_coin": "سکهٔ عمومی (غیرگروهی)",
        "order_flow": "جریان سفارش",
        "market_regime": "رژیم بازار",
    }
    tables: list[str] = []
    for settlement, settlement_label in SETTLEMENT_FA.items():
        inputs = _settlement_inputs(settlements, settlement)
        rows: list[str] = []
        for key, label in labels.items():
            payload = inputs.get(key)
            payload = payload if isinstance(payload, dict) else {}
            status = str(payload.get("status") or "NO_DATA").upper()
            if key == "generic_coin" and payload.get("excluded_observations"):
                status = "EXCLUDED"
            status_text = INPUT_STATUS_FA.get(status, status)
            active_value = _input_active_value(payload)
            if key == "order_flow":
                score = payload.get("estimator_score")
                active_text = (
                    NO_DATA_TOKEN
                    if score is None
                    else f"{fa_number(float(score) * 100, decimals=1)}٪"
                )
                average_text = "امتیاز اثر در تخمین"
                average_kind = "سیگنال محاسبه‌شدهٔ مدل"
                source_text = "جریان خرید/فروش، جداشده بر پایهٔ تسویه و فرم"
            elif key == "market_regime":
                active_text = html.escape(str(payload.get("regime") or NO_DATA_TOKEN))
                direction = payload.get("direction_score")
                confidence = payload.get("confidence")
                average_text = (
                    f"جهت {fa_number(direction, decimals=3)} · اطمینان "
                    f"{fa_number(confidence, decimals=3)}"
                    if direction is not None or confidence is not None
                    else NO_DATA_TOKEN
                )
                average_kind = "سیگنال محاسبه‌شدهٔ مدل"
                source_text = "رژیم مستقل بازار؛ ورودی سیگنال و دامنهٔ عدم‌قطعیت"
            else:
                active_text = _input_value_text(key, active_value)
                average_text = _input_value_text(key, payload.get("average_price"))
                average_kind = "میانگین بازهٔ مدل"
                source_text = _input_source_text(key, payload)
            selection = str(payload.get("selection") or "—")
            observed_at = _input_time(payload)
            age = _fa_age(payload.get("anchor_age_seconds"))
            time_text = fa_datetime(observed_at) if observed_at else NO_DATA_TOKEN
            if age:
                time_text += f" · سن لنگر {age}"
            if key in {"order_flow", "market_regime"}:
                active_kind = "سیگنال مستقیم مدل"
            else:
                active_kind = (
                    "آخرین رویداد واقعی"
                    if payload.get("point_price") is not None
                    else (
                        "مقدار برآوردی/میانگین فعال"
                        if active_value is not None
                        else "بدون مقدار قابل مصرف"
                    )
                )
            rows.append(
                "<tr>"
                f"<td><strong>{html.escape(label)}</strong></td>"
                f"<td><span class='audit-status audit-{html.escape(status.lower())}'>"
                f"{html.escape(status_text)}</span></td>"
                f"<td><strong class='audit-value'>{active_text}</strong>"
                f"<small>{html.escape(active_kind)}</small></td>"
                f"<td>{average_text}<small>{html.escape(average_kind)}</small></td>"
                f"<td>{html.escape(source_text)}<code dir='ltr'>{html.escape(selection)}</code></td>"
                f"<td>{html.escape(time_text)}</td>"
                "</tr>"
            )
        tables.append(
            "<article class='model-input-book'>"
            f"<h3>دفتر ورودی {html.escape(settlement_label)}</h3>"
            "<div class='table-wrap'><table class='input-audit-table'>"
            "<thead><tr><th>ورودی</th><th>وضعیت</th><th>مقدار واقعاً مصرف‌شده</th>"
            "<th>میانگین/سیگنال</th><th>منبع و مسیر انتخاب</th><th>زمان داده</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div></article>"
        )
    return f"""
    <div class="model-input-audit" role="region" aria-labelledby="model-input-audit-title">
      <div class="section-head">
        <div><span class="section-kicker">اتصال مستقیم به snapshot مدل</span>
        <h3 id="model-input-audit-title">دفتر دقیق ورودی‌های تخمین</h3></div>
        <span class="badge">نقدی و فردایی جدا</span>
      </div>
      <p>مقدار فعال دقیقاً با قرارداد مدل انتخاب می‌شود: آخرین رویداد واقعی؛ و فقط اگر نقطه‌ای وجود نداشته باشد، برآورد یا میانگین معتبر همان ورودی.</p>
      <div class="model-input-books">{''.join(tables)}</div>
    </div>
    """


def _render_group_anchor(anchor: object, *, historical: bool = False) -> str:
    payload = anchor if isinstance(anchor, dict) else {}
    if str(payload.get("status") or "").upper() != "OBSERVED":
        return "<span class='missing'>بدون لنگر واجد شرایط</span>"
    price = payload.get("reference_price_toman")
    observed_at = payload.get("event_time_utc") if historical else payload.get("latest_event_utc")
    age = _fa_age(payload.get("age_seconds"))
    source = str(payload.get("reference_source") or payload.get("selection") or "—")
    counts = (
        f"{fa_number(payload.get('offer_count', 0))} آفر · "
        f"{fa_number(payload.get('trade_count', 0))} معامله"
    )
    return (
        f"<strong class='audit-value'>{fa_number(price)} تومان کامل</strong>"
        f"<small>{html.escape(counts)}</small>"
        f"<small>{html.escape(fa_datetime(str(observed_at)) if observed_at else NO_DATA_TOKEN)}"
        f"{f' · {html.escape(age)} پیش' if age else ''}</small>"
        f"<code dir='ltr'>{html.escape(source)}</code>"
    )


def render_group_model_input_audit(
    settlements: dict[str, dict[str, Any]],
) -> str:
    books: list[str] = []
    for settlement, settlement_label in SETTLEMENT_FA.items():
        payload = settlements.get(settlement)
        payload = payload if isinstance(payload, dict) else {}
        rates = payload.get("rates")
        rates = rates if isinstance(rates, list) else []
        rows: list[str] = []
        for rate in rates:
            if not isinstance(rate, dict):
                continue
            live = rate.get("group_offer_anchor")
            historical = rate.get("historical_group_anchor")
            historical_payload = historical if isinstance(historical, dict) else {}
            anchor_weight = rate.get("anchor_weight")
            if (
                isinstance(live, dict)
                and str(live.get("status") or "").upper() == "OBSERVED"
            ):
                effect = "لنگر زنده مستقیماً در این ردیف فعال است"
            elif str(historical_payload.get("status") or "").upper() == "OBSERVED":
                effect = (
                    "لنگر تاریخی در ترکیب فعال است"
                    + (
                        f" · وزن {fa_number(float(anchor_weight) * 100, decimals=1)}٪"
                        if anchor_weight is not None
                        else ""
                    )
                )
            elif str(rate.get("method") or "").startswith("CURRENT_CASH_ESTIMATE"):
                effect = "از نرخ نقدی مشتق شده؛ اثر گروه را در ردیف نقدی همان کالا ببینید"
            else:
                effect = "اثر مستقیم لنگر گروه در این ردیف ثبت نشده است"
            rows.append(
                "<tr>"
                f"<td><strong>{html.escape(str(rate.get('commodity_name') or '—'))}</strong></td>"
                f"<td>{_render_group_anchor(live)}</td>"
                f"<td>{_render_group_anchor(historical, historical=True)}</td>"
                f"<td>{html.escape(effect)}<code dir='ltr'>"
                f"{html.escape(str(rate.get('method') or '—'))}</code></td>"
                "</tr>"
            )
        books.append(
            "<article class='model-input-book group-input-book'>"
            f"<h3>اثر گروه‌ها بر برآورد {html.escape(settlement_label)}</h3>"
            "<div class='table-wrap'><table class='input-audit-table group-audit-table'>"
            "<thead><tr><th>کالا</th><th>لنگر زندهٔ ۵ دقیقه‌ای</th>"
            "<th>لنگر تاریخی منتخب</th><th>اثر ثبت‌شده در خروجی</th></tr></thead>"
            f"<tbody>{''.join(rows) if rows else '<tr><td colspan=\"4\" class=\"missing\">نرخی در این دفتر ثبت نشده است</td></tr>'}</tbody>"
            "</table></div></article>"
        )
    return f"""
    <div class="model-input-audit group-model-audit" role="region" aria-labelledby="group-model-input-title">
      <div class="section-head">
        <div><span class="section-kicker">آفر و معاملهٔ واجد شرایط</span>
        <h3 id="group-model-input-title">اثر واقعی گروه‌های سکه در هر نرخ</h3></div>
        <span class="badge">زنده، تاریخی یا بدون اثر</span>
      </div>
      <p>heartbeat جمع‌آور با وجود بازار ساکت هم می‌تواند سالم باشد؛ این جدول جداگانه نشان می‌دهد کدام لنگر گروه واقعاً در هر خروجی استفاده شده است.</p>
      <div class="model-input-books">{''.join(books)}</div>
    </div>
    """


def render_input_health_panel(input_health: object) -> str:
    if not isinstance(input_health, dict):
        return ""
    aggregate = str(input_health.get("status") or "UNKNOWN").upper()
    status_fa = {
        "HEALTHY": "سالم",
        "DEGRADED": "نیازمند توجه",
        "CRITICAL": "بحرانی",
        "DISABLED": "غیرفعال",
        "AVAILABLE": "در دسترس",
        "AVAILABLE_PROXY": "در دسترس با منبع جایگزین",
        "PARTIAL": "بخشی از دفتر در دسترس",
        "HISTORICAL_ONLY": "فقط لنگر تاریخی",
        "HISTORICAL": "تاریخی",
        "EXCLUDED": "کنارگذاشته‌شده",
        "EXCLUDED_BY_CONTRACT": "داده موجود؛ خارج از قرارداد",
        "QUIET_OR_NO_DATA": "بازار ساکت / بدون داده",
        "NO_DATA": "بدون داده",
        "STALE": "کهنه",
    }
    collector_labels = {
        "public_market_telegram": "تلگرام بازار عمومی",
        "wallex_public_api": "API تتر",
        "binance_paxg_public_api": "پراکسی اونس PAXG",
        "coin_group_projection": "ورود گروه‌ها تا مدل",
    }
    input_labels = {
        "melted_gold": "آب‌شده",
        "xauusd": "اونس",
        "usd": "دلار هرات",
        "usdt": "تتر",
        "generic_coin": "سکه عمومی",
        "order_flow": "جریان سفارش",
        "market_regime": "رژیم بازار",
        "coin_groups": "گروه‌های سکه",
    }
    cards: list[str] = []
    collectors = input_health.get("collectors")
    if isinstance(collectors, dict):
        for key, label in collector_labels.items():
            payload = collectors.get(key)
            payload = payload if isinstance(payload, dict) else {}
            status = str(payload.get("status") or "UNKNOWN").upper()
            age = payload.get("heartbeat_age_seconds")
            detail = (
                "heartbeat دریافت نشده"
                if age is None
                else f"heartbeat: {fa_number(age)} ثانیه پیش"
            )
            if key == "coin_group_projection":
                details = payload.get("details")
                details = details if isinstance(details, dict) else {}
                for group_number in (1, 2):
                    prefix = f"group_{group_number}"
                    canonical_event = details.get(
                        f"{prefix}_latest_canonical_event_utc"
                    )
                    eligible = details.get(
                        f"{prefix}_latest_eligible_event_utc"
                    )
                    pending = details.get(f"{prefix}_pending_review_total")
                    rejected = details.get(f"{prefix}_rejected_total")
                    canonical_event_text = (
                        fa_datetime(str(canonical_event))
                        if canonical_event
                        else "بدون ورودی canonical"
                    )
                    eligible_text = (
                        fa_datetime(str(eligible))
                        if eligible
                        else "بدون ورودی واجدشرایط"
                    )
                    detail += (
                        f" · گروه {fa_number(group_number)}: جدیدترین رویداد canonical گروه "
                        f"{canonical_event_text}؛ آخرین ورودی واجدشرایط canonical {eligible_text}"
                    )
                    if pending is not None or rejected is not None:
                        detail += (
                            f"؛ در انتظار بررسی {fa_number(pending or 0)}"
                            f"؛ رد/نادیده {fa_number(rejected or 0)}"
                        )
                detail += (
                    " · گیت ایمنی: ناهمخوانی معامله با آفر ریشه "
                    f"{fa_number(details.get('causal_trade_mismatches') or 0)}"
                    "؛ پرت قیمتی نسبت به آفرهای زنده "
                    f"{fa_number(details.get('live_book_price_outliers') or 0)}"
                )
            cards.append(
                f"<article class='health-card health-{html.escape(status.lower())}'>"
                f"<span>{html.escape(label)}</span>"
                f"<strong>{html.escape(status_fa.get(status, status))}</strong>"
                f"<small>{html.escape(detail)}</small></article>"
            )
    model_inputs = input_health.get("model_inputs")
    if isinstance(model_inputs, dict):
        for key in (
            "melted_gold",
            "xauusd",
            "usd",
            "usdt",
            "generic_coin",
            "coin_groups",
            "order_flow",
        ):
            payload = model_inputs.get(key)
            payload = payload if isinstance(payload, dict) else {}
            status = str(payload.get("status") or "UNKNOWN").upper()
            settlements = payload.get("settlements")
            settlements = settlements if isinstance(settlements, dict) else {}
            detail = "نقدی: {cash} · فردایی: {tomorrow}".format(
                cash=status_fa.get(
                    str(settlements.get("CASH", "NO_DATA")).upper(),
                    str(settlements.get("CASH", "NO_DATA")),
                ),
                tomorrow=status_fa.get(
                    str(settlements.get("TOMORROW", "NO_DATA")).upper(),
                    str(settlements.get("TOMORROW", "NO_DATA")),
                ),
            )
            age = _fa_age(payload.get("latest_observation_age_seconds"))
            if age:
                age_label = (
                    "جدیدترین لنگر واجدشرایط مدل"
                    if key == "coin_groups"
                    else "آخرین دادهٔ قابل‌مصرف"
                )
                detail += f" · {age_label} {age} پیش"
            cards.append(
                f"<article class='health-card health-{html.escape(status.lower())}'>"
                f"<span>ورودی {html.escape(input_labels.get(key, key))}</span>"
                f"<strong>{html.escape(status_fa.get(status, status))}</strong>"
                f"<small>{html.escape(detail)}</small></article>"
            )
    reason_count = len(input_health.get("reason_codes") or [])
    subtitle = (
        "heartbeat جمع‌آورها و تازگی ورودی‌های واقعی مستقل پایش می‌شوند."
        if aggregate == "HEALTHY"
        else f"{fa_number(reason_count)} علت فعال؛ جزئیات ماشینی در healthz و data.json"
    )
    return f"""
    <section class="input-health-panel health-{html.escape(aggregate.lower())}" aria-label="سلامت ورودی‌های مدل">
      <div class="input-health-head">
        <div><span class="section-kicker">پایش انتها‌به‌انتها</span><h3>سلامت ورودی‌های مدل</h3></div>
        <span class="health-summary">{html.escape(status_fa.get(aggregate, aggregate))}</span>
      </div>
      <p>{html.escape(subtitle)}</p>
      <div class="health-grid">{''.join(cards)}</div>
    </section>
    """


def render_manual_effect(state: dict[str, Any]) -> str:
    cards = []
    for settlement, payload in state.get("settlements", {}).items():
        items = []
        for rate in payload.get("rates", []):
            if rate.get("status") != "ESTIMATED":
                continue
            tolerance = rate.get("tolerance") or {}
            items.append(
                "<li>"
                f"<strong>{html.escape(str(rate.get('commodity_name') or '—'))}</strong> "
                f"{fa_number(rate.get('estimated_project_price'))} "
                f"<small>({fa_number(tolerance.get('lower_project_price'))} تا "
                f"{fa_number(tolerance.get('upper_project_price'))})</small></li>"
            )
        if not items:
            items.append(f"<li class='missing'>{NO_DATA_TOKEN}</li>")
        cards.append(
            "<div class='effect-card'>"
            f"<h3>{SETTLEMENT_FA.get(settlement, settlement)}</h3>"
            f"<ul>{''.join(items)}</ul></div>"
        )
    return "".join(cards) or f"<p class='missing'>{NO_DATA_TOKEN}</p>"


def render_manual_entry_panel(
    state: dict[str, Any], *, manual_path: str, write_enabled: bool, flash: str | None,
    open_manual_offers: list[dict[str, Any]],
) -> str:
    commodity_options = "".join(
        f"<option value='{html.escape(name)}'>{html.escape(name)}</option>"
        for name in COMMODITY_SPECS
    )
    flash_html = ""
    if flash == "saved":
        flash_html = "<p class='flash success'>آفر با موفقیت ثبت و نرخ‌ها بروزرسانی شدند.</p>"
    elif flash == "saved_refresh_failed":
        flash_html = "<p class='flash warning'>اطلاعات ثبت شد، اما بروزرسانی لحظه‌ای با خطا مواجه شد.</p>"
    elif flash == "invalid":
        flash_html = "<p class='flash warning'>اطلاعات ورودی معتبر نیست. لطفاً فرم را بررسی کنید.</p>"
    elif flash == "forbidden":
        flash_html = "<p class='flash warning'>کلید دسترسی ثبت معتبر نمی‌باشد.</p>"
    disabled = "" if write_enabled else " disabled"
    open_offer_options = "".join(
        "<option value='{id}'>#{id} — {commodity} / {side} / {price} — {time}</option>".format(
            id=int(offer["id"]),
            commodity=html.escape(str(offer["commodity"])),
            side="خرید" if offer["side"] == "BUY" else "فروش",
            price=fa_number(offer["price"]),
            time=fa_datetime(str(offer["occurred_at_utc"])),
        )
        for offer in open_manual_offers
    )
    confirm_disabled = disabled if open_offer_options else " disabled"
    counts = state.get("manual_entry_counts") or {}
    tehran_now = datetime.now(TEHRAN).strftime("%Y-%m-%dT%H:%M")
    return f"""
    <section id="manual-entry" class="manual-section">
      <div class="section-head">
        <h2>پنل ثبت دستی پیشنهادات</h2>
        <span class="badge">{fa_number(counts.get('offers', 0))} آفر / {fa_number(counts.get('confirmed_trades', 0))} معامله</span>
      </div>
      {flash_html}
      <form class="manual-form" method="post" action="{html.escape(manual_path)}">
        <h3 class="form-title">۱. ثبت آفر جدید</h3>
        <label class="wide">متن آفر
          <textarea id="raw-offer-text" name="raw_offer_text" maxlength="2000" rows="2" placeholder="مثلاً: ۱۵:۳۱ — ۱۰ تا ربع ۵۱۲۰۰ ف"{disabled}></textarea>
        </label>
        <button class="wide secondary" id="parse-offer-text" type="button"{disabled}>استخراج هوشمند متن</button>
        <p id="parse-offer-result" class="manual-help wide"></p>
        <label>کالا<select name="commodity" required{disabled}>{commodity_options}</select></label>
        <label>تسویه<select name="settlement" required{disabled}><option value="CASH">نقدی</option><option value="TOMORROW">فردایی</option></select></label>
        <label>نوع معامله<select name="trade_form" required{disabled}><option value="PHYSICAL">فیزیکی</option><option value="PAPER">کاغذی</option></select></label>
        <label>نوع آفر<select name="side" required{disabled}><option value="" selected>انتخاب کنید...</option><option value="BUY">خرید</option><option value="SELL">فروش</option></select></label>
        <label>قیمت آفر (تومان)<input name="price" inputmode="numeric" required{disabled}></label>
        <label>تعداد (اختیاری)<input name="quantity" inputmode="numeric"{disabled}></label>
        <label class="check wide"><input type="checkbox" name="offer_live" value="1" checked{disabled}> ثبت لایو (زمان حال)</label>
        <label class="wide">زمان ثبت آفر<input name="offer_time" type="datetime-local" value="{tehran_now}"{disabled}></label>
        <label class="wide">توضیحات تکمیلی<textarea name="description" maxlength="2000" rows="1"{disabled}></textarea></label>
        <button class="wide primary-btn" type="submit"{disabled}>ثبت آفر و محاسبه</button>
      </form>
      <form class="manual-form confirm-form" method="post" action="{html.escape(manual_path)}">
        <input type="hidden" name="entry_mode" value="confirm_existing">
        <h3 class="form-title">۲. ثبت معامله برای آفر موجود</h3>
        <label class="wide">انتخاب آفر باز<select name="offer_id" required{confirm_disabled}><option value="">انتخاب آفر...</option>{open_offer_options}</select></label>
        <label class="check wide"><input type="checkbox" name="trade_live" value="1" checked{confirm_disabled}> معامله لایو (زمان حال)</label>
        <label class="wide">زمان ثبت معامله<input name="trade_time" type="datetime-local" value="{tehran_now}"{confirm_disabled}></label>
        <label>قیمت معامله (تومان)<input name="trade_price" inputmode="numeric"{confirm_disabled}></label>
        <label>تعداد معامله<input name="trade_quantity" inputmode="numeric"{confirm_disabled}></label>
        <button class="wide primary-btn" type="submit"{confirm_disabled}>ثبت معامله</button>
      </form>
      <aside class="manual-effect">
        <h3>تأثیر لحظه‌ای بر تخمین</h3>
        <div class="effect-grid">{render_manual_effect(state)}</div>
      </aside>
    </section>
    """


class SessionStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._memory: dict[str, tuple[str, float]] = {}
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS web_sessions (
                    token TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    expires_at_utc REAL NOT NULL
                )
                """
            )
            conn.commit()
            now = time.time()
            rows = conn.execute("SELECT token, username, expires_at_utc FROM web_sessions WHERE expires_at_utc > ?", (now,)).fetchall()
            for token, username, expires_at in rows:
                self._memory[token] = (username, expires_at)
            conn.close()
        except Exception:
            pass

    def create_session(self, username: str) -> str:
        token = secrets.token_hex(32)
        expires_at = time.time() + 2_592_000  # 30 days
        with self._lock:
            self._memory[token] = (username, expires_at)
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute(
                    "INSERT OR REPLACE INTO web_sessions (token, username, expires_at_utc) VALUES (?, ?, ?)",
                    (token, username, expires_at),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
        return token

    def validate_session(self, token: str | None) -> str | None:
        if not token:
            return None
        with self._lock:
            if token not in self._memory:
                return None
            username, expires_at = self._memory[token]
            if time.time() > expires_at:
                del self._memory[token]
                try:
                    conn = sqlite3.connect(self._db_path)
                    conn.execute("DELETE FROM web_sessions WHERE token = ?", (token,))
                    conn.commit()
                    conn.close()
                except Exception:
                    pass
                return None
            return username

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._memory.pop(token, None)
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute("DELETE FROM web_sessions WHERE token = ?", (token,))
                conn.commit()
                conn.close()
            except Exception:
                pass


def render_login_page(login_path: str = "/login", error: str | None = None) -> bytes:
    error_html = f"<div class='alert-error'>⚠️ {html.escape(error)}</div>" if error else ""
    document = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ورود به سامانه تخمین نرخ سکه</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root {{
  --bg-deep: #0b1329;
  --bg-surface: rgba(15, 23, 42, 0.85);
  --bg-card: #141f36;
  --border-line: rgba(255, 255, 255, 0.08);
  --border-gold: rgba(245, 158, 11, 0.35);
  --border-gold-glow: 0 0 15px rgba(245, 158, 11, 0.12);
  --text-main: #f8fafc;
  --text-sub: #94a3b8;
  --accent-gold: #f59e0b;
  --accent-cyan: #06b6d4;
  --accent-indigo: #6366f1;
  --accent-rose: #f43f5e;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: radial-gradient(circle at 50% 20%, #1e1b4b 0%, #0b1329 80%);
  color: var(--text-main);
  font-family: Vazirmatn, system-ui, -apple-system, sans-serif;
  line-height: 1.5;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}}
.login-card {{
  width: 100%;
  max-width: 400px;
  background: var(--bg-surface);
  backdrop-filter: blur(24px);
  border: 1px solid var(--border-gold);
  box-shadow: var(--border-gold-glow);
  border-radius: 20px;
  padding: 32px 28px;
}}
.login-brand {{
  text-align: center;
  margin-bottom: 24px;
}}
.logo-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 54px;
  height: 54px;
  border-radius: 16px;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.25), rgba(99, 102, 241, 0.25));
  border: 1px solid var(--accent-gold);
  font-size: 28px;
  margin-bottom: 12px;
}}
h1 {{
  margin: 0 0 6px;
  font-size: 22px;
  font-weight: 800;
  background: linear-gradient(135deg, #ffffff 40%, var(--accent-gold) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
p.subtitle {{
  margin: 0;
  color: var(--text-sub);
  font-size: 13px;
}}
.alert-error {{
  background: rgba(244, 63, 94, 0.15);
  border: 1px solid rgba(244, 63, 94, 0.4);
  color: #fca5a5;
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
  margin-bottom: 20px;
  text-align: center;
}}
.form-group {{
  margin-bottom: 18px;
}}
label {{
  display: block;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-sub);
  margin-bottom: 6px;
}}
input[type="text"], input[type="password"] {{
  width: 100%;
  padding: 11px 14px;
  border-radius: 10px;
  border: 1px solid var(--border-line);
  background: rgba(15, 23, 42, 0.9);
  color: var(--text-main);
  font-family: inherit;
  font-size: 14px;
  transition: all 0.2s ease;
}}
input[type="text"]:focus, input[type="password"]:focus {{
  outline: none;
  border-color: var(--accent-gold);
  box-shadow: 0 0 10px rgba(245, 158, 11, 0.2);
}}
.submit-btn {{
  width: 100%;
  padding: 12px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-gold), #d97706);
  color: #0b1329;
  font-weight: 800;
  font-size: 15px;
  border: none;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(245, 158, 11, 0.25);
  transition: all 0.2s ease;
  margin-top: 6px;
}}
.submit-btn:hover {{
  transform: translateY(-1px);
  box-shadow: 0 8px 24px rgba(245, 158, 11, 0.35);
}}
</style>
</head>
<body>
<div class="login-card">
  <div class="login-brand">
    <div class="logo-badge">🪙</div>
    <h1>ورود به سامانه <span>تخمین نرخ سکه</span></h1>
    <p class="subtitle">جهت دسترسی به اطلاعات بازار، نام کاربری و رمز عبور را وارد کنید</p>
  </div>
  {error_html}
  <form method="post" action="{html.escape(login_path)}">
    <div class="form-group">
      <label>نام کاربری</label>
      <input type="text" name="username" placeholder="نام کاربری خود را وارد کنید" required autofocus autocomplete="username">
    </div>
    <div class="form-group">
      <label>رمز عبور</label>
      <input type="password" name="password" placeholder="••••••••" required autocomplete="current-password">
    </div>
    <button type="submit" class="submit-btn">ورود به سامانه</button>
  </form>
</div>
</body>
</html>"""
    return document.encode("utf-8")


def load_shadow_dashboard_payload(shadow_state_path: Path | None = None) -> dict[str, Any]:
    """Read the isolated parallel-shadow state for the operator dashboard."""

    path = shadow_state_path or DEFAULT_SHADOW_STATE
    if not path.is_file():
        return {
            "enabled": False,
            "status": "MISSING",
            "estimate": {},
            "comparison_vs_live": {},
            "pair_details": [],
            "shadow_model_path": str(path),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {
            "enabled": False,
            "status": "UNREADABLE",
            "estimate": {},
            "comparison_vs_live": {},
            "pair_details": [],
            "shadow_model_path": str(path),
        }
    if not isinstance(payload, dict):
        return {
            "enabled": False,
            "status": "INVALID",
            "estimate": {},
            "comparison_vs_live": {},
            "pair_details": [],
            "shadow_model_path": str(path),
        }
    return payload


def render_shadow_compare_rows(pair_details: list[dict[str, Any]]) -> str:
    if not pair_details:
        return f"<tr><td colspan='5' class='missing'>{NO_DATA_TOKEN}</td></tr>"
    rows = []
    for item in pair_details:
        key = str(item.get("key") or "—")
        live = item.get("live") or {}
        shadow = item.get("shadow") or {}
        live_status = str(live.get("status") or "MISSING")
        shadow_status = str(shadow.get("status") or "MISSING")
        live_price = (
            fa_number(live.get("estimated_project_price"))
            if live_status == "ESTIMATED"
            else NO_DATA_TOKEN
        )
        shadow_price = (
            fa_number(shadow.get("estimated_project_price"))
            if shadow_status == "ESTIMATED"
            else NO_DATA_TOKEN
        )
        abs_pct = item.get("abs_pct")
        diff = (
            f"{fa_number(round(float(abs_pct) * 100, 3))}٪"
            if abs_pct is not None
            else "—"
        )
        rows.append(
            "<tr>"
            f"<td><strong>{html.escape(key)}</strong></td>"
            f"<td class='rate-cell'><strong>{live_price}</strong>"
            f"<small>{html.escape(live_status)}</small></td>"
            f"<td class='rate-cell'><strong>{shadow_price}</strong>"
            f"<small>{html.escape(shadow_status)}</small></td>"
            f"<td>{diff}</td>"
            f"<td><small>{html.escape(str(shadow.get('method') or '—'))}</small></td>"
            "</tr>"
        )
    return "".join(rows)


def _estimate_centers(estimate: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Map commodity:settlement → rate payload for side-by-side compare."""

    out: dict[str, dict[str, Any]] = {}
    if not isinstance(estimate, dict):
        return out
    for settlement, body in (estimate.get("settlements") or {}).items():
        if not isinstance(body, dict):
            continue
        for item in body.get("rates") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("commodity_name") or "")
            if not name:
                continue
            out[f"{name}:{settlement}"] = item
    return out


def _shadow_estimate_body(payload: dict[str, Any]) -> dict[str, Any]:
    estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
    if not estimate and payload.get("settlements"):
        estimate = payload
    return estimate if isinstance(estimate, dict) else {}


def _price_cell(rate: dict[str, Any] | None, *, tone: str = "") -> str:
    if not rate or str(rate.get("status")) != "ESTIMATED":
        return f"<td class='rate-cell {tone}'><span class='missing'>{NO_DATA_TOKEN}</span></td>"
    price = fa_number(rate.get("estimated_project_price"))
    method = html.escape(str(rate.get("method") or "—"))
    return (
        f"<td class='rate-cell {tone}'><strong>{price}</strong>"
        f"<small title='{method}'>{method[:42]}{'…' if len(method) > 42 else ''}</small></td>"
    )


def _diff_cell(live_rate: dict[str, Any] | None, shadow_rate: dict[str, Any] | None) -> str:
    if (
        not live_rate
        or not shadow_rate
        or str(live_rate.get("status")) != "ESTIMATED"
        or str(shadow_rate.get("status")) != "ESTIMATED"
        or live_rate.get("estimated_project_price") is None
        or shadow_rate.get("estimated_project_price") is None
    ):
        return "<td class='diff-cell'>—</td>"
    live_price = float(live_rate["estimated_project_price"])
    shadow_price = float(shadow_rate["estimated_project_price"])
    if live_price == 0:
        return "<td class='diff-cell'>—</td>"
    signed = (shadow_price - live_price) / abs(live_price)
    cls = "diff-up" if signed > 1e-9 else "diff-down" if signed < -1e-9 else "diff-flat"
    sign = "+" if signed > 0 else ""
    return (
        f"<td class='diff-cell {cls}'>"
        f"{sign}{fa_number(round(signed * 100, 3))}٪"
        f"</td>"
    )


def render_unified_shadow_compare_table(
    live_state: dict[str, Any],
    models: list[tuple[str, dict[str, Any]]],
) -> str:
    """One table: live + all shadows for every commodity/settlement."""

    live_centers = _estimate_centers(live_state if isinstance(live_state, dict) else {})
    model_centers = [
        (label, _estimate_centers(_shadow_estimate_body(payload)))
        for label, payload in models
    ]
    keys: list[str] = []
    seen: set[str] = set()
    for settlement in ("CASH", "TOMORROW"):
        for name in COMMODITY_SPECS:
            key = f"{name}:{settlement}"
            if key in live_centers or any(key in centers for _, centers in model_centers):
                if key not in seen:
                    keys.append(key)
                    seen.add(key)
        # Include any extra names that appear only in estimates.
        for source in [live_centers, *[centers for _, centers in model_centers]]:
            for key in source:
                if key.endswith(f":{settlement}") and key not in seen:
                    keys.append(key)
                    seen.add(key)

    settlement_fa = {"CASH": "نقدی", "TOMORROW": "فردایی"}
    if not keys:
        colspan = 3 + 2 * len(models)
        return f"<tr><td colspan='{colspan}' class='missing'>{NO_DATA_TOKEN}</td></tr>"

    rows: list[str] = []
    for key in keys:
        name, settlement = key.split(":", 1)
        live_rate = live_centers.get(key)
        cells = [
            f"<td><strong>{html.escape(name)}</strong></td>",
            f"<td>{html.escape(settlement_fa.get(settlement, settlement))}</td>",
            _price_cell(live_rate, tone="col-live"),
        ]
        for _, centers in model_centers:
            shadow_rate = centers.get(key)
            cells.append(_price_cell(shadow_rate, tone="col-shadow"))
            cells.append(_diff_cell(live_rate, shadow_rate))
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(rows)


def _shadow_status_card(
    *,
    title: str,
    payload: dict[str, Any],
    accent: str,
) -> str:
    estimate = _shadow_estimate_body(payload)
    comparison = payload.get("comparison_vs_live") or {}
    mean_pct = comparison.get("mean_abs_pct")
    mean_text = (
        f"{fa_number(round(float(mean_pct) * 100, 3))}٪"
        if mean_pct is not None
        else "—"
    )
    status = html.escape(str(payload.get("status") or "UNKNOWN"))
    window_end = fa_datetime(estimate.get("window_end_utc") or payload.get("generated_at_utc"))
    return f"""
    <article class="input-card model-status-card" style="--model-accent:{accent}">
      <span class="model-label">{html.escape(title)}</span>
      <strong>{status}</strong>
      <small>میانگین اختلاف با اصلی: {mean_text}</small>
      <small>پنجره: {html.escape(window_end)}</small>
    </article>
    """


def _shadow_panel_html(
    *,
    title: str,
    subtitle: str,
    badge: str,
    payload: dict[str, Any],
    accent: str = "rgba(99,102,241,0.45)",
) -> str:
    """Render one clearly titled shadow block for the dual-shadow page."""

    estimate = payload.get("estimate") if isinstance(payload.get("estimate"), dict) else {}
    if not estimate and payload.get("settlements"):
        estimate = payload
    shadow_state = dict(estimate) if isinstance(estimate, dict) else {}
    settlements = shadow_state.get("settlements") or {}
    comparison = payload.get("comparison_vs_live") or {}
    pair_details = list(payload.get("pair_details") or [])
    generated = fa_datetime(
        shadow_state.get("generated_at_utc") or payload.get("generated_at_utc")
    )
    window_end = fa_datetime(shadow_state.get("window_end_utc"))
    model_kind = html.escape(
        str(payload.get("shadow_model_kind") or shadow_state.get("model_kind") or "—")
    )
    status = html.escape(str(payload.get("status") or "UNKNOWN"))
    mean_pct = comparison.get("mean_abs_pct")
    median_pct = comparison.get("median_abs_pct")
    paired = comparison.get("paired_estimated_count")
    mean_text = (
        f"{fa_number(round(float(mean_pct) * 100, 3))}٪" if mean_pct is not None else "—"
    )
    median_text = (
        f"{fa_number(round(float(median_pct) * 100, 3))}٪"
        if median_pct is not None
        else "—"
    )
    model_path = html.escape(str(payload.get("shadow_model_path") or "—"))
    return f"""
    <section class="shadow-panel" style="border-color:{accent}">
      <div class="section-head">
        <div>
          <h2>{html.escape(title)}</h2>
          <p class="shadow-subtitle">{html.escape(subtitle)}</p>
        </div>
        <span class="badge">{html.escape(badge)}</span>
      </div>
      <div class="inputs" style="margin-bottom:12px">
        <div class="input-card observed"><span>وضعیت</span><strong>{status}</strong><small>{model_kind}</small></div>
        <div class="input-card observed"><span>میانگین اختلاف با اصلی</span><strong>{mean_text}</strong><small>جفت: {fa_number(paired or 0)}</small></div>
        <div class="input-card observed"><span>میانهٔ اختلاف</span><strong>{median_text}</strong><small>به‌روزرسانی: {html.escape(generated)}</small></div>
        <div class="input-card observed"><span>پایان پنجره</span><strong style="font-size:13px">{html.escape(window_end)}</strong><small>فقط پایش</small></div>
      </div>
      <div class="table-wrap" style="margin-bottom:12px">
        <table>
          <thead>
            <tr>
              <th>نوع کالا</th>
              <th>نرخ نقدی (تومان)</th>
              <th>نرخ فردایی (تومان)</th>
            </tr>
          </thead>
          <tbody>{render_combined_rate_rows(settlements if isinstance(settlements, dict) else {})}</tbody>
        </table>
      </div>
      <div class="section-head"><h2 style="font-size:14px">مقایسه با مدل اصلی</h2></div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>کالا / تسویه</th>
              <th>مدل اصلی</th>
              <th>{html.escape(title)}</th>
              <th>اختلاف نسبی</th>
              <th>روش سایه</th>
            </tr>
          </thead>
          <tbody>{render_shadow_compare_rows(pair_details)}</tbody>
        </table>
      </div>
      <p class="shadow-path">مسیر مدل: <code>{model_path}</code></p>
    </section>
    """


MODEL_OUTCOME_LABELS = {
    MAIN_COMPARISON_MODEL_ID: "مدل اصلی",
    "SHADOW1_PREVIOUS": "سایه ۱ — مدل قبلی",
    "SHADOW2_MORNING_REOPEN": "سایه ۲ — بازگشایی صبح",
    "SHADOW3_ML_RESIDUAL": "سایه ۳ — یادگیری ماشین",
}


def render_model_outcome_panel(live_state: dict[str, Any]) -> str:
    """Render accuracy against realised trades — the only promotion evidence."""

    learning = (
        live_state.get("online_residual_learning")
        if isinstance(live_state, dict)
        else None
    )
    outcomes = (
        learning.get("model_outcomes") if isinstance(learning, dict) else None
    )
    if not isinstance(outcomes, dict) or not outcomes:
        body = (
            "<tr><td colspan='6'>هنوز هیچ پیش‌بینی‌ای به معاملهٔ واقعی متصل نشده "
            "است. تا انباشت داده، مقایسهٔ معتبر در دسترس نیست.</td></tr>"
        )
        footnote = ""
    else:
        rows = []
        legacy = 0
        for model_id in sorted(outcomes):
            entry = outcomes[model_id]
            if not isinstance(entry, dict):
                continue
            legacy += int(entry.get("capped_only_sample_count") or 0)
            label = MODEL_OUTCOME_LABELS.get(model_id, model_id)
            rows.append(
                "<tr>"
                f"<td>{html.escape(label)}</td>"
                f"<td class='rate-cell'>{fa_number(entry.get('sample_count'))}</td>"
                f"<td class='rate-cell'>{fa_number(round(float(entry.get('mape_percent') or 0.0), 3))}٪</td>"
                f"<td class='rate-cell'>{fa_number(round(float(entry.get('bias_percent') or 0.0), 3))}٪</td>"
                f"<td class='rate-cell'>{fa_number(round(float(entry.get('worst_abs_error_percent') or 0.0), 3))}٪</td>"
                f"<td class='rate-cell'>{fa_number(round(float(entry.get('interval_coverage_percent') or 0.0), 1))}٪</td>"
                "</tr>"
            )
        body = "".join(rows)
        footnote = (
            "<p class='shadow-path'>بخشی از ردیف‌ها پیش از افزودن ستون خطای خام "
            "ارزیابی شده‌اند و خطایشان در سقف ۳٫۵٪ محدود مانده؛ میانگین این "
            "مدل‌ها کمتر از واقعیت است.</p>"
            if legacy
            else ""
        )
    return f"""
    <section class="shadow-panel outcome-panel">
      <div class="section-head">
        <div>
          <h2>دقت واقعی در برابر معاملهٔ انجام‌شده</h2>
          <p class="shadow-subtitle">تنها معیار معتبر برای ارتقای یک سایه</p>
        </div>
        <span class="badge">۷ روز گذشته</span>
      </div>
      <div class="table-wrap">
        <table class="compare-table">
          <thead>
            <tr>
              <th>مدل</th>
              <th>تعداد ارزیابی</th>
              <th>میانگین خطای مطلق</th>
              <th>سوگیری</th>
              <th>بدترین خطا</th>
              <th>پوشش بازه</th>
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
      <p class="shadow-path">هر چهار کتاب با همان معاملهٔ واقعی سنجیده می‌شوند.
      سوگیری منفی یعنی مدل به‌طور سیستماتیک پایین‌تر از بازار تخمین زده است.
      پوشش بازه باید نزدیک ۸۰٪ باشد؛ کمتر یعنی بازه‌ها بیش از حد باریک‌اند.</p>
      {footnote}
    </section>
    """


SHADOW_DASHBOARD_POLISH_CSS = """
html {
  color-scheme: dark;
}
body {
  overflow-x: hidden;
  background:
    radial-gradient(circle at 9% 8%, rgba(37, 99, 235, 0.18), transparent 28rem),
    radial-gradient(circle at 92% 4%, rgba(13, 148, 136, 0.13), transparent 26rem),
    linear-gradient(155deg, #07111f 0%, #0a1425 45%, #080f1c 100%);
}
body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: 0.18;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.055) 1px, transparent 1px);
  background-size: 42px 42px;
  mask-image: linear-gradient(to bottom, black, transparent 76%);
}
.wrap {
  position: relative;
  width: min(1580px, calc(100% - 40px));
  margin: 24px auto 48px;
}
.shadow-hero {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  min-height: 142px;
  padding: 24px 26px;
  border: 1px solid rgba(96, 165, 250, 0.2);
  border-radius: 24px;
  background:
    linear-gradient(112deg, rgba(15, 31, 52, 0.98), rgba(12, 24, 43, 0.9)),
    var(--bg-surface);
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.28), inset 0 1px rgba(255, 255, 255, 0.04);
}
.shadow-hero::after {
  content: "";
  position: absolute;
  z-index: -1;
  width: 360px;
  height: 360px;
  inset: -235px auto auto -70px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(45, 212, 191, 0.25), transparent 68%);
}
.shadow-brand {
  display: flex;
  align-items: center;
  gap: 16px;
  min-width: 310px;
}
.logo-badge {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 58px;
  height: 58px;
  border: 1px solid rgba(94, 234, 212, 0.35);
  border-radius: 18px;
  color: #5eead4;
  background: linear-gradient(145deg, rgba(13, 148, 136, 0.22), rgba(37, 99, 235, 0.2));
  box-shadow: 0 16px 34px rgba(8, 145, 178, 0.14);
  font-size: 30px;
}
.eyebrow {
  display: block;
  margin-bottom: 3px;
  color: #5eead4;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
}
.shadow-hero h1 {
  font-size: clamp(20px, 2vw, 28px);
  line-height: 1.3;
}
.shadow-hero p {
  max-width: 690px;
  line-height: 1.75;
}
.shadow-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.user-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 7px 11px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  border-radius: 12px;
  color: var(--text-sub);
  background: rgba(7, 17, 31, 0.42);
  font-size: 12px;
}
.user-label strong { color: var(--text-main); }
.user-avatar { color: #5eead4; font-size: 10px; }
.nav-btn {
  min-height: 36px;
  padding: 8px 13px;
  border-radius: 11px;
  background: linear-gradient(135deg, #0f766e, #155e75);
  border-color: rgba(94, 234, 212, 0.22);
  box-shadow: 0 10px 24px rgba(8, 145, 178, 0.14);
  transition: transform 160ms ease, border-color 160ms ease, background 160ms ease;
}
.nav-btn.secondary {
  background: rgba(15, 31, 52, 0.72);
  border-color: rgba(148, 163, 184, 0.16);
}
.nav-btn:hover {
  transform: translateY(-1px);
  border-color: rgba(94, 234, 212, 0.5);
}
.nav-btn:focus-visible {
  outline: 3px solid rgba(45, 212, 191, 0.24);
  outline-offset: 2px;
}
#estimate-content {
  display: grid;
  gap: 18px;
}
.shadow-panel {
  position: relative;
  overflow: hidden;
  margin: 0;
  padding: 22px;
  border: 1px solid rgba(148, 163, 184, 0.13);
  border-radius: 22px;
  background: linear-gradient(155deg, rgba(17, 31, 51, 0.94), rgba(12, 24, 42, 0.94));
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.22), inset 0 1px rgba(255, 255, 255, 0.035);
}
.comparison-panel { border-top-color: rgba(96, 165, 250, 0.45); }
.outcome-panel { border-top-color: rgba(52, 211, 153, 0.45); }
.section-head {
  align-items: center;
  margin-bottom: 16px;
}
.section-head h2 {
  font-size: 18px;
  line-height: 1.5;
}
.shadow-subtitle {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.7;
}
.badge {
  flex: 0 0 auto;
  padding: 5px 11px;
  color: #93c5fd;
  border-color: rgba(96, 165, 250, 0.25);
  background: rgba(37, 99, 235, 0.11);
}
.inputs {
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.model-status-card {
  position: relative;
  overflow: hidden;
  min-height: 126px;
  padding: 15px 16px 14px;
  border: 1px solid rgba(148, 163, 184, 0.13);
  background: rgba(7, 17, 31, 0.48);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.025);
}
.model-status-card::before {
  content: "";
  position: absolute;
  inset: 0 0 auto;
  height: 3px;
  background: var(--model-accent);
  box-shadow: 0 0 22px var(--model-accent);
}
.model-status-card .model-label {
  min-height: 34px;
  color: #cbd5e1;
  font-size: 12px;
  font-weight: 750;
  line-height: 1.6;
}
.model-status-card strong {
  margin: 2px 0 4px;
  color: var(--model-accent);
  font-size: 19px;
}
.model-status-card small {
  line-height: 1.65;
  white-space: normal;
}
.table-wrap {
  overflow: auto;
  border-color: rgba(148, 163, 184, 0.12);
  border-radius: 14px;
  background: rgba(6, 14, 26, 0.4);
  scrollbar-color: rgba(94, 234, 212, 0.38) rgba(15, 23, 42, 0.45);
  scrollbar-width: thin;
}
.comparison-panel .compare-table { min-width: 1220px; }
.outcome-panel .compare-table { min-width: 760px; }
th, td {
  padding: 12px 14px;
  border-bottom-color: rgba(148, 163, 184, 0.1);
}
th {
  top: 0;
  color: #aebed2;
  background: rgba(8, 19, 34, 0.98);
  backdrop-filter: blur(12px);
}
tbody tr:nth-child(even) td { background: rgba(148, 163, 184, 0.018); }
tbody tr:hover td { background: rgba(94, 234, 212, 0.035); }
.comparison-panel .compare-table th:nth-child(3),
.comparison-panel .compare-table td:nth-child(3) { background-color: rgba(246, 196, 83, 0.045); }
.comparison-panel .compare-table th:nth-child(4),
.comparison-panel .compare-table td:nth-child(4),
.comparison-panel .compare-table th:nth-child(5),
.comparison-panel .compare-table td:nth-child(5) { background-color: rgba(245, 158, 11, 0.035); }
.comparison-panel .compare-table th:nth-child(6),
.comparison-panel .compare-table td:nth-child(6),
.comparison-panel .compare-table th:nth-child(7),
.comparison-panel .compare-table td:nth-child(7) { background-color: rgba(34, 211, 238, 0.035); }
.comparison-panel .compare-table th:nth-child(8),
.comparison-panel .compare-table td:nth-child(8),
.comparison-panel .compare-table th:nth-child(9),
.comparison-panel .compare-table td:nth-child(9) { background-color: rgba(52, 211, 153, 0.035); }
.rate-cell strong { font-size: 15px; }
.rate-cell small {
  max-width: 160px;
  line-height: 1.45;
}
.diff-cell {
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.01em;
}
.comparison-notes {
  display: grid;
  grid-template-columns: minmax(0, 0.8fr) minmax(0, 1.2fr);
  gap: 10px;
  margin-top: 14px;
}
.comparison-notes .shadow-path,
.outcome-panel > .shadow-path {
  margin: 0;
  padding: 11px 13px;
  border: 1px solid rgba(148, 163, 184, 0.1);
  border-radius: 12px;
  background: rgba(7, 17, 31, 0.38);
  line-height: 1.75;
  word-break: normal;
}
footer {
  margin-top: 22px;
  padding: 16px 4px 0;
  border-top: 1px solid rgba(148, 163, 184, 0.1);
  text-align: center;
}
@media (max-width: 1100px) {
  .shadow-hero { align-items: flex-start; flex-direction: column; }
  .shadow-actions { justify-content: flex-start; }
  .inputs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 680px) {
  .wrap { width: min(100% - 22px, 1580px); margin-top: 11px; }
  .shadow-hero { min-height: auto; padding: 18px; border-radius: 19px; }
  .shadow-brand { align-items: flex-start; min-width: 0; }
  .logo-badge { width: 46px; height: 46px; border-radius: 14px; font-size: 24px; }
  .shadow-actions { width: 100%; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .user-label { grid-column: 1 / -1; }
  .nav-btn { justify-content: center; text-align: center; }
  .shadow-panel { padding: 16px; border-radius: 18px; }
  .section-head { align-items: flex-start; gap: 10px; }
  .inputs { grid-template-columns: 1fr; }
  .model-status-card { min-height: 112px; }
  .comparison-notes { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
}
"""


ESTIMATOR_DASHBOARD_POLISH_CSS = """
html {
  color-scheme: dark;
}
body {
  overflow-x: hidden;
  background:
    radial-gradient(circle at 88% 2%, rgba(245, 158, 11, 0.15), transparent 27rem),
    radial-gradient(circle at 4% 18%, rgba(14, 116, 144, 0.16), transparent 30rem),
    linear-gradient(155deg, #07111f 0%, #0a1425 48%, #080f1b 100%);
}
body::before {
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: 0.16;
  background-image:
    linear-gradient(rgba(148, 163, 184, 0.05) 1px, transparent 1px),
    linear-gradient(90deg, rgba(148, 163, 184, 0.05) 1px, transparent 1px);
  background-size: 44px 44px;
  mask-image: linear-gradient(to bottom, black, transparent 72%);
}
.wrap {
  position: relative;
  width: min(1480px, calc(100% - 40px));
  margin: 24px auto 48px;
}
.dashboard-hero {
  position: relative;
  isolation: isolate;
  overflow: hidden;
  min-height: 142px;
  padding: 22px 24px;
  border: 1px solid rgba(246, 196, 83, 0.22);
  border-radius: 24px;
  background: linear-gradient(110deg, rgba(17, 31, 51, 0.98), rgba(11, 24, 43, 0.9));
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.28), inset 0 1px rgba(255, 255, 255, 0.04);
}
.dashboard-hero::after {
  content: "";
  position: absolute;
  z-index: -1;
  width: 390px;
  height: 390px;
  inset: -250px -70px auto auto;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(246, 196, 83, 0.28), transparent 68%);
}
.header-brand { min-width: 315px; gap: 16px; }
.logo-badge {
  flex: 0 0 auto;
  width: 58px;
  height: 58px;
  border-radius: 18px;
  color: #f6c453;
  background: linear-gradient(145deg, rgba(245, 158, 11, 0.23), rgba(14, 116, 144, 0.2));
  border-color: rgba(246, 196, 83, 0.42);
  box-shadow: 0 16px 34px rgba(245, 158, 11, 0.13);
  font-size: 28px;
}
.brand-copy { min-width: 0; }
.brand-kicker,
.section-kicker {
  display: block;
  color: #67e8f9;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.06em;
}
h1 {
  margin: 2px 0 7px;
  font-size: clamp(20px, 2vw, 28px);
  line-height: 1.35;
  background: none;
  color: #f8fafc;
  -webkit-text-fill-color: currentColor;
}
h1 span { color: #f6c453; }
.status-pill {
  gap: 7px;
  padding: 5px 10px;
  background: rgba(16, 185, 129, 0.1);
  border-color: rgba(52, 211, 153, 0.24);
  font-size: 11px;
}
.status-dot { box-shadow: 0 0 12px rgba(52, 211, 153, 0.75); }
.status-pill.warning {
  color: #fda4af;
  border-color: rgba(251, 113, 133, 0.25);
  background: rgba(225, 29, 72, 0.1);
}
.status-pill.warning .status-dot {
  background: #fb7185;
  box-shadow: 0 0 12px rgba(251, 113, 133, 0.7);
}
.meta {
  flex: 1;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 12px;
}
#freshness-content { min-width: 270px; }
.meta-time {
  display: grid;
  gap: 2px;
  padding: 9px 12px;
  border: 1px solid rgba(148, 163, 184, 0.13);
  border-radius: 13px;
  color: var(--text-sub);
  background: rgba(7, 17, 31, 0.42);
  text-align: right;
}
.meta-time span { font-size: 10px; color: #67e8f9; }
.meta-time strong { color: #e2e8f0; font-size: 12px; font-weight: 750; }
.meta-time small { color: #7f91a8; font-size: 10px; }
.header-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.user-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 8px 11px;
  border: 1px solid rgba(148, 163, 184, 0.13);
  border-radius: 12px;
  color: var(--text-sub);
  background: rgba(7, 17, 31, 0.42);
  font-size: 12px;
}
.user-label strong { color: var(--text-main); }
.user-avatar { color: #f6c453; font-size: 10px; }
.nav-btn {
  min-height: 38px;
  padding: 9px 14px;
  border-radius: 11px;
  background: linear-gradient(135deg, #d99622, #b86e12);
  color: #fff8e7;
  border: 1px solid rgba(251, 191, 36, 0.22);
  box-shadow: 0 10px 24px rgba(217, 150, 34, 0.17);
  transition: transform 160ms ease, border-color 160ms ease, box-shadow 160ms ease;
}
.nav-btn.secondary {
  color: #dce8f5;
  background: rgba(15, 31, 52, 0.72);
  border-color: rgba(148, 163, 184, 0.15);
}
.nav-btn:hover {
  transform: translateY(-1px);
  border-color: rgba(246, 196, 83, 0.52);
  box-shadow: 0 12px 28px rgba(217, 150, 34, 0.2);
}
.nav-btn:focus-visible,
button:focus-visible,
input:focus-visible,
select:focus-visible,
textarea:focus-visible {
  outline: 3px solid rgba(103, 232, 249, 0.22);
  outline-offset: 2px;
}
.surface-panel,
section,
.group-control-card {
  border: 1px solid rgba(148, 163, 184, 0.12);
  border-radius: 22px;
  background: linear-gradient(155deg, rgba(17, 31, 51, 0.94), rgba(12, 24, 42, 0.94));
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.22), inset 0 1px rgba(255, 255, 255, 0.03);
}
.market-pulse {
  padding: 20px;
  border-top-color: rgba(246, 196, 83, 0.42);
}
.market-pulse .section-head { margin-bottom: 15px; }
.section-head { gap: 12px; }
.section-head h2 {
  margin-top: 2px;
  font-size: 18px;
  line-height: 1.45;
}
.badge {
  flex: 0 0 auto;
  padding: 5px 11px;
  color: #67e8f9;
  border-color: rgba(34, 211, 238, 0.22);
  background: rgba(8, 145, 178, 0.1);
}
.badge-live { color: #6ee7b7; border-color: rgba(52, 211, 153, 0.24); background: rgba(16, 185, 129, 0.1); }
.badge-live span { display: inline-block; margin-left: 4px; font-size: 8px; }
.top-ticker { margin: 0; }
.inputs {
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 10px;
}
.input-card {
  position: relative;
  overflow: hidden;
  justify-content: space-between;
  min-height: 122px;
  padding: 14px 15px;
  border-color: rgba(148, 163, 184, 0.12);
  border-radius: 16px;
  background: rgba(7, 17, 31, 0.52);
  box-shadow: inset 0 1px rgba(255, 255, 255, 0.025);
}
.input-card::after {
  content: "";
  position: absolute;
  width: 72px;
  height: 72px;
  inset: auto auto -44px -28px;
  border-radius: 50%;
  background: rgba(103, 232, 249, 0.055);
}
.input-card:hover {
  transform: translateY(-1px);
  border-color: rgba(103, 232, 249, 0.26);
}
.input-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.input-icon {
  flex: 0 0 auto;
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 9px;
  color: #f6c453;
  background: rgba(246, 196, 83, 0.1);
  font-size: 13px;
}
.input-card strong {
  margin: 6px 0 3px;
  font-size: clamp(18px, 1.55vw, 23px);
  font-variant-numeric: tabular-nums;
}
.input-card strong .settlement-value {
  color: #93a7be;
  font-size: 10px;
  font-weight: 800;
}
.input-card.observed {
  border-color: rgba(52, 211, 153, 0.19);
  background: linear-gradient(155deg, rgba(16, 185, 129, 0.085), rgba(7, 17, 31, 0.52));
}
.input-card.estimated {
  border-color: rgba(246, 196, 83, 0.23);
  background: linear-gradient(155deg, rgba(245, 158, 11, 0.09), rgba(7, 17, 31, 0.52));
}
.input-card.no-data { border-style: dashed; }
.input-health-panel {
  margin-top: 4px;
  padding: 16px;
  border-radius: 17px;
  background: rgba(5, 14, 27, 0.52);
  box-shadow: none;
}
.input-health-panel.health-healthy { border-color: rgba(52, 211, 153, 0.22); }
.input-health-panel.health-degraded { border-color: rgba(251, 191, 36, 0.3); }
.input-health-panel.health-critical { border-color: rgba(251, 113, 133, 0.38); }
.input-health-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.input-health-head h3 { margin: 2px 0 0; font-size: 15px; }
.input-health-panel > p { margin: 6px 0 12px; color: var(--text-sub); font-size: 11px; }
.health-summary {
  padding: 5px 10px;
  border-radius: 999px;
  color: #6ee7b7;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(52, 211, 153, 0.22);
  font-size: 11px;
  font-weight: 800;
}
.health-degraded > .input-health-head .health-summary {
  color: #fde68a; background: rgba(245, 158, 11, 0.1); border-color: rgba(251, 191, 36, 0.24);
}
.health-critical > .input-health-head .health-summary {
  color: #fda4af; background: rgba(225, 29, 72, 0.1); border-color: rgba(251, 113, 133, 0.28);
}
.health-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 8px; }
.health-card {
  display: grid;
  gap: 3px;
  min-width: 0;
  padding: 10px 11px;
  border: 1px solid rgba(148, 163, 184, 0.1);
  border-radius: 12px;
  background: rgba(15, 29, 48, 0.62);
}
.health-card span, .health-card small { color: var(--text-sub); font-size: 10px; }
.health-card strong { color: #6ee7b7; font-size: 12px; }
.health-card.health-degraded strong,
.health-card.health-available_proxy strong,
.health-card.health-partial strong,
.health-card.health-historical_only strong,
.health-card.health-excluded_by_contract strong,
.health-card.health-quiet_or_no_data strong { color: #fde68a; }
.health-card.health-critical strong,
.health-card.health-no_data strong,
.health-card.health-stale strong { color: #fda4af; }
.model-input-audit {
  margin-top: 12px;
  padding: 16px;
  border: 1px solid rgba(103, 232, 249, 0.17);
  border-radius: 17px;
  background: rgba(5, 14, 27, 0.56);
}
.model-input-audit > p {
  margin: -5px 0 13px;
  color: var(--text-sub);
  font-size: 11px;
  line-height: 1.8;
}
.model-input-audit .section-head h3 {
  margin: 2px 0 0;
  color: #e8f0f9;
  font-size: 15px;
}
.model-input-books {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.model-input-book {
  min-width: 0;
}
.model-input-book h3 {
  margin: 0 0 8px;
  color: #67e8f9;
  font-size: 13px;
}
.input-audit-table {
  min-width: 880px;
  white-space: normal;
}
.input-audit-table th,
.input-audit-table td {
  vertical-align: top;
  padding: 10px;
  font-size: 10px;
  line-height: 1.65;
}
.input-audit-table td > small,
.input-audit-table td > code {
  display: block;
  margin-top: 3px;
  color: #7f91a8;
  font-size: 9px;
  overflow-wrap: anywhere;
  white-space: normal;
}
.audit-value {
  display: block;
  color: #f4cf74;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.audit-status {
  display: inline-block;
  padding: 3px 7px;
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 999px;
  color: #aebed2;
  white-space: nowrap;
}
.audit-observed { color: #6ee7b7; border-color: rgba(52, 211, 153, 0.24); }
.audit-estimated { color: #fde68a; border-color: rgba(251, 191, 36, 0.26); }
.audit-excluded { color: #fde68a; border-color: rgba(251, 191, 36, 0.26); }
.audit-no_data { color: #fda4af; border-color: rgba(251, 113, 133, 0.25); }
.group-model-audit { border-color: rgba(246, 196, 83, 0.18); }
.group-audit-table { min-width: 760px; }
.group-control-card {
  position: relative;
  padding: 18px 20px;
  border-radius: 19px;
  box-shadow: 0 14px 36px rgba(0, 0, 0, 0.18);
}
.group-control-copy { gap: 5px; }
.group-control-copy .section-head { margin-bottom: 4px; }
.group-control-copy strong { color: #e5edf6; font-size: 13px; }
.group-control-copy small { line-height: 1.65; }
.dashboard-grid {
  grid-template-columns: minmax(0, 1.15fr) minmax(420px, 0.85fr);
  gap: 18px;
}
.dashboard-grid section { margin-bottom: 0; }
.table-section { padding: 20px; border-top-color: rgba(103, 232, 249, 0.26); }
.table-wrap {
  overflow: auto;
  border-color: rgba(148, 163, 184, 0.11);
  border-radius: 14px;
  background: rgba(6, 14, 26, 0.4);
  scrollbar-color: rgba(246, 196, 83, 0.35) rgba(15, 23, 42, 0.45);
  scrollbar-width: thin;
}
th, td { padding: 13px 14px; border-bottom-color: rgba(148, 163, 184, 0.09); }
th {
  color: #aebed2;
  background: rgba(8, 19, 34, 0.97);
  position: sticky;
  top: 0;
  z-index: 1;
}
tbody tr:nth-child(even) td { background: rgba(148, 163, 184, 0.018); }
tbody tr:hover td { background: rgba(103, 232, 249, 0.035); }
.table-section tbody td:first-child strong { color: #e6edf5; font-size: 14px; }
.rate-cell { min-width: 190px; }
.rate-cell.cash { background: rgba(52, 211, 153, 0.025); }
.rate-cell.tomorrow { background: rgba(96, 165, 250, 0.025); }
.rate-cell strong { color: #f6c453; font-size: 18px; font-variant-numeric: tabular-nums; }
.rate-cell small { margin-top: 3px; color: #7f91a8; }
.side-column section { padding: 20px; }
.group-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.feed-card {
  min-width: 0;
  padding: 13px;
  border-color: rgba(148, 163, 184, 0.1);
  border-radius: 14px;
  background: rgba(7, 17, 31, 0.48);
}
.feed-card h3 { margin: 0 0 7px; color: #f6c453; line-height: 1.6; }
.feed-card li { line-height: 1.75; border-top-color: rgba(148, 163, 184, 0.08); }
.activity-freshness { line-height: 1.6; }
footer { text-align: center; border-top-color: rgba(148, 163, 184, 0.1); }
@media (max-width: 1180px) {
  .dashboard-hero { align-items: flex-start; flex-direction: column; }
  .meta { width: 100%; justify-content: space-between; }
  .dashboard-grid { grid-template-columns: 1fr; }
  .side-column .group-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .model-input-books { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .wrap { width: min(100% - 22px, 1480px); margin-top: 11px; }
  .dashboard-hero { min-height: auto; padding: 18px; border-radius: 19px; }
  .header-brand { min-width: 0; align-items: flex-start; }
  .logo-badge { width: 46px; height: 46px; border-radius: 14px; font-size: 22px; }
  .meta, #freshness-content { width: 100%; min-width: 0; }
  .header-actions { width: 100%; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .user-label { grid-column: 1 / -1; }
  .nav-btn { justify-content: center; text-align: center; }
  .market-pulse, .table-section, .side-column section { padding: 16px; border-radius: 18px; }
  .inputs { display: flex; overflow-x: auto; scroll-snap-type: x proximity; padding-bottom: 5px; }
  .input-card { flex: 0 0 min(76vw, 235px); min-height: 112px; scroll-snap-align: start; }
  .group-control-card { align-items: stretch; padding: 16px; }
  .group-control-card form, .group-control-card .nav-btn { width: 100%; }
  .side-column .group-grid { grid-template-columns: 1fr; }
  .section-head { align-items: flex-start; }
}
@media (max-width: 460px) {
  .header-actions { grid-template-columns: 1fr; }
  .user-label { grid-column: auto; }
  .section-head { flex-direction: column; }
  .badge { align-self: flex-start; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; animation: none !important; transition: none !important; }
}
"""


def render_shadow_page(
    live_state: dict[str, Any],
    *,
    home_path: str,
    shadow_path: str,
    shadow_data_path: str,
    shadow_estimate_path: str,
    logout_path: str = "/logout",
    user_session: str | None = None,
    shadow_state_path: Path | None = None,
    research_shadow_state_path: Path | None = None,
    research_shadow_data_path: str | None = None,
    ml_shadow_state_path: Path | None = None,
    ml_shadow_data_path: str | None = None,
    estimate_fragment: bool = False,
) -> bytes:
    """Operator page: one unified table for live + all shadows."""

    previous_payload = load_shadow_dashboard_payload(
        shadow_state_path or DEFAULT_SHADOW_STATE
    )
    research_path = research_shadow_state_path or DEFAULT_RESEARCH_SHADOW_STATE
    research_payload = load_shadow_dashboard_payload(research_path)
    ml_path = ml_shadow_state_path or DEFAULT_ML_SHADOW_STATE_PATH
    ml_payload = load_shadow_dashboard_payload(ml_path)

    model_specs = [
        ("سایه ۱ — مدل قبلی", previous_payload, "rgba(245,158,11,0.45)"),
        ("سایه ۲ — بازگشایی صبح", research_payload, "rgba(6,182,212,0.45)"),
        ("سایه ۳ — یادگیری ماشین", ml_payload, "rgba(16,185,129,0.45)"),
    ]
    live_window = fa_datetime(live_state.get("window_end_utc") if isinstance(live_state, dict) else None)
    live_card = f"""
    <article class="input-card model-status-card live-model-card" style="--model-accent:#f6c453">
      <span class="model-label">مدل اصلی (مرجع)</span>
      <strong>زنده</strong>
      <small>پایهٔ مقایسهٔ جدول</small>
      <small>پنجره: {html.escape(live_window)}</small>
    </article>
    """
    status_cards = live_card + "".join(
        _shadow_status_card(title=title, payload=payload, accent=accent)
        for title, payload, accent in model_specs
    )
    table_rows = render_unified_shadow_compare_table(
        live_state if isinstance(live_state, dict) else {},
        [(title, payload) for title, payload, _ in model_specs],
    )
    header_cols = "".join(
        f"<th>{html.escape(title)}</th><th>اختلاف با اصلی</th>"
        for title, _, _ in model_specs
    )
    panels = f"""
    <section class="shadow-panel comparison-panel">
      <div class="section-head">
        <div>
          <h2>جدول مقایسهٔ یکپارچه</h2>
          <p class="shadow-subtitle">مدل اصلی و هر سه سایه در یک جدول — دادهٔ زندهٔ یکسان</p>
        </div>
        <span class="badge">۴ ستون نرخ</span>
      </div>
      <div class="inputs">{status_cards}</div>
      <div class="table-wrap">
        <table class="compare-table">
          <thead>
            <tr>
              <th>نوع کالا</th>
              <th>تسویه</th>
              <th>مدل اصلی</th>
              {header_cols}
            </tr>
          </thead>
          <tbody>{table_rows}</tbody>
        </table>
      </div>
      <div class="comparison-notes">
        <p class="shadow-path">اختلاف = (سایه − اصلی) ÷ اصلی · علامت مثبت یعنی سایه بالاتر از اصلی است.</p>
        <p class="shadow-path"><strong>این ستون معیار دقت نیست.</strong> فقط واگرایی از کتاب
        اصلیِ کالیبره‌شده را نشان می‌دهد؛ اگر مدل اصلی خطا داشته باشد، سایهٔ دقیق‌تر
        «اختلاف بیشتر» نشان می‌دهد. تنها معیار معتبر برای ارتقا، ارزیابی هر مدل در
        برابر معاملهٔ واقعی است که در جدول زیر می‌آید.</p>
      </div>
    </section>
    {render_model_outcome_panel(live_state if isinstance(live_state, dict) else {})}
    """
    if estimate_fragment:
        return f"""
        <div id="table-fragment">{panels}</div>
        """.encode("utf-8")

    user_badge = (
        f"<span class='user-label'><span class='user-avatar' aria-hidden='true'>◉</span>"
        f"<span>کاربر <strong>{html.escape(user_session or 'bahar')}</strong></span></span>"
    )
    research_link = ""
    if research_shadow_data_path:
        research_link = (
            f"<a class='nav-btn secondary' href='{html.escape(research_shadow_data_path)}'>"
            f"JSON سایه ۲</a> "
        )
    ml_link = ""
    if ml_shadow_data_path:
        ml_link = (
            f"<a class='nav-btn secondary' href='{html.escape(ml_shadow_data_path)}'>"
            f"JSON سایه ۳</a> "
        )
    navigation = (
        f"<nav class='shadow-actions' aria-label='ناوبری مدل‌های سایه'>{user_badge} "
        f"<a class='nav-btn secondary' href='{html.escape(home_path)}'>بازگشت به مدل اصلی</a> "
        f"<a class='nav-btn secondary' href='{html.escape(shadow_data_path)}'>JSON سایه ۱</a> "
        f"{research_link}"
        f"{ml_link}"
        f"<a class='nav-btn secondary' href='{html.escape(logout_path)}'>خروج</a></nav>"
    )
    document = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>پایش مدل‌های سایه</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root {{
  --bg-deep: #0b1329; --bg-surface: rgba(15, 23, 42, 0.85); --bg-card: #141f36;
  --border-line: rgba(255, 255, 255, 0.08); --border-gold: rgba(245, 158, 11, 0.35);
  --text-main: #f8fafc; --text-sub: #94a3b8; --accent-gold: #f59e0b;
  --accent-cyan: #06b6d4; --accent-emerald: #10b981; --accent-rose: #f43f5e;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: radial-gradient(circle at 50% -10%, #312e81 0%, #0b1329 70%);
  color: var(--text-main); font-family: Vazirmatn, system-ui, sans-serif; min-height: 100vh;
}}
.wrap {{ width: min(1680px, 98%); margin: 16px auto 40px; }}
header {{
  display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:16px;
  padding:14px 20px; background:var(--bg-surface); border:1px solid rgba(99,102,241,0.45);
  border-radius:16px;
}}
header h1 {{ margin:0; font-size:20px; }}
header p {{ margin:4px 0 0; color:var(--text-sub); font-size:12px; }}
.nav-btn {{
  display:inline-block; padding:8px 12px; border-radius:10px; text-decoration:none;
  background:rgba(99,102,241,0.2); color:var(--text-main); border:1px solid rgba(99,102,241,0.45);
  font-size:12px; font-weight:700;
}}
.nav-btn.secondary {{ background:rgba(148,163,184,0.12); border-color:var(--border-line); }}
.inputs {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin-bottom:14px; }}
.input-card {{
  background:var(--bg-card); border:1px solid var(--border-line); border-radius:12px; padding:12px;
  display:flex; flex-direction:column; gap:4px;
}}
.input-card span,.input-card small {{ color:var(--text-sub); font-size:11px; }}
.input-card strong {{ font-size:18px; color:var(--accent-gold); direction:ltr; text-align:right; }}
.input-card.no-data strong {{ color:var(--accent-rose); }}
.shadow-panel {{
  background:var(--bg-card); border:1px solid var(--border-line); border-radius:16px;
  padding:16px; margin-bottom:18px;
}}
.section-head {{ display:flex; justify-content:space-between; align-items:flex-start; gap:10px; margin-bottom:10px; }}
.section-head h2 {{ margin:0; font-size:17px; }}
.shadow-subtitle {{ margin:6px 0 0; color:var(--text-sub); font-size:12px; }}
.badge {{
  display:inline-block; padding:3px 10px; border-radius:99px; background:rgba(99,102,241,0.15);
  border:1px solid rgba(99,102,241,0.4); color:#a5b4fc; font-size:11px; font-weight:600;
}}
.table-wrap {{ overflow-x:auto; border-radius:12px; border:1px solid var(--border-line); }}
table {{ width:100%; border-collapse:collapse; white-space:nowrap; }}
th, td {{ padding:10px 12px; border-bottom:1px solid var(--border-line); text-align:right; font-size:12px; }}
th {{ color:var(--text-sub); background:rgba(15,23,42,0.95); position:sticky; top:0; z-index:1; }}
.compare-table th:nth-child(3), .compare-table td.col-live strong {{ color:var(--accent-gold); }}
.compare-table th:nth-child(4), .compare-table th:nth-child(5) {{ color:#fbbf24; }}
.compare-table th:nth-child(6), .compare-table th:nth-child(7) {{ color:#22d3ee; }}
.compare-table th:nth-child(8), .compare-table th:nth-child(9) {{ color:#34d399; }}
.rate-cell strong {{ display:block; font-size:15px; color:var(--accent-gold); direction:ltr; text-align:right; }}
.rate-cell small {{ display:block; color:var(--text-sub); font-size:10px; margin-top:2px; max-width:180px; overflow:hidden; text-overflow:ellipsis; }}
.diff-cell {{ direction:ltr; text-align:right; font-weight:700; }}
.diff-up {{ color:var(--accent-rose); }}
.diff-down {{ color:var(--accent-emerald); }}
.diff-flat {{ color:var(--text-sub); }}
.missing {{ color:var(--accent-rose); }}
.shadow-path {{ margin:12px 0 0; color:var(--text-sub); font-size:11px; word-break:break-all; }}
footer {{ margin-top:18px; color:var(--text-sub); font-size:12px; }}
{SHADOW_DASHBOARD_POLISH_CSS}
</style>
</head>
<body>
<main class="wrap">
  <header class="shadow-hero">
    <div class="shadow-brand">
      <div class="logo-badge" aria-hidden="true">◌</div>
      <div>
      <span class="eyebrow">آزمایشگاه مدل</span>
      <h1>پایش مدل‌های سایه</h1>
      <p>جدول یکپارچهٔ مقایسه — مدل اصلی و سه سایه روی دادهٔ زندهٔ یکسان</p>
      <p>سایه ۱ = مدل قبلی · سایه ۲ = بازگشایی صبح · سایه ۳ = یادگیری ماشین</p>
      </div>
    </div>
    {navigation}
  </header>
  <div id="estimate-content">{panels}</div>
  <footer>مدل اصلی همچنان تنها منبع نرخ نمایشی کاربر است.</footer>
</main>
<script>
async function refreshShadowView() {{
  try {{
    const response = await fetch({json.dumps(shadow_estimate_path)}, {{cache: "no-store"}});
    if (!response.ok) return;
    const htmlText = await response.text();
    const doc = new DOMParser().parseFromString(htmlText, "text/html");
    const newTable = doc.getElementById("table-fragment");
    if (newTable && document.getElementById("estimate-content")) {{
      document.getElementById("estimate-content").innerHTML = newTable.innerHTML;
    }}
  }} catch (_) {{}}
}}
window.setInterval(refreshShadowView, 15000);
</script>
</body>
</html>"""
    return document.encode("utf-8")


def render_page(
    state: dict[str, Any], *, manual_path: str = "/manual-entry", analytics_path: str = "/analytics",
    logout_path: str = "/logout", user_session: str | None = None,
    estimate_path: str = "/estimates.html", activity_path: str = "/activity.html", write_enabled: bool = False,
    flash: str | None = None, open_manual_offers: list[dict[str, Any]] | None = None,
    estimate_fragment: bool = False, page: str = "home",
    market_db: Path | None = None, conversation_db: Path | None = None,
    group_live_control_path: str = "/group-live-control",
    group_live_control: dict[str, Any] | None = None,
    shadow_path: str = "/shadow",
) -> bytes:
    generated = fa_datetime(state.get("generated_at_utc"))
    window_start = fa_datetime(state.get("window_start_utc"))
    window_end = fa_datetime(state.get("window_end_utc"))
    raw_service_status = str(state.get("service_status", "RUNNING"))
    service_status = html.escape(raw_service_status)
    service_status_class = (
        "healthy" if raw_service_status.upper() in {"RUNNING", "READY", "OK"} else "warning"
    )
    settlements = state.get("settlements", {})
    ticker_cards = (
        f"<div class='inputs'>{render_input_cards(settlements)}</div>"
        f"{render_input_health_panel(state.get('input_health'))}"
        f"{render_model_input_audit(settlements)}"
        f"{render_group_model_input_audit(settlements)}"
    )
    table_section = f"""
      <section class="table-section surface-panel">
        <div class='section-head'>
          <div>
            <span class="section-kicker">خروجی مدل اصلی</span>
            <h2>لیست نرخ سکه و مسکوکات</h2>
          </div>
          <span class='badge'>برآورد نقدی و فردایی</span>
        </div>
        <div class='table-wrap'>
          <table>
            <thead>
              <tr>
                <th>نوع کالا</th>
                <th>نرخ نقدی (تومان)</th>
                <th>نرخ فردایی (تومان)</th>
              </tr>
            </thead>
            <tbody>{render_combined_rate_rows(settlements)}</tbody>
          </table>
        </div>
      </section>
    """
    freshness_section = f"""
      <div class="meta-time">
        <span>آخرین بروزرسانی</span>
        <strong>{generated}</strong>
        <small>بازهٔ داده: {window_start} تا {window_end}</small>
      </div>
    """
    if estimate_fragment:
        return f"""
        <div id="freshness-fragment">{freshness_section}</div>
        <div id="ticker-fragment">
          <div class="top-ticker">
            {ticker_cards}
          </div>
        </div>
        <div id="table-fragment">
          {table_section}
        </div>
        """.encode("utf-8")

    manual_panel_html = render_manual_entry_panel(
        state,
        manual_path=manual_path,
        write_enabled=write_enabled,
        flash=flash,
        open_manual_offers=open_manual_offers or [],
    )

    control = group_live_control or {
        "enabled": True,
        "disabled_since_utc": None,
        "changed_at_utc": None,
    }
    group_live_enabled = bool(control.get("enabled", True))
    control_action = "disconnect" if group_live_enabled else "connect"
    control_title = (
        "رویدادهای زندهٔ گروه‌های سکه به مدل متصل هستند"
        if group_live_enabled
        else "رویدادهای زندهٔ گروه‌های سکه از مدل قطع هستند"
    )
    control_detail = (
        "آفرها و معاملات گروه همچنان در سمت راست صفحه نمایش داده می‌شوند."
        if group_live_enabled
        else "رویدادها همچنان ذخیره و نمایش داده می‌شوند؛ با اتصال مجدد همهٔ رویدادهای صف‌شده وارد تخمین می‌شوند."
    )
    control_time = control.get("changed_at_utc")
    group_control_html = f"""
    <section class="group-control-card">
      <div class="group-control-copy">
        <div class="section-head">
          <h2>کنترل ورودی گروه‌های سکه</h2>
          <span class="badge {'group-connected' if group_live_enabled else 'group-disconnected'}">
            {'متصل' if group_live_enabled else 'قطع'}
          </span>
        </div>
        <strong>{html.escape(control_title)}</strong>
        <small>{html.escape(control_detail)}</small>
        <small>داده‌های تاریخی گروه‌ها و آموزش مدل در هر دو حالت فعال باقی می‌مانند.</small>
        {f"<small>آخرین تغییر: {html.escape(fa_datetime(str(control_time)))}</small>" if control_time else ""}
      </div>
      <form method="post" action="{html.escape(group_live_control_path)}">
        <input type="hidden" name="action" value="{control_action}">
        <button class="nav-btn {'secondary' if group_live_enabled else ''}" type="submit">
          {'قطع اتصال رویدادهای زنده' if group_live_enabled else 'اتصال و اعمال رویدادهای صف‌شده'}
        </button>
      </form>
    </section>
    """

    user_badge = (
        f"<span class='user-label'><span class='user-avatar' aria-hidden='true'>◉</span>"
        f"<span>کاربر <strong>{html.escape(user_session or 'bahar')}</strong></span></span>"
    )
    logout_btn = f"<a class='nav-btn secondary' href='{html.escape(logout_path)}'>خروج</a>"

    if page == "manual":
        navigation = (
            f"<nav class='header-actions' aria-label='ناوبری داشبورد'>{user_badge} "
            f"<a class='nav-btn secondary' href='{html.escape('/' + manual_path.strip('/').rsplit('/', 1)[0])}'>"
            f"بازگشت به داشبورد</a> {logout_btn}</nav>"
        )
        page_content = manual_panel_html
        refresh_script = ""
    else:
        navigation = (
            f"<nav class='header-actions' aria-label='ناوبری داشبورد'>{user_badge} "
            f"<a class='nav-btn secondary' href='{html.escape(shadow_path)}'>مدل‌های سایه</a> "
            f"<a class='nav-btn secondary' href='{html.escape(analytics_path + '#parser-review-ledger')}'>بازبینی parser</a> "
            f"<a class='nav-btn' href='{html.escape(manual_path)}'>ثبت دستی آفر</a> "
            f"{logout_btn}</nav>"
        )
        page_content = f"""
        <section class="market-pulse surface-panel" aria-labelledby="market-pulse-title">
          <div class="section-head">
            <div>
              <span class="section-kicker">ورودی‌های زنده</span>
              <h2 id="market-pulse-title">نبض بازار</h2>
            </div>
            <span class="badge badge-live"><span aria-hidden="true">●</span> جریان داده</span>
          </div>
          <div id="ticker-content">
            <div class="top-ticker">{ticker_cards}</div>
          </div>
        </section>
        {group_control_html}
        <div class="dashboard-grid">
          <div class="main-column">
            <div id="estimate-content">{table_section}</div>
          </div>
          <div class="side-column">
            <div id="activity-content">{render_group_activity_fragment(conversation_db, input_health=state.get('input_health')) if conversation_db else ''}</div>
          </div>
        </div>"""
        refresh_script = "window.setInterval(refreshEstimateView, 15000); window.setInterval(refreshActivityView, 15000);"

    document = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>سامانه تخمین و تحلیل نرخ سکه</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root {{
  --bg-deep: #0b1329;
  --bg-surface: rgba(15, 23, 42, 0.85);
  --bg-card: #141f36;
  --border-line: rgba(255, 255, 255, 0.08);
  --border-gold: rgba(245, 158, 11, 0.35);
  --border-gold-glow: 0 0 15px rgba(245, 158, 11, 0.12);
  --text-main: #f8fafc;
  --text-sub: #94a3b8;
  --accent-gold: #f59e0b;
  --accent-cyan: #06b6d4;
  --accent-emerald: #10b981;
  --accent-rose: #f43f5e;
  --accent-indigo: #6366f1;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: radial-gradient(circle at 50% -10%, #1e1b4b 0%, #0b1329 70%);
  color: var(--text-main);
  font-family: Vazirmatn, system-ui, -apple-system, sans-serif;
  line-height: 1.5;
  min-height: 100vh;
}}
.wrap {{
  width: min(1440px, 96%);
  margin: 16px auto 40px;
}}
header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
  padding: 14px 20px;
  background: var(--bg-surface);
  backdrop-filter: blur(20px);
  border: 1px solid var(--border-gold);
  box-shadow: var(--border-gold-glow);
  border-radius: 16px;
}}
.header-brand {{
  display: flex;
  align-items: center;
  gap: 12px;
}}
.logo-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.25), rgba(99, 102, 241, 0.25));
  border: 1px solid var(--accent-gold);
  font-size: 20px;
}}
h1 {{
  margin: 0;
  font-size: 20px;
  font-weight: 800;
  background: linear-gradient(135deg, #ffffff 40%, var(--accent-gold) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
.status-pill {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 99px;
  background: rgba(16, 185, 129, 0.12);
  border: 1px solid rgba(16, 185, 129, 0.3);
  color: var(--accent-emerald);
  font-size: 12px;
  font-weight: 600;
}}
.status-dot {{
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--accent-emerald);
  box-shadow: 0 0 8px var(--accent-emerald);
  animation: pulse 2s infinite;
}}
@keyframes pulse {{
  0% {{ opacity: 0.6; transform: scale(0.95); }}
  50% {{ opacity: 1; transform: scale(1.15); }}
  100% {{ opacity: 0.6; transform: scale(0.95); }}
}}
.meta {{
  display: flex;
  align-items: center;
  gap: 16px;
}}
.meta-time {{
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1.4;
  text-align: left;
}}
.nav-btn {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-indigo), #4f46e5);
  color: #ffffff;
  font-weight: 700;
  font-size: 13px;
  text-decoration: none;
  box-shadow: 0 6px 18px rgba(99, 102, 241, 0.3);
  transition: all 0.2s ease;
  border: none;
  cursor: pointer;
}}
.nav-btn:hover {{
  transform: translateY(-2px);
  box-shadow: 0 10px 22px rgba(99, 102, 241, 0.4);
}}
.nav-btn.secondary {{
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--border-line);
  color: var(--text-main);
  box-shadow: none;
}}

/* Top full-width horizontal ticker strip */
.top-ticker {{
  margin-bottom: 16px;
}}
.inputs {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}}
.input-card {{
  display: flex;
  flex-direction: column;
  padding: 12px 14px;
  border-radius: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-gold);
  box-shadow: var(--border-gold-glow);
  transition: all 0.2s ease;
  min-width: 0;
}}
.input-card:hover {{
  transform: translateY(-2px);
  border-color: var(--accent-gold);
}}
.input-card span {{
  color: var(--text-sub);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.input-card strong {{
  font-size: 19px;
  font-weight: 800;
  margin: 3px 0;
  color: var(--text-main);
  direction: ltr;
  text-align: right;
}}
.input-card small {{
  color: var(--text-sub);
  font-size: 11px;
  opacity: 0.85;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.input-card.observed {{
  border-color: rgba(16, 185, 129, 0.4);
  background: linear-gradient(180deg, rgba(16, 185, 129, 0.08) 0%, var(--bg-card) 100%);
}}
.input-card.observed strong {{
  color: var(--accent-emerald);
}}
.input-card.estimated {{
  border-color: var(--border-gold);
  background: linear-gradient(180deg, rgba(245, 158, 11, 0.08) 0%, var(--bg-card) 100%);
}}
.input-card.estimated strong {{
  color: var(--accent-gold);
}}
.input-card.no-data strong, .missing {{
  color: var(--accent-rose);
}}
.group-control-card {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  background: var(--bg-surface);
  border: 1px solid var(--border-line);
  border-radius: 16px;
  padding: 16px 18px;
  margin-bottom: 16px;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.20);
}}
.group-control-copy {{
  display: flex;
  flex-direction: column;
  gap: 3px;
}}
.group-control-copy .section-head {{ margin-bottom: 3px; }}
.group-control-copy strong {{ font-size: 13px; }}
.group-control-copy small {{ color: var(--text-sub); font-size: 11px; }}
.group-connected {{
  color: var(--accent-emerald);
  border-color: rgba(16, 185, 129, 0.35);
  background: rgba(16, 185, 129, 0.12);
}}
.group-disconnected {{
  color: var(--accent-rose);
  border-color: rgba(244, 63, 94, 0.35);
  background: rgba(244, 63, 94, 0.12);
}}

/* 2-Column Split Dashboard Layout */
.dashboard-grid {{
  display: grid;
  grid-template-columns: minmax(0, 1.75fr) minmax(320px, 1.25fr);
  gap: 16px;
  align-items: start;
}}
section {{
  background: var(--bg-surface);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border-line);
  border-radius: 16px;
  padding: 18px;
  margin-bottom: 16px;
  box-shadow: 0 12px 30px rgba(0, 0, 0, 0.25);
}}
.section-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}}
h2 {{
  margin: 0;
  font-size: 16px;
  font-weight: 800;
  color: var(--text-main);
  display: flex;
  align-items: center;
  gap: 8px;
}}
.badge {{
  display: inline-block;
  padding: 3px 10px;
  border-radius: 99px;
  background: rgba(6, 182, 212, 0.12);
  border: 1px solid rgba(6, 182, 212, 0.3);
  color: var(--accent-cyan);
  font-size: 11px;
  font-weight: 600;
}}
.table-wrap {{
  overflow-x: auto;
  border-radius: 12px;
  border: 1px solid var(--border-line);
}}
table {{
  width: 100%;
  border-collapse: collapse;
  white-space: nowrap;
}}
th {{
  background: rgba(15, 23, 42, 0.95);
  padding: 12px 14px;
  color: var(--text-sub);
  font-size: 12px;
  font-weight: 700;
  text-align: right;
  border-bottom: 1px solid var(--border-line);
}}
td {{
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-line);
  font-size: 13.5px;
}}
tr:last-child td {{
  border-bottom: none;
}}
tr:hover td {{
  background: rgba(255, 255, 255, 0.03);
}}
.rate-cell {{
  min-width: 180px;
}}
.rate-cell strong {{
  display: block;
  font-size: 17px;
  font-weight: 800;
  color: var(--accent-gold);
  direction: ltr;
  text-align: right;
}}
.rate-cell small {{
  display: block;
  color: var(--text-sub);
  font-size: 11px;
  margin-top: 1px;
  direction: ltr;
  text-align: right;
}}

/* Manual Form & Activity Layout */
.manual-section {{
  display: flex;
  flex-direction: column;
  gap: 14px;
}}
.manual-form {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  padding: 16px;
  border-radius: 14px;
  background: var(--bg-card);
  border: 1px solid var(--border-gold);
}}
.form-title {{
  margin: 0;
  grid-column: 1 / -1;
  font-size: 14px;
  font-weight: 800;
  color: var(--accent-gold);
  border-bottom: 1px solid var(--border-line);
  padding-bottom: 6px;
}}
.manual-form label {{
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: var(--text-sub);
  font-size: 12px;
  font-weight: 600;
}}
.manual-form input, .manual-form select, .manual-form textarea {{
  width: 100%;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--border-line);
  background: rgba(15, 23, 42, 0.85);
  color: var(--text-main);
  font-family: inherit;
  font-size: 13px;
  transition: all 0.2s ease;
}}
.manual-form input:focus, .manual-form select:focus, .manual-form textarea:focus {{
  outline: none;
  border-color: var(--accent-indigo);
  box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.25);
}}
.manual-form textarea {{
  resize: vertical;
}}
.manual-form .check {{
  flex-direction: row;
  align-items: center;
  gap: 6px;
  color: var(--text-main);
}}
.manual-form .check input {{
  width: auto;
}}
.manual-form .wide {{
  grid-column: 1 / -1;
}}
button {{
  padding: 10px 16px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--accent-indigo), #4f46e5);
  color: #ffffff;
  font-weight: 700;
  font-family: inherit;
  font-size: 13px;
  border: none;
  cursor: pointer;
  transition: all 0.2s ease;
}}
button:hover {{
  opacity: 0.92;
  transform: translateY(-1px);
}}
button.secondary {{
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--border-line);
  color: var(--accent-cyan);
}}
button:disabled, input:disabled, select:disabled, textarea:disabled {{
  opacity: 0.5;
  cursor: not-allowed;
  transform: none !important;
}}
.manual-help {{
  margin: 0;
  color: var(--text-sub);
  font-size: 11px;
  line-height: 1.5;
}}
.manual-effect {{
  border: 1px solid var(--border-line);
  border-radius: 14px;
  padding: 14px;
  background: var(--bg-card);
}}
.manual-effect h3 {{
  margin: 0 0 6px;
  color: var(--accent-cyan);
  font-size: 14px;
}}
.effect-grid {{
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 10px;
}}
.effect-card {{
  border-top: 1px solid var(--border-line);
  padding-top: 8px;
}}
.effect-card h3 {{
  font-size: 13px;
  color: var(--accent-gold);
}}
.effect-card ul {{
  padding: 0;
  margin: 0;
  list-style: none;
}}
.effect-card li {{
  padding: 4px 0;
  font-size: 12px;
}}
.effect-card small {{
  color: var(--text-sub);
}}
.flash {{
  padding: 10px 14px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 12px;
}}
.flash.success {{
  background: rgba(16, 185, 129, 0.15);
  border: 1px solid rgba(16, 185, 129, 0.3);
  color: #6ee7b7;
}}
.flash.warning {{
  background: rgba(244, 63, 94, 0.15);
  border: 1px solid rgba(244, 63, 94, 0.3);
  color: #fda4af;
}}
.group-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px;
}}
.feed-card {{
  background: var(--bg-card);
  border: 1px solid var(--border-line);
  border-radius: 12px;
  padding: 14px;
}}
.feed-card h3 {{
  font-size: 13px;
  color: var(--accent-gold);
  margin-bottom: 8px;
}}
.feed-card ul {{
  list-style: none;
  margin: 0;
  padding: 0;
}}
.feed-card li {{
  padding: 6px 0;
  border-top: 1px solid var(--border-line);
  font-size: 12px;
}}
.feed-card time, .feed-card small {{
  color: var(--text-sub);
  font-size: 11px;
}}
.activity-freshness {{
  display: block;
  margin: -3px 0 6px;
  font-size: 11px;
}}
.activity-freshness.fresh {{ color: var(--accent-emerald); }}
.activity-freshness.stale {{ color: var(--accent-rose); }}
.fact-status {{
  display: inline-flex;
  margin-inline-start: 4px;
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px solid currentColor;
  font-size: 10px !important;
}}
.fact-status.model {{ color: var(--accent-emerald) !important; }}
.fact-status.audit {{ color: var(--accent-gold) !important; }}
.fact-status.review {{ color: var(--accent-rose) !important; }}
.activity-scope-note {{
  margin: -3px 0 12px;
  color: var(--text-sub);
  font-size: 11px;
  line-height: 1.75;
}}
.group-runtime-summary {{
  margin: 0 0 14px;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-strong) 84%, transparent);
}}
.group-runtime-summary h3 {{ margin: 0 0 4px; font-size: 13px; }}
.group-runtime-summary > p {{ margin: 0 0 10px; color: var(--text-sub); font-size: 11px; line-height: 1.7; }}
.group-runtime-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
.group-runtime-card {{ display: grid; gap: 5px; padding: 10px; border-radius: 11px; background: var(--surface); }}
.group-runtime-card > div {{ display: flex; justify-content: space-between; gap: 8px; align-items: center; }}
.group-runtime-card small {{ color: var(--text-sub); line-height: 1.65; }}
@media (max-width: 680px) {{ .group-runtime-grid {{ grid-template-columns: 1fr; }} }}
footer {{
  color: var(--text-sub);
  font-size: 12px;
  padding: 16px 4px 0;
  border-top: 1px solid var(--border-line);
  margin-top: 24px;
  line-height: 1.6;
}}
footer code {{
  background: rgba(255, 255, 255, 0.08);
  padding: 2px 6px;
  border-radius: 4px;
  color: var(--accent-gold);
  font-family: monospace;
}}

@media (max-width: 1024px) {{
  .dashboard-grid {{
    grid-template-columns: 1fr;
  }}
  .group-control-card {{
    align-items: flex-start;
    flex-direction: column;
  }}
}}
@media (max-width: 850px) {{
  header {{
    flex-direction: column;
    align-items: flex-start;
  }}
  .meta {{
    width: 100%;
    justify-content: space-between;
  }}
}}
{ESTIMATOR_DASHBOARD_POLISH_CSS}
</style>
</head>
<body>
<main class="wrap">
  <header class="dashboard-hero">
    <div class="header-brand">
      <div class="logo-badge" aria-hidden="true">◈</div>
      <div class="brand-copy">
        <span class="brand-kicker">مرکز پایش بازار</span>
        <h1>سامانه <span>تخمین نرخ سکه</span></h1>
        <div class="status-pill {service_status_class}">
          <span class="status-dot"></span>
          وضعیت سرویس: {service_status}
        </div>
      </div>
    </div>
    <div class="meta">
      <div id="freshness-content">{freshness_section}</div>
      {navigation}
    </div>
  </header>
  {page_content}
  <footer>
    این سامانه یک ابزار تحلیل و برآورد هوشمند است و توصیه خرید یا فروش نیست.
    مقدار <code>{NO_DATA_TOKEN}</code> نشان‌دهنده عدم وجود داده واقعی در دقیقه جاری است.
  </footer>
</main>
<script>
async function refreshEstimateView() {{
  try {{
    const response = await fetch({json.dumps(estimate_path)}, {{cache: "no-store"}});
    if (!response.ok) return;
    const html = await response.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");
    const newTicker = doc.getElementById("ticker-fragment");
    const newTable = doc.getElementById("table-fragment");
    const newFreshness = doc.getElementById("freshness-fragment");
    if (newFreshness && document.getElementById("freshness-content")) {{
      document.getElementById("freshness-content").innerHTML = newFreshness.innerHTML;
    }}
    if (newTicker && document.getElementById("ticker-content")) {{
      document.getElementById("ticker-content").innerHTML = newTicker.innerHTML;
    }}
    if (newTable && document.getElementById("estimate-content")) {{
      document.getElementById("estimate-content").innerHTML = newTable.innerHTML;
    }}
  }} catch (_) {{}}
}}
async function refreshActivityView() {{
  try {{
    const response = await fetch({json.dumps(activity_path)}, {{cache: "no-store"}});
    if (response.ok) document.getElementById("activity-content").innerHTML = await response.text();
  }} catch (_) {{}}
}}
{refresh_script}
document.getElementById("parse-offer-text")?.addEventListener("click", async () => {{
  const raw = document.getElementById("raw-offer-text");
  if (!raw?.value.trim()) return;
  try {{
    const response = await fetch({json.dumps(manual_path + "/parse-text")}, {{
      method: "POST", headers: {{"Content-Type": "text/plain; charset=utf-8"}}, body: raw.value
    }});
    if (!response.ok) return;
    const suggested = await response.json();
    const form = raw.closest("form");
    for (const name of ["commodity", "settlement", "trade_form", "side", "price", "quantity"]) {{
      if (suggested[name] !== null && suggested[name] !== undefined && suggested[name] !== "") {{
        const field = form.elements.namedItem(name);
        if (field) field.value = suggested[name];
      }}
    }}
    if (suggested.time_detected && suggested.offer_time) {{
      const liveField = form.elements.namedItem("offer_live");
      const timeField = form.elements.namedItem("offer_time");
      if (liveField) liveField.checked = false;
      if (timeField) timeField.value = suggested.offer_time;
    }}
    const result = document.getElementById("parse-offer-result");
    if (result) {{
      const warningText = (suggested.warnings || []).map((item) => ({{
        DATE_ADVANCED_FROM_SEQUENCE_ROLLOVER: "تاریخ به‌دلیل عبور توالی به روز بعد منتقل شد",
        NON_MONOTONIC_CLOCK_KEPT_ON_PREVIOUS_DATE: "ساعت از آفر قبلی عقب‌تر بود؛ همان تاریخ قبلی حفظ شد",
        MULTIPLE_PLAUSIBLE_PRICES: "بیش از یک عدد شبیه قیمت بود؛ قیمت پیشنهادی را بررسی کنید",
        SIDE_REQUIRES_REVIEW: "سمت خرید/فروش از متن قطعی نبود",
        FIRST_CLOCK_DATE_INFERRED_AS_PREVIOUS_DAY: "به‌دلیل نبود آفر قبلی، تاریخ روز قبل در نظر گرفته شد"
      }}[item] || item)).join("؛ ");
      const timeText = suggested.time_detected
        ? `زمان پیشنهادی تهران: ${{suggested.offer_time}}`
        : "ساعتی در متن پیدا نشد";
      result.textContent = `${{timeText}}` + (warningText ? ` — توجه: ${{warningText}}` : "");
      result.classList.toggle("missing", Boolean((suggested.warnings || []).length));
    }}
  }} catch (_) {{}}
}});
document.querySelectorAll(".manual-form").forEach((form) => {{
  form.addEventListener("submit", () => {{
    const button = form.querySelector('button[type="submit"]');
    if (button) {{
      button.disabled = true;
      button.textContent = "در حال ثبت…";
    }}
  }});
}});
</script>
</body>
</html>"""
    return document.encode("utf-8")


def render_analytics_page(
    conversation_db: Path,
    *,
    calibration_db: Path = DEFAULT_CALIBRATION_DB,
    feedback_db: Path = DEFAULT_REVIEW_DECISIONS_DB,
    coin_group_staging_db: Path | None = None,
    analytics_path: str = "/analytics",
    home_path: str = "/",
    logout_path: str = "/logout",
    user_session: str | None = None,
    range_type: str = "today",
    start_shamsi: str | None = None,
    end_shamsi: str | None = None,
) -> bytes:
    data = query_user_analytics(
        conversation_db,
        range_type=range_type,
        start_shamsi=start_shamsi,
        end_shamsi=end_shamsi,
    )
    event_audit = query_model_event_audit(
        conversation_db,
        calibration_db,
        feedback_db=feedback_db,
        coin_group_staging_db=coin_group_staging_db,
        range_type=range_type,
        start_shamsi=start_shamsi,
        end_shamsi=end_shamsi,
    )
    event_audit_html = render_model_event_audit(event_audit)

    def btn_active(r: str) -> str:
        return "active-filter" if data["range_type"] == r else ""

    groups_html = []
    for grp in (1, 2):
        gdata = data["groups"].get(grp, {})
        summary = gdata.get("summary", {})

        off_c = fa_number(summary.get("total_offer_count", 0))
        off_q = fa_number(summary.get("total_offer_qty", 0))
        trd_c = fa_number(summary.get("total_trade_count", 0))
        trd_q = fa_number(summary.get("total_trade_qty", 0))
        model_off_c = fa_number(summary.get("model_eligible_offer_count", 0))
        audit_off_c = fa_number(summary.get("audit_only_offer_count", 0))
        model_trd_c = fa_number(summary.get("model_eligible_trade_count", 0))
        audit_trd_c = fa_number(summary.get("audit_only_trade_count", 0))
        canonical_live = data.get("data_source") == "CANONICAL_LIVE_AUDIT"
        offer_breakdown = (
            f"<small>{model_off_c} ورودی مدل · {audit_off_c} فقط گزارش/بررسی</small>"
            if canonical_live
            else ""
        )
        trade_breakdown = (
            f"<small>{model_trd_c} ورودی مدل · {audit_trd_c} فقط گزارش/بررسی</small>"
            if canonical_live
            else ""
        )

        summary_cards_html = f"""
        <div class='group-summary-grid'>
          <div class='summary-card'>
            <div class='stat-icon'>📨</div>
            <div class='stat-info'>
              <span>آفرهای تشخیص‌داده‌شده</span>
              <strong>{off_c} <small>آفر</small></strong>
              {offer_breakdown}
            </div>
          </div>
          <div class='summary-card'>
            <div class='stat-icon'>🤝</div>
            <div class='stat-info'>
              <span>معاملات تشخیص‌داده‌شده</span>
              <strong>{trd_c} <small>معامله</small></strong>
              {trade_breakdown}
            </div>
          </div>
          <div class='summary-card'>
            <div class='stat-icon'>📦</div>
            <div class='stat-info'>
              <span>حجم کالای آفرشده</span>
              <strong>{off_q} <small>عدد کالا</small></strong>
            </div>
          </div>
          <div class='summary-card'>
            <div class='stat-icon'>🏷️</div>
            <div class='stat-info'>
              <span>حجم کالای معامله‌شده</span>
              <strong>{trd_q} <small>عدد کالا</small></strong>
            </div>
          </div>
        </div>
        """

        t1 = render_analytics_leaderboard_table("۱۰ کاربر بیشترین آفر دهنده", "بر اساس تعداد آفر ثبت‌شده", gdata.get("top_offer_count", []), "count", "آفر", grp, "offer")
        t2 = render_analytics_leaderboard_table("۱۰ کاربر بیشترین معامله کننده", "شامل خریدار و فروشنده / آفر و درخواست", gdata.get("top_trade_count", []), "count", "معامله", grp, "trade")
        t3 = render_analytics_leaderboard_table("۱۰ کاربر بیشترین حجم آفر", "مجموع تعداد کالای پیشنهادشده", gdata.get("top_offer_qty", []), "total_qty", "عدد کالا", grp, "offer_qty")
        t4 = render_analytics_leaderboard_table("۱۰ کاربر بیشترین حجم معامله", "مجموع تعداد کالای معامله‌شده", gdata.get("top_trade_qty", []), "total_qty", "عدد کالا", grp, "trade_qty")

        leaderboards = (
            f"<div class='leaderboards-grid'>{t1}{t2}{t3}{t4}</div>"
            if data.get("identity_analytics_available")
            else (
                "<p class='identity-notice'>رتبه‌بندی هویتی در مسیر canonical "
                "ذخیره نمی‌شود؛ این بخش فقط آمار اقتصادی زنده و بدون هویت را نمایش می‌دهد.</p>"
            )
        )
        groups_html.append(
            f"""
            <section class='group-analytics-section'>
              <div class='section-head'>
                <h2>تحلیل و خلاصه آمار — گروه {fa_number(grp)}</h2>
                <span class='badge'>گروه {fa_number(grp)} معاملاتی</span>
              </div>
              {summary_cards_html}
              {leaderboards}
            </section>
            """
        )

    user_badge = f"<span class='user-label' style='font-size:13px;color:var(--text-sub);margin-left:6px'>👤 <strong>{html.escape(user_session or 'bahar')}</strong></span>"
    logout_btn = f"<a class='nav-btn secondary' href='{html.escape(logout_path)}'>خروج</a>"
    navigation = f"{user_badge} <a class='nav-btn secondary' href='{html.escape(home_path)}'>بازگشت به داشبورد اصلی</a> {logout_btn}"
    source_notice = (
        "<p class='source-notice'>منبع این آمار، projection زندهٔ canonical است؛ "
        "داده‌های دیررس و نیازمند بررسی نمایش داده می‌شوند اما وارد مدل تخمین نمی‌شوند.</p>"
        if data.get("data_source") == "CANONICAL_LIVE_AUDIT"
        else ""
    )

    document = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>بازبینی parser و آمار و تحلیل گروه‌های معاملاتی</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root {{
  --bg-deep: #0b1329;
  --bg-surface: rgba(15, 23, 42, 0.85);
  --bg-card: #141f36;
  --border-line: rgba(255, 255, 255, 0.08);
  --border-gold: rgba(245, 158, 11, 0.35);
  --border-gold-glow: 0 0 15px rgba(245, 158, 11, 0.12);
  --text-main: #f8fafc;
  --text-sub: #94a3b8;
  --accent-gold: #f59e0b;
  --accent-cyan: #06b6d4;
  --accent-emerald: #10b981;
  --accent-indigo: #6366f1;
  --accent-rose: #f43f5e;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: radial-gradient(circle at 50% -10%, #1e1b4b 0%, #0b1329 70%);
  color: var(--text-main);
  font-family: Vazirmatn, system-ui, -apple-system, sans-serif;
  line-height: 1.5;
  min-height: 100vh;
}}
.wrap {{
  width: min(1440px, 96%);
  margin: 16px auto 40px;
}}
header {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
  padding: 14px 20px;
  background: var(--bg-surface);
  backdrop-filter: blur(20px);
  border: 1px solid var(--border-gold);
  box-shadow: var(--border-gold-glow);
  border-radius: 16px;
}}
.header-brand {{
  display: flex;
  align-items: center;
  gap: 12px;
}}
.logo-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.25), rgba(99, 102, 241, 0.25));
  border: 1px solid var(--accent-gold);
  font-size: 20px;
}}
h1 {{
  margin: 0;
  font-size: 20px;
  font-weight: 800;
  background: linear-gradient(135deg, #ffffff 40%, var(--accent-gold) 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}}
.nav-btn {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--accent-indigo), #4f46e5);
  color: #ffffff;
  font-weight: 700;
  font-size: 13px;
  text-decoration: none;
  box-shadow: 0 6px 18px rgba(99, 102, 241, 0.3);
  transition: all 0.2s ease;
  border: none;
  cursor: pointer;
}}
.nav-btn.secondary {{
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--border-line);
  color: var(--text-main);
  box-shadow: none;
}}
.filter-bar {{
  background: var(--bg-surface);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border-gold);
  border-radius: 16px;
  padding: 16px 20px;
  margin-bottom: 20px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}}
.filter-presets {{
  display: flex;
  align-items: center;
  gap: 10px;
}}
.filter-btn {{
  padding: 8px 16px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--border-line);
  color: var(--text-sub);
  font-weight: 600;
  font-size: 13px;
  text-decoration: none;
  transition: all 0.2s ease;
}}
.filter-btn.active-filter, .filter-btn:hover {{
  background: var(--accent-gold);
  color: #0b1329;
  border-color: var(--accent-gold);
  font-weight: 800;
}}
.shamsi-form {{
  display: flex;
  align-items: center;
  gap: 10px;
}}
.shamsi-form label {{
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-sub);
}}
.shamsi-form input {{
  padding: 7px 12px;
  border-radius: 8px;
  border: 1px solid var(--border-line);
  background: rgba(15, 23, 42, 0.85);
  color: var(--text-main);
  font-family: inherit;
  font-size: 13px;
  width: 110px;
  text-align: center;
}}
.shamsi-form button {{
  padding: 7px 14px;
  border-radius: 8px;
  background: var(--accent-cyan);
  color: #0b1329;
  font-weight: 700;
  font-size: 13px;
  border: none;
  cursor: pointer;
}}
.group-analytics-section {{
  background: var(--bg-surface);
  backdrop-filter: blur(16px);
  border: 1px solid var(--border-line);
  border-radius: 18px;
  padding: 20px;
  margin-bottom: 24px;
}}
.section-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}}
.group-summary-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 14px;
  margin-bottom: 20px;
}}
.summary-card {{
  background: var(--bg-card);
  border: 1px solid var(--border-gold);
  box-shadow: var(--border-gold-glow);
  border-radius: 14px;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}}
.summary-card:hover {{
  transform: translateY(-2px);
  box-shadow: 0 0 20px rgba(245, 158, 11, 0.25);
}}
.stat-icon {{
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.2), rgba(6, 182, 212, 0.2));
  border: 1px solid var(--border-gold);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
}}
.stat-info {{
  display: flex;
  flex-direction: column;
}}
.stat-info span {{
  font-size: 12px;
  color: var(--text-sub);
  font-weight: 600;
}}
.stat-info strong {{
  font-size: 19px;
  font-weight: 800;
  color: var(--accent-gold);
  line-height: 1.2;
}}
.stat-info strong small {{
  font-size: 11.5px;
  color: var(--accent-cyan);
  font-weight: 600;
}}
h2 {{
  margin: 0;
  font-size: 18px;
  font-weight: 800;
  color: var(--text-main);
}}
.badge {{
  padding: 3px 10px;
  border-radius: 99px;
  background: rgba(6, 182, 212, 0.12);
  border: 1px solid rgba(6, 182, 212, 0.3);
  color: var(--accent-cyan);
  font-size: 11.5px;
  font-weight: 600;
}}
.leaderboards-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}}
.leaderboard-card {{
  background: var(--bg-card);
  border: 1px solid var(--border-gold);
  box-shadow: var(--border-gold-glow);
  border-radius: 14px;
  padding: 14px;
}}
.card-header {{
  margin-bottom: 10px;
}}
.card-header h3 {{
  margin: 0 0 2px;
  font-size: 14px;
  font-weight: 800;
  color: var(--accent-gold);
}}
.card-header small {{
  color: var(--text-sub);
  font-size: 11px;
}}
.table-wrap {{
  overflow-x: auto;
  border-radius: 10px;
  border: 1px solid var(--border-line);
}}
table {{
  width: 100%;
  border-collapse: collapse;
  white-space: nowrap;
}}
th {{
  background: rgba(15, 23, 42, 0.95);
  padding: 9px 12px;
  color: var(--text-sub);
  font-size: 11.5px;
  font-weight: 700;
  text-align: right;
  border-bottom: 1px solid var(--border-line);
}}
td {{
  padding: 9px 12px;
  border-bottom: 1px solid var(--border-line);
  font-size: 12.5px;
}}
.rank-col {{
  color: var(--accent-cyan);
  font-weight: 700;
}}
.value-col {{
  text-align: left;
  direction: ltr;
}}
.value-col strong {{
  color: var(--accent-gold);
}}
.missing {{
  color: var(--accent-rose);
  text-align: center;
  padding: 12px;
}}

.user-link {{
  color: var(--text-main);
  text-decoration: underline;
  text-decoration-color: var(--border-gold);
  text-underline-offset: 3px;
  cursor: pointer;
  transition: all 0.2s ease;
}}
.user-link:hover {{
  color: var(--accent-gold);
  text-decoration-color: var(--accent-gold);
}}
.modal-overlay {{
  display: none;
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(11, 19, 41, 0.85);
  backdrop-filter: blur(12px);
  z-index: 1000;
  align-items: center;
  justify-content: center;
  padding: 20px;
}}
.modal-overlay.active {{
  display: flex;
}}
.modal-card {{
  width: 100%;
  max-width: 950px;
  max-height: 85vh;
  background: #141f36;
  border: 1px solid var(--border-gold);
  box-shadow: 0 0 30px rgba(245, 158, 11, 0.2);
  border-radius: 20px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}}
.modal-header {{
  padding: 16px 24px;
  background: rgba(15, 23, 42, 0.95);
  border-bottom: 1px solid var(--border-line);
  display: flex;
  align-items: center;
  justify-content: space-between;
}}
.modal-header h2 {{
  margin: 0 0 2px;
  font-size: 17px;
  color: var(--accent-gold);
}}
.modal-close-btn {{
  background: none;
  border: none;
  color: var(--text-sub);
  font-size: 24px;
  cursor: pointer;
  padding: 0 6px;
  line-height: 1;
}}
.modal-close-btn:hover {{
  color: var(--accent-rose);
}}
.modal-body {{
  padding: 20px;
  overflow-y: auto;
}}
.modal-badge-bar {{
  display: flex;
  gap: 12px;
  margin-bottom: 14px;
}}
.modal-badge {{
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--border-line);
  padding: 6px 12px;
  border-radius: 8px;
  font-size: 12.5px;
}}
.modal-badge strong {{
  color: var(--accent-cyan);
}}
.source-notice, .identity-notice {{
  margin: 0 0 16px;
  padding: 11px 14px;
  border: 1px solid var(--border-gold);
  border-radius: 12px;
  color: var(--text-sub);
  background: rgba(245, 158, 11, 0.07);
  font-size: 12px;
  line-height: 1.8;
}}
.stat-info > small {{ color: var(--text-sub); font-size: 10px; }}
.event-audit-section {{
  background: var(--bg-surface);
  border: 1px solid var(--border-gold);
  border-radius: 18px;
  padding: 20px;
  margin-bottom: 24px;
}}
.event-audit-section .section-head {{ gap: 16px; align-items: flex-start; }}
.event-audit-section .section-head p {{
  margin: 6px 0 0;
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1.9;
}}
.event-audit-summary {{
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin: 14px 0;
}}
.event-audit-summary span {{
  padding: 7px 11px;
  border: 1px solid var(--border-line);
  border-radius: 9px;
  background: var(--bg-card);
  color: var(--text-sub);
  font-size: 11.5px;
}}
.event-audit-summary strong {{ color: var(--accent-gold); margin-right: 4px; }}
.event-audit-summary .warning {{ border-color: var(--accent-rose); }}
.event-audit-wrap {{ max-height: 68vh; overflow: auto; }}
.event-audit-table {{ min-width: 2480px; }}
.event-audit-table thead {{ position: sticky; top: 0; z-index: 2; }}
.event-audit-table td {{ vertical-align: top; white-space: normal; min-width: 105px; }}
.event-audit-table td:nth-child(5),
.event-audit-table td:nth-child(6) {{ min-width: 165px; }}
.event-audit-table th:first-child,
.event-audit-table td:first-child {{
  position: sticky;
  right: 0;
  min-width: 185px;
  background: #141f36;
  border-left: 1px solid var(--border-line);
  z-index: 1;
}}
.event-audit-table th:first-child {{ background: #0f172a; z-index: 3; }}
.event-status-cell {{ min-width: 185px !important; }}
.raw-text-cell {{ min-width: 320px !important; max-width: 420px; }}
.raw-event-text strong {{ display: block; color: var(--accent-gold); font-size: 10.5px; }}
.raw-event-text strong:not(:first-child) {{ margin-top: 9px; color: var(--accent-cyan); }}
.raw-event-text pre {{
  margin: 5px 0 0;
  padding: 8px 10px;
  max-height: 150px;
  overflow: auto;
  border: 1px solid var(--border-line);
  border-radius: 8px;
  background: #0f172a;
  color: var(--text-main);
  font: inherit;
  font-size: 11.5px;
  line-height: 1.8;
  text-align: right;
  direction: rtl;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}}
.raw-text-unavailable {{ color: var(--text-sub); font-size: 10.5px; line-height: 1.8; }}
.event-status {{
  display: inline-block;
  padding: 4px 8px;
  border-radius: 99px;
  font-size: 10.5px;
  font-weight: 800;
  white-space: nowrap;
}}
.event-status.model_input {{
  color: #6ee7b7;
  background: rgba(16,185,129,.13);
  border: 1px solid rgba(16,185,129,.35);
}}
.event-status.audit_only_late,
.event-status.audit_only_conditional {{
  color: #fcd34d;
  background: rgba(245,158,11,.12);
  border: 1px solid rgba(245,158,11,.32);
}}
.event-status.pending_review {{
  color: #fda4af;
  background: rgba(244,63,94,.12);
  border: 1px solid rgba(244,63,94,.32);
}}
.numeric {{ direction: rtl; }}
.numeric small, .estimate-cell small, .estimate-cell time {{
  display: block;
  margin-top: 4px;
  color: var(--text-sub);
  font-size: 10px;
  direction: rtl;
}}
.estimate-cell {{ min-width: 190px !important; direction: rtl; }}
.estimate-cell strong {{ color: var(--accent-cyan); }}
.event-no-estimate {{ color: var(--text-sub); }}
.parser-feedback-btn {{
  display: block;
  width: 100%;
  margin-top: 6px;
  padding: 7px 9px;
  border-radius: 8px;
  border: 1px solid rgba(6,182,212,.35);
  background: rgba(6,182,212,.12);
  color: var(--accent-cyan);
  font-family: inherit;
  font-size: 10.5px;
  font-weight: 800;
  cursor: pointer;
}}
.feedback-state {{ display: block; color: #6ee7b7; line-height: 1.7; }}
.feedback-modal-card {{ max-width: 780px; }}
.feedback-help {{
  margin: 0 0 14px;
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1.9;
}}
.feedback-fields {{
  border: 1px solid var(--border-line);
  border-radius: 12px;
  padding: 12px;
  margin: 0 0 14px;
  display: grid;
  grid-template-columns: repeat(auto-fit,minmax(150px,1fr));
  gap: 8px;
}}
.feedback-fields legend {{ color: var(--accent-gold); padding: 0 7px; font-size: 12px; }}
.feedback-fields label, .feedback-toggle {{ color: var(--text-main); font-size: 11.5px; }}
.feedback-grid {{
  display: grid;
  grid-template-columns: repeat(2,minmax(0,1fr));
  gap: 12px;
}}
.feedback-grid label {{ color: var(--text-sub); font-size: 11.5px; }}
.feedback-grid input[type='number'], .feedback-grid select {{
  width: 100%;
  margin-top: 5px;
  padding: 9px 10px;
  border: 1px solid var(--border-line);
  border-radius: 8px;
  color: var(--text-main);
  background: #0f172a;
  font-family: inherit;
}}
.feedback-toggle {{ display: flex; align-items: center; gap: 7px; }}
.feedback-result {{ min-height: 24px; color: var(--accent-cyan); font-size: 12px; }}
.feedback-result.error {{ color: var(--accent-rose); }}
.feedback-actions {{
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--border-line);
}}
@media (max-width: 720px) {{ .feedback-grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main class="wrap">
  <header>
    <div class="header-brand">
      <div class="logo-badge">📊</div>
      <div>
        <h1>بازبینی parser و <span>آمار و تحلیل گروه‌های معاملاتی</span></h1>
        <small style="color:var(--text-sub)">بازه‌ فعلی: <strong>{data['range_label']}</strong></small>
      </div>
    </div>
    {navigation}
  </header>

  <div class="filter-bar">
    <div class="filter-presets">
      <span style="font-size:13px;font-weight:700;color:var(--text-sub)">فیلتر سریع:</span>
      <a class="filter-btn {btn_active('today')}" href="{analytics_path}?range_type=today">امروز (روز جاری)</a>
      <a class="filter-btn {btn_active('7d')}" href="{analytics_path}?range_type=7d">۷ روز اخیر</a>
      <a class="filter-btn {btn_active('30d')}" href="{analytics_path}?range_type=30d">۳۰ روز اخیر (۱ ماهه)</a>
    </div>
    <form class="shamsi-form" method="get" action="{analytics_path}">
      <input type="hidden" name="range_type" value="custom">
      <label>از تاریخ (شمسی): <input name="start_shamsi" placeholder="1405/05/10" value="{html.escape(data['start_shamsi'])}"></label>
      <label>تا تاریخ (شمسی): <input name="end_shamsi" placeholder="1405/05/12" value="{html.escape(data['end_shamsi'])}"></label>
      <button type="submit">اعمال فیلتر شمسی</button>
    </form>
  </div>

  {source_notice}
  {event_audit_html}
  {''.join(groups_html)}
</main>

<div id="detail-modal" class="modal-overlay" onclick="closeUserModal(event)">
  <div class="modal-card" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div>
        <h2 id="modal-title">جزییات فعالیت کاربر</h2>
        <small id="modal-subtitle" style="color:var(--text-sub)"></small>
      </div>
      <button class="modal-close-btn" onclick="closeUserModal()">&times;</button>
    </div>
    <div id="modal-body" class="modal-body">
      <div style="text-align:center;padding:30px;color:var(--text-sub)">در حال دریافت داده‌ها…</div>
    </div>
  </div>
</div>

<script>
document.addEventListener("click", function(e) {{
  const target = e.target.closest(".user-link");
  if (target) {{
    e.preventDefault();
    const username = target.getAttribute("data-username");
    const group = target.getAttribute("data-group");
    const kind = target.getAttribute("data-kind");
    if (username) {{
      openUserModal(username, group, kind);
    }}
  }}
}});

async function openUserModal(username, group, kind) {{
  const modal = document.getElementById("detail-modal");
  const title = document.getElementById("modal-title");
  const subtitle = document.getElementById("modal-subtitle");
  const body = document.getElementById("modal-body");

  const isTrade = kind === "trade" || kind === "trade_qty";
  const kindTitle = isTrade ? "معاملات تاییدشده" : "آفرهای ثبت‌شده";
  title.textContent = `ریز جزییات ${{kindTitle}} — ${{username}}`;
  subtitle.textContent = `گروه ${{group}} | در حال بارگذاری…`;
  body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-sub)">در حال دریافت داده‌ها…</div>';
  modal.classList.add("active");

  const urlParams = new URLSearchParams(window.location.search);
  urlParams.set("username", username);
  urlParams.set("group", group);
  urlParams.set("kind", kind);

  try {{
    const res = await fetch("{analytics_path}/user-details.json?" + urlParams.toString());
    if (!res.ok) throw new Error();
    const data = await res.json();

    subtitle.textContent = `گروه ${{data.group}} | ${{data.range_label}}`;

    if (!data.items || data.items.length === 0) {{
      body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--accent-rose)">هیچ رکوردی در این بازه زمانی یافت نشد.</div>';
      return;
    }}

    let rows = "";
    if (isTrade) {{
      rows = data.items.map(item => `
        <tr>
          <td>${{item.time}}</td>
          <td><span class="badge" style="background:rgba(99,102,241,0.15);color:var(--accent-indigo);border-color:rgba(99,102,241,0.3)">${{item.role}}</span></td>
          <td><strong style="color:var(--accent-gold)">${{item.counterparty}}</strong></td>
          <td>${{item.commodity}}</td>
          <td><span style="color:${{item.side === 'خرید' ? 'var(--accent-emerald)' : 'var(--accent-rose)'}}">${{item.side}}</span></td>
          <td dir="ltr" style="text-align:left"><strong>${{item.price}}</strong> تومان</td>
          <td dir="ltr" style="text-align:left">${{item.quantity}} عدد</td>
          <td><small>${{item.settlement}}</small></td>
        </tr>
      `).join("");

      body.innerHTML = `
        <div class="modal-badge-bar" style="justify-content:space-between;align-items:center">
          <div style="display:flex;gap:12px">
            <div class="modal-badge">تعداد کل معاملات: <strong>${{data.total_items}}</strong></div>
            <div class="modal-badge">مجموع حجم کالا: <strong>${{data.total_qty}} عدد</strong></div>
          </div>
          <a href="{analytics_path}/user-details/pdf?${{urlParams.toString()}}" target="_blank" class="nav-btn secondary" style="font-size:12.5px;padding:6px 14px">📄 دریافت گزارش PDF</a>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>زمان ثبت</th><th>نقش کاربر</th><th>طرف مقابل معامله</th><th>کالا</th><th>سمت</th><th style="text-align:left">قیمت</th><th style="text-align:left">تعداد</th><th>تسویه</th></tr>
            </thead>
            <tbody>${{rows}}</tbody>
          </table>
        </div>
      `;
    }} else {{
      rows = data.items.map(item => `
        <tr>
          <td>${{item.time}}</td>
          <td>${{item.commodity}}</td>
          <td><span style="color:${{item.side === 'خرید' ? 'var(--accent-emerald)' : 'var(--accent-rose)'}}">${{item.side}}</span></td>
          <td dir="ltr" style="text-align:left"><strong>${{item.price}}</strong> تومان</td>
          <td dir="ltr" style="text-align:left">${{item.quantity}} عدد</td>
          <td><small>${{item.settlement}}</small></td>
          <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis" title="${{item.text}}">${{item.text}}</td>
        </tr>
      `).join("");

      body.innerHTML = `
        <div class="modal-badge-bar" style="justify-content:space-between;align-items:center">
          <div style="display:flex;gap:12px">
            <div class="modal-badge">تعداد کل آفرها: <strong>${{data.total_items}}</strong></div>
            <div class="modal-badge">مجموع حجم کالا: <strong>${{data.total_qty}} عدد</strong></div>
          </div>
          <a href="{analytics_path}/user-details/pdf?${{urlParams.toString()}}" target="_blank" class="nav-btn secondary" style="font-size:12.5px;padding:6px 14px">📄 دریافت گزارش PDF</a>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>زمان ثبت</th><th>کالا</th><th>سمت</th><th style="text-align:left">قیمت</th><th style="text-align:left">تعداد</th><th>تسویه</th><th>متن آفر</th></tr>
            </thead>
            <tbody>${{rows}}</tbody>
          </table>
        </div>
      `;
    }}
  }} catch (e) {{
    body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--accent-rose)">خطا در دریافت اطلاعات.</div>';
  }}
}}

function closeUserModal(e) {{
  if (e && e.target !== e.currentTarget && !e.target.classList.contains('modal-close-btn')) return;
  document.getElementById("detail-modal").classList.remove("active");
}}
document.addEventListener("keydown", (e) => {{
  if (e.key === "Escape") {{ closeUserModal(); closeParserFeedback(); }}
}});

function openParserFeedback(button) {{
  const data = JSON.parse(button.getAttribute("data-review") || "{{}}");
  const modal = document.getElementById("parser-feedback-modal");
  const form = document.getElementById("parser-feedback-form");
  form.reset();
  document.getElementById("feedback-event-id").value = data.event_id || "";
  document.getElementById("feedback-event-confirmed").checked = data.feedback
    ? Boolean(data.feedback.event_confirmed) : true;
  document.getElementById("feedback-commodity").value = data.commodity_code || "";
  document.getElementById("feedback-side").value = data.side || "SELL";
  document.getElementById("feedback-price").value = data.price_toman || "";
  document.getElementById("feedback-quantity").value = data.quantity || 1;
  document.getElementById("feedback-settlement").value = data.settlement_term || "TOMORROW";
  document.getElementById("feedback-trade-form").value = data.trade_form || "PHYSICAL";
  document.getElementById("feedback-conditional").checked = Boolean(data.is_conditional);
  const selected = new Set((data.feedback && data.feedback.ambiguous_fields) || []);
  if (!selected.size && !data.commodity_code) selected.add("commodity");
  document.querySelectorAll("input[name='ambiguous_field']").forEach((input) => {{
    input.checked = selected.has(input.value);
  }});
  document.getElementById("parser-feedback-context").textContent =
    `${{data.event_type === "TRADE" ? "معامله" : "آفر"}} · شناسهٔ امن ${{String(data.event_id || "").slice(0, 12)}}`;
  const result = document.getElementById("parser-feedback-result");
  result.textContent = "";
  result.classList.remove("error");
  modal.classList.add("active");
}}

function closeParserFeedback(e) {{
  if (e && e.target !== e.currentTarget && !e.target.classList.contains("modal-close-btn")) return;
  const modal = document.getElementById("parser-feedback-modal");
  if (modal) modal.classList.remove("active");
}}

const feedbackEventConfirmed = document.getElementById("feedback-event-confirmed");
feedbackEventConfirmed.addEventListener("change", (event) => {{
  const commodity = document.getElementById("feedback-commodity");
  if (event.target.checked) {{
    if (commodity.value === "UNRESOLVED") commodity.value = "";
    return;
  }}
  const validity = document.querySelector(
    "input[name='ambiguous_field'][value='event_validity']"
  );
  if (validity) validity.checked = true;
  if (!commodity.value) commodity.value = "UNRESOLVED";
}});
document.getElementById("feedback-commodity").addEventListener("change", (event) => {{
  if (event.target.value !== "UNRESOLVED") return;
  feedbackEventConfirmed.checked = false;
  feedbackEventConfirmed.dispatchEvent(new Event("change"));
}});

document.getElementById("parser-feedback-form").addEventListener("submit", async (event) => {{
  event.preventDefault();
  const result = document.getElementById("parser-feedback-result");
  const button = document.getElementById("parser-feedback-submit");
  const ambiguousFields = Array.from(
    document.querySelectorAll("input[name='ambiguous_field']:checked")
  ).map((input) => input.value);
  if (!ambiguousFields.length) {{
    result.textContent = "حداقل یک فیلد مبهم را انتخاب کنید.";
    result.classList.add("error");
    return;
  }}
  const payload = {{
    event_id: document.getElementById("feedback-event-id").value,
    ambiguous_fields: ambiguousFields,
    event_confirmed: document.getElementById("feedback-event-confirmed").checked,
    commodity_code: document.getElementById("feedback-commodity").value,
    side: document.getElementById("feedback-side").value,
    price_toman: Number(document.getElementById("feedback-price").value),
    quantity: Number(document.getElementById("feedback-quantity").value),
    settlement_term: document.getElementById("feedback-settlement").value,
    trade_form: document.getElementById("feedback-trade-form").value,
    is_conditional: document.getElementById("feedback-conditional").checked,
  }};
  button.disabled = true;
  result.classList.remove("error");
  result.textContent = "در حال ثبت بازخورد و افزایش نسخهٔ کالیبراسیون…";
  try {{
    const response = await fetch("{analytics_path}/parser-feedback.json", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(payload),
    }});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "ثبت بازخورد ناموفق بود");
    result.textContent = `بازخورد نسخهٔ ${{body.review_revision}} ثبت شد؛ حداکثر تا ${{body.expected_apply_seconds}} ثانیه در چرخهٔ parser اعمال می‌شود.`;
    setTimeout(() => window.location.reload(), 1800);
  }} catch (error) {{
    result.textContent = error.message || "ثبت بازخورد ناموفق بود.";
    result.classList.add("error");
    button.disabled = false;
  }}
}});
</script>
</body>
</html>"""
    return document.encode("utf-8")


def health_response(state: dict[str, Any]) -> tuple[HTTPStatus, dict[str, Any]]:
    input_health = state.get("input_health")
    input_health = input_health if isinstance(input_health, dict) else {}
    aggregate = str(input_health.get("status") or "").upper()
    service_status = str(state.get("service_status", "UNKNOWN")).upper()
    if not aggregate:
        aggregate = "HEALTHY" if service_status in {"RUNNING", "READY", "OK"} else "CRITICAL"
    collectors = input_health.get("collectors")
    collectors = collectors if isinstance(collectors, dict) else {}
    model_inputs = input_health.get("model_inputs")
    model_inputs = model_inputs if isinstance(model_inputs, dict) else {}
    payload = {
        "status": aggregate,
        "service_status": service_status,
        "generated_at_utc": state.get("generated_at_utc"),
        "evaluated_at_utc": input_health.get("evaluated_at_utc"),
        "reason_codes": list(input_health.get("reason_codes") or []),
        "collectors": {
            str(name): {
                "status": value.get("status"),
                "heartbeat_age_seconds": value.get("heartbeat_age_seconds"),
                "max_age_seconds": value.get("max_age_seconds"),
                "reason_code": value.get("reason_code"),
            }
            for name, value in collectors.items()
            if isinstance(value, dict)
        },
        "model_inputs": {
            str(name): {
                "status": value.get("status"),
                "importance": value.get("importance"),
                "settlements": value.get("settlements"),
            }
            for name, value in model_inputs.items()
            if isinstance(value, dict)
        },
    }
    status = HTTPStatus.SERVICE_UNAVAILABLE if aggregate == "CRITICAL" else HTTPStatus.OK
    return status, payload


def handler_factory(
    route: str,
    state_store: StateStore,
    *,
    market_db: Path,
    conversation_db: Path,
    calibration_db: Path,
    feedback_db: Path,
    coin_group_staging_db: Path | None,
    write_token: str | None,
    refresh_estimate,
    group_live_control: GroupLiveInputControl,
):
    normalized = "/" + route.strip("/")
    data_path = normalized + "/data.json"
    health_path = normalized + "/healthz"
    manual_path = normalized + "/manual-entry"
    analytics_path = normalized + "/analytics"
    parser_feedback_path = analytics_path + "/parser-feedback.json"
    login_path = normalized + "/login"
    logout_path = normalized + "/logout"
    estimate_path = normalized + "/estimates.html"
    activity_path = normalized + "/activity.html"
    shadow_path = normalized + "/shadow"
    shadow_data_path = shadow_path + "/data.json"
    shadow_estimate_path = shadow_path + "/estimates.html"
    research_shadow_data_path = shadow_path + "/morning-reopen/data.json"
    ml_shadow_data_path = shadow_path + "/ml-residual/data.json"
    group_live_control_path = normalized + "/group-live-control"
    parse_offer_path = manual_path + "/parse-text"
    session_store = SessionStore(RUNTIME_ROOT / "web_sessions.sqlite3")

    class Handler(BaseHTTPRequestHandler):
        server_version = "CoinEstimator/1"

        def _headers(self, status: HTTPStatus, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()

        def _redirect(self, target: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()

        def _redirect_with_cookie(self, target: str, cookie_str: str) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", target)
            self.send_header("Set-Cookie", cookie_str)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()

        def _get_session_token(self) -> str | None:
            cookie_header = self.headers.get("Cookie", "")
            if not cookie_header:
                return None
            try:
                cookie = SimpleCookie(cookie_header)
                if "coin_session_token" in cookie:
                    return cookie["coin_session_token"].value
            except Exception:
                pass
            return None

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path.rstrip("/") or "/"
            state = state_store.get()

            if path == health_path:
                health_status, health_payload = health_response(state)
                body = json.dumps(health_payload, ensure_ascii=False).encode("utf-8")
                self._headers(health_status, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return

            if path == login_path:
                token = self._get_session_token()
                if session_store.validate_session(token):
                    self._redirect(normalized)
                    return
                body = render_login_page(login_path=login_path)
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return

            if path == logout_path:
                token = self._get_session_token()
                session_store.revoke_session(token)
                self._redirect_with_cookie(login_path, "coin_session_token=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
                return

            # Authentication Check
            token = self._get_session_token()
            user_session = session_store.validate_session(token)
            if not user_session:
                if path.endswith(".json"):
                    body = b'{"error":"unauthorized"}'
                    self._headers(HTTPStatus.UNAUTHORIZED, "application/json; charset=utf-8", len(body))
                    self.wfile.write(body)
                else:
                    self._redirect(login_path)
                return

            if path == normalized:
                body = render_page(
                    state,
                    manual_path=manual_path,
                    analytics_path=analytics_path,
                    logout_path=logout_path,
                    user_session=user_session,
                    estimate_path=estimate_path,
                    activity_path=activity_path,
                    write_enabled=write_token is not None,
                    market_db=market_db,
                    conversation_db=conversation_db,
                    group_live_control_path=group_live_control_path,
                    group_live_control=group_live_control.get(),
                    shadow_path=shadow_path,
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == shadow_path:
                body = render_shadow_page(
                    state,
                    home_path=normalized,
                    shadow_path=shadow_path,
                    shadow_data_path=shadow_data_path,
                    shadow_estimate_path=shadow_estimate_path,
                    logout_path=logout_path,
                    user_session=user_session,
                    shadow_state_path=DEFAULT_SHADOW_STATE,
                    research_shadow_state_path=DEFAULT_RESEARCH_SHADOW_STATE,
                    research_shadow_data_path=research_shadow_data_path,
                    ml_shadow_state_path=DEFAULT_ML_SHADOW_STATE_PATH,
                    ml_shadow_data_path=ml_shadow_data_path,
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == shadow_estimate_path:
                body = render_shadow_page(
                    state,
                    home_path=normalized,
                    shadow_path=shadow_path,
                    shadow_data_path=shadow_data_path,
                    shadow_estimate_path=shadow_estimate_path,
                    logout_path=logout_path,
                    user_session=user_session,
                    shadow_state_path=DEFAULT_SHADOW_STATE,
                    research_shadow_state_path=DEFAULT_RESEARCH_SHADOW_STATE,
                    ml_shadow_state_path=DEFAULT_ML_SHADOW_STATE_PATH,
                    estimate_fragment=True,
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == shadow_data_path:
                body = json.dumps(
                    load_shadow_dashboard_payload(DEFAULT_SHADOW_STATE),
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == research_shadow_data_path:
                body = json.dumps(
                    load_shadow_dashboard_payload(DEFAULT_RESEARCH_SHADOW_STATE),
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == ml_shadow_data_path:
                body = json.dumps(
                    load_shadow_dashboard_payload(DEFAULT_ML_SHADOW_STATE_PATH),
                    ensure_ascii=False,
                    indent=2,
                ).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == analytics_path:
                query = parse_qs(urlsplit(self.path).query)
                range_type = query.get("range_type", ["today"])[0]
                start_shamsi = query.get("start_shamsi", [None])[0]
                end_shamsi = query.get("end_shamsi", [None])[0]
                body = render_analytics_page(
                    conversation_db,
                    calibration_db=calibration_db,
                    feedback_db=feedback_db,
                    coin_group_staging_db=coin_group_staging_db,
                    analytics_path=analytics_path,
                    home_path=normalized,
                    logout_path=logout_path,
                    user_session=user_session,
                    range_type=range_type,
                    start_shamsi=start_shamsi,
                    end_shamsi=end_shamsi,
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == analytics_path + ".json":
                query = parse_qs(urlsplit(self.path).query)
                range_type = query.get("range_type", ["today"])[0]
                start_shamsi = query.get("start_shamsi", [None])[0]
                end_shamsi = query.get("end_shamsi", [None])[0]
                data = query_user_analytics(
                    conversation_db,
                    range_type=range_type,
                    start_shamsi=start_shamsi,
                    end_shamsi=end_shamsi,
                )
                data["model_event_audit"] = query_model_event_audit(
                    conversation_db,
                    calibration_db,
                    feedback_db=feedback_db,
                    range_type=range_type,
                    start_shamsi=start_shamsi,
                    end_shamsi=end_shamsi,
                )
                body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == analytics_path + "/user-details.json":
                query = parse_qs(urlsplit(self.path).query)
                username = query.get("username", [""])[0]
                try:
                    group = int(query.get("group", [1])[0])
                except ValueError:
                    group = 1
                kind = query.get("kind", ["offer"])[0]
                range_type = query.get("range_type", ["today"])[0]
                start_shamsi = query.get("start_shamsi", [None])[0]
                end_shamsi = query.get("end_shamsi", [None])[0]
                data = query_user_details(
                    conversation_db,
                    username,
                    group,
                    kind,
                    range_type=range_type,
                    start_shamsi=start_shamsi,
                    end_shamsi=end_shamsi,
                )
                body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == analytics_path + "/user-details/pdf":
                query = parse_qs(urlsplit(self.path).query)
                username = query.get("username", [""])[0]
                try:
                    group = int(query.get("group", [1])[0])
                except ValueError:
                    group = 1
                kind = query.get("kind", ["offer"])[0]
                range_type = query.get("range_type", ["today"])[0]
                start_shamsi = query.get("start_shamsi", [None])[0]
                end_shamsi = query.get("end_shamsi", [None])[0]
                body = render_user_details_pdf_page(
                    conversation_db,
                    username,
                    group,
                    kind,
                    range_type=range_type,
                    start_shamsi=start_shamsi,
                    end_shamsi=end_shamsi,
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == manual_path:
                query = parse_qs(urlsplit(self.path).query)
                flash = query.get("entry", [None])[0]
                body = render_page(
                    state,
                    manual_path=manual_path,
                    analytics_path=analytics_path,
                    logout_path=logout_path,
                    user_session=user_session,
                    estimate_path=estimate_path,
                    activity_path=activity_path,
                    write_enabled=write_token is not None,
                    flash=flash,
                    open_manual_offers=list_open_manual_offers(conversation_db),
                    page="manual",
                    market_db=market_db,
                    conversation_db=conversation_db,
                    group_live_control_path=group_live_control_path,
                    group_live_control=group_live_control.get(),
                    shadow_path=shadow_path,
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == estimate_path:
                body = render_page(
                    state,
                    estimate_fragment=True,
                    market_db=market_db,
                    conversation_db=conversation_db,
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == activity_path:
                body = render_group_activity_fragment(
                    conversation_db,
                    input_health=state.get("input_health"),
                ).encode("utf-8")
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == data_path:
                body = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            body = b"Not found"
            self._headers(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", len(body))
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path.rstrip("/") or "/"
            if path == login_path:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                payload = self.rfile.read(content_length).decode("utf-8", errors="replace") if content_length > 0 else ""
                parsed = parse_qs(payload, keep_blank_values=True)
                u_val = parsed.get("username", [""])[-1].strip()
                p_val = parsed.get("password", [""])[-1].strip()
                try:
                    expected_user, expected_password = load_dashboard_credentials()
                except (OSError, ValueError, RuntimeError) as exc:
                    body = render_login_page(
                        login_path=login_path,
                        error="پیکربندی ورود ناقص است؛ با اپراتور تماس بگیرید",
                    )
                    self._headers(HTTPStatus.UNAUTHORIZED, "text/html; charset=utf-8", len(body))
                    self.wfile.write(body)
                    print(
                        json.dumps(
                            {
                                "event": "dashboard_credentials_error",
                                "error": f"{type(exc).__name__}: {exc}"[:500],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    return

                user_ok = hmac.compare_digest(u_val, expected_user)
                pass_ok = hmac.compare_digest(p_val, expected_password)
                if user_ok and pass_ok:
                    new_token = session_store.create_session(expected_user)
                    cookie_str = f"coin_session_token={new_token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax"
                    self._redirect_with_cookie(normalized, cookie_str)
                    return
                else:
                    body = render_login_page(login_path=login_path, error="نام کاربری یا رمز عبور اشتباه است")
                    self._headers(HTTPStatus.UNAUTHORIZED, "text/html; charset=utf-8", len(body))
                    self.wfile.write(body)
                    return

            token = self._get_session_token()
            user_session = session_store.validate_session(token)
            if not user_session:
                if path.endswith(".json") or path == parse_offer_path:
                    body = b'{"error":"unauthorized"}'
                    self._headers(HTTPStatus.UNAUTHORIZED, "application/json; charset=utf-8", len(body))
                    self.wfile.write(body)
                else:
                    self._redirect(login_path)
                return
            if path == parser_feedback_path:
                origin = self.headers.get("Origin")
                referer = self.headers.get("Referer")
                expected_host = self.headers.get("Host", "")
                for candidate in (origin, referer):
                    if not candidate:
                        continue
                    parsed_candidate = urlsplit(candidate)
                    if parsed_candidate.netloc and parsed_candidate.netloc != expected_host:
                        body = b'{"error":"cross_origin_forbidden"}'
                        self._headers(
                            HTTPStatus.FORBIDDEN,
                            "application/json; charset=utf-8",
                            len(body),
                        )
                        self.wfile.write(body)
                        return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                if content_length <= 0 or content_length > 4_096:
                    body = b'{"error":"invalid_parser_feedback_request"}'
                    self._headers(
                        HTTPStatus.BAD_REQUEST,
                        "application/json; charset=utf-8",
                        len(body),
                    )
                    self.wfile.write(body)
                    return
                try:
                    payload = json.loads(
                        self.rfile.read(content_length).decode(
                            "utf-8", errors="strict"
                        )
                    )
                    if not isinstance(payload, dict):
                        raise CoinGroupFeedbackError(
                            "parser_feedback_payload_invalid"
                        )
                    result = submit_coin_group_parser_feedback(
                        conversation_db,
                        feedback_db,
                        payload,
                        reviewer=user_session,
                    )
                except (
                    CoinGroupFeedbackError,
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    OSError,
                    sqlite3.Error,
                ) as exc:
                    body = json.dumps(
                        {"error": _parser_feedback_error_message(exc)},
                        ensure_ascii=False,
                    ).encode("utf-8")
                    self._headers(
                        HTTPStatus.BAD_REQUEST,
                        "application/json; charset=utf-8",
                        len(body),
                    )
                    self.wfile.write(body)
                    return
                body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                self._headers(
                    HTTPStatus.OK,
                    "application/json; charset=utf-8",
                    len(body),
                )
                self.wfile.write(body)
                print(
                    json.dumps(
                        {
                            "event": "coin_group_parser_feedback_recorded",
                            "review_revision": result["review_revision"],
                            "ambiguous_field_count": len(
                                result["ambiguous_fields"]
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return
            if path == group_live_control_path:
                # The endpoint is authenticated and rejects cross-origin
                # browser submissions when the browser supplies Origin or
                # Referer.  This keeps the operator switch from becoming a
                # CSRF-able state mutation while preserving simple clients
                # that do not send either header.
                origin = self.headers.get("Origin")
                referer = self.headers.get("Referer")
                expected_host = self.headers.get("Host", "")
                for candidate in (origin, referer):
                    if not candidate:
                        continue
                    parsed_candidate = urlsplit(candidate)
                    if parsed_candidate.netloc and parsed_candidate.netloc != expected_host:
                        body = b'{"error":"cross_origin_forbidden"}'
                        self._headers(
                            HTTPStatus.FORBIDDEN,
                            "application/json; charset=utf-8",
                            len(body),
                        )
                        self.wfile.write(body)
                        return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                if content_length <= 0 or content_length > 1_024:
                    body = b'{"error":"invalid_control_request"}'
                    self._headers(
                        HTTPStatus.BAD_REQUEST,
                        "application/json; charset=utf-8",
                        len(body),
                    )
                    self.wfile.write(body)
                    return
                payload = self.rfile.read(content_length).decode(
                    "utf-8", errors="replace"
                )
                action = parse_qs(payload, keep_blank_values=True).get(
                    "action", [""]
                )[-1].strip().lower()
                if action not in {"connect", "disconnect"}:
                    body = b'{"error":"invalid_control_action"}'
                    self._headers(
                        HTTPStatus.BAD_REQUEST,
                        "application/json; charset=utf-8",
                        len(body),
                    )
                    self.wfile.write(body)
                    return
                group_live_control.set_enabled(
                    action == "connect", changed_by=user_session
                )
                try:
                    refresh_estimate()
                except Exception as exc:
                    print(
                        json.dumps(
                            {
                                "event": "group_live_control_refresh_failed",
                                "action": action,
                                "error": f"{type(exc).__name__}: {exc}"[:500],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                self._redirect(normalized)
                return
            if path == parse_offer_path:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    content_length = 0
                if content_length <= 0 or content_length > 2_000:
                    body = b'{"error":"invalid_text"}'
                    self._headers(
                        HTTPStatus.BAD_REQUEST,
                        "application/json; charset=utf-8",
                        len(body),
                    )
                    self.wfile.write(body)
                    return
                text = self.rfile.read(content_length).decode(
                    "utf-8", errors="replace"
                )
                body = json.dumps(
                    parse_offer_text(
                        text,
                        conversation_db=conversation_db,
                    ),
                    ensure_ascii=False,
                ).encode("utf-8")
                self._headers(
                    HTTPStatus.OK,
                    "application/json; charset=utf-8",
                    len(body),
                )
                self.wfile.write(body)
                return
            if path != manual_path:
                body = b"Not found"
                self._headers(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if write_token is None:
                body = b"Manual entry is disabled"
                self._headers(HTTPStatus.SERVICE_UNAVAILABLE, "text/plain; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = 0
            if content_length <= 0 or content_length > MAX_MANUAL_FORM_BYTES:
                self._redirect(manual_path + "?" + urlencode({"entry": "invalid"}))
                return
            payload = self.rfile.read(content_length).decode("utf-8", errors="replace")
            parsed = parse_qs(payload, keep_blank_values=True)
            form = {key: values[-1] for key, values in parsed.items()}
            try:
                if form.pop("entry_mode", "offer") == "confirm_existing":
                    insert_manual_trade_for_open_offer(conversation_db, form)
                else:
                    insert_manual_entry(conversation_db, form)
            except (ValueError, sqlite3.Error):
                self._redirect(manual_path + "?" + urlencode({"entry": "invalid"}))
                return
            try:
                refresh_estimate()
                status = "saved"
            except Exception:
                status = "saved_refresh_failed"
            self._redirect(manual_path + "?" + urlencode({"entry": status}))

        def log_message(self, message_format: str, *args: object) -> None:
            print(
                json.dumps(
                    {
                        "event": "http_access",
                        "remote": self.address_string(),
                        "message": message_format % args,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    return Handler


def start_web_server(
    host: str,
    port: int,
    route: str,
    state: StateStore,
    *,
    market_db: Path,
    conversation_db: Path,
    calibration_db: Path,
    feedback_db: Path,
    coin_group_staging_db: Path | None,
    write_token: str | None,
    refresh_estimate,
    group_live_control: GroupLiveInputControl,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(
        (host, port),
        handler_factory(
            route,
            state,
            market_db=market_db,
            conversation_db=conversation_db,
            calibration_db=calibration_db,
            feedback_db=feedback_db,
            coin_group_staging_db=coin_group_staging_db,
            write_token=write_token,
            refresh_estimate=refresh_estimate,
            group_live_control=group_live_control,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, name="web", daemon=True)
    thread.start()
    return server


def raw_post_from_message(message: object) -> RawPost | None:
    published = getattr(message, "date", None)
    if published is None:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    published_at = iso_utc(published)
    message_id = int(getattr(message, "id"))
    raw_text = getattr(message, "message", None) or ""
    return RawPost(
        message_id=message_id,
        published_at_utc=published_at,
        raw_text=raw_text,
    )


def persist_message(
    connection,
    source_code: str,
    username: str,
    message: object,
) -> int:
    post = raw_post_from_message(message)
    if post is None:
        return 0
    is_forwarded = getattr(message, "fwd_from", None) is not None
    events = [] if is_forwarded else parse_message(username, post.raw_text)
    if not events:
        return 0
    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            raw_post_id = upsert_raw_post(
                connection,
                source_code=source_code,
                post=post,
            )
            event_count = replace_price_events(
                connection,
                raw_post_id=raw_post_id,
                event_time_utc=post.published_at_utc,
                events=events,
            )
            infer_naghdp_trade_sides(connection, raw_post_id=raw_post_id)
            connection.commit()
            return event_count
        except sqlite3.OperationalError as exc:
            connection.rollback()
            retryable = "locked" in str(exc).lower() or "busy" in str(exc).lower()
            if not retryable or attempt == max_attempts - 1:
                raise
            time.sleep(0.2 * (attempt + 1))
        except Exception:
            connection.rollback()
            raise
    raise RuntimeError("unreachable persistence retry state")


def refresh_estimate(
    model: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    state_path: Path,
    state: StateStore,
    *,
    calibration_db: Path | None = None,
    end: datetime | None = None,
    group_live_control: GroupLiveInputControl | None = None,
    shadow_model_path: Path | None = None,
    shadow_state_path: Path | None = None,
    research_shadow_model_path: Path | None = None,
    research_shadow_state_path: Path | None = None,
    ml_shadow_model_path: Path | None = None,
    ml_shadow_state_path: Path | None = None,
    health_config: InputHealthConfig | None = None,
) -> dict[str, Any]:
    effective_end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    effective_calibration_db = calibration_db or DEFAULT_CALIBRATION_DB
    if effective_calibration_db.resolve() == conversation_db.resolve():
        raise ValueError(
            "calibration_db must differ from the promotion-owned conversation_db"
        )
    control = group_live_control.get() if group_live_control else {
        "enabled": True,
        "disabled_since_utc": None,
        "changed_at_utc": None,
        "changed_by": None,
    }
    enabled = bool(control.get("enabled", True))
    disabled_since = None
    if not enabled and control.get("disabled_since_utc"):
        try:
            disabled_since = parse_datetime(str(control["disabled_since_utc"]))
        except (TypeError, ValueError):
            disabled_since = effective_end
    reconnect_at = None
    if enabled and control.get("changed_at_utc"):
        try:
            reconnect_at = parse_datetime(str(control["changed_at_utc"]))
        except (TypeError, ValueError):
            reconnect_at = None
    # Reconcile first so observations that arrived while the group gate was
    # disconnected can calibrate this very estimate after reconnection.
    calibration_connection = open_calibration_connection(effective_calibration_db)
    observation_connection = open_conversation_read_connection(conversation_db)
    try:
        reconciliation = (
            reconcile_predictions(
                calibration_connection,
                now=effective_end,
                live_group_enabled=True,
                reconnect_at=reconnect_at,
                observation_connection=observation_connection,
            )
            if enabled
            else {
                "evaluated": 0,
                "residuals": [],
                "reconnect_bridged": 0,
                "status": "DEFERRED_GROUP_INPUT_DISCONNECTED",
            }
        )
        outcome_metrics = summarize_model_outcomes(
            calibration_connection,
            as_of=effective_end,
        )
        calibration_connection.commit()
    except Exception:
        calibration_connection.rollback()
        calibration_connection.close()
        raise
    finally:
        observation_connection.close()
    calibration_connection.close()
    estimate = estimate_rates(
        model,
        market_db,
        effective_end,
        conversation_db,
        live_group_events_enabled=enabled,
        group_live_events_before=disabled_since,
    )
    # ML residual is trained for the structural (pre-online-residual) book.
    # Keep an immutable branch so serving does not mix its baseline with the
    # main model's learned online calibration.
    ml_structural_estimate = deepcopy(estimate)
    finalization = {"term_structure_fixes": [], "low_date_rows": 0, "band_widened": 0}
    calibration_connection = open_calibration_connection(effective_calibration_db)
    try:
        online_metadata = apply_snapshot_calibration(
            calibration_connection,
            settlements=estimate.get("settlements", {}),
        )
        recent_realized_metadata = apply_recent_realized_snapshot_calibration(
            calibration_connection,
            settlements=estimate.get("settlements", {}),
            as_of=effective_end,
        )
        finalization = finalize_deterministic_book(estimate)
        calibration_connection.commit()
    except Exception:
        calibration_connection.rollback()
        calibration_connection.close()
        raise
    calibration_connection.close()
    control["last_applied_at_utc"] = iso_utc(datetime.now(timezone.utc))
    estimate["live_group_input_control"] = control
    estimate["service_status"] = "RUNNING"
    estimate["manual_entry_counts"] = manual_entry_counts(conversation_db)
    light = shadow_light_mode()
    shadow_meta = run_shadow_parallel(
        live_estimate=estimate,
        market_db=market_db,
        conversation_db=conversation_db,
        end=effective_end,
        shadow_model_path=shadow_model_path,
        shadow_state_path=shadow_state_path or DEFAULT_SHADOW_STATE,
        live_group_events_enabled=enabled,
        group_live_events_before=disabled_since,
        finalize_book=finalize_deterministic_book,
    )
    research_meta = run_shadow_parallel(
        live_estimate=estimate,
        market_db=market_db,
        conversation_db=conversation_db,
        end=effective_end,
        shadow_model_path=research_shadow_model_path
        if research_shadow_model_path is not None
        else DEFAULT_RESEARCH_SHADOW_MODEL,
        shadow_state_path=research_shadow_state_path or DEFAULT_RESEARCH_SHADOW_STATE,
        live_group_events_enabled=enabled,
        group_live_events_before=disabled_since,
        finalize_book=finalize_deterministic_book,
    )
    ml_meta = run_ml_residual_shadow(
        live_estimate=ml_structural_estimate,
        end=effective_end,
        artifact_path=ml_shadow_model_path
        if ml_shadow_model_path is not None
        else DEFAULT_ML_SHADOW_MODEL,
        shadow_state_path=ml_shadow_state_path or DEFAULT_ML_SHADOW_STATE_PATH,
        comparison_estimate=estimate,
        finalize_book=finalize_deterministic_book,
    )
    estimate["shadow_parallel"] = {
        "enabled": shadow_meta.get("enabled"),
        "status": shadow_meta.get("status"),
        "shadow_model_path": shadow_meta.get("shadow_model_path"),
        "shadow_model_kind": shadow_meta.get("shadow_model_kind"),
        "comparison_vs_live": shadow_meta.get("comparison_vs_live"),
        "authoritative_override": False,
        "state_path": str(shadow_state_path or DEFAULT_SHADOW_STATE),
        "label": "سایه ۱ — مدل قبلی زنده",
        "shared_live_window_end_utc": estimate.get("window_end_utc"),
        "light_mode": light,
    }
    estimate["research_shadow_parallel"] = {
        "enabled": research_meta.get("enabled"),
        "status": research_meta.get("status"),
        "shadow_model_path": research_meta.get("shadow_model_path"),
        "shadow_model_kind": research_meta.get("shadow_model_kind"),
        "comparison_vs_live": research_meta.get("comparison_vs_live"),
        "authoritative_override": False,
        "state_path": str(research_shadow_state_path or DEFAULT_RESEARCH_SHADOW_STATE),
        "label": "سایه ۲ — کاندید بازگشایی صبح",
        "shared_live_window_end_utc": estimate.get("window_end_utc"),
        "light_mode": light,
    }
    estimate["ml_shadow_parallel"] = {
        "enabled": ml_meta.get("enabled"),
        "status": ml_meta.get("status"),
        "shadow_model_path": ml_meta.get("shadow_model_path"),
        "shadow_model_kind": ml_meta.get("shadow_model_kind"),
        "comparison_vs_live": ml_meta.get("comparison_vs_live"),
        "authoritative_override": False,
        "state_path": str(ml_shadow_state_path or DEFAULT_ML_SHADOW_STATE_PATH),
        "label": "سایه ۳ — مدل یادگیری ماشین",
        "shared_live_window_end_utc": estimate.get("window_end_utc"),
        "light_mode": light,
    }
    # Main book learns from shadows vs morning truth (bounded; not a model swap).
    # Cross-calibration is research-only unless an operator explicitly opts
    # in; a shadow must never promote itself through a default environment.
    cross_apply = (not light) and _env_flag("COIN_RATE_ESTIMATOR_SHADOW_CROSS_CAL_APPLY", "0")
    if light:
        estimate["shadow_cross_calibration"] = {
            "status": "LIGHT_MODE_SKIPPED",
            "applied_count": 0,
        }
    else:
        estimate["shadow_cross_calibration"] = maybe_run_shadow_cross_calibration(
            live_estimate=estimate,
            conversation_db=conversation_db,
            end=effective_end,
            shadow_state_paths={
                "سایه۱-قبلی": shadow_state_path or DEFAULT_SHADOW_STATE,
                "سایه۲-بازگشایی": research_shadow_state_path or DEFAULT_RESEARCH_SHADOW_STATE,
                "سایه۳-ML": ml_shadow_state_path or DEFAULT_ML_SHADOW_STATE_PATH,
            },
            apply=cross_apply,
        )
    if (not light) and estimate["shadow_cross_calibration"].get("applied_count"):
        finalization = finalize_deterministic_book(estimate)
    # MAIN_ONLINE keeps its 30-second learning cadence.  Accuracy is a separate
    # cohort: the same final main book plus all three challengers are recorded
    # only when all four exist, at one exact timestamp.  This prevents a later
    # trade from scoring a 30-second main forecast against a two-minute shadow
    # forecast and calling that a fair comparison.
    comparison_books = (
        (MAIN_COMPARISON_MODEL_ID, str(model.get("model_kind") or "MAIN"), estimate),
        ("SHADOW1_PREVIOUS", str(shadow_meta.get("shadow_model_kind") or "UNKNOWN"), shadow_meta.get("estimate")),
        ("SHADOW2_MORNING_REOPEN", str(research_meta.get("shadow_model_kind") or "UNKNOWN"), research_meta.get("estimate")),
        ("SHADOW3_ML_RESIDUAL", str(ml_meta.get("shadow_version") or "UNKNOWN"), ml_meta.get("estimate")),
    )
    comparison_ready = all(isinstance(book, dict) for _, _, book in comparison_books)
    comparison_commodities: dict[str, set[str]] = {}
    if comparison_ready:
        settlement_sets = [
            set((book.get("settlements") or {}).keys())
            for _, _, book in comparison_books
            if isinstance(book, dict)
        ]
        shared_settlements = set.intersection(*settlement_sets) if settlement_sets else set()
        for settlement in shared_settlements:
            book_commodities: list[set[str]] = []
            for _, _, book in comparison_books:
                payload = (book.get("settlements") or {}).get(settlement, {})
                names: set[str] = set()
                for rate in payload.get("rates", []) if isinstance(payload, dict) else []:
                    if not isinstance(rate, dict):
                        continue
                    try:
                        value = float(rate.get("estimated_price_toman"))
                    except (TypeError, ValueError):
                        continue
                    name = str(rate.get("commodity_name") or "")
                    if name and math.isfinite(value) and value > 0:
                        names.add(name)
                book_commodities.append(names)
            comparison_commodities[str(settlement)] = (
                set.intersection(*book_commodities) if book_commodities else set()
            )
    prediction_books = [
        (
            MAIN_MODEL_ID,
            str(model.get("model_kind") or "MAIN"),
            estimate,
            LEARNING_EVALUATION_ROLE,
            None,
        )
    ]
    if comparison_ready:
        prediction_books.extend(
            (
                model_id,
                model_version,
                book,
                COMPARISON_EVALUATION_ROLE,
                effective_end,
            )
            for model_id, model_version, book in comparison_books
        )
    calibration_connection = open_calibration_connection(effective_calibration_db)
    try:
        ensure_online_schema(calibration_connection)
        predictions_recorded = 0
        predictions_by_model: dict[str, int] = {}
        for model_id, model_version, book, evaluation_role, comparison_cohort in prediction_books:
            if not isinstance(book, dict):
                continue
            count = 0
            for settlement, payload in (book.get("settlements") or {}).items():
                if not isinstance(payload, dict):
                    continue
                rates = list(payload.get("rates", []))
                if evaluation_role == COMPARISON_EVALUATION_ROLE:
                    shared = comparison_commodities.get(str(settlement), set())
                    rates = [
                        rate
                        for rate in rates
                        if isinstance(rate, dict)
                        and str(rate.get("commodity_name") or "") in shared
                    ]
                count += record_predictions(
                    calibration_connection,
                    prediction_time=effective_end,
                    settlement=str(settlement),
                    rates=rates,
                    group_live_enabled=enabled,
                    model_id=model_id,
                    model_version=model_version,
                    evaluation_role=evaluation_role,
                    comparison_cohort=comparison_cohort,
                )
            predictions_by_model[model_id] = count
            predictions_recorded += count
        # One learning book plus four comparison books write where one used to.
        # Bounding the ledger cannot wait
        # for an operator to remember a CLI, so maintenance runs here — but at
        # most hourly, so the refresh path stays free.
        ledger_maintenance = maintain_prediction_ledger(
            calibration_connection, as_of=effective_end
        )
        calibration_connection.commit()
    except Exception:
        calibration_connection.rollback()
        calibration_connection.close()
        raise
    calibration_connection.close()
    estimate["online_residual_learning"] = {
        "mode": "BOUNDED_ONLINE_RESIDUAL_CALIBRATION",
        "reconciliation": reconciliation,
        # Accuracy against later realised trades.  This is the only promotion
        # evidence; the per-shadow ``comparison_vs_live`` block is divergence
        # from the main book, not a quality score.
        "model_outcomes": outcome_metrics,
        "calibration": online_metadata,
        "recent_realized_calibration": recent_realized_metadata,
        "predictions_recorded": predictions_recorded,
        "predictions_by_model": predictions_by_model,
        "comparison_cohort_ready": comparison_ready,
        "ledger_maintenance": ledger_maintenance,
        "automatic_model_weight_promotion": False,
        "deterministic_finalization": finalization,
    }
    if health_config is not None:
        health = build_estimator_input_health(
            estimate,
            as_of=effective_end,
            config=health_config,
        )
        estimate["input_health"] = health
        estimate["service_status"] = {
            "HEALTHY": "RUNNING",
            "DEGRADED": "DEGRADED",
            "CRITICAL": "INPUT_CRITICAL",
        }.get(str(health.get("status") or "CRITICAL"), "INPUT_CRITICAL")
    state.set(estimate)
    write_json_atomic(state_path, estimate, mode=0o644)
    return estimate


async def estimation_loop(
    model: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    calibration_db: Path,
    state_path: Path,
    state: StateStore,
    group_live_control: GroupLiveInputControl,
    *,
    health_config: InputHealthConfig | None = None,
    shadow_model_path: Path | None = None,
    shadow_state_path: Path | None = None,
    research_shadow_model_path: Path | None = None,
    research_shadow_state_path: Path | None = None,
    ml_shadow_model_path: Path | None = None,
    ml_shadow_state_path: Path | None = None,
) -> None:
    last_run: datetime | None = None
    refresh_seconds = estimate_refresh_seconds()
    while True:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if last_run is None or (now - last_run).total_seconds() >= refresh_seconds:
            end = now
            try:
                estimate = refresh_estimate(
                    model,
                    market_db,
                    conversation_db,
                    state_path,
                    state,
                    calibration_db=calibration_db,
                    end=end,
                    group_live_control=group_live_control,
                    shadow_model_path=shadow_model_path,
                    shadow_state_path=shadow_state_path,
                    research_shadow_model_path=research_shadow_model_path,
                    research_shadow_state_path=research_shadow_state_path,
                    ml_shadow_model_path=ml_shadow_model_path,
                    ml_shadow_state_path=ml_shadow_state_path,
                    health_config=health_config,
                )
                print(
                    json.dumps(
                        {
                            "event": "estimate_complete",
                            "window_end_utc": estimate["window_end_utc"],
                            "settlements": list(estimate["settlements"]),
                            "input_health_status": (
                                estimate.get("input_health") or {}
                            ).get("status"),
                            "input_health_reason_codes": (
                                estimate.get("input_health") or {}
                            ).get("reason_codes", []),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                last_run = end
            except Exception as exc:
                failed = state.get()
                failed["service_status"] = "ESTIMATION_ERROR"
                failed["last_error"] = f"{type(exc).__name__}: {exc}"[:500]
                failed["generated_at_utc"] = iso_utc(datetime.now(timezone.utc))
                state.set(failed)
                print(
                    json.dumps(
                        {"event": "estimate_failed", "error": failed["last_error"]},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        await asyncio.sleep(1)


async def _public_collector_heartbeat(
    client: object,
    health_path: Path,
    *,
    channel_count: int,
) -> None:
    while True:
        connected = bool(client.is_connected())  # type: ignore[attr-defined]
        update_probe_state(
            health_path,
            source="PUBLIC_MARKET_TELEGRAM",
            status="HEALTHY" if connected else "FAILED",
            successful=connected,
            error_code=None if connected else "TELEGRAM_DISCONNECTED",
            details={"connected": connected, "channel_count": channel_count},
        )
        if not connected:
            raise RuntimeError("public_market_telegram_disconnected")
        await asyncio.sleep(15)


async def live_collection_loop(
    market_db: Path,
    backfill_minutes: int,
    channels: tuple[str, ...],
    *,
    health_state_path: Path | None = None,
) -> None:
    health_path = health_state_path or market_db.parent / PUBLIC_COLLECTOR_HEALTH_NAME
    update_probe_state(
        health_path,
        source="PUBLIC_MARKET_TELEGRAM",
        status="STARTING",
        successful=None,
        details={"configured_channel_count": len(channels)},
    )
    connection = None
    client = None
    try:
        try:
            from telethon import TelegramClient, events
        except ImportError as exc:
            raise RuntimeError("Telethon is unavailable in collector .deps") from exc

        settings = Settings.with_interactive_credentials()
        connection = connect(market_db)
        initialize(connection)
        client = TelegramClient(
            str(settings.session_path),
            settings.api_id,
            settings.api_hash,
            flood_sleep_threshold=60,
            sequential_updates=True,
        )
        await client.connect()
        if not await client.is_user_authorized():
            if not settings.phone:
                raise RuntimeError(
                    "Telegram session is not authorised and TELEGRAM_PHONE is not configured"
                )
            await client.start(phone=settings.phone)
        entities = []
        channel_by_peer: dict[int, tuple[str, str, str]] = {}
        for requested in channels:
            entity = await client.get_entity(requested)
            username = getattr(entity, "username", None) or requested
            title = getattr(entity, "title", None) or username
            source_code = source_code_for_channel(username)
            peer_id = int(getattr(entity, "id"))
            channel_by_peer[peer_id] = (source_code, username, title)
            entities.append(entity)

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=backfill_minutes)
        for entity in entities:
            peer_id = int(getattr(entity, "id"))
            source_code, username, title = channel_by_peer[peer_id]
            messages = 0
            price_events = 0
            async for message in client.iter_messages(entity):
                published = getattr(message, "date", None)
                if published is None:
                    continue
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < cutoff:
                    break
                price_events += persist_message(connection, source_code, username, message)
                messages += 1
            side_links = (
                infer_naghdp_trade_sides(connection)
                if username.lower() == "naghdp"
                else {"examined": 0, "matched": 0, "unresolved": 0}
            )
            connection.commit()
            print(
                json.dumps(
                    {
                        "event": "live_backfill_complete",
                        "channel": f"@{username}",
                        "title": title,
                        "messages": messages,
                        "price_events": price_events,
                        "trade_side_links": side_links,
                        "cutoff_utc": iso_utc(cutoff),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        @client.on(events.NewMessage(chats=entities))
        async def handle_new_message(event) -> None:
            message = event.message
            peer = getattr(message, "peer_id", None)
            peer_id = getattr(peer, "channel_id", None)
            metadata = channel_by_peer.get(int(peer_id)) if peer_id is not None else None
            if metadata is None:
                return
            source_code, username, title = metadata
            count = persist_message(connection, source_code, username, message)
            published = getattr(message, "date", None)
            print(
                json.dumps(
                    {
                        "event": "live_message",
                        "channel": f"@{username}",
                        "title": title,
                        "message_id": int(message.id),
                        "source_datetime_utc": iso_utc(published),
                        "price_events": count,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

        print(
            json.dumps(
                {
                    "event": "telegram_live_ready",
                    "channels": [f"@{item[1]}" for item in channel_by_peer.values()],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        update_probe_state(
            health_path,
            source="PUBLIC_MARKET_TELEGRAM",
            status="HEALTHY",
            successful=True,
            details={"connected": True, "channel_count": len(entities)},
        )
        await asyncio.gather(
            client.run_until_disconnected(),
            _public_collector_heartbeat(
                client,
                health_path,
                channel_count=len(entities),
            ),
        )
    except asyncio.CancelledError:
        update_probe_state(
            health_path,
            source="PUBLIC_MARKET_TELEGRAM",
            status="STOPPED",
            successful=None,
            error_code="COLLECTOR_CANCELLED",
        )
        raise
    except BaseException as exc:
        update_probe_state(
            health_path,
            source="PUBLIC_MARKET_TELEGRAM",
            status="FAILED",
            successful=False,
            error_code=f"TELEGRAM_{type(exc).__name__.upper()}",
        )
        raise
    finally:
        if client is not None:
            await client.disconnect()
        if connection is not None:
            connection.close()


async def external_collection_loop(
    market_db: Path,
    *,
    wallex_interval: int,
    ime_interval: int,
    ime_timeout: float,
    health_state_path: Path | None = None,
) -> None:
    if wallex_interval <= 0 or ime_interval < 0:
        raise ValueError(
            "Wallex interval must be positive and IME interval non-negative"
        )
    connection = connect(market_db)
    initialize(connection)
    health_path = health_state_path or market_db.parent / EXTERNAL_MARKET_HEALTH_NAME
    last_ime_attempt: datetime | None = None
    if ime_interval == 0:
        update_probe_state(
            health_path,
            source="IME_REALTIME_BOARD",
            status="DISABLED",
            successful=True,
            details={"configured_interval_seconds": 0},
        )
    try:
        while True:
            cycle_started = datetime.now(timezone.utc)
            try:
                wallex_rows = await asyncio.to_thread(fetch_wallex_live)
                if not wallex_rows:
                    raise ExternalSourceError("wallex_empty_snapshot")
                upsert_external_observations(connection, wallex_rows)
                update_probe_state(
                    health_path,
                    source="WALLEX_PUBLIC_API",
                    status="HEALTHY",
                    successful=True,
                    details={
                        "observation_count": len(wallex_rows),
                        "configured_interval_seconds": wallex_interval,
                    },
                )
                print(
                    json.dumps(
                        {
                            "event": "external_live",
                            "source": "WALLEX_PUBLIC_API",
                            "observations": len(wallex_rows),
                            "observed_at_utc": wallex_rows[0].observed_at_utc,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as exc:
                connection.rollback()
                update_probe_state(
                    health_path,
                    source="WALLEX_PUBLIC_API",
                    status="FAILED",
                    successful=False,
                    error_code=f"WALLEX_{type(exc).__name__.upper()}",
                    details={"configured_interval_seconds": wallex_interval},
                )
                print(
                    json.dumps(
                        {
                            "event": "external_live_failed",
                            "source": "WALLEX_PUBLIC_API",
                            "error": f"{type(exc).__name__}: {exc}"[:500],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            try:
                paxg_rows = await asyncio.to_thread(fetch_binance_paxg_live)
                if not paxg_rows:
                    raise ExternalSourceError("binance_paxg_empty_snapshot")
                upsert_external_observations(connection, paxg_rows)
                update_probe_state(
                    health_path,
                    source="BINANCE_PAXG_PUBLIC_API",
                    status="HEALTHY",
                    successful=True,
                    details={
                        "observation_count": len(paxg_rows),
                        "configured_interval_seconds": wallex_interval,
                        "corroborating_books": 2,
                    },
                )
                print(
                    json.dumps(
                        {
                            "event": "external_live",
                            "source": "BINANCE_PAXG_PUBLIC_API",
                            "observations": len(paxg_rows),
                            "observed_at_utc": paxg_rows[0].observed_at_utc,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except Exception as exc:
                connection.rollback()
                update_probe_state(
                    health_path,
                    source="BINANCE_PAXG_PUBLIC_API",
                    status="FAILED",
                    successful=False,
                    error_code=f"BINANCE_PAXG_{type(exc).__name__.upper()}",
                    details={
                        "configured_interval_seconds": wallex_interval,
                        "corroborating_books": 2,
                    },
                )
                print(
                    json.dumps(
                        {
                            "event": "external_live_failed",
                            "source": "BINANCE_PAXG_PUBLIC_API",
                            "error": f"{type(exc).__name__}: {exc}"[:500],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

            should_poll_ime = ime_interval > 0 and (
                last_ime_attempt is None
                or (cycle_started - last_ime_attempt).total_seconds() >= ime_interval
            )
            if should_poll_ime:
                last_ime_attempt = cycle_started
                try:
                    ime_rows = await fetch_ime_live(timeout=ime_timeout)
                    if not ime_rows:
                        raise ExternalSourceError("ime_empty_snapshot")
                    upsert_external_observations(connection, ime_rows)
                    update_probe_state(
                        health_path,
                        source="IME_REALTIME_BOARD",
                        status="HEALTHY",
                        successful=True,
                        details={
                            "observation_count": len(ime_rows),
                            "configured_interval_seconds": ime_interval,
                        },
                    )
                    print(
                        json.dumps(
                            {
                                "event": "external_live",
                                "source": "IME_REALTIME_BOARD",
                                "observations": len(ime_rows),
                                "observed_at_utc": ime_rows[0].observed_at_utc,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                except (ExternalSourceError, OSError, TimeoutError, sqlite3.DatabaseError) as exc:
                    connection.rollback()
                    update_probe_state(
                        health_path,
                        source="IME_REALTIME_BOARD",
                        status="FAILED",
                        successful=False,
                        error_code=f"IME_{type(exc).__name__.upper()}",
                        details={"configured_interval_seconds": ime_interval},
                    )
                    print(
                        json.dumps(
                            {
                                "event": "external_live_failed",
                                "source": "IME_REALTIME_BOARD",
                                "error": f"{type(exc).__name__}: {exc}"[:500],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            elapsed = (datetime.now(timezone.utc) - cycle_started).total_seconds()
            await asyncio.sleep(max(1.0, wallex_interval - elapsed))
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--path", required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument(
        "--shadow-model",
        type=Path,
        default=DEFAULT_SHADOW_MODEL,
        help="Optional parallel shadow model; never overrides live output.",
    )
    parser.add_argument(
        "--shadow-state",
        type=Path,
        default=DEFAULT_SHADOW_STATE,
        help="Isolated JSON state for the parallel shadow estimate.",
    )
    parser.add_argument(
        "--research-shadow-model",
        type=Path,
        default=DEFAULT_RESEARCH_SHADOW_MODEL,
        help="Second shadow: morning-reopen research candidate; never overrides live.",
    )
    parser.add_argument(
        "--research-shadow-state",
        type=Path,
        default=DEFAULT_RESEARCH_SHADOW_STATE,
        help="Isolated JSON state for the morning-reopen research shadow.",
    )
    parser.add_argument(
        "--ml-shadow-model",
        type=Path,
        default=DEFAULT_ML_SHADOW_MODEL,
        help="Sklearn residual ML artifact (.joblib); never overrides live.",
    )
    parser.add_argument(
        "--ml-shadow-state",
        type=Path,
        default=DEFAULT_ML_SHADOW_STATE_PATH,
        help="Isolated JSON state for the ML residual shadow.",
    )
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument(
        "--conversation-db", type=Path, default=DEFAULT_CONVERSATION_DB
    )
    parser.add_argument(
        "--calibration-db", type=Path, default=DEFAULT_CALIBRATION_DB,
        help="Mutable prediction ledger kept outside the promoted conversation input.",
    )
    parser.add_argument(
        "--parser-feedback-db",
        type=Path,
        default=DEFAULT_REVIEW_DECISIONS_DB,
        help="Privacy-safe operator corrections used by the coin-group parser.",
    )
    parser.add_argument(
        "--coin-group-staging-db",
        type=Path,
        default=None,
        help=(
            "Optional external three-day private staging database used only "
            "to render authenticated parser review text."
        ),
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument(
        "--group-live-control",
        type=Path,
        default=DEFAULT_GROUP_LIVE_CONTROL,
        help="Persistent operator switch for new live coin-group events",
    )
    parser.add_argument(
        "--manual-entry-token-file", type=Path, default=DEFAULT_WRITE_TOKEN_FILE
    )
    parser.add_argument("--disable-manual-entry", action="store_true")
    parser.add_argument("--backfill-minutes", type=int, default=5)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--no-external", action="store_true")
    parser.add_argument("--wallex-interval", type=int, default=10)
    parser.add_argument("--ime-interval", type=int, default=60)
    parser.add_argument("--ime-timeout", type=float, default=8.0)
    parser.add_argument("--channel", action="append")
    return parser


async def async_main(
    args: argparse.Namespace,
    state: StateStore,
    model: dict[str, Any],
    group_live_control: GroupLiveInputControl,
    health_config: InputHealthConfig,
) -> None:
    tasks = [
        asyncio.create_task(
            estimation_loop(
                model,
                args.market_db,
                args.conversation_db,
                args.calibration_db,
                args.state,
                state,
                group_live_control,
                health_config=health_config,
                shadow_model_path=args.shadow_model,
                shadow_state_path=args.shadow_state,
                research_shadow_model_path=args.research_shadow_model,
                research_shadow_state_path=args.research_shadow_state,
                ml_shadow_model_path=args.ml_shadow_model,
                ml_shadow_state_path=args.ml_shadow_state,
            ),
            name="estimator",
        )
    ]
    if not args.no_telegram:
        tasks.append(
            asyncio.create_task(
                live_collection_loop(
                    args.market_db,
                    args.backfill_minutes,
                    tuple(args.channel or DEFAULT_CHANNELS),
                    health_state_path=health_config.public_telegram_state,
                ),
                name="telegram",
            )
        )
    if not args.no_external:
        tasks.append(
            asyncio.create_task(
                external_collection_loop(
                    args.market_db,
                    wallex_interval=args.wallex_interval,
                    ime_interval=args.ime_interval,
                    ime_timeout=args.ime_timeout,
                    health_state_path=health_config.external_market_state,
                ),
                name="external-markets",
            )
        )
    await asyncio.gather(*tasks)


def main() -> int:
    args = build_parser().parse_args()
    ensure_manual_entry_schema(args.conversation_db)
    prepare_calibration_store(args.calibration_db, args.conversation_db)
    ensure_coin_group_feedback_store(args.parser_feedback_db)
    model = load_model(args.model)
    state = StateStore()
    group_live_control = GroupLiveInputControl(args.group_live_control)
    health_config = input_health_config(args.market_db, args.conversation_db)
    route = "/" + args.path.strip("/")
    write_token = (
        None
        if args.disable_manual_entry
        else read_or_create_write_token(args.manual_entry_token_file)
    )

    def refresh_from_web() -> None:
        refresh_estimate(
            model,
            args.market_db,
            args.conversation_db,
            args.state,
            state,
            calibration_db=args.calibration_db,
            group_live_control=group_live_control,
            shadow_model_path=args.shadow_model,
            shadow_state_path=args.shadow_state,
            research_shadow_model_path=args.research_shadow_model,
            research_shadow_state_path=args.research_shadow_state,
            ml_shadow_model_path=args.ml_shadow_model,
            ml_shadow_state_path=args.ml_shadow_state,
            health_config=health_config,
        )

    server = start_web_server(
        args.host,
        args.port,
        route,
        state,
        market_db=args.market_db,
        conversation_db=args.conversation_db,
        calibration_db=args.calibration_db,
        feedback_db=args.parser_feedback_db,
        coin_group_staging_db=args.coin_group_staging_db,
        write_token=write_token,
        refresh_estimate=refresh_from_web,
        group_live_control=group_live_control,
    )
    print(
        json.dumps(
            {
                "event": "web_ready",
                "listen": f"{args.host}:{args.port}",
                "path": route,
                "data_path": route + "/data.json",
                "manual_entry_path": route + "/manual-entry",
                "manual_entry_enabled": write_token is not None,
                "manual_entry_token_file": (
                    str(args.manual_entry_token_file) if write_token is not None else None
                ),
                "group_live_control_file": str(args.group_live_control),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        asyncio.run(async_main(args, state, model, group_live_control, health_config))
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
