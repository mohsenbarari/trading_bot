#!/usr/bin/env python3
"""Atomically promote a validated live-group conversation-data candidate.

This is a data-only promotion: estimator logic/model weights are unchanged.
The previous active DB is copied to the immutable versions directory first.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.coin_intelligence_private_ingest.runtime_paths import (
    CONVERSATION_DB as ACTIVE,
    DATA_ROOT,
    PIPELINE_ROOT as PIPE,
)
CANDIDATE=PIPE/'conversation_events.live-group-shadow.candidate.sqlite3'
VERSIONS=DATA_ROOT/'models/versions'
LEDGER=PIPE/'conversation_data_promotions.json'
NOW=lambda:datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def digest(p:Path)->str:
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
 return h.hexdigest()

def facts(p:Path)->dict:
 c=sqlite3.connect(p)
 integrity=c.execute('pragma integrity_check').fetchone()[0]
 result={'integrity':integrity,'offers':c.execute('select count(*) from offers').fetchone()[0],'trades':c.execute('select count(*) from confirmed_trades').fetchone()[0],'latest_message':c.execute('select max(event_time_utc) from messages').fetchone()[0]}
 # Every new shadow-import offer/trade must have market-quality annotation.
 result['unannotated_new_offers']=c.execute("select count(*) from offers o left join offer_market_quality q on q.offer_id=o.id where o.import_id=(select max(id) from imports) and q.offer_id is null").fetchone()[0]
 result['unannotated_new_trades']=c.execute("select count(*) from confirmed_trades t left join trade_market_quality q on q.trade_id=t.id where t.import_id=(select max(id) from imports) and q.trade_id is null").fetchone()[0]
 return result

def atomic_copy(source:Path,destination:Path)->None:
 temporary=destination.with_suffix(destination.suffix+'.tmp')
 shutil.copy2(source,temporary); os.chmod(temporary,0o600); temporary.replace(destination)

def main()->None:
 if not CANDIDATE.is_file() or not ACTIVE.is_file(): raise RuntimeError('missing active or candidate database')
 before=facts(ACTIVE); candidate=facts(CANDIDATE)
 if candidate['integrity']!='ok' or candidate['unannotated_new_offers'] or candidate['unannotated_new_trades']:
  raise RuntimeError(f'candidate gate failed: {candidate}')
 # A legitimate live batch can contain only offers or only confirmed trades.
 # Reject only a candidate that adds neither; the builder already guarantees
 # that it is composed exclusively of accepted shadow records.
 if candidate['offers']<=before['offers'] and candidate['trades']<=before['trades']:
  raise RuntimeError('candidate does not contain additive accepted data')
 old=digest(ACTIVE); new=digest(CANDIDATE)
 VERSIONS.mkdir(parents=True,exist_ok=True)
 backup=VERSIONS/f'conversation-data-{old[:12]}.sqlite3'
 if not backup.exists(): atomic_copy(ACTIVE,backup)
 atomic_copy(CANDIDATE,ACTIVE)
 after=facts(ACTIVE)
 if after['integrity']!='ok' or digest(ACTIVE)!=new:
  raise RuntimeError('post-promotion verification failed')
 ledger={'schema_version':1,'promotions':[]}
 if LEDGER.exists(): ledger=json.loads(LEDGER.read_text(encoding='utf-8'))
 ledger.setdefault('promotions',[]).append({'promoted_at_utc':NOW(),'kind':'DATA_ONLY_ALGORITHM_UNCHANGED','reason':'Accepted live group records originate from the established manual group source; excluded 403/404, Thursday/cashier, and unparseable records remain out.','previous_sha256':old,'active_sha256':new,'backup':str(backup),'before':before,'after':after})
 temporary=LEDGER.with_suffix('.json.tmp'); temporary.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(temporary,0o600); temporary.replace(LEDGER)
 print(json.dumps({'previous':before,'active':after,'backup':str(backup),'active_sha256':new},ensure_ascii=False))

if __name__=='__main__': main()
