from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Select, desc, or_, select, text
from sqlalchemy.dialects import postgresql


CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

from core.db import AsyncSessionLocal
from models.offer import Offer, OfferStatus
from models.trade import Trade


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run read-only EXPLAIN ANALYZE for core market/trade queries.")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    parser.add_argument("--include-sql", action="store_true", help="Include compiled SQL text in the report output.")
    parser.add_argument("--limit", type=int, default=50)
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


def summarize_explain_payload(payload: list[dict[str, Any]]) -> dict[str, Any]:
    if not payload:
        raise ValueError("EXPLAIN payload was empty")
    root = payload[0]
    plan = root.get("Plan") or {}
    if not isinstance(plan, dict):
        raise ValueError("EXPLAIN payload is missing the root plan")
    return {
        "node_type": str(plan.get("Node Type") or "unknown"),
        "execution_time_ms": float(root.get("Execution Time") or 0.0),
        "planning_time_ms": float(root.get("Planning Time") or 0.0),
        "actual_rows": int(plan.get("Actual Rows")) if plan.get("Actual Rows") is not None else None,
        "plan_rows": int(plan.get("Plan Rows")) if plan.get("Plan Rows") is not None else None,
        "shared_hit_blocks": int(plan.get("Shared Hit Blocks")) if plan.get("Shared Hit Blocks") is not None else None,
        "shared_read_blocks": int(plan.get("Shared Read Blocks")) if plan.get("Shared Read Blocks") is not None else None,
        "temp_read_blocks": int(plan.get("Temp Read Blocks")) if plan.get("Temp Read Blocks") is not None else None,
        "temp_written_blocks": int(plan.get("Temp Written Blocks")) if plan.get("Temp Written Blocks") is not None else None,
        "scan_nodes": collect_scan_nodes(plan),
    }


async def run_explain(session, *, name: str, stmt: Select[Any], include_sql: bool) -> dict[str, Any]:
    sql = compile_postgres_sql(stmt)
    result = await session.execute(text(build_explain_sql(sql)))
    raw_payload = result.scalar_one()
    payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
    report: dict[str, Any] = {
        "name": name,
        "summary": summarize_explain_payload(payload),
    }
    if include_sql:
        report["sql"] = sql
    return report


async def resolve_offer_user_sample(session) -> int | None:
    result = await session.execute(
        select(Offer.user_id)
        .where(Offer.user_id.is_not(None))
        .order_by(desc(Offer.created_at), desc(Offer.id))
        .limit(1)
    )
    value = result.scalar_one_or_none()
    return int(value) if value is not None else None


async def resolve_trade_user_sample(session) -> int | None:
    result = await session.execute(
        select(Trade.offer_user_id, Trade.responder_user_id, Trade.actor_user_id)
        .order_by(desc(Trade.created_at), desc(Trade.id))
        .limit(1)
    )
    row = result.first()
    if row is None:
        return None
    for value in (row.offer_user_id, row.responder_user_id, row.actor_user_id):
        if value is not None:
            return int(value)
    return None


async def build_report(args: argparse.Namespace) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        offer_user_id = await resolve_offer_user_sample(session)
        trade_user_id = await resolve_trade_user_sample(session)
        reports: list[dict[str, Any]] = []
        skipped: list[dict[str, str]] = []

        reports.append(
            await run_explain(
                session,
                name="active_offers_feed",
                stmt=select(Offer)
                .where(Offer.status == OfferStatus.ACTIVE)
                .order_by(desc(Offer.created_at), desc(Offer.id))
                .limit(args.limit),
                include_sql=args.include_sql,
            )
        )
        reports.append(
            await run_explain(
                session,
                name="recent_trades_feed",
                stmt=select(Trade).order_by(desc(Trade.created_at), desc(Trade.id)).limit(args.limit),
                include_sql=args.include_sql,
            )
        )

        if offer_user_id is None:
            skipped.append({"name": "user_offers_history", "reason": "No offer user sample found"})
        else:
            reports.append(
                await run_explain(
                    session,
                    name="user_offers_history",
                    stmt=select(Offer)
                    .where(Offer.user_id == offer_user_id)
                    .order_by(desc(Offer.created_at), desc(Offer.id))
                    .limit(args.limit),
                    include_sql=args.include_sql,
                )
            )

        if trade_user_id is None:
            skipped.append({"name": "user_trade_history", "reason": "No trade user sample found"})
        else:
            reports.append(
                await run_explain(
                    session,
                    name="user_trade_history",
                    stmt=select(Trade)
                    .where(
                        or_(
                            Trade.offer_user_id == trade_user_id,
                            Trade.responder_user_id == trade_user_id,
                            Trade.actor_user_id == trade_user_id,
                        )
                    )
                    .order_by(desc(Trade.created_at), desc(Trade.id))
                    .limit(args.limit),
                    include_sql=args.include_sql,
                )
            )

    return {"reports": reports, "skipped": skipped}


def format_human_report(payload: dict[str, Any]) -> str:
    lines = ["Market/trade query plans:"]
    for report in payload.get("reports", []):
        summary = report["summary"]
        lines.append(
            f"- {report['name']}: {summary['node_type']} | exec={summary['execution_time_ms']:.3f}ms | "
            f"plan={summary['planning_time_ms']:.3f}ms | scans={', '.join(summary['scan_nodes']) or 'none'}"
        )
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
