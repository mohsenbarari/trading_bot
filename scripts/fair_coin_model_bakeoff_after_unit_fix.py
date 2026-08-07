#!/usr/bin/env python3
"""Fair bake-off after Market Store toman→IRT repair.

Compares:
1) structural commodity engine on Market Store
2) live operator estimator on legacy market DB
at identical UTC cutoffs.  Read-only; no promotion.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps" / "coin_rate_estimator"))

from coin_estimator import estimate_rates, load_model  # noqa: E402
from core.market_intelligence.coin_rate_engine import build_coin_rate_estimates  # noqa: E402


NAME_TO_CODE = {
    "امام": "IMAM",
    "بهار": "BAHAR",
    "نیم بهار": "HALF_BAHAR",
    "ربع بهار": "QUARTER_BAHAR",
    "نیم تاریخ پایین": "HALF_LOW_DATE",
    "ربع تاریخ پایین": "QUARTER_LOW_DATE",
    "یک گرمی": "ONE_GRAM",
}
CODE_TO_NAME = {value: key for key, value in NAME_TO_CODE.items()}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cutoffs(day: str, hours: list[int]) -> list[datetime]:
    base = datetime.fromisoformat(day + "T00:00:00+00:00")
    return [base + timedelta(hours=hour) for hour in hours]


def operator_centers(model: dict[str, Any], market_db: Path, as_of: datetime) -> dict[str, Any]:
    payload = estimate_rates(
        model=model,
        market_db=market_db,
        end=as_of,
        live_group_events_enabled=False,
        group_live_events_before=as_of,
    )
    out: dict[str, Any] = {}
    for settlement, body in (payload.get("settlements") or {}).items():
        rates = body.get("rates") if isinstance(body, dict) else None
        if not isinstance(rates, list):
            continue
        for item in rates:
            if not isinstance(item, dict):
                continue
            code = NAME_TO_CODE.get(str(item.get("commodity_name")))
            if code is None:
                continue
            out[f"{code}:{settlement}"] = {
                "status": item.get("status"),
                "estimated_project_price": item.get("estimated_project_price"),
                "method": item.get("method"),
            }
    return out


def commodity_centers(market_store: Path, as_of: datetime) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{market_store}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        estimates = build_coin_rate_estimates(connection, as_of_utc=as_of)
    finally:
        connection.close()
    out: dict[str, Any] = {}
    for item in estimates:
        payload = item.to_dict()
        key = f"{payload['commodity_code']}:{payload['settlement_term']}"
        out[key] = {
            "status": payload.get("status"),
            "estimated_project_price": payload.get("estimated_project_price"),
            "method": payload.get("method"),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default="2026-08-05")
    parser.add_argument("--hours", default="8,10,12,14,16")
    parser.add_argument(
        "--market-store",
        type=Path,
        default=Path(
            "/srv/trading-bot/production-data/coin-intelligence/"
            "private-gold-live/market/market.sqlite3"
        ),
    )
    parser.add_argument(
        "--market-db",
        type=Path,
        default=Path(
            "/srv/trading-bot-three-site-staging-data/coin-intelligence/"
            "apps/telegram-price-poc/data/market_prices.sqlite3"
        ),
    )
    parser.add_argument(
        "--live-model",
        type=Path,
        default=Path(
            "/srv/trading-bot-three-site-staging-data/coin-intelligence/"
            "apps/coin-rate-estimator/runtime/model.json"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO_ROOT / "tmp" / "shadow-ml-evidence" / "fair_bakeoff_after_unit_fix.json",
    )
    args = parser.parse_args()
    hours = [int(part) for part in str(args.hours).split(",") if part.strip()]
    model = load_model(args.live_model)
    rows = []
    paired_errors: list[float] = []
    for as_of in _cutoffs(args.day, hours):
        print(f"cutoff {_iso(as_of)}", flush=True)
        op = operator_centers(model, args.market_db, as_of)
        co = commodity_centers(args.market_store, as_of)
        keys = sorted(set(op) | set(co))
        pairwise = []
        for key in keys:
            left = op.get(key) or {"status": "MISSING"}
            right = co.get(key) or {"status": "MISSING"}
            rel = None
            if (
                left.get("status") == "ESTIMATED"
                and right.get("status") == "ESTIMATED"
                and left.get("estimated_project_price")
                and right.get("estimated_project_price")
            ):
                a = float(left["estimated_project_price"])
                b = float(right["estimated_project_price"])
                rel = abs(a - b) / max(a, 1.0)
                paired_errors.append(rel)
            pairwise.append({"key": key, "operator": left, "commodity": right, "abs_pct": rel})
        rows.append({"as_of_utc": _iso(as_of), "pairs": pairwise})
    summary = {
        "generated_at_utc": _iso(datetime.now(timezone.utc)),
        "day": args.day,
        "hours": hours,
        "paired_estimated_count": len(paired_errors),
        "mean_abs_pct_operator_vs_commodity": (
            sum(paired_errors) / len(paired_errors) if paired_errors else None
        ),
        "median_abs_pct_operator_vs_commodity": (
            sorted(paired_errors)[len(paired_errors) // 2] if paired_errors else None
        ),
        "note": (
            "After Market Store unit repair, commodity engine should no longer "
            "be ~10× below operator on shared estimated cells."
        ),
        "cutoffs": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({k: summary[k] for k in summary if k != "cutoffs"}, ensure_ascii=False, indent=2))
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
