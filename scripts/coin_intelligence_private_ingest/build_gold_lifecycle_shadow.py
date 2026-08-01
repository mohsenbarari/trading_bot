#!/usr/bin/env python3
"""Materialize offer/trade lifecycle events from normalized gold staging.

This preserves the late trade-crawler update as a separate effective-time event
while retaining the originating offer.  Ignored/review records are visible for
audit but never marked model-eligible.
"""
from __future__ import annotations
import json
import sqlite3
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from statistics import median

from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE
SOURCE=PIPE/'gold_offer_staging.sqlite3'; OUT=PIPE/'gold_lifecycle_shadow.sqlite3'
VERSION='gold-lifecycle-shadow-v1.2'; NOW=lambda:datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
SCHEMA='''
CREATE TABLE IF NOT EXISTS gold_offer_lifecycle (
 source_message_id TEXT PRIMARY KEY,
 offered_at_utc TEXT,
 quote_price INTEGER,
 quote_price_raw TEXT,
 quantity INTEGER,
 side TEXT,
 trade_form TEXT,
 settlement TEXT,
 paper_variant TEXT,
 trade_status TEXT,
 traded_quantity INTEGER,
 traded_at_utc TEXT,
 trade_time_source TEXT,
 trade_delay_seconds REAL,
 parser_status TEXT NOT NULL,
 model_eligible INTEGER NOT NULL,
 lifecycle_version TEXT NOT NULL,
 updated_at_utc TEXT NOT NULL,
 edited_at_utc TEXT,
 is_conditional INTEGER NOT NULL DEFAULT 0,
 condition_reason TEXT,
 trade_evidence TEXT,
 market_check_status TEXT,
 market_reference_price INTEGER,
 market_deviation_bps REAL,
 model_weight REAL NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS gold_model_events_shadow (
 source_message_id TEXT NOT NULL,
 event_type TEXT NOT NULL CHECK(event_type IN ('OFFER','CONFIRMED_TRADE')),
 effective_at_utc TEXT,
 quote_price INTEGER NOT NULL,
 quantity INTEGER,
 side TEXT,
 trade_form TEXT,
 settlement TEXT,
 paper_variant TEXT,
 model_eligible INTEGER NOT NULL,
 lifecycle_version TEXT NOT NULL,
 trade_evidence TEXT,
 market_check_status TEXT,
 market_reference_price INTEGER,
 market_deviation_bps REAL,
 model_weight REAL NOT NULL DEFAULT 1,
 PRIMARY KEY(source_message_id,event_type)
);
CREATE INDEX IF NOT EXISTS idx_gold_model_event_market ON gold_model_events_shadow(model_eligible,trade_form,settlement,paper_variant,effective_at_utc);
CREATE TABLE IF NOT EXISTS gold_lifecycle_metadata (
 key TEXT PRIMARY KEY,
 value_json TEXT NOT NULL
);
'''
def lag(a,b):
 if not a or not b:return None
 try:return (datetime.fromisoformat(b.replace('Z','+00:00'))-datetime.fromisoformat(a.replace('Z','+00:00'))).total_seconds()
 except ValueError:return None
def ensure_columns(conn:sqlite3.Connection)->None:
 for table,columns in {
  'gold_offer_lifecycle':(('edited_at_utc','TEXT'),('is_conditional','INTEGER NOT NULL DEFAULT 0'),('condition_reason','TEXT'),('trade_evidence','TEXT'),('market_check_status','TEXT'),('market_reference_price','INTEGER'),('market_deviation_bps','REAL'),('model_weight','REAL NOT NULL DEFAULT 1')),
  'gold_model_events_shadow':(('trade_evidence','TEXT'),('market_check_status','TEXT'),('market_reference_price','INTEGER'),('market_deviation_bps','REAL'),('model_weight','REAL NOT NULL DEFAULT 1')),
 }.items():
  existing={r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
  for name,definition in columns:
   if name not in existing: conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')

def epoch(value):
 if not value: return None
 try: return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
 except ValueError: return None

def market_key(x):
 return (x['trade_form'],x['settlement'],x['paper_variant'])

def build_normal_reference_index(rows):
 """Only ordinary parsed offers form the local market reference.

 Conditional offers are judged against the normal book rather than reinforcing
 one another.  The ±15-minute window responds to intraday moves yet catches a
 price that is clearly special because of its payment condition.
 """
 index={}
 for x in rows:
  if x['parser_status']=='PARSED' and x['price'] is not None and not x.get('is_conditional'):
   ts=epoch(x['occurred_at_utc'])
   if ts is not None:index.setdefault(market_key(x),[]).append((ts,x['price']))
 for values in index.values(): values.sort()
 return index

def assess_conditional_market(x,index):
 if not x.get('is_conditional'): return 'ORDINARY_OFFER',None,None,1.0
 ts=epoch(x['occurred_at_utc']); values=index.get(market_key(x),[])
 if ts is None or not values:return 'PENDING_NO_LOCAL_REFERENCE',None,None,0.0
 times=[v[0] for v in values]; lo=bisect_left(times,ts-900); hi=bisect_right(times,ts+900)
 prices=[v[1] for v in values[lo:hi]]
 if len(prices)<4:return 'PENDING_INSUFFICIENT_LOCAL_REFERENCE',None,None,0.0
 ref=median(prices); deviations=[abs(p-ref) for p in prices]; mad=median(deviations)
 deviation_bps=abs(x['price']-ref)*10000/ref
 # At least 75bp, or four robust local deviations, is required before an
 # explicit condition can be deemed non-comparable.  This avoids filtering a
 # legitimate quote merely because the market was moving quickly.
 limit_bps=max(75.0,(4*mad*10000/ref))
 if deviation_bps>limit_bps:return 'OUTLIER_VS_NORMAL_MARKET',round(ref),round(deviation_bps,2),0.0
 return 'COMPARABLE_TO_NORMAL_MARKET',round(ref),round(deviation_bps,2),1.0
def source_marker(conn:sqlite3.Connection)->str:
 latest=conn.execute('select max(updated_at_utc) from gold_offer_events').fetchone()[0]
 if not latest:return ''
 rows=conn.execute('select source_message_id,parser_version from gold_offer_events where updated_at_utc=? order by source_message_id',(latest,)).fetchall()
 digest=__import__('hashlib').sha256('|'.join(f'{r[0]}:{r[1]}' for r in rows).encode()).hexdigest()
 return json.dumps({'updated_at_utc':latest,'batch_count':len(rows),'digest':digest},sort_keys=True)
def main():
 src=sqlite3.connect(SOURCE); src.row_factory=sqlite3.Row; out=sqlite3.connect(OUT); out.executescript(SCHEMA); ensure_columns(out)
 marker=source_marker(src); previous=out.execute("select value_json from gold_lifecycle_metadata where key='staging_marker'").fetchone()
 if previous is not None and previous[0]==marker:
  print(json.dumps({'status':'NO_STAGING_CHANGE','version':VERSION},ensure_ascii=False)); return
 out.execute('delete from gold_offer_lifecycle'); out.execute('delete from gold_model_events_shadow')
 rows=[dict(r) for r in src.execute('select * from gold_offer_events')]
 normal_reference_index=build_normal_reference_index(rows)
 tally={}; checks={}
 for x in rows:
  conditional=bool(x.get('is_conditional')); check_status,reference_price,deviation_bps,weight=assess_conditional_market(x,normal_reference_index)
  eligible=int(x['parser_status']=='PARSED' and x['price'] is not None and weight>0)
  edited_at=x.get('edited_at_utc'); detected_at=x.get('trade_detected_at_utc')
  effective_trade_time=edited_at or detected_at
  explicit=x['trade_status'] in {'FULL','PARTIAL'}
  evidence=('TELEGRAM_EDIT_CONFIRMED' if edited_at and explicit else ('TELEGRAM_EDIT_PENDING_QUANTITY' if edited_at else ('CRAWLER_CONFIRMED' if explicit and detected_at else None)))
  delay=lag(x['occurred_at_utc'],effective_trade_time)
  time_source='TELEGRAM_EDIT_DATETIME' if edited_at else x['trade_time_source']
  out.execute('''insert into gold_offer_lifecycle (source_message_id,offered_at_utc,quote_price,quote_price_raw,quantity,side,trade_form,settlement,paper_variant,trade_status,traded_quantity,traded_at_utc,trade_time_source,trade_delay_seconds,parser_status,model_eligible,lifecycle_version,updated_at_utc,edited_at_utc,is_conditional,condition_reason,trade_evidence,market_check_status,market_reference_price,market_deviation_bps,model_weight) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(x['source_message_id'],x['occurred_at_utc'],x['price'],x['price_raw'],x['initial_quantity'],x['side'],x['trade_form'],x['settlement'],x['paper_variant'],x['trade_status'],x['traded_quantity'],effective_trade_time,time_source,delay,x['parser_status'],eligible,VERSION,NOW(),edited_at,int(conditional),x.get('condition_reason'),evidence,check_status,reference_price,deviation_bps,weight))
  # Parsed conditional rows are retained in the model-event store even when
  # pending/outlier.  Consumers use model_eligible/model_weight, so the data
  # stays available for future conditional-offer modelling without moving the
  # normal-market estimate today.
  if x['parser_status']=='PARSED' and x['price'] is not None:
   out.execute('insert into gold_model_events_shadow (source_message_id,event_type,effective_at_utc,quote_price,quantity,side,trade_form,settlement,paper_variant,model_eligible,lifecycle_version,trade_evidence,market_check_status,market_reference_price,market_deviation_bps,model_weight) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(x['source_message_id'],'OFFER',x['occurred_at_utc'],x['price'],x['initial_quantity'],x['side'],x['trade_form'],x['settlement'],x['paper_variant'],eligible,VERSION,None,check_status,reference_price,deviation_bps,weight))
   if effective_trade_time and (explicit or edited_at):
    quantity=x['traded_quantity'] if explicit else None
    out.execute('insert into gold_model_events_shadow (source_message_id,event_type,effective_at_utc,quote_price,quantity,side,trade_form,settlement,paper_variant,model_eligible,lifecycle_version,trade_evidence,market_check_status,market_reference_price,market_deviation_bps,model_weight) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(x['source_message_id'],'CONFIRMED_TRADE',effective_trade_time,x['price'],quantity,x['side'],x['trade_form'],x['settlement'],x['paper_variant'],eligible,VERSION,evidence,check_status,reference_price,deviation_bps,weight))
  tally[(x['parser_status'],x['trade_status'])]=tally.get((x['parser_status'],x['trade_status']),0)+1
  checks[check_status]=checks.get(check_status,0)+1
 out.execute("insert into gold_lifecycle_metadata(key,value_json) values('staging_marker',?) on conflict(key) do update set value_json=excluded.value_json",(marker,))
 out.commit(); print(json.dumps({'lifecycle_rows':out.execute('select count(*) from gold_offer_lifecycle').fetchone()[0],'model_events':out.execute('select count(*) from gold_model_events_shadow').fetchone()[0],'eligible_trade_events':out.execute("select count(*) from gold_model_events_shadow where event_type='CONFIRMED_TRADE' and model_eligible=1").fetchone()[0],'conditional_market_checks':checks,'by_state':{'|'.join(k):v for k,v in tally.items()},'integrity':out.execute('pragma integrity_check').fetchone()[0],'version':VERSION},ensure_ascii=False))
if __name__=='__main__':main()
