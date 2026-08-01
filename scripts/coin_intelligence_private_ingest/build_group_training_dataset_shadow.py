#!/usr/bin/env python3
"""Build a minimal, non-production group-offer training dataset.

Inputs are existing shadow tables only.  The output contains no Telegram IDs,
source keys, URLs, peer IDs, raw envelopes, or channel metadata.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE
COMPONENT = PIPE / 'offer_field_staging.sqlite3'
RAW = PIPE / 'raw_events.sqlite3'
STAGE = PIPE / 'text_staging.sqlite3'
TRADES = PIPE / 'trade_link_staging.sqlite3'
OUT = PIPE / 'group_training_dataset_shadow.sqlite3'
VERSION = 'group-training-shadow-v1.0'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS dataset_runs (
 id INTEGER PRIMARY KEY,
 created_at_utc TEXT NOT NULL,
 version TEXT NOT NULL,
 offer_count INTEGER NOT NULL,
 trade_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS offer_training_examples (
 id INTEGER PRIMARY KEY,
 occurred_at_utc TEXT,
 group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
 offerer_name TEXT,
 offer_text TEXT NOT NULL,
 commodity TEXT NOT NULL,
 price INTEGER NOT NULL CHECK(price > 0),
 quantity INTEGER,
 side TEXT NOT NULL,
 settlement TEXT NOT NULL,
 trade_form TEXT NOT NULL,
 extraction_confidence REAL NOT NULL,
 training_weight REAL NOT NULL,
 dataset_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS confirmed_trade_training_examples (
 id INTEGER PRIMARY KEY,
 occurred_at_utc TEXT,
 group_number INTEGER NOT NULL CHECK(group_number IN (1,2)),
 offerer_name TEXT,
 counterparty_name TEXT,
 offer_text TEXT NOT NULL,
 commodity TEXT NOT NULL,
 price INTEGER NOT NULL CHECK(price > 0),
 quantity INTEGER,
 side TEXT NOT NULL,
 settlement TEXT NOT NULL,
 trade_form TEXT NOT NULL,
 confirmation_type TEXT NOT NULL,
 extraction_confidence REAL NOT NULL,
 training_weight REAL NOT NULL,
 dataset_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offer_training_when ON offer_training_examples(occurred_at_utc,commodity,settlement);
CREATE INDEX IF NOT EXISTS idx_trade_training_when ON confirmed_trade_training_examples(occurred_at_utc,commodity,settlement);
'''
NOW = lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')

def name_and_time(records: dict[tuple[str,str], dict], display_names: dict[tuple[str,str], str], source: str, message: str | None) -> tuple[str | None, str | None]:
    if message is None:
        return None, None
    record = records.get((source, str(message)), {})
    # Never fall back to peer IDs in the final dataset.
    name = record.get('sender_name') or display_names.get((source, str(record.get('sender_peer_id') or ''))) or None
    when = record.get('telegram_datetime') or None
    return str(name) if name else None, str(when) if when else None

def main() -> None:
    component = sqlite3.connect(COMPONENT); component.row_factory = sqlite3.Row
    raw = sqlite3.connect(RAW); raw.row_factory = sqlite3.Row
    stage = sqlite3.connect(STAGE); stage.row_factory = sqlite3.Row
    trades = sqlite3.connect(TRADES); trades.row_factory = sqlite3.Row
    out = sqlite3.connect(OUT); out.executescript(SCHEMA)
    records = {}
    for row in raw.execute("SELECT source_key,message_id,record_json FROM source_messages_current WHERE source_key IN ('account2_group1','account2_group2')"):
        records[(row['source_key'],row['message_id'])] = json.loads(row['record_json'])
    # Internal-only display-name recovery for incomplete source records.  The
    # peer ID is used as a join key here and is never projected to the output.
    display_names: dict[tuple[str,str], str] = {}
    for (source, _), record in records.items():
        peer = str(record.get('sender_peer_id') or '')
        name = str(record.get('sender_name') or '').strip()
        if peer and name:
            display_names[(source, peer)] = name
    timestamps = {(r['source_key'],r['message_id']): r['telegram_datetime'] for r in stage.execute("SELECT source_key,message_id,telegram_datetime FROM text_candidates WHERE source_key IN ('account2_group1','account2_group2')")}
    out.execute('DELETE FROM offer_training_examples')
    out.execute('DELETE FROM confirmed_trade_training_examples')
    accepted: dict[tuple[str,str], sqlite3.Row] = {}
    for row in component.execute("SELECT * FROM offer_component_candidates WHERE extraction_status='SHADOW_ACCEPTED' ORDER BY source_key,message_id,offer_index"):
        data = json.loads(row['extracted_json'])
        key = (row['source_key'],row['message_id'])
        accepted.setdefault(key, row)
        offerer, raw_when = name_and_time(records, display_names, *key)
        out.execute('''INSERT INTO offer_training_examples(
          occurred_at_utc,group_number,offerer_name,offer_text,commodity,price,quantity,side,settlement,trade_form,extraction_confidence,training_weight,dataset_version
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (timestamps.get(key) or raw_when,row['group_number'],offerer,data.get('source_text') or '',data['commodity'],int(data['price']),data.get('quantity'),data['side'],data['settlement'],data['trade_form'],float(data['confidence']),1.0,VERSION))
    trades_added = 0
    for row in trades.execute('SELECT * FROM linked_confirmed_trades'):
        key = (row['source_key'], str(row['offer_message_id'] or ''))
        # No offer-key linkage exists in final data; it is used only here to
        # enforce the accepted-offer gate before projection.
        if key not in accepted:
            continue
        data = json.loads(row['trade_json'])
        offerer, offer_when = name_and_time(records, display_names, row['source_key'], row['offer_message_id'])
        counterparty, _ = name_and_time(records, display_names, row['source_key'], row['request_message_id'])
        group_number = json.loads(accepted[key]['extracted_json'])['group_number']
        out.execute('''INSERT INTO confirmed_trade_training_examples(
          occurred_at_utc,group_number,offerer_name,counterparty_name,offer_text,commodity,price,quantity,side,settlement,trade_form,confirmation_type,extraction_confidence,training_weight,dataset_version
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (data.get('event_time_utc') or offer_when,group_number,offerer,counterparty,
           json.loads(accepted[key]['extracted_json']).get('source_text') or '',data['commodity'],int(data['price']),data.get('quantity'),data['side'],data['settlement'],data['trade_form'],data['confirmation_type'],float(data['confidence']),4.0,VERSION))
        trades_added += 1
    offers = out.execute('SELECT count(*) FROM offer_training_examples').fetchone()[0]
    out.execute('INSERT INTO dataset_runs(created_at_utc,version,offer_count,trade_count) VALUES(?,?,?,?)',(NOW(),VERSION,offers,trades_added))
    out.commit()
    print(json.dumps({'offers':offers,'confirmed_trades':trades_added,'integrity':out.execute('PRAGMA integrity_check').fetchone()[0],'version':VERSION},ensure_ascii=False))

if __name__ == '__main__':
    main()
