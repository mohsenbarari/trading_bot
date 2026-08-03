#!/usr/bin/env python3
"""Live Telegram collector, one-minute estimator, and read-only web page."""

from __future__ import annotations

import argparse
import asyncio
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
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from zoneinfo import ZoneInfo


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


TEHRAN = ZoneInfo("Asia/Tehran")
DEFAULT_STATE = RUNTIME_ROOT / "state.json"
DEFAULT_WRITE_TOKEN_FILE = RUNTIME_ROOT / "manual-entry.token"
MAX_MANUAL_FORM_BYTES = 16_384
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


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
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TEHRAN)
    return parsed.strftime("%Y-%m-%d %H:%M:%S").translate(
        str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    )


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
    """Read paper/physical one-minute means solely for the dashboard."""
    empty = {form: {"average_price": None, "sample_count": 0} for form in ("PAPER", "PHYSICAL")}
    if not end_value or not market_db.exists():
        return empty
    try:
        end = parse_datetime(end_value)
        start = iso_utc(end - timedelta(seconds=60))
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
    return f"""<section><div class="section-head"><h2>آخرین فعالیت گروه‌های معاملاتی</h2><span class="badge">دادهٔ ثبت‌شده در مدل</span></div>
      <div class="group-grid">
        <article class="feed-card"><h3>۵ آفر آخر — گروه ۱</h3><ul>{render_live_rows(activity.get('group_1_offers', []), kind='offer')}</ul></article>
        <article class="feed-card"><h3>۵ آفر آخر — گروه ۲</h3><ul>{render_live_rows(activity.get('group_2_offers', []), kind='offer')}</ul></article>
        <article class="feed-card"><h3>۳ معاملهٔ آخر — گروه ۱</h3><ul>{render_live_rows(activity.get('group_1_trades', []), kind='trade')}</ul></article>
        <article class="feed-card"><h3>۳ معاملهٔ آخر — گروه ۲</h3><ul>{render_live_rows(activity.get('group_2_trades', []), kind='trade')}</ul></article>
      </div>
    </section>"""


def render_input_cards(inputs: dict[str, dict[str, Any]]) -> str:
    labels = {
        "melted_gold": "آب‌شده",
        "generic_coin": "سکه عمومی کانال",
        "xauusd": "اونس جهانی",
        "usd": "دلار هرات",
        "usdt": "تتر/تومان",
    }
    cards = []
    for key, label in labels.items():
        value = inputs.get(key, {})
        estimated = key == "usd" and bool(value.get("is_estimated"))
        observed = value.get("status") in {"OBSERVED", "ESTIMATED"}
        if estimated:
            label = (
                "دلار هرات نقدی (برآورد زمانی)"
                if value.get("market_movement_driver")
                else "دلار هرات فردایی (برآورد روندی)"
            )
        rendered = fa_number(value.get("average_price"), decimals=2 if key == "xauusd" else 0)
        samples = fa_number(value.get("sample_count", 0))
        css = "estimated" if estimated else ("observed" if observed else "no-data")
        if estimated:
            trend_code = value.get("market_direction") or value.get(
                "usdt_trend"
            )
            direction = {
                "UP": "افزایشی",
                "DOWN": "کاهشی",
                "NEUTRAL": "خنثی",
            }.get(str(trend_code), "نامشخص")
            anchor = fa_number(value.get("anchor_price"))
            age_minutes = fa_number(
                float(value.get("anchor_age_seconds") or 0.0) / 60.0,
                decimals=1,
            )
            if value.get("market_movement_driver"):
                tomorrow = fa_number(value.get("market_driver_current_price"))
                basis = fa_number(
                    value.get("cash_tomorrow_basis_estimated_current")
                )
                closed_hours = fa_number(
                    value.get("cash_closed_hours"),
                    decimals=1,
                )
                detail = (
                    f"لنگر نقدی: {anchor} | فردایی فعلی: {tomorrow} | "
                    f"جهت: {direction} | فاصله: {basis} | "
                    f"زمان بسته: {closed_hours} ساعت"
                )
            else:
                detail = (
                    f"لنگر هرات: {anchor} | روند تتر: {direction} | "
                    f"سن لنگر: {age_minutes} دقیقه"
                )
        else:
            detail = f"تعداد نمونه: {samples}"
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
        flash_html = "<p class='flash success'>ثبت شد و تخمین فوراً بازمحاسبه شد.</p>"
    elif flash == "saved_refresh_failed":
        flash_html = "<p class='flash warning'>ثبت شد؛ بازمحاسبهٔ فوری خطا داشت و در چرخهٔ بعدی تکرار می‌شود.</p>"
    elif flash == "invalid":
        flash_html = "<p class='flash warning'>اطلاعات واردشده معتبر نبود. زمان‌های غیرلایو را با ساعت تهران وارد کنید.</p>"
    elif flash == "forbidden":
        flash_html = "<p class='flash warning'>توکن ثبت معتبر نیست.</p>"
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
    availability = (
        "این فرم موقت است و زمان‌ها همگی بر اساس ساعت تهران ثبت می‌شوند."
        if write_enabled
        else "ثبت دستی در این اجرا غیرفعال است."
    )
    counts = state.get("manual_entry_counts") or {}
    tehran_now = datetime.now(TEHRAN).strftime("%Y-%m-%dT%H:%M")
    return f"""
    <section id="manual-entry" class="manual-section">
      <div class="section-head"><div><h2>ثبت آفر و معاملهٔ گروه</h2>
      <p class="manual-help">همهٔ زمان‌ها ساعت تهران هستند. قیمت با «واحد پروژه» است؛ مثلاً ۱۸۵٬۰۰۰ یعنی ۱۸۵٬۰۰۰٬۰۰۰ تومان.</p></div>
      <span class="badge">ثبت دستی: {fa_number(counts.get('offers', 0))} آفر / {fa_number(counts.get('confirmed_trades', 0))} معامله</span></div>
      {flash_html}
      <div class="manual-grid">
        <form class="manual-form" method="post" action="{html.escape(manual_path)}">
          <h3 class="wide">۱. ثبت آفر جدید</h3>
          <label class="wide">متن خام آفر گروه (اختیاری؛ برای استخراج و آموزش)
            <textarea id="raw-offer-text" name="raw_offer_text" maxlength="2000" rows="3" placeholder="مثلاً: ۱۵:۳۱ — ۱۰ تا ربع ۵۱۲۰۰ ف"{disabled}></textarea>
          </label>
          <button class="wide secondary" id="parse-offer-text" type="button"{disabled}>استخراج اولیه از متن و پر کردن فرم</button>
          <p id="parse-offer-result" class="manual-help wide">اگر ساعت را به شکل ۱۵:۳۱ بنویسید، فرم از آخرین آفر ثبت‌شده تاریخ تهران را استنباط می‌کند. ساعت فقط برای زمان رخداد مصرف می‌شود و داخل متن آفر ذخیره نخواهد شد.</p>
          <label>کالا<select name="commodity" required{disabled}>{commodity_options}</select></label>
          <label>تسویه<select name="settlement" required{disabled}><option value="CASH">نقدی / امروز</option><option value="TOMORROW">فردایی</option></select></label>
          <label>نوع معامله<select name="trade_form" required{disabled}><option value="PHYSICAL">فیزیکی / واقعی</option><option value="PAPER">کاغذی / حواله</option></select></label>
          <label>سمت آفر<select name="side" required{disabled}><option value="" selected>تشخیص داده نشد؛ انتخاب کنید</option><option value="BUY">خرید</option><option value="SELL">فروش</option></select></label>
          <label>قیمت آفر (واحد پروژه)<input name="price" inputmode="numeric" required{disabled}></label>
          <label>تعداد آفر (اختیاری)<input name="quantity" inputmode="numeric"{disabled}></label>
          <label class="check"><input type="checkbox" name="offer_live" value="1" checked{disabled}> آفر همین حالا (به وقت تهران) لایو است</label>
          <label>اگر لایو نیست: زمان آفر (تهران)<input name="offer_time" type="datetime-local" value="{tehran_now}"{disabled}></label>
          <label class="wide">توضیحات (اختیاری)<textarea name="description" maxlength="2000" rows="2"{disabled}></textarea></label>
          <button class="wide" type="submit"{disabled}>ثبت و باز‌محاسبهٔ تخمین</button>
          <p class="manual-help wide">{availability} اگر آفر لایو نباشد، زمان آفر الزامی است.</p>
        </form>
        <form class="manual-form confirm-form" method="post" action="{html.escape(manual_path)}">
          <input type="hidden" name="entry_mode" value="confirm_existing">
          <h3 class="wide">۲. ثبت معامله برای آفرِ ثبت‌شده</h3>
          <label class="wide">آفر باز<select name="offer_id" required{confirm_disabled}><option value="">انتخاب آفر…</option>{open_offer_options}</select></label>
          <label class="check"><input type="checkbox" name="trade_live" value="1" checked{confirm_disabled}> معامله همین حالا (به وقت تهران) انجام شده است</label>
          <label>اگر لایو نیست: زمان معامله (تهران)<input name="trade_time" type="datetime-local" value="{tehran_now}"{confirm_disabled}></label>
          <label>قیمت معامله (اختیاری؛ پیش‌فرض قیمت آفر)<input name="trade_price" inputmode="numeric"{confirm_disabled}></label>
          <label>تعداد معامله (اختیاری؛ برای معاملهٔ جزئی)<input name="trade_quantity" inputmode="numeric"{confirm_disabled}></label>
          <button class="wide" type="submit"{confirm_disabled}>اتصال معامله و باز‌محاسبهٔ تخمین</button>
          <p class="manual-help wide">{('ابتدا یک آفر ثبت کنید؛ سپس در همین بخش معاملهٔ آن را ثبت کنید.' if not open_offer_options else 'پس از ثبت معامله، آفرِ مرتبط دیگر به‌عنوان آفر باز در لنگر لحظه‌ای شمرده نمی‌شود.')}</p>
        </form>
        <aside class="manual-effect"><h3>تأثیر فعلی بر تخمین</h3>
          <p class="manual-help">این مقادیر پس از هر ثبت موفق، با دادهٔ تازه دوباره محاسبه می‌شوند.</p>
          <div class="effect-grid">{render_manual_effect(state)}</div>
        </aside>
      </div>
    </section>
    """


def render_page(
    state: dict[str, Any], *, manual_path: str = "/manual-entry",
    estimate_path: str = "/estimates.html", activity_path: str = "/activity.html", write_enabled: bool = False,
    flash: str | None = None, open_manual_offers: list[dict[str, Any]] | None = None,
    estimate_fragment: bool = False, page: str = "home",
    market_db: Path | None = None, conversation_db: Path | None = None,
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
        f"<span>آب‌شدهٔ {'کاغذی' if form == 'PAPER' else 'فیزیکی'} — میانگین یک دقیقه</span>"
        f"<strong>{fa_number(value.get('average_price'))}</strong>"
        f"<small>{fa_number(value.get('sample_count', 0))} رویداد</small></div>"
        for form, value in melted.items()
    )
    estimate_view = f"""
      <div class='estimate-meta'>پنجره داده: {window_start} تا {window_end}<br>آخرین محاسبه: {generated}</div>
      <section><div class='section-head'><h2>ورودی‌های بازار</h2><span class='badge'>مقادیر فقط بر اساس دادهٔ واقعی همان بازه‌اند</span></div>
        <div class='inputs'>{render_input_cards(inputs)}{melted_cards}</div></section>
      <section><div class='section-head'><h2>تخمین انواع سکه</h2><span class='badge'>نقدی و فردایی در یک جدول</span></div>
        <div class='table-wrap'><table><thead><tr><th>کالا</th><th>نقدی <small>(واحد پروژه / بازه)</small></th><th>فردایی <small>(واحد پروژه / بازه)</small></th></tr></thead>
        <tbody>{render_combined_rate_rows(settlements)}</tbody></table></div></section>
    """
    estimate_view = (
        estimate_view
    )
    if estimate_fragment:
        return estimate_view.encode("utf-8")
    if page == "manual":
        navigation = f"<a class='nav-link' href='{html.escape('/' + manual_path.strip('/').rsplit('/', 1)[0])}'>بازگشت به تخمین‌ها</a>"
        page_content = render_manual_entry_panel(
            state,
            manual_path=manual_path,
            write_enabled=write_enabled,
            flash=flash,
            open_manual_offers=open_manual_offers or [],
        )
        refresh_script = ""
    else:
        navigation = f"<a class='nav-link primary' href='{html.escape(manual_path)}'>ثبت دستی آفر و معامله</a>"
        page_content = f"""
        <div id="estimate-content">{estimate_view}</div>
        <div id="activity-content">{render_group_activity_fragment(conversation_db) if conversation_db else ''}</div>"""
        refresh_script = "window.setInterval(refreshEstimateView, 15000); window.setInterval(refreshActivityView, 15000);"
    document = f"""<!doctype html>
<html lang="fa" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>تخمین‌گر نرخ سکه</title>
<style>
:root{{--bg:#07111f;--card:#101d2e;--line:#24364c;--text:#edf4ff;--muted:#9fb0c7;--cyan:#4fd1c5;--gold:#f6c453;--red:#ff8a8a}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at top,#17304b 0,#07111f 42%);color:var(--text);font-family:Tahoma,Arial,sans-serif;line-height:1.65}}
.wrap{{width:min(1280px,94%);margin:34px auto 60px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;margin-bottom:22px}}h1{{margin:0;font-size:clamp(24px,4vw,42px)}}h1 span{{color:var(--gold)}}.meta,.estimate-meta{{color:var(--muted);font-size:13px;text-align:left;direction:rtl}}.estimate-meta{{margin:18px 2px}}section{{background:rgba(16,29,46,.94);border:1px solid var(--line);border-radius:18px;padding:20px;margin:18px 0;box-shadow:0 18px 50px rgba(0,0,0,.22)}}.section-head{{display:flex;align-items:center;justify-content:space-between;gap:12px}}h2{{margin:0 0 14px;font-size:20px}}h3{{margin:0 0 8px}}.badge{{border:1px solid #2d8079;color:var(--cyan);padding:3px 10px;border-radius:99px;font-size:12px}}.inputs{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:8px 0 18px}}.input-card{{display:flex;flex-direction:column;padding:12px;border-radius:12px;border:1px solid var(--line);background:#0b1727}}.input-card span,.input-card small{{color:var(--muted);font-size:12px}}.input-card strong{{font-size:17px;margin:3px 0;direction:ltr;text-align:right}}.input-card.observed{{border-color:#27655f}}.input-card.estimated{{border-color:#aa7a2a}}.input-card.no-data strong,.missing{{color:var(--red)}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{padding:11px 12px;border-bottom:1px solid var(--line);text-align:right}}th{{color:var(--muted);font-size:12px}}td{{font-size:14px}}.rate-cell{{min-width:220px}}.rate-cell strong{{display:block;color:var(--gold);font-size:17px;direction:ltr;text-align:right}}.rate-cell small{{display:block;color:var(--muted);font-size:11px;direction:ltr;text-align:right}}footer{{color:var(--muted);font-size:12px;padding:8px 4px}}code{{direction:ltr;display:inline-block}}.status{{color:var(--cyan)}}.nav-link{{display:inline-block;border:1px solid #2d8079;color:var(--cyan);padding:8px 12px;border-radius:9px;text-decoration:none;font-size:13px}}.nav-link.primary{{background:var(--cyan);color:#06211f;font-weight:700}}.group-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.feed-card{{background:#0b1727;border:1px solid var(--line);border-radius:12px;padding:13px}}.feed-card h3{{font-size:15px;color:var(--gold)}}.feed-card ul{{list-style:none;margin:0;padding:0}}.feed-card li{{padding:7px 0;border-top:1px solid #1c2c40;font-size:13px}}.feed-card time,.feed-card small{{color:var(--muted);font-size:11px}}
.manual-grid{{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:18px}}.manual-form{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:14px;border:1px solid var(--line);border-radius:14px;background:#0b1727}}.manual-form label{{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px}}.manual-form input,.manual-form select,.manual-form textarea{{width:100%;padding:9px 10px;border-radius:8px;border:1px solid #334964;background:#081321;color:var(--text);font:inherit}}.manual-form textarea{{resize:vertical}}.manual-form .check{{flex-direction:row;align-items:center;padding-top:22px;color:var(--text)}}.manual-form .check input{{width:auto}}.manual-form .wide{{grid-column:1 / -1}}.manual-form hr{{width:100%;border:0;border-top:1px solid var(--line);margin:2px 0}}button{{border:0;border-radius:9px;padding:11px 15px;background:var(--cyan);color:#06211f;font-weight:700;font:inherit;cursor:pointer}}button:disabled,input:disabled,select:disabled,textarea:disabled{{opacity:.55;cursor:not-allowed}}.manual-help{{margin:0;color:var(--muted);font-size:12px}}.manual-effect{{border:1px solid #2d8079;border-radius:14px;padding:14px;background:#0b1727}}.manual-effect h3{{margin:0 0 5px;color:var(--cyan)}}.effect-grid{{display:grid;gap:10px;margin-top:12px}}.effect-card{{border-top:1px solid var(--line);padding-top:8px}}.effect-card h3{{font-size:15px;color:var(--gold)}}.effect-card ul{{padding:0;margin:0;list-style:none}}.effect-card li{{padding:4px 0;font-size:13px}}.effect-card small{{color:var(--muted)}}.flash{{padding:9px 12px;border-radius:9px}}.flash.success{{background:#113a35;color:#aaf0e8}}.flash.warning{{background:#4a2b26;color:#ffd0c8}}
.manual-grid{{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:18px}}.manual-form{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;padding:14px;border:1px solid var(--line);border-radius:14px;background:#0b1727}}.manual-form label{{display:flex;flex-direction:column;gap:5px;color:var(--muted);font-size:12px}}.manual-form input,.manual-form select,.manual-form textarea{{width:100%;padding:9px 10px;border-radius:8px;border:1px solid #334964;background:#081321;color:var(--text);font:inherit}}.manual-form textarea{{resize:vertical}}.manual-form .check{{flex-direction:row;align-items:center;padding-top:22px;color:var(--text)}}.manual-form .check input{{width:auto}}.manual-form .wide{{grid-column:1 / -1}}.manual-form hr{{width:100%;border:0;border-top:1px solid var(--line);margin:2px 0}}button{{border:0;border-radius:9px;padding:11px 15px;background:var(--cyan);color:#06211f;font-weight:700;font:inherit;cursor:pointer}}button.secondary{{background:#17324b;color:var(--cyan);border:1px solid #2d8079}}button:disabled,input:disabled,select:disabled,textarea:disabled{{opacity:.55;cursor:not-allowed}}.manual-help{{margin:0;color:var(--muted);font-size:12px}}.manual-effect{{border:1px solid #2d8079;border-radius:14px;padding:14px;background:#0b1727}}.manual-effect h3{{margin:0 0 5px;color:var(--cyan)}}.effect-grid{{display:grid;gap:10px;margin-top:12px}}.effect-card{{border-top:1px solid var(--line);padding-top:8px}}.effect-card h3{{font-size:15px;color:var(--gold)}}.effect-card ul{{padding:0;margin:0;list-style:none}}.effect-card li{{padding:4px 0;font-size:13px}}.effect-card small{{color:var(--muted)}}.flash{{padding:9px 12px;border-radius:9px}}.flash.success{{background:#113a35;color:#aaf0e8}}.flash.warning{{background:#4a2b26;color:#ffd0c8}}
@media(max-width:850px){{header{{display:block}}.meta{{text-align:right;margin-top:12px}}.nav-link{{margin-top:12px}}.inputs,.group-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(max-width:850px){{.manual-grid{{grid-template-columns:1fr}}}}@media(max-width:520px){{.inputs,.manual-form{{grid-template-columns:1fr}}section{{padding:14px}}.manual-form .wide{{grid-column:auto}}}}
</style></head><body><main class="wrap">
<header><div><h1>مدل <span>تخمین نرخ سکه</span></h1><div class="status">وضعیت سرویس: {service_status}</div></div>
<div class="meta">پنجره داده: {window_start} تا {window_end}<br>آخرین محاسبه: {generated}<br>{navigation}</div></header>
{page_content}
<footer>این خروجی آزمایشی است و توصیه خرید یا فروش نیست. مقدار <code>{NO_DATA_TOKEN}</code> یعنی در همان دقیقه داده واقعی وجود نداشته و مقدار قبلی حمل نشده است. قیمت «واحد پروژه» با ضریب ×۱۰۰۰ به تومان کامل تبدیل می‌شود. اطمینان هیچ کالا در این نسخه «بالا» نیست.</footer>
</main><script>
async function refreshEstimateView() {{
  try {{
    const response = await fetch({json.dumps(estimate_path)}, {{cache: "no-store"}});
    if (response.ok) document.getElementById("estimate-content").innerHTML = await response.text();
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
        ? `زمان پیشنهادی تهران: ${{suggested.offer_time}}؛ ساعت در متن ذخیره نمی‌شود`
        : "ساعتی در متن پیدا نشد";
      result.textContent = `${{timeText}} — parser: ${{suggested.parser}} / ${{suggested.training_examples || 0}} نمونه`
        + (warningText ? ` — توجه: ${{warningText}}` : "");
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
</script></body></html>"""
    return document.encode("utf-8")


def handler_factory(
    route: str,
    state_store: StateStore,
    *,
    market_db: Path,
    conversation_db: Path,
    write_token: str | None,
    refresh_estimate,
):
    normalized = "/" + route.strip("/")
    data_path = normalized + "/data.json"
    health_path = normalized + "/healthz"
    manual_path = normalized + "/manual-entry"
    estimate_path = normalized + "/estimates.html"
    activity_path = normalized + "/activity.html"
    parse_offer_path = manual_path + "/parse-text"

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

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path.rstrip("/") or "/"
            state = state_store.get()
            if path == normalized:
                body = render_page(
                    state,
                    manual_path=manual_path,
                    estimate_path=estimate_path,
                    activity_path=activity_path,
                    write_enabled=write_token is not None,
                    market_db=market_db,
                    conversation_db=conversation_db,
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
                    estimate_path=estimate_path,
                    activity_path=activity_path,
                    write_enabled=write_token is not None,
                    flash=flash,
                    open_manual_offers=list_open_manual_offers(conversation_db),
                    page="manual",
                    market_db=market_db,
                    conversation_db=conversation_db,
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
) -> dict[str, Any]:
    effective_end = (end or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    estimate = estimate_rates(model, market_db, effective_end, conversation_db)
    estimate["service_status"] = "RUNNING"
    estimate["manual_entry_counts"] = manual_entry_counts(conversation_db)
    state.set(estimate)
    write_json_atomic(state_path, estimate, mode=0o644)
    return estimate


async def estimation_loop(
    model: dict[str, Any],
    market_db: Path,
    conversation_db: Path,
    state_path: Path,
    state: StateStore,
) -> None:
    last_end: datetime | None = None
    while True:
        now = datetime.now(timezone.utc)
        end = now.replace(second=0, microsecond=0)
        if end != last_end:
            try:
                estimate = refresh_estimate(
                    model, market_db, conversation_db, state_path, state, end=end
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
                last_end = end
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
        await asyncio.sleep(2)


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
        "--manual-entry-token-file", type=Path, default=DEFAULT_WRITE_TOKEN_FILE
    )
    parser.add_argument("--disable-manual-entry", action="store_true")
    parser.add_argument("--backfill-minutes", type=int, default=5)
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--no-external", action="store_true")
    parser.add_argument("--wallex-interval", type=int, default=15)
    parser.add_argument("--ime-interval", type=int, default=60)
    parser.add_argument("--ime-timeout", type=float, default=8.0)
    parser.add_argument("--channel", action="append")
    return parser


async def async_main(
    args: argparse.Namespace, state: StateStore, model: dict[str, Any]
) -> None:
    tasks = [
        asyncio.create_task(
            estimation_loop(
                model,
                args.market_db,
                args.conversation_db,
                args.state,
                state,
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
    route = "/" + args.path.strip("/")
    write_token = (
        None
        if args.disable_manual_entry
        else read_or_create_write_token(args.manual_entry_token_file)
    )

    def refresh_from_web() -> None:
        refresh_estimate(
            model, args.market_db, args.conversation_db, args.state, state
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
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        asyncio.run(async_main(args, state, model))
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
