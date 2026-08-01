#!/usr/bin/env python3
"""Enforce the three-day retention policy for rebuildable pipeline data.

This never touches the active market database, active conversation/model data,
model artifacts, or backups.  It only removes source payloads and derived
staging rows that can be rebuilt or have already been promoted.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE
RETENTION=timedelta(days=3)

def cutoff(): return datetime.now(timezone.utc)-RETENTION
def parse(value):
 try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(timezone.utc)
 except (TypeError,ValueError):return None
def connect(name):
 c=sqlite3.connect(PIPE/name); c.execute('pragma busy_timeout=15000'); return c
def delete_before(conn,table,column,bound):
 return conn.execute(f"delete from {table} where {column} < ?",(bound.isoformat().replace('+00:00','Z'),)).rowcount
def main():
 bound=cutoff(); report={'cutoff_utc':bound.isoformat().replace('+00:00','Z')}
 raw=connect('raw_events.sqlite3'); raw.row_factory=sqlite3.Row
 keys=[]
 for row in raw.execute('select source_key,message_id,record_json,updated_at_utc from source_messages_current'):
  try: event_time=json.loads(row['record_json']).get('telegram_datetime')
  except json.JSONDecodeError: event_time=None
  stamp=parse(event_time) or parse(row['updated_at_utc'])
  if stamp is not None and stamp<bound: keys.append((row['source_key'],row['message_id']))
 version_ids=[]
 for row in raw.execute('select id,record_json,first_ingested_at_utc from raw_message_versions'):
  try: event_time=json.loads(row['record_json']).get('telegram_datetime')
  except json.JSONDecodeError: event_time=None
  stamp=parse(event_time) or parse(row['first_ingested_at_utc'])
  if stamp is not None and stamp<bound: version_ids.append((row['id'],))
 raw.execute('begin')
 raw.executemany('delete from raw_message_versions where id=?',version_ids)
 raw.executemany('delete from raw_message_versions where source_key=? and message_id=?',keys)
 raw.executemany('delete from source_messages_current where source_key=? and message_id=?',keys)
 raw.commit(); report['raw_source_messages_deleted']=len(keys); report['raw_message_versions_deleted']=len(version_ids)

 text=connect('text_staging.sqlite3'); text.row_factory=sqlite3.Row
 text_keys=[]
 for r in text.execute('select source_key,message_id,telegram_datetime,updated_at_utc from text_candidates'):
  stamp=parse(r['telegram_datetime']) or parse(r['updated_at_utc'])
  if stamp is not None and stamp<bound:text_keys.append((r['source_key'],r['message_id']))
 text.executemany('delete from text_candidates where source_key=? and message_id=?',text_keys); text.commit(); report['text_candidates_deleted']=len(text_keys)
 staging=connect('gold_offer_staging.sqlite3'); report['gold_offer_events_deleted']=delete_before(staging,'gold_offer_events','occurred_at_utc',bound); staging.commit()
 life=connect('gold_lifecycle_shadow.sqlite3')
 old_ids=[r[0] for r in life.execute('select source_message_id from gold_offer_lifecycle where offered_at_utc < ?',(report['cutoff_utc'],))]
 life.executemany('delete from gold_model_events_shadow where source_message_id=?',[(x,) for x in old_ids])
 life.executemany('delete from gold_offer_lifecycle where source_message_id=?',[(x,) for x in old_ids]); life.commit(); report['gold_lifecycle_deleted']=len(old_ids)
 minute=connect('gold_minute_features_shadow.sqlite3'); report['gold_minute_features_deleted']=delete_before(minute,'gold_market_minute_features','minute_utc',bound); minute.commit()
 regime=connect('gold_market_regime_shadow.sqlite3'); report['gold_market_regimes_deleted']=delete_before(regime,'gold_market_regimes','minute_utc',bound); report['gold_regime_consensus_deleted']=delete_before(regime,'gold_market_regime_consensus','minute_utc',bound); regime.commit()
 print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
