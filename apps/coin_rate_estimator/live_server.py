#!/usr/bin/env python3
"""Live Telegram collector, one-minute estimator, and read-only web page."""

from __future__ import annotations

import argparse
import asyncio
import glob
import hmac
import html
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time
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
    DEFAULT_CONVERSATION_DB,
    DEFAULT_MARKET_DB,
    DEFAULT_MODEL,
    COMMODITY_SPECS,
    NO_DATA_TOKEN,
    estimate_rates,
    iso_utc,
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
from telegram_price_collector.external_collectors import (  # noqa: E402
    ExternalSourceError,
    fetch_ime_live,
    fetch_wallex_live,
)
from telegram_price_collector.models import RawPost  # noqa: E402
from telegram_price_collector.parsers import parse_message  # noqa: E402
from online_recalibration import (  # noqa: E402
    apply_snapshot_calibration,
    ensure_schema as ensure_online_schema,
    reconcile_predictions,
    record_predictions,
)


TEHRAN = ZoneInfo("Asia/Tehran")
DEFAULT_STATE = RUNTIME_ROOT / "state.json"
DEFAULT_WRITE_TOKEN_FILE = RUNTIME_ROOT / "manual-entry.token"
DEFAULT_GROUP_LIVE_CONTROL = RUNTIME_ROOT / "group-live-input-control.json"
DEFAULT_DASHBOARD_CREDENTIALS_FILE = RUNTIME_ROOT / "dashboard-credentials.json"
MAX_MANUAL_FORM_BYTES = 16_384
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


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
        ensure_online_schema(connection)
        connection.commit()
    finally:
        connection.close()


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
    price = normalize_integer(form.get("price", ""), field="قیمت آفر")
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
        trade_price = normalize_integer(
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
        return (
            f"<strong>{fa_number(rate.get('estimated_project_price'))}</strong>"
            f"<small>{fa_number(tolerance.get('lower_project_price'))} تا "
            f"{fa_number(tolerance.get('upper_project_price'))}</small>"
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
    """Return only the compact live dashboard feed, separated by source group."""
    result = {"group_1_offers": [], "group_2_offers": [], "group_1_trades": [], "group_2_trades": []}
    if not conversation_db.exists():
        return result
    try:
        connection = sqlite3.connect(f"file:{conversation_db.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        for group in ("group_1", "group_2"):
            offer_rows = connection.execute(
                """
                SELECT m.event_time_utc,o.commodity,o.side,o.price,o.quantity,o.settlement
                FROM offers o JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id
                WHERE m.source_html_file=? ORDER BY m.event_time_utc DESC LIMIT 5
                """,
                (group,),
            ).fetchall()
            trade_rows = connection.execute(
                """
                SELECT t.event_time_utc,t.commodity,t.side,t.price,t.quantity,t.settlement
                FROM confirmed_trades t JOIN messages m
                  ON m.import_id=t.import_id AND m.message_id=t.confirmation_message_id
                WHERE m.source_html_file=? ORDER BY t.event_time_utc DESC LIMIT 3
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
        rows.append(
            "<li>"
            f"<time>{fa_datetime(item.get('event_time_utc'))}</time> "
            f"<strong>{html.escape(str(item.get('commodity') or '—'))}</strong> "
            f"{side.get(str(item.get('side')), '—')} {fa_number(item.get('price'))}{quantity}"
            f" <small>{SETTLEMENT_FA.get(str(item.get('settlement')), '—')}</small>"
            "</li>"
        )
    return "".join(rows)


def render_group_activity_fragment(conversation_db: Path) -> str:
    activity = read_recent_group_activity(conversation_db)
    return f"""<section><div class="section-head"><h2>آخرین فعالیت گروه‌های معاملاتی</h2><span class="badge">داده‌های ثبت‌شده</span></div>
      <div class="group-grid">
        <article class="feed-card"><h3>۵ آفر آخر — گروه ۱</h3><ul>{render_live_rows(activity.get('group_1_offers', []), kind='offer')}</ul></article>
        <article class="feed-card"><h3>۵ آفر آخر — گروه ۲</h3><ul>{render_live_rows(activity.get('group_2_offers', []), kind='offer')}</ul></article>
        <article class="feed-card"><h3>۳ معاملهٔ آخر — گروه ۱</h3><ul>{render_live_rows(activity.get('group_1_trades', []), kind='trade')}</ul></article>
        <article class="feed-card"><h3>۳ معاملهٔ آخر — گروه ۲</h3><ul>{render_live_rows(activity.get('group_2_trades', []), kind='trade')}</ul></article>
      </div>
    </section>"""


def find_analytics_db(conversation_db: Path) -> Path | None:
    snapshots = sorted(glob.glob("/srv/trading-bot-three-site-staging-data/coin-intelligence/private-channel-ingest/pipeline/training-snapshots/group-training-*.sqlite3"))
    if snapshots:
        return Path(snapshots[-1])
    shadow = Path("/srv/trading-bot-three-site-staging-data/coin-intelligence/private-channel-ingest/pipeline/group_training_dataset_shadow.sqlite3")
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

    empty_result = {
        "range_type": range_type,
        "range_label": label,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "start_shamsi": start_shamsi or "",
        "end_shamsi": end_shamsi or "",
        "groups": {1: {}, 2: {}},
    }
    if not db_path or not db_path.exists():
        return empty_result

    try:
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
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
            "groups": groups_data,
        }
    except Exception:
        return empty_result


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


def render_input_cards(inputs: dict[str, dict[str, Any]]) -> str:
    labels = {
        "melted_gold": "طلا آب‌شده",
        "generic_coin": "سکه عمومی",
        "xauusd": "اونس جهانی",
        "usd": "دلار هرات",
        "usdt": "تتر / تومان",
    }
    cards = []
    for key, label in labels.items():
        value = inputs.get(key, {})
        estimated = key == "usd" and bool(value.get("is_estimated"))
        observed = value.get("status") in {"OBSERVED", "ESTIMATED"}
        if estimated:
            label = (
                "دلار هرات نقدی"
                if value.get("market_movement_driver")
                else "دلار هرات فردایی"
            )
        rendered = fa_number(value.get("average_price"), decimals=2 if key == "xauusd" else 0)
        samples = fa_number(value.get("sample_count", 0))
        css = "estimated" if estimated else ("observed" if observed else "no-data")
        if estimated:
            trend_code = value.get("market_direction") or value.get("usdt_trend")
            direction = {
                "UP": "افزایشی",
                "DOWN": "کاهشی",
                "NEUTRAL": "خنثی",
            }.get(str(trend_code), "نامشخص")
            detail = f"برآورد روند: {direction}"
        else:
            detail = f"تعداد رویداد: {samples}"
        cards.append(
            f"<div class='input-card {css}'>"
            f"<span>{label}</span><strong>{rendered}</strong>"
            f"<small>{detail}</small></div>"
        )
    return "".join(cards)


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


def render_page(
    state: dict[str, Any], *, manual_path: str = "/manual-entry", analytics_path: str = "/analytics",
    logout_path: str = "/logout", user_session: str | None = None,
    estimate_path: str = "/estimates.html", activity_path: str = "/activity.html", write_enabled: bool = False,
    flash: str | None = None, open_manual_offers: list[dict[str, Any]] | None = None,
    estimate_fragment: bool = False, page: str = "home",
    market_db: Path | None = None, conversation_db: Path | None = None,
    group_live_control_path: str = "/group-live-control",
    group_live_control: dict[str, Any] | None = None,
) -> bytes:
    generated = fa_datetime(state.get("generated_at_utc"))
    window_start = fa_datetime(state.get("window_start_utc"))
    window_end = fa_datetime(state.get("window_end_utc"))
    service_status = html.escape(str(state.get("service_status", "RUNNING")))
    settlements = state.get("settlements", {})
    inputs = settlements.get("CASH", {}).get("inputs") or settlements.get("TOMORROW", {}).get("inputs") or {}
    melted = read_melted_minute_averages(market_db, state.get("window_end_utc")) if market_db else {}
    melted_cards = "".join(
        f"<div class='input-card {'observed' if value.get('average_price') is not None else 'no-data'}'>"
        f"<span>طلا آب‌شده {'کاغذی' if form == 'PAPER' else 'فیزیکی'}</span>"
        f"<strong>{fa_number(value.get('average_price'))}</strong>"
        f"<small>{fa_number(value.get('sample_count', 0))} رویداد</small></div>"
        for form, value in melted.items()
    )
    ticker_cards = f"<div class='inputs'>{render_input_cards(inputs)}{melted_cards}</div>"
    table_section = f"""
      <section class="table-section">
        <div class='section-head'>
          <h2>لیست نرخ سکه و مسکوکات</h2>
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
    if estimate_fragment:
        return f"""
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

    user_badge = f"<span class='user-label' style='font-size:13px;color:var(--text-sub);margin-left:6px'>👤 <strong>{html.escape(user_session or 'bahar')}</strong></span>"
    logout_btn = f"<a class='nav-btn secondary' href='{html.escape(logout_path)}'>خروج</a>"

    if page == "manual":
        navigation = f"{user_badge} <a class='nav-btn secondary' href='{html.escape('/' + manual_path.strip('/').rsplit('/', 1)[0])}'>بازگشت به داشبورد</a> {logout_btn}"
        page_content = manual_panel_html
        refresh_script = ""
    else:
        navigation = f"{user_badge} <a class='nav-btn secondary' href='{html.escape(analytics_path)}'>📊 آمار کاربران</a> <a class='nav-btn' href='{html.escape(manual_path)}'>ثبت دستی آفر</a> {logout_btn}"
        page_content = f"""
        <div id="ticker-content">
          <div class="top-ticker">
            {ticker_cards}
          </div>
        </div>
        {group_control_html}
        <div class="dashboard-grid">
          <div class="main-column">
            <div id="estimate-content">{table_section}</div>
          </div>
          <div class="side-column">
            <div id="activity-content">{render_group_activity_fragment(conversation_db) if conversation_db else ''}</div>
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
</style>
</head>
<body>
<main class="wrap">
  <header>
    <div class="header-brand">
      <div class="logo-badge">🪙</div>
      <div>
        <h1>سامانه <span>تخمین نرخ سکه</span></h1>
        <div class="status-pill">
          <span class="status-dot"></span>
          وضعیت سرویس: {service_status}
        </div>
      </div>
    </div>
    <div class="meta">
      <div class="meta-time">
        بروزرسانی: {generated}<br>
        بازه داده: {window_start} تا {window_end}
      </div>
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

        summary_cards_html = f"""
        <div class='group-summary-grid'>
          <div class='summary-card'>
            <div class='stat-icon'>📨</div>
            <div class='stat-info'>
              <span>تعداد کل آفرها</span>
              <strong>{off_c} <small>آفر</small></strong>
            </div>
          </div>
          <div class='summary-card'>
            <div class='stat-icon'>🤝</div>
            <div class='stat-info'>
              <span>تعداد کل معاملات</span>
              <strong>{trd_c} <small>معامله</small></strong>
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

        groups_html.append(
            f"""
            <section class='group-analytics-section'>
              <div class='section-head'>
                <h2>تحلیل و خلاصه آمار — گروه {fa_number(grp)}</h2>
                <span class='badge'>گروه {fa_number(grp)} معاملاتی</span>
              </div>
              {summary_cards_html}
              <div class='leaderboards-grid'>
                {t1}
                {t2}
                {t3}
                {t4}
              </div>
            </section>
            """
        )

    user_badge = f"<span class='user-label' style='font-size:13px;color:var(--text-sub);margin-left:6px'>👤 <strong>{html.escape(user_session or 'bahar')}</strong></span>"
    logout_btn = f"<a class='nav-btn secondary' href='{html.escape(logout_path)}'>خروج</a>"
    navigation = f"{user_badge} <a class='nav-btn secondary' href='{html.escape(home_path)}'>بازگشت به داشبورد اصلی</a> {logout_btn}"

    document = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>آمار و تحلیل کاربران گروه‌های معاملاتی</title>
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
</style>
</head>
<body>
<main class="wrap">
  <header>
    <div class="header-brand">
      <div class="logo-badge">📊</div>
      <div>
        <h1>آمار و تحلیل <span>کاربران گروه‌های معاملاتی</span></h1>
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
  if (e.key === "Escape") closeUserModal();
}});
</script>
</body>
</html>"""
    return document.encode("utf-8")


def handler_factory(
    route: str,
    state_store: StateStore,
    *,
    market_db: Path,
    conversation_db: Path,
    write_token: str | None,
    refresh_estimate,
    group_live_control: GroupLiveInputControl,
):
    normalized = "/" + route.strip("/")
    data_path = normalized + "/data.json"
    health_path = normalized + "/healthz"
    manual_path = normalized + "/manual-entry"
    analytics_path = normalized + "/analytics"
    login_path = normalized + "/login"
    logout_path = normalized + "/logout"
    estimate_path = normalized + "/estimates.html"
    activity_path = normalized + "/activity.html"
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
                body = json.dumps(
                    {
                        "status": state.get("service_status", "UNKNOWN"),
                        "generated_at_utc": state.get("generated_at_utc"),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
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
                )
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == analytics_path:
                query = parse_qs(urlsplit(self.path).query)
                range_type = query.get("range_type", ["today"])[0]
                start_shamsi = query.get("start_shamsi", [None])[0]
                end_shamsi = query.get("end_shamsi", [None])[0]
                body = render_analytics_page(
                    conversation_db,
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
                body = render_group_activity_fragment(conversation_db).encode("utf-8")
                self._headers(HTTPStatus.OK, "text/html; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == data_path:
                body = json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")
                self._headers(HTTPStatus.OK, "application/json; charset=utf-8", len(body))
                self.wfile.write(body)
                return
            if path == health_path:
                body = json.dumps(
                    {
                        "status": state.get("service_status", "UNKNOWN"),
                        "generated_at_utc": state.get("generated_at_utc"),
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
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
    end: datetime | None = None,
    group_live_control: GroupLiveInputControl | None = None,
) -> dict[str, Any]:
    effective_end = (end or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
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
    calibration_connection = sqlite3.connect(conversation_db)
    calibration_connection.row_factory = sqlite3.Row
    try:
        reconciliation = (
            reconcile_predictions(
                calibration_connection,
                now=effective_end,
                live_group_enabled=True,
                reconnect_at=reconnect_at,
            )
            if enabled
            else {
                "evaluated": 0,
                "residuals": [],
                "reconnect_bridged": 0,
                "status": "DEFERRED_GROUP_INPUT_DISCONNECTED",
            }
        )
        calibration_connection.commit()
    except Exception:
        calibration_connection.rollback()
        calibration_connection.close()
        raise
    calibration_connection.close()
    estimate = estimate_rates(
        model,
        market_db,
        effective_end,
        conversation_db,
        live_group_events_enabled=enabled,
        group_live_events_before=disabled_since,
    )
    calibration_connection = sqlite3.connect(conversation_db)
    calibration_connection.row_factory = sqlite3.Row
    try:
        online_metadata = apply_snapshot_calibration(
            calibration_connection,
            settlements=estimate.get("settlements", {}),
        )
        predictions_recorded = 0
        for settlement, payload in estimate.get("settlements", {}).items():
            predictions_recorded += record_predictions(
                calibration_connection,
                prediction_time=effective_end,
                settlement=str(settlement),
                rates=list(payload.get("rates", [])),
                group_live_enabled=enabled,
            )
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
    estimate["online_residual_learning"] = {
        "mode": "BOUNDED_ONLINE_RESIDUAL_CALIBRATION",
        "reconciliation": reconciliation,
        "calibration": online_metadata,
        "predictions_recorded": predictions_recorded,
        "automatic_model_weight_promotion": False,
    }
    state.set(estimate)
    write_json_atomic(state_path, estimate, mode=0o644)
    return estimate


async def estimation_loop(
    model: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    state_path: Path,
    state: StateStore,
    group_live_control: GroupLiveInputControl,
) -> None:
    last_run: datetime | None = None
    refresh_seconds = 5
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
                    end=end,
                    group_live_control=group_live_control,
                )
                print(
                    json.dumps(
                        {
                            "event": "estimate_complete",
                            "window_end_utc": estimate["window_end_utc"],
                            "settlements": list(estimate["settlements"]),
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


async def live_collection_loop(
    market_db: Path, backfill_minutes: int, channels: tuple[str, ...]
) -> None:
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
    try:
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
        await client.run_until_disconnected()
    finally:
        await client.disconnect()
        connection.close()


async def external_collection_loop(
    market_db: Path,
    *,
    wallex_interval: int,
    ime_interval: int,
    ime_timeout: float,
) -> None:
    if wallex_interval <= 0 or ime_interval < 0:
        raise ValueError(
            "Wallex interval must be positive and IME interval non-negative"
        )
    connection = connect(market_db)
    initialize(connection)
    last_ime_attempt: datetime | None = None
    try:
        while True:
            cycle_started = datetime.now(timezone.utc)
            try:
                wallex_rows = await asyncio.to_thread(fetch_wallex_live)
                upsert_external_observations(connection, wallex_rows)
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

            should_poll_ime = ime_interval > 0 and (
                last_ime_attempt is None
                or (cycle_started - last_ime_attempt).total_seconds() >= ime_interval
            )
            if should_poll_ime:
                last_ime_attempt = cycle_started
                try:
                    ime_rows = await fetch_ime_live(timeout=ime_timeout)
                    upsert_external_observations(connection, ime_rows)
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
    parser.add_argument("--market-db", type=Path, default=DEFAULT_MARKET_DB)
    parser.add_argument(
        "--conversation-db", type=Path, default=DEFAULT_CONVERSATION_DB
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
) -> None:
    tasks = [
        asyncio.create_task(
            estimation_loop(
                model,
                args.market_db,
                args.conversation_db,
                args.state,
                state,
                group_live_control,
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
                ),
                name="external-markets",
            )
        )
    await asyncio.gather(*tasks)


def main() -> int:
    args = build_parser().parse_args()
    ensure_manual_entry_schema(args.conversation_db)
    model = load_model(args.model)
    state = StateStore()
    group_live_control = GroupLiveInputControl(args.group_live_control)
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
            group_live_control=group_live_control,
        )

    server = start_web_server(
        args.host,
        args.port,
        route,
        state,
        market_db=args.market_db,
        conversation_db=args.conversation_db,
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
        asyncio.run(async_main(args, state, model, group_live_control))
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
