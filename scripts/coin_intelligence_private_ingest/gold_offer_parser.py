#!/usr/bin/env python3
"""Parse account1 gold offer/trade events into a dedicated shadow staging DB.

Confirmed business rules: an offer containing ``با حواله`` with neither ``روز``
nor ``فردا`` is PAPER/TOMORROW.  From the explicit policy activation boundary
forward, ``روز`` without a physical marker is PAPER/TODAY as well.
"""
from __future__ import annotations
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE
RAW=PIPE/'raw_events.sqlite3'; OUT=PIPE/'gold_offer_staging.sqlite3'
VERSION='gold-offer-parser-shadow-v1.3'
# Do not reinterpret historical training data.  This is the first account1
# source-message id after the user clarified that e.g. "فروشروز" is paper.
DAY_WITHOUT_PHYSICAL_IS_PAPER_FROM_MESSAGE_ID=int(
    os.environ.get('COIN_GOLD_DAY_PAPER_FROM_MESSAGE_ID') or str(2**63 - 1)
)
NOW=lambda:datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
SCHEMA='''
CREATE TABLE IF NOT EXISTS gold_offer_events (
 source_message_id TEXT PRIMARY KEY,
 occurred_at_utc TEXT,
 offerer_name TEXT,
 offer_text TEXT NOT NULL,
 price_raw TEXT,
 price INTEGER,
 initial_quantity INTEGER,
 side TEXT,
 trade_form TEXT NOT NULL,
 settlement TEXT NOT NULL,
 paper_variant TEXT NOT NULL,
 trade_status TEXT NOT NULL,
 traded_quantity INTEGER,
 trade_detected_at_utc TEXT,
 trade_time_source TEXT,
 parser_status TEXT NOT NULL,
 parser_version TEXT NOT NULL,
 updated_at_utc TEXT NOT NULL,
 edited_at_utc TEXT,
 is_conditional INTEGER NOT NULL DEFAULT 0,
 condition_reason TEXT,
 condition_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_gold_offer_market ON gold_offer_events(trade_form,settlement,paper_variant,occurred_at_utc);
CREATE INDEX IF NOT EXISTS idx_gold_offer_trade ON gold_offer_events(trade_status,trade_detected_at_utc);
CREATE TABLE IF NOT EXISTS gold_offer_parser_metadata (
 key TEXT PRIMARY KEY,
 value_json TEXT NOT NULL
);
'''

def n(text:str)->str:
 return text.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789')).replace('\u200c',' ')

def conditional_details(text:str)->tuple[int,str|None,str|None]:
 """Identify terms which make an offer non-comparable to the normal market.

 A free-form ``توضیحات`` tail is intentionally treated as conditional.  This
 is conservative: the record remains in staging for later review, but cannot
 distort a normal-market estimate merely because the condition was subtle.
 """
 value=n(text)
 description=re.search(r'توضیحات\s*[:：]\s*(.+)',value,re.S)
 if description and description.group(1).strip():
  return 1,'FREEFORM_CONDITION_DESCRIPTION',description.group(1).strip()
 markers=(
  ('ONE_PAYMENT_SLIP',r'یک\s*فقره|تک\s*فقره'),
  ('PAYMENT_SLIP',r'فیش'),
  ('PAYMENT_DEADLINE',r'تا\s*(?:ساعت|[0-2]?\d\s*[:.]?\s*[0-5]\d)|مهلت'),
  ('PAYMENT_CONDITION',r'واریز|تسویه|چک'),
  ('EXCHANGE_CONDITION',r'تعویض|خط'),
  ('EXPLICIT_CONDITION',r'شرط'),
 )
 for reason,pattern in markers:
  found=re.search(pattern,value,re.I)
  if found:
   start=max(0,found.start()-60); end=min(len(value),found.end()+120)
   return 1,reason,value[start:end].strip()
 return 0,None,None

def ensure_columns(conn:sqlite3.Connection)->None:
 existing={r[1] for r in conn.execute('PRAGMA table_info(gold_offer_events)')}
 for name,definition in (
  ('edited_at_utc','TEXT'),
  ('is_conditional','INTEGER NOT NULL DEFAULT 0'),
  ('condition_reason','TEXT'),
  ('condition_text','TEXT'),
 ):
  if name not in existing: conn.execute(f'ALTER TABLE gold_offer_events ADD COLUMN {name} {definition}')

def source_marker(conn:sqlite3.Connection)->str:
 """Fingerprint the newest raw batch, including same-second edits."""
 latest=conn.execute("select max(updated_at_utc) from source_messages_current where source_key='account1_channel'").fetchone()[0]
 if not latest:return ''
 rows=conn.execute("select message_id,content_sha256 from source_messages_current where source_key='account1_channel' and updated_at_utc=? order by message_id",(latest,)).fetchall()
 digest=__import__('hashlib').sha256('|'.join(f'{r[0]}:{r[1]}' for r in rows).encode()).hexdigest()
 return json.dumps({'updated_at_utc':latest,'batch_count':len(rows),'digest':digest,'parser_version':VERSION},sort_keys=True)

def parse(record:dict)->dict:
 text=str(record.get('initial_offer_text') or record.get('text') or '')
 value=n(text)
 price_match=re.search(r'(?<!\d)(\d{2,3}(?:[,.]\d{3})+|\d{7,9})(?!\d)',value)
 raw_price=price_match.group(1) if price_match else None
 price=int(re.sub(r'\D','',raw_price)) if raw_price else None
 parsed_side=(
  'SELL' if re.search(r'فروش|\bف\b',value)
  else ('BUY' if re.search(r'خرید|\bخ\b',value) else None)
 )
 quantity_match=re.search(r'(?<!\d)(\d{1,4})\s*تا\b',value)
 parsed_quantity=int(quantity_match.group(1)) if quantity_match else None
 has_havale=bool(re.search(r'با\s*حواله',value))
 physical_cash=bool(re.search(r'نقد\s*حاضر',value))
 physical_tomorrow=bool(re.search(r'بی\s*حواله|بدون\s*حواله',value))
 day=bool(re.search(r'روز',value)); tomorrow=bool(re.search(r'فردا',value))
 if has_havale:
  form='PAPER'
  settlement='TODAY' if day else 'TOMORROW' # confirmed: omitted day defaults tomorrow
  variant='REVERSE' if 'معکوس' in value else ('SWIM' if 'شنا' in value else 'NORMAL')
 elif physical_cash:
  form='PHYSICAL'; settlement='TODAY'; variant='N/A'
 elif physical_tomorrow:
  form='PHYSICAL'; settlement='TOMORROW'; variant='N/A'
 elif day and int(record.get('message_id') or 0) >= DAY_WITHOUT_PHYSICAL_IS_PAPER_FROM_MESSAGE_ID:
  # "روز" is a paper/today marker unless an explicit physical marker above
  # says otherwise.  The id boundary preserves the already-approved past
  # corpus exactly as it was.
  form='PAPER'; settlement='TODAY'
  variant='REVERSE' if 'معکوس' in value else ('SWIM' if 'شنا' in value else 'NORMAL')
 else:
  form='UNKNOWN'; settlement='UNKNOWN'; variant='N/A'
 status=str(record.get('trade_status') or 'PENDING').upper()
 if status not in {'NONE','FULL','PARTIAL','PENDING','CHANGED_UNCLASSIFIED'}: status='PENDING'
 is_conditional,condition_reason,condition_text=conditional_details(text)
 parser_status=(
  'IGNORED_PENDING_SEMANTIC_RULE' if form=='UNKNOWN' and day
  else ('PARSED' if price and form!='UNKNOWN' else 'REVIEW')
 )
 return {'source_message_id':str(record['message_id']),'occurred_at_utc':record.get('telegram_datetime'),'offerer_name':record.get('sender_name') or None,'offer_text':text,'price_raw':raw_price,'price':price,'initial_quantity':record.get('initial_quantity') or parsed_quantity,'side':str(record.get('offer_side') or '').upper() or parsed_side,'trade_form':form,'settlement':settlement,'paper_variant':variant,'trade_status':status,'traded_quantity':record.get('traded_quantity'),'trade_detected_at_utc':record.get('trade_detected_at'),'trade_time_source':record.get('trade_time_source'),'parser_status':parser_status,'parser_version':VERSION,'updated_at_utc':NOW(),'edited_at_utc':record.get('telegram_edit_datetime'),'is_conditional':is_conditional,'condition_reason':condition_reason,'condition_text':condition_text}
def main()->None:
 raw=sqlite3.connect(RAW); raw.row_factory=sqlite3.Row; out=sqlite3.connect(OUT); out.executescript(SCHEMA); ensure_columns(out)
 marker=source_marker(raw); previous=out.execute("select value_json from gold_offer_parser_metadata where key='raw_source_marker'").fetchone()
 if previous is not None and previous[0]==marker:
  print(json.dumps({'status':'NO_SOURCE_CHANGE','version':VERSION},ensure_ascii=False)); return
 previous_marker=json.loads(previous[0]) if previous is not None else None
 if previous_marker and previous_marker.get('updated_at_utc') and previous_marker.get('parser_version') == VERSION:
  rows=raw.execute("SELECT record_json FROM source_messages_current WHERE source_key='account1_channel' and updated_at_utc>=?",(previous_marker['updated_at_utc'],)).fetchall()
 else:
  rows=raw.execute("SELECT record_json FROM source_messages_current WHERE source_key='account1_channel'").fetchall()
 total=raw.execute("SELECT count(*) FROM source_messages_current WHERE source_key='account1_channel'").fetchone()[0]; tally={}
 for row in rows:
  record=json.loads(row['record_json'])
  post_type=record.get('post_type')
  # Live account events omit post_type.  Accept non-empty textual events unless
  # an explicit non-offer type was supplied; parser_status then remains the
  # final safety gate for any text that is not a real price offer.
  if post_type not in (None,'','offer') or not str(record.get('initial_offer_text') or record.get('text') or '').strip(): continue
  x=parse(record); tally[(x['trade_form'],x['settlement'],x['paper_variant'],x['parser_status'])]=tally.get((x['trade_form'],x['settlement'],x['paper_variant'],x['parser_status']),0)+1
  out.execute('''INSERT INTO gold_offer_events (source_message_id,occurred_at_utc,offerer_name,offer_text,price_raw,price,initial_quantity,side,trade_form,settlement,paper_variant,trade_status,traded_quantity,trade_detected_at_utc,trade_time_source,parser_status,parser_version,updated_at_utc,edited_at_utc,is_conditional,condition_reason,condition_text) VALUES (:source_message_id,:occurred_at_utc,:offerer_name,:offer_text,:price_raw,:price,:initial_quantity,:side,:trade_form,:settlement,:paper_variant,:trade_status,:traded_quantity,:trade_detected_at_utc,:trade_time_source,:parser_status,:parser_version,:updated_at_utc,:edited_at_utc,:is_conditional,:condition_reason,:condition_text) ON CONFLICT(source_message_id) DO UPDATE SET occurred_at_utc=excluded.occurred_at_utc,offerer_name=excluded.offerer_name,offer_text=excluded.offer_text,price_raw=excluded.price_raw,price=excluded.price,initial_quantity=excluded.initial_quantity,side=excluded.side,trade_form=excluded.trade_form,settlement=excluded.settlement,paper_variant=excluded.paper_variant,trade_status=excluded.trade_status,traded_quantity=excluded.traded_quantity,trade_detected_at_utc=excluded.trade_detected_at_utc,trade_time_source=excluded.trade_time_source,parser_status=excluded.parser_status,parser_version=excluded.parser_version,updated_at_utc=excluded.updated_at_utc,edited_at_utc=excluded.edited_at_utc,is_conditional=excluded.is_conditional,condition_reason=excluded.condition_reason,condition_text=excluded.condition_text''',x)
 out.execute("insert into gold_offer_parser_metadata(key,value_json) values('raw_source_marker',?) on conflict(key) do update set value_json=excluded.value_json",(marker,))
 out.commit(); print(json.dumps({'source_offer_rows':total,'changed_source_rows':len(rows),'parsed_by_class':{'|'.join(k):v for k,v in tally.items()},'conditional_offers':out.execute('select count(*) from gold_offer_events where is_conditional=1').fetchone()[0],'integrity':out.execute('pragma integrity_check').fetchone()[0],'version':VERSION},ensure_ascii=False))
if __name__=='__main__':main()
