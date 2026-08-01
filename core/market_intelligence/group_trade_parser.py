#!/usr/bin/env python3
"""Build a filtered, reply-aware offer and trade dataset from a Telegram export."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


from core.market_intelligence.group_offer_parser import (
    choose_price,
    enrich_records,
    explicit_commodity,
    explicit_quantity,
    normalize_text,
    numeric_tokens,
    offer_context,
    side_spans,
    token_has_non_price_context,
)
from core.market_intelligence.group_export import (
    TelegramHtmlParser,
    html_page_order,
    normalize_messages,
    parse_archive,
)


DEFAULT_SOURCE = Path(os.environ.get("COIN_GROUP_EXPORT_ROOT", "chat-export"))
DEFAULT_DB = Path(os.environ.get("COIN_CONVERSATION_CANDIDATE_DB", "conversation_events.candidate.sqlite3"))
DEFAULT_JSON = DEFAULT_SOURCE / "group_market_filtered.json"
DEFAULT_MANIFEST = Path(os.environ.get("COIN_CONVERSATION_IMPORT_MANIFEST", "conversation_import.candidate.json"))

EXTRACTOR_VERSION = "reply-rules-v6-coin-default-tomorrow"
MAX_REPLY_AGE_SECONDS = 2 * 60 * 60
POST_CONFIRMATION_PRICE_AUDIT_SECONDS = 15 * 60

EXPLICIT_TRADE_RE = re.compile(
    r"(?:معامله|مع\s+با|مع\s+شد|انجام\s*شد|خریدم|فروختم|برداشتم|"
    r"مال\s+من|خرید(?:م|ه)?\s*شد|فروش(?:م|ه)?\s*شد|(?<![آ-ی])مع(?![آ-ی]))"
)
CANCEL_RE = re.compile(
    r"(?:کنسل|لغو|منتفی|پاس|حذف|نشد|ندارم|تموم\s*شد|تمام\s*شد|اشتباه|عذر)"
)
QUESTION_RE = re.compile(r"[؟?]|(?:^|\s)(?:شد|داری|هست)(?:$|\s)")
CUMULATIVE_TRADE_RE = re.compile(r"(?:کلا|کلن|جمعا|مجموعا|مجموع)")
BAREKAT_RE = re.compile(r"برکت")
BLESSING_PREFIX_RE = re.compile(r"(?:خدا|الله|پر)\s*برکت")
ACK_RE = re.compile(
    r"^(?:اوکی|تایید|قبول|شد|بزن|بده|بردار|من|مال\s+من|باشه|تمام)(?:\s|$)"
)
BUY_REPLY_RE = re.compile(
    r"^(?:(?:\d{1,3})\s*(?:تا|عدد)?\s*)?(?:ب|خریدم|برداشتم)(?:\s|$)"
)
REMAINING_BUY_RE = re.compile(
    r"^(?:مابقی|باقی(?:ش)?|بقیه(?:ش)?)\s+(?:من|مال\s+من)(?:\s|$)"
)
CONDITIONAL_BUY_RE = re.compile(r"^ب\s+(?:اگه|اگر)\s+(?:هست|دارید|داری)(?:\s|$)")
QUANTITY_ONLY_RE = re.compile(r"^\d{1,3}\s*(?:تا|عدد)?$")
OFFERISH_RE = re.compile(
    r"(?:\d|ربع|نیم|امام|بهار|گرمی).*(?:خرید|فروش|(?<![آ-ی])[خف](?![آ-ی]))|"
    r"(?:خرید|فروش|(?<![آ-ی])[خف](?![آ-ی])).*(?:\d|ربع|نیم|امام|بهار|گرمی)"
)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sender_hash(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:20]


def source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if not path.is_dir():
        raise FileNotFoundError(f"Telegram export source not found: {path}")
    files = sorted(path.glob("messages*.html"), key=lambda item: html_page_order(item.name))
    if not files:
        raise RuntimeError(f"No messages*.html files found in {path}")
    for item in files:
        digest.update(item.name.encode("utf-8"))
        digest.update(b"\0")
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def parse_source(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if path.is_file():
        return parse_archive(path)
    if not path.is_dir():
        raise FileNotFoundError(f"Telegram export source not found: {path}")
    messages: list[dict[str, Any]] = []
    files = sorted(path.glob("messages*.html"), key=lambda item: html_page_order(item.name))
    if not files:
        raise RuntimeError(f"No messages*.html files found in {path}")
    for item in files:
        parser = TelegramHtmlParser(item.name)
        parser.feed(item.read_text(encoding="utf-8", errors="replace"))
        parser.close()
        if parser.current is not None:
            raise RuntimeError(f"Unclosed message block in {item}")
        messages.extend(parser.messages)
    return messages, [item.name for item in files]


def normalize_full_history(
    raw_messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    floor = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return normalize_messages(raw_messages, floor)


def classify_signal(text: str) -> str:
    normalized = normalize_text(text)
    compact = re.sub(r"[.،,؛:!؟?\-_/]+", " ", normalized)
    compact = " ".join(compact.split())
    if not compact:
        return "NEGOTIATION"
    if CANCEL_RE.search(compact):
        return "REJECT"
    if EXPLICIT_TRADE_RE.search(compact):
        return "EXPLICIT_TRADE"
    quantity, _, _ = explicit_quantity(compact)
    has_small_quantity = quantity is not None and not any(
        len(token.digits) >= 4 for token in numeric_tokens(compact)
    )
    if CONDITIONAL_BUY_RE.search(compact):
        return "BUY_ACCEPT"
    if QUESTION_RE.search(normalized):
        return "QUANTITY_QUESTION" if has_small_quantity else "QUESTION"
    if BAREKAT_RE.search(compact):
        return "BLESSING_ACK" if BLESSING_PREFIX_RE.search(compact) else "BAREKAT_ACCEPT"
    if BUY_REPLY_RE.search(compact):
        return "BUY_ACCEPT"
    if REMAINING_BUY_RE.search(compact):
        return "BUY_ACCEPT"
    if ACK_RE.search(compact):
        return "ACCEPT"
    if QUANTITY_ONLY_RE.fullmatch(compact.replace(" ", "")):
        return "QUANTITY_REQUEST"
    if has_small_quantity:
        return "QUANTITY_REQUEST"
    return "NEGOTIATION"


def reply_quantity(text: str, offered_quantity: int | None = None) -> int | None:
    normalized = normalize_text(text)
    quantity, _, _ = explicit_quantity(normalized)
    if quantity is not None:
        return int(quantity)
    candidates = [
        int(token.digits)
        for token in numeric_tokens(normalized)
        if len(token.digits) <= 3 and 1 <= int(token.digits) <= 100
    ]
    if offered_quantity is not None:
        bounded = [value for value in candidates if value <= offered_quantity]
        if bounded:
            return bounded[0]
    return candidates[0] if candidates else None


def reply_price_adjustment(
    text: str, offer: dict[str, Any]
) -> dict[str, Any] | None:
    """Extract a price stated inside a negotiation/confirmation reply.

    Three-digit values in this position are treated as price tails only when they
    are not an explicitly marked quantity and remain near the linked offer.
    """
    normalized = normalize_text(text)
    _, quantity_spans, _ = explicit_quantity(normalized)
    tokens = numeric_tokens(normalized)
    choice = choose_price(
        normalized,
        tokens,
        str(offer["commodity"]),
        quantity_spans,
        side_spans(normalized),
        full_anchor=float(offer["price"]),
    )
    occupied = quantity_spans
    candidates = [
        token
        for token in tokens
        if not any(token.start < end and token.end > start for start, end in occupied)
        and len(token.digits) == 3
        and int(token.digits) > 100
        and int(token.digits) % 50 == 0
        and not token_has_non_price_context(normalized, token)
    ]
    tail_result: dict[str, Any] | None = None
    if len(candidates) == 1:
        token = candidates[0]
        anchor = int(offer["price"])
        base = (anchor // 1000) * 1000
        reconstructed = min(
            (
                base - 1000 + int(token.digits),
                base + int(token.digits),
                base + 1000 + int(token.digits),
            ),
            key=lambda value: abs(value - anchor),
        )
        if abs(reconstructed - anchor) <= 3000:
            tail_result = {
                "price": reconstructed,
                "price_raw": token.raw,
                "price_method": "reply_contextual_tail",
            }
    if choice is not None and tail_result is not None:
        anchor = int(offer["price"])
        if abs(int(choice.value) - anchor) <= abs(int(tail_result["price"]) - anchor):
            tail_result = None
    if choice is not None and tail_result is None:
        return {
            "price": int(choice.value),
            "price_raw": choice.token.raw,
            "price_method": choice.method,
        }
    return tail_result


def reply_chain(
    message_id: int, by_id: dict[int, dict[str, Any]], max_depth: int = 12
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    current_id: int | None = message_id
    while current_id is not None and current_id in by_id and len(result) < max_depth:
        if current_id in seen:
            break
        seen.add(current_id)
        current = by_id[current_id]
        result.append(current)
        reply_id = current.get("reply_to_message_id")
        current_id = int(reply_id) if reply_id is not None else None
    return result


def best_offer(offers: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    values = list(offers)
    return max(values, key=lambda row: float(row["confidence"])) if values else None


def standalone_trade_fields(text: str) -> dict[str, Any] | None:
    normalized = normalize_text(text)
    commodity = explicit_commodity(normalized)
    quantity, quantity_spans, quantity_method = explicit_quantity(normalized)
    choice = choose_price(
        normalized,
        numeric_tokens(normalized),
        commodity,
        quantity_spans,
        side_spans(normalized),
    )
    if choice is None:
        return None
    commodity = commodity or choice.inferred_commodity
    if commodity is None:
        return None
    side, settlement, trade_form = offer_context(normalized)
    return {
        "commodity": commodity,
        "price": int(choice.value),
        "price_raw": choice.token.raw,
        "price_method": choice.method,
        "quantity": int(quantity) if quantity is not None else None,
        "quantity_method": quantity_method,
        "side": side,
        "settlement": settlement,
        "trade_form": trade_form,
    }


def analyze_reply_trades(
    messages: list[dict[str, Any]],
    offers_by_message: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    by_id = {int(row["message_id"]): row for row in messages}
    children: dict[int, list[int]] = defaultdict(list)
    for row in messages:
        reply_id = row.get("reply_to_message_id")
        if reply_id is not None and int(reply_id) in by_id:
            children[int(reply_id)].append(int(row["message_id"]))

    root_cache: dict[int, int | None] = {}

    def root_offer_id(message_id: int) -> int | None:
        if message_id in root_cache:
            return root_cache[message_id]
        chain = reply_chain(message_id, by_id)
        root = next(
            (
                int(item["message_id"])
                for item in chain
                if offers_by_message.get(int(item["message_id"]))
            ),
            None,
        )
        root_cache[message_id] = root
        return root

    resolved_offer_cache: dict[tuple[int, str | None], dict[str, Any] | None] = {}

    def resolved_offer(
        message_id: int, commodity_hint: str | None = None
    ) -> dict[str, Any] | None:
        """Use the nearest offer update and inherit fields it omitted."""
        cache_key = (message_id, commodity_hint)
        if cache_key in resolved_offer_cache:
            value = resolved_offer_cache[cache_key]
            return dict(value) if value is not None else None
        choices = list(offers_by_message.get(message_id, []))
        if commodity_hint is not None:
            choices = [row for row in choices if row.get("commodity") == commodity_hint]
        nearest = best_offer(choices)
        if nearest is None:
            resolved_offer_cache[cache_key] = None
            return None
        result = dict(nearest)
        owner = sender_hash(by_id[message_id].get("from_name"))
        for ancestor in reply_chain(message_id, by_id)[1:]:
            if sender_hash(ancestor.get("from_name")) != owner:
                continue
            previous = best_offer(offers_by_message.get(int(ancestor["message_id"]), []))
            if previous is None or previous.get("commodity") != result.get("commodity"):
                continue
            for field in ("quantity", "quantity_method"):
                if result.get(field) is None and previous.get(field) is not None:
                    result[field] = previous[field]
            if result.get("side") in {None, "UNKNOWN"}:
                result["side"] = previous.get("side")
            if result.get("settlement") in {None, "UNKNOWN"}:
                result["settlement"] = previous.get("settlement")
            if result.get("trade_form") in {None, "UNKNOWN"}:
                result["trade_form"] = previous.get("trade_form")
        resolved_offer_cache[cache_key] = dict(result)
        return result

    def select_root_offer(
        message_id: int, text: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        choices = list(offers_by_message.get(message_id, []))
        if not choices:
            return None, "LINKED_OFFER_MISSING"
        if len(choices) == 1:
            return resolved_offer(message_id), None
        hint = explicit_commodity(normalize_text(text))
        if hint is not None:
            matching = [row for row in choices if row.get("commodity") == hint]
            if len(matching) == 1:
                return resolved_offer(message_id, hint), None
        price_matches: list[tuple[int, dict[str, Any]]] = []
        for choice in choices:
            adjustment = reply_price_adjustment(text, choice)
            if adjustment is not None:
                price_matches.append(
                    (abs(int(adjustment["price"]) - int(choice["price"])), choice)
                )
        price_matches.sort(key=lambda item: item[0])
        if len(price_matches) == 1 or (
            len(price_matches) > 1 and price_matches[0][0] + 100 < price_matches[1][0]
        ):
            return dict(price_matches[0][1]), None
        reason = (
            "MULTI_OFFER_BUNDLE_REQUIRES_SPLIT"
            if re.search(r"(?:با\s*هم|هردو|هر\s*دو|جفت)", normalize_text(text))
            else "AMBIGUOUS_MULTI_OFFER_REQUEST"
        )
        return None, reason

    branch_by_message: dict[int, int] = {}
    requests: dict[int, dict[str, Any]] = {}
    branch_messages: dict[int, list[int]] = defaultdict(list)
    relevant_ids: set[int] = set(offers_by_message)
    review_items: list[dict[str, Any]] = []

    for row in messages:
        message_id = int(row["message_id"])
        root_id = root_offer_id(message_id)
        if root_id is None or root_id == message_id:
            continue
        root_message = by_id[root_id]
        age = (parse_time(row["date_utc"]) - parse_time(root_message["date_utc"])).total_seconds()
        if age < 0 or age > MAX_REPLY_AGE_SECONDS:
            continue
        chain = reply_chain(message_id, by_id)
        relevant_ids.update(int(item["message_id"]) for item in chain)
        parent_id = row.get("reply_to_message_id")
        signal = classify_signal(str(row.get("text") or ""))
        root_sender = sender_hash(root_message.get("from_name"))
        current_sender = sender_hash(row.get("from_name"))
        inherited_request = (
            branch_by_message.get(int(parent_id)) if parent_id is not None else None
        )
        starts_new_fill = False
        if inherited_request is not None:
            inherited = requests.get(inherited_request)
            parent_sender = (
                sender_hash(by_id[int(parent_id)].get("from_name"))
                if parent_id is not None and int(parent_id) in by_id
                else None
            )
            starts_new_fill = bool(
                inherited is not None
                and current_sender
                and current_sender
                not in {
                    root_sender,
                    inherited.get("request_sender_hash"),
                }
                and parent_sender == root_sender
                and signal
                in {
                    "EXPLICIT_TRADE",
                    "BAREKAT_ACCEPT",
                    "BUY_ACCEPT",
                    "ACCEPT",
                    "QUANTITY_REQUEST",
                    "QUANTITY_QUESTION",
                }
            )
            if not starts_new_fill:
                branch_by_message[message_id] = inherited_request
                branch_messages[inherited_request].append(message_id)
                continue
        ancestors_after_offer = [
            item for item in chain[1:] if int(item["message_id"]) != root_id
        ]
        follows_explicit_trade = any(
            classify_signal(str(item.get("text") or "")) == "EXPLICIT_TRADE"
            for item in ancestors_after_offer
        )
        initial_signal = signal in {
            "EXPLICIT_TRADE",
            "BAREKAT_ACCEPT",
            "BUY_ACCEPT",
            "ACCEPT",
            "QUANTITY_REQUEST",
            "QUANTITY_QUESTION",
        }
        if signal == "BAREKAT_ACCEPT" and follows_explicit_trade:
            initial_signal = False
        if initial_signal and current_sender and current_sender != root_sender:
            offer, selection_error = select_root_offer(
                root_id, str(row.get("text") or "")
            )
            if offer is None:
                if selection_error is not None:
                    review_items.append(
                        {
                            "message_id": message_id,
                            "reason": selection_error,
                            "confidence": 0.58,
                            "context_message_ids": [
                                int(item["message_id"]) for item in chain
                            ],
                        }
                    )
                continue
            quantity = reply_quantity(str(row.get("text") or ""), offer.get("quantity"))
            quantity_source = "reply_explicit" if quantity is not None else None
            if (
                signal == "EXPLICIT_TRADE"
                and int(parent_id) != root_id
                and quantity is None
                and reply_price_adjustment(str(row.get("text") or ""), offer) is None
            ):
                review_items.append(
                    {
                        "message_id": message_id,
                        "reason": "DESCENDANT_EXPLICIT_LANGUAGE_WITHOUT_PRICE_OR_QUANTITY",
                        "confidence": 0.60,
                        "context_message_ids": [int(item["message_id"]) for item in chain],
                    }
                )
                continue
            if quantity is None:
                for ancestor in ancestors_after_offer:
                    if sender_hash(ancestor.get("from_name")) != root_sender:
                        continue
                    quantity = reply_quantity(
                        str(ancestor.get("text") or ""), offer.get("quantity")
                    )
                    if quantity is not None:
                        quantity_source = "owner_update"
                        break
            requests[message_id] = {
                "request_message_id": message_id,
                "offer_message_id": root_id,
                "signal": signal,
                "quantity": quantity,
                "quantity_source": quantity_source,
                "selected_offer": offer,
                "request_sender_hash": current_sender,
                "offer_sender_hash": root_sender,
                "nested_fill": starts_new_fill,
            }
            branch_by_message[message_id] = message_id
            branch_messages[message_id].append(message_id)

    accepted_trades: list[dict[str, Any]] = []
    request_rows: list[dict[str, Any]] = []
    confirmation_ids: set[int] = set()

    for request_id, request in requests.items():
        request_message = by_id[request_id]
        offer_message = by_id[int(request["offer_message_id"])]
        offer = dict(request["selected_offer"])
        if offer is None:
            continue
        descendants = [by_id[item] for item in branch_messages[request_id] if item != request_id]
        descendants.sort(key=lambda row: (row["date_utc"], int(row["message_id"])))
        rejected = next(
            (
                item
                for item in descendants
                if classify_signal(str(item.get("text") or "")) == "REJECT"
                and sender_hash(item.get("from_name")) == request["offer_sender_hash"]
            ),
            None,
        )

        confirmation: dict[str, Any] | None = None
        confirmation_type: str | None = None
        confidence = 0.0
        evidence = ["REPLY_CHAIN_LINKED_OFFER", f"REQUEST_{request['signal']}"]
        if rejected is None:
            if request["signal"] == "EXPLICIT_TRADE":
                confirmation = request_message
                confirmation_type = "EXPLICIT_TRADE_REPLY"
                confidence = 0.96
            elif request["signal"] == "BAREKAT_ACCEPT":
                confirmation = request_message
                confirmation_type = "DIRECT_BAREKAT_ACCEPTANCE"
                confidence = 0.90 if request["quantity"] is not None else 0.87

            for item in descendants:
                signal = classify_signal(str(item.get("text") or ""))
                item_sender = sender_hash(item.get("from_name"))
                if signal == "REJECT":
                    continue
                if item_sender == request["offer_sender_hash"] and signal in {
                    "EXPLICIT_TRADE",
                    "BAREKAT_ACCEPT",
                    "BLESSING_ACK",
                    "BUY_ACCEPT",
                    "ACCEPT",
                }:
                    confirmation = item
                    confirmation_type = "RECIPROCAL_OFFERER_CONFIRMATION"
                    confidence = max(confidence, 0.98)
                    evidence.append("OFFERER_REPLIED_WITH_CONFIRMATION")
                    break
                if item_sender == request["request_sender_hash"] and signal == "EXPLICIT_TRADE":
                    confirmation = item
                    confirmation_type = "COUNTERPARTY_EXPLICIT_TRADE"
                    confidence = max(confidence, 0.95)
                    evidence.append("COUNTERPARTY_EXPLICITLY_CONFIRMED_TRADE")
                    break

        price_cutoff = parse_time(confirmation["date_utc"]) if confirmation is not None else None
        negotiation_rows = [request_message, *descendants]
        price_rows = [
            item
            for item in negotiation_rows
            if price_cutoff is None or parse_time(item["date_utc"]) <= price_cutoff
        ]
        price = int(offer["price"])
        price_raw = offer.get("price_raw")
        price_method = offer.get("price_method") or "linked_offer"
        for item in price_rows:
            adjustment = reply_price_adjustment(str(item.get("text") or ""), offer)
            if adjustment is not None:
                price = int(adjustment["price"])
                price_raw = adjustment["price_raw"]
                price_method = adjustment["price_method"]

        owner_price_clarification: tuple[dict[str, Any], dict[str, Any]] | None = None
        if confirmation is not None:
            confirmation_time = parse_time(confirmation["date_utc"])
            for item in descendants:
                item_time = parse_time(item["date_utc"])
                age_seconds = (item_time - confirmation_time).total_seconds()
                if not 0 < age_seconds <= POST_CONFIRMATION_PRICE_AUDIT_SECONDS:
                    continue
                if sender_hash(item.get("from_name")) != request["offer_sender_hash"]:
                    continue
                item_text = str(item.get("text") or "")
                normalized_item = normalize_text(item_text)
                if not (
                    BAREKAT_RE.search(normalized_item)
                    or EXPLICIT_TRADE_RE.search(normalized_item)
                ):
                    continue
                adjustment = reply_price_adjustment(item_text, offer)
                if (
                    adjustment is not None
                    and adjustment["price_method"] == "full"
                    and int(adjustment["price"]) != int(price)
                ):
                    owner_price_clarification = (item, adjustment)
                    break

        training_eligible = True
        if owner_price_clarification is not None:
            clarification_message, clarification = owner_price_clarification
            price = int(clarification["price"])
            price_raw = clarification["price_raw"]
            price_method = "post_confirmation_owner_explicit_price_unreviewed"
            training_eligible = False
            evidence.extend(
                [
                    "POST_CONFIRMATION_OWNER_PRICE_CLARIFICATION",
                    "PRICE_LABEL_HELD_FOR_REVIEW",
                ]
            )

        requester_quantities: list[tuple[datetime, int, int]] = []
        for item in negotiation_rows:
            if sender_hash(item.get("from_name")) != request["request_sender_hash"]:
                continue
            value = reply_quantity(str(item.get("text") or ""), offer.get("quantity"))
            if value is not None:
                requester_quantities.append(
                    (parse_time(item["date_utc"]), int(item["message_id"]), int(value))
                )
        confirmation_time = (
            parse_time(confirmation["date_utc"])
            if confirmation is not None
            else None
        )
        confirmed_requester_quantities = [
            item
            for item in requester_quantities
            if confirmation_time is None or item[0] <= confirmation_time
        ]
        latest_requester_quantity = (
            sorted(confirmed_requester_quantities)[-1]
            if confirmed_requester_quantities
            else None
        )
        latest_any_requester_quantity = (
            sorted(requester_quantities)[-1]
            if requester_quantities
            else None
        )
        owner_quantity_clarifications: list[tuple[datetime, int, int]] = []
        if confirmation_time is not None:
            for item in descendants:
                item_time = parse_time(item["date_utc"])
                age_seconds = (
                    item_time - confirmation_time
                ).total_seconds()
                # The offerer may state the accepted quantity either while
                # negotiating before the final bare "ب", or shortly after an
                # initial confirmation to reject a requested amendment.
                if age_seconds > POST_CONFIRMATION_PRICE_AUDIT_SECONDS:
                    continue
                if (
                    sender_hash(item.get("from_name"))
                    != request["offer_sender_hash"]
                ):
                    continue
                value = reply_quantity(
                    str(item.get("text") or ""),
                    offer.get("quantity"),
                )
                if value is not None:
                    owner_quantity_clarifications.append(
                        (item_time, int(item["message_id"]), int(value))
                    )
        latest_owner_quantity = (
            sorted(owner_quantity_clarifications)[-1]
            if owner_quantity_clarifications
            else None
        )
        confirmation_quantity = (
            reply_quantity(str(confirmation.get("text") or ""), offer.get("quantity"))
            if confirmation is not None
            else None
        )
        quantity = (
            latest_owner_quantity[2]
            if latest_owner_quantity is not None
            else latest_requester_quantity[2]
            if latest_requester_quantity is not None
            else confirmation_quantity or request["quantity"] or offer.get("quantity")
        )
        if latest_owner_quantity is not None:
            evidence.append("OFFERER_EXPLICIT_QUANTITY_CLARIFICATION")
        if request.get("nested_fill"):
            evidence.append("NESTED_THIRD_PARTY_FILL_AFTER_OWNER_RESPONSE")
        offered_quantity = offer.get("quantity")
        reason: str | None = None
        if rejected is not None:
            reason = "OFFERER_REJECTED_OR_CANCELLED_BRANCH"
            evidence.append("OFFERER_REJECTION")
            confidence = 0.20
        elif confirmation is None:
            reason = "REQUEST_WITHOUT_RELIABLE_CONFIRMATION"
            confidence = 0.72 if request["signal"] == "BUY_ACCEPT" else 0.62
        elif (
            request.get("nested_fill")
            and request.get("quantity") is None
            and confirmation_quantity is None
            and latest_owner_quantity is None
        ):
            reason = "NESTED_FILL_WITHOUT_EXPLICIT_QUANTITY"
            evidence.append("NESTED_THIRD_PARTY_FILL_REQUIRES_EXPLICIT_QUANTITY")
            confidence = min(confidence, 0.72)
        elif (
            confirmation_quantity is not None
            and latest_any_requester_quantity is not None
            and latest_any_requester_quantity[0] > parse_time(confirmation["date_utc"])
            and latest_any_requester_quantity[2] != confirmation_quantity
            and latest_owner_quantity is None
        ):
            reason = "CONFLICTING_QUANTITY_AFTER_CONFIRMATION"
            evidence.append("QUANTITY_CONFLICT")
            confidence = min(confidence, 0.64)
        elif (
            quantity is not None
            and offered_quantity is not None
            and int(quantity) > int(offered_quantity)
        ):
            reason = "TRADE_QUANTITY_EXCEEDS_OFFER_QUANTITY"
            evidence.append("QUANTITY_CONFLICT")
            confidence = min(confidence, 0.64)

        event_message = confirmation or request_message
        context_message_ids = [
            int(item["message_id"])
            for item in reply_chain(event_message["message_id"], by_id)
        ]
        if owner_price_clarification is not None:
            context_message_ids.append(
                int(owner_price_clarification[0]["message_id"])
            )
        if latest_owner_quantity is not None:
            context_message_ids.append(int(latest_owner_quantity[1]))
        context_message_ids = list(dict.fromkeys(context_message_ids))
        trade = {
            "offer_message_id": int(request["offer_message_id"]),
            "request_message_id": request_id,
            "confirmation_message_id": (
                int(confirmation["message_id"]) if confirmation is not None else None
            ),
            "event_time_utc": event_message["date_utc"],
            "commodity": offer["commodity"],
            "price": price,
            "price_raw": price_raw,
            "price_method": price_method,
            "quantity": int(quantity) if quantity is not None else None,
            "quantity_method": (
                "offerer_explicit_clarification"
                if latest_owner_quantity is not None
                else "reply_explicit"
                if latest_requester_quantity is not None or confirmation_quantity is not None
                else request.get("quantity_source") or "linked_offer"
            ),
            "side": offer.get("side") or "UNKNOWN",
            "settlement": offer.get("settlement") or "CASH",
            "trade_form": offer.get("trade_form") or "PHYSICAL",
            "confidence": round(confidence, 2),
            "confirmation_type": confirmation_type,
            "status": "ACCEPTED" if confirmation is not None and reason is None and confidence >= 0.85 else "REVIEW",
            "training_eligible": training_eligible,
            "reason": reason,
            "evidence": list(dict.fromkeys(evidence)),
            "context_message_ids": context_message_ids,
        }
        request_rows.append(
            {
                **request,
                "status": trade["status"],
                "confirmation_message_id": trade["confirmation_message_id"],
                "confidence": trade["confidence"],
            }
        )
        if trade["status"] == "ACCEPTED":
            accepted_trades.append(trade)
            if trade["confirmation_message_id"] is not None:
                confirmation_ids.add(int(trade["confirmation_message_id"]))
            if owner_price_clarification is not None:
                review_items.append(
                    {
                        "message_id": int(
                            owner_price_clarification[0]["message_id"]
                        ),
                        "reason": (
                            "POST_CONFIRMATION_OWNER_PRICE_"
                            "CLARIFICATION_REQUIRES_REVIEW"
                        ),
                        "confidence": 0.88,
                        "context_message_ids": context_message_ids,
                    }
                )
        else:
            review_items.append(
                {
                    "message_id": request_id,
                    "reason": reason or "AMBIGUOUS_TRADE_BRANCH",
                    "confidence": trade["confidence"],
                    "context_message_ids": trade["context_message_ids"],
                }
            )

    # Many sellers/buyers announce a completed deal by replying to their own
    # offer (for example: "معامله با ...").  These are high-value confirmations
    # even when the counterparty's acceptance was not itself a reply.
    for row in messages:
        message_id = int(row["message_id"])
        root_id = root_offer_id(message_id)
        if (
            root_id is None
            or root_id == message_id
            or message_id in branch_by_message
            or classify_signal(str(row.get("text") or "")) != "EXPLICIT_TRADE"
        ):
            continue
        root_message = by_id[root_id]
        if sender_hash(row.get("from_name")) != sender_hash(root_message.get("from_name")):
            continue
        chain = reply_chain(message_id, by_id)
        offer, selection_error = select_root_offer(
            root_id, str(row.get("text") or "")
        )
        if offer is None:
            if selection_error is not None:
                review_items.append(
                    {
                        "message_id": message_id,
                        "reason": selection_error,
                        "confidence": 0.60,
                        "context_message_ids": [
                            int(item["message_id"]) for item in chain
                        ],
                    }
                )
            continue
        earlier_owner_trade = any(
            sender_hash(ancestor.get("from_name")) == sender_hash(root_message.get("from_name"))
            and classify_signal(str(ancestor.get("text") or "")) == "EXPLICIT_TRADE"
            for ancestor in chain[1:]
            if int(ancestor["message_id"]) != root_id
        )
        adjustment = reply_price_adjustment(str(row.get("text") or ""), offer)
        quantity = reply_quantity(str(row.get("text") or ""), offer.get("quantity"))
        if quantity is None:
            for ancestor in reply_chain(message_id, by_id)[1:]:
                if int(ancestor["message_id"]) == root_id:
                    break
                if sender_hash(ancestor.get("from_name")) != sender_hash(root_message.get("from_name")):
                    continue
                quantity = reply_quantity(
                    str(ancestor.get("text") or ""), offer.get("quantity")
                )
                if quantity is not None:
                    break
        quantity = quantity or offer.get("quantity")
        if CUMULATIVE_TRADE_RE.search(normalize_text(str(row.get("text") or ""))):
            trade_side, _, _ = offer_context(normalize_text(str(row.get("text") or "")))
            trade = {
                "offer_message_id": None,
                "request_message_id": None,
                "confirmation_message_id": message_id,
                "event_time_utc": row["date_utc"],
                "commodity": offer["commodity"],
                "price": int(adjustment["price"] if adjustment is not None else offer["price"]),
                "price_raw": (
                    adjustment["price_raw"] if adjustment is not None else offer.get("price_raw")
                ),
                "price_method": (
                    adjustment["price_method"]
                    if adjustment is not None
                    else "cumulative_context_offer"
                ),
                "quantity": int(quantity) if quantity is not None else None,
                "quantity_method": "confirmation_explicit",
                "side": trade_side if trade_side != "UNKNOWN" else offer.get("side") or "UNKNOWN",
                "settlement": offer.get("settlement") or "CASH",
                "trade_form": offer.get("trade_form") or "PHYSICAL",
                "confidence": 0.91,
                "confirmation_type": "CUMULATIVE_TRADE_ANNOUNCEMENT",
                "status": "ACCEPTED",
                "reason": None,
                "evidence": [
                    "EXPLICIT_CUMULATIVE_TRADE_LANGUAGE",
                    "CONTEXT_PRICE_FROM_LINKED_OFFER",
                    "NOT_CONSTRAINED_BY_SINGLE_OFFER_QUANTITY",
                ],
                "context_message_ids": [int(item["message_id"]) for item in chain],
            }
            accepted_trades.append(trade)
            confirmation_ids.add(message_id)
            relevant_ids.update(trade["context_message_ids"])
            continue
        if earlier_owner_trade or (
            quantity is not None
            and offer.get("quantity") is not None
            and int(quantity) > int(offer["quantity"])
        ):
            context_ids = [int(item["message_id"]) for item in chain]
            relevant_ids.update(context_ids)
            review_items.append(
                {
                    "message_id": message_id,
                    "reason": (
                        "POSSIBLE_CUMULATIVE_OR_DUPLICATE_OWNER_TRADE_ANNOUNCEMENT"
                        if earlier_owner_trade
                        else "OWNER_TRADE_QUANTITY_EXCEEDS_LINKED_OFFER"
                    ),
                    "confidence": 0.68,
                    "context_message_ids": context_ids,
                }
            )
            continue
        trade = {
            "offer_message_id": root_id,
            "request_message_id": None,
            "confirmation_message_id": message_id,
            "event_time_utc": row["date_utc"],
            "commodity": offer["commodity"],
            "price": int(adjustment["price"] if adjustment is not None else offer["price"]),
            "price_raw": (
                adjustment["price_raw"] if adjustment is not None else offer.get("price_raw")
            ),
            "price_method": (
                adjustment["price_method"]
                if adjustment is not None
                else offer.get("price_method") or "linked_offer"
            ),
            "quantity": int(quantity) if quantity is not None else None,
            "quantity_method": (
                "confirmation_explicit"
                if reply_quantity(str(row.get("text") or ""), offer.get("quantity")) is not None
                else "linked_offer"
            ),
            "side": offer.get("side") or "UNKNOWN",
            "settlement": offer.get("settlement") or "CASH",
            "trade_form": offer.get("trade_form") or "PHYSICAL",
            "confidence": 0.97,
            "confirmation_type": "OWNER_EXPLICIT_REPLY_TRADE",
            "status": "ACCEPTED",
            "reason": None,
            "evidence": ["EXPLICIT_TRADE_LANGUAGE", "OWNER_REPLIED_TO_LINKED_OFFER"],
            "context_message_ids": [int(item["message_id"]) for item in chain],
        }
        accepted_trades.append(trade)
        confirmation_ids.add(message_id)
        relevant_ids.update(trade["context_message_ids"])

    offer_timeline = sorted(
        (
            parse_time(by_id[message_id]["date_utc"]),
            message_id,
            best_offer(offers),
        )
        for message_id, offers in offers_by_message.items()
        if best_offer(offers) is not None
    )
    branch_member_ids = set(branch_by_message)
    for row in messages:
        message_id = int(row["message_id"])
        text = str(row.get("text") or "")
        if (
            message_id in branch_member_ids
            or message_id in confirmation_ids
            or not EXPLICIT_TRADE_RE.search(normalize_text(text))
            or root_offer_id(message_id) is not None
        ):
            continue
        fields = standalone_trade_fields(text)
        if fields is None and CUMULATIVE_TRADE_RE.search(normalize_text(text)):
            event_time = parse_time(row["date_utc"])
            same_sender = sender_hash(row.get("from_name"))
            nearby_owner_offer: tuple[float, int, dict[str, Any]] | None = None
            for offer_time, offer_message_id, offer in reversed(offer_timeline):
                age = (event_time - offer_time).total_seconds()
                if age < 0:
                    continue
                if age > 3600:
                    break
                if sender_hash(by_id[offer_message_id].get("from_name")) == same_sender:
                    nearby_owner_offer = (age, offer_message_id, offer)
                    break
            quantity = reply_quantity(text)
            if nearby_owner_offer is not None and quantity is not None:
                _, context_offer_id, context_offer = nearby_owner_offer
                trade_side, _, _ = offer_context(normalize_text(text))
                trade = {
                    "offer_message_id": None,
                    "request_message_id": None,
                    "confirmation_message_id": message_id,
                    "event_time_utc": row["date_utc"],
                    "commodity": context_offer["commodity"],
                    "price": int(context_offer["price"]),
                    "price_raw": context_offer.get("price_raw"),
                    "price_method": "cumulative_context_offer",
                    "quantity": int(quantity),
                    "quantity_method": "confirmation_explicit",
                    "side": trade_side if trade_side != "UNKNOWN" else context_offer.get("side") or "UNKNOWN",
                    "settlement": context_offer.get("settlement") or "CASH",
                    "trade_form": context_offer.get("trade_form") or "PHYSICAL",
                    "confidence": 0.88,
                    "confirmation_type": "CUMULATIVE_TRADE_ANNOUNCEMENT",
                    "status": "ACCEPTED",
                    "reason": None,
                    "evidence": [
                        "EXPLICIT_CUMULATIVE_TRADE_LANGUAGE",
                        "CONTEXT_PRICE_FROM_NEAREST_OWNER_OFFER",
                        "NOT_CONSTRAINED_BY_SINGLE_OFFER_QUANTITY",
                    ],
                    "context_message_ids": [message_id, context_offer_id],
                }
                accepted_trades.append(trade)
                confirmation_ids.add(message_id)
                relevant_ids.update(trade["context_message_ids"])
                continue
        if fields is None:
            review_items.append(
                {
                    "message_id": message_id,
                    "reason": "EXPLICIT_TRADE_WITHOUT_EXTRACTABLE_FIELDS",
                    "confidence": 0.55,
                    "context_message_ids": [message_id],
                }
            )
            relevant_ids.add(message_id)
            continue

        event_time = parse_time(row["date_utc"])
        same_sender = sender_hash(row.get("from_name"))
        nearby: list[tuple[float, int, dict[str, Any]]] = []
        for offer_time, offer_message_id, offer in reversed(offer_timeline):
            age = (event_time - offer_time).total_seconds()
            if age < 0:
                continue
            if age > 3600:
                break
            if offer is None or offer["commodity"] != fields["commodity"]:
                continue
            price_matches = abs(int(offer["price"]) - int(fields["price"])) <= 500
            sender_matches = sender_hash(by_id[offer_message_id].get("from_name")) == same_sender
            if price_matches or sender_matches:
                nearby.append((age, offer_message_id, offer))
        linked = sorted(nearby, key=lambda item: (not (abs(int(item[2]["price"]) - int(fields["price"])) <= 500), item[0]))[0] if nearby else None
        if (
            linked is not None
            and fields["quantity"] is not None
            and linked[2].get("quantity") is not None
            and int(fields["quantity"]) > int(linked[2]["quantity"])
        ):
            linked = None
        if linked is not None:
            _, linked_id, linked_offer = linked
            fields.update(
                {
                    "side": linked_offer.get("side") or fields["side"],
                    "settlement": linked_offer.get("settlement") or fields["settlement"],
                    "trade_form": linked_offer.get("trade_form") or fields["trade_form"],
                }
            )
        else:
            linked_id = None
        confidence = 0.93 if linked is not None else (0.89 if fields["quantity"] is not None else 0.83)
        trade = {
            "offer_message_id": linked_id,
            "request_message_id": None,
            "confirmation_message_id": message_id,
            "event_time_utc": row["date_utc"],
            "commodity": fields["commodity"],
            "price": fields["price"],
            "price_raw": fields["price_raw"],
            "price_method": fields["price_method"],
            "quantity": fields["quantity"],
            "quantity_method": fields["quantity_method"],
            "side": fields["side"],
            "settlement": fields["settlement"],
            "trade_form": fields["trade_form"],
            "confidence": confidence,
            "confirmation_type": "EXPLICIT_STANDALONE_TRADE_ANNOUNCEMENT",
            "status": "ACCEPTED" if confidence >= 0.85 else "REVIEW",
            "reason": None if confidence >= 0.85 else "STANDALONE_TRADE_MISSING_QUANTITY",
            "evidence": ["EXPLICIT_TRADE_LANGUAGE", "PRICE_AND_COMMODITY_EXTRACTED"],
            "context_message_ids": [message_id] + ([linked_id] if linked_id is not None else []),
        }
        relevant_ids.update(trade["context_message_ids"])
        if trade["status"] == "ACCEPTED":
            accepted_trades.append(trade)
            confirmation_ids.add(message_id)
        else:
            review_items.append(
                {
                    "message_id": message_id,
                    "reason": trade["reason"],
                    "confidence": trade["confidence"],
                    "context_message_ids": trade["context_message_ids"],
                }
            )

    # One participant may publish several lots and later confirm several fills.
    # Therefore the quantity in one message is not a hard cap on all subsequent
    # confirmed trades.  We retain every strongly confirmed fill.  If their sum
    # exceeds one message's quantity, the records remain confirmed but are kept
    # out of training unless a later cumulative announcement corroborates them.
    grouped_trades: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for trade in accepted_trades:
        trade.setdefault("training_eligible", trade["confirmation_type"] != "CUMULATIVE_TRADE_ANNOUNCEMENT")
        trade.setdefault("is_aggregate", trade["confirmation_type"] == "CUMULATIVE_TRADE_ANNOUNCEMENT")
        trade.setdefault("reported_quantity", trade.get("quantity"))
        if trade["offer_message_id"] is not None:
            grouped_trades[(int(trade["offer_message_id"]), str(trade["commodity"]))].append(trade)

    cumulative_by_owner_commodity: dict[tuple[str | None, str], list[dict[str, Any]]] = defaultdict(list)
    for trade in accepted_trades:
        if not trade["is_aggregate"]:
            continue
        confirmation_id = int(trade["confirmation_message_id"])
        owner = sender_hash(by_id[confirmation_id].get("from_name"))
        cumulative_by_owner_commodity[(owner, str(trade["commodity"]))].append(trade)

    for (offer_id, commodity), trades in grouped_trades.items():
        candidates = [
            row
            for row in offers_by_message.get(offer_id, [])
            if row.get("commodity") == commodity
        ]
        linked = best_offer(candidates)
        offered_quantity = linked.get("quantity") if linked is not None else None
        if offered_quantity is None:
            linked_resolved = resolved_offer(offer_id)
            if linked_resolved is not None and linked_resolved.get("commodity") == commodity:
                offered_quantity = linked_resolved.get("quantity")
        if offered_quantity is None:
            continue
        quantities = [int(row["quantity"]) for row in trades if row.get("quantity") is not None]
        if not quantities or sum(quantities) <= int(offered_quantity):
            continue
        owner = sender_hash(by_id[offer_id].get("from_name"))
        latest_trade_time = max(parse_time(row["event_time_utc"]) for row in trades)
        corroborating = next(
            (
                row
                for row in cumulative_by_owner_commodity.get((owner, commodity), [])
                if 0
                <= (parse_time(row["event_time_utc"]) - latest_trade_time).total_seconds()
                <= MAX_REPLY_AGE_SECONDS
                and row.get("reported_quantity") is not None
                and int(row["reported_quantity"]) >= sum(quantities)
            ),
            None,
        )
        for trade in trades:
            if corroborating is not None:
                trade["evidence"] = list(
                    dict.fromkeys(
                        [
                            *trade["evidence"],
                            "MULTIPLE_CONFIRMED_FILLS",
                            "CORROBORATED_BY_CUMULATIVE_ANNOUNCEMENT",
                        ]
                    )
                )
                trade["training_eligible"] = True
            else:
                trade["evidence"] = list(
                    dict.fromkeys(
                        [*trade["evidence"], "MULTIPLE_FILLS_EXCEED_SINGLE_MESSAGE_QUANTITY"]
                    )
                )
                trade["training_eligible"] = False

    suspicious_ids: set[int] = set()
    for row in messages:
        message_id = int(row["message_id"])
        if message_id in relevant_ids:
            continue
        normalized = normalize_text(str(row.get("text") or ""))
        if OFFERISH_RE.search(normalized) or EXPLICIT_TRADE_RE.search(normalized):
            suspicious_ids.add(message_id)
            relevant_ids.add(message_id)
            reply_id = row.get("reply_to_message_id")
            if reply_id is not None and int(reply_id) in by_id:
                relevant_ids.add(int(reply_id))
            review_items.append(
                {
                    "message_id": message_id,
                    "reason": "OFFER_OR_TRADE_LIKE_TEXT_NOT_FULLY_PARSED",
                    "confidence": 0.45,
                    "context_message_ids": [message_id],
                }
            )

    return {
        "root_offer_id": root_offer_id,
        "relevant_ids": relevant_ids,
        "requests": request_rows,
        "accepted_trades": sorted(
            accepted_trades,
            key=lambda row: (row["event_time_utc"], row["confirmation_message_id"] or -1),
        ),
        "review_items": review_items,
        "branch_by_message": branch_by_message,
        "confirmation_ids": confirmation_ids,
        "suspicious_ids": suspicious_ids,
        "children": children,
    }


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE imports (
            id INTEGER PRIMARY KEY,
            archive_path TEXT NOT NULL,
            archive_sha256 TEXT NOT NULL UNIQUE,
            imported_at_utc TEXT NOT NULL,
            cutoff_utc TEXT NOT NULL,
            message_count INTEGER NOT NULL,
            retained_message_count INTEGER NOT NULL,
            dropped_message_count INTEGER NOT NULL,
            extractor_version TEXT NOT NULL
        );
        CREATE TABLE messages (
            import_id INTEGER NOT NULL REFERENCES imports(id),
            message_id INTEGER NOT NULL,
            event_time_utc TEXT NOT NULL,
            event_time_tehran TEXT NOT NULL,
            sender_hash TEXT,
            text TEXT NOT NULL,
            reply_to_message_id INTEGER,
            source_html_file TEXT NOT NULL,
            roles_json TEXT NOT NULL,
            relevance_json TEXT NOT NULL,
            PRIMARY KEY(import_id, message_id)
        );
        CREATE TABLE offers (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES imports(id),
            message_id INTEGER NOT NULL,
            offer_index INTEGER NOT NULL,
            commodity TEXT NOT NULL,
            price INTEGER NOT NULL,
            quantity INTEGER,
            side TEXT NOT NULL,
            settlement TEXT NOT NULL,
            trade_form TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_text TEXT NOT NULL,
            price_raw TEXT,
            price_method TEXT,
            commodity_method TEXT,
            quantity_method TEXT,
            UNIQUE(import_id, message_id, offer_index)
        );
        CREATE TABLE trade_requests (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES imports(id),
            request_message_id INTEGER NOT NULL,
            offer_message_id INTEGER NOT NULL,
            confirmation_message_id INTEGER,
            signal TEXT NOT NULL,
            quantity INTEGER,
            status TEXT NOT NULL,
            confidence REAL NOT NULL,
            UNIQUE(import_id, request_message_id)
        );
        CREATE TABLE confirmed_trades (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES imports(id),
            confirmation_message_id INTEGER NOT NULL,
            offer_message_id INTEGER,
            request_message_id INTEGER,
            event_time_utc TEXT NOT NULL,
            commodity TEXT NOT NULL,
            price INTEGER NOT NULL,
            price_raw TEXT,
            price_method TEXT,
            quantity INTEGER,
            quantity_method TEXT,
            reported_quantity INTEGER,
            is_aggregate INTEGER NOT NULL DEFAULT 0,
            training_eligible INTEGER NOT NULL DEFAULT 1,
            side TEXT NOT NULL,
            settlement TEXT NOT NULL,
            trade_form TEXT NOT NULL,
            confidence REAL NOT NULL,
            confirmation_type TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            context_json TEXT NOT NULL,
            UNIQUE(import_id, confirmation_message_id, request_message_id)
        );
        CREATE TABLE review_queue (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL REFERENCES imports(id),
            message_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            confidence REAL NOT NULL,
            context_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            reviewer_payload_json TEXT,
            UNIQUE(import_id, message_id, reason)
        );
        CREATE INDEX idx_messages_time ON messages(event_time_utc);
        CREATE INDEX idx_offers_market ON offers(commodity, settlement, trade_form);
        CREATE INDEX idx_confirmed_trades_market
            ON confirmed_trades(commodity, settlement, trade_form, event_time_utc);
        """
    )


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def repair_unparsed_offers(
    messages: list[dict[str, Any]],
    enriched: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recover narrow, auditable cases that the legacy parser misses."""

    anchors: list[tuple[datetime, int, int]] = []
    review_items: list[dict[str, Any]] = []

    def remember(index: int, row: dict[str, Any]) -> None:
        when = parse_time(str(messages[index]["date_utc"]))
        for offer in row.get("extracted_offers") or []:
            if (
                offer.get("commodity") == "امام"
                and offer.get("price_method") == "full"
                and float(offer.get("confidence") or 0.0) >= 0.84
            ):
                anchors.append((when, index, int(offer["price"])))

    for index, (message, row) in enumerate(zip(messages, enriched)):
        if row.get("extracted_offers"):
            remember(index, row)
            continue

        text = str(message.get("text") or "")
        normalized = normalize_text(text)

        # Here شنبه is a settlement date.  It must not cause the immediately
        # preceding full price to be discarded as non-price context.
        if re.search(r"(?<![آ-ی])شنبه(?![آ-ی])", normalized):
            without_settlement = re.sub(
                r"(?<![آ-ی])شنبه(?![آ-ی])",
                " ",
                text,
            )
            repaired = enrich_records(
                [{"date": message["date_tehran"], "text": without_settlement}]
            )[0]["extracted_offers"]
            if repaired:
                for offer in repaired:
                    offer["settlement"] = "TOMORROW"
                row["extracted_offers"] = repaired
                remember(index, row)
                continue

        # A non-round tail such as 403 in an explicit Imam offer can be mapped
        # to the nearest strictly-prior same-day full-price anchor.  It stays
        # review-only because the tail is less conventional than 400/500.
        if explicit_commodity(normalized) != "امام":
            continue
        side, settlement, trade_form = offer_context(normalized)
        if side == "UNKNOWN":
            continue
        quantity, quantity_spans, quantity_method = explicit_quantity(normalized)
        if quantity is None:
            continue
        candidates = [
            token
            for token in numeric_tokens(normalized)
            if len(token.digits) in {3, 4}
            and not any(
                token.start < span[1] and span[0] < token.end
                for span in quantity_spans
            )
            and not token_has_non_price_context(normalized, token)
        ]
        when = parse_time(str(message["date_utc"]))
        nearby = [
            item
            for item in anchors[-20:]
            if item[0].date() == when.date()
            and item[0] <= when
            and (
                (when - item[0]).total_seconds() <= 1800
                or 0 <= index - item[1] <= 60
            )
        ]
        if len(candidates) != 1 or not nearby:
            continue
        nearby.sort(
            key=lambda item: (
                (when - item[0]).total_seconds(),
                index - item[1],
            )
        )
        anchor = nearby[0][2]
        token = candidates[0]
        modulus = 10 ** len(token.digits)
        base = (anchor // modulus) * modulus
        prices = [
            price
            for price in (
                base + int(token.digits),
                base - modulus + int(token.digits),
                base + modulus + int(token.digits),
            )
            if 135_000 <= price <= 260_000
        ]
        if not prices:
            continue
        price = min(prices, key=lambda value: (abs(value - anchor), value))
        if abs(price - anchor) > 6_000:
            continue
        row["extracted_offers"] = [
            {
                "commodity": "امام",
                "commodity_method": "explicit",
                "price": price,
                "price_raw": token.raw,
                "price_method": "contextual_tail_unrounded",
                "quantity": quantity,
                "quantity_method": quantity_method,
                "side": side,
                "settlement": settlement,
                "trade_form": trade_form,
                "confidence": 0.78,
            }
        ]
        review_items.append(
            {
                "message_id": int(message["message_id"]),
                "reason": "NON_ROUND_CONTEXTUAL_TAIL_REQUIRES_REVIEW",
                "confidence": 0.78,
                "context_message_ids": [int(message["message_id"])],
            }
        )

    return review_items


def build(source: Path, output: Path, json_output: Path) -> dict[str, Any]:
    digest = source_hash(source)
    raw_messages, html_files = parse_source(source)
    messages, parse_counts = normalize_full_history(raw_messages)
    enriched = enrich_records(
        [{"date": row["date_tehran"], "text": row["text"]} for row in messages]
    )
    parse_review_items = repair_unparsed_offers(messages, enriched)
    offers_by_message = {
        int(message["message_id"]): list(enriched[index]["extracted_offers"])
        for index, message in enumerate(messages)
        if enriched[index]["extracted_offers"]
    }
    analysis = analyze_reply_trades(messages, offers_by_message)
    analysis["review_items"].extend(parse_review_items)
    analysis["relevant_ids"].update(
        int(item["message_id"]) for item in parse_review_items
    )
    relevant_ids = set(analysis["relevant_ids"])
    by_id = {int(row["message_id"]): row for row in messages}
    roles: dict[int, set[str]] = defaultdict(set)
    reasons: dict[int, set[str]] = defaultdict(set)
    for message_id in offers_by_message:
        roles[message_id].add("OFFER")
        reasons[message_id].add("PARSED_OFFER")
    for request in analysis["requests"]:
        message_id = int(request["request_message_id"])
        roles[message_id].add("TRADE_REQUEST")
        reasons[message_id].add(str(request["signal"]))
    for trade in analysis["accepted_trades"]:
        confirmation_id = int(trade["confirmation_message_id"])
        roles[confirmation_id].add("TRADE_CONFIRMATION")
        reasons[confirmation_id].add(str(trade["confirmation_type"]))
    for item in analysis["review_items"]:
        message_id = int(item["message_id"])
        roles[message_id].add("REVIEW")
        reasons[message_id].add(str(item["reason"]))
    for message_id in analysis["suspicious_ids"]:
        roles[message_id].add("OFFER_PARSE_REVIEW")
    for message_id in relevant_ids:
        if not roles[message_id]:
            roles[message_id].add("NEGOTIATION_CONTEXT")
            reasons[message_id].add("REPLY_CONNECTED_TO_OFFER")

    retained_messages: list[dict[str, Any]] = []
    for row in messages:
        message_id = int(row["message_id"])
        if message_id not in relevant_ids:
            continue
        retained_messages.append(
            {
                "message_id": message_id,
                "date_utc": row["date_utc"],
                "date_tehran": row["date_tehran"],
                "sender_hash": sender_hash(row.get("from_name")),
                "text": row["text"],
                "reply_to_message_id": row.get("reply_to_message_id"),
                "roles": sorted(roles[message_id]),
                "relevance": sorted(reasons[message_id]),
                "extracted_offers": offers_by_message.get(message_id, []),
            }
        )

    accepted_trades = analysis["accepted_trades"]
    review_items = analysis["review_items"]
    payload = {
        "metadata": {
            "source": str(source.resolve()),
            "source_sha256": digest,
            "extractor_version": EXTRACTOR_VERSION,
            "html_files": html_files,
            "first_message_utc": messages[0]["date_utc"] if messages else None,
            "last_message_utc": messages[-1]["date_utc"] if messages else None,
            "raw_messages": len(messages),
            "retained_messages": len(retained_messages),
            "dropped_definitely_irrelevant_messages": len(messages) - len(retained_messages),
            "offers": sum(len(value) for value in offers_by_message.values()),
            "trade_requests": len(analysis["requests"]),
            "confirmed_trades": len(accepted_trades),
            "training_eligible_trades": sum(
                bool(row.get("training_eligible", True)) for row in accepted_trades
            ),
            "aggregate_trade_announcements": sum(
                bool(row.get("is_aggregate")) for row in accepted_trades
            ),
            "review_candidates": len(review_items),
        },
        "messages": retained_messages,
        "confirmed_trades": accepted_trades,
        "review_candidates": review_items,
    }
    write_json_atomic(json_output, payload)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    try:
        initialize(connection)
        imported_at = iso_utc(datetime.now(timezone.utc))
        cursor = connection.execute(
            """
            INSERT INTO imports(
                archive_path, archive_sha256, imported_at_utc, cutoff_utc,
                message_count, retained_message_count, dropped_message_count,
                extractor_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(source.resolve()),
                digest,
                imported_at,
                messages[0]["date_utc"] if messages else "",
                len(messages),
                len(retained_messages),
                len(messages) - len(retained_messages),
                EXTRACTOR_VERSION,
            ),
        )
        import_id = int(cursor.lastrowid)
        for row in retained_messages:
            connection.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    import_id,
                    row["message_id"],
                    row["date_utc"],
                    row["date_tehran"],
                    row["sender_hash"],
                    row["text"],
                    row["reply_to_message_id"],
                    by_id[row["message_id"]]["source_html_file"],
                    json.dumps(row["roles"], ensure_ascii=False),
                    json.dumps(row["relevance"], ensure_ascii=False),
                ),
            )
            for offer_index, offer in enumerate(row["extracted_offers"]):
                connection.execute(
                    """
                    INSERT INTO offers(
                        import_id, message_id, offer_index, commodity, price,
                        quantity, side, settlement, trade_form, confidence,
                        source_text, price_raw, price_method, commodity_method,
                        quantity_method
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_id,
                        row["message_id"],
                        offer_index,
                        offer["commodity"],
                        offer["price"],
                        offer.get("quantity"),
                        offer.get("side") or "UNKNOWN",
                        offer.get("settlement") or "CASH",
                        offer.get("trade_form") or "PHYSICAL",
                        offer["confidence"],
                        row["text"],
                        offer.get("price_raw"),
                        offer.get("price_method"),
                        offer.get("commodity_method"),
                        offer.get("quantity_method"),
                    ),
                )
        for request in analysis["requests"]:
            connection.execute(
                """
                INSERT INTO trade_requests(
                    import_id, request_message_id, offer_message_id,
                    confirmation_message_id, signal, quantity, status, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    request["request_message_id"],
                    request["offer_message_id"],
                    request["confirmation_message_id"],
                    request["signal"],
                    request["quantity"],
                    request["status"],
                    request["confidence"],
                ),
            )
        for trade in accepted_trades:
            context = [
                {
                    "message_id": message_id,
                    "sender_hash": sender_hash(by_id[message_id].get("from_name")),
                    "text": by_id[message_id]["text"],
                    "reply_to_message_id": by_id[message_id].get("reply_to_message_id"),
                }
                for message_id in trade["context_message_ids"]
                if message_id in by_id
            ]
            connection.execute(
                """
                INSERT INTO confirmed_trades(
                    import_id, confirmation_message_id, offer_message_id,
                    request_message_id, event_time_utc, commodity, price,
                    price_raw, price_method, quantity, quantity_method,
                    reported_quantity, is_aggregate, training_eligible, side,
                    settlement, trade_form, confidence, confirmation_type,
                    evidence_json, context_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    trade["confirmation_message_id"],
                    trade["offer_message_id"],
                    trade["request_message_id"],
                    trade["event_time_utc"],
                    trade["commodity"],
                    trade["price"],
                    trade.get("price_raw"),
                    trade.get("price_method"),
                    trade["quantity"],
                    trade.get("quantity_method"),
                    trade.get("reported_quantity"),
                    int(bool(trade.get("is_aggregate"))),
                    int(bool(trade.get("training_eligible", True))),
                    trade["side"],
                    trade["settlement"],
                    trade["trade_form"],
                    trade["confidence"],
                    trade["confirmation_type"],
                    json.dumps(trade["evidence"], ensure_ascii=False),
                    json.dumps(context, ensure_ascii=False),
                ),
            )
        for item in review_items:
            context = [
                {
                    "message_id": message_id,
                    "sender_hash": sender_hash(by_id[message_id].get("from_name")),
                    "text": by_id[message_id]["text"],
                    "reply_to_message_id": by_id[message_id].get("reply_to_message_id"),
                }
                for message_id in item["context_message_ids"]
                if message_id in by_id
            ]
            connection.execute(
                """
                INSERT OR IGNORE INTO review_queue(
                    import_id, message_id, reason, confidence, context_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    item["message_id"],
                    item["reason"],
                    item["confidence"],
                    json.dumps(context, ensure_ascii=False),
                ),
            )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        connection.close()
    os.chmod(temporary, 0o600)
    temporary.replace(output)

    summary = {
        **payload["metadata"],
        "database": str(output.resolve()),
        "json_output": str(json_output.resolve()),
        "database_integrity": integrity,
        "parse_counts": parse_counts,
        "accepted_trade_types": dict(
            sorted(
                {
                    kind: sum(row["confirmation_type"] == kind for row in accepted_trades)
                    for kind in {row["confirmation_type"] for row in accepted_trades}
                }.items()
            )
        ),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    summary = build(args.source, args.output, args.json_output)
    write_json_atomic(args.manifest, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
