#!/usr/bin/env python3
"""Build one-minute, market-separated features from eligible gold events.

The output deliberately retains market form, settlement and paper variant in
its primary key.  Paper normal/reverse/swim and physical today/tomorrow are
different markets and must never be averaged into a single quote.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE
SOURCE=PIPE/'gold_lifecycle_shadow.sqlite3'; OUT=PIPE/'gold_minute_features_shadow.sqlite3'
VERSION='gold-minute-features-shadow-v1.0'

SCHEMA='''
CREATE TABLE IF NOT EXISTS gold_market_minute_features (
 minute_utc TEXT NOT NULL,
 trade_form TEXT NOT NULL,
 settlement TEXT NOT NULL,
 paper_variant TEXT NOT NULL,
 offer_count INTEGER NOT NULL,
 buy_offer_count INTEGER NOT NULL,
 sell_offer_count INTEGER NOT NULL,
 offer_first_price INTEGER,
 offer_last_price INTEGER,
 offer_low_price INTEGER,
 offer_high_price INTEGER,
 offer_mean_price REAL,
 offer_price_change INTEGER,
 offer_buy_sell_imbalance REAL,
 trade_event_count INTEGER NOT NULL,
 confirmed_quantity_count INTEGER NOT NULL,
 confirmed_quantity_sum INTEGER NOT NULL,
 buy_trade_count INTEGER NOT NULL,
 sell_trade_count INTEGER NOT NULL,
 excluded_offer_count INTEGER NOT NULL,
 excluded_trade_count INTEGER NOT NULL,
 feature_version TEXT NOT NULL,
 PRIMARY KEY(minute_utc,trade_form,settlement,paper_variant)
);
CREATE INDEX IF NOT EXISTS idx_gold_minute_features_time
 ON gold_market_minute_features(minute_utc,trade_form,settlement,paper_variant);
'''

def minute(value:str)->str:
 dt=datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc)
 return dt.replace(second=0,microsecond=0).isoformat().replace('+00:00','Z')

def fresh_bucket():
 return {'offers':[],'trades':[],'excluded_offers':0,'excluded_trades':0}

def main():
 src=sqlite3.connect(SOURCE); src.row_factory=sqlite3.Row
 out=sqlite3.connect(OUT); out.executescript(SCHEMA); out.execute('delete from gold_market_minute_features')
 buckets=defaultdict(fresh_bucket)
 for row in src.execute('select * from gold_model_events_shadow where effective_at_utc is not null'):
  x=dict(row); key=(minute(x['effective_at_utc']),x['trade_form'],x['settlement'],x['paper_variant'])
  bucket=buckets[key]
  if not x['model_eligible']:
   bucket['excluded_offers' if x['event_type']=='OFFER' else 'excluded_trades']+=1
   continue
  if x['event_type']=='OFFER': bucket['offers'].append(x)
  else: bucket['trades'].append(x)
 for key,b in buckets.items():
  offers=sorted(b['offers'],key=lambda x:x['effective_at_utc']); trades=b['trades']; prices=[x['quote_price'] for x in offers]
  buys=sum(x['side']=='BUY' for x in offers); sells=sum(x['side']=='SELL' for x in offers)
  q=[x['quantity'] for x in trades if x['quantity'] is not None]
  tbuys=sum(x['side']=='BUY' for x in trades); tsells=sum(x['side']=='SELL' for x in trades)
  out.execute('''insert into gold_market_minute_features values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(
   *key,len(offers),buys,sells,
   prices[0] if prices else None,prices[-1] if prices else None,min(prices) if prices else None,max(prices) if prices else None,
   sum(prices)/len(prices) if prices else None,(prices[-1]-prices[0]) if prices else None,
   ((buys-sells)/len(offers)) if offers else None,len(trades),len(q),sum(q or []),tbuys,tsells,
   b['excluded_offers'],b['excluded_trades'],VERSION))
 out.commit()
 print(json.dumps({'minute_market_rows':len(buckets),'eligible_offer_events':sum(len(b['offers']) for b in buckets.values()),'eligible_trade_events':sum(len(b['trades']) for b in buckets.values()),'excluded_events':sum(b['excluded_offers']+b['excluded_trades'] for b in buckets.values()),'integrity':out.execute('pragma integrity_check').fetchone()[0],'version':VERSION},ensure_ascii=False))

if __name__=='__main__':main()
