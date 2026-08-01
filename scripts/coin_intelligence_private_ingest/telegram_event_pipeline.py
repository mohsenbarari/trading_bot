#!/usr/bin/env python3
"""Idempotent raw/staging ingest for Telegram scraper snapshots and live archive.

Nothing here is a production price signal. raw_events is immutable-version
audit storage; text_staging is a replaceable candidate queue for experiments.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sqlite3, time
from datetime import datetime, timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import (
    DROP_ROOT as DROP,
    PRIVATE_ROOT as ROOT,
    PIPELINE_ROOT as PIPELINE,
)
RAW_DB, STAGING_DB = PIPELINE / 'raw_events.sqlite3', PIPELINE / 'text_staging.sqlite3'
RAW_SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS source_snapshots(path TEXT PRIMARY KEY,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,imported_at_utc TEXT NOT NULL,record_count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS raw_message_versions(id INTEGER PRIMARY KEY,source_key TEXT NOT NULL,message_id TEXT NOT NULL,content_sha256 TEXT NOT NULL,origin TEXT NOT NULL,observed_at_utc TEXT,record_json TEXT NOT NULL,first_ingested_at_utc TEXT NOT NULL,UNIQUE(source_key,message_id,content_sha256));
CREATE TABLE IF NOT EXISTS source_messages_current(source_key TEXT NOT NULL,message_id TEXT NOT NULL,content_sha256 TEXT NOT NULL,source_observed_at_utc TEXT,origin TEXT NOT NULL,record_json TEXT NOT NULL,updated_at_utc TEXT NOT NULL,PRIMARY KEY(source_key,message_id));
CREATE TABLE IF NOT EXISTS live_file_offsets(path TEXT PRIMARY KEY,byte_offset INTEGER NOT NULL,updated_at_utc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_raw_versions_source_message ON raw_message_versions(source_key,message_id);
'''
STAGING_SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS text_candidates(source_key TEXT NOT NULL,message_id TEXT NOT NULL,source_payload_sha256 TEXT NOT NULL,telegram_datetime TEXT,telegram_day TEXT,text TEXT NOT NULL,reply_detected INTEGER NOT NULL,reply_message_id TEXT,reply_reference_status TEXT,source_post_type TEXT,source_offer_side TEXT,source_trade_status TEXT,weak_label TEXT,label_origin TEXT NOT NULL,extraction_status TEXT NOT NULL DEFAULT 'PENDING',extractor_version TEXT,extracted_json TEXT,extraction_confidence REAL,updated_at_utc TEXT NOT NULL,PRIMARY KEY(source_key,message_id));
CREATE INDEX IF NOT EXISTS idx_candidates_status ON text_candidates(extraction_status,source_key);
'''
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def db(path,schema):
    path.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(path); c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA synchronous=FULL'); c.executescript(schema); return c
def hashed(r):
    s=json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':')); return hashlib.sha256(s.encode()).hexdigest(),s
def observed(r,fallback): return max((str(r[k]) for k in ('captured_at','emitted_at_utc','trade_detected_at','checked_at','telegram_edit_datetime','telegram_datetime','occurred_at_utc') if r.get(k)),default=fallback)

def has_textual_offer(r):
    return bool(str(r.get('initial_offer_text') or r.get('text') or '').strip())

# The transport normally sends one JSON object per Telegram post.  Under queue
# pressure the sender may instead post a JSON array, or concatenate complete
# JSON objects separated by a decorative horizontal rule.  Decode at the
# transport boundary so every inner event retains its own source/message id
# and therefore uses the normal idempotent storage path below.
_BATCH_RULE = re.compile(r'(?:\r?\n\s*){1,}[━─—-]{3,}\s*(?:\r?\n\s*){1,}')

# The original offer channel carried a short legacy combined stream.  After
# this Telegram post the three streams are intentionally separated.  The
# boundary is transport metadata, not a market-message id.
_SEPARATED_OFFER_CHANNEL_START = int(
    os.environ.get('COIN_PRIVATE_OFFER_STREAM_SPLIT_MESSAGE_ID') or '0'
)

def decode_live_payloads(payload_text):
    """Yield JSON object envelopes from one archived Telegram message.

    A malformed item never prevents its valid siblings from being ingested.
    Only JSON objects are yielded; marker prose and scalar/list elements are
    intentionally ignored by the caller.
    """
    if not isinstance(payload_text,str): return
    try:
        decoded=json.loads(payload_text)
        items=decoded if isinstance(decoded,list) else [decoded]
    except json.JSONDecodeError:
        items=[]
        # This fallback is deliberately narrow: do not attempt to infer JSON
        # from ordinary Telegram prose.  It only supports the sender's known
        # multi-event delimiter, with each segment required to be valid JSON.
        for segment in _BATCH_RULE.split(payload_text):
            segment=segment.strip()
            if not segment: continue
            try:
                decoded=json.loads(segment)
            except json.JSONDecodeError:
                continue
            items.extend(decoded if isinstance(decoded,list) else [decoded])
    for item in items:
        if isinstance(item,dict): yield item

def accepted_for_event_channel(channel, telegram_message_id, market, event_type):
    """Prevent a misrouted event from becoming an active market candidate."""
    if channel == 'trade':
        return market == 'gold' and event_type == 'offer_verified'
    if channel == 'coin':
        return market == 'coin' and event_type == 'message_created'
    if channel == 'offer':
        # Before the separate channels existed, this channel legitimately
        # carried gold verification updates and a small coin backfill.
        if int(telegram_message_id or 0) <= _SEPARATED_OFFER_CHANNEL_START:
            return (market == 'gold' and event_type in {'message_created','offer_verified'}) or (market == 'coin' and event_type == 'message_created')
        return market == 'gold' and event_type == 'message_created'
    return False

def is_account1_trade_update(r):
    """A trade-channel payload is a verifier update, not a replacement offer."""
    return not has_textual_offer(r) and ('verification' in r or 'trade' in r) and r.get('message_id') is not None

def prior_textual_offer(raw,source,mid,current=None):
    """Find the last complete offer snapshot for an idempotent verifier update."""
    if current and has_textual_offer(current): return current
    rows=raw.execute('SELECT record_json FROM raw_message_versions WHERE source_key=? AND message_id=? ORDER BY id DESC',(source,mid)).fetchall()
    for (blob,) in rows:
        candidate=json.loads(blob)
        if has_textual_offer(candidate): return candidate
    return None

def merge_account1_trade_update(base, update):
    """Overlay verifier facts while retaining the offer text and quote fields."""
    merged=dict(base)
    verification=update.get('verification')
    if isinstance(verification,dict):
        merged['verification']=verification
        for source_key,target_key in (('state','check_state'),('result','check_result'),('checked_at','checked_at')):
            if verification.get(source_key) is not None: merged[target_key]=verification[source_key]
    trade=update.get('trade')
    if isinstance(trade,dict):
        for source_key,target_key in (
            ('status','trade_status'),('traded_quantity','traded_quantity'),
            ('trade_detected_at','trade_detected_at'),('telegram_edit_datetime','telegram_edit_datetime'),
            ('trade_time_source','trade_time_source'),
        ):
            if trade.get(source_key) is not None: merged[target_key]=trade[source_key]
        if trade.get('telegram_edit_datetime') and not merged.get('trade_time_source'):
            merged['trade_time_source']='telegram_edit_metadata'
    elif isinstance(verification,dict) and verification.get('result') == 'no_trade':
        merged['trade_status']='NONE'
    return merged
def put(raw,stage,source,r,origin,fallback):
    mid=str(r.get('message_id') or '')
    if not source or not mid: return False
    source_h,source_s=hashed(r); source_seen=observed(r,fallback)
    raw.execute('INSERT OR IGNORE INTO raw_message_versions(source_key,message_id,content_sha256,origin,observed_at_utc,record_json,first_ingested_at_utc) VALUES(?,?,?,?,?,?,?)',(source,mid,source_h,origin,source_seen,source_s,now()))
    current_row=raw.execute('SELECT record_json FROM source_messages_current WHERE source_key=? AND message_id=?',(source,mid)).fetchone()
    current=json.loads(current_row[0]) if current_row else None
    if source=='account1_channel' and is_account1_trade_update(r):
        base=prior_textual_offer(raw,source,mid,current)
        if base is None:
            # Keep the verifier payload in audit/current storage until its
            # matching offer arrives; it is deliberately not staged as an
            # offer with a missing quote.
            return False
        r=merge_account1_trade_update(base,r)
    elif source=='account1_channel' and has_textual_offer(r):
        # Rarely delivery can invert the offer and verifier-update messages.
        # Fold the latest already-audited verifier result into the offer so a
        # future parser run is independent of cross-channel arrival order.
        for (blob,) in raw.execute('SELECT record_json FROM raw_message_versions WHERE source_key=? AND message_id=? ORDER BY id DESC',(source,mid)):
            candidate=json.loads(blob)
            if is_account1_trade_update(candidate):
                r=merge_account1_trade_update(r,candidate)
                break
    h,s=hashed(r); seen=observed(r,fallback)
    old=raw.execute('SELECT content_sha256 FROM source_messages_current WHERE source_key=? AND message_id=?',(source,mid)).fetchone()
    if old and old[0]==h: return False
    raw.execute('INSERT INTO source_messages_current(source_key,message_id,content_sha256,source_observed_at_utc,origin,record_json,updated_at_utc) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_key,message_id) DO UPDATE SET content_sha256=excluded.content_sha256,source_observed_at_utc=excluded.source_observed_at_utc,origin=excluded.origin,record_json=excluded.record_json,updated_at_utc=excluded.updated_at_utc',(source,mid,h,seen,origin,s,now()))
    weak=r.get('post_type') if source=='account1_channel' else None
    stage.execute('''INSERT INTO text_candidates(source_key,message_id,source_payload_sha256,telegram_datetime,telegram_day,text,reply_detected,reply_message_id,reply_reference_status,source_post_type,source_offer_side,source_trade_status,weak_label,label_origin,extraction_status,updated_at_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'PENDING', ?) ON CONFLICT(source_key,message_id) DO UPDATE SET source_payload_sha256=excluded.source_payload_sha256,telegram_datetime=excluded.telegram_datetime,telegram_day=excluded.telegram_day,text=excluded.text,reply_detected=excluded.reply_detected,reply_message_id=excluded.reply_message_id,reply_reference_status=excluded.reply_reference_status,source_post_type=excluded.source_post_type,source_offer_side=excluded.source_offer_side,source_trade_status=excluded.source_trade_status,weak_label=excluded.weak_label,label_origin=excluded.label_origin,extraction_status='PENDING',extractor_version=NULL,extracted_json=NULL,extraction_confidence=NULL,updated_at_utc=excluded.updated_at_utc''',(source,mid,h,r.get('telegram_datetime'),r.get('telegram_day'),str(r.get('text') or ''),int(bool(r.get('reply_detected'))),str(r['reply_message_id']) if r.get('reply_message_id') is not None else None,r.get('reply_reference_status'),r.get('post_type'),r.get('offer_side'),r.get('trade_status'),weak,'SOURCE_VERIFIED' if weak else 'UNLABELED',now()))
    return True
def snapshots(raw,stage):
    files=recs=changed=0
    for p in sorted(DROP.rglob('*.json')):
        b=p.read_bytes(); h=hashlib.sha256(b).hexdigest(); key=str(p.relative_to(ROOT)); prior=raw.execute('SELECT sha256 FROM source_snapshots WHERE path=?',(key,)).fetchone()
        if prior and prior[0]==h: continue
        x=json.loads(b); source=str((x.get('source') or {}).get('key') or ''); rows=x.get('messages') or []
        raw.execute('BEGIN'); stage.execute('BEGIN')
        try:
            for r in rows: recs+=1; changed+=put(raw,stage,source,r,'SNAPSHOT',now())
            raw.execute('INSERT INTO source_snapshots(path,sha256,bytes,imported_at_utc,record_count) VALUES(?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,bytes=excluded.bytes,imported_at_utc=excluded.imported_at_utc,record_count=excluded.record_count',(key,h,len(b),now(),len(rows))); raw.commit(); stage.commit(); files+=1
        except Exception: raw.rollback(); stage.rollback(); raise
    return {'snapshot_files_imported':files,'snapshot_records_seen':recs,'snapshot_current_rows_changed':changed}
def live(raw,stage,replay=False):
    archive_messages=seen=changed=skipped=misrouted=0
    for p in sorted(ROOT.glob('events-*.jsonl')):
        key=str(p.relative_to(ROOT)); size=p.stat().st_size; row=raw.execute('SELECT byte_offset FROM live_file_offsets WHERE path=?',(key,)).fetchone(); off=0 if replay else (int(row[0]) if row else 0)
        if size<off: off=0
        with p.open(encoding='utf-8') as f: f.seek(off); lines=f.readlines(); end=f.tell()
        if not lines: continue
        raw.execute('BEGIN'); stage.execute('BEGIN')
        try:
            for line in lines:
                e=json.loads(line)
                archive_messages+=1
                # payload_format is only listener telemetry.  In particular a
                # delimiter-separated batch is labelled non_json_text because
                # the *outer* message is not one JSON document; it must still
                # be decoded here.
                for x in decode_live_payloads(e.get('payload_text')):
                    source=x.get('source') or {}
                    # Live archives carry one market-specific payload.  Gold-channel
                    # events use ``gold`` and coin-group events use ``coin``.
                    # Keeping this dispatch explicit prevents one stream silently
                    # being discarded when the other is healthy.
                    market=str(source.get('market') or '').lower()
                    payload=x.get('gold') if market=='gold' else x.get('coin') if market=='coin' else None
                    if not isinstance(payload,dict) or not source:
                        skipped+=1
                        continue
                    if not accepted_for_event_channel(str(e.get('event_channel_key') or 'offer'), e.get('telegram_message_id'), market, str(x.get('event_type') or '')):
                        # Keep the outer Telegram post in immutable archive,
                        # but never let a cross-routed item reach current or
                        # text staging.  This is a transport safety boundary.
                        misrouted+=1
                        continue
                    seen+=1
                    changed+=put(raw,stage,str(source.get('source_key') or ''),payload,'LIVE_'+(market.upper() or 'UNKNOWN'),str(x.get('emitted_at_utc') or now()))
            raw.execute('INSERT INTO live_file_offsets(path,byte_offset,updated_at_utc) VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET byte_offset=excluded.byte_offset,updated_at_utc=excluded.updated_at_utc',(key,end,now())); raw.commit(); stage.commit()
        except Exception: raw.rollback(); stage.rollback(); raise
    return {'live_archive_messages_seen':archive_messages,'live_events_seen':seen,'live_invalid_payload_items_skipped':skipped,'live_misrouted_items_skipped':misrouted,'live_current_rows_changed':changed}
def report(raw,stage):
    return {'raw_versions':raw.execute('SELECT count(*) FROM raw_message_versions').fetchone()[0],'current_messages':raw.execute('SELECT count(*) FROM source_messages_current').fetchone()[0],'current_by_source':dict(raw.execute('SELECT source_key,count(*) FROM source_messages_current GROUP BY source_key')),'staging_candidates':stage.execute('SELECT count(*) FROM text_candidates').fetchone()[0],'weak_labels':dict(stage.execute("SELECT COALESCE(weak_label,'UNLABELED'),count(*) FROM text_candidates GROUP BY weak_label"))}
def once(replay_live=False):
    raw,stage=db(RAW_DB,RAW_SCHEMA),db(STAGING_DB,STAGING_SCHEMA)
    try: return {**snapshots(raw,stage),**live(raw,stage,replay=replay_live),**report(raw,stage)}
    finally: raw.close(); stage.close()
if __name__=='__main__':
    a=argparse.ArgumentParser(); a.add_argument('--watch-seconds',type=float); a.add_argument('--replay-live',action='store_true'); x=a.parse_args()
    first=True
    while True:
        raw,stage=db(RAW_DB,RAW_SCHEMA),db(STAGING_DB,STAGING_SCHEMA)
        try:
            result=(snapshots(raw,stage) if first else {}) | live(raw,stage,replay=x.replay_live and first) | report(raw,stage)
        finally:
            raw.close(); stage.close()
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if x.watch_seconds is None: break
        first=False
        time.sleep(x.watch_seconds)
