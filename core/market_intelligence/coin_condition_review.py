"""Authenticated owner-review workflow for coin-offer condition research.

Private offer text is resolved from an external sealed pack or the canonical
read-only source databases only while an authenticated page is rendered.  The
review database stores opaque sample digests, structured labels and character
spans, never source text, Telegram identifiers or sender identity.  Predictions
are research-only shadow output and cannot affect offer or estimator runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import blake2b, sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterable, Mapping, Sequence

from .coin_groups import CoinGroupMessageInput, parse_coin_group_offers
from .coin_offer_conditions import (
    CONDITION_FAMILIES,
    CONDITION_TAXONOMY_VERSION,
    extract_offer_conditions,
    masked_condition_model_text,
    normalize_offer_text,
    semantic_condition_alias_spans,
)


REVIEW_SCHEMA_VERSION = "coin-offer-condition-review-store-v2"
OWNER_PACK_VERSION = "coin-offer-condition-owner-review-v1"
REVIEW_STATUSES = frozenset({"CONDITIONAL", "UNCONDITIONAL", "AMBIGUOUS"})
REVIEW_SETTLEMENTS = frozenset({"CASH", "TOMORROW", "UNKNOWN"})
QUEUE_KINDS = frozenset({"SEALED", "LIVE"})
_DEADLINE_RE = re.compile(r"^(?:|AMBIGUOUS|(?:[01]\d|2[0-3]):[0-5]\d)$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{40}$")
_REVIEW_FORMAT_CONTROL_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]"
)
_REVIEW_FIELD_LABEL_RE = re.compile(
    r"(?m)^\s*شرط(?:\s+(?:خرید|فروش))?\s*[:：]\s*"
)
_REVIEW_FRAGMENT_SEPARATOR_RE = re.compile(r"[|؛;،,\r\n\u2028\u2029]+")


class ConditionReviewError(ValueError):
    """Redacted validation/store error suitable for an authenticated UI."""


@dataclass(frozen=True, slots=True)
class ConditionReviewSample:
    sample_digest: str
    queue_kind: str
    source_fingerprint: str
    group_code: str
    event_time_utc: str
    settlement_term: str
    trade_form: str
    session_phase: str
    private_offer_text: str


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _utc_string(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def condition_sample_digest(
    *,
    group_code: str,
    event_time_utc: str,
    settlement_term: str,
    trade_form: str,
    model_text: str,
) -> str:
    """Match the sealed trainer's privacy-safe row identity contract."""

    digest = blake2b(digest_size=20, person=b"coin-cond-row-v1")
    for value in (
        group_code,
        event_time_utc,
        settlement_term,
        trade_form,
        model_text,
    ):
        encoded = str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _reviewer_digest(value: str) -> bytes:
    normalized = str(value or "").strip()
    if not normalized:
        raise ConditionReviewError("condition_review_reviewer_required")
    return blake2b(
        normalized.encode("utf-8"),
        digest_size=32,
        person=b"coin-cond-owner1",
    ).digest()


def _read_only_connection(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved.name)
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS coin_offer_condition_review_state(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_version TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS coin_offer_condition_reviews(
    sample_digest BLOB PRIMARY KEY CHECK(length(sample_digest)=20),
    queue_kind TEXT NOT NULL CHECK(queue_kind IN ('SEALED','LIVE')),
    source_fingerprint TEXT NOT NULL,
    source_group_code TEXT NOT NULL,
    source_event_time_utc TEXT NOT NULL,
    source_settlement_term TEXT NOT NULL,
    source_trade_form TEXT NOT NULL,
    owner_status TEXT NOT NULL CHECK(
        owner_status IN ('CONDITIONAL','UNCONDITIONAL','AMBIGUOUS')
    ),
    owner_families_json TEXT NOT NULL,
    owner_settlement TEXT NOT NULL CHECK(
        owner_settlement IN ('CASH','TOMORROW','UNKNOWN')
    ),
    owner_condition_spans_json TEXT NOT NULL,
    owner_deadline TEXT NOT NULL,
    reviewer_digest BLOB NOT NULL CHECK(length(reviewer_digest)=32),
    review_revision INTEGER NOT NULL CHECK(review_revision>0),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coin_condition_review_queue
ON coin_offer_condition_reviews(queue_kind,updated_at_utc);
CREATE TABLE IF NOT EXISTS coin_offer_condition_review_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_digest BLOB NOT NULL CHECK(length(sample_digest)=20),
    queue_kind TEXT NOT NULL CHECK(queue_kind IN ('SEALED','LIVE')),
    source_group_code TEXT NOT NULL,
    source_event_time_utc TEXT NOT NULL,
    source_settlement_term TEXT NOT NULL,
    source_trade_form TEXT NOT NULL,
    owner_status TEXT NOT NULL,
    owner_families_json TEXT NOT NULL,
    owner_settlement TEXT NOT NULL,
    owner_condition_spans_json TEXT NOT NULL,
    owner_deadline TEXT NOT NULL,
    reviewer_digest BLOB NOT NULL CHECK(length(reviewer_digest)=32),
    review_revision INTEGER NOT NULL CHECK(review_revision>0),
    reviewed_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coin_condition_review_history_sample
ON coin_offer_condition_review_history(sample_digest,review_revision);
"""


class ConditionReviewStore:
    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def ensure_schema(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.executescript(_SCHEMA)
                row = connection.execute(
                    "SELECT schema_version FROM coin_offer_condition_review_state "
                    "WHERE singleton=1"
                ).fetchone()
                now = _utc_now()
                if row is None:
                    connection.execute(
                        "INSERT INTO coin_offer_condition_review_state VALUES(1,?,?)",
                        (REVIEW_SCHEMA_VERSION, now),
                    )
                elif str(row["schema_version"]) == "coin-offer-condition-review-store-v1":
                    for table in (
                        "coin_offer_condition_reviews",
                        "coin_offer_condition_review_history",
                    ):
                        columns = {
                            str(item[1])
                            for item in connection.execute(f"PRAGMA table_info({table})")
                        }
                        additions = (
                            ("source_group_code", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
                            ("source_event_time_utc", "TEXT NOT NULL DEFAULT ''"),
                            ("source_settlement_term", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
                            ("source_trade_form", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
                        )
                        for name, declaration in additions:
                            if name not in columns:
                                connection.execute(
                                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                                )
                    connection.execute(
                        "UPDATE coin_offer_condition_review_state SET schema_version=?,updated_at_utc=? WHERE singleton=1",
                        (REVIEW_SCHEMA_VERSION, now),
                    )
                elif str(row["schema_version"]) != REVIEW_SCHEMA_VERSION:
                    raise ConditionReviewError(
                        "condition_review_schema_upgrade_required"
                    )
                connection.commit()
            finally:
                connection.close()
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            connection = _read_only_connection(self.path)
            try:
                rows = connection.execute(
                    "SELECT * FROM coin_offer_condition_reviews"
                ).fetchall()
            finally:
                connection.close()
        output: dict[str, dict[str, Any]] = {}
        for row in rows:
            digest = bytes(row["sample_digest"]).hex()
            output[digest] = {
                "owner_status": str(row["owner_status"]),
                "owner_families": json.loads(row["owner_families_json"]),
                "owner_settlement": str(row["owner_settlement"]),
                "owner_condition_spans": json.loads(
                    row["owner_condition_spans_json"]
                ),
                "owner_deadline": str(row["owner_deadline"]),
                "review_revision": int(row["review_revision"]),
                "updated_at_utc": str(row["updated_at_utc"]),
                "queue_kind": str(row["queue_kind"]),
                "source_fingerprint": str(row["source_fingerprint"]),
                "source_group_code": str(row["source_group_code"]),
                "source_event_time_utc": str(row["source_event_time_utc"]),
                "source_settlement_term": str(row["source_settlement_term"]),
                "source_trade_form": str(row["source_trade_form"]),
            }
        return output

    def record(
        self,
        sample: ConditionReviewSample,
        payload: Mapping[str, Any],
        *,
        reviewer: str,
    ) -> dict[str, Any]:
        normalized = _validate_review_payload(sample, payload)
        expected_revision = payload.get("expected_revision", 0)
        if isinstance(expected_revision, bool):
            raise ConditionReviewError("condition_review_revision_invalid")
        try:
            expected_revision = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise ConditionReviewError("condition_review_revision_invalid") from exc
        if expected_revision < 0:
            raise ConditionReviewError("condition_review_revision_invalid")
        reviewer_key = _reviewer_digest(reviewer)
        digest = bytes.fromhex(sample.sample_digest)
        now = _utc_now()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    "SELECT review_revision,created_at_utc FROM "
                    "coin_offer_condition_reviews WHERE sample_digest=?",
                    (digest,),
                ).fetchone()
                actual_revision = int(current["review_revision"]) if current else 0
                if actual_revision != expected_revision:
                    raise ConditionReviewError("condition_review_revision_conflict")
                revision = actual_revision + 1
                created_at = str(current["created_at_utc"]) if current else now
                values = (
                    digest,
                    sample.queue_kind,
                    sample.source_fingerprint,
                    sample.group_code,
                    sample.event_time_utc,
                    sample.settlement_term,
                    sample.trade_form,
                    normalized["owner_status"],
                    json.dumps(normalized["owner_families"], separators=(",", ":")),
                    normalized["owner_settlement"],
                    json.dumps(
                        normalized["owner_condition_spans"], separators=(",", ":")
                    ),
                    normalized["owner_deadline"],
                    reviewer_key,
                    revision,
                    created_at,
                    now,
                )
                connection.execute(
                    """
                    INSERT INTO coin_offer_condition_reviews(
                        sample_digest,queue_kind,source_fingerprint,
                        source_group_code,source_event_time_utc,
                        source_settlement_term,source_trade_form,owner_status,
                        owner_families_json,owner_settlement,
                        owner_condition_spans_json,owner_deadline,reviewer_digest,
                        review_revision,created_at_utc,updated_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(sample_digest) DO UPDATE SET
                        queue_kind=excluded.queue_kind,
                        source_fingerprint=excluded.source_fingerprint,
                        source_group_code=excluded.source_group_code,
                        source_event_time_utc=excluded.source_event_time_utc,
                        source_settlement_term=excluded.source_settlement_term,
                        source_trade_form=excluded.source_trade_form,
                        owner_status=excluded.owner_status,
                        owner_families_json=excluded.owner_families_json,
                        owner_settlement=excluded.owner_settlement,
                        owner_condition_spans_json=excluded.owner_condition_spans_json,
                        owner_deadline=excluded.owner_deadline,
                        reviewer_digest=excluded.reviewer_digest,
                        review_revision=excluded.review_revision,
                        updated_at_utc=excluded.updated_at_utc
                    """,
                    values,
                )
                connection.execute(
                    """
                    INSERT INTO coin_offer_condition_review_history(
                        sample_digest,queue_kind,source_group_code,
                        source_event_time_utc,source_settlement_term,
                        source_trade_form,owner_status,owner_families_json,
                        owner_settlement,owner_condition_spans_json,owner_deadline,
                        reviewer_digest,review_revision,reviewed_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        digest,
                        sample.queue_kind,
                        sample.group_code,
                        sample.event_time_utc,
                        sample.settlement_term,
                        sample.trade_form,
                        normalized["owner_status"],
                        json.dumps(normalized["owner_families"], separators=(",", ":")),
                        normalized["owner_settlement"],
                        json.dumps(
                            normalized["owner_condition_spans"],
                            separators=(",", ":"),
                        ),
                        normalized["owner_deadline"],
                        reviewer_key,
                        revision,
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE coin_offer_condition_review_state SET updated_at_utc=? "
                    "WHERE singleton=1",
                    (now,),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return {**normalized, "review_revision": revision, "updated_at_utc": now}


def _condition_spans(
    text: str,
    fragments_text: str,
    *,
    allowed_families: Iterable[str] = (),
) -> list[list[int]]:
    normalized = normalize_offer_text(text)[:512]
    review_text = _REVIEW_FORMAT_CONTROL_RE.sub("", str(fragments_text or ""))
    review_text = _REVIEW_FIELD_LABEL_RE.sub("", review_text)
    fragments = [
        normalized_item
        for item in _REVIEW_FRAGMENT_SEPARATOR_RE.split(review_text)
        if (normalized_item := normalize_offer_text(item))
    ]
    if not fragments:
        return []
    spans: list[list[int]] = []
    used: set[tuple[int, int]] = set()
    cursor = 0
    for fragment in fragments:
        index = normalized.find(fragment, cursor)
        if index < 0:
            index = normalized.find(fragment)
        exact_span = (index, index + len(fragment)) if index >= 0 else None
        if exact_span is not None and exact_span not in used:
            selected = exact_span
        else:
            candidates = semantic_condition_alias_spans(
                normalized,
                fragment,
                allowed_families=allowed_families,
            )
            if not candidates and semantic_condition_alias_spans(normalized, fragment):
                raise ConditionReviewError(
                    "condition_review_alias_family_mismatch"
                )
            available = [span for span in candidates if span not in used]
            forward = [span for span in available if span[0] >= cursor]
            selected = forward[0] if forward else available[0] if available else None
        if selected is None:
            raise ConditionReviewError("condition_review_span_not_in_offer")
        spans.append([selected[0], selected[1]])
        used.add(selected)
        cursor = selected[1]
    return spans


def _validate_review_payload(
    sample: ConditionReviewSample, payload: Mapping[str, Any]
) -> dict[str, Any]:
    if str(payload.get("sample_digest") or "") != sample.sample_digest:
        raise ConditionReviewError("condition_review_sample_mismatch")
    status = str(payload.get("owner_status") or "").strip().upper()
    if status not in REVIEW_STATUSES:
        raise ConditionReviewError("condition_review_status_invalid")
    families_raw = payload.get("owner_families")
    if not isinstance(families_raw, list):
        raise ConditionReviewError("condition_review_families_invalid")
    families = sorted({str(item).strip().upper() for item in families_raw})
    if not set(families).issubset(CONDITION_FAMILIES):
        raise ConditionReviewError("condition_review_families_invalid")
    if status == "CONDITIONAL" and not families:
        raise ConditionReviewError("condition_review_family_required")
    if status != "CONDITIONAL" and families:
        raise ConditionReviewError("condition_review_family_not_allowed")
    settlement = str(payload.get("owner_settlement") or "").strip().upper()
    if settlement not in REVIEW_SETTLEMENTS:
        raise ConditionReviewError("condition_review_settlement_invalid")
    deadline = str(payload.get("owner_deadline") or "").strip().upper()
    if len(deadline) > 16 or not _DEADLINE_RE.fullmatch(deadline):
        raise ConditionReviewError("condition_review_deadline_invalid")
    fragments = str(payload.get("owner_condition_text") or "").strip()
    if len(fragments) > 512:
        raise ConditionReviewError("condition_review_condition_text_too_long")
    if status != "CONDITIONAL" and fragments:
        raise ConditionReviewError("condition_review_span_not_allowed")
    spans = _condition_spans(
        sample.private_offer_text,
        fragments,
        allowed_families=families,
    )
    if status == "CONDITIONAL" and not spans:
        raise ConditionReviewError("condition_review_span_required")
    return {
        "owner_status": status,
        "owner_families": families,
        "owner_settlement": settlement,
        "owner_condition_spans": spans,
        "owner_deadline": deadline,
    }


def _span_text(text: str, spans: Iterable[Sequence[int]]) -> str:
    normalized = normalize_offer_text(text)[:512]
    pieces: list[str] = []
    for span in spans:
        if len(span) != 2:
            continue
        try:
            start, end = int(span[0]), int(span[1])
        except (TypeError, ValueError):
            continue
        if 0 <= start < end <= len(normalized):
            pieces.append(normalized[start:end])
    return " | ".join(pieces)


def load_owner_pack(path: Path | None) -> tuple[list[ConditionReviewSample], dict[str, Any]]:
    if path is None or not path.expanduser().is_file():
        return [], {
            "status": "SEALED_PACK_UNAVAILABLE",
            "sample_count": 0,
            "source_fingerprint": None,
        }
    resolved = path.expanduser().resolve()
    stat = resolved.stat()
    if stat.st_uid != os.geteuid() or stat.st_mode & 0o022:
        raise ConditionReviewError("condition_review_owner_pack_permissions_invalid")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if payload.get("schema_version") != OWNER_PACK_VERSION:
        raise ConditionReviewError("condition_review_owner_pack_version_invalid")
    source_fingerprint = str(payload.get("source_fingerprint") or "")
    raw_samples = payload.get("samples")
    if not re.fullmatch(r"[0-9a-f]{64}", source_fingerprint) or not isinstance(
        raw_samples, list
    ):
        raise ConditionReviewError("condition_review_owner_pack_invalid")
    samples: list[ConditionReviewSample] = []
    seen: set[str] = set()
    for item in raw_samples:
        if not isinstance(item, dict):
            raise ConditionReviewError("condition_review_owner_pack_invalid")
        digest = str(item.get("sample_digest") or "")
        private_text = normalize_offer_text(str(item.get("private_offer_text") or ""))[:512]
        if not _DIGEST_RE.fullmatch(digest) or digest in seen or not private_text:
            raise ConditionReviewError("condition_review_owner_pack_invalid")
        seen.add(digest)
        samples.append(
            ConditionReviewSample(
                sample_digest=digest,
                queue_kind="SEALED",
                source_fingerprint=source_fingerprint,
                group_code=str(item.get("group_code") or "UNKNOWN"),
                event_time_utc=str(item.get("event_time_utc") or ""),
                settlement_term=str(item.get("settlement_term") or "UNKNOWN").upper(),
                trade_form=str(item.get("trade_form") or "UNKNOWN").upper(),
                session_phase=str(item.get("session_phase") or "UNKNOWN").upper(),
                private_offer_text=private_text,
            )
        )
    declared = payload.get("selection", {}).get("sample_count")
    if declared is not None and int(declared) != len(samples):
        raise ConditionReviewError("condition_review_owner_pack_count_mismatch")
    return samples, {
        "status": "READY",
        "sample_count": len(samples),
        "source_fingerprint": source_fingerprint,
    }


def _historical_offer_samples(
    database: Path, *, earliest_event_time_utc: str, row_limit: int
) -> list[ConditionReviewSample]:
    if not database.is_file():
        return []
    connection = _read_only_connection(database)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"offers", "messages"}.issubset(tables):
            return []
        rows = connection.execute(
            """
            SELECT m.source_html_file AS group_code,m.event_time_utc,
                   o.settlement,o.trade_form,o.source_text
            FROM offers o
            JOIN messages m
              ON m.import_id=o.import_id AND m.message_id=o.message_id
            WHERE m.source_html_file IN ('group_1','group_2')
              AND trim(COALESCE(o.source_text,'')) <> ''
              AND m.event_time_utc>=?
            ORDER BY m.event_time_utc DESC,o.id DESC
            LIMIT ?
            """,
            (earliest_event_time_utc, row_limit),
        ).fetchall()
    finally:
        connection.close()
    samples: list[ConditionReviewSample] = []
    for row in rows:
        text = normalize_offer_text(str(row["source_text"] or ""))[:512]
        settlement = str(row["settlement"] or "UNKNOWN").upper()
        trade_form = str(row["trade_form"] or "UNKNOWN").upper()
        event_time = str(row["event_time_utc"] or "")
        group = str(row["group_code"] or "UNKNOWN")
        try:
            axes = extract_offer_conditions(
                text,
                event_time_utc=event_time,
                settlement_term=settlement,
                trade_form=trade_form,
            )
        except (TypeError, ValueError):
            continue
        samples.append(
            ConditionReviewSample(
                sample_digest=condition_sample_digest(
                    group_code=group,
                    event_time_utc=event_time,
                    settlement_term=settlement,
                    trade_form=trade_form,
                    model_text=masked_condition_model_text(text),
                ),
                queue_kind="LIVE",
                source_fingerprint="LIVE_CANONICAL",
                group_code=group,
                event_time_utc=event_time,
                settlement_term=settlement,
                trade_form=trade_form,
                session_phase=axes.market_session_phase,
                private_offer_text=text,
            )
        )
    return samples


def _staged_offer_samples(
    database: Path | None, *, message_limit: int
) -> list[ConditionReviewSample]:
    if database is None or not database.is_file():
        return []
    connection = _read_only_connection(database)
    try:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='coin_group_staged_messages'"
        ).fetchone() is None:
            return []
        rows = connection.execute(
            """
            SELECT group_number,message_id,event_time_utc,available_at_utc,message_text
            FROM coin_group_staged_messages
            WHERE available_at_utc<=?
            ORDER BY event_time_utc DESC,group_number,message_id DESC
            LIMIT ?
            """,
            (_utc_now(), message_limit),
        ).fetchall()
    finally:
        connection.close()
    samples: list[ConditionReviewSample] = []
    for row in rows:
        raw = str(row["message_text"] or "")
        for line_index, line in enumerate(raw.splitlines() or [raw]):
            text = normalize_offer_text(line)[:512]
            if not text:
                continue
            source = CoinGroupMessageInput(
                group_number=int(row["group_number"]),
                source_event_id=f"{int(row['message_id'])}:{line_index}",
                published_at_utc=str(row["event_time_utc"]),
                available_at_utc=str(row["available_at_utc"]),
                text=line,
            )
            try:
                parsed = parse_coin_group_offers(source)
            except (TypeError, ValueError):
                continue
            for offer in parsed:
                settlement = str(offer.settlement_term).upper()
                trade_form = str(offer.trade_form).upper()
                event_time = str(row["event_time_utc"])
                group = f"group_{int(row['group_number'])}"
                axes = extract_offer_conditions(
                    text,
                    event_time_utc=event_time,
                    settlement_term=settlement,
                    trade_form=trade_form,
                )
                samples.append(
                    ConditionReviewSample(
                        sample_digest=condition_sample_digest(
                            group_code=group,
                            event_time_utc=event_time,
                            settlement_term=settlement,
                            trade_form=trade_form,
                            model_text=masked_condition_model_text(text),
                        ),
                        queue_kind="LIVE",
                        source_fingerprint="LIVE_PROTECTED_STAGING",
                        group_code=group,
                        event_time_utc=event_time,
                        settlement_term=settlement,
                        trade_form=trade_form,
                        session_phase=axes.market_session_phase,
                        private_offer_text=text,
                    )
                )
    return samples


def load_live_offer_samples(
    conversation_db: Path,
    staging_db: Path | None,
    *,
    recent_days: int = 3,
    sample_limit: int = 1_000,
) -> list[ConditionReviewSample]:
    """Return newest unique live/recent offers without operational identifiers."""

    def signature(path: Path | None) -> tuple[str, int, int]:
        if path is None or not path.is_file():
            return ("", 0, 0)
        stat = path.stat()
        return (str(path.resolve()), stat.st_mtime_ns, stat.st_size)

    bounded_limit = max(100, min(5_000, int(sample_limit)))
    cache_key = (
        signature(conversation_db),
        signature(staging_db),
        int(recent_days),
        bounded_limit,
    )
    with _LIVE_SAMPLE_CACHE_LOCK:
        cached = _LIVE_SAMPLE_CACHE.get(cache_key)
        if cached is not None:
            return list(cached)
    earliest = _utc_string(
        datetime.now(timezone.utc) - timedelta(days=max(1, min(30, recent_days)))
    )
    combined = _staged_offer_samples(
        staging_db, message_limit=bounded_limit * 2
    ) + _historical_offer_samples(
        conversation_db,
        earliest_event_time_utc=earliest,
        row_limit=bounded_limit * 2,
    )
    unique: dict[str, ConditionReviewSample] = {}
    for sample in combined:
        unique.setdefault(sample.sample_digest, sample)
    result = sorted(
        unique.values(), key=lambda sample: (sample.event_time_utc, sample.sample_digest), reverse=True
    )[:bounded_limit]
    with _LIVE_SAMPLE_CACHE_LOCK:
        _LIVE_SAMPLE_CACHE.clear()
        _LIVE_SAMPLE_CACHE[cache_key] = tuple(result)
    return result


def resolve_reviewed_live_samples(
    conversation_db: Path,
    decisions: Mapping[str, Mapping[str, Any]],
    *,
    exclude_digests: set[str],
) -> list[ConditionReviewSample]:
    """Resolve older reviewed text from canonical source using safe metadata."""

    wanted = {
        digest: decision
        for digest, decision in decisions.items()
        if decision.get("queue_kind") == "LIVE" and digest not in exclude_digests
    }
    if not wanted or not conversation_db.is_file():
        return []
    connection = _read_only_connection(conversation_db)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"offers", "messages"}.issubset(tables):
            return []
        offer_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(offers)")
        }
        samples: list[ConditionReviewSample] = []
        for digest, decision in wanted.items():
            group = str(decision.get("source_group_code") or "UNKNOWN")
            event_time = str(decision.get("source_event_time_utc") or "")
            settlement = str(
                decision.get("source_settlement_term") or "UNKNOWN"
            ).upper()
            trade_form = str(decision.get("source_trade_form") or "UNKNOWN").upper()
            if not event_time or group not in {"group_1", "group_2"}:
                continue
            trade_expression = (
                "COALESCE(o.trade_form,'UNKNOWN')"
                if "trade_form" in offer_columns
                else "'UNKNOWN'"
            )
            rows = connection.execute(
                f"""
                SELECT o.source_text,{trade_expression} AS trade_form
                FROM offers o
                JOIN messages m
                  ON m.import_id=o.import_id AND m.message_id=o.message_id
                WHERE m.source_html_file=? AND m.event_time_utc=?
                  AND COALESCE(o.settlement,'UNKNOWN')=?
                  AND trim(COALESCE(o.source_text,''))<>''
                ORDER BY o.id
                """,
                (group, event_time, settlement),
            ).fetchall()
            for row in rows:
                candidate_form = str(row["trade_form"] or "UNKNOWN").upper()
                if trade_form != "UNKNOWN" and candidate_form != trade_form:
                    continue
                text = normalize_offer_text(str(row["source_text"] or ""))[:512]
                candidate_digest = condition_sample_digest(
                    group_code=group,
                    event_time_utc=event_time,
                    settlement_term=settlement,
                    trade_form=candidate_form,
                    model_text=masked_condition_model_text(text),
                )
                if candidate_digest != digest:
                    continue
                axes = extract_offer_conditions(
                    text,
                    event_time_utc=event_time,
                    settlement_term=settlement,
                    trade_form=candidate_form,
                )
                samples.append(
                    ConditionReviewSample(
                        sample_digest=digest,
                        queue_kind="LIVE",
                        source_fingerprint=str(
                            decision.get("source_fingerprint") or "LIVE_CANONICAL"
                        ),
                        group_code=group,
                        event_time_utc=event_time,
                        settlement_term=settlement,
                        trade_form=candidate_form,
                        session_phase=axes.market_session_phase,
                        private_offer_text=text,
                    )
                )
                break
        return samples
    finally:
        connection.close()


_LIVE_SAMPLE_CACHE_LOCK = threading.Lock()
_LIVE_SAMPLE_CACHE: dict[
    tuple[tuple[str, int, int], tuple[str, int, int], int, int],
    tuple[ConditionReviewSample, ...],
] = {}
_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[str, int, int], tuple[dict[str, Any] | None, str]] = {}


def _load_model(path: Path | None) -> tuple[dict[str, Any] | None, str]:
    if path is None or not path.is_file():
        return None, "MODEL_ARTIFACT_UNAVAILABLE"
    stat = path.stat()
    if stat.st_uid != os.geteuid() or stat.st_mode & 0o022:
        return None, "MODEL_ARTIFACT_PERMISSIONS_INVALID"
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _MODEL_CACHE_LOCK:
        if key in _MODEL_CACHE:
            return _MODEL_CACHE[key]
        try:
            import joblib  # research dependency is deliberately optional

            artifact = joblib.load(path)
            if not isinstance(artifact, dict):
                raise ValueError("artifact_not_mapping")
            if artifact.get("taxonomy_version") != CONDITION_TAXONOMY_VERSION:
                raise ValueError("taxonomy_mismatch")
            if artifact.get("status") != "RESEARCH_ONLY_NOT_PROMOTED":
                raise ValueError("artifact_status_invalid")
            privacy = artifact.get("privacy")
            if not isinstance(privacy, dict) or privacy.get("raw_text_retained") is not False:
                raise ValueError("artifact_privacy_invalid")
            result = artifact, sha256(path.read_bytes()).hexdigest()
        except Exception:
            result = None, "MODEL_ARTIFACT_INVALID_OR_DEPENDENCY_MISSING"
        _MODEL_CACHE.clear()
        _MODEL_CACHE[key] = result
        return result


def analyze_condition_sample(
    sample: ConditionReviewSample,
    *,
    model_path: Path | None,
) -> dict[str, Any]:
    axes = extract_offer_conditions(
        sample.private_offer_text,
        event_time_utc=sample.event_time_utc,
        settlement_term=sample.settlement_term,
        trade_form=sample.trade_form,
    )
    artifact, artifact_identity = _load_model(model_path)
    result: dict[str, Any] = {
        "taxonomy_version": CONDITION_TAXONOMY_VERSION,
        "shadow_only": True,
        "runtime_effect": False,
        "rule": axes.to_dict(include_text=True),
        "model": {
            "status": artifact_identity,
            "artifact_sha256": (
                artifact_identity if artifact is not None else None
            ),
            "labels": {},
        },
    }
    if artifact is None:
        return result
    try:
        vectorizer = artifact["vectorizer"]
        models = artifact["models"]
        calibrators = artifact["probability_calibrators"]
        policies = artifact["abstention_policies"]
        matrix = vectorizer.transform(
            [masked_condition_model_text(sample.private_offer_text)]
        )
        labels: dict[str, Any] = {}
        for label in ("HAS_CONDITION", *CONDITION_FAMILIES):
            model = models.get(label)
            calibrator = calibrators.get(label)
            policy = policies.get(label)
            if model is None or calibrator is None or not isinstance(policy, dict):
                labels[label] = {"decision": "RULE_ONLY", "probability": None}
                continue
            raw = model.predict_proba(matrix)[:, 1]
            probability = float(calibrator.predict(raw)[0])
            positive = policy.get("positive_threshold")
            negative = policy.get("negative_threshold")
            if positive is not None and probability >= float(positive):
                decision = "POSITIVE"
            elif negative is not None and probability <= float(negative):
                decision = "NEGATIVE"
            else:
                decision = "ABSTAIN"
            quality_gate_passed = label in set(
                artifact.get("quality_gate_passed_labels") or []
            )
            if decision == "POSITIVE" and not quality_gate_passed:
                decision = "QUALITY_BLOCKED_POSITIVE"
            labels[label] = {
                "decision": decision,
                "probability": round(probability, 6),
                "positive_threshold": positive,
                "negative_threshold": negative,
                "quality_gate_passed": quality_gate_passed,
            }
        result["model"] = {
            "status": "READY_RESEARCH_SHADOW",
            "artifact_sha256": artifact_identity,
            "labels": labels,
        }
    except Exception:
        result["model"] = {
            "status": "MODEL_INFERENCE_FAILED",
            "artifact_sha256": artifact_identity,
            "labels": {},
        }
    return result


class ConditionReviewService:
    def __init__(
        self,
        *,
        conversation_db: Path,
        staging_db: Path | None,
        review_db: Path,
        owner_pack_path: Path | None,
        model_path: Path | None,
        live_recent_days: int = 3,
        live_sample_limit: int = 1_000,
    ):
        self.conversation_db = conversation_db
        self.staging_db = staging_db
        self.owner_pack_path = owner_pack_path
        self.model_path = model_path
        self.live_recent_days = max(1, min(30, int(live_recent_days)))
        self.live_sample_limit = max(100, min(5_000, int(live_sample_limit)))
        self.store = ConditionReviewStore(review_db)

    def _sources(self) -> tuple[list[ConditionReviewSample], list[ConditionReviewSample], dict[str, Any]]:
        sealed, pack_status = load_owner_pack(self.owner_pack_path)
        live = load_live_offer_samples(
            self.conversation_db,
            self.staging_db,
            recent_days=self.live_recent_days,
            sample_limit=self.live_sample_limit,
        )
        sealed_ids = {sample.sample_digest for sample in sealed}
        live = [sample for sample in live if sample.sample_digest not in sealed_ids]
        return sealed, live, pack_status

    def list_queue(
        self,
        *,
        queue: str,
        status: str = "ALL",
        group: str = "ALL",
        offset: int = 0,
        limit: int = 20,
        search: str = "",
    ) -> dict[str, Any]:
        queue_name = str(queue or "SEALED").upper()
        if queue_name not in {"SEALED", "LIVE", "REVIEWED"}:
            raise ConditionReviewError("condition_review_queue_invalid")
        status_name = str(status or "ALL").upper()
        if status_name not in {"ALL", "PENDING", "REVIEWED"}:
            raise ConditionReviewError("condition_review_filter_invalid")
        if group not in {"ALL", "group_1", "group_2"}:
            raise ConditionReviewError("condition_review_filter_invalid")
        try:
            offset_value = max(0, int(offset))
            limit_value = min(50, max(1, int(limit)))
        except (TypeError, ValueError) as exc:
            raise ConditionReviewError("condition_review_paging_invalid") from exc
        query = normalize_offer_text(search)[:80]
        sealed, live, pack_status = self._sources()
        decisions = self.store.load()
        if queue_name == "SEALED":
            source = sealed
        elif queue_name == "LIVE":
            source = live
        else:
            visible_ids = {sample.sample_digest for sample in sealed + live}
            older_reviewed = resolve_reviewed_live_samples(
                self.conversation_db,
                decisions,
                exclude_digests=visible_ids,
            )
            source = sealed + live + older_reviewed
            source = [sample for sample in source if sample.sample_digest in decisions]
        if status_name == "PENDING":
            source = [sample for sample in source if sample.sample_digest not in decisions]
        elif status_name == "REVIEWED":
            source = [sample for sample in source if sample.sample_digest in decisions]
        if group != "ALL":
            source = [sample for sample in source if sample.group_code == group]
        if query:
            source = [
                sample
                for sample in source
                if query in normalize_offer_text(sample.private_offer_text)
            ]
        if queue_name == "SEALED":
            source = sorted(
                source, key=lambda sample: (sample.event_time_utc, sample.sample_digest)
            )
        total = len(source)
        page = source[offset_value : offset_value + limit_value]
        items: list[dict[str, Any]] = []
        for sample in page:
            review = decisions.get(sample.sample_digest)
            reveal_analysis = sample.queue_kind == "LIVE" or review is not None
            review_payload = None
            if review is not None:
                review_payload = {
                    **review,
                    "owner_condition_text": _span_text(
                        sample.private_offer_text,
                        review.get("owner_condition_spans") or [],
                    ),
                }
                review_payload.pop("owner_condition_spans", None)
            items.append(
                {
                    "sample_digest": sample.sample_digest,
                    "queue_kind": sample.queue_kind,
                    "group_code": sample.group_code,
                    "event_time_utc": sample.event_time_utc,
                    "settlement_term": sample.settlement_term,
                    "trade_form": sample.trade_form,
                    "session_phase": sample.session_phase,
                    "private_offer_text": sample.private_offer_text,
                    "review": review_payload,
                    "analysis": (
                        analyze_condition_sample(sample, model_path=self.model_path)
                        if reveal_analysis
                        else None
                    ),
                    "analysis_blinded_until_review": not reveal_analysis,
                }
            )
        reviewed_sealed = sum(
            sample.sample_digest in decisions for sample in sealed
        )
        reviewed_live = sum(sample.sample_digest in decisions for sample in live)
        _, model_status = _load_model(self.model_path)
        if model_status and re.fullmatch(r"[0-9a-f]{64}", model_status):
            model_status = "READY_RESEARCH_SHADOW"
        return {
            "schema_version": "coin-offer-condition-review-api-v1",
            "generated_at_utc": _utc_now(),
            "queue": queue_name,
            "status_filter": status_name,
            "group_filter": group,
            "offset": offset_value,
            "limit": limit_value,
            "total": total,
            "items": items,
            "progress": {
                "sealed_total": len(sealed),
                "sealed_reviewed": reviewed_sealed,
                "live_visible": len(live),
                "live_reviewed": reviewed_live,
            },
            "sealed_pack": pack_status,
            "model_status": model_status,
            "claim_boundary": {
                "authenticated_private_review": True,
                "shadow_only": True,
                "offer_runtime_effect": False,
                "estimator_runtime_effect": False,
                "sealed_predictions_blinded_until_owner_review": True,
            },
        }

    def record(
        self, payload: Mapping[str, Any], *, reviewer: str
    ) -> dict[str, Any]:
        digest = str(payload.get("sample_digest") or "")
        if not _DIGEST_RE.fullmatch(digest):
            raise ConditionReviewError("condition_review_sample_invalid")
        sealed, live, _ = self._sources()
        sample = next(
            (item for item in sealed + live if item.sample_digest == digest), None
        )
        if sample is None:
            raise ConditionReviewError("condition_review_sample_not_found")
        result = self.store.record(sample, payload, reviewer=reviewer)
        return {
            "status": "RECORDED_RESEARCH_REVIEW",
            "sample_digest": sample.sample_digest,
            **result,
            "analysis": analyze_condition_sample(
                sample, model_path=self.model_path
            ),
            "runtime_effect": False,
        }
