#!/usr/bin/env python3
"""Conservative semantic adjudication of model-candidate and review group rows."""
from __future__ import annotations
import re, sqlite3
from datetime import datetime,timezone
from scripts.coin_intelligence_private_ingest.runtime_paths import PIPELINE_ROOT as ROOT

NOW=lambda:datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')


def main() -> None:
    f=sqlite3.connect(ROOT/'group_filter.sqlite3'); f.execute('''CREATE TABLE IF NOT EXISTS adjudicated_labels(source_key TEXT NOT NULL,message_id TEXT NOT NULL,label INTEGER NOT NULL,reason TEXT NOT NULL,adjudicated_at_utc TEXT NOT NULL,PRIMARY KEY(source_key,message_id))''')
    s=sqlite3.connect(ROOT/'text_staging.sqlite3'); s.row_factory=sqlite3.Row; s.execute("attach database ? as filterdb",(str(ROOT/'group_filter.sqlite3'),))
    rows=s.execute("select t.*,d.decision from text_candidates t join filterdb.filter_decisions d using(source_key,message_id) where d.decision in ('KEEP_MODEL_CANDIDATE','REVIEW')").fetchall(); tally={}
    for r in rows:
        text=str(r['text'] or '').strip(); compact=re.sub(r'[\s.،,;؛:!?؟\-ـ]+','',text).lower(); decision=r['decision']
        if decision=='KEEP_MODEL_CANDIDATE': label,reason=1,'semantic_short_offer_or_trade_language'
        elif not compact or compact in {'ن','نه','اطفا','00000','ب'}: label,reason=0,'empty_punctuation_or_non_action_reply'
        elif any(x in text for x in ('تا','خریدار','میخر','می خو','بشه','شنبه','می تونم','مال شما')) or bool(re.search(r'\d',text)): label,reason=1,'actionable_quantity_price_or_settlement_reply'
        else: label,reason=0,'no_actionable_offer_or_trade_request_signal'
        f.execute('insert into adjudicated_labels values(?,?,?,?,?) on conflict(source_key,message_id) do update set label=excluded.label,reason=excluded.reason,adjudicated_at_utc=excluded.adjudicated_at_utc',(r['source_key'],r['message_id'],label,reason,NOW())); tally[(label,reason)]=tally.get((label,reason),0)+1
        f.execute("update filter_decisions set decision=?,reason=?,updated_at_utc=? where source_key=? and message_id=?",('KEEP_ADJUDICATED_RELEVANT' if label else 'REJECTED_NOISE_ADJUDICATED',reason,NOW(),r['source_key'],r['message_id']))
    f.commit(); print({'reviewed':len(rows),'tally':{str(k):v for k,v in tally.items()}})


if __name__ == '__main__':
    main()
