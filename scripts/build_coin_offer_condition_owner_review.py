#!/usr/bin/env python3
"""Build a local, blind owner-review pack for coin-offer conditions.

The pack contains exact normalized private offer text and must stay outside the
repository.  It samples only the sealed temporal evaluation partition and does
not expose weak labels, model probabilities, message IDs, or sender identity.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import html
import json
import os
from pathlib import Path
import random
from typing import Any, Sequence

from core.market_intelligence.coin_offer_conditions import (
    CONDITION_FAMILIES,
    masked_condition_model_text,
    normalize_offer_text,
)
from core.market_intelligence.coin_groups import CoinGroupMessageInput, parse_coin_group_offers
from scripts.train_coin_offer_condition_classifier import (
    TrainingRow,
    _connect_read_only,
    _opaque_digest,
    _safe_output_dir,
    chronological_three_way_split,
    load_training_rows,
)


PACK_VERSION = "coin-offer-condition-owner-review-v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def select_owner_review_rows(
    rows: Sequence[TrainingRow],
    *,
    sample_count: int,
    seed: int = 1729,
) -> list[TrainingRow]:
    """Select a deterministic, diverse sample from sealed evaluation only."""

    _, _, sealed = chronological_three_way_split(rows)
    if sample_count < 1 or sample_count > len(sealed):
        raise ValueError("owner_review_sample_count_out_of_range")
    rng = random.Random(seed)
    selected: dict[str, TrainingRow] = {}

    def add(pool: Sequence[TrainingRow], limit: int) -> None:
        candidates = [row for row in pool if row.opaque_digest not in selected]
        rng.shuffle(candidates)
        for row in candidates[:limit]:
            if len(selected) >= sample_count:
                return
            selected[row.opaque_digest] = row

    # Rare families get deliberate representation without revealing their
    # silver label in the review UI.
    for family in CONDITION_FAMILIES:
        add([row for row in sealed if family in row.families], 8)

    strata: dict[tuple[str, str, str, bool], list[TrainingRow]] = defaultdict(list)
    for row in sealed:
        strata[(row.group_code, row.settlement_term, row.session_phase, row.has_condition)].append(row)
    for key in sorted(strata):
        add(strata[key], 2)

    add(list(sealed), sample_count - len(selected))
    return sorted(selected.values(), key=lambda row: (row.event_time_utc, row.opaque_digest))


def _review_text_by_digest(database: Path, staging_database: Path) -> dict[str, str]:
    """Resolve exact normalized text transiently for the private owner pack."""

    source: list[tuple[str, str, str, str, str]] = []
    connection = _connect_read_only(database)
    try:
        rows = connection.execute(
            """
            SELECT m.source_html_file AS group_code,m.event_time_utc,
                   o.settlement,o.trade_form,o.source_text
            FROM offers o
            JOIN messages m ON m.import_id=o.import_id AND m.message_id=o.message_id
            JOIN imports i ON i.id=o.import_id
            WHERE m.source_html_file IN ('group_1','group_2')
              AND trim(COALESCE(o.source_text,'')) <> ''
              AND i.archive_path <> 'canonical-market-store'
            ORDER BY m.event_time_utc,o.id
            """
        ).fetchall()
    finally:
        connection.close()
    source.extend(
        (
            str(row["group_code"]),
            str(row["event_time_utc"]),
            str(row["settlement"] or "UNKNOWN").upper(),
            str(row["trade_form"] or "UNKNOWN").upper(),
            str(row["source_text"] or ""),
        )
        for row in rows
    )

    staging = _connect_read_only(staging_database)
    try:
        staged = staging.execute(
            """
            SELECT group_number,message_id,event_time_utc,available_at_utc,message_text
            FROM coin_group_staged_messages
            ORDER BY event_time_utc,group_number,message_id
            """
        ).fetchall()
    finally:
        staging.close()
    for row in staged:
        raw_message = str(row["message_text"] or "")
        for line_index, line in enumerate(raw_message.splitlines() or [raw_message]):
            if not line.strip():
                continue
            message = CoinGroupMessageInput(
                group_number=int(row["group_number"]),
                source_event_id=f"{int(row['message_id'])}:{line_index}",
                published_at_utc=str(row["event_time_utc"]),
                available_at_utc=str(row["available_at_utc"]),
                text=line,
            )
            for offer in parse_coin_group_offers(message):
                source.append(
                    (
                        f"group_{int(row['group_number'])}",
                        str(row["event_time_utc"]),
                        str(offer.settlement_term).upper(),
                        str(offer.trade_form).upper(),
                        line,
                    )
                )

    output: dict[str, str] = {}
    for group, event_time, settlement, trade_form, raw_text in source:
        model_text = masked_condition_model_text(raw_text)
        digest = _opaque_digest(
            group_code=group,
            event_time_utc=event_time,
            settlement_term=settlement,
            trade_form=trade_form,
            model_text=model_text,
        )
        output.setdefault(digest, normalize_offer_text(raw_text)[:512])
    return output


def _render_html(pack: dict[str, Any]) -> str:
    data = json.dumps(pack, ensure_ascii=False).replace("</", "<\\/")
    family_boxes = "".join(
        f'<label><input type="checkbox" value="{html.escape(family)}"> {html.escape(family)}</label>'
        for family in CONDITION_FAMILIES
    )
    return f"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>مرور شروط آفرهای سکه</title>
<style>
body{{font-family:Vazirmatn,Tahoma,sans-serif;max-width:980px;margin:auto;padding:24px;background:#f6f7f8;color:#17212b}}
.notice,.card{{background:#fff;border:1px solid #d9dee3;border-radius:14px;padding:16px;margin:12px 0}}
.text{{font-size:1.08rem;line-height:1.9;background:#f7f8fa;border-radius:10px;padding:12px;white-space:pre-wrap}}
.meta{{color:#596571;font-size:.88rem}} label{{display:inline-flex;gap:5px;margin:7px}}
textarea{{width:100%;min-height:56px}} button{{padding:10px 16px;border:0;border-radius:9px;background:#1769aa;color:white;cursor:pointer}}
</style></head><body>
<div class="notice"><h1>مرور نهایی مالک</h1><p>ابتدا متن را مستقل قضاوت کنید. هیچ weak label یا confidence مدل در این صفحه نمایش داده نمی‌شود. متن کامل خصوصی است؛ این فایل نباید منتشر یا وارد مخزن شود.</p><button id="save">دانلود برچسب‌ها</button></div>
<main id="root"></main><script id="pack" type="application/json">{data}</script>
<template id="families">{family_boxes}</template>
<script>
const pack=JSON.parse(document.getElementById('pack').textContent), root=document.getElementById('root');
for(const [i,s] of pack.samples.entries()){{
 const card=document.createElement('section'); card.className='card'; card.dataset.digest=s.sample_digest;
 card.innerHTML=`<div class="meta">${{i+1}} از ${{pack.samples.length}} · ${{s.group_code}} · ${{s.settlement_term}} · ${{s.session_phase}} · ${{s.event_time_utc}}</div><div class="text"></div><fieldset><legend>حکم مستقل</legend><label><input type="radio" name="status-${{i}}" value="CONDITIONAL"> شرط‌دار</label><label><input type="radio" name="status-${{i}}" value="UNCONDITIONAL"> بدون شرط</label><label><input type="radio" name="status-${{i}}" value="AMBIGUOUS"> مبهم</label></fieldset><fieldset class="family"><legend>خانواده‌ها (در صورت شرط‌دار)</legend>${{document.getElementById('families').innerHTML}}</fieldset><label>settlement صحیح <select class="settlement"><option value="">انتخاب نشده</option><option>CASH</option><option>TOMORROW</option><option>UNKNOWN</option></select></label><label>متن دقیق شرط <textarea class="condition-text"></textarea></label><label>deadline دقیق/مبهم (اختیاری)<input class="deadline" placeholder="مثلاً 14:00 یا AMBIGUOUS"></label><label>یادداشت کوتاه<textarea class="note"></textarea></label>`;
 card.querySelector('.text').textContent=s.private_offer_text; root.appendChild(card);
}}
document.getElementById('save').onclick=()=>{{
 const annotations=[...document.querySelectorAll('.card')].map(card=>({{
  sample_digest:card.dataset.digest,
  owner_status:card.querySelector('input[type=radio]:checked')?.value||null,
  owner_families:[...card.querySelectorAll('.family input:checked')].map(x=>x.value),
  owner_settlement:card.querySelector('.settlement').value||null,
  owner_condition_text:card.querySelector('.condition-text').value.trim(),
  owner_deadline:card.querySelector('.deadline').value.trim(),
  owner_note:card.querySelector('.note').value.trim()
 }}));
 const blob=new Blob([JSON.stringify({{schema_version:'coin-offer-condition-owner-annotations-v1',source_fingerprint:pack.source_fingerprint,annotations}},null,2)],{{type:'application/json'}});
 const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='coin-offer-condition-owner-annotations.json';a.click();URL.revokeObjectURL(a.href);
}};
</script></body></html>"""


def build(args: argparse.Namespace) -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    output = _safe_output_dir(args.output_dir, repository_root=repository_root)
    rows = load_training_rows(
        args.conversation_db,
        staging_database=args.staging_db,
        market_open_minute=args.market_open_minute,
        market_close_minute=args.market_close_minute,
    )
    replay_manifest = None
    if args.selection_manifest is not None:
        replay_manifest = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
        by_digest = {row.opaque_digest: row for row in rows}
        requested = [str(sample["sample_digest"]) for sample in replay_manifest["samples"]]
        if len(requested) != len(set(requested)):
            raise RuntimeError("owner_review_replay_digest_duplicate")
        missing_rows = [digest for digest in requested if digest not in by_digest]
        if missing_rows:
            raise RuntimeError("owner_review_replay_row_missing")
        selected = [by_digest[digest] for digest in requested]
    else:
        selected = select_owner_review_rows(rows, sample_count=args.sample_count, seed=args.seed)
    review_texts = _review_text_by_digest(args.conversation_db, args.staging_db)
    missing = [row.opaque_digest for row in selected if row.opaque_digest not in review_texts]
    if missing:
        raise RuntimeError("owner_review_private_text_resolution_failed")
    fingerprint = sha256()
    for row in rows:
        fingerprint.update(bytes.fromhex(row.opaque_digest))
    source_fingerprint = (
        str(replay_manifest["source_fingerprint"])
        if replay_manifest is not None
        else fingerprint.hexdigest()
    )
    pack = {
        "schema_version": PACK_VERSION,
        "created_at_utc": _utc_now(),
        "status": "PENDING_OWNER_REVIEW",
        "source_fingerprint": source_fingerprint,
        "selection": {
            "partition": "SEALED_MANIFEST_REPLAY"
            if replay_manifest is not None
            else "SEALED_TEMPORAL_EVALUATION_ONLY",
            "sample_count": len(selected),
            "seed": args.seed,
            "weak_labels_exposed": False,
            "model_predictions_exposed": False,
        },
        "privacy": {
            "private_normalized_text_present": True,
            "raw_numeric_text_present": True,
            "message_ids_present": False,
            "sender_identity_present": False,
            "must_remain_outside_repository": True,
        },
        "samples": [
            {
                "sample_digest": row.opaque_digest,
                "group_code": row.group_code,
                "event_time_utc": row.event_time_utc,
                "settlement_term": row.settlement_term,
                "trade_form": row.trade_form,
                "session_phase": row.session_phase,
                "private_offer_text": review_texts[row.opaque_digest],
            }
            for row in selected
        ],
    }
    json_path = output / "coin-offer-condition-owner-review.json"
    html_path = output / "coin-offer-condition-owner-review.html"
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_render_html(pack), encoding="utf-8")
    os.chmod(json_path, 0o600)
    os.chmod(html_path, 0o600)
    return {
        "status": pack["status"],
        "sample_count": len(selected),
        "source_fingerprint": pack["source_fingerprint"],
        "json": str(json_path),
        "json_sha256": sha256(json_path.read_bytes()).hexdigest(),
        "html": str(html_path),
        "html_sha256": sha256(html_path.read_bytes()).hexdigest(),
    }


def _minute(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":", 1))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise argparse.ArgumentTypeError("market time must be HH:MM")
    return hour * 60 + minute


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conversation-db", type=Path, required=True)
    parser.add_argument("--staging-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=240)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument(
        "--selection-manifest",
        type=Path,
        help="Replay an already-sealed review selection while resolving exact private text.",
    )
    parser.add_argument("--market-open", dest="market_open_minute", type=_minute, default=600)
    parser.add_argument("--market-close", dest="market_close_minute", type=_minute, default=900)
    result = build(parser.parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
