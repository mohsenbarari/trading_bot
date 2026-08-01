#!/usr/bin/env python3
"""Conservative first-pass offer/reply extraction into text_staging.sqlite3.

Outputs candidates only.  No result is promoted to a production model table.
"""
from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone

from core.market_intelligence.group_offer_parser import enrich_records
from core.market_intelligence.group_trade_parser import classify_signal, EXTRACTOR_VERSION
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT

DB=PIPELINE_ROOT/'text_staging.sqlite3'
NOW=lambda: datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def main():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 rows=c.execute("SELECT * FROM text_candidates WHERE source_key IN ('account2_group1','account2_group2') AND extraction_status='PENDING' ORDER BY source_key,COALESCE(telegram_datetime,''),message_id").fetchall()
 grouped={}
 for r in rows: grouped.setdefault(r['source_key'],[]).append(r)
 count={}
 for source,items in grouped.items():
  def parser_date(r):
   value=str(r['telegram_datetime'] or '')
   if 'T' in value: return value.replace('Z','+00:00')
   day=str(r['telegram_day'] or '1970-01-01')
   if len(value) in (5,8) and ':' in value: return day+'T'+value+'+00:00'
   return day+'T00:00:00+00:00'
  inputs=[{'date':parser_date(r),'text':r['text']} for r in items]
  parsed=enrich_records(inputs)
  for r,out in zip(items,parsed):
   offers=out.get('extracted_offers') or []; signal=classify_signal(r['text']) if r['reply_detected'] else 'ROOT_MESSAGE'
   if r['source_post_type']=='offer': kind='OFFER_SOURCE_CONFIRMED'; confidence=1.0
   elif r['source_post_type']=='non_offer': kind='NON_OFFER_SOURCE_CONFIRMED'; confidence=1.0
   elif offers: kind='OFFER_CANDIDATE'; confidence=max(float(x.get('confidence') or 0) for x in offers)
   elif r['reply_detected']: kind='REPLY_'+signal; confidence=0.70
   else: kind='UNKNOWN_NON_OFFER'; confidence=0.35
   payload={'kind':kind,'signal':signal,'offers':offers,'reply':{'detected':bool(r['reply_detected']),'message_id':r['reply_message_id'],'resolution':r['reply_reference_status']},'promotion':'FORBIDDEN_PENDING_REVIEW'}
   c.execute("UPDATE text_candidates SET extraction_status=?,extractor_version=?,extracted_json=?,extraction_confidence=?,updated_at_utc=? WHERE source_key=? AND message_id=?",('RULES_CANDIDATE',EXTRACTOR_VERSION,json.dumps(payload,ensure_ascii=False,separators=(',',':')),confidence,NOW(),r['source_key'],r['message_id']))
   count[kind]=count.get(kind,0)+1
  c.commit()
 print(json.dumps({'processed':len(rows),'by_kind':count,'extractor_version':EXTRACTOR_VERSION},ensure_ascii=False))
if __name__=='__main__': main()
