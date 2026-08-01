#!/usr/bin/env python3
"""Train/evaluate a group-only relevance filter and write reversible decisions."""
from __future__ import annotations
import json, math, re, sqlite3
from collections import Counter
from datetime import datetime, timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import (
    CONVERSATION_LABEL_DB as LABEL_DB,
    PIPELINE_ROOT as PIPE,
)

STAGE=PIPE/'text_staging.sqlite3'; FILTER=PIPE/'group_filter.sqlite3'; MODEL=PIPE/'group_relevance_nb_v1.json'
VERSION='group-relevance-charword-nb-v1'
SCHEMA='''CREATE TABLE IF NOT EXISTS filter_runs(id INTEGER PRIMARY KEY,version TEXT NOT NULL,created_at_utc TEXT NOT NULL,metrics_json TEXT NOT NULL); CREATE TABLE IF NOT EXISTS filter_decisions(source_key TEXT NOT NULL,message_id TEXT NOT NULL,source_payload_sha256 TEXT NOT NULL,model_probability REAL NOT NULL,parser_kind TEXT,decision TEXT NOT NULL,reason TEXT NOT NULL,model_version TEXT NOT NULL,updated_at_utc TEXT NOT NULL,PRIMARY KEY(source_key,message_id)); CREATE INDEX IF NOT EXISTS idx_filter_decision ON filter_decisions(decision);'''
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def norm(s): return re.sub(r'\s+',' ',str(s).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹','0123456789')).replace('\u200c',' ')).strip().lower()
def feats(text):
 s=norm(text); words=re.findall(r'[\wآ-ی]+',s); out={'w:'+w for w in words}
 compact=re.sub(r'\s+','_',s)
 out|={'c:'+compact[i:i+3] for i in range(max(0,len(compact)-2))}
 return out
def fit(rows):
 docs=Counter(); counts={0:Counter(),1:Counter()}
 for _,text,y in rows:
  docs[y]+=1; counts[y].update(feats(text))
 return {'version':VERSION,'docs':dict(docs),'positive':dict(counts[1]),'negative':dict(counts[0])}
def prob(m,text):
 d0,d1=int(m['docs'].get('0',m['docs'].get(0,0))),int(m['docs'].get('1',m['docs'].get(1,0))); pos=Counter(m['positive']); neg=Counter(m['negative']); a=math.log((d1+1)/(d0+1)); b=0.0
 for f in feats(text):
  p1=(pos[f]+1)/(d1+2); p0=(neg[f]+1)/(d0+2); a+=math.log(p1); b+=math.log(p0)
 z=max(-40,min(40,a-b)); return 1/(1+math.exp(-z))
def labelled():
 c=sqlite3.connect(LABEL_DB)
 q='''WITH positive AS (SELECT import_id,message_id FROM offers UNION SELECT import_id,request_message_id FROM trade_requests) SELECT m.event_time_utc,m.text,CASE WHEN p.message_id IS NULL THEN 0 ELSE 1 END FROM messages m LEFT JOIN positive p ON p.import_id=m.import_id AND p.message_id=m.message_id WHERE trim(m.text)<>'' ORDER BY m.event_time_utc,m.message_id'''
 return c.execute(q).fetchall()
def metrics(model,rows):
 tp=fp=tn=fn=0
 for _,text,y in rows:
  pred=prob(model,text)>=.5
  tp+=pred and y==1; fp+=pred and y==0; tn+=(not pred) and y==0; fn+=(not pred) and y==1
 return {'holdout_rows':len(rows),'precision':tp/(tp+fp) if tp+fp else 0,'recall':tp/(tp+fn) if tp+fn else 0,'specificity':tn/(tn+fp) if tn+fp else 0,'confusion':{'tp':tp,'fp':fp,'tn':tn,'fn':fn}}
def main():
 rows=labelled(); cut=int(len(rows)*.8); m=fit(rows[:cut]); audit=metrics(m,rows[cut:]);
 manual=[]
 try:
  x=sqlite3.connect(FILTER); x.execute("attach database ? as stage",(str(STAGE),)); manual=[('',r[0],r[1]) for r in x.execute('select t.text,a.label from adjudicated_labels a join stage.text_candidates t using(source_key,message_id)')]
 except sqlite3.OperationalError: pass
 full=fit(rows+manual); MODEL.write_text(json.dumps({'model':full,'holdout':audit,'training_rows':len(rows),'adjudicated_rows':len(manual)},ensure_ascii=False),encoding='utf-8')
 f=sqlite3.connect(FILTER); f.executescript(SCHEMA); f.execute('insert into filter_runs(version,created_at_utc,metrics_json) values(?,?,?)',(VERSION,now(),json.dumps(audit)))
 s=sqlite3.connect(STAGE); s.row_factory=sqlite3.Row
 rows=s.execute("select * from text_candidates where source_key in ('account2_group1','account2_group2')").fetchall(); tally=Counter()
 for r in rows:
  override=f.execute('select label,reason from adjudicated_labels where source_key=? and message_id=?',(r['source_key'],r['message_id'])).fetchone()
  p=prob(full,r['text']); payload=json.loads(r['extracted_json'] or '{}'); kind=payload.get('kind')
  if override:
   dec,why=('KEEP_ADJUDICATED_RELEVANT' if override[0] else 'REJECTED_NOISE_ADJUDICATED'),override[1]
  elif kind=='OFFER_CANDIDATE': dec,why='KEEP_OFFER_CANDIDATE','parser_offer'
  elif str(kind).startswith('REPLY_') and kind not in ('REPLY_NEGOTIATION','REPLY_QUESTION','REPLY_QUANTITY_QUESTION'): dec,why='KEEP_TRADE_REQUEST_CANDIDATE','reply_signal'
  elif p>=.75: dec,why='KEEP_MODEL_CANDIDATE','model_high_relevance'
  elif p<.08: dec,why='REJECTED_NOISE','model_low_relevance_and_no_parser_signal'
  else: dec,why='REVIEW','uncertain'
  f.execute('''insert into filter_decisions(source_key,message_id,source_payload_sha256,model_probability,parser_kind,decision,reason,model_version,updated_at_utc) values(?,?,?,?,?,?,?,?,?) on conflict(source_key,message_id) do update set source_payload_sha256=excluded.source_payload_sha256,model_probability=excluded.model_probability,parser_kind=excluded.parser_kind,decision=excluded.decision,reason=excluded.reason,model_version=excluded.model_version,updated_at_utc=excluded.updated_at_utc''',(r['source_key'],r['message_id'],r['source_payload_sha256'],p,kind,dec,why,VERSION,now())); tally[dec]+=1
 f.commit(); print(json.dumps({'training_rows':len(rows),'holdout':audit,'decisions':dict(tally),'model':str(MODEL)},ensure_ascii=False))
if __name__=='__main__': main()
