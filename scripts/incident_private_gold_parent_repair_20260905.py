"""Replay only real, existing offer parents through the canonical exporter."""
import argparse
from contextlib import ExitStack
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import re
import time
from core.market_intelligence.private_pipeline_foundation import exclusive_lock, owner_lock_paths
from core.market_intelligence.private_coin_processor import _archive_connection, _research_archive_key
from core.market_intelligence.market_fact_archive import stable_fact_id
from core.market_intelligence.market_fact_projection import export_market_store_facts

p = argparse.ArgumentParser()
p.add_argument('--apply',action='store_true')
args = p.parse_args()
state = Path('/var/lib/market-data/state/market-processor')
with ExitStack() as locks:
    for lock in owner_lock_paths('market-processor'):
        locks.enter_context(exclusive_lock(lock))
    market = sqlite3.connect((state/'shadow-market.sqlite3').as_uri()+('?mode=rw' if args.apply else '?mode=ro'),uri=True)
    market.row_factory = sqlite3.Row
    market.create_function('event_key_bytes',1,lambda s: bytes.fromhex(s) if isinstance(s,str)
                           and re.fullmatch('[0-9a-f]{64}',s) else None,deterministic=True)
    query_started = time.monotonic()
    market.set_progress_handler(lambda: int(time.monotonic()-query_started>25),10000)
    rows = market.execute('''
      SELECT DISTINCT r.* FROM market_observations o
      JOIN market_observations r ON r.event_key=event_key_bytes(json_extract(o.attributes_json,'$.root_offer_event_key'))
      LEFT JOIN market_fact_export_ledger l ON l.event_key=o.event_key
      WHERE o.source_code='PRIVATE_GOLD_CHANNEL' AND o.event_type='TRADE'
      AND r.source_code=o.source_code AND r.event_type='OFFER'
      AND (l.event_key IS NULL OR l.observation_inserted_at_utc<>o.inserted_at_utc)
      ORDER BY r.id LIMIT 5000
    ''').fetchall()
    market.set_progress_handler(None,0)
    assert len(rows) < 5000, 'scope_too_large'
    archive = _archive_connection()
    missing = []
    with archive:
        with archive.cursor() as cursor:
            for row in rows:
                key = bytes(row['event_key'])
                fid = stable_fact_id(source_code='PRIVATE_GOLD_CHANNEL',event_key=key.hex(),fact_kind='PRIVATE_GOLD_OFFER')
                cursor.execute('SELECT 1 FROM market_data.private_gold_offers WHERE fact_id=decode(%s,\'hex\')',(fid,))
                if cursor.fetchone() is None:
                    missing.append(key)
    summary = {'candidate_roots':len(rows),'missing_roots':len(missing),
               'scope_sha256':sha256(b''.join(sorted(missing))).hexdigest(),'applied':False}
    print(json.dumps(summary),flush=True)
    if args.apply:
        assert len(missing) == 1 and summary['scope_sha256'] == '7fefe6fc0affe0379c98ce0180d3a5ad402d0dd2c2ef2ba52e37c4c33e0d6529', 'approved_scope_drift'
    if args.apply and missing:
        staging = sqlite3.connect((state/'capture-staging.sqlite3').as_uri()+'?mode=ro',uri=True)
        staging.row_factory = sqlite3.Row
        research = _research_archive_key()
        for offset in range(0,len(missing),100):
            keys = tuple(missing[offset:offset+100])
            try:
                with archive:
                    report = export_market_store_facts(market,archive,max_rows=len(keys),force_event_keys=keys,
                                                      capture_staging=staging,research_key=research)
                    assert report.rejected == 0 and report.selected == len(keys), 'canonical_parent_rejected'
                market.commit()
            except BaseException:
                market.rollback()
                raise
        staging.close()
        print(json.dumps({'applied':True,'parent_roots':len(missing),'data_deleted':False}),flush=True)
    archive.close()
    market.close()
