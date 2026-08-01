#!/usr/bin/env python3
"""Atomically promote a validated live-group reconciliation candidate.

This is a data-only promotion: estimator logic/model weights are unchanged.
The immediately previous active DB and one start-of-day recovery point are
copied first.  This avoids the v1 behavior that created an immutable multi-MB
backup for every single Telegram message.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.coin_intelligence_private_ingest.runtime_paths import (
        CONVERSATION_DB as ACTIVE,
        DATA_ROOT,
        PIPELINE_ROOT as PIPE,
    )
except ModuleNotFoundError:  # Standalone immutable runtime deployment.
    PIPE = Path(__file__).resolve().parent
    DATA_ROOT = PIPE.parents[1]
    ACTIVE = DATA_ROOT / "apps/coin-intelligence/data/conversation_events.sqlite3"
CANDIDATE=PIPE/'conversation_events.live-group-shadow.candidate.sqlite3'
REPORT=PIPE/'conversation_candidate_import.latest.json'
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
 result['live_v1_imports']=c.execute("select count(*) from imports where extractor_version like 'live-group-shadow-import-v1.%'").fetchone()[0]
 result['live_v2_imports']=c.execute("select count(*) from imports where extractor_version like 'live-group-reconciled-v2.%'").fetchone()[0]
 result['live_v2_fingerprint']=c.execute("select archive_sha256 from imports where extractor_version like 'live-group-reconciled-v2.%' order by id desc limit 1").fetchone()
 result['live_v2_fingerprint']=result['live_v2_fingerprint'][0] if result['live_v2_fingerprint'] else None
 c.close()
 return result

def atomic_copy(source:Path,destination:Path)->None:
 temporary=destination.with_suffix(destination.suffix+'.tmp')
 shutil.copy2(source,temporary); os.chmod(temporary,0o600); temporary.replace(destination)

def main()->None:
 if not CANDIDATE.is_file() or not ACTIVE.is_file() or not REPORT.is_file(): raise RuntimeError('missing active, candidate, or reconciliation report')
 report=json.loads(REPORT.read_text(encoding='utf-8'))
 if report.get('status')!='RECONCILIATION_CANDIDATE_READY':
  raise RuntimeError('candidate is not backed by a reconciliation-ready report')
 before=facts(ACTIVE); candidate=facts(CANDIDATE)
 if report.get('active_source_sha256')!=digest(ACTIVE):
  raise RuntimeError('active database changed after candidate creation')
 if report.get('quality_annotated_candidate_sha256')!=digest(CANDIDATE):
  raise RuntimeError('quality-annotated candidate hash does not match reconciliation report')
 if candidate['integrity']!='ok' or candidate['unannotated_new_offers'] or candidate['unannotated_new_trades']:
  raise RuntimeError(f'candidate gate failed: {candidate}')
 if candidate['live_v1_imports'] or candidate['live_v2_imports']!=1:
  raise RuntimeError('candidate must contain exactly one reconciled live import')
 if candidate['live_v2_fingerprint']!=report.get('source_fingerprint_sha256'):
  raise RuntimeError('candidate live fingerprint mismatch')
 old=digest(ACTIVE); new=digest(CANDIDATE)
 VERSIONS.mkdir(parents=True,exist_ok=True)
 backup=VERSIONS/'conversation-data-previous.sqlite3'
 atomic_copy(ACTIVE,backup)
 day=NOW()[:10].replace('-','')
 daily=VERSIONS/f'conversation-data-daily-{day}.sqlite3'
 if not daily.exists(): atomic_copy(ACTIVE,daily)
 atomic_copy(CANDIDATE,ACTIVE)
 after=facts(ACTIVE)
 if after['integrity']!='ok' or digest(ACTIVE)!=new:
  raise RuntimeError('post-promotion verification failed')
 ledger={'schema_version':1,'promotions':[]}
 if LEDGER.exists(): ledger=json.loads(LEDGER.read_text(encoding='utf-8'))
 ledger.setdefault('promotions',[]).append({'promoted_at_utc':NOW(),'kind':'DATA_RECONCILIATION_ALGORITHM_UNCHANGED','reason':'Rebuild the pipeline-owned live-group slice from the current accepted parser state; historical/manual imports are preserved.','previous_sha256':old,'active_sha256':new,'rollback_backup':str(backup),'daily_backup':str(daily),'reconciliation':report.get('offer_reconciliation'),'before':before,'after':after})
 temporary=LEDGER.with_suffix('.json.tmp'); temporary.write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8'); os.chmod(temporary,0o600); temporary.replace(LEDGER)
 print(json.dumps({'previous':before,'active':after,'rollback_backup':str(backup),'daily_backup':str(daily),'active_sha256':new},ensure_ascii=False))

if __name__=='__main__': main()
