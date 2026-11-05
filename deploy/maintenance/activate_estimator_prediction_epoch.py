"""Explicitly select an already staged, complete, fresh estimator epoch.

Run inside the qualified snapshot receiver. This uses its normal authority API;
it does not rewrite observations, historical predictions or parser decisions.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

from core.market_intelligence.coin_rate_engine import COIN_SPECS
from core.market_intelligence.estimator_snapshot_receiver import (
    activate_private_prediction_authority,
)
from core.market_intelligence.private_pipeline_contracts import EstimatorSnapshotV2


def authority(path: Path) -> tuple[str, str]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT active_epoch,active_feed_mode "
            "FROM coin_estimate_prediction_authority WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise RuntimeError("prediction_authority_missing")
        return str(row[0]), str(row[1])
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--expected-old-epoch", required=True)
    parser.add_argument("--expected-model-version", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"coin-rate-engine-v[0-9]+", args.expected_model_version):
        raise RuntimeError("expected_model_version_invalid")
    with args.snapshot.open("rb") as source:
        raw = source.read(1_048_577)
    if len(raw) > 1_048_576:
        raise RuntimeError("snapshot_size_invalid")
    view = json.loads(raw)
    snapshot = EstimatorSnapshotV2.model_validate(view["snapshot"])
    if (
        view.get("contract") != "estimator_snapshot_web_view/1.0"
        or view.get("snapshot_hash") != snapshot.snapshot_id
        or view.get("snapshot_version") != snapshot.snapshot_version
        or view.get("feed_mode") != "PRIVATE_PRIMARY"
        or snapshot.feed_mode != "PRIVATE_PRIMARY"
        or snapshot.model_version != args.expected_model_version
        or snapshot.status != "OK"
    ):
        raise RuntimeError("qualified_primary_snapshot_required")
    now = datetime.now(timezone.utc)
    if not 0 <= (now - snapshot.generated_at_utc).total_seconds() <= 30:
        raise RuntimeError("fresh_primary_snapshot_required")
    expected_grid = {
        ("COIN_" + code, settlement)
        for code in COIN_SPECS
        for settlement in ("CASH", "TOMORROW")
    }
    if (
        len(snapshot.rates) != len(expected_grid)
        or {(rate.instrument, rate.settlement) for rate in snapshot.rates} != expected_grid
        or any(rate.status != "ESTIMATED" for rate in snapshot.rates)
    ):
        raise RuntimeError("complete_estimated_primary_grid_required")
    target = "PRIVATE_PRIMARY:" + snapshot.model_version
    previous = authority(args.ledger)
    if previous not in {
        (args.expected_old_epoch, "PRIVATE_PRIMARY"),
        (target, "PRIVATE_PRIMARY"),
    }:
        raise RuntimeError("unexpected_prediction_authority")
    if previous[0] != target:
        activate_private_prediction_authority(args.ledger, snapshot)
    if authority(args.ledger) != (target, "PRIVATE_PRIMARY"):
        raise RuntimeError("prediction_authority_verification_failed")
    print(json.dumps({
        "status": "ACTIVATED" if previous[0] != target else "ALREADY_ACTIVE",
        "previous_epoch": previous[0],
        "active_epoch": target,
        "snapshot_version": snapshot.snapshot_version,
        "snapshot_generated_at_utc": snapshot.generated_at_utc.isoformat(),
        "prediction_or_market_rows_rewritten": False,
    }))


if __name__ == "__main__":
    main()
