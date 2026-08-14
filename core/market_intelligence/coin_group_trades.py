"""Conservative reply-chain trade linking for short-lived coin-group staging.

Only a structurally linked and explicit confirmation creates a trade fact.
Requests, blessings without an attributable offerer confirmation, ambiguous
reply parents, and quantity overfills remain out of the model-facing store.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
from typing import Iterable, Mapping

from .coin_group_resolution import ResolvedCoinGroupOffer
from .coin_group_staging import StagedCoinGroupMessage
from .market_contracts import MarketObservation, derive_event_key


COIN_GROUP_TRADE_LINKER_VERSION = "coin-group-trade-link-v2-branch-terms"
MAX_REPLY_DEPTH = 12
MAX_REPLY_AGE_SECONDS = 2 * 60 * 60
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_QTY = re.compile(r"(?<!\d)(\d{1,3})\s*(?:د?تا|عدد)\b")
_NUMBER = re.compile(r"(?<!\d)(\d{1,3}(?:[٬،,]\d{3})+|\d{2,7})(?!\d)")
_SMALL_NUMBER = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_CANCEL = re.compile(r"کنسل|لغو|منتفی|پاس|حذف|نشد|ندارم|تمام\s*شد|اشتباه|عذر")
_EXPLICIT_TRADE = re.compile(
    r"معامله|مع\s+با|مع\s+شد|انجام\s*شد|خریدم|فروختم|برداشتم|مال\s+من|"
    r"خرید(?:م|ه)?\s*شد|فروش(?:م|ه)?|(?<![آ-ی])مع(?![آ-ی])"
)
_CUMULATIVE = re.compile(r"کلا|کلن|جمعا|مجموعا|مجموع")
_ACCEPT = re.compile(r"برکت|^چشم\s+ب(?:\s|$)|^(?:ب|اوکی|تایید|قبول|شد|بزن|بده|بردار|باشه|تمام)(?:\s|$)")
_BUY_MARKER = re.compile(r"(?<![آ-ی])ب(?![آ-ی])|خریدم|برداشتم|مال\s+من")


@dataclass(frozen=True, slots=True)
class CoinGroupOfferRecord:
    """Transient link between an eligible resolved offer and its staged root."""

    group_number: int
    message_id: int
    offerer_digest: bytes | None
    offer_event_time_utc: str
    offer_available_at_utc: str
    offer: ResolvedCoinGroupOffer


@dataclass(frozen=True, slots=True)
class LinkedCoinGroupTrade:
    """Private IDs exist only in memory; final projection keeps an opaque key."""

    group_number: int
    root_offer_message_id: int
    confirmation_message_id: int
    commodity_code: str
    price_project_thousand_toman: int
    quantity: int
    side: str
    settlement_term: str
    trade_form: str
    event_time_utc: str
    available_at_utc: str
    is_conditional: bool
    quality_state: str
    confirmation_kind: str
    is_aggregate: bool


def _text(value: str) -> str:
    return " ".join(str(value or "").translate(_DIGITS).replace("\u200c", " ").split())


def _signal(text: str) -> str:
    normalized = _text(text)
    if not normalized or _CANCEL.search(normalized):
        return "REJECT"
    if "؟" in normalized or "?" in normalized:
        return "QUESTION"
    if _EXPLICIT_TRADE.search(normalized):
        return "EXPLICIT_TRADE"
    if _ACCEPT.search(normalized):
        return "ACCEPT"
    if _BUY_MARKER.search(normalized):
        return "BUY_REQUEST"
    return "NEGOTIATION"


def _quantity_and_spans(text: str) -> tuple[int | None, list[tuple[int, int]]]:
    match = _QTY.search(text)
    if match is not None:
        value = int(match.group(1))
        return (value if 1 <= value <= 100 else None), [match.span(1)]
    # Negotiation replies commonly omit «تا» (``ب ۱۰``, ``۹ب``, or a bare
    # ``۲۵``).  Accept one small integer only when the rest of the message is
    # an acceptance/question/quantity-shaped phrase; a three-digit price tail
    # such as ``۳۰۰`` must not become quantity.
    candidates = [
        item
        for item in _SMALL_NUMBER.finditer(text)
        if 1 <= int(re.sub(r"\D", "", item.group(1))) <= 100
    ]
    if len(candidates) == 1 and (
        _BUY_MARKER.search(text)
        or "؟" in text
        or "?" in text
        or re.fullmatch(r"\s*\d{1,3}\s*", text)
    ):
        candidate = candidates[0]
        return int(re.sub(r"\D", "", candidate.group(1))), [candidate.span(1)]
    return None, []


def _overlap(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in spans)


def _negotiated_price(text: str, *, offer_price: int, quantity_spans: Iterable[tuple[int, int]]) -> int | None:
    """Use an explicit reply price only when it remains close to the offer."""

    candidates: list[int] = []
    for match in _NUMBER.finditer(text):
        if _overlap(match.span(1), quantity_spans):
            continue
        digits = re.sub(r"\D", "", match.group(1))
        raw = int(digits)
        values: list[int] = []
        if len(digits) in {5, 6}:
            values.append(raw)
        elif len(digits) == 3 and raw % 50 == 0:
            base = (offer_price // 1000) * 1000
            values.extend((base - 1000 + raw, base + raw, base + 1000 + raw))
        elif len(digits) == 4 and raw % 50 == 0:
            base = (offer_price // 10_000) * 10_000
            values.extend((base - 10_000 + raw, base + raw, base + 10_000 + raw))
        for value in values:
            if value > 0 and abs(value - offer_price) / offer_price <= 0.03:
                candidates.append(value)
    if not candidates:
        return None
    ranked = sorted(set(candidates), key=lambda item: (abs(item - offer_price), item))
    winner = ranked[0]
    if len(ranked) > 1 and abs(ranked[0] - offer_price) == abs(ranked[1] - offer_price):
        return None
    return winner


def _message_map(messages: Iterable[StagedCoinGroupMessage]) -> dict[tuple[int, int], StagedCoinGroupMessage]:
    values: dict[tuple[int, int], StagedCoinGroupMessage] = {}
    for message in messages:
        key = (message.group_number, message.message_id)
        current = values.get(key)
        if current is None or message.revision > current.revision:
            values[key] = message
    return values


def _root_offer(
    message: StagedCoinGroupMessage,
    *,
    messages: Mapping[tuple[int, int], StagedCoinGroupMessage],
    offers: Mapping[tuple[int, int], CoinGroupOfferRecord],
) -> CoinGroupOfferRecord | None:
    current = message
    seen: set[int] = set()
    for _ in range(MAX_REPLY_DEPTH):
        key = (current.group_number, current.message_id)
        candidate = offers.get(key)
        if candidate is not None:
            return candidate
        parent_id = current.reply_to_message_id
        if parent_id is None or parent_id in seen:
            return None
        seen.add(parent_id)
        parent = messages.get((current.group_number, parent_id))
        if parent is None:
            return None
        current = parent
    return None


def _branch_from_root(
    message: StagedCoinGroupMessage,
    *,
    root: CoinGroupOfferRecord,
    messages: Mapping[tuple[int, int], StagedCoinGroupMessage],
) -> list[StagedCoinGroupMessage]:
    """Return exactly one reply path, excluding sibling/user branches."""

    reverse_path: list[StagedCoinGroupMessage] = []
    current = message
    seen: set[int] = set()
    for _ in range(MAX_REPLY_DEPTH):
        if current.message_id in seen:
            return []
        seen.add(current.message_id)
        reverse_path.append(current)
        if current.message_id == root.message_id:
            return list(reversed(reverse_path))
        parent_id = current.reply_to_message_id
        if parent_id is None:
            return []
        parent = messages.get((current.group_number, parent_id))
        if parent is None:
            return []
        current = parent
    return []


def _age_seconds(later: str, earlier: str) -> float:
    from datetime import datetime

    return (datetime.fromisoformat(later.replace("Z", "+00:00")) - datetime.fromisoformat(earlier.replace("Z", "+00:00"))).total_seconds()


def _trade_from_confirmation(
    message: StagedCoinGroupMessage,
    *,
    root: CoinGroupOfferRecord,
    messages: Mapping[tuple[int, int], StagedCoinGroupMessage],
) -> LinkedCoinGroupTrade | None:
    offer = root.offer
    if offer.quality_state != "ELIGIBLE" or offer.commodity_code is None:
        return None
    if root.offerer_digest is None or message.sender_digest is None:
        # We may retain a source row with an unknown display name, but without
        # a stable transient identity we cannot safely assert a reciprocal
        # confirmation or counterparty declaration.
        return None
    if not 0 <= _age_seconds(message.event_time_utc, root.offer_event_time_utc) <= MAX_REPLY_AGE_SECONDS:
        return None
    signal = _signal(message.text)
    if signal in {"REJECT", "QUESTION", "NEGOTIATION"}:
        return None
    branch = _branch_from_root(message, root=root, messages=messages)
    if len(branch) < 2:
        return None
    parent = branch[-2]
    owner_confirmation = message.sender_digest is not None and message.sender_digest == root.offerer_digest
    direct_to_root = parent.message_id == root.message_id
    counterparty_digest: bytes | None = None
    if owner_confirmation:
        if direct_to_root and signal != "EXPLICIT_TRADE":
            return None
        if not direct_to_root and signal not in {"ACCEPT", "EXPLICIT_TRADE"}:
            return None
        for branch_message in reversed(branch[1:-1]):
            if (
                branch_message.sender_digest is not None
                and branch_message.sender_digest != root.offerer_digest
            ):
                counterparty_digest = branch_message.sender_digest
                break
        if not direct_to_root and counterparty_digest is None:
            return None
        kind = "OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE" if direct_to_root else "RECIPROCAL_OFFERER_CONFIRMATION"
    else:
        # A counterparty's direct «خریدم/برداشتم/معامله شد» reply is recorded
        # as a lower-confidence declaration.  A later bare acceptance is valid
        # only when that same counterparty already participated in this exact
        # branch and replies to an owner counter-offer.
        counterparty_digest = message.sender_digest
        prior_counterparty_turn = any(
            item.sender_digest == counterparty_digest for item in branch[1:-1]
        )
        if direct_to_root:
            if signal != "EXPLICIT_TRADE":
                return None
        elif not (
            signal in {"ACCEPT", "EXPLICIT_TRADE"}
            and parent.sender_digest == root.offerer_digest
            and prior_counterparty_turn
        ):
            return None
        kind = (
            "COUNTERPARTY_EXPLICIT_REPLY_TRADE"
            if signal == "EXPLICIT_TRADE"
            else "RECIPROCAL_COUNTERPARTY_CONFIRMATION"
        )

    participants = {root.offerer_digest}
    if counterparty_digest is not None:
        participants.add(counterparty_digest)
    evidence = [
        item for item in branch[1:] if item.sender_digest in participants
    ]
    quantity: int | None = None
    price: int | None = None
    for evidence_message in evidence:
        normalized = _text(evidence_message.text)
        candidate_quantity, spans = _quantity_and_spans(normalized)
        if candidate_quantity is not None:
            quantity = candidate_quantity
        candidate_price = _negotiated_price(
            normalized,
            offer_price=offer.price_project_thousand_toman,
            quantity_spans=spans,
        )
        if candidate_price is not None:
            price = candidate_price
    if quantity is None:
        if kind == "OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE":
            return None
        quantity = offer.quantity
    if price is None:
        price = offer.price_project_thousand_toman
    is_aggregate = kind == "OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE" and bool(
        _CUMULATIVE.search(_text(message.text))
    )
    quality = "PENDING_REVIEW" if is_aggregate and quantity > offer.quantity else "ELIGIBLE"
    return LinkedCoinGroupTrade(
        group_number=root.group_number,
        root_offer_message_id=root.message_id,
        confirmation_message_id=message.message_id,
        commodity_code=offer.commodity_code,
        price_project_thousand_toman=price,
        quantity=quantity,
        side=offer.side,
        settlement_term=offer.settlement_term,
        trade_form=offer.trade_form,
        event_time_utc=message.event_time_utc,
        available_at_utc=message.available_at_utc,
        is_conditional=offer.is_conditional,
        quality_state=quality,
        confirmation_kind=kind,
        is_aggregate=is_aggregate,
    )


def link_coin_group_trades(
    messages: Iterable[StagedCoinGroupMessage],
    offers: Iterable[CoinGroupOfferRecord],
) -> list[LinkedCoinGroupTrade]:
    """Link only confirmation chains; repeated fills cannot overrun an offer."""

    message_by_key = _message_map(messages)
    offer_by_key = {
        (offer.group_number, offer.message_id): offer
        for offer in offers
        if offer.offer.quality_state == "ELIGIBLE" and offer.offer.commodity_code is not None
    }
    candidates: list[tuple[LinkedCoinGroupTrade, frozenset[int]]] = []
    for message in sorted(message_by_key.values(), key=lambda item: (item.event_time_utc, item.message_id)):
        if message.reply_to_message_id is None:
            continue
        root = _root_offer(message, messages=message_by_key, offers=offer_by_key)
        if root is None or message.message_id == root.message_id:
            continue
        trade = _trade_from_confirmation(message, root=root, messages=message_by_key)
        if trade is None:
            continue
        branch_ids = frozenset(
            item.message_id
            for item in _branch_from_root(message, root=root, messages=message_by_key)
        )
        candidates.append((trade, branch_ids))

    # One conversational branch can contain both a counterparty declaration
    # and the owner's later confirmation.  They are two pieces of evidence for
    # one fill, not two fills.  Prefer owner evidence, then the later terminal
    # confirmation; sibling reply branches remain independent.
    authority = {
        "OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE": 3,
        "RECIPROCAL_OFFERER_CONFIRMATION": 3,
        "COUNTERPARTY_EXPLICIT_REPLY_TRADE": 2,
        "RECIPROCAL_COUNTERPARTY_CONFIRMATION": 2,
    }
    selected: list[LinkedCoinGroupTrade] = []
    for candidate, candidate_branch in candidates:
        superseded = False
        for other, other_branch in candidates:
            if other is candidate:
                continue
            same_root = (
                other.group_number == candidate.group_number
                and other.root_offer_message_id == candidate.root_offer_message_id
            )
            same_reply_path = (
                candidate.confirmation_message_id in other_branch
                or other.confirmation_message_id in candidate_branch
            )
            if not same_root or not same_reply_path:
                continue
            candidate_rank = (
                authority.get(candidate.confirmation_kind, 0),
                candidate.event_time_utc,
                candidate.confirmation_message_id,
            )
            other_rank = (
                authority.get(other.confirmation_kind, 0),
                other.event_time_utc,
                other.confirmation_message_id,
            )
            if other_rank > candidate_rank:
                superseded = True
                break
        if not superseded:
            selected.append(candidate)

    filled: dict[tuple[int, int], int] = {}
    trades: list[LinkedCoinGroupTrade] = []
    for trade in sorted(
        selected, key=lambda item: (item.event_time_utc, item.confirmation_message_id)
    ):
        root_key = (trade.group_number, trade.root_offer_message_id)
        root = offer_by_key[root_key]
        if not trade.is_aggregate:
            remaining = root.offer.quantity - filled.get(root_key, 0)
            if trade.quantity > remaining:
                continue
            filled[root_key] = filled.get(root_key, 0) + trade.quantity
        trades.append(trade)
    return trades


def coin_group_trade_observations(trades: Iterable[LinkedCoinGroupTrade]) -> list[MarketObservation]:
    """Project linked trades without raw message/reply/sender identifiers."""

    observations: list[MarketObservation] = []
    for trade in trades:
        observations.append(
            MarketObservation(
                event_key=derive_event_key(
                    "coin-group-trade-v1", trade.group_number, trade.root_offer_message_id, trade.confirmation_message_id
                ),
                source_code=f"GROUP_{trade.group_number}",
                source_family="GROUP",
                event_time_utc=trade.event_time_utc,
                available_at_utc=trade.available_at_utc,
                instrument="COIN_" + trade.commodity_code,
                market_label="GROUP_COIN_" + trade.commodity_code,
                settlement_term=trade.settlement_term,
                trade_form=trade.trade_form,
                event_type="TRADE",
                side=trade.side,
                price=Decimal(trade.price_project_thousand_toman),
                price_unit="PROJECT_THOUSAND_TOMAN",
                currency="TOMAN",
                quantity=trade.quantity,
                quantity_unit="COIN_COUNT",
                parse_confidence=0.99 if trade.quality_state == "ELIGIBLE" else 0.65,
                parser_version=COIN_GROUP_TRADE_LINKER_VERSION,
                quality_state=trade.quality_state,
                quality_policy_version="coin-group-trade-link-v1",
                is_conditional=trade.is_conditional,
                attributes={
                    "group_number": trade.group_number,
                    "confirmation_kind": trade.confirmation_kind,
                    "is_aggregate": trade.is_aggregate,
                },
            )
        )
    return observations
