#!/usr/bin/env python3
"""Promote validated account1 gold events into the active market database.

Physical non-conditional offers remain individual price events.  Paper events
are represented twice: raw events feed order flow, while a single derived
minute quote feeds price selection.  A confirmed paper trade has weight 3.0
versus 1.0 for its offer in that minute quote.
"""
from __future__ import annotations

import hashlib,json,sqlite3
from collections import defaultdict
from datetime import datetime,timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import (
    MARKET_DB as DEST,
    PIPELINE_ROOT as PIPE,
)
SOURCE=PIPE/'gold_lifecycle_shadow.sqlite3'
VERSION='account1-gold-main-bridge-v1.0'; TRADE_WEIGHT=3.0
PHYSICAL_LABELS={'TODAY':'آبشده کانال جدید نقد حاضر','TOMORROW':'آبشده کانال جدید فیزیکی فردا'}
PAPER_LABELS={'NORMAL':'آبشده کانال جدید کاغذی عادی','REVERSE':'آبشده کانال جدید کاغذی معکوس','SWIM':'آبشده کانال جدید کاغذی شنا'}

def now():return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def stable_id(value:str)->int:return int.from_bytes(hashlib.blake2b(value.encode(),digest_size=7,person=b'acct1gold').digest(),'big')
def minute(value:str)->str:return value[:16]+':00Z'
def minute_close(value:str)->str:return value[:16]+':59Z'
def setup(conn):
 conn.execute('''CREATE TABLE IF NOT EXISTS account1_gold_main_bridge_metadata (key TEXT PRIMARY KEY,value_json TEXT NOT NULL)''')
def source_marker(conn):
 latest=conn.execute('select max(updated_at_utc) from gold_offer_lifecycle').fetchone()[0]
 if not latest:return ''
 rows=conn.execute('select source_message_id,lifecycle_version from gold_offer_lifecycle where updated_at_utc=? order by source_message_id',(latest,)).fetchall()
 digest=hashlib.sha256('|'.join(f'{r[0]}:{r[1]}' for r in rows).encode()).hexdigest()
 return json.dumps({'updated_at_utc':latest,'batch_count':len(rows),'digest':digest},sort_keys=True)
def upsert_post(conn,source,message_id,at,text):
 conn.execute('''INSERT INTO raw_posts(source_code,message_id,published_at_utc,raw_text,parse_status) VALUES(?,?,?,?,?) ON CONFLICT(source_code,message_id) DO UPDATE SET published_at_utc=excluded.published_at_utc,raw_text=excluded.raw_text,parse_status=excluded.parse_status''',(source,message_id,at,text,'PARSED'))
 return conn.execute('select id from raw_posts where source_code=? and message_id=?',(source,message_id)).fetchone()[0]
def replace_events(conn,post_id,events):
 conn.execute('delete from price_events where raw_post_id=?',(post_id,))
 for index,event in enumerate(events):
  conn.execute('''INSERT INTO price_events(raw_post_id,event_index,instrument,market_label,settlement_term,trade_form,event_type,side,price_value,price_num,currency,price_unit,quantity_value,quantity_num,movement,event_time_utc,tehran_datetime,tehran_date,tehran_minute,tehran_weekday,tehran_weekday_name,source_datetime_text,parse_method,parse_confidence,parser_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
   post_id,index,event['instrument'],event['market_label'],event['settlement_term'],event['trade_form'],event['event_type'],event['side'],str(event['price']),event['price'],'IRT','IRT_PER_MESGHAL_750',str(event['quantity']) if event['quantity'] is not None else None,event['quantity'],'UNKNOWN',event['at'],event['at'],'','','0','',None,'ACCOUNT1_GOLD_MAIN_BRIDGE',1.0,VERSION))
def main():
 src=sqlite3.connect(SOURCE); src.row_factory=sqlite3.Row
 dst=sqlite3.connect(DEST); dst.row_factory=sqlite3.Row; setup(dst)
 marker=source_marker(src); previous=dst.execute("select value_json from account1_gold_main_bridge_metadata where key='lifecycle_source_marker'").fetchone()
 if previous is not None and previous[0]==marker:
  print(json.dumps({'status':'NO_LIFECYCLE_CHANGE','version':VERSION},ensure_ascii=False)); return
 # Only bridge-owned rows are replaced; no legacy source is touched.
 for prefix in ('ACCOUNT1_GOLD_PHYSICAL_','ACCOUNT1_GOLD_PAPER_RAW_','ACCOUNT1_GOLD_PAPER_MINUTE_'):
  dst.execute('delete from price_events where raw_post_id in (select id from raw_posts where source_code like ?)',(prefix+'%',))
  dst.execute('delete from raw_posts where source_code like ?',(prefix+'%',))
 physical=0; physical_conditional_raw=0; paper_raw=0; minute_inputs=defaultdict(list)
 rows=src.execute('''select l.*,e.event_type,e.effective_at_utc,e.quantity as event_quantity
 from gold_offer_lifecycle l join gold_model_events_shadow e using(source_message_id)
 where e.model_eligible=1 and l.parser_status='PARSED' order by e.effective_at_utc''').fetchall()
 grouped=defaultdict(list)
 for row in rows:
  x=dict(row)
  if x['trade_form']=='PHYSICAL':
   # A comparable conditional physical event is preserved for market-flow
   # learning, but never becomes the direct physical price reference.
   kind='PHYSICAL_CONDITIONAL_RAW' if x['is_conditional'] else 'PHYSICAL'
   grouped[(kind,x['source_message_id'])].append(x)
  elif x['trade_form']=='PAPER':
   grouped[('PAPER_RAW',x['source_message_id'])].append(x)
   minute_inputs[(minute(x['effective_at_utc']),x['settlement'],x['paper_variant'])].append(x)
 for (kind,source_id),items in grouped.items():
  first=items[0]; source=f'ACCOUNT1_GOLD_{kind}_{first["settlement"]}_{first["paper_variant"]}'
  post=upsert_post(dst,source,stable_id(source_id),first['offered_at_utc'],first['quote_price_raw'] or '')
  events=[]
  for x in items:
   if kind.startswith('PHYSICAL'):
    label=PHYSICAL_LABELS[x['settlement']] + (' شرطی' if kind!='PHYSICAL' else '')
   else: label=PAPER_LABELS[x['paper_variant']]
   events.append({'instrument':'MELTED_GOLD' if kind=='PHYSICAL' else 'MELTED_GOLD_FLOW','market_label':label,'settlement_term':x['settlement'],'trade_form':x['trade_form'],'event_type':'TRADE' if x['event_type']=='CONFIRMED_TRADE' else 'OFFER','side':x['side'] or 'UNKNOWN','price':x['quote_price'],'quantity':x['event_quantity'] if x['event_type']=='CONFIRMED_TRADE' else x['quantity'],'at':x['effective_at_utc']})
  replace_events(dst,post,events)
  physical+=kind=='PHYSICAL'; physical_conditional_raw+=kind=='PHYSICAL_CONDITIONAL_RAW'; paper_raw+=kind=='PAPER_RAW'
 paper_minutes=0
 for (at,settlement,variant),items in minute_inputs.items():
  numerator=denominator=0.0
  for x in items:
   weight=TRADE_WEIGHT if x['event_type']=='CONFIRMED_TRADE' else 1.0
   numerator+=float(x['quote_price'])*weight; denominator+=weight
  price=numerator/denominator
  source=f'ACCOUNT1_GOLD_PAPER_MINUTE_{settlement}_{variant}'
  # A minute aggregate is known only at the close of that minute.  Timestamping
  # it at :59 avoids accidentally excluding it from the next 60s window.
  effective_at=minute_close(at)
  post=upsert_post(dst,source,stable_id(at+settlement+variant),effective_at,f'weighted paper minute {variant}')
  replace_events(dst,post,[{'instrument':'MELTED_GOLD','market_label':PAPER_LABELS[variant],'settlement_term':settlement,'trade_form':'PAPER','event_type':'OFFER','side':'UNKNOWN','price':price,'quantity':None,'at':effective_at}])
  paper_minutes+=1
 dst.execute('insert into account1_gold_main_bridge_metadata(key,value_json) values(?,?) on conflict(key) do update set value_json=excluded.value_json',('last_run',json.dumps({'at':now(),'physical_source_offers':physical,'conditional_physical_flow_offers':physical_conditional_raw,'paper_raw_source_offers':paper_raw,'paper_weighted_minutes':paper_minutes,'paper_confirmed_trade_weight':TRADE_WEIGHT,'version':VERSION},ensure_ascii=False)))
 dst.execute("insert into account1_gold_main_bridge_metadata(key,value_json) values('lifecycle_source_marker',?) on conflict(key) do update set value_json=excluded.value_json",(marker,))
 dst.commit(); print(json.dumps({'physical_source_offers':physical,'conditional_physical_flow_offers':physical_conditional_raw,'paper_raw_source_offers':paper_raw,'paper_weighted_minutes':paper_minutes,'paper_confirmed_trade_weight':TRADE_WEIGHT,'integrity':dst.execute('pragma integrity_check').fetchone()[0],'version':VERSION},ensure_ascii=False))
if __name__=='__main__':main()
