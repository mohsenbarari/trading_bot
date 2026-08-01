#!/usr/bin/env python3
"""Batch accepted live-group data into the active estimator conversation DB.

The script is intentionally serial and idempotent.  It relies on the existing
watchers to complete filtering/extraction first, then only promotes a newly
created, quality-annotated candidate.  No estimator algorithm is changed.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.coin_intelligence_private_ingest.runtime_paths import (
        PIPELINE_ROOT as PIPE,
        REPOSITORY_ROOT,
    )
    STANDALONE = False
except ModuleNotFoundError:  # Standalone immutable runtime deployment.
    PIPE = Path(__file__).resolve().parent
    REPOSITORY_ROOT = Path("/root/trading-bot/trading_bot")
    STANDALONE = True

BUILDER=(PIPE/'build_active_conversation_candidate_from_shadow.py' if STANDALONE else 'scripts.coin_intelligence_private_ingest.build_active_conversation_candidate_from_shadow')
QUALITY=(PIPE.parents[1]/'apps/coin-intelligence/market_quality.py' if STANDALONE else 'core.market_intelligence.conversation_quality')
PROMOTER=(PIPE/'promote_live_group_conversation_data.py' if STANDALONE else 'scripts.coin_intelligence_private_ingest.promote_live_group_conversation_data')
CANDIDATE=PIPE/'conversation_events.live-group-shadow.candidate.sqlite3'
REPORT=PIPE/'conversation_candidate_import.latest.json'
LOCK=PIPE/'live_group_model_sync.lock'
NOW=lambda:datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def run(target,*args:str)->dict:
    command=(
        [sys.executable,str(target),*args]
        if isinstance(target,Path)
        else [sys.executable,'-m',target,*args]
    )
    result=subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    # Most pipeline commands emit one compact JSON object.  market_quality
    # intentionally emits pretty-printed JSON, so parsing only its final line
    # (a bare ``}``) silently broke all live promotion after candidate creation.
    output=result.stdout.strip()
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        # Keep a narrow compatibility fallback for legacy commands that may
        # prefix a diagnostic line before their final compact JSON result.
        return json.loads(output.splitlines()[-1])

def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()

def record_quality_annotation(quality:dict)->None:
    report=json.loads(REPORT.read_text(encoding='utf-8'))
    report['quality_annotated_candidate_sha256']=digest(CANDIDATE)
    report['quality_summary']={
        key:quality.get(key) for key in (
            'offers_total','offers_training_eligible','trades_total',
            'trades_training_eligible','offers_crossed_excluded',
            'trades_crossed_excluded',
        )
    }
    temporary=REPORT.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.chmod(temporary,0o600)
    temporary.replace(REPORT)

def main()->None:
    LOCK.touch(exist_ok=True)
    with LOCK.open('r+') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'SYNC_ALREADY_RUNNING'})); return
        build=run(BUILDER)
        if build.get('status') in {
            'NO_NEW_ACCEPTED_RECORDS',
            'NO_RECONCILIATION_CHANGE',
        }:
            print(json.dumps({'status':build.get('status'),'checked_at_utc':NOW()})); return
        if build.get('status') != 'RECONCILIATION_CANDIDATE_READY':
            raise RuntimeError(f"unexpected reconciliation status: {build.get('status')}")
        if build.get('candidate_sha256') != digest(CANDIDATE):
            raise RuntimeError('candidate changed before quality annotation')
        quality=run(QUALITY,'--conversation-db',str(CANDIDATE))
        record_quality_annotation(quality)
        promoted=run(PROMOTER)
        print(json.dumps({'status':'PROMOTED','checked_at_utc':NOW(),'build':build,'quality':quality,'promotion':promoted},ensure_ascii=False))

if __name__=='__main__': main()
