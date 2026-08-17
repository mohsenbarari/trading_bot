"""Bot callbacks for overtime owner approve/reject and requester cancel."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from aiogram import F, Router, types, Bot
from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from api.deps import EffectiveOwnerActor
from api.routers.trades import (
    TradeCreate,
    _execute_trade_authoritatively_with_transient_retry,
)
from bot.overtime_request_status import (
    edit_owner_overtime_approval_message,
    edit_requester_overtime_status,
    empty_inline_keyboard,
    owner_terminal_text_for_reason,
    terminal_requester_status_text,
)
from bot.telegram_callback_answer import answer_callback_query_via_runtime
from bot.telegram_interaction_message import edit_callback_message_via_runtime
from core.db import AsyncSessionLocal
from core.offer_lifecycle import read_normal_lifetime_minutes
from core.offer_overtime_request_forwarding import forward_overtime_requester_cancel
from core.offer_overtime_bot_copy import (
    M15_REQUESTER_STATUS_CANCELLED,
    M29_OWNER_STATUS_APPROVED,
    M30_OWNER_STATUS_REJECTED,
    M32_OWNER_DECISION_EXPIRED_MESSAGE,
    M33_OWNER_ALREADY_TERMINAL_MESSAGE,
    M34_OWNER_NOT_OWNER_MESSAGE,
    M37_OWNER_REVALIDATION_FAILED_MESSAGE,
)
from core.server_routing import current_server, normalize_server
from core.services.offer_overtime_request_service import (
    OvertimeRequestError,
    OvertimeRequestErrorCode,
    cancel_by_requester,
    claim_owner_approval,
    load_overtime_request_by_public_id,
    reject_by_owner,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    parse_overtime_owner_approval_callback_data,
)
from core.telegram_delivery_overtime_requester_status_contract import (
    parse_overtime_requester_cancel_callback_data,
)
from core.trading_settings import get_trading_settings_async
from models.offer_request import OfferRequestWorkflow
from models.user import User

logger = logging.getLogger(__name__)
router = Router()


async def _answer(
    callback: types.CallbackQuery,
    text: str,
    *,
    alert: bool = False,
) -> None:
    await answer_callback_query_via_runtime(
        callback,
        text,
        show_alert=alert,
    )


@router.callback_query(F.data.startswith("otc:"))
async def handle_overtime_requester_cancel(
    callback: types.CallbackQuery,
    user: Optional[User],
    bot: Bot,
):
    if not user:
        await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
        return
    public_id = parse_overtime_requester_cancel_callback_data(callback.data)
    if public_id is None:
        await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
        return

    async with AsyncSessionLocal() as session:
        ledger = await load_overtime_request_by_public_id(
            session, public_id, for_update=False
        )
        if ledger is None or getattr(ledger, "workflow_kind", None) != OfferRequestWorkflow.OVERTIME:
            await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
            return
        if int(getattr(ledger, "requester_user_id", 0) or 0) != int(user.id):
            await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
            return
        home = normalize_server(getattr(ledger, "request_home_server", None), current_server())
        if home != current_server():
            source = normalize_server(
                getattr(ledger, "request_source_server", None),
                current_server(),
            )
            if source != current_server():
                await _answer(
                    callback,
                    "این درخواست باید از همان محلی که ثبت شده لغو شود.",
                    alert=True,
                )
                return
            await session.rollback()
            forwarded_status, forwarded_body = await forward_overtime_requester_cancel(
                home,
                {
                    "request_public_id": public_id,
                    "requester_user_id": int(user.id),
                    "source_server": current_server(),
                },
            )
            if forwarded_status >= 400:
                detail = (
                    forwarded_body.get("detail")
                    if isinstance(forwarded_body, dict)
                    else None
                ) or "لغو درخواست انجام نشد. لطفاً دوباره تلاش کنید."
                if isinstance(detail, dict):
                    detail = detail.get("message") or "لغو درخواست انجام نشد. لطفاً دوباره تلاش کنید."
                await _answer(callback, str(detail), alert=True)
                return
            await edit_callback_message_via_runtime(
                callback,
                user,
                M15_REQUESTER_STATUS_CANCELLED,
                source_key="ot-cancel-edit-forwarded",
                reply_markup=empty_inline_keyboard(),
                session=session,
                commit=False,
            )
            await session.commit()
            await _answer(callback, M15_REQUESTER_STATUS_CANCELLED, alert=False)
            return
        ledger = await load_overtime_request_by_public_id(
            session, public_id, for_update=True
        )
        if ledger is None or getattr(ledger, "workflow_kind", None) != OfferRequestWorkflow.OVERTIME:
            await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
            return
        ts = await get_trading_settings_async()
        try:
            await cancel_by_requester(
                session,
                ledger,
                requester_user_id=int(user.id),
                now=datetime.utcnow(),
                normal_lifetime_minutes=read_normal_lifetime_minutes(ts),
            )
        except OvertimeRequestError as exc:
            if exc.code in {
                OvertimeRequestErrorCode.NOT_REQUESTER,
                OvertimeRequestErrorCode.ALREADY_TERMINAL,
            }:
                await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
                return
            await _answer(callback, str(exc.detail), alert=True)
            return

        await edit_callback_message_via_runtime(
            callback,
            user,
            M15_REQUESTER_STATUS_CANCELLED,
            source_key="ot-cancel-edit",
            reply_markup=empty_inline_keyboard(),
            session=session,
            commit=False,
        )
        owner_id = getattr(ledger, "offer_owner_user_id", None)
        if owner_id is not None:
            owner = await session.get(User, int(owner_id))
            if owner is not None:
                await edit_owner_overtime_approval_message(
                    session=session,
                    owner=owner,
                    ledger=ledger,
                    text=owner_terminal_text_for_reason(),
                    bot=bot,
                )
        await session.commit()
    await _answer(callback, M15_REQUESTER_STATUS_CANCELLED, alert=False)


@router.callback_query(F.data.startswith("ota:"))
async def handle_overtime_owner_decision(
    callback: types.CallbackQuery,
    user: Optional[User],
    bot: Bot,
):
    if not user:
        await _answer(callback, M34_OWNER_NOT_OWNER_MESSAGE, alert=True)
        return
    parsed = parse_overtime_owner_approval_callback_data(callback.data)
    if parsed is None:
        await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
        return
    public_id, decision = parsed

    async with AsyncSessionLocal() as session:
        ledger = await load_overtime_request_by_public_id(
            session, public_id, for_update=True
        )
        if ledger is None or getattr(ledger, "workflow_kind", None) != OfferRequestWorkflow.OVERTIME:
            await _answer(callback, M33_OWNER_ALREADY_TERMINAL_MESSAGE, alert=True)
            return
        home = normalize_server(getattr(ledger, "request_home_server", None), current_server())
        if home != current_server():
            await _answer(
                callback,
                "این درخواست فقط روی سرور مرجع لفظ قابل تصمیم است.",
                alert=True,
            )
            return

        ts = await get_trading_settings_async()
        normal_minutes = read_normal_lifetime_minutes(ts)
        requester = await session.get(User, int(ledger.requester_user_id))

        if decision == "reject":
            try:
                await reject_by_owner(
                    session,
                    ledger,
                    decided_by_user_id=int(user.id),
                    now=datetime.utcnow(),
                    normal_lifetime_minutes=normal_minutes,
                )
            except OvertimeRequestError as exc:
                await _answer_owner_error(callback, exc)
                return
            await edit_callback_message_via_runtime(
                callback,
                user,
                M30_OWNER_STATUS_REJECTED,
                source_key="ot-owner-reject",
                reply_markup=empty_inline_keyboard(),
                session=session,
                commit=False,
            )
            if requester is not None:
                await edit_requester_overtime_status(
                    session=session,
                    user=requester,
                    ledger=ledger,
                    text=terminal_requester_status_text(),
                    keep_cancel=False,
                    bot=bot,
                )
            await session.commit()
            await _answer(callback, M30_OWNER_STATUS_REJECTED, alert=False)
            return

        try:
            await claim_owner_approval(
                ledger,
                decided_by_user_id=int(user.id),
                now=datetime.utcnow(),
            )
        except OvertimeRequestError as exc:
            await _answer_owner_error(callback, exc)
            return

        if requester is None:
            await _answer(callback, M37_OWNER_REVALIDATION_FAILED_MESSAGE, alert=True)
            return

        background_tasks = BackgroundTasks()
        trade_data = TradeCreate(
            offer_id=int(getattr(ledger, "local_offer_id")),
            offer_public_id=getattr(ledger, "offer_public_id", None),
            quantity=int(getattr(ledger, "requested_quantity")),
            idempotency_key=getattr(ledger, "idempotency_key", None),
        )
        actor_id = getattr(ledger, "actor_user_id", None) or requester.id
        actor_user = (
            requester
            if int(actor_id) == int(requester.id)
            else await session.get(User, int(actor_id))
        ) or requester
        try:
            result = await _execute_trade_authoritatively_with_transient_retry(
                trade_data=trade_data,
                background_tasks=background_tasks,
                db=session,
                context=EffectiveOwnerActor(
                    owner_user=requester,
                    actor_user=actor_user,
                    relation=None,
                    is_accountant_context=False,
                ),
                edge_received_at=getattr(ledger, "received_at", None) or datetime.utcnow(),
                request_source_surface=getattr(ledger, "request_source_surface", None),
                request_source_server=getattr(
                    ledger, "request_source_server", current_server()
                ),
                overtime_approval_ledger=ledger,
                overtime_decided_by_user_id=int(user.id),
            )
        except HTTPException as exc:
            detail = str(exc.detail or M37_OWNER_REVALIDATION_FAILED_MESSAGE)
            await edit_callback_message_via_runtime(
                callback,
                user,
                M37_OWNER_REVALIDATION_FAILED_MESSAGE,
                source_key="ot-owner-reval",
                reply_markup=empty_inline_keyboard(),
                session=session,
                commit=False,
            )
            await edit_requester_overtime_status(
                session=session,
                user=requester,
                ledger=ledger,
                text=terminal_requester_status_text(),
                keep_cancel=False,
                bot=bot,
            )
            await session.commit()
            await _answer(callback, detail, alert=True)
            return

        if isinstance(result, JSONResponse):
            await edit_callback_message_via_runtime(
                callback,
                user,
                M37_OWNER_REVALIDATION_FAILED_MESSAGE,
                source_key="ot-owner-json",
                reply_markup=empty_inline_keyboard(),
                session=session,
                commit=False,
            )
            await edit_requester_overtime_status(
                session=session,
                user=requester,
                ledger=ledger,
                text=terminal_requester_status_text(),
                keep_cancel=False,
                bot=bot,
            )
            await session.commit()
            await _answer(callback, M37_OWNER_REVALIDATION_FAILED_MESSAGE, alert=True)
            return

        await edit_callback_message_via_runtime(
            callback,
            user,
            M29_OWNER_STATUS_APPROVED,
            source_key="ot-owner-ok",
            reply_markup=empty_inline_keyboard(),
            session=session,
            commit=False,
        )
        await edit_requester_overtime_status(
            session=session,
            user=requester,
            ledger=ledger,
            text=terminal_requester_status_text(approved=True),
            keep_cancel=False,
            bot=bot,
        )
        await session.commit()
        try:
            await background_tasks()
        except Exception as exc:
            logger.debug("overtime approve background tasks failed: %s", exc)
        await _answer(callback, M29_OWNER_STATUS_APPROVED, alert=False)


async def _answer_owner_error(
    callback: types.CallbackQuery,
    exc: OvertimeRequestError,
) -> None:
    if exc.code == OvertimeRequestErrorCode.NOT_OWNER:
        text = M34_OWNER_NOT_OWNER_MESSAGE
    elif exc.code == OvertimeRequestErrorCode.DECISION_EXPIRED:
        text = M32_OWNER_DECISION_EXPIRED_MESSAGE
    elif exc.code == OvertimeRequestErrorCode.ALREADY_TERMINAL:
        text = M33_OWNER_ALREADY_TERMINAL_MESSAGE
    else:
        text = str(exc.detail or M33_OWNER_ALREADY_TERMINAL_MESSAGE)
    await _answer(callback, text, alert=True)
