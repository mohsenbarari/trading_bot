#!/usr/bin/env python3
"""Validate shadow offer fields against nearby same-market offer anchors.

This is an evidence layer, not a price predictor and not a promotion step.  It
uses only high-confidence parsed offers as local anchors.  Price bands infer the
coin family; where Imam and Bahar overlap, the product rule (unnamed full coin
means Imam) remains authoritative.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE
COMPONENT = PIPE / 'offer_field_staging.sqlite3'
STAGE = PIPE / 'text_staging.sqlite3'
OUT = PIPE / 'offer_context_validation.sqlite3'
VERSION = 'offer-context-validation-shadow-v1.0'
WINDOW_SECONDS = 90 * 60

SCHEMA = '''
CREATE TABLE IF NOT EXISTS offer_context_validation (
 source_key TEXT NOT NULL,
 message_id TEXT NOT NULL,
 offer_index INTEGER NOT NULL,
 commodity_inference_basis TEXT NOT NULL,
 local_anchor_price INTEGER,
 anchor_count INTEGER NOT NULL,
 relative_distance REAL,
 validation_status TEXT NOT NULL,
 validation_confidence REAL NOT NULL,
 validator_version TEXT NOT NULL,
 created_at_utc TEXT NOT NULL,
 PRIMARY KEY(source_key,message_id,offer_index)
);
'''
NOW = lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

def parsed_time(value: str | None, day: str | None) -> float | None:
    raw = str(value or '')
    try:
        if 'T' in raw:
            return datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp()
        if ':' in raw and day:
            return datetime.fromisoformat(f'{day}T{raw}+00:00').timestamp()
    except ValueError:
        pass
    return None

def median(values: list[int]) -> int:
    values = sorted(values); n = len(values)
    return values[n // 2] if n % 2 else round((values[n//2-1] + values[n//2]) / 2)

def basis(offer: dict) -> str:
    if offer.get('commodity_method') == 'explicit':
        return 'EXPLICIT_TEXT'
    if offer.get('commodity') == 'امام':
        return 'DEFAULT_IMAM_FOR_UNNAMED_FULL_COIN'
    return 'PRICE_BAND'

def main() -> None:
    c = sqlite3.connect(COMPONENT); c.row_factory = sqlite3.Row
    c.execute('ATTACH DATABASE ? AS stage', (str(STAGE),))
    out = sqlite3.connect(OUT); out.executescript(SCHEMA); out.execute('DELETE FROM offer_context_validation')
    records = []
    for row in c.execute('''
      SELECT o.*,t.telegram_datetime,t.telegram_day FROM offer_component_candidates o
      JOIN stage.text_candidates t USING(source_key,message_id)
    '''):
        offer = json.loads(row['extracted_json'])
        records.append({**dict(row), 'offer': offer, 'when': parsed_time(row['telegram_datetime'], row['telegram_day'])})
    # Anchors need an unambiguous full price; a corrected tail never anchors
    # another corrected tail.  Both groups are the same market, but source keys
    # remain in the stored record and are never merged.
    anchors: dict[tuple[str,str], list[dict]] = {}
    for r in records:
        x = r['offer']
        if (r['extraction_status'] == 'SHADOW_ACCEPTED' and r['when'] is not None
                and float(x.get('confidence') or 0) >= .90 and x.get('price_method') == 'full'):
            anchors.setdefault((x['commodity'], x['settlement']), []).append(r)
    total = {'records': len(records), 'strong': 0, 'moderate': 0, 'insufficient': 0, 'conflict': 0}
    for r in records:
        x = r['offer']; key = (x['commodity'], x['settlement'])
        nearby = [] if r['when'] is None else [a for a in anchors.get(key, [])
            if not (a['source_key'] == r['source_key'] and a['message_id'] == r['message_id'])
            and abs(a['when'] - r['when']) <= WINDOW_SECONDS]
        prices = [int(a['offer']['price']) for a in nearby]
        anchor = median(prices) if prices else None
        distance = abs(int(x['price']) - anchor) / anchor if anchor else None
        if anchor is None:
            status, confidence = 'INSUFFICIENT_LOCAL_EVIDENCE', .0; total['insufficient'] += 1
        elif distance <= .012:
            status, confidence = 'STRONG_LOCAL_AGREEMENT', .96; total['strong'] += 1
        elif distance <= .030:
            status, confidence = 'MODERATE_LOCAL_AGREEMENT', .80; total['moderate'] += 1
        else:
            status, confidence = 'LOCAL_PRICE_CONFLICT', .20; total['conflict'] += 1
        out.execute('INSERT INTO offer_context_validation VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (r['source_key'],r['message_id'],r['offer_index'],basis(x),anchor,len(prices),distance,status,confidence,VERSION,NOW()))
    out.commit()
    print(json.dumps({**total, 'integrity':out.execute('PRAGMA integrity_check').fetchone()[0], 'version':VERSION}, ensure_ascii=False))

if __name__ == '__main__':
    main()
