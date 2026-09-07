"""Live inputs must not wait behind days of unrelated retained history."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.capture_event_adapter import (
    _select_dirty_market_messages, decode_market_channel_event,
    initialize_capture_adapter, project_capture_changes, stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_store import connect_market_store, initialize_market_store
from tests.test_capture_event_adapter import market_event


class CaptureLiveFairnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.staging = connect_coin_group_staging(Path(self.temp.name)/'capture.sqlite')
        self.market = connect_market_store(Path(self.temp.name)/'market.sqlite')
        initialize_capture_adapter(self.staging)
        initialize_market_store(self.market)
        self.now = '2026-08-24T12:00:00Z'

    def tearDown(self):
        self.staging.close()
        self.market.close()
        self.temp.cleanup()

    def insert(self, source, identity, timestamp):
        self.staging.execute('INSERT INTO capture_dirty_market_messages VALUES(?,?,?,?)',
                             (source, identity, timestamp, timestamp))

    def selected(self, limit=20):
        return _select_dirty_market_messages(self.staging, as_of_utc=self.now, max_messages=limit)

    def test_each_live_independent_lane_and_each_causal_lane_get_capacity(self):
        for i in range(1,101):
            self.insert('MELTED_PRIMARY_FLOW',i,'2026-08-24T09:00:00Z')
        for source in ('MELTED_PRIMARY_FLOW','MELTED_AGGREGATE','XAUUSD','USD_HERAT','MELTED_FLOW'):
            self.insert(source,1000,'2026-08-24T11:59:00Z')
        for source in ('USD_HERAT','MELTED_FLOW'):
            for i in range(1,5):
                self.insert(source,i,'2026-08-24T10:00:00Z')
        rows=self.selected()
        keys={(r['source_id'],r['message_id']) for r in rows}
        self.assertEqual(len(rows),20)
        for source in ('MELTED_PRIMARY_FLOW','MELTED_AGGREGATE','XAUUSD'):
            self.assertIn((source,1000),keys)
        for source in ('USD_HERAT','MELTED_FLOW'):
            self.assertIn((source,1),keys)
            self.assertIn((source,2),keys)
            self.assertNotIn((source,1000),keys)
        # Even during continuous fresh traffic at least half goes to oldest history.
        self.assertGreaterEqual(sum(r['event_time_utc']=='2026-08-24T09:00:00Z' for r in rows),10)
        self.assertEqual(self.staging.execute('SELECT count(*) FROM capture_dirty_market_messages').fetchone()[0],113)

    def test_future_rows_are_never_selected_and_small_batch_preserves_order(self):
        for i in range(1,13): self.insert('XAUUSD',i,'2026-08-24T09:00:00Z')
        self.insert('XAUUSD',100,'2026-08-24T12:01:00Z')
        self.assertEqual([r['message_id'] for r in self.selected(3)],[1,2,3])
        self.assertEqual(len(self.selected()),12)
        self.assertEqual(len(self.selected(None)),12)

    def test_repeated_batches_drain_every_identity_once_and_keep_timestamps(self):
        for source in ('MELTED_PRIMARY_FLOW','MELTED_AGGREGATE','XAUUSD','USD_HERAT','MELTED_FLOW'):
            for i in range(1,31):
                self.insert(source,i,'2026-08-24T11:59:00Z' if i>25 else '2026-08-24T09:00:00Z')
        expected={tuple(r) for r in self.staging.execute('SELECT * FROM capture_dirty_market_messages')}
        observed=[]
        for _ in range(20):
            rows=self.selected(16)
            if not rows: break
            observed.extend(tuple(r) for r in rows)
            self.staging.executemany('DELETE FROM capture_dirty_market_messages WHERE source_id=? AND message_id=?',
                                     [(r['source_id'],r['message_id']) for r in rows])
        self.assertEqual(len(observed),len(set(observed)))
        self.assertEqual(set(observed),expected)

    def test_additive_indexes_exist_on_new_and_retained_schema(self):
        for name in ('idx_capture_dirty_market_source_ready','idx_capture_dirty_market_source_event'):
            self.assertIsNotNone(self.staging.execute('SELECT name FROM sqlite_master WHERE name=?',(name,)).fetchone())
            self.staging.execute('DROP INDEX '+name)
        initialize_capture_adapter(self.staging)
        for source,fragment in (('XAUUSD','source_event'),('USD_HERAT','source_ready')):
            query=('SELECT * FROM capture_dirty_market_messages WHERE source_id=? AND event_time_utc>=? AND available_at_utc<=? ORDER BY event_time_utc DESC LIMIT 2'
                   if source=='XAUUSD' else 'SELECT * FROM capture_dirty_market_messages WHERE source_id=? AND available_at_utc>=? AND available_at_utc<=? ORDER BY available_at_utc,message_id LIMIT 2')
            plan=' '.join(str(tuple(r)) for r in self.staging.execute('EXPLAIN QUERY PLAN '+query,(source,'2026-08-24T11:50:00Z',self.now)))
            self.assertIn('idx_capture_dirty_market_'+fragment,plan)
            self.assertNotIn('USE TEMP B-TREE',plan)

    def test_primary_live_revision_is_complete_and_final_history_matches_fifo(self):
        def stage(connection, record):
            stage_capture_event(connection,decode_market_channel_event(record))
        records=[market_event(i,source='MELTED_PRIMARY_FLOW',message_id=i,
                             text='95,000,000 فروش 10 تا بدون حواله',
                             published='2026-08-24T10:00:00Z',available='2026-08-24T10:00:01Z') for i in range(1,31)]
        records.extend([
            market_event(100,source='MELTED_PRIMARY_FLOW',message_id=100,
                         text='96,000,000 فروش 10 تا بدون حواله',published='2026-08-24T11:57:00Z',available='2026-08-24T11:57:01Z'),
            market_event(101,source='MELTED_PRIMARY_FLOW',message_id=100,event_type='message_edited',
                         text='96,000,000 فروش 10 تا بدون حواله باقی 6',published='2026-08-24T11:57:00Z',
                         edited='2026-08-24T11:57:40Z',available='2026-08-24T11:57:41Z'),
        ])
        fifo_staging=connect_coin_group_staging(Path(self.temp.name)/'fifo-capture.sqlite')
        fifo_market=connect_market_store(Path(self.temp.name)/'fifo-market.sqlite')
        try:
            initialize_capture_adapter(fifo_staging); initialize_market_store(fifo_market)
            for record in records:
                stage(self.staging,record);stage(fifo_staging,record)
            project_capture_changes(self.staging,self.market,as_of_utc=self.now,max_market_messages=10)
            trade=self.market.execute("SELECT price_value,quantity_value,event_time_utc,available_at_utc FROM market_observations WHERE event_type='TRADE'").fetchone()
            self.assertIsNotNone(trade)
            self.assertEqual(trade['price_value'],'96000000')
            self.assertEqual(trade['quantity_value'],'4')
            self.assertGreater(self.staging.execute('SELECT count(*) FROM capture_dirty_market_messages').fetchone()[0],0)
            for _ in range(10):
                project_capture_changes(self.staging,self.market,as_of_utc=self.now,max_market_messages=10)
            project_capture_changes(fifo_staging,fifo_market,as_of_utc=self.now)
            columns='event_key,event_time_utc,available_at_utc,instrument,event_type,price_value,quantity_value,quality_state,attributes_json'
            self.assertEqual([tuple(r) for r in self.market.execute('SELECT '+columns+' FROM market_observations ORDER BY event_key')],
                             [tuple(r) for r in fifo_market.execute('SELECT '+columns+' FROM market_observations ORDER BY event_key')])
        finally:
            fifo_market.close();fifo_staging.close()


if __name__=='__main__':
    unittest.main()
