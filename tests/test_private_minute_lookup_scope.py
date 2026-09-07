"""Sparse replay minutes must not fetch all private facts between them."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_store import connect_market_store, initialize_market_store, upsert_observation
from core.market_intelligence.private_gold import (
    PrivateGoldOfferInput, private_gold_observations, refresh_private_gold_paper_minutes,
    ensure_private_minute_index,
)


class TrackedConnection:
    def __init__(self, connection):
        self.connection=connection
        self.source_reads=[]

    def execute(self, query, parameters=()):
        cursor=self.connection.execute(query,parameters)
        if 'SELECT price_num,event_type,settlement_term,trade_form,event_time_utc' not in query:
            return cursor
        tracker=self
        class TrackedCursor:
            def fetchall(self):
                rows=cursor.fetchall()
                tracker.source_reads.append((query,parameters,len(rows)))
                return rows
        return TrackedCursor()


class PrivateMinuteScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.connection=connect_market_store(Path(self.temp.name)/'market.sqlite')
        initialize_market_store(self.connection)

    def tearDown(self):
        self.connection.close();self.temp.cleanup()

    def offer(self, identity, timestamp, price=95000000):
        return private_gold_observations(PrivateGoldOfferInput(
            source_event_id=str(identity),published_at_utc=timestamp,
            available_at_utc=timestamp,text=f'{price:,} فروش 5 تا با حواله',
        ))[0]

    def test_sparse_minutes_read_only_requested_inputs_with_same_weighted_price(self):
        early=self.offer('early','2026-09-06T08:00:05Z',95000000)
        late=self.offer('late','2026-09-06T18:00:05Z',96000000)
        for observation in (early,late): upsert_observation(self.connection,observation)
        trade=replace(self.offer('trade','2026-09-06T08:00:15Z',97000000),event_type='TRADE')
        upsert_observation(self.connection,trade)
        for i in range(100):
            upsert_observation(self.connection,self.offer(f'irrelevant-{i}','2026-09-06T12:00:05Z'))
        tracker=TrackedConnection(self.connection)
        books=(('TOMORROW','NORMAL','2026-09-06T08:00:00Z'),('TOMORROW','NORMAL','2026-09-06T18:00:00Z'))
        report=refresh_private_gold_paper_minutes(tracker,minute_books=books,available_at_utc='2026-09-06T18:01:00Z')
        self.assertEqual(set(report),set(books))
        self.assertEqual(sum(read[2] for read in tracker.source_reads),3)
        rows=self.connection.execute("SELECT price_value,attributes_json FROM market_observations WHERE source_code='PRIVATE_GOLD_PAPER_MINUTE' ORDER BY event_time_utc").fetchall()
        self.assertEqual([r['price_value'] for r in rows],['96500000.0','96000000.0'])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM market_observations WHERE source_code='PRIVATE_GOLD_CHANNEL'").fetchone()[0],103)
        query,parameters,_=tracker.source_reads[0]
        plan=' '.join(str(tuple(r)) for r in self.connection.execute('EXPLAIN QUERY PLAN '+query,parameters))
        self.assertIn('idx_market_private_minute_bucket',plan)
        self.assertIn('<expr>=?',plan)

    def test_index_and_materialization_do_not_commit_the_callers_transaction(self):
        self.connection.execute('BEGIN')
        upsert_observation(self.connection,self.offer('retained','2026-09-06T08:00:05Z'))
        ensure_private_minute_index(self.connection)
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()
        self.assertEqual(self.connection.execute('SELECT count(*) FROM market_observations').fetchone()[0],0)
        self.assertIsNone(self.connection.execute("SELECT name FROM sqlite_master WHERE name='idx_market_private_minute_bucket'").fetchone())


if __name__=='__main__': unittest.main()
