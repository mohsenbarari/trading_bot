#!/usr/bin/env python3
"""Privacy-safe audit of primary melted-gold revisions against legacy labels.

The command reads raw capture spools only where they already live.  Its output
contains aggregate counters and confusion matrices; it never emits message
text, Telegram identifiers, per-message hashes, or examples.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable, Mapping, Sequence
import unicodedata


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.private_gold import (  # noqa: E402
    PrivateGoldOfferInput,
    parse_private_gold_offer,
)
from core.market_intelligence.private_gold_trade_revisions import (  # noqa: E402
    PrivateGoldRevision,
    extract_private_gold_trade,
)


COMMAND_VERSION = "private-gold-trade-revision-audit-v1"
SOURCE_CODE = "MELTED_PRIMARY_FLOW"
OFFER_LIFETIME_SECONDS = 120
_SPOOL = re.compile(r"^events-\d{4}-\d{2}-\d{2}\.jsonl$")
_QUANTITY = re.compile(r"(?<!\d)(\d{1,4})\s*(?:تا\b|عدد\b|تا\s*عدد)")
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_RESOLVED_LABELS = frozenset({"NONE", "FULL", "PARTIAL"})
_BATCH_DIVIDER = re.compile(r"(?:\r?\n\s*)+[━─—-]{3,}\s*(?:\r?\n\s*)+")
_NUMBER = re.compile(r"\d+")
_REMAINING_QUANTITY = (
    re.compile(r"(?:باقی|مانده|موند|مقدار)\D{0,16}(\d{1,4})(?!\d)"),
    re.compile(r"(?<!\d)(\d{1,4})\D{0,16}(?:باقی|مانده|موند)"),
)
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TRADE_WORD", re.compile(r"معامل|انجام|فروخته|خریداری|خورد")),
    ("COMPLETION_WORD", re.compile(r"تمام|تکمیل|کامل")),
    ("REMAINING_WORD", re.compile(r"باقی|مانده|موند|مقدار")),
    ("CANCEL_WORD", re.compile(r"لغو|باطل|کنسل|حذف")),
    ("POSITIVE_SYMBOL", re.compile(r"[✅✔☑]")),
    ("NEGATIVE_SYMBOL", re.compile(r"[❌✖❎]")),
)


class AuditError(RuntimeError):
    pass


def _dt(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def _hashed_message_id(key: bytes, message_id: object) -> str:
    material = f"{SOURCE_CODE}:{int(str(message_id))}".encode("ascii")
    return hmac.new(key, material, sha256).hexdigest()


def _json_lines(path: Path) -> Iterable[Mapping[str, object]]:
    with path.open("rb") as handle:
        for raw in handle:
            if not raw.endswith(b"\n"):
                continue
            try:
                item = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(item, Mapping):
                yield item


def _legacy(path: Path) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for item in _json_lines(path):
        key = str(item.get("key") or "")
        if len(key) == 64:
            result[key] = item
    return result


def _embedded_items(text: str) -> tuple[Mapping[str, object], ...]:
    candidates: list[object] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        for segment in _BATCH_DIVIDER.split(text):
            try:
                candidates.append(json.loads(segment))
            except json.JSONDecodeError:
                continue
    result: list[Mapping[str, object]] = []
    for candidate in candidates:
        values = candidate if isinstance(candidate, list) else (candidate,)
        result.extend(item for item in values if isinstance(item, Mapping))
    return tuple(result)


def _embedded_primary(
    capture: Mapping[str, list[Mapping[str, object]]],
    *,
    key: bytes,
) -> tuple[dict[str, list[Mapping[str, object]]], Counter[str]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    counters: Counter[str] = Counter()
    for events in capture.values():
        for event in events:
            if event.get("event_type") == "message_deleted":
                continue
            message = _message(event)
            producer = _producer(event)
            if message is None or producer is None:
                continue
            items = _embedded_items(str(message.get("text") or ""))
            counters["outer_revisions_with_json"] += int(bool(items))
            counters["embedded_items"] += len(items)
            for item in items:
                source = item.get("source")
                gold = item.get("gold")
                event_type = str(item.get("event_type") or "")
                counters[f"embedded_event_type:{event_type or 'MISSING'}"] += 1
                if (
                    str(item.get("schema_version") or "") != "1.0"
                    or not isinstance(source, Mapping)
                    or not isinstance(gold, Mapping)
                    or str(source.get("source_key") or "") != "account1_channel"
                    or event_type != "message_created"
                ):
                    continue
                try:
                    identity = _hashed_message_id(key, gold.get("message_id"))
                except (TypeError, ValueError):
                    counters["embedded_offer_invalid"] += 1
                    continue
                text = gold.get("text")
                published = _dt(gold.get("telegram_datetime"))
                if not isinstance(text, str) or not text.strip() or published is None:
                    counters["embedded_offer_invalid"] += 1
                    continue
                grouped[identity].append(
                    {
                        "text_sha256": sha256(text.encode()).hexdigest(),
                        "published_second": published.replace(microsecond=0).isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "edited_at_utc": gold.get("telegram_edit_datetime"),
                        "outer_available_at_utc": producer.get("available_at_utc"),
                    }
                )
                counters["embedded_offers"] += 1
    counters["embedded_offer_messages"] = len(grouped)
    return grouped, counters


def _capture_events(
    spool: Path,
    *,
    key: bytes,
) -> tuple[dict[str, list[Mapping[str, object]]], Counter[str]]:
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    counters: Counter[str] = Counter()
    files = sorted(path for path in spool.iterdir() if path.is_file() and _SPOOL.fullmatch(path.name))
    for path in files:
        for event in _json_lines(path):
            counters["records_seen"] += 1
            source = event.get("source")
            message = event.get("message")
            producer = event.get("producer")
            if not isinstance(source, Mapping) or source.get("source_id") != SOURCE_CODE:
                continue
            if not isinstance(message, Mapping) or not isinstance(producer, Mapping):
                counters["primary_invalid"] += 1
                continue
            try:
                identity = _hashed_message_id(key, message.get("message_id"))
            except (TypeError, ValueError):
                counters["primary_invalid"] += 1
                continue
            grouped[identity].append(event)
            counters["primary_events"] += 1
            counters[f"event_type:{event.get('event_type')}"] += 1
    for events in grouped.values():
        events.sort(
            key=lambda event: (
                int((event.get("producer") or {}).get("capture_sequence") or 0),
                str((event.get("producer") or {}).get("available_at_utc") or ""),
                str(event.get("event_id") or ""),
            )
        )
    counters["primary_messages"] = len(grouped)
    return grouped, counters


def _utf16_boundaries(text: str) -> list[int]:
    result = [0]
    total = 0
    for character in text:
        total += len(character.encode("utf-16-le")) // 2
        result.append(total)
    return result


def _python_index(boundaries: list[int], offset: int, *, end: bool) -> int | None:
    if offset < 0 or offset > boundaries[-1]:
        return None
    if offset in boundaries:
        return boundaries.index(offset)
    if end:
        for index, value in enumerate(boundaries):
            if value > offset:
                return index
    else:
        for index in range(len(boundaries) - 1, -1, -1):
            if boundaries[index] < offset:
                return index
    return None


def _strike_fragments(message: Mapping[str, object]) -> tuple[str, ...]:
    text = str(message.get("text") or "")
    boundaries = _utf16_boundaries(text)
    result: list[str] = []
    entities = message.get("entities")
    if not isinstance(entities, list):
        return ()
    for entity in entities:
        if not isinstance(entity, Mapping) or str(entity.get("type") or "") != "MessageEntityStrike":
            continue
        try:
            offset = int(entity.get("offset_utf16"))
            length = int(entity.get("length_utf16"))
        except (TypeError, ValueError):
            continue
        start = _python_index(boundaries, offset, end=False)
        end = _python_index(boundaries, offset + length, end=True)
        if start is not None and end is not None and start < end:
            result.append(text[start:end])
    return tuple(result)


def _quantity(text: str) -> int | None:
    match = _QUANTITY.search(text.translate(_DIGITS))
    if match is None:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _semantic_text_digest(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(_DIGITS).casefold()
    material = "".join(character for character in normalized if character.isalnum())
    return sha256(material.encode()).hexdigest()


def _remaining_quantity(text: str) -> int | None:
    for pattern in _REMAINING_QUANTITY:
        match = pattern.search(text.translate(_DIGITS))
        if match is not None:
            value = int(match.group(1))
            if 0 <= value <= 10_000:
                return value
    return None


def _parsed(text: str, published: str, available: str) -> object | None:
    try:
        return parse_private_gold_offer(
            PrivateGoldOfferInput(
                source_event_id="audit",
                published_at_utc=published,
                available_at_utc=available,
                text=text,
                trade_status="NONE",
            )
        )
    except (TypeError, ValueError):
        return None


def _message(event: Mapping[str, object]) -> Mapping[str, object] | None:
    value = event.get("message")
    return value if isinstance(value, Mapping) else None


def _producer(event: Mapping[str, object]) -> Mapping[str, object] | None:
    value = event.get("producer")
    return value if isinstance(value, Mapping) else None


def _features(events: list[Mapping[str, object]]) -> dict[str, object]:
    revisions = [event for event in events if event.get("event_type") != "message_deleted" and _message(event)]
    if not revisions:
        return {"has_revision": False}
    first = revisions[0]
    first_message = _message(first)
    first_producer = _producer(first)
    assert first_message is not None and first_producer is not None
    published = str(first_message.get("published_at_utc") or "")
    first_available = str(first_producer.get("available_at_utc") or published)
    first_text = str(first_message.get("text") or "")
    initial = _parsed(first_text, published, first_available)
    first_strikes = _strike_fragments(first_message)
    previous_text = first_text
    previous_strikes = first_strikes
    candidate_quantity = None
    candidate_reason = None
    candidate_at = None
    edit_times: list[str] = []
    edits_within = 0
    edits_after = 0
    text_changes = 0
    format_only_edits = 0
    strike_edits = 0
    quantity_reductions = 0
    publication = _dt(published)
    for event in revisions[1:]:
        if event.get("event_type") != "message_edited":
            continue
        message = _message(event)
        producer = _producer(event)
        assert message is not None and producer is not None
        edited = _dt(message.get("edited_at_utc"))
        if edited is not None:
            edit_times.append(edited.isoformat().replace("+00:00", "Z"))
        age = (edited - publication).total_seconds() if edited is not None and publication is not None else None
        within = age is not None and 0 <= age <= OFFER_LIFETIME_SECONDS
        edits_within += int(within)
        edits_after += int(age is not None and age > OFFER_LIFETIME_SECONDS)
        text = str(message.get("text") or "")
        strikes = _strike_fragments(message)
        if text == previous_text:
            format_only_edits += 1
        else:
            text_changes += 1
        if strikes != previous_strikes:
            strike_edits += 1
        parsed = _parsed(text, published, str(producer.get("available_at_utc") or published))
        initial_quantity = getattr(initial, "quantity", None)
        parsed_quantity = getattr(parsed, "quantity", None)
        reduced = (
            isinstance(initial_quantity, int)
            and isinstance(parsed_quantity, int)
            and 0 < parsed_quantity < initial_quantity
        )
        quantity_reductions += int(reduced)
        if within and candidate_reason is None:
            newly_struck = strikes if strikes != previous_strikes else ()
            struck_quantities = [value for fragment in newly_struck if (value := _quantity(fragment))]
            if struck_quantities and isinstance(initial_quantity, int):
                candidate_quantity = min(initial_quantity, max(struck_quantities))
                candidate_reason = "STRIKE_QUANTITY"
                candidate_at = message.get("edited_at_utc")
            elif newly_struck and isinstance(initial_quantity, int):
                visible = sum(len(fragment.strip()) for fragment in newly_struck)
                non_space = max(1, len(re.sub(r"\s+", "", text)))
                if visible / non_space >= 0.65:
                    candidate_quantity = initial_quantity
                    candidate_reason = "STRIKE_FULL_MESSAGE"
                    candidate_at = message.get("edited_at_utc")
            elif reduced and isinstance(initial_quantity, int) and isinstance(parsed_quantity, int):
                candidate_quantity = initial_quantity - parsed_quantity
                candidate_reason = "QUANTITY_REDUCTION"
                candidate_at = message.get("edited_at_utc")
        previous_text = text
        previous_strikes = strikes
    deletes_within = 0
    for event in events:
        if event.get("event_type") != "message_deleted":
            continue
        producer = _producer(event)
        deleted = _dt(producer.get("available_at_utc")) if producer else None
        if publication is not None and deleted is not None:
            deletes_within += int(0 <= (deleted - publication).total_seconds() <= OFFER_LIFETIME_SECONDS)
    final_message = _message(revisions[-1])
    final_producer = _producer(revisions[-1])
    assert final_message is not None
    assert final_producer is not None
    final_parsed = _parsed(
        str(final_message.get("text") or ""),
        published,
        str(final_producer.get("available_at_utc") or published),
    )
    final_economics = (
        (
            int(final_parsed.price_toman),
            int(final_parsed.quantity),
            str(final_parsed.side),
            str(final_parsed.settlement_term),
            str(final_parsed.trade_form),
        )
        if final_parsed is not None
        else None
    )
    return {
        "has_revision": True,
        "first_event_type": str(first.get("event_type") or ""),
        "revision_count": len(revisions),
        "edits_within": edits_within,
        "edits_after": edits_after,
        "text_changes": text_changes,
        "format_only_edits": format_only_edits,
        "strike_edits": strike_edits,
        "quantity_reductions": quantity_reductions,
        "deletes_within": deletes_within,
        "initial_parsed": initial is not None,
        "published_second": (
            publication.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            if publication is not None
            else None
        ),
        "final_text_sha256": sha256(str(final_message.get("text") or "").encode()).hexdigest(),
        "final_semantic_text_sha256": _semantic_text_digest(
            str(final_message.get("text") or "")
        ),
        "final_economics": final_economics,
        "candidate_reason": candidate_reason,
        "candidate_quantity": candidate_quantity,
        "candidate_at": candidate_at,
        "edit_times": tuple(edit_times),
    }


def _event_edit_key(event: Mapping[str, object]) -> str | None:
    message = _message(event)
    edited = _dt(message.get("edited_at_utc")) if message else None
    return edited.isoformat().replace("+00:00", "Z") if edited is not None else None


def _entity_types(message: Mapping[str, object]) -> frozenset[str]:
    entities = message.get("entities")
    if not isinstance(entities, list):
        return frozenset()
    return frozenset(
        str(entity.get("type"))
        for entity in entities
        if isinstance(entity, Mapping) and entity.get("type")
    )


def _transition(
    events: list[Mapping[str, object]],
    *,
    edit_key: str,
    legacy_quantity: object,
) -> Counter[str]:
    result: Counter[str] = Counter()
    revisions = [event for event in events if event.get("event_type") != "message_deleted" and _message(event)]
    target_index = next(
        (index for index, event in enumerate(revisions) if _event_edit_key(event) == edit_key),
        None,
    )
    if target_index is None:
        result["transition_missing"] += 1
        return result
    if target_index == 0:
        result["previous_revision_missing"] += 1
        return result
    previous = _message(revisions[target_index - 1])
    current = _message(revisions[target_index])
    previous_producer = _producer(revisions[target_index - 1])
    current_producer = _producer(revisions[target_index])
    assert previous is not None and current is not None
    assert previous_producer is not None and current_producer is not None
    before = str(previous.get("text") or "").translate(_DIGITS)
    after = str(current.get("text") or "").translate(_DIGITS)
    publication = str(current.get("published_at_utc") or previous.get("published_at_utc") or "")
    before_parsed = _parsed(before, publication, str(previous_producer.get("available_at_utc") or publication))
    after_parsed = _parsed(after, publication, str(current_producer.get("available_at_utc") or publication))
    result["transition_available"] += 1
    published_at = _dt(publication)
    edited_at = _dt(current.get("edited_at_utc"))
    if published_at is not None and edited_at is not None:
        age = (edited_at - published_at).total_seconds()
        result["target_edit_within_lifetime"] += int(0 <= age <= OFFER_LIFETIME_SECONDS)
        result["target_edit_after_lifetime"] += int(age > OFFER_LIFETIME_SECONDS)
        result["target_edit_before_publication"] += int(age < 0)
    result["text_shrank" if len(after) < len(before) else "text_grew" if len(after) > len(before) else "text_size_same"] += 1
    before_lines = len(before.splitlines())
    after_lines = len(after.splitlines())
    result[
        "lines_removed" if after_lines < before_lines else "lines_added" if after_lines > before_lines else "line_count_same"
    ] += 1
    before_numbers = Counter(_NUMBER.findall(before))
    after_numbers = Counter(_NUMBER.findall(after))
    removed = list((before_numbers - after_numbers).elements())
    added = list((after_numbers - before_numbers).elements())
    result["numbers_unchanged" if not removed and not added else "numbers_changed"] += 1
    result["numbers_removed"] += len(removed)
    result["numbers_added"] += len(added)
    before_quantity = getattr(before_parsed, "quantity", None)
    after_quantity = getattr(after_parsed, "quantity", None)
    before_price = getattr(before_parsed, "price_toman", None)
    after_price = getattr(after_parsed, "price_toman", None)
    result["parser_quantity_changed"] += int(
        before_quantity is not None and after_quantity is not None and before_quantity != after_quantity
    )
    result["parser_price_changed"] += int(
        before_price is not None and after_price is not None and before_price != after_price
    )
    result["parser_became_unresolved"] += int(before_parsed is not None and after_parsed is None)
    result["parser_became_resolved"] += int(before_parsed is None and after_parsed is not None)
    try:
        expected = int(legacy_quantity) if legacy_quantity is not None else None
    except (TypeError, ValueError):
        expected = None
    if expected is not None:
        result["legacy_quantity_in_removed_numbers"] += int(str(expected) in removed)
        result["legacy_quantity_in_added_numbers"] += int(str(expected) in added)
        result["legacy_quantity_equals_offer_quantity"] += int(expected == before_quantity)
        result["legacy_quantity_equals_parser_delta"] += int(
            isinstance(before_quantity, int)
            and isinstance(after_quantity, int)
            and expected == before_quantity - after_quantity
        )
        remaining = _remaining_quantity(after)
        result["remaining_quantity_found"] += int(remaining is not None)
        result["remaining_quantity_valid_for_offer"] += int(
            isinstance(before_quantity, int)
            and remaining is not None
            and 0 <= remaining < before_quantity
        )
        result["legacy_quantity_equals_remaining"] += int(expected == remaining)
        result["legacy_quantity_equals_offer_minus_remaining"] += int(
            isinstance(before_quantity, int)
            and remaining is not None
            and expected == before_quantity - remaining
        )
    before_types = _entity_types(previous)
    after_types = _entity_types(current)
    for entity_type in sorted(after_types - before_types):
        result[f"entity_added:{entity_type}"] += 1
    for entity_type in sorted(before_types - after_types):
        result[f"entity_removed:{entity_type}"] += 1
    for marker_name, marker in _MARKERS:
        before_marker = bool(marker.search(before))
        after_marker = bool(marker.search(after))
        result[f"marker_added:{marker_name}"] += int(after_marker and not before_marker)
        result[f"marker_removed:{marker_name}"] += int(before_marker and not after_marker)
        result[f"marker_present_after:{marker_name}"] += int(after_marker)
    return result


def _new_trade_decision(events: list[Mapping[str, object]]):
    revisions: list[PrivateGoldRevision] = []
    publication: datetime | None = None
    latest_available: datetime | None = None
    for event in events:
        if event.get("event_type") == "message_deleted":
            continue
        message = _message(event)
        producer = _producer(event)
        if message is None or producer is None:
            continue
        published = _dt(message.get("published_at_utc"))
        available = _dt(producer.get("available_at_utc"))
        text = message.get("text")
        if published is None or available is None or not isinstance(text, str):
            continue
        publication = published if publication is None else min(publication, published)
        latest_available = available if latest_available is None else max(latest_available, available)
        revisions.append(
            PrivateGoldRevision(
                event_id=str(event.get("event_id") or ""),
                event_type=str(event.get("event_type") or ""),
                published_at_utc=published,
                available_at_utc=available,
                edited_at_utc=_dt(message.get("edited_at_utc")),
                text=text,
            )
        )
    if not revisions or publication is None or latest_available is None:
        return None
    as_of = max(
        latest_available,
        publication + timedelta(seconds=OFFER_LIFETIME_SECONDS + 1),
    )
    return extract_private_gold_trade(revisions, as_of_utc=as_of)


def _run(args: argparse.Namespace) -> int:
    spool = Path(args.market_spool_dir).resolve()
    reference_path = Path(args.legacy_reference).resolve()
    key_path = Path(args.hmac_key).resolve()
    output = Path(args.output_report).resolve()
    if not spool.is_dir() or not reference_path.is_file() or not key_path.is_file():
        raise AuditError("audit_input_unavailable")
    if output.exists() or not output.parent.is_dir():
        raise AuditError("audit_output_invalid")
    key = key_path.read_bytes()
    if len(key) < 32:
        raise AuditError("audit_hmac_key_invalid")
    legacy = _legacy(reference_path)
    capture, capture_counts = _capture_events(spool, key=key)
    embedded_capture, embedded_counts = _embedded_primary(capture, key=key)
    embedded_overlap = set(legacy) & set(embedded_capture)
    embedded_text_matches = sum(
        any(
            revision.get("text_sha256") == legacy[identity].get("text_sha256")
            for revision in embedded_capture[identity]
        )
        for identity in embedded_overlap
    )
    embedded_time_matches = sum(
        any(
            revision.get("published_second")
            == (
                timestamp.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                if (timestamp := _dt(legacy[identity].get("event_time_utc"))) is not None
                else None
            )
            for revision in embedded_capture[identity]
        )
        for identity in embedded_overlap
    )
    direct_overlap = set(legacy) & set(capture)
    legacy_composites: dict[tuple[str, str], list[str]] = defaultdict(list)
    legacy_semantic_composites: dict[tuple[str, str], list[str]] = defaultdict(list)
    legacy_economic_composites: dict[tuple[object, ...], list[str]] = defaultdict(list)
    legacy_time_counts: Counter[str] = Counter()
    for identity, item in legacy.items():
        event_time = _dt(item.get("event_time_utc"))
        if event_time is None:
            continue
        second = event_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        legacy_time_counts[second] += 1
        legacy_composites[
            (
                second,
                str(item.get("text_sha256") or ""),
            )
        ].append(identity)
        legacy_semantic_composites[
            (second, str(item.get("semantic_text_sha256") or ""))
        ].append(identity)
        parsed = item.get("parsed")
        if isinstance(parsed, Mapping):
            legacy_economic_composites[
                (
                    second,
                    int(parsed.get("price_toman") or 0),
                    int(parsed.get("quantity") or 0),
                    str(parsed.get("side") or ""),
                    str(parsed.get("settlement_term") or ""),
                    str(parsed.get("trade_form") or ""),
                )
            ].append(identity)
    capture_features = {identity: _features(events) for identity, events in capture.items()}
    matched: list[tuple[str, str, str]] = [
        (identity, identity, "MESSAGE_ID") for identity in sorted(direct_overlap)
    ]
    used_legacy = set(direct_overlap)
    used_capture = set(direct_overlap)
    ambiguous_composites = 0
    for capture_identity, features in capture_features.items():
        if capture_identity in used_capture:
            continue
        composite = (
            str(features.get("published_second") or ""),
            str(features.get("final_text_sha256") or ""),
        )
        candidates = [identity for identity in legacy_composites.get(composite, ()) if identity not in used_legacy]
        if len(candidates) != 1:
            ambiguous_composites += int(len(candidates) > 1)
            continue
        legacy_identity = candidates[0]
        used_legacy.add(legacy_identity)
        used_capture.add(capture_identity)
        matched.append((legacy_identity, capture_identity, "TIME_TEXT"))
    for capture_identity, features in capture_features.items():
        if capture_identity in used_capture:
            continue
        composite = (
            str(features.get("published_second") or ""),
            str(features.get("final_semantic_text_sha256") or ""),
        )
        candidates = [
            identity
            for identity in legacy_semantic_composites.get(composite, ())
            if identity not in used_legacy
        ]
        if len(candidates) != 1:
            ambiguous_composites += int(len(candidates) > 1)
            continue
        legacy_identity = candidates[0]
        used_legacy.add(legacy_identity)
        used_capture.add(capture_identity)
        matched.append((legacy_identity, capture_identity, "TIME_SEMANTIC_TEXT"))
    for capture_identity, features in capture_features.items():
        if capture_identity in used_capture or features.get("final_economics") is None:
            continue
        economics = tuple(features["final_economics"])
        composite = (str(features.get("published_second") or ""), *economics)
        candidates = [
            identity
            for identity in legacy_economic_composites.get(composite, ())
            if identity not in used_legacy
        ]
        if len(candidates) != 1:
            ambiguous_composites += int(len(candidates) > 1)
            continue
        legacy_identity = candidates[0]
        used_legacy.add(legacy_identity)
        used_capture.add(capture_identity)
        matched.append((legacy_identity, capture_identity, "TIME_ECONOMICS"))
    capture_time_counts = Counter(
        str(features.get("published_second") or "")
        for features in capture_features.values()
        if features.get("published_second")
    )
    economic_index: dict[tuple[object, ...], list[tuple[datetime, str]]] = defaultdict(list)
    edit_time_index: dict[str, list[str]] = defaultdict(list)
    capture_publications = []
    for identity, features in capture_features.items():
        published_at = _dt(features.get("published_second"))
        economics = features.get("final_economics")
        if published_at is None:
            continue
        capture_publications.append(published_at)
        if economics is not None:
            economic_index[tuple(economics)].append((published_at, identity))
        for edited_at in features.get("edit_times") or ():
            edit_time_index[str(edited_at)].append(identity)
    for rows in economic_index.values():
        rows.sort(key=lambda row: (row[0], row[1]))
    nearest_economics: dict[str, Counter[str]] = defaultdict(Counter)
    greedy_pair_features: dict[str, Counter[str]] = defaultdict(Counter)
    greedy_pair_confusion: Counter[str] = Counter()
    greedy_pair_quantity: Counter[str] = Counter()
    used_greedy_capture: set[str] = set()
    legacy_edit_match: dict[str, Counter[str]] = defaultdict(Counter)
    legacy_edit_match_features: dict[str, Counter[str]] = defaultdict(Counter)
    legacy_edit_transition_features: dict[str, Counter[str]] = defaultdict(Counter)
    new_extractor_by_legacy: dict[str, Counter[str]] = defaultdict(Counter)
    new_extractor_in_lifetime_confusion: Counter[str] = Counter()
    new_extractor_quantity: Counter[str] = Counter()
    if capture_publications:
        capture_start = min(capture_publications)
        capture_end = max(capture_publications)
        ordered_legacy = sorted(
            legacy.values(), key=lambda item: str(item.get("event_time_utc") or "")
        )
        for item in ordered_legacy:
            status = str(item.get("trade_status") or "UNKNOWN").upper()
            edit_value = item.get("trade_edited_at_utc") or item.get("offer_edited_at_utc")
            edit_at = _dt(edit_value)
            if edit_at is not None:
                edit_key = edit_at.isoformat().replace("+00:00", "Z")
                matches = edit_time_index.get(edit_key, ())
                edit_bucket = legacy_edit_match[status]
                edit_bucket["legacy_with_edit_time"] += 1
                edit_bucket["capture_exact_edit_time"] += int(bool(matches))
                edit_bucket["capture_unique_exact_edit_time"] += int(len(matches) == 1)
                if matches:
                    matching_features = capture_features[matches[0]]
                    edit_bucket[
                        f"candidate:{matching_features.get('candidate_reason') or 'NONE'}"
                    ] += 1
                    feature_bucket = legacy_edit_match_features[status]
                    for name in (
                        "initial_parsed",
                        "edits_within",
                        "edits_after",
                        "text_changes",
                        "format_only_edits",
                        "strike_edits",
                        "quantity_reductions",
                        "deletes_within",
                    ):
                        feature_bucket[name] += int(matching_features.get(name) or 0)
                    feature_bucket[
                        f"first:{matching_features.get('first_event_type')}"
                    ] += 1
                    feature_bucket[
                        f"revisions:{min(4, int(matching_features.get('revision_count') or 0))}"
                    ] += 1
                    legacy_edit_transition_features[status].update(
                        _transition(
                            capture[matches[0]],
                            edit_key=edit_key,
                            legacy_quantity=item.get("traded_quantity"),
                        )
                    )
                    decision = _new_trade_decision(capture[matches[0]])
                    if decision is not None:
                        new_bucket = new_extractor_by_legacy[status]
                        new_bucket[f"decision:{decision.status}"] += 1
                        first_message = _message(capture[matches[0]][0])
                        published_at = (
                            _dt(first_message.get("published_at_utc"))
                            if first_message is not None
                            else None
                        )
                        target_age = (
                            (edit_at - published_at).total_seconds()
                            if published_at is not None
                            else None
                        )
                        if target_age is not None and 0 <= target_age <= OFFER_LIFETIME_SECONDS:
                            actual = status in {"FULL", "PARTIAL"}
                            predicted = decision.status in {"FULL", "PARTIAL"}
                            new_extractor_in_lifetime_confusion[
                                "tp"
                                if actual and predicted
                                else "fn"
                                if actual
                                else "fp"
                                if predicted
                                else "tn"
                            ] += 1
                            if actual and predicted:
                                expected = item.get("traded_quantity")
                                new_extractor_quantity["exact"] += int(
                                    expected is not None and decision.traded_quantity == expected
                                )
                                new_extractor_quantity["different"] += int(
                                    expected is not None and decision.traded_quantity != expected
                                )
                        elif status in {"FULL", "PARTIAL"}:
                            new_bucket["legacy_positive_after_lifetime"] += 1
            published_at = _dt(item.get("event_time_utc"))
            parsed = item.get("parsed")
            if published_at is None or not isinstance(parsed, Mapping):
                continue
            if not capture_start <= published_at <= capture_end:
                continue
            bucket = nearest_economics[status]
            bucket["legacy_in_capture_horizon"] += 1
            economics = (
                int(parsed.get("price_toman") or 0),
                int(parsed.get("quantity") or 0),
                str(parsed.get("side") or ""),
                str(parsed.get("settlement_term") or ""),
                str(parsed.get("trade_form") or ""),
            )
            candidates = economic_index.get(economics, ())
            if not candidates:
                bucket["no_exact_economics"] += 1
                continue
            bucket["has_exact_economics"] += 1
            times = [row[0] for row in candidates]
            index = bisect_left(times, published_at)
            nearby = candidates[max(0, index - 1) : min(len(candidates), index + 2)]
            delta = min(abs((candidate[0] - published_at).total_seconds()) for candidate in nearby)
            for threshold in (0, 1, 2, 5, 10, 30, 60, 120, 300, 900, 3600):
                bucket[f"nearest_within_{threshold}s"] += int(delta <= threshold)
            available_candidates = [
                candidate
                for candidate in candidates
                if candidate[1] not in used_greedy_capture
                and abs((candidate[0] - published_at).total_seconds()) <= OFFER_LIFETIME_SECONDS
            ]
            if not available_candidates:
                bucket["greedy_unpaired"] += 1
                continue
            selected = min(
                available_candidates,
                key=lambda candidate: (
                    abs((candidate[0] - published_at).total_seconds()),
                    candidate[0],
                    candidate[1],
                ),
            )
            used_greedy_capture.add(selected[1])
            bucket["greedy_paired"] += 1
            pair_delta = abs((selected[0] - published_at).total_seconds())
            for threshold in (0, 1, 2, 5, 10, 30, 60, 120):
                bucket[f"greedy_within_{threshold}s"] += int(pair_delta <= threshold)
            features = capture_features[selected[1]]
            pair_bucket = greedy_pair_features[status]
            for name in (
                "initial_parsed",
                "edits_within",
                "edits_after",
                "text_changes",
                "format_only_edits",
                "strike_edits",
                "quantity_reductions",
                "deletes_within",
            ):
                pair_bucket[name] += int(features.get(name) or 0)
            pair_bucket[f"candidate:{features.get('candidate_reason') or 'NONE'}"] += 1
            pair_bucket[f"first:{features.get('first_event_type')}"] += 1
            if status not in _RESOLVED_LABELS:
                continue
            actual = status in {"FULL", "PARTIAL"}
            predicted = features.get("candidate_reason") is not None
            greedy_pair_confusion[
                "tp" if actual and predicted else "fn" if actual else "fp" if predicted else "tn"
            ] += 1
            if actual and predicted:
                expected = item.get("traded_quantity")
                candidate = features.get("candidate_quantity")
                greedy_pair_quantity["exact"] += int(expected is not None and candidate == expected)
                greedy_pair_quantity["different"] += int(expected is not None and candidate != expected)
    labels: Counter[str] = Counter()
    feature_counts: dict[str, Counter[str]] = defaultdict(Counter)
    confusion: Counter[str] = Counter()
    quantity: Counter[str] = Counter()
    current_hash_matches = 0
    resolved = 0
    match_modes: Counter[str] = Counter()
    for legacy_identity, capture_identity, match_mode in matched:
        match_modes[match_mode] += 1
        reference = legacy[legacy_identity]
        status = str(reference.get("trade_status") or "UNKNOWN").upper()
        labels[status] += 1
        features = capture_features[capture_identity]
        if not features.get("has_revision"):
            continue
        current_hash_matches += int(features.get("final_text_sha256") == reference.get("text_sha256"))
        bucket = feature_counts[status]
        for name in (
            "initial_parsed",
            "edits_within",
            "edits_after",
            "text_changes",
            "format_only_edits",
            "strike_edits",
            "quantity_reductions",
            "deletes_within",
        ):
            bucket[name] += int(features.get(name) or 0)
        bucket[f"first:{features.get('first_event_type')}"] += 1
        bucket[f"candidate:{features.get('candidate_reason') or 'NONE'}"] += 1
        bucket[f"revisions:{min(4, int(features.get('revision_count') or 0))}"] += 1
        if status not in _RESOLVED_LABELS:
            continue
        resolved += 1
        actual = status in {"FULL", "PARTIAL"}
        predicted = features.get("candidate_reason") is not None
        confusion[
            "tp" if actual and predicted else "fn" if actual else "fp" if predicted else "tn"
        ] += 1
        if actual and predicted:
            expected = reference.get("traded_quantity")
            candidate = features.get("candidate_quantity")
            quantity["exact"] += int(expected is not None and candidate == expected)
            quantity["different"] += int(expected is not None and candidate != expected)
            quantity["legacy_missing"] += int(expected is None)
    report = {
        "schema": "private_gold_trade_revision_audit",
        "schema_version": "1.0",
        "command_version": COMMAND_VERSION,
        "offer_lifetime_seconds": OFFER_LIFETIME_SECONDS,
        "capture": dict(sorted(capture_counts.items())),
        "embedded_capture": dict(sorted(embedded_counts.items())),
        "embedded_identity_overlap": len(embedded_overlap),
        "embedded_current_text_matches": embedded_text_matches,
        "embedded_event_time_matches": embedded_time_matches,
        "legacy_rows": len(legacy),
        "overlap_messages": len(matched),
        "match_modes": dict(sorted(match_modes.items())),
        "ambiguous_time_text_composites": ambiguous_composites,
        "shared_publication_seconds": len(set(legacy_time_counts) & set(capture_time_counts)),
        "nearest_exact_economics_by_label": {
            status: dict(sorted(values.items()))
            for status, values in sorted(nearest_economics.items())
        },
        "greedy_economic_pair_features_by_label": {
            status: dict(sorted(values.items()))
            for status, values in sorted(greedy_pair_features.items())
        },
        "greedy_economic_pair_confusion": dict(sorted(greedy_pair_confusion.items())),
        "greedy_economic_pair_quantity": dict(sorted(greedy_pair_quantity.items())),
        "legacy_edit_time_matches": {
            status: dict(sorted(values.items()))
            for status, values in sorted(legacy_edit_match.items())
        },
        "legacy_edit_time_match_features": {
            status: dict(sorted(values.items()))
            for status, values in sorted(legacy_edit_match_features.items())
        },
        "legacy_edit_transition_features": {
            status: dict(sorted(values.items()))
            for status, values in sorted(legacy_edit_transition_features.items())
        },
        "new_extractor_by_legacy_label": {
            status: dict(sorted(values.items()))
            for status, values in sorted(new_extractor_by_legacy.items())
        },
        "new_extractor_in_lifetime_confusion": dict(
            sorted(new_extractor_in_lifetime_confusion.items())
        ),
        "new_extractor_in_lifetime_quantity": dict(sorted(new_extractor_quantity.items())),
        "legacy_only_messages": len(set(legacy) - used_legacy),
        "capture_only_messages": len(set(capture) - used_capture),
        "current_text_hash_matches": current_hash_matches,
        "labels": dict(sorted(labels.items())),
        "features_by_label": {
            status: dict(sorted(values.items())) for status, values in sorted(feature_counts.items())
        },
        "resolved_comparison_messages": resolved,
        "confusion": dict(sorted(confusion.items())),
        "quantity": dict(sorted(quantity.items())),
    }
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, output)
    print(
        json.dumps(
            {
                "status": "WRITTEN",
                "overlap_messages": len(matched),
                "resolved_comparison_messages": resolved,
                "confusion": dict(sorted(confusion.items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-spool-dir", required=True)
    parser.add_argument("--legacy-reference", required=True)
    parser.add_argument("--hmac-key", required=True)
    parser.add_argument("--output-report", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    previous_umask = os.umask(0o077)
    try:
        return _run(build_parser().parse_args(argv))
    except (AuditError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, sort_keys=True))
        return 2
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
