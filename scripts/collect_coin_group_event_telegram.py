#!/usr/bin/env python3
"""Collect normalized coin-group events from one private Telegram channel.

The upstream bot publishes JSON envelopes to a dedicated event channel.  This
one-shot command validates that transport envelope, keeps raw group text only
in the bounded three-day staging database, and writes privacy-minimized facts
through the current coin-group parser, causal resolver, and trade linker.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.coin_group_pipeline import process_coin_group_staging
from core.market_intelligence.coin_prediction_anchors import (
    load_coin_prediction_anchors,
)
from core.market_intelligence.coin_group_feedback import (
    ensure_coin_group_feedback_store,
    load_coin_group_parser_feedback,
    mark_coin_group_parser_feedback_applied,
)
from core.market_intelligence.coin_group_staging import (
    CoinGroupStagingError,
    CoinGroupStagingMessage,
    connect_coin_group_staging,
    initialize_coin_group_staging,
    list_current_staged_coin_group_messages,
    purge_expired_coin_group_staging,
    stage_coin_group_message,
)
from core.market_intelligence.market_contracts import normalize_utc
from core.market_intelligence.market_store import (
    advance_source_checkpoint,
    connect_market_store,
    initialize_market_store,
    read_source_checkpoint,
)
from core.market_intelligence.public_telegram.transport import PublicTelegramCredentials


CHECKPOINT_SOURCE = "COIN_GROUP_EVENT_CHANNEL"
MAXIMUM_MESSAGES = 2_000
_BATCH_RULE = re.compile(r"(?:\r?\n\s*)+[━─—-]{3,}\s*(?:\r?\n\s*)+")


class CoinGroupEventCollectorError(RuntimeError):
    """A redacted, operator-safe collection failure."""


def collector_failure_reason(exc: BaseException) -> str:
    """Return actionable detail only for errors with an operator-safe contract."""

    if isinstance(
        exc,
        (CoinGroupEventCollectorError, CoinGroupStagingError, OSError, ValueError),
    ):
        return str(exc)
    return f"coin_group_event_collect_failed:{type(exc).__name__}"


def _emit(**payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), flush=True)


def _runtime_root(value: str) -> Path:
    root = Path(value).expanduser().resolve()
    if not root.is_dir():
        raise CoinGroupEventCollectorError("runtime_root_unavailable")
    return root


def _inside(root: Path, value: str, *, field: str) -> Path:
    supplied = Path(value).expanduser()
    path = supplied.resolve() if supplied.is_absolute() else (root / supplied).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CoinGroupEventCollectorError(f"{field}_outside_runtime_root") from exc
    if path == root:
        raise CoinGroupEventCollectorError(f"{field}_must_be_file")
    return path


def _external_sidecar(value: str, *, field: str) -> Path:
    path = Path(value).expanduser().resolve()
    try:
        path.relative_to(REPO_ROOT.resolve())
    except ValueError:
        pass
    else:
        raise CoinGroupEventCollectorError(f"{field}_inside_repository")
    if path == path.parent:
        raise CoinGroupEventCollectorError(f"{field}_must_be_file")
    return path


def _channel_id(environment: Mapping[str, str] | None = None) -> int:
    values = os.environ if environment is None else environment
    raw = str(values.get("COIN_GROUP_EVENT_CHANNEL_ID", "")).strip()
    if not re.fullmatch(r"-100\d{8,16}", raw):
        raise CoinGroupEventCollectorError("coin_group_event_channel_id_missing_or_invalid")
    return int(raw)


def decode_event_envelopes(payload: str) -> Iterable[dict[str, object]]:
    """Decode only complete JSON documents or the known batch delimiter."""

    try:
        decoded = json.loads(payload)
        candidates = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        candidates = []
        for segment in _BATCH_RULE.split(payload):
            segment = segment.strip()
            if not segment:
                continue
            try:
                decoded = json.loads(segment)
            except json.JSONDecodeError:
                continue
            candidates.extend(decoded if isinstance(decoded, list) else [decoded])
    for candidate in candidates:
        if isinstance(candidate, dict):
            yield candidate


def _positive_int(value: object) -> int | None:
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _event_message(
    envelope: Mapping[str, object],
    *,
    received_at_utc: str,
) -> CoinGroupStagingMessage | None:
    source = envelope.get("source")
    payload = envelope.get("coin")
    if not isinstance(source, Mapping) or not isinstance(payload, Mapping):
        return None
    if str(source.get("market") or "").strip().lower() != "coin":
        return None
    if str(envelope.get("event_type") or "").strip().lower() not in {
        "message_created",
        "message_edited",
        "message_updated",
    }:
        return None
    group_number = _positive_int(source.get("group_number"))
    message_id = _positive_int(payload.get("message_id"))
    if group_number not in {1, 2} or message_id is None:
        return None
    text = str(payload.get("text") or "").strip()
    if not text:
        return None
    published_raw = payload.get("telegram_datetime") or envelope.get("occurred_at_utc")
    if published_raw is None:
        return None
    try:
        published = normalize_utc(published_raw, field_name="coin_group_event_time_utc")
        received = normalize_utc(received_at_utc, field_name="coin_group_received_at_utc")
        edited = (
            normalize_utc(payload["telegram_edit_datetime"], field_name="coin_group_edited_at_utc")
            if payload.get("telegram_edit_datetime")
            else None
        )
    except Exception:
        return None
    # A malformed edit timestamp must not poison the complete channel batch.
    # The canonical payload decoder already applies this same fail-closed
    # ordering rule: reject only the contradictory envelope and never repair
    # or guess its source time.
    if edited is not None and edited < published:
        return None
    available = max(published, received)
    reply = _positive_int(payload.get("reply_message_id"))
    sender = str(payload.get("sender_peer_id") or payload.get("sender_name") or "").strip() or None
    return CoinGroupStagingMessage(
        group_number=group_number,
        message_id=message_id,
        event_time_utc=published,
        available_at_utc=available,
        text=text,
        reply_to_message_id=reply,
        sender_identity=sender,
        edited_at_utc=edited,
    )


async def _incremental_channel_messages(
    client: object,
    entity: object,
    *,
    minimum_id: int,
    maximum_messages: int,
) -> list[object]:
    """Fetch the normal small delta in one newest-first Telegram request.

    Telethon's reverse iterator emulates oldest-first pagination with extra
    history requests.  The recurring collector normally has a tiny delta, so
    fetch newest-first and restore causal order locally.  If the delta exceeds
    the bounded batch, refetch oldest-first so advancing the checkpoint can
    never skip an older transport envelope.
    """

    newest_first = [
        message
        async for message in client.iter_messages(  # type: ignore[attr-defined]
            entity,
            min_id=int(minimum_id),
            reverse=False,
            limit=int(maximum_messages) + 1,
        )
    ]
    if len(newest_first) <= int(maximum_messages):
        return sorted(newest_first, key=lambda message: int(message.id))
    return [
        message
        async for message in client.iter_messages(  # type: ignore[attr-defined]
            entity,
            min_id=int(minimum_id),
            reverse=True,
            limit=int(maximum_messages),
        )
    ]


async def collect(args: argparse.Namespace) -> dict[str, object]:
    root = _runtime_root(args.runtime_root)
    market_path = _inside(root, args.market_store, field="market_store")
    staging_path = _inside(root, args.staging_store, field="staging_store")
    session_path = _inside(root, args.session, field="telegram_session")
    feedback_path = (
        _external_sidecar(args.feedback_db, field="feedback_db")
        if args.feedback_db
        else None
    )
    prediction_path = (
        _external_sidecar(
            args.estimator_calibration_db,
            field="estimator_calibration_db",
        )
        if args.estimator_calibration_db
        else None
    )
    market_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(session_path.parent, 0o700)
    if args.bootstrap_session and not sys.stdin.isatty():
        raise CoinGroupEventCollectorError("interactive_session_requires_tty")
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise CoinGroupEventCollectorError("telethon_optional_dependency_not_installed") from exc

    market = connect_market_store(market_path)
    staging = connect_coin_group_staging(staging_path, repository_root=REPO_ROOT)
    client = None
    previous_umask = os.umask(0o077)
    counters = {
        "telegram_messages": 0,
        "event_envelopes": 0,
        "invalid_or_unrelated_envelopes": 0,
        "staged_changes": 0,
    }
    try:
        initialize_market_store(market)
        initialize_coin_group_staging(staging)
        if feedback_path is not None:
            ensure_coin_group_feedback_store(feedback_path)
        credentials = PublicTelegramCredentials.from_environment()
        client = TelegramClient(
            str(session_path),
            credentials.api_id,
            credentials.api_hash,
            sequential_updates=True,
            flood_sleep_threshold=60,
            timeout=10,
            connection_retries=3,
            retry_delay=1,
            request_retries=3,
            auto_reconnect=True,
        )
        await client.connect()
        if not await client.is_user_authorized():
            if not args.bootstrap_session:
                raise CoinGroupEventCollectorError("coin_group_telegram_session_authorization_required")
            await client.start(phone=credentials.phone)
        entity = await client.get_entity(_channel_id())
        minimum_id = read_source_checkpoint(market, CHECKPOINT_SOURCE)
        if minimum_id is None and not args.replay_window:
            newest = [message async for message in client.iter_messages(entity, limit=1)]
            minimum_id = max(0, int(newest[0].id) - 1) if newest else 0
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, min(3, int(args.days))))
        if args.replay_window:
            messages = []
            async for telegram_message in client.iter_messages(
                entity,
                min_id=0,
                reverse=True,
                limit=int(args.maximum_messages),
            ):
                published = getattr(telegram_message, "date", None)
                if not isinstance(published, datetime):
                    continue
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published.astimezone(timezone.utc) < cutoff:
                    continue
                messages.append(telegram_message)
        else:
            messages = await _incremental_channel_messages(
                client,
                entity,
                minimum_id=int(minimum_id or 0),
                maximum_messages=int(args.maximum_messages),
            )

        received_at = normalize_utc(datetime.now(timezone.utc), field_name="coin_group_received_at_utc")
        checkpoints: list[tuple[int, str]] = []
        for telegram_message in messages:
            published = getattr(telegram_message, "date", None)
            if not isinstance(published, datetime):
                continue
            counters["telegram_messages"] += 1
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            checkpoints.append(
                (
                    int(telegram_message.id),
                    normalize_utc(published, field_name="coin_group_transport_event_time_utc"),
                )
            )
            raw = str(getattr(telegram_message, "message", None) or "")
            decoded = tuple(decode_event_envelopes(raw))
            if not decoded:
                counters["invalid_or_unrelated_envelopes"] += 1
                continue
            for envelope in decoded:
                counters["event_envelopes"] += 1
                staged = _event_message(envelope, received_at_utc=received_at)
                if staged is None:
                    counters["invalid_or_unrelated_envelopes"] += 1
                    continue
                counters["staged_changes"] += int(
                    stage_coin_group_message(staging, staged, staged_at_utc=received_at)
                )
        staging.commit()
        try:
            prediction_anchors = ()
            prediction_rows_seen = 0
            prediction_rows_rejected = 0
            staged_window = list_current_staged_coin_group_messages(
                staging,
                as_of_utc=received_at,
            )
            if prediction_path is not None and staged_window:
                prediction_load = load_coin_prediction_anchors(
                    prediction_path,
                    earliest_event_time_utc=staged_window[0].event_time_utc,
                    as_of_utc=received_at,
                )
                prediction_anchors = prediction_load.anchors
                prediction_rows_seen = prediction_load.rows_seen
                prediction_rows_rejected = prediction_load.rows_rejected
            pipeline = process_coin_group_staging(
                staging,
                market,
                as_of_utc=received_at,
                additional_anchors=prediction_anchors,
                parser_feedback=(
                    load_coin_group_parser_feedback(feedback_path)
                    if feedback_path is not None
                    else None
                ),
            )
            for message_id, event_time in checkpoints:
                advance_source_checkpoint(
                    market,
                    source_code=CHECKPOINT_SOURCE,
                    message_id=message_id,
                    event_time_utc=event_time,
                )
            purged = purge_expired_coin_group_staging(staging, as_of_utc=received_at)
            market.commit()
            staging.commit()
            feedback_marked_applied = (
                mark_coin_group_parser_feedback_applied(
                    feedback_path,
                    pipeline.applied_feedback_event_keys,
                    applied_at_utc=received_at,
                )
                if feedback_path is not None
                else 0
            )
        except BaseException:
            market.rollback()
            staging.rollback()
            raise
        return {
            "status": "COLLECTED",
            **counters,
            "purged_staging_messages": purged,
            "pipeline": {
                "staged_messages_seen": pipeline.staged_messages_seen,
                "offer_facts_upserted": pipeline.offer_facts_upserted,
                "eligible_offers": pipeline.eligible_offers,
                "pending_or_rejected_offers": pipeline.pending_or_rejected_offers,
                "trade_facts_upserted": pipeline.trade_facts_upserted,
                "eligible_trades": pipeline.eligible_trades,
                "pending_or_rejected_trades": pipeline.pending_or_rejected_trades,
                "root_messages_not_trade_linkable": pipeline.root_messages_not_trade_linkable,
                "retracted_facts": pipeline.retracted_facts,
                "feedback_reviews_seen": pipeline.feedback_reviews_seen,
                "feedback_reviews_applied": pipeline.feedback_reviews_applied,
                "feedback_pattern_calibrations_applied": (
                    pipeline.feedback_pattern_calibrations_applied
                ),
                "feedback_revisions_marked_applied": feedback_marked_applied,
                "estimator_prediction_rows_seen": prediction_rows_seen,
                "estimator_prediction_rows_rejected": prediction_rows_rejected,
                "estimator_prediction_anchors": len(prediction_anchors),
            },
        }
    finally:
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        market.close()
        staging.close()
        os.umask(previous_umask)
        session_file = session_path.with_suffix(".session")
        if session_file.exists():
            os.chmod(session_file, 0o600)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--market-store", default="market/market.sqlite3")
    parser.add_argument("--staging-store", default="staging/coin-groups.sqlite3")
    parser.add_argument(
        "--feedback-db",
        help="External privacy-safe parser-feedback sidecar; raw text is never stored there.",
    )
    parser.add_argument(
        "--estimator-calibration-db",
        default=os.environ.get("COIN_GROUP_ESTIMATOR_CALIBRATION_DB") or None,
        help="External read-only prediction ledger used for causal unnamed-commodity resolution.",
    )
    parser.add_argument("--session", default="session/coin-group-event-reader")
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--maximum-messages", type=int, default=MAXIMUM_MESSAGES)
    parser.add_argument("--replay-window", action="store_true")
    parser.add_argument("--bootstrap-session", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 < int(args.maximum_messages) <= MAXIMUM_MESSAGES:
        _emit(status="FAILED", reason="maximum_messages_out_of_range")
        return 2
    try:
        _emit(**asyncio.run(collect(args)))
        return 0
    except Exception as exc:
        _emit(status="FAILED", reason=collector_failure_reason(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
