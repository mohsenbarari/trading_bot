#!/usr/bin/env python3
"""Append accepted live-group shadow data to a copy of active conversation DB.

The active DB is read-only input.  This preserves the established model schema
and creates a versioned candidate for evaluation/promotion.
"""
from __future__ import annotations
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.coin_intelligence_private_ingest.runtime_paths import (
    CONVERSATION_DB as ACTIVE,
    PIPELINE_ROOT as PIPE,
)
COMPONENT = PIPE / 'offer_field_staging.sqlite3'
RAW = PIPE / 'raw_events.sqlite3'
STAGE = PIPE / 'text_staging.sqlite3'
TRADES = PIPE / 'trade_link_staging.sqlite3'
OUT = PIPE / 'conversation_events.live-group-shadow.candidate.sqlite3'
REPORT = PIPE / 'conversation_candidate_import.latest.json'
VERSION = 'live-group-shadow-import-v1.0'

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def integer(v):
    try: return int(v) if v is not None else None
    except (ValueError,TypeError): return None

def main() -> None:
    temp=OUT.with_suffix('.sqlite3.tmp')
    if temp.exists(): temp.unlink()
    shutil.copy2(ACTIVE,temp)
    out=sqlite3.connect(temp); out.row_factory=sqlite3.Row
    component=sqlite3.connect(COMPONENT); component.row_factory=sqlite3.Row
    raw=sqlite3.connect(RAW); raw.row_factory=sqlite3.Row
    stage=sqlite3.connect(STAGE); stage.row_factory=sqlite3.Row
    trade=sqlite3.connect(TRADES); trade.row_factory=sqlite3.Row
    raw_records={(r['source_key'],r['message_id']):json.loads(r['record_json']) for r in raw.execute("SELECT source_key,message_id,record_json FROM source_messages_current WHERE source_key IN ('account2_group1','account2_group2')")}
    stage_times={(r['source_key'],r['message_id']):r['telegram_datetime'] for r in stage.execute("SELECT source_key,message_id,telegram_datetime FROM text_candidates WHERE source_key IN ('account2_group1','account2_group2')")}
    accepted=component.execute("SELECT * FROM offer_component_candidates WHERE extraction_status='SHADOW_ACCEPTED' ORDER BY source_key,message_id,offer_index").fetchall()
    accepted_keys={(r['source_key'],r['message_id']) for r in accepted}
    # The active conversation DB is the durable model-ingest cursor.  A message
    # is imported only once; later live batches contain only unseen records.
    existing_message_ids={int(r[0]) for r in out.execute('SELECT DISTINCT message_id FROM messages')}
    existing_confirmation_ids={int(r[0]) for r in out.execute('SELECT DISTINCT confirmation_message_id FROM confirmed_trades')}
    new_accepted=[r for r in accepted if integer(r['message_id']) not in existing_message_ids]
    source_hash=sha256(COMPONENT)
    out.execute('BEGIN IMMEDIATE')
    needed={(r['source_key'],r['message_id']) for r in new_accepted}
    eligible_trades=[]
    for row in trade.execute('SELECT * FROM linked_confirmed_trades'):
        key=(row['source_key'],str(row['offer_message_id'] or ''))
        if key in accepted_keys and integer(row['confirmation_message_id']) not in existing_confirmation_ids:
            eligible_trades.append(row)
            for message in (row['request_message_id'],row['confirmation_message_id']):
                if message is not None: needed.add((row['source_key'],str(message)))
    if not new_accepted and not eligible_trades:
        out.rollback(); out.close(); temp.unlink(missing_ok=True)
        report={'version':VERSION,'status':'NO_NEW_ACCEPTED_RECORDS','active_source_sha256':sha256(ACTIVE),'inserted_offers':0,'inserted_confirmed_trades':0}
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False)); return
    cur=out.execute('''INSERT INTO imports(archive_path,archive_sha256,imported_at_utc,cutoff_utc,message_count,retained_message_count,dropped_message_count,extractor_version) VALUES(?,?,?,?,?,?,?,?)''',
        ('private_telegram_group_shadow_accepted',source_hash,now(),now(),len(needed),len(needed),0,VERSION))
    import_id=cur.lastrowid
    inserted_messages=0
    for source,message_id in sorted(needed):
        if integer(message_id) in existing_message_ids:
            continue
        r=raw_records.get((source,message_id))
        if not r: continue
        group_number=1 if source=='account2_group1' else 2
        event=str(stage_times.get((source,message_id)) or r.get('telegram_datetime') or now())
        out.execute('''INSERT INTO messages(import_id,message_id,event_time_utc,event_time_tehran,sender_hash,text,reply_to_message_id,source_html_file,roles_json,relevance_json) VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (import_id,integer(message_id),event,event,None,str(r.get('text') or ''),integer(r.get('reply_message_id')),f'group_{group_number}',json.dumps({'group_number':group_number}),json.dumps({'source':'accepted_live_group_shadow'})))
        inserted_messages+=1
    for row in new_accepted:
        x=json.loads(row['extracted_json'])
        out.execute('''INSERT INTO offers(import_id,message_id,offer_index,commodity,price,quantity,side,settlement,trade_form,confidence,source_text,price_raw,price_method,commodity_method,quantity_method) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (import_id,integer(row['message_id']),row['offer_index'],x['commodity'],int(x['price']),x.get('quantity'),x['side'],x['settlement'],x['trade_form'],float(x['confidence']),x.get('source_text') or '',x.get('price_raw'),x.get('price_method'),x.get('commodity_method'),x.get('quantity_method')))
    for row in eligible_trades:
        x=json.loads(row['trade_json'])
        out.execute('''INSERT INTO confirmed_trades(import_id,confirmation_message_id,offer_message_id,request_message_id,event_time_utc,commodity,price,price_raw,price_method,quantity,quantity_method,reported_quantity,is_aggregate,training_eligible,side,settlement,trade_form,confidence,confirmation_type,evidence_json,context_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (import_id,integer(row['confirmation_message_id']),integer(row['offer_message_id']),integer(row['request_message_id']),x['event_time_utc'],x['commodity'],int(x['price']),x.get('price_raw'),x.get('price_method'),x.get('quantity'),x.get('quantity_method'),x.get('reported_quantity'),int(bool(x.get('is_aggregate'))),int(bool(x.get('training_eligible'))),x['side'],x['settlement'],x['trade_form'],float(x['confidence']),x['confirmation_type'],json.dumps(x.get('evidence') or [],ensure_ascii=False),json.dumps({'status':x.get('status')},ensure_ascii=False)))
    out.commit()
    integrity=out.execute('PRAGMA integrity_check').fetchone()[0]
    counts={t:out.execute(f'SELECT count(*) FROM {t}').fetchone()[0] for t in ('messages','offers','confirmed_trades')}
    out.close()
    if integrity!='ok': raise RuntimeError(integrity)
    temp.replace(OUT)
    report={'version':VERSION,'status':'CANDIDATE_READY','active_source_sha256':sha256(ACTIVE),'candidate_sha256':sha256(OUT),'import_id':import_id,'inserted_messages':inserted_messages,'inserted_offers':len(new_accepted),'inserted_confirmed_trades':len(eligible_trades),'total_counts':counts,'integrity':integrity,'candidate':str(OUT)}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
