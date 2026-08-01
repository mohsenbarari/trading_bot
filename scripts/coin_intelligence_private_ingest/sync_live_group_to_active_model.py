#!/usr/bin/env python3
"""Batch accepted live-group data into the active estimator conversation DB.

The script is intentionally serial and idempotent.  It relies on the existing
watchers to complete filtering/extraction first, then only promotes a newly
created, quality-annotated candidate.  No estimator algorithm is changed.
"""
from __future__ import annotations
import fcntl
import json
import subprocess
import sys
from datetime import datetime, timezone

from scripts.coin_intelligence_private_ingest.runtime_paths import (
    PIPELINE_ROOT as PIPE,
    REPOSITORY_ROOT,
)

BUILDER='scripts.coin_intelligence_private_ingest.build_active_conversation_candidate_from_shadow'
QUALITY='core.market_intelligence.conversation_quality'
PROMOTER='scripts.coin_intelligence_private_ingest.promote_live_group_conversation_data'
CANDIDATE=PIPE/'conversation_events.live-group-shadow.candidate.sqlite3'
LOCK=PIPE/'live_group_model_sync.lock'
NOW=lambda:datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')

def run(module:str,*args:str)->dict:
    result=subprocess.run(
        [sys.executable,'-m',module,*args],
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

def main()->None:
    LOCK.touch(exist_ok=True)
    with LOCK.open('r+') as handle:
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'status':'SYNC_ALREADY_RUNNING'})); return
        build=run(BUILDER)
        if build.get('status')=='NO_NEW_ACCEPTED_RECORDS':
            print(json.dumps({'status':'NO_NEW_ACCEPTED_RECORDS','checked_at_utc':NOW()})); return
        quality=run(QUALITY,'--conversation-db',str(CANDIDATE))
        promoted=run(PROMOTER)
        print(json.dumps({'status':'PROMOTED','checked_at_utc':NOW(),'build':build,'quality':quality,'promotion':promoted},ensure_ascii=False))

if __name__=='__main__': main()
