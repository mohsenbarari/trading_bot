#!/usr/bin/env python3
"""Export raw-text-free owner decisions for offline calibration/evaluation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path

from core.market_intelligence.coin_condition_review import (
    ConditionReviewStore,
    load_owner_pack,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def export(args: argparse.Namespace) -> dict[str, object]:
    repository = Path(__file__).resolve().parents[1]
    output = args.output.expanduser().resolve()
    for prohibited in (
        repository,
        Path("/srv/trading-bot/production-data").resolve(),
    ):
        try:
            output.relative_to(prohibited)
        except ValueError:
            continue
        raise ValueError("condition_review_export_must_be_external")
    sealed, pack_status = load_owner_pack(args.owner_pack)
    decisions = ConditionReviewStore(args.review_db).load()
    sealed_ids = {sample.sample_digest for sample in sealed}
    annotations = []
    for digest in sorted(sealed_ids & decisions.keys()):
        decision = decisions[digest]
        annotations.append(
            {
                "sample_digest": digest,
                "owner_status": decision["owner_status"],
                "owner_families": decision["owner_families"],
                "owner_settlement": decision["owner_settlement"],
                "owner_condition_spans": decision["owner_condition_spans"],
                "owner_deadline": decision["owner_deadline"],
                "review_revision": decision["review_revision"],
            }
        )
    payload = {
        "schema_version": "coin-offer-condition-owner-annotations-v2",
        "created_at_utc": _utc_now(),
        "source_fingerprint": pack_status["source_fingerprint"],
        "sealed_sample_count": len(sealed),
        "reviewed_sample_count": len(annotations),
        "complete": len(annotations) == len(sealed) and len(sealed) > 0,
        "raw_text_retained": False,
        "reviewer_identity_retained": False,
        "annotations": annotations,
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(output, 0o600)
    return {
        "status": "COMPLETE" if payload["complete"] else "PARTIAL",
        "sealed_sample_count": len(sealed),
        "reviewed_sample_count": len(annotations),
        "output": str(output),
        "sha256": sha256(output.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-db", type=Path, required=True)
    parser.add_argument("--owner-pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    result = export(parser.parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
