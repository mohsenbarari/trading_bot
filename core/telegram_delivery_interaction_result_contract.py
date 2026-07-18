"""Pure result/dependency contract for queued Bot interactions.

Interactive handlers often use the ``Message.message_id`` returned by
``sendMessage`` as the target of a later edit or as the current reply-keyboard
anchor.  Queue mode cannot fabricate that id or block the handler until the
worker runs.  This module defines the fail-closed contract used by the durable
receipt layer added in the next Stage 3 slice.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Mapping

from core.telegram_delivery_queue_contract import (
    FINAL_DELIVERY_STATES,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFlowExit,
    authenticated_keyboard_policy,
)


class TelegramInteractionResultRequirement(str, Enum):
    NONE = "none"
    CAPTURE_MESSAGE_ID = "capture_message_id"


class TelegramInteractionAnchorEffect(str, Enum):
    NONE = "none"
    PRESERVE_CURRENT = "preserve_current"
    SET_CURRENT = "set_current"


class TelegramInteractionTargetKind(str, Enum):
    KNOWN_MESSAGE = "known_message"
    DELIVERY_RESULT = "delivery_result"


class TelegramInteractionDependencyOutcome(str, Enum):
    READY = "ready"
    WAIT_DEPENDENCY = "wait_dependency"
    SUPERSEDED = "superseded"
    QUARANTINED = "quarantined"


class TelegramInteractionResultOutcome(str, Enum):
    APPLIED = "applied"
    APPLIED_STALE_ANCHOR = "applied_stale_anchor"
    WAIT_DELIVERY = "wait_delivery"
    WAIT_RECONCILIATION = "wait_reconciliation"
    TERMINAL_NO_RESULT = "terminal_no_result"
    QUARANTINED = "quarantined"


_SUPPORTED_INTERACTION_METHODS = frozenset(
    {"sendMessage", "editMessageText", "editMessageReplyMarkup"}
)
_UNRESOLVED_DELIVERY_STATES = frozenset(
    {
        TelegramDeliveryState.AMBIGUOUS,
        TelegramDeliveryState.AMBIGUOUS_UNRESOLVED,
        TelegramDeliveryState.PENDING_RECONCILE,
    }
)


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _nonzero_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized != 0 else None


def _nonempty(value: object, *, max_length: int) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > max_length:
        return None
    return normalized


def _delivery_state(value: TelegramDeliveryState | str) -> TelegramDeliveryState:
    try:
        return TelegramDeliveryState(str(getattr(value, "value", value)))
    except ValueError as exc:
        raise ValueError("telegram_interaction_delivery_state_invalid") from exc


@dataclass(frozen=True, slots=True)
class TelegramInteractionResultContract:
    logical_message_key: str
    method: str
    destination_class: TelegramDestinationClass
    result_requirement: TelegramInteractionResultRequirement
    anchor_effect: TelegramInteractionAnchorEffect
    anchor_generation: int | None
    authenticated: bool
    temporary_context_keyboard: bool
    flow_exit: TelegramFlowExit | None
    persistent_menu_present: bool


@dataclass(frozen=True, slots=True)
class TelegramInteractionTargetReference:
    kind: TelegramInteractionTargetKind
    chat_id: int
    message_id: int | None = None
    source_receipt_id: int | None = None


@dataclass(frozen=True, slots=True)
class TelegramInteractionDependencyDecision:
    outcome: TelegramInteractionDependencyOutcome
    chat_id: int
    message_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramInteractionResultDecision:
    outcome: TelegramInteractionResultOutcome
    telegram_message_id: int | None = None
    activate_anchor: bool = False
    reason: str | None = None


_SERIALIZED_CONTRACT_KEYS = frozenset(
    {
        "anchor_effect",
        "anchor_generation",
        "authenticated",
        "destination_class",
        "flow_exit",
        "logical_message_key",
        "method",
        "persistent_menu_present",
        "result_requirement",
        "temporary_context_keyboard",
    }
)


def build_interaction_result_contract(
    *,
    logical_message_key: object,
    method: object,
    destination_class: TelegramDestinationClass | str,
    result_requirement: TelegramInteractionResultRequirement | str,
    anchor_effect: TelegramInteractionAnchorEffect | str = TelegramInteractionAnchorEffect.NONE,
    anchor_generation: object = None,
    authenticated: bool,
    temporary_context_keyboard: bool = False,
    flow_exit: TelegramFlowExit | str | None = None,
    persistent_menu_present: bool = False,
) -> TelegramInteractionResultContract:
    """Validate one immutable send/edit result contract.

    An authenticated temporary flow exit must carry the persistent menu in the
    queued payload.  A current anchor is only created by a private
    ``sendMessage`` whose provider message id will be captured.  There is no
    delete-active-anchor option by design.
    """
    logical_key = _nonempty(logical_message_key, max_length=192)
    if logical_key is None:
        raise ValueError("telegram_interaction_logical_message_key_invalid")
    normalized_method = str(method or "").strip()
    if normalized_method not in _SUPPORTED_INTERACTION_METHODS:
        raise ValueError("telegram_interaction_method_unsupported")
    try:
        normalized_destination = TelegramDestinationClass(
            str(getattr(destination_class, "value", destination_class))
        )
        normalized_requirement = TelegramInteractionResultRequirement(
            str(getattr(result_requirement, "value", result_requirement))
        )
        normalized_anchor = TelegramInteractionAnchorEffect(
            str(getattr(anchor_effect, "value", anchor_effect))
        )
        normalized_exit = (
            None
            if flow_exit is None
            else TelegramFlowExit(str(getattr(flow_exit, "value", flow_exit)))
        )
    except ValueError as exc:
        raise ValueError("telegram_interaction_result_contract_enum_invalid") from exc

    if normalized_method != "sendMessage" and (
        normalized_requirement
        == TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
    ):
        raise ValueError("telegram_interaction_edit_cannot_capture_new_message_id")

    normalized_generation = _positive_int(anchor_generation)
    if normalized_anchor == TelegramInteractionAnchorEffect.SET_CURRENT:
        if normalized_method != "sendMessage":
            raise ValueError("telegram_interaction_anchor_requires_send")
        if normalized_destination != TelegramDestinationClass.PRIVATE:
            raise ValueError("telegram_interaction_anchor_requires_private_destination")
        if not authenticated:
            raise ValueError("telegram_interaction_anchor_requires_authenticated_user")
        if (
            normalized_requirement
            != TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
        ):
            raise ValueError("telegram_interaction_anchor_requires_message_result")
        if normalized_generation is None:
            raise ValueError("telegram_interaction_anchor_generation_invalid")
        if not persistent_menu_present:
            raise ValueError("telegram_interaction_anchor_requires_persistent_menu")
    elif normalized_generation is not None:
        raise ValueError("telegram_interaction_anchor_generation_without_set")
    elif authenticated and normalized_method == "sendMessage" and persistent_menu_present:
        raise ValueError("telegram_interaction_persistent_menu_requires_anchor")

    if normalized_exit is not None:
        keyboard = authenticated_keyboard_policy(
            authenticated=bool(authenticated),
            temporary_context_keyboard=bool(temporary_context_keyboard),
            flow_exit=normalized_exit,
            business_inline_keyboard_stale=False,
        )
        if keyboard.restore_persistent_main_menu and not persistent_menu_present:
            raise ValueError("telegram_interaction_flow_exit_missing_persistent_menu")

    return TelegramInteractionResultContract(
        logical_message_key=logical_key,
        method=normalized_method,
        destination_class=normalized_destination,
        result_requirement=normalized_requirement,
        anchor_effect=normalized_anchor,
        anchor_generation=normalized_generation,
        authenticated=bool(authenticated),
        temporary_context_keyboard=bool(temporary_context_keyboard),
        flow_exit=normalized_exit,
        persistent_menu_present=bool(persistent_menu_present),
    )


def serialize_interaction_result_contract(
    contract: TelegramInteractionResultContract,
) -> dict[str, object]:
    return {
        "anchor_effect": contract.anchor_effect.value,
        "anchor_generation": contract.anchor_generation,
        "authenticated": contract.authenticated,
        "destination_class": contract.destination_class.value,
        "flow_exit": contract.flow_exit.value if contract.flow_exit else None,
        "logical_message_key": contract.logical_message_key,
        "method": contract.method,
        "persistent_menu_present": contract.persistent_menu_present,
        "result_requirement": contract.result_requirement.value,
        "temporary_context_keyboard": contract.temporary_context_keyboard,
    }


def parse_interaction_result_contract(
    payload: Mapping[str, object] | object,
) -> TelegramInteractionResultContract:
    if not isinstance(payload, Mapping) or set(payload) != _SERIALIZED_CONTRACT_KEYS:
        raise ValueError("telegram_interaction_serialized_contract_invalid")
    for key in (
        "authenticated",
        "persistent_menu_present",
        "temporary_context_keyboard",
    ):
        if not isinstance(payload.get(key), bool):
            raise ValueError("telegram_interaction_serialized_contract_boolean_invalid")
    return build_interaction_result_contract(
        logical_message_key=payload.get("logical_message_key"),
        method=payload.get("method"),
        destination_class=payload.get("destination_class"),
        result_requirement=payload.get("result_requirement"),
        anchor_effect=payload.get("anchor_effect"),
        anchor_generation=payload.get("anchor_generation"),
        authenticated=bool(payload["authenticated"]),
        temporary_context_keyboard=bool(payload["temporary_context_keyboard"]),
        flow_exit=payload.get("flow_exit"),
        persistent_menu_present=bool(payload["persistent_menu_present"]),
    )


def build_known_message_target(
    *, chat_id: object, message_id: object
) -> TelegramInteractionTargetReference:
    normalized_chat = _nonzero_int(chat_id)
    normalized_message = _positive_int(message_id)
    if normalized_chat is None or normalized_message is None:
        raise ValueError("telegram_interaction_known_target_invalid")
    return TelegramInteractionTargetReference(
        kind=TelegramInteractionTargetKind.KNOWN_MESSAGE,
        chat_id=normalized_chat,
        message_id=normalized_message,
    )


def build_delivery_result_target(
    *, chat_id: object, source_receipt_id: object
) -> TelegramInteractionTargetReference:
    normalized_chat = _nonzero_int(chat_id)
    normalized_receipt = _positive_int(source_receipt_id)
    if normalized_chat is None or normalized_receipt is None:
        raise ValueError("telegram_interaction_result_target_invalid")
    return TelegramInteractionTargetReference(
        kind=TelegramInteractionTargetKind.DELIVERY_RESULT,
        chat_id=normalized_chat,
        source_receipt_id=normalized_receipt,
    )


def resolve_interaction_target(
    reference: TelegramInteractionTargetReference,
    *,
    source_state: TelegramDeliveryState | str | None = None,
    source_telegram_message_id: object = None,
) -> TelegramInteractionDependencyDecision:
    """Resolve a known target or wait fail-closed for its send result."""
    if reference.kind == TelegramInteractionTargetKind.KNOWN_MESSAGE:
        if reference.message_id is None:
            return TelegramInteractionDependencyDecision(
                TelegramInteractionDependencyOutcome.QUARANTINED,
                reference.chat_id,
                reason="telegram_interaction_known_target_missing_message_id",
            )
        return TelegramInteractionDependencyDecision(
            TelegramInteractionDependencyOutcome.READY,
            reference.chat_id,
            message_id=reference.message_id,
        )

    if reference.kind != TelegramInteractionTargetKind.DELIVERY_RESULT:
        return TelegramInteractionDependencyDecision(
            TelegramInteractionDependencyOutcome.QUARANTINED,
            reference.chat_id,
            reason="telegram_interaction_target_kind_unknown",
        )
    if source_state is None:
        return TelegramInteractionDependencyDecision(
            TelegramInteractionDependencyOutcome.QUARANTINED,
            reference.chat_id,
            reason="telegram_interaction_source_state_missing",
        )

    state = _delivery_state(source_state)
    message_id = _positive_int(source_telegram_message_id)
    if state == TelegramDeliveryState.SENT:
        if message_id is None:
            return TelegramInteractionDependencyDecision(
                TelegramInteractionDependencyOutcome.QUARANTINED,
                reference.chat_id,
                reason="telegram_interaction_sent_source_missing_message_id",
            )
        return TelegramInteractionDependencyDecision(
            TelegramInteractionDependencyOutcome.READY,
            reference.chat_id,
            message_id=message_id,
        )
    if state in _UNRESOLVED_DELIVERY_STATES or state not in FINAL_DELIVERY_STATES:
        return TelegramInteractionDependencyDecision(
            TelegramInteractionDependencyOutcome.WAIT_DEPENDENCY,
            reference.chat_id,
            reason="telegram_interaction_source_not_resolved",
        )
    if state == TelegramDeliveryState.QUARANTINED:
        return TelegramInteractionDependencyDecision(
            TelegramInteractionDependencyOutcome.QUARANTINED,
            reference.chat_id,
            reason="telegram_interaction_source_quarantined",
        )
    return TelegramInteractionDependencyDecision(
        TelegramInteractionDependencyOutcome.SUPERSEDED,
        reference.chat_id,
        reason="telegram_interaction_source_terminal_without_message",
    )


def apply_interaction_delivery_result(
    contract: TelegramInteractionResultContract,
    *,
    delivery_state: TelegramDeliveryState | str,
    telegram_message_id: object = None,
    desired_anchor_generation: object = None,
) -> TelegramInteractionResultDecision:
    """Translate a queue result into receipt/anchor feedback.

    Ambiguous sends never activate an anchor.  A late successful send records
    its real message id, but cannot replace a newer desired anchor generation.
    """
    state = _delivery_state(delivery_state)
    message_id = _positive_int(telegram_message_id)

    if state in _UNRESOLVED_DELIVERY_STATES:
        return TelegramInteractionResultDecision(
            TelegramInteractionResultOutcome.WAIT_RECONCILIATION,
            reason="telegram_interaction_send_result_unresolved",
        )
    if state not in FINAL_DELIVERY_STATES:
        return TelegramInteractionResultDecision(
            TelegramInteractionResultOutcome.WAIT_DELIVERY,
            reason="telegram_interaction_delivery_not_terminal",
        )
    if state != TelegramDeliveryState.SENT:
        if state == TelegramDeliveryState.QUARANTINED:
            return TelegramInteractionResultDecision(
                TelegramInteractionResultOutcome.QUARANTINED,
                reason="telegram_interaction_delivery_quarantined",
            )
        return TelegramInteractionResultDecision(
            TelegramInteractionResultOutcome.TERMINAL_NO_RESULT,
            reason="telegram_interaction_delivery_terminal_without_result",
        )

    if (
        contract.result_requirement
        == TelegramInteractionResultRequirement.CAPTURE_MESSAGE_ID
        and message_id is None
    ):
        return TelegramInteractionResultDecision(
            TelegramInteractionResultOutcome.QUARANTINED,
            reason="telegram_interaction_required_message_id_missing",
        )

    if contract.anchor_effect != TelegramInteractionAnchorEffect.SET_CURRENT:
        return TelegramInteractionResultDecision(
            TelegramInteractionResultOutcome.APPLIED,
            telegram_message_id=message_id,
        )

    desired_generation = _positive_int(desired_anchor_generation)
    if desired_generation is None:
        return TelegramInteractionResultDecision(
            TelegramInteractionResultOutcome.QUARANTINED,
            telegram_message_id=message_id,
            reason="telegram_interaction_desired_anchor_generation_missing",
        )
    if desired_generation != contract.anchor_generation:
        return TelegramInteractionResultDecision(
            TelegramInteractionResultOutcome.APPLIED_STALE_ANCHOR,
            telegram_message_id=message_id,
            activate_anchor=False,
            reason="telegram_interaction_anchor_generation_superseded",
        )
    return TelegramInteractionResultDecision(
        TelegramInteractionResultOutcome.APPLIED,
        telegram_message_id=message_id,
        activate_anchor=True,
    )
