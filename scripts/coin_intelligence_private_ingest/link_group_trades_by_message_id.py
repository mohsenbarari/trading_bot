#!/usr/bin/env python3
"""Apply the proven reply-chain trade logic to new group data using exact IDs."""
from __future__ import annotations
import json
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def _pipeline_root() -> Path:
    """Use repository paths in tests and the pinned runtime source in service."""

    try:
        from scripts.coin_intelligence_private_ingest.runtime_paths import (
            PIPELINE_ROOT,
        )
    except ModuleNotFoundError:
        runtime_source = Path(
            os.environ.get(
                "COIN_INTELLIGENCE_RUNTIME_SOURCE",
                "/srv/trading-bot-three-site-staging-data/coin-intelligence/runtime-source",
            )
        ).resolve()
        if not (runtime_source / "core").is_dir():
            raise RuntimeError("coin-intelligence runtime source is unavailable")
        sys.path.insert(0, str(runtime_source))
        private_root = Path(
            os.environ.get(
                "COIN_PRIVATE_EVENT_ROOT",
                "/srv/trading-bot-three-site-staging-data/coin-intelligence/private-channel-ingest",
            )
        ).resolve()
        return private_root / "pipeline"
    return PIPELINE_ROOT


PIPE = _pipeline_root()

from core.market_intelligence.group_trade_parser import analyze_reply_trades, EXTRACTOR_VERSION
OUT=PIPE/'trade_link_staging.sqlite3'
SCHEMA='''CREATE TABLE IF NOT EXISTS linked_trade_requests(source_key TEXT NOT NULL,request_message_id TEXT NOT NULL,offer_message_id TEXT NOT NULL,signal TEXT NOT NULL,quantity INTEGER,confidence REAL NOT NULL,evidence_json TEXT NOT NULL,PRIMARY KEY(source_key,request_message_id)); CREATE TABLE IF NOT EXISTS linked_confirmed_trades(source_key TEXT NOT NULL,confirmation_message_id TEXT NOT NULL,offer_message_id TEXT,request_message_id TEXT,trade_json TEXT NOT NULL,confidence REAL NOT NULL,PRIMARY KEY(source_key,confirmation_message_id)); CREATE TABLE IF NOT EXISTS link_review_queue(source_key TEXT NOT NULL,message_id TEXT NOT NULL,reason TEXT NOT NULL,confidence REAL NOT NULL,context_json TEXT NOT NULL,PRIMARY KEY(source_key,message_id,reason));'''
def iso(r):
 v=str(r.get('telegram_datetime') or '')
 if 'T' in v:return v.replace('Z','+00:00')
 d=str(r.get('telegram_day') or '1970-01-01'); t=str(r.get('datetime') or '00:00')
 return d+'T'+(t if ':' in t else '00:00')+':00+00:00'
def main():
 raw=sqlite3.connect(PIPE/'raw_events.sqlite3'); st=sqlite3.connect(PIPE/'text_staging.sqlite3'); st.row_factory=sqlite3.Row; st.execute("attach database ? as filterdb",(str(PIPE/'group_filter.sqlite3'),))
 components=sqlite3.connect(PIPE/'offer_field_staging.sqlite3'); components.row_factory=sqlite3.Row
 out=sqlite3.connect(OUT); out.executescript(SCHEMA)
 for source in ('account2_group1','account2_group2'):
  records={mid:json.loads(blob) for mid,blob in raw.execute('select message_id,record_json from source_messages_current where source_key=?',(source,))}
  rows=st.execute("select t.*,f.decision from text_candidates t left join filterdb.filter_decisions f using(source_key,message_id) where t.source_key=? order by coalesce(t.telegram_datetime,''),t.message_id",(source,)).fetchall()
  messages=[]; offers=defaultdict(list)
  for r in rows:
   x=records.get(r['message_id'],{}); messages.append({'message_id':int(r['message_id']),'date_utc':iso(x),'from_name':str(x.get('sender_name') or ''),'text':r['text'],'reply_to_message_id':int(x['reply_message_id']) if x.get('reply_message_id') is not None else None})
  for component in components.execute("select message_id,extracted_json from offer_component_candidates where source_key=? and extraction_status='SHADOW_ACCEPTED' order by message_id,offer_index",(source,)):
   offers[int(component['message_id'])].append(json.loads(component['extracted_json']))
  analysis=analyze_reply_trades(messages,offers)
  out.execute('delete from linked_trade_requests where source_key=?',(source,)); out.execute('delete from linked_confirmed_trades where source_key=?',(source,)); out.execute('delete from link_review_queue where source_key=?',(source,))
  for x in analysis['requests']:
   out.execute('insert into linked_trade_requests values(?,?,?,?,?,?,?)',(source,str(x['request_message_id']),str(x['offer_message_id']),x['signal'],x.get('quantity'),float(x.get('confidence',0.0)),json.dumps(x,ensure_ascii=False)))
  for x in analysis['accepted_trades']:
   out.execute('insert into linked_confirmed_trades values(?,?,?,?,?,?)',(source,str(x['confirmation_message_id']),str(x['offer_message_id']) if x.get('offer_message_id') else None,str(x['request_message_id']) if x.get('request_message_id') else None,json.dumps(x,ensure_ascii=False),float(x.get('confidence',0.0))))
  for x in analysis['review_items']:
   out.execute('insert or replace into link_review_queue values(?,?,?,?,?)',(source,str(x['message_id']),x['reason'],float(x['confidence']),json.dumps(x.get('context_message_ids') or [])))
  print(json.dumps({'source':source,'messages':len(messages),'linked_offer_roots':len(offers),'requests':len(analysis['requests']),'confirmed':len(analysis['accepted_trades']),'review':len(analysis['review_items']),'extractor_version':EXTRACTOR_VERSION},ensure_ascii=False))
 out.commit(); print('integrity='+out.execute('pragma integrity_check').fetchone()[0])
 components.close(); st.close(); raw.close(); out.close()
if __name__=='__main__':main()
