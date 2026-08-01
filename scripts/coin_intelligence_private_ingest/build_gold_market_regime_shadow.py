#!/usr/bin/env python3
"""Derive explainable, causal market-regime labels from gold minute features.

This is a shadow signal: it never alters a quote.  Every label uses only the
current and preceding 15 minutes, avoiding future-data leakage in evaluation.
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as PIPE
SOURCE=PIPE/'gold_minute_features_shadow.sqlite3'; OUT=PIPE/'gold_market_regime_shadow.sqlite3'
VERSION='gold-market-regime-shadow-v1.0'
WINDOW_SECONDS=15*60

SCHEMA='''
CREATE TABLE IF NOT EXISTS gold_market_regimes (
 minute_utc TEXT NOT NULL,
 trade_form TEXT NOT NULL,
 settlement TEXT NOT NULL,
 paper_variant TEXT NOT NULL,
 regime TEXT NOT NULL CHECK(regime IN ('NORMAL','RISING','FALLING','VOLATILE','INSUFFICIENT_DATA')),
 last_price INTEGER,
 return_15m_bps REAL,
 range_15m_bps REAL,
 buy_sell_imbalance REAL,
 offer_count_15m INTEGER NOT NULL,
 trade_count_15m INTEGER NOT NULL,
 confidence REAL NOT NULL,
 rationale TEXT NOT NULL,
 regime_version TEXT NOT NULL,
 PRIMARY KEY(minute_utc,trade_form,settlement,paper_variant)
);
CREATE TABLE IF NOT EXISTS gold_market_regime_consensus (
 minute_utc TEXT PRIMARY KEY,
 regime TEXT NOT NULL CHECK(regime IN ('NORMAL','RISING','FALLING','VOLATILE','INSUFFICIENT_DATA')),
 confidence REAL NOT NULL,
 contributing_markets INTEGER NOT NULL,
 rationale TEXT NOT NULL,
 regime_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gold_regime_time ON gold_market_regimes(minute_utc,regime);
'''

WEIGHTS={
 ('PAPER','TOMORROW','NORMAL'):1.00,
 ('PHYSICAL','TOMORROW','N/A'):0.70,
 ('PAPER','TOMORROW','REVERSE'):0.35,
 ('PAPER','TOMORROW','SWIM'):0.35,
 ('PHYSICAL','TODAY','N/A'):0.20,
}

def stamp(value): return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
def classify(history,current):
 """Return regime and evidence from a trailing 15-minute market window."""
 now=stamp(current['minute_utc'])
 window=[x for x in history if now-stamp(x['minute_utc'])<=WINDOW_SECONDS and x['offer_last_price'] is not None]
 offers=sum(x['offer_count'] for x in window); trades=sum(x['trade_event_count'] for x in window)
 if len(window)<3:
  return 'INSUFFICIENT_DATA',None,None,0.0,offers,trades,0.0,'fewer than three quote minutes in trailing 15m'
 first=window[0]['offer_last_price']; last=window[-1]['offer_last_price']
 low=min(x['offer_low_price'] for x in window if x['offer_low_price'] is not None)
 high=max(x['offer_high_price'] for x in window if x['offer_high_price'] is not None)
 ret=(last-first)*10000/first; spread=(high-low)*10000/first
 imbalances=[x['offer_buy_sell_imbalance'] for x in window if x['offer_buy_sell_imbalance'] is not None]
 imbalance=sum(imbalances)/len(imbalances) if imbalances else 0.0
 activity=min(1.0,(offers+2*trades)/30)
 # A wide range alone is not called volatile when net movement explains it:
 # a persistent trend should remain RISING/FALLING.
 if spread>=80 and abs(ret)<0.60*spread:
  confidence=min(0.95,0.45+spread/300+activity/5)
  return 'VOLATILE',ret,spread,imbalance,offers,trades,confidence,'wide intrawindow range without a dominant net direction'
 if ret>=18 and imbalance>=-0.20:
  confidence=min(0.95,0.40+abs(ret)/180+activity/5)
  return 'RISING',ret,spread,imbalance,offers,trades,confidence,'positive 15m return with non-negative order-flow support'
 if ret<=-18 and imbalance<=0.20:
  confidence=min(0.95,0.40+abs(ret)/180+activity/5)
  return 'FALLING',ret,spread,imbalance,offers,trades,confidence,'negative 15m return with non-positive order-flow support'
 confidence=min(0.85,0.35+activity/3)
 return 'NORMAL',ret,spread,imbalance,offers,trades,confidence,'limited directional movement in trailing 15m'

def consensus(rows):
 """Weighted consensus for the coin-estimation pipeline's market context."""
 score=defaultdict(float); confidence_weight=0.0; contributors=[]
 directional={'RISING':1,'FALLING':-1,'NORMAL':0,'VOLATILE':0}
 for x in rows:
  weight=WEIGHTS.get((x['trade_form'],x['settlement'],x['paper_variant']),0.0)*x['confidence']
  if not weight or x['regime']=='INSUFFICIENT_DATA':continue
  score[x['regime']]+=weight; confidence_weight+=weight; contributors.append(x)
 if not contributors:return 'INSUFFICIENT_DATA',0.0,0,'no market has sufficient trailing evidence'
 # Volatility wins only with substantive independent support; otherwise a
 # liquid normal-paper directional market remains the primary anchor.
 winner=max(score,key=score.get)
 return winner,min(0.95,confidence_weight/sum(WEIGHTS.get((x['trade_form'],x['settlement'],x['paper_variant']),0.0) for x in contributors)),len(contributors),f'weighted market consensus: {winner}'

def main():
 src=sqlite3.connect(SOURCE); src.row_factory=sqlite3.Row
 out=sqlite3.connect(OUT); out.executescript(SCHEMA); out.execute('delete from gold_market_regimes'); out.execute('delete from gold_market_regime_consensus')
 grouped=defaultdict(list)
 for row in src.execute('select * from gold_market_minute_features order by minute_utc'):
  x=dict(row); grouped[(x['trade_form'],x['settlement'],x['paper_variant'])].append(x)
 all_rows=[]
 for key,series in grouped.items():
  for i,x in enumerate(series):
   regime,ret,spread,imbalance,offers,trades,confidence,rationale=classify(series[:i+1],x)
   record={'minute_utc':x['minute_utc'],'trade_form':key[0],'settlement':key[1],'paper_variant':key[2],'regime':regime,'last_price':x['offer_last_price'],'return_15m_bps':ret,'range_15m_bps':spread,'buy_sell_imbalance':imbalance,'offer_count_15m':offers,'trade_count_15m':trades,'confidence':confidence,'rationale':rationale,'regime_version':VERSION}
   all_rows.append(record)
   out.execute('''insert into gold_market_regimes values(:minute_utc,:trade_form,:settlement,:paper_variant,:regime,:last_price,:return_15m_bps,:range_15m_bps,:buy_sell_imbalance,:offer_count_15m,:trade_count_15m,:confidence,:rationale,:regime_version)''',record)
 by_minute=defaultdict(list)
 for x in all_rows: by_minute[x['minute_utc']].append(x)
 for minute,rows in by_minute.items():
  regime,confidence,count,rationale=consensus(rows)
  out.execute('insert into gold_market_regime_consensus values(?,?,?,?,?,?)',(minute,regime,confidence,count,rationale,VERSION))
 out.commit()
 print(json.dumps({'market_regime_rows':len(all_rows),'consensus_rows':len(by_minute),'consensus_distribution':dict(out.execute('select regime,count(*) from gold_market_regime_consensus group by regime')),'integrity':out.execute('pragma integrity_check').fetchone()[0],'version':VERSION},ensure_ascii=False))

if __name__=='__main__':main()
