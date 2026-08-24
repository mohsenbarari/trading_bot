"""Conservative reply-chain trade linking for short-lived coin-group staging.

Only a structurally linked and explicit confirmation creates a trade fact.
Requests, blessings without an attributable offerer confirmation, ambiguous
reply parents, and quantity overfills remain out of the model-facing store.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
import re
from typing import Iterable, Mapping

from .coin_group_resolution import ResolvedCoinGroupOffer
from .coin_group_staging import StagedCoinGroupMessage
from .coin_groups import (
    _PRICE_BOUNDS,
    _explicit_quantity,
    _price_candidates,
    _text as _normalize_group_text,
    coin_group_settlement_markers,
)
from .market_contracts import MarketObservation, derive_event_key, normalize_utc


COIN_GROUP_TRADE_LINKER_VERSION = "coin-group-trade-link-v6-contextual-replies"
MAX_REPLY_DEPTH = 12
MAX_REPLY_AGE_SECONDS = 2 * 60 * 60
MAX_NEGOTIATED_PRICE_RELATIVE_DELTA = 0.05
_NUMBER = re.compile(
    r"(?<!\d)(\d[٬،,./]\d{2}[٬،,./]\d{3}|"
    r"\d{1,3}(?:[٬،,./]\d{3})+|\d{2,3}[٬،,./]\d{1,2}|"
    r"\d{2,3}[٬،,./]\d{4,5}|\d{2,7})(?!\d)"
)
_SMALL_NUMBER = re.compile(r"(?<!\d)(\d{1,3})(?!\d)")
_CANCEL = re.compile(
    r"کنسل|لغو|منتفی|پاس|حذف|نشد|ندارم|اشتباه|عذر|بی\s*خیال|"
    r"قبول\s*نیست|نمی\s*(?:خوام|خواهم)"
)
_EXPLICIT_TRADE = re.compile(
    r"معامله|مع\s+با|مع\s+شد|انجام\s*شد|قطعی\s*شد|جوش\s*خورد|"
    r"خریدم|فروختم|برداشتم|مال\s+من|خرید(?:ه)?\s*شد|"
    r"فروخته\s*شد|فروش\s*(?:انجام\s*)?شد|(?<![آ-ی])مع(?![آ-ی])"
)
_CUMULATIVE = re.compile(r"کلا|کلن|جمعا|مجموعا|مجموع")
_ACCEPT = re.compile(
    r"برکت|^چشم\s+ب(?:\s|$)|^(?:ب|بله|اوکی(?:ه)?|ت[اأ]یید|قبول(?:ه)?|"
    r"موافق(?:م)?|حله|شد|بزن(?:ید)?|بده|بردار|باش(?:ه)?|تمام)(?:\s|$)"
)
_BUY_MARKER = re.compile(
    r"(?<![آ-ی])(?:ب|خ)(?![آ-ی])|خریدم|برداشتم|مال\s+من"
)
_SELL_MARKER = re.compile(r"(?<![آ-ی])ف(?![آ-ی])|فروختم")
_PARTICIPATION_MARKER = re.compile(
    rf"(?:{_BUY_MARKER.pattern})|(?:{_SELL_MARKER.pattern})"
)


@dataclass(frozen=True, slots=True)
class CoinGroupOfferRecord:
    """Transient link between one resolved candidate and its staged root."""

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
    commodity_code: str | None
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
    quantity_was_negotiated: bool
    resolution_reason: str


@dataclass(frozen=True, slots=True)
class _TradeCandidate:
    trade: LinkedCoinGroupTrade
    branch_ids: frozenset[int]
    participant_digests: frozenset[bytes]


def _text(value: str) -> str:
    return _normalize_group_text(value)


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
    if _SELL_MARKER.search(normalized):
        return "SELL_REQUEST"
    return "NEGOTIATION"


def _quantity_and_spans(
    text: str,
    *,
    offer_price: int,
) -> tuple[int | None, list[tuple[int, int]]]:
    explicit, spans = _explicit_quantity(text)
    if explicit is not None:
        return explicit, spans
    # Negotiation replies commonly omit «تا» (``ب ۱۰``, ``۹ب``, or a bare
    # ``۲۵``).  Accept one small integer only when the rest of the message is
    # an acceptance/question/quantity-shaped phrase; a three-digit price tail
    # such as ``۳۰۰`` must not become quantity.
    candidates = [
        item
        for item in _SMALL_NUMBER.finditer(text)
        if 1 <= int(re.sub(r"\D", "", item.group(1))) <= 100
    ]
    other_price_shaped_number = bool(
        len(candidates) == 1
        and any(
            not _overlap(match.span(1), [candidates[0].span(1)])
            and len(re.sub(r"\D", "", match.group(1))) >= 3
            for match in _NUMBER.finditer(text)
        )
    )
    if len(candidates) == 1 and (
        _PARTICIPATION_MARKER.search(text)
        or _ACCEPT.search(text)
        or "؟" in text
        or "?" in text
        or re.fullmatch(r"\s*\d{1,3}\s*", text)
        or other_price_shaped_number
    ):
        candidate = candidates[0]
        value = int(re.sub(r"\D", "", candidate.group(1)))
        digits = len(re.sub(r"\D", "", candidate.group(1)))
        # In coin bargaining, bare three-digit multiples and two-digit values
        # near the root quote are price/tail shorthands.  Quantity 100 remains
        # available only when explicitly marked with «تا/عدد/دونه».
        price_shorthand = bool(
            digits == 3
            or digits == 2
            and abs(value * 1000 - offer_price) / offer_price
            <= MAX_NEGOTIATED_PRICE_RELATIVE_DELTA
        )
        if not price_shorthand:
            return value, [candidate.span(1)]
    return None, []


def _overlap(span: tuple[int, int], spans: Iterable[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in spans)


def _negotiated_price_result(
    text: str,
    *,
    offer_price: int,
    quantity_spans: Iterable[tuple[int, int]],
    commodity_code: str | None,
) -> tuple[int | None, bool, bool]:
    """Return price, whether a price-like number existed, and safety status.

    Exact project-thousand values, full-Toman values, redundant-zero prices,
    and contextual tails share the offer parser's normalization policy.  A
    plausible but unusually distant negotiated value is retained for audit and
    gated from the model instead of silently falling back to the root price.
    """

    excluded = tuple(quantity_spans)
    numeric_seen = False
    candidates = {
        value
        for value, _score, _span in _price_candidates(
            text,
            excluded,
            commodity=commodity_code,
        )
    }
    for match in _NUMBER.finditer(text):
        if _overlap(match.span(1), excluded):
            continue
        numeric_seen = True
        digits = re.sub(r"\D", "", match.group(1))
        raw = int(digits)
        values: list[int] = []
        if len(digits) == 3 and raw % 50 == 0:
            base = (offer_price // 1000) * 1000
            values.extend((base - 1000 + raw, base + raw, base + 1000 + raw))
        elif len(digits) == 4 and raw % 50 == 0:
            base = (offer_price // 10_000) * 10_000
            values.extend((base - 10_000 + raw, base + raw, base + 10_000 + raw))
        candidates.update(value for value in values if value > 0)
    low, high = _PRICE_BOUNDS.get(commodity_code or "", (20_000, 260_000))
    plausible = sorted(
        {value for value in candidates if low <= value <= high},
        key=lambda item: (abs(item - offer_price), item),
    )
    if not plausible:
        return None, numeric_seen, not numeric_seen
    winner = plausible[0]
    if (
        len(plausible) > 1
        and abs(plausible[0] - offer_price) == abs(plausible[1] - offer_price)
    ):
        return None, True, False
    safe = abs(winner - offer_price) / offer_price <= MAX_NEGOTIATED_PRICE_RELATIVE_DELTA
    return winner, True, safe


def _negotiated_price(
    text: str,
    *,
    offer_price: int,
    quantity_spans: Iterable[tuple[int, int]],
    commodity_code: str | None = None,
) -> int | None:
    winner, _seen, _safe = _negotiated_price_result(
        text,
        offer_price=offer_price,
        quantity_spans=quantity_spans,
        commodity_code=commodity_code,
    )
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
    oldest_candidate: CoinGroupOfferRecord | None = None
    for _ in range(MAX_REPLY_DEPTH):
        key = (current.group_number, current.message_id)
        candidate = offers.get(key)
        if candidate is not None:
            # Negotiation replies can themselves look offer-shaped.  Keep
            # walking to the oldest offer candidate on this exact ancestry so
            # a counter-offer cannot steal the root from the original offer.
            oldest_candidate = candidate
        parent_id = current.reply_to_message_id
        if parent_id is None or parent_id in seen:
            return oldest_candidate if parent_id is None else None
        seen.add(parent_id)
        parent = messages.get((current.group_number, parent_id))
        if parent is None:
            # A retained counter-offer must not become a new root merely
            # because an older parent fell outside staging retention.
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


def _append_gate_reason(current: str, gate: str) -> str:
    if current == "STRUCTURALLY_LINKED_CONFIRMED_TRADE":
        return gate
    if gate in current.split(";"):
        return current
    return current + ";" + gate


def _trade_from_confirmation(
    message: StagedCoinGroupMessage,
    *,
    root: CoinGroupOfferRecord,
    messages: Mapping[tuple[int, int], StagedCoinGroupMessage],
) -> tuple[LinkedCoinGroupTrade, frozenset[bytes]] | None:
    offer = root.offer
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
    last_reject_index = max(
        (index for index, item in enumerate(evidence) if _signal(item.text) == "REJECT"),
        default=-1,
    )
    active_evidence = evidence[last_reject_index + 1 :]
    quantity: int | None = None
    price: int | None = None
    price_is_safe = True
    price_is_ambiguous = False
    settlement = offer.settlement_term
    settlement_changed = False
    post_reject_has_terms = False
    for evidence_message in active_evidence:
        normalized = _text(evidence_message.text)
        candidate_quantity, spans = _quantity_and_spans(
            normalized,
            offer_price=offer.price_project_thousand_toman,
        )
        if candidate_quantity is not None:
            quantity = candidate_quantity
            post_reject_has_terms = True
        candidate_price, price_seen, candidate_price_is_safe = _negotiated_price_result(
            normalized,
            offer_price=offer.price_project_thousand_toman,
            quantity_spans=spans,
            commodity_code=offer.commodity_code,
        )
        if price_seen:
            post_reject_has_terms = True
            price = candidate_price
            price_is_ambiguous = candidate_price is None
            price_is_safe = candidate_price_is_safe
        explicit_cash, explicit_tomorrow = coin_group_settlement_markers(normalized)
        if explicit_cash and explicit_tomorrow:
            return None
        if explicit_cash or explicit_tomorrow:
            post_reject_has_terms = True
            candidate_settlement = "CASH" if explicit_cash else "TOMORROW"
            settlement_changed = candidate_settlement != offer.settlement_term
            settlement = candidate_settlement
    if last_reject_index >= 0 and not post_reject_has_terms:
        # A bare acceptance cannot revive terms that a participant already
        # cancelled.  A new explicit quantity/price/book proposal is required.
        return None
    if price_is_ambiguous:
        return None
    quantity_was_negotiated = quantity is not None
    if quantity is None:
        if kind == "OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE":
            return None
        quantity = offer.quantity
    if price is None:
        price = offer.price_project_thousand_toman
    is_aggregate = kind == "OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE" and bool(
        _CUMULATIVE.search(_text(message.text))
    )
    if offer.quality_state == "REJECTED":
        quality = "REJECTED"
        resolution_reason = "ROOT_OFFER_REJECTED:" + offer.resolution_reason
    elif offer.quality_state != "ELIGIBLE" or offer.commodity_code is None:
        quality = "PENDING_REVIEW"
        resolution_reason = "ROOT_OFFER_NOT_MODEL_ELIGIBLE:" + offer.resolution_reason
    elif is_aggregate and quantity > offer.quantity:
        quality = "PENDING_REVIEW"
        resolution_reason = "AGGREGATE_QUANTITY_EXCEEDS_ROOT_OFFER"
    else:
        quality = "ELIGIBLE"
        resolution_reason = "STRUCTURALLY_LINKED_CONFIRMED_TRADE"
    gates: list[str] = []
    if kind == "COUNTERPARTY_EXPLICIT_REPLY_TRADE":
        gates.append("COUNTERPARTY_DECLARATION_REQUIRES_OFFERER_CONFIRMATION")
    if not price_is_safe:
        gates.append("NEGOTIATED_PRICE_OUTSIDE_SAFE_RELATIVE_DELTA")
    if settlement_changed:
        gates.append("NEGOTIATED_SETTLEMENT_REQUIRES_SAME_BOOK_VALIDATION")
    if quality != "REJECTED" and gates:
        quality = "PENDING_REVIEW"
        for gate in gates:
            resolution_reason = _append_gate_reason(resolution_reason, gate)
    return LinkedCoinGroupTrade(
        group_number=root.group_number,
        root_offer_message_id=root.message_id,
        confirmation_message_id=message.message_id,
        commodity_code=offer.commodity_code,
        price_project_thousand_toman=price,
        quantity=quantity,
        side=offer.side,
        settlement_term=settlement,
        trade_form=offer.trade_form,
        event_time_utc=message.event_time_utc,
        available_at_utc=message.available_at_utc,
        is_conditional=offer.is_conditional,
        quality_state=quality,
        confirmation_kind=kind,
        is_aggregate=is_aggregate,
        quantity_was_negotiated=quantity_was_negotiated,
        resolution_reason=resolution_reason,
    ), frozenset(participants)


def _has_later_participant_rejection(
    candidate: _TradeCandidate,
    *,
    children: Mapping[tuple[int, int], tuple[StagedCoinGroupMessage, ...]],
) -> bool:
    """A participant cancellation after confirmation gates the prior fill."""

    trade = candidate.trade
    pending = list(children.get((trade.group_number, trade.confirmation_message_id), ()))
    seen: set[int] = set()
    while pending:
        message = pending.pop()
        if message.message_id in seen:
            continue
        seen.add(message.message_id)
        age = _age_seconds(message.event_time_utc, trade.event_time_utc)
        if age < 0 or age > MAX_REPLY_AGE_SECONDS:
            continue
        if (
            message.sender_digest in candidate.participant_digests
            and _signal(message.text) == "REJECT"
        ):
            return True
        pending.extend(children.get((message.group_number, message.message_id), ()))
    return False


def _trade_from_sibling_confirmation(
    message: StagedCoinGroupMessage,
    *,
    root: CoinGroupOfferRecord,
    messages: Mapping[tuple[int, int], StagedCoinGroupMessage],
    children: Mapping[tuple[int, int], tuple[StagedCoinGroupMessage, ...]],
) -> tuple[LinkedCoinGroupTrade, frozenset[bytes]] | None:
    """Pair one unambiguous direct proposal with an owner's direct acceptance.

    Telegram users sometimes reply independently to the root instead of to
    each other.  This is accepted only when exactly one earlier counterparty
    sibling contains participation or negotiated terms.  Multiple users or
    multiple candidate branches remain unlinked.
    """

    if (
        root.offerer_digest is None
        or message.sender_digest != root.offerer_digest
        or message.reply_to_message_id != root.message_id
        or _signal(message.text) not in {"ACCEPT", "EXPLICIT_TRADE"}
        or _CUMULATIVE.search(_text(message.text))
    ):
        return None
    candidates: list[StagedCoinGroupMessage] = []
    for sibling in children.get((root.group_number, root.message_id), ()):
        if (
            sibling.message_id == message.message_id
            or sibling.sender_digest is None
            or sibling.sender_digest == root.offerer_digest
            or sibling.event_time_utc >= message.event_time_utc
            or not 0
            <= _age_seconds(message.event_time_utc, sibling.event_time_utc)
            <= 5 * 60
        ):
            continue
        signal = _signal(sibling.text)
        normalized = _text(sibling.text)
        quantity, spans = _quantity_and_spans(
            normalized,
            offer_price=root.offer.price_project_thousand_toman,
        )
        price, price_seen, _safe = _negotiated_price_result(
            normalized,
            offer_price=root.offer.price_project_thousand_toman,
            quantity_spans=spans,
            commodity_code=root.offer.commodity_code,
        )
        if signal in {
            "ACCEPT",
            "EXPLICIT_TRADE",
            "BUY_REQUEST",
            "SELL_REQUEST",
        } or quantity is not None or price is not None or price_seen:
            candidates.append(sibling)
    if len(candidates) != 1:
        return None
    proposal = candidates[0]
    synthetic_confirmation = replace(
        message,
        reply_to_message_id=proposal.message_id,
    )
    synthetic_messages = dict(messages)
    synthetic_messages[
        (message.group_number, message.message_id)
    ] = synthetic_confirmation
    linked = _trade_from_confirmation(
        synthetic_confirmation,
        root=root,
        messages=synthetic_messages,
    )
    if linked is None:
        return None
    trade, participants = linked
    return replace(
        trade,
        confirmation_kind="SIBLING_RECIPROCAL_OFFERER_CONFIRMATION",
    ), participants


def link_coin_group_trades(
    messages: Iterable[StagedCoinGroupMessage],
    offers: Iterable[CoinGroupOfferRecord],
) -> list[LinkedCoinGroupTrade]:
    """Link only confirmation chains; repeated fills cannot overrun an offer."""

    message_by_key = _message_map(messages)
    offer_by_key = {
        (offer.group_number, offer.message_id): offer
        for offer in offers
    }
    children: dict[tuple[int, int], list[StagedCoinGroupMessage]] = {}
    for message in message_by_key.values():
        if message.reply_to_message_id is not None:
            children.setdefault(
                (message.group_number, message.reply_to_message_id), []
            ).append(message)
    frozen_children = {key: tuple(value) for key, value in children.items()}
    candidates: list[_TradeCandidate] = []
    for message in sorted(message_by_key.values(), key=lambda item: (item.event_time_utc, item.message_id)):
        if message.reply_to_message_id is None:
            continue
        root = _root_offer(message, messages=message_by_key, offers=offer_by_key)
        if root is None or message.message_id == root.message_id:
            continue
        linked = _trade_from_sibling_confirmation(
            message,
            root=root,
            messages=message_by_key,
            children=frozen_children,
        ) or _trade_from_confirmation(message, root=root, messages=message_by_key)
        if linked is None:
            continue
        trade, participants = linked
        branch_ids = frozenset(
            item.message_id
            for item in _branch_from_root(message, root=root, messages=message_by_key)
        )
        candidates.append(_TradeCandidate(trade, branch_ids, participants))

    # One conversational branch can contain both a counterparty declaration
    # and the owner's later confirmation.  They are two pieces of evidence for
    # one fill, not two fills.  Prefer owner evidence, then the later terminal
    # confirmation; sibling reply branches remain independent.
    authority = {
        "OWNER_EXPLICIT_AGGREGATE_REPLY_TRADE": 3,
        "RECIPROCAL_OFFERER_CONFIRMATION": 3,
        "SIBLING_RECIPROCAL_OFFERER_CONFIRMATION": 3,
        "COUNTERPARTY_EXPLICIT_REPLY_TRADE": 2,
        "RECIPROCAL_COUNTERPARTY_CONFIRMATION": 2,
    }
    selected: list[_TradeCandidate] = []
    for candidate in candidates:
        superseded = False
        for other in candidates:
            if other is candidate:
                continue
            same_root = (
                other.trade.group_number == candidate.trade.group_number
                and other.trade.root_offer_message_id == candidate.trade.root_offer_message_id
            )
            same_reply_path = (
                candidate.trade.confirmation_message_id in other.branch_ids
                or other.trade.confirmation_message_id in candidate.branch_ids
            )
            if not same_root or not same_reply_path:
                continue
            candidate_rank = (
                authority.get(candidate.trade.confirmation_kind, 0),
                candidate.trade.event_time_utc,
                candidate.trade.confirmation_message_id,
            )
            other_rank = (
                authority.get(other.trade.confirmation_kind, 0),
                other.trade.event_time_utc,
                other.trade.confirmation_message_id,
            )
            if other_rank > candidate_rank:
                superseded = True
                break
        if not superseded:
            selected.append(candidate)

    filled: dict[tuple[int, int], int] = {}
    trades: list[LinkedCoinGroupTrade] = []
    for candidate in sorted(
        selected,
        key=lambda item: (
            item.trade.event_time_utc,
            item.trade.confirmation_message_id,
        ),
    ):
        trade = candidate.trade
        if _has_later_participant_rejection(
            candidate,
            children=frozen_children,
        ):
            trade = replace(
                trade,
                quality_state=(
                    "REJECTED" if trade.quality_state == "REJECTED" else "PENDING_REVIEW"
                ),
                resolution_reason=_append_gate_reason(
                    trade.resolution_reason,
                    "PARTICIPANT_REJECTION_AFTER_CONFIRMATION",
                ),
            )
        root_key = (trade.group_number, trade.root_offer_message_id)
        root = offer_by_key[root_key]
        if not trade.is_aggregate:
            already_filled = filled.get(root_key, 0)
            remaining = root.offer.quantity - already_filled
            # A reciprocal branch can explicitly amend the root quantity.  A
            # seller advertising 10 and then accepting a counterparty's
            # explicit request for 15 has agreed a 15-unit fill; it is not an
            # overfill merely because the negotiated final quantity exceeds
            # the original advert.  This exception is deliberately limited to
            # the first fill.  Independent later branches still cannot consume
            # more than the unamended root remainder.
            explicit_first_fill_amendment = (
                already_filled == 0
                and trade.quantity_was_negotiated
                and trade.confirmation_kind
                in {
                    "RECIPROCAL_OFFERER_CONFIRMATION",
                    "SIBLING_RECIPROCAL_OFFERER_CONFIRMATION",
                    "RECIPROCAL_COUNTERPARTY_CONFIRMATION",
                }
            )
            if trade.quantity > remaining and not explicit_first_fill_amendment:
                trades.append(
                    replace(
                        trade,
                        quality_state=(
                            "REJECTED"
                            if trade.quality_state == "REJECTED"
                            else "PENDING_REVIEW"
                        ),
                        resolution_reason=_append_gate_reason(
                            trade.resolution_reason,
                            "NON_AGGREGATE_FILL_EXCEEDS_REMAINING_ROOT_QUANTITY",
                        ),
                    )
                )
                continue
            filled[root_key] = already_filled + trade.quantity
        trades.append(trade)
    return trades


def coin_group_trade_observations(
    trades: Iterable[LinkedCoinGroupTrade],
    *,
    resolution_available_at_utc: str | None = None,
) -> list[MarketObservation]:
    """Project linked trades without raw message/reply/sender identifiers."""

    resolution_available = (
        normalize_utc(
            resolution_available_at_utc,
            field_name="coin_group_trade_resolution_available_at_utc",
        )
        if resolution_available_at_utc is not None
        else None
    )
    observations: list[MarketObservation] = []
    for trade in trades:
        commodity = trade.commodity_code or "UNRESOLVED"
        trade_available = normalize_utc(
            trade.available_at_utc,
            field_name="coin_group_trade_available_at_utc",
        )
        if resolution_available is not None and resolution_available < trade_available:
            raise ValueError("coin_group_trade_resolution_available_before_confirmation")
        observations.append(
            MarketObservation(
                event_key=derive_event_key(
                    "coin-group-trade-v1", trade.group_number, trade.root_offer_message_id, trade.confirmation_message_id
                ),
                source_code=f"GROUP_{trade.group_number}",
                source_family="GROUP",
                event_time_utc=trade.event_time_utc,
                available_at_utc=resolution_available or trade_available,
                instrument="COIN_" + commodity,
                market_label="GROUP_COIN_" + commodity,
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
                quality_policy_version="coin-group-trade-link-v2",
                is_conditional=trade.is_conditional,
                attributes={
                    "group_number": trade.group_number,
                    "confirmation_kind": trade.confirmation_kind,
                    "is_aggregate": trade.is_aggregate,
                    "quantity_was_negotiated": trade.quantity_was_negotiated,
                    "root_offer_event_key": derive_event_key(
                        "coin-group-offer-v1",
                        trade.group_number,
                        trade.root_offer_message_id,
                        0,
                    ).hex(),
                    "resolution_reason": trade.resolution_reason,
                },
            )
        )
    return observations
