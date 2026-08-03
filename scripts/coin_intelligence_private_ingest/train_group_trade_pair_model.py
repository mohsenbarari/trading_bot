#!/usr/bin/env python3
"""Train a chain-purged second-opinion model for group trade replies.

Exact reply/message linking remains deterministic and authoritative.  This
candidate only scores the combined offer/reply wording for later comparison;
it cannot create a confirmed trade or a training label by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

try:
    from scripts.coin_intelligence_private_ingest.runtime_paths import (
        CONVERSATION_LABEL_DB,
        PIPELINE_ROOT,
    )
    from scripts.coin_intelligence_private_ingest.train_group_noise_filter import (
        choose_thresholds,
        fit,
        metrics,
        operational_metrics,
    )
except ModuleNotFoundError:  # Standalone immutable runtime deployment.
    import sys

    PIPELINE_ROOT = Path(__file__).resolve().parent
    CONVERSATION_LABEL_DB = (
        PIPELINE_ROOT.parents[1]
        / "apps/coin-intelligence/data/conversation_events.sqlite3"
    )
    sys.path.insert(0, str(PIPELINE_ROOT))
    from train_group_noise_filter import (  # type: ignore[no-redef]
        choose_thresholds,
        fit,
        metrics,
        operational_metrics,
    )


VERSION = "group-trade-pair-nb-v2.2-active-quality-gated-chain-purged"
DEFAULT_OUTPUT = PIPELINE_ROOT / "group_trade_pair_nb_v2.json"


def _rows(path: Path) -> list[tuple[str, str, int, str]]:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    positive = connection.execute(
        """SELECT t.event_time_utc,
                  COALESCE(offer_message.text,'') || ' [REPLY] ' ||
                    COALESCE(confirm_message.text,''),
                  1,
                  CAST(t.import_id AS TEXT) || ':' ||
                    CAST(t.offer_message_id AS TEXT)
        FROM confirmed_trades AS t
        JOIN trade_market_quality AS quality
          ON quality.trade_id=t.id
        JOIN messages AS confirm_message
          ON confirm_message.import_id=t.import_id
         AND confirm_message.message_id=t.confirmation_message_id
        LEFT JOIN messages AS offer_message
          ON offer_message.import_id=t.import_id
         AND offer_message.message_id=t.offer_message_id
        WHERE t.offer_message_id IS NOT NULL
          AND quality.training_eligible=1
        ORDER BY t.event_time_utc,t.id"""
    ).fetchall()
    negative = connection.execute(
        """SELECT reply.event_time_utc,
                  COALESCE(offer_message.text,'') || ' [REPLY] ' ||
                    COALESCE(reply.text,''),
                  0,
                  CAST(offer.import_id AS TEXT) || ':' ||
                    CAST(offer.message_id AS TEXT)
        FROM messages AS reply
        JOIN offers AS offer
          ON offer.import_id=reply.import_id
         AND offer.message_id=reply.reply_to_message_id
        JOIN messages AS offer_message
          ON offer_message.import_id=offer.import_id
         AND offer_message.message_id=offer.message_id
        LEFT JOIN confirmed_trades AS trade
          ON trade.import_id=reply.import_id
         AND trade.confirmation_message_id=reply.message_id
        WHERE trade.confirmation_message_id IS NULL
        ORDER BY reply.event_time_utc,reply.message_id"""
    ).fetchall()
    connection.close()
    return sorted([tuple(row) for row in positive + negative])


def _chain_split(rows: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    by_chain: dict[str, list[tuple]] = defaultdict(list)
    for row in rows:
        by_chain[str(row[3])].append(row)
    chains = sorted(
        by_chain,
        key=lambda chain: min(str(row[0]) for row in by_chain[chain]),
    )
    cutoff = max(1, int(len(chains) * 0.80))
    training_chains = set(chains[:cutoff])
    training = [row for row in rows if str(row[3]) in training_chains]
    holdout = [row for row in rows if str(row[3]) not in training_chains]
    return training, holdout


def train(label_database: Path, output: Path) -> dict:
    rows = _rows(label_database)
    training, holdout = _chain_split(rows)
    validation_model = fit(training)
    keep_threshold, reject_threshold = choose_thresholds(
        validation_model, holdout
    )
    holdout_metrics = metrics(validation_model, holdout)
    operational = operational_metrics(
        validation_model,
        holdout,
        keep_threshold=keep_threshold,
        reject_threshold=reject_threshold,
    )
    full = fit(rows)
    fingerprint = hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    artifact = {
        "version": VERSION,
        "label_fingerprint_sha256": fingerprint,
        "role": (
            "SECOND_OPINION_REJECT_ONLY_NEVER_AUTO_CONFIRM_OR_LABEL"
            if keep_threshold > 1.0
            else "SECOND_OPINION_ONLY_NEVER_AUTO_LABEL"
        ),
        "training": {
            "rows": len(rows),
            "positive": sum(int(row[2]) == 1 for row in rows),
            "negative": sum(int(row[2]) == 0 for row in rows),
            "independent_chains": len({str(row[3]) for row in rows}),
        },
        "split": "chronological_80_20_purged_by_offer_chain",
        "thresholds": {
            "auto_keep": keep_threshold,
            "auto_reject": reject_threshold,
        },
        "holdout": holdout_metrics,
        "holdout_operational": operational,
        "model": full,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(artifact, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(output)
    return {
        key: artifact[key]
        for key in (
            "version",
            "role",
            "training",
            "split",
            "thresholds",
            "holdout",
            "holdout_operational",
        )
    } | {"output": str(output)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label-database", type=Path, default=CONVERSATION_LABEL_DB
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(train(args.label_database, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
