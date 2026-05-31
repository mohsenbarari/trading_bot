from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Select, select, text
from sqlalchemy.dialects import postgresql


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.db import AsyncSessionLocal
from core.enums import ChatMembershipStatus, ChatType
from core.services.chat_room_service import (
    build_room_conversation_projection_stmt,
    build_room_message_history_stmt,
)
from core.services.chat_service import (
    build_direct_conversation_list_stmt,
    build_direct_message_history_statements,
    build_direct_unread_poll_stmt,
)
from models.chat import Chat
from models.chat_member import ChatMember
from models.conversation import Conversation


@dataclass(frozen=True)
class DirectSample:
    current_user_id: int
    other_user_id: int


@dataclass(frozen=True)
class RoomSample:
    current_user_id: int
    chat_id: int


@dataclass(frozen=True)
class QueryPlanSummary:
    node_type: str
    execution_time_ms: float
    planning_time_ms: float
    actual_rows: int | None
    plan_rows: int | None
    shared_hit_blocks: int | None
    shared_read_blocks: int | None
    temp_read_blocks: int | None
    temp_written_blocks: int | None
    scan_nodes: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run EXPLAIN ANALYZE for the core Messenger chat queries against the current database.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    parser.add_argument("--include-sql", action="store_true", help="Include compiled SQL text in the report output.")
    parser.add_argument("--history-limit", type=int, default=50, help="History query limit to compile and explain.")
    parser.add_argument("--direct-current-user-id", type=int, default=None)
    parser.add_argument("--direct-other-user-id", type=int, default=None)
    parser.add_argument("--group-current-user-id", type=int, default=None)
    parser.add_argument("--group-chat-id", type=int, default=None)
    parser.add_argument("--channel-current-user-id", type=int, default=None)
    parser.add_argument("--channel-chat-id", type=int, default=None)
    return parser.parse_args(argv)


def build_explain_sql(sql: str) -> str:
    return f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) {sql}"


def compile_postgres_sql(stmt: Select[Any]) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def collect_scan_nodes(plan_node: dict[str, Any]) -> list[str]:
    nodes: list[str] = []
    node_type = str(plan_node.get("Node Type") or "").strip()
    if "Scan" in node_type:
        relation_name = str(plan_node.get("Relation Name") or "").strip()
        if relation_name:
            nodes.append(f"{node_type} on {relation_name}")
        else:
            nodes.append(node_type)

    for child in plan_node.get("Plans", []) or []:
        if isinstance(child, dict):
            nodes.extend(collect_scan_nodes(child))
    return nodes


def summarize_explain_payload(payload: list[dict[str, Any]]) -> QueryPlanSummary:
    if not payload:
        raise ValueError("EXPLAIN payload was empty")
    root = payload[0]
    plan = root.get("Plan") or {}
    if not isinstance(plan, dict):
        raise ValueError("EXPLAIN payload is missing the root plan")

    return QueryPlanSummary(
        node_type=str(plan.get("Node Type") or "unknown"),
        execution_time_ms=float(root.get("Execution Time") or 0.0),
        planning_time_ms=float(root.get("Planning Time") or 0.0),
        actual_rows=int(plan.get("Actual Rows")) if plan.get("Actual Rows") is not None else None,
        plan_rows=int(plan.get("Plan Rows")) if plan.get("Plan Rows") is not None else None,
        shared_hit_blocks=int(plan.get("Shared Hit Blocks")) if plan.get("Shared Hit Blocks") is not None else None,
        shared_read_blocks=int(plan.get("Shared Read Blocks")) if plan.get("Shared Read Blocks") is not None else None,
        temp_read_blocks=int(plan.get("Temp Read Blocks")) if plan.get("Temp Read Blocks") is not None else None,
        temp_written_blocks=int(plan.get("Temp Written Blocks")) if plan.get("Temp Written Blocks") is not None else None,
        scan_nodes=collect_scan_nodes(plan),
    )


async def resolve_direct_sample(session, args: argparse.Namespace) -> DirectSample | None:
    if args.direct_current_user_id is not None and args.direct_other_user_id is not None:
        return DirectSample(
            current_user_id=args.direct_current_user_id,
            other_user_id=args.direct_other_user_id,
        )

    result = await session.execute(
        select(Conversation.user1_id, Conversation.user2_id)
        .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    return DirectSample(current_user_id=int(row.user1_id), other_user_id=int(row.user2_id))


async def resolve_room_sample(session, args: argparse.Namespace, *, room_type: ChatType) -> RoomSample | None:
    user_id_arg = args.group_current_user_id if room_type == ChatType.GROUP else args.channel_current_user_id
    chat_id_arg = args.group_chat_id if room_type == ChatType.GROUP else args.channel_chat_id
    if user_id_arg is not None and chat_id_arg is not None:
        return RoomSample(current_user_id=user_id_arg, chat_id=chat_id_arg)

    result = await session.execute(
        select(ChatMember.user_id, ChatMember.chat_id)
        .join(Chat, Chat.id == ChatMember.chat_id)
        .where(
            Chat.type == room_type,
            Chat.is_deleted.is_(False),
            ChatMember.membership_status == ChatMembershipStatus.ACTIVE,
        )
        .order_by(Chat.last_message_at.desc().nullslast(), Chat.id.desc(), ChatMember.user_id.asc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    return RoomSample(current_user_id=int(row.user_id), chat_id=int(row.chat_id))


async def run_explain(session, *, name: str, stmt: Select[Any], include_sql: bool) -> dict[str, Any]:
    sql = compile_postgres_sql(stmt)
    result = await session.execute(text(build_explain_sql(sql)))
    raw_payload = result.scalar_one()
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    summary = summarize_explain_payload(payload)

    report: dict[str, Any] = {
        "name": name,
        "summary": asdict(summary),
    }
    if include_sql:
        report["sql"] = sql
    return report


async def build_report(args: argparse.Namespace) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        direct_sample = await resolve_direct_sample(session, args)
        group_sample = await resolve_room_sample(session, args, room_type=ChatType.GROUP)
        channel_sample = await resolve_room_sample(session, args, room_type=ChatType.CHANNEL)

        reports: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []

        if direct_sample is None:
            skipped.extend([
                {"name": "direct_conversation_list", "reason": "No direct conversation sample found"},
                {"name": "direct_unread_poll", "reason": "No direct conversation sample found"},
                {"name": "direct_message_history", "reason": "No direct conversation sample found"},
            ])
        else:
            reports.append(
                await run_explain(
                    session,
                    name="direct_conversation_list",
                    stmt=build_direct_conversation_list_stmt(direct_sample.current_user_id),
                    include_sql=args.include_sql,
                )
            )
            reports.append(
                await run_explain(
                    session,
                    name="direct_unread_poll",
                    stmt=build_direct_unread_poll_stmt(direct_sample.current_user_id),
                    include_sql=args.include_sql,
                )
            )
            direct_history_stmt, _ = await build_direct_message_history_statements(
                session,
                current_user_id=direct_sample.current_user_id,
                other_user_id=direct_sample.other_user_id,
                limit=args.history_limit,
            )
            reports.append(
                await run_explain(
                    session,
                    name="direct_message_history",
                    stmt=direct_history_stmt,
                    include_sql=args.include_sql,
                )
            )

        if group_sample is None:
            skipped.extend([
                {"name": "group_conversation_list", "reason": "No group membership sample found"},
                {"name": "group_message_history", "reason": "No group membership sample found"},
            ])
        else:
            reports.append(
                await run_explain(
                    session,
                    name="group_conversation_list",
                    stmt=build_room_conversation_projection_stmt(
                        current_user_id=group_sample.current_user_id,
                        room_type=ChatType.GROUP,
                    ),
                    include_sql=args.include_sql,
                )
            )
            reports.append(
                await run_explain(
                    session,
                    name="group_message_history",
                    stmt=build_room_message_history_stmt(chat_id=group_sample.chat_id)
                    .order_by(text("messages.id DESC"))
                    .limit(args.history_limit),
                    include_sql=args.include_sql,
                )
            )

        if channel_sample is None:
            skipped.extend([
                {"name": "channel_conversation_list", "reason": "No channel membership sample found"},
                {"name": "channel_message_history", "reason": "No channel membership sample found"},
            ])
        else:
            reports.append(
                await run_explain(
                    session,
                    name="channel_conversation_list",
                    stmt=build_room_conversation_projection_stmt(
                        current_user_id=channel_sample.current_user_id,
                        room_type=ChatType.CHANNEL,
                    ),
                    include_sql=args.include_sql,
                )
            )
            reports.append(
                await run_explain(
                    session,
                    name="channel_message_history",
                    stmt=build_room_message_history_stmt(chat_id=channel_sample.chat_id)
                    .order_by(text("messages.id DESC"))
                    .limit(args.history_limit),
                    include_sql=args.include_sql,
                )
            )

    return {
        "reports": reports,
        "skipped": skipped,
    }


def format_human_report(payload: dict[str, Any]) -> str:
    lines = ["Messenger query plans:"]
    for report in payload.get("reports", []):
        summary = report["summary"]
        lines.append(
            f"- {report['name']}: {summary['node_type']} | exec={summary['execution_time_ms']:.3f}ms | plan={summary['planning_time_ms']:.3f}ms | scans={', '.join(summary['scan_nodes']) or 'none'}"
        )
        if "sql" in report:
            lines.append(f"  SQL: {report['sql']}")
    for skipped in payload.get("skipped", []):
        lines.append(f"- {skipped['name']}: skipped ({skipped['reason']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = asyncio.run(build_report(args))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_human_report(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())