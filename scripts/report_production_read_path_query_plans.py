from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Select, desc, or_, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import joinedload


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from api.routers.users_public import (  # noqa: E402
    _build_customer_public_visibility_filter,
    _build_project_user_directory_stmt,
)
from core.db import AsyncSessionLocal  # noqa: E402
from core.enums import ChatType, UserAccountStatus  # noqa: E402
from core.services.chat_room_service import build_room_conversation_projection_stmt  # noqa: E402
from core.services.chat_service import (  # noqa: E402
    build_direct_conversation_list_stmt,
    build_direct_unread_poll_stmt,
)
from core.utils import utc_now  # noqa: E402
from models.accountant_relation import AccountantRelation, AccountantRelationStatus  # noqa: E402
from models.admin_message import AdminMarketMessage  # noqa: E402
from models.conversation import Conversation  # noqa: E402
from models.customer_relation import CustomerRelation, CustomerRelationStatus  # noqa: E402
from models.offer import Offer, OfferStatus  # noqa: E402
from models.user import User, UserRole  # noqa: E402


CAPACITY_ACCOUNTANT_STATUSES = (
    AccountantRelationStatus.PENDING,
    AccountantRelationStatus.ACTIVE,
)
CAPACITY_CUSTOMER_STATUSES = (
    CustomerRelationStatus.PENDING,
    CustomerRelationStatus.ACTIVE,
)


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


@dataclass(frozen=True)
class QueryCase:
    endpoint_family: str
    name: str
    statement: Select[Any]
    notes: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run read-only EXPLAIN ANALYZE coverage for production hot read paths "
            "identified by Stage L/RPL2."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Emit only the JSON report to stdout.")
    parser.add_argument("--include-sql", action="store_true", help="Include compiled SQL in the report.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Optional directory for JSON/Markdown artifacts.")
    parser.add_argument("--limit", type=int, default=30, help="Default endpoint limit used by Stage L k6 reads.")
    parser.add_argument("--search-query", default="loadtest", help="Search term used for users-public/search.")
    parser.add_argument("--statement-timeout-ms", type=int, default=15_000)
    parser.add_argument("--fail-on-error", action="store_true", help="Exit 1 if any EXPLAIN case fails.")
    return parser.parse_args(argv)


def compile_postgres_sql(stmt: Select[Any]) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def build_explain_sql(sql: str) -> str:
    return f"EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) {sql}"


def collect_scan_nodes(plan_node: dict[str, Any]) -> list[str]:
    nodes: list[str] = []
    node_type = str(plan_node.get("Node Type") or "").strip()
    if "Scan" in node_type:
        relation_name = str(plan_node.get("Relation Name") or "").strip()
        nodes.append(f"{node_type} on {relation_name}" if relation_name else node_type)
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


async def resolve_general_user(session) -> User | None:
    active_customer_exists = (
        select(CustomerRelation.id)
        .where(
            CustomerRelation.customer_user_id == User.id,
            CustomerRelation.status == CustomerRelationStatus.ACTIVE,
            CustomerRelation.deleted_at.is_(None),
        )
        .exists()
    )
    result = await session.execute(
        select(User)
        .where(
            User.is_deleted.is_(False),
            User.account_status == UserAccountStatus.ACTIVE,
            ~active_customer_exists,
        )
        .order_by(User.id.desc())
        .limit(1)
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    result = await session.execute(
        select(User)
        .where(User.is_deleted.is_(False), User.account_status == UserAccountStatus.ACTIVE)
        .order_by(User.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def resolve_owner_user_id(session) -> int | None:
    result = await session.execute(
        select(AccountantRelation.owner_user_id)
        .where(
            AccountantRelation.deleted_at.is_(None),
            AccountantRelation.status.in_(CAPACITY_ACCOUNTANT_STATUSES),
        )
        .order_by(AccountantRelation.created_at.desc(), AccountantRelation.id.desc())
        .limit(1)
    )
    value = result.scalar_one_or_none()
    if value is not None:
        return int(value)
    result = await session.execute(
        select(CustomerRelation.owner_user_id)
        .where(
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.status.in_(CAPACITY_CUSTOMER_STATUSES),
        )
        .order_by(CustomerRelation.created_at.desc(), CustomerRelation.id.desc())
        .limit(1)
    )
    value = result.scalar_one_or_none()
    if value is not None:
        return int(value)
    user = await resolve_general_user(session)
    return int(user.id) if user is not None else None


async def resolve_offer_owner_user_id(session) -> int | None:
    result = await session.execute(
        select(Offer.user_id)
        .where(Offer.user_id.is_not(None))
        .order_by(Offer.created_at.desc(), Offer.id.desc())
        .limit(1)
    )
    value = result.scalar_one_or_none()
    if value is not None:
        return int(value)
    return await resolve_owner_user_id(session)


async def run_explain(session, *, case: QueryCase, include_sql: bool, statement_timeout_ms: int) -> dict[str, Any]:
    sql = compile_postgres_sql(case.statement)
    timeout = max(1000, int(statement_timeout_ms))
    await session.execute(text(f"SET LOCAL statement_timeout = '{timeout}ms'"))
    result = await session.execute(text(build_explain_sql(sql)))
    raw_payload = result.scalar_one()
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    report: dict[str, Any] = {
        "endpoint_family": case.endpoint_family,
        "name": case.name,
        "notes": case.notes,
        "summary": asdict(summarize_explain_payload(payload)),
    }
    if include_sql:
        report["sql"] = sql
    return report


def build_admin_users_stmt(*, limit: int) -> Select[Any]:
    return (
        select(User)
        .where(User.is_deleted.is_(False))
        .order_by(User.id.desc())
        .limit(limit)
    )


def build_public_search_stmt(*, current_user: User, search_query: str, limit: int) -> Select[Any]:
    search_pattern = f"%{search_query}%"
    return (
        select(User)
        .where(
            User.is_deleted.is_(False),
            User.id != current_user.id,
            _build_customer_public_visibility_filter(current_user),
            or_(
                User.full_name.ilike(search_pattern),
                User.account_name.ilike(search_pattern),
                User.username.ilike(search_pattern),
                User.mobile_number.ilike(search_pattern),
            ),
        )
        .order_by(User.id.desc())
        .limit(limit)
    )


def build_accountant_pending_sweep_stmt(*, owner_user_id: int) -> Select[Any]:
    now = utc_now().replace(tzinfo=None)
    return (
        select(AccountantRelation)
        .where(
            AccountantRelation.status == AccountantRelationStatus.PENDING,
            AccountantRelation.deleted_at.is_(None),
            AccountantRelation.expires_at <= now,
            AccountantRelation.owner_user_id == owner_user_id,
        )
        .order_by(AccountantRelation.id.asc())
    )


def build_accountant_relations_stmt(*, owner_user_id: int) -> Select[Any]:
    return (
        select(AccountantRelation)
        .options(joinedload(AccountantRelation.accountant_user))
        .where(
            AccountantRelation.owner_user_id == owner_user_id,
            AccountantRelation.deleted_at.is_(None),
            AccountantRelation.status.in_(CAPACITY_ACCOUNTANT_STATUSES),
        )
        .order_by(AccountantRelation.created_at.desc(), AccountantRelation.id.desc())
    )


def build_customer_pending_sweep_stmt(*, owner_user_id: int) -> Select[Any]:
    now = utc_now().replace(tzinfo=None)
    return (
        select(CustomerRelation)
        .where(
            CustomerRelation.status == CustomerRelationStatus.PENDING,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.expires_at.is_not(None),
            CustomerRelation.expires_at <= now,
            CustomerRelation.owner_user_id == owner_user_id,
        )
        .order_by(CustomerRelation.id.asc())
    )


def build_customer_relations_stmt(*, owner_user_id: int) -> Select[Any]:
    return (
        select(CustomerRelation)
        .options(joinedload(CustomerRelation.customer_user))
        .where(
            CustomerRelation.owner_user_id == owner_user_id,
            CustomerRelation.deleted_at.is_(None),
            CustomerRelation.status.in_(CAPACITY_CUSTOMER_STATUSES),
        )
        .order_by(CustomerRelation.created_at.desc(), CustomerRelation.id.desc())
    )


def build_offers_list_stmt(*, limit: int) -> Select[Any]:
    return (
        select(Offer)
        .where(Offer.status == OfferStatus.ACTIVE)
        .order_by(Offer.created_at.desc())
        .limit(limit)
    )


def build_offers_my_stmt(*, owner_user_id: int, limit: int) -> Select[Any]:
    return (
        select(Offer)
        .where(
            Offer.user_id == owner_user_id,
            Offer.republished_offer_id.is_(None),
        )
        .order_by(Offer.created_at.desc())
        .limit(limit)
    )


def build_admin_market_current_stmt() -> Select[Any]:
    return (
        select(AdminMarketMessage)
        .options(joinedload(AdminMarketMessage.created_by))
        .where(AdminMarketMessage.is_active.is_(True))
        .order_by(AdminMarketMessage.published_at.desc(), AdminMarketMessage.id.desc())
        .limit(1)
    )


async def build_cases(session, args: argparse.Namespace) -> tuple[list[QueryCase], list[dict[str, str]]]:
    current_user = await resolve_general_user(session)
    owner_user_id = await resolve_owner_user_id(session)
    offer_owner_user_id = await resolve_offer_owner_user_id(session)
    cases: list[QueryCase] = []
    skipped: list[dict[str, str]] = []

    if current_user is None:
        skipped.extend(
            [
                {"name": "users_public_search", "reason": "No active user sample found"},
                {"name": "project_users", "reason": "No active user sample found"},
                {"name": "chat_conversations", "reason": "No active user sample found"},
                {"name": "chat_poll", "reason": "No active user sample found"},
            ]
        )
    else:
        cases.append(
            QueryCase(
                endpoint_family="users_public_search",
                name="users_public_search_loadtest",
                statement=build_public_search_stmt(
                    current_user=current_user,
                    search_query=args.search_query,
                    limit=20,
                ),
                notes="Mirrors GET /api/users-public/search?q=loadtest&limit=20 for a non-customer user sample.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="project_users",
                name="project_users_directory",
                statement=_build_project_user_directory_stmt(q=None, limit=args.limit, offset=0),
                notes="Mirrors GET /api/users-public/{id}/project-users?limit=30.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="chat_conversations",
                name="chat_conversations_direct",
                statement=build_direct_conversation_list_stmt(current_user.id),
                notes="Direct portion of GET /api/chat/conversations and GET /api/chat/poll.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="chat_conversations",
                name="chat_conversations_rooms",
                statement=build_room_conversation_projection_stmt(
                    current_user_id=current_user.id,
                    room_type=(ChatType.GROUP, ChatType.CHANNEL),
                ),
                notes="Room portion of GET /api/chat/conversations.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="chat_poll",
                name="chat_poll_direct_full",
                statement=build_direct_conversation_list_stmt(current_user.id),
                notes="Current poll path reads the full direct conversation projection, then filters unread rows in Python.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="chat_poll",
                name="chat_poll_rooms_full",
                statement=build_room_conversation_projection_stmt(
                    current_user_id=current_user.id,
                    room_type=(ChatType.GROUP, ChatType.CHANNEL),
                ),
                notes="Current poll path reads group/channel conversation projections before filtering unread rows.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="chat_poll",
                name="chat_poll_direct_unread_diagnostic",
                statement=build_direct_unread_poll_stmt(current_user.id),
                notes="Diagnostic unread-only direct query; not the current route behavior, but useful for RPL4.",
            )
        )

    if owner_user_id is None:
        skipped.extend(
            [
                {"name": "accountant_relations", "reason": "No owner user sample found"},
                {"name": "customer_relations", "reason": "No owner user sample found"},
            ]
        )
    else:
        cases.append(
            QueryCase(
                endpoint_family="accountant_relations",
                name="accountant_pending_sweep_probe",
                statement=build_accountant_pending_sweep_stmt(owner_user_id=owner_user_id),
                notes="Read-only probe for the pending-expiry sweep that precedes owner accountant listing.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="accountant_relations",
                name="accountant_relations_list",
                statement=build_accountant_relations_stmt(owner_user_id=owner_user_id),
                notes="Mirrors GET /api/accountants/owner-relations after the sweep probe.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="customer_relations",
                name="customer_pending_sweep_probe",
                statement=build_customer_pending_sweep_stmt(owner_user_id=owner_user_id),
                notes="Read-only probe for the pending-expiry sweep that precedes owner customer listing.",
            )
        )
        cases.append(
            QueryCase(
                endpoint_family="customer_relations",
                name="customer_relations_list",
                statement=build_customer_relations_stmt(owner_user_id=owner_user_id),
                notes="Mirrors GET /api/customers/owner-relations after the sweep probe.",
            )
        )

    cases.append(
        QueryCase(
            endpoint_family="admin_users",
            name="admin_users_list",
            statement=build_admin_users_stmt(limit=args.limit),
            notes="Mirrors GET /api/users/?limit=30 for super-admin/dev-key reads.",
        )
    )
    cases.append(
        QueryCase(
            endpoint_family="offers_list",
            name="offers_list_active",
            statement=build_offers_list_stmt(limit=args.limit),
            notes="Mirrors GET /api/offers/?limit=30 primary offer query.",
        )
    )
    if offer_owner_user_id is None:
        skipped.append({"name": "offers_my", "reason": "No offer owner user sample found"})
    else:
        cases.append(
            QueryCase(
                endpoint_family="offers_my",
                name="offers_my_owner_history",
                statement=build_offers_my_stmt(owner_user_id=offer_owner_user_id, limit=args.limit),
                notes="Mirrors GET /api/offers/my?limit=30 primary offer query.",
            )
        )
    cases.append(
        QueryCase(
            endpoint_family="admin_market_current",
            name="admin_market_current",
            statement=build_admin_market_current_stmt(),
            notes="Mirrors GET /api/admin-messages/market/current.",
        )
    )
    return cases, skipped


async def build_report(args: argparse.Namespace) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        cases, skipped = await build_cases(session, args)
        reports: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for case in cases:
            try:
                reports.append(
                    await run_explain(
                        session,
                        case=case,
                        include_sql=args.include_sql,
                        statement_timeout_ms=args.statement_timeout_ms,
                    )
                )
            except Exception as exc:  # pragma: no cover - production-data dependent
                errors.append(
                    {
                        "endpoint_family": case.endpoint_family,
                        "name": case.name,
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
                await session.rollback()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "source": "Stage RPL2 production read-path query plan coverage",
            "stage_l_endpoint_params": {
                "users_public_search": f"/users-public/search?q={args.search_query}&limit=20",
                "project_users": f"/users-public/{{id}}/project-users?limit={args.limit}",
                "accountant_relations": "/accountants/owner-relations",
                "customer_relations": "/customers/owner-relations",
                "admin_users": f"/users/?limit={args.limit}",
                "offers_list": f"/offers/?limit={args.limit}",
                "offers_my": f"/offers/my?limit={args.limit}",
                "admin_market_current": "/admin-messages/market/current",
                "chat_conversations": "/chat/conversations",
                "chat_poll": "/chat/poll",
            },
        },
        "reports": reports,
        "skipped": skipped,
        "errors": errors,
    }


def format_human_report(payload: dict[str, Any]) -> str:
    lines = ["Production read-path query plans:"]
    by_family: dict[str, list[dict[str, Any]]] = {}
    for report in payload.get("reports", []):
        by_family.setdefault(str(report["endpoint_family"]), []).append(report)

    for family in sorted(by_family):
        lines.append(f"\n[{family}]")
        for report in by_family[family]:
            summary = report["summary"]
            lines.append(
                f"- {report['name']}: {summary['node_type']} | "
                f"exec={summary['execution_time_ms']:.3f}ms | "
                f"plan={summary['planning_time_ms']:.3f}ms | "
                f"rows={summary['actual_rows']} | "
                f"temp={summary['temp_read_blocks']}/{summary['temp_written_blocks']} | "
                f"scans={', '.join(summary['scan_nodes']) or 'none'}"
            )
            if "sql" in report:
                lines.append(f"  SQL: {report['sql']}")

    if payload.get("skipped"):
        lines.append("\n[skipped]")
        for skipped in payload["skipped"]:
            lines.append(f"- {skipped['name']}: {skipped['reason']}")
    if payload.get("errors"):
        lines.append("\n[errors]")
        for error in payload["errors"]:
            lines.append(f"- {error['name']}: {error['error']}")
    return "\n".join(lines)


def write_artifacts(payload: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"production-read-path-query-plans-{stamp}.json"
    md_path = output_dir / f"production-read-path-query-plans-{stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(format_human_report(payload) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = asyncio.run(build_report(args))
    if args.output_dir is not None:
        payload["artifacts"] = write_artifacts(payload, args.output_dir)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_human_report(payload))
        if payload.get("artifacts"):
            print(f"\nArtifacts: {payload['artifacts']}")
    return 1 if args.fail_on_error and payload.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
