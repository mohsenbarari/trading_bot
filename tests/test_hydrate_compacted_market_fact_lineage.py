from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts import hydrate_compacted_market_fact_lineage as hydrate


RELEASE = "a" * 40
FACT = "b" * 64
EVENT = "c" * 64
PAYLOAD = "d" * 64
ENVELOPE = "e" * 64
TIME = "2026-08-26T10:00:00Z"


class HydrateCompactedMarketFactLineageTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.artifact = self.root / "lineage.tsv"
        self.source_receipt = self.root / "source.json"
        self.receiver_path = self.root / "receiver.sqlite"
        self.store_path = self.root / "store.sqlite"
        self.backup = self.root / "backup.sqlite"
        self.output = self.root / "output.json"
        row = (
            FACT,
            "GROUP_1",
            "market.fact.coin.group.1",
            "7",
            "1",
            "9",
            EVENT,
            PAYLOAD,
            "ELIGIBLE",
            ENVELOPE,
            TIME,
            TIME,
            TIME,
        )
        self.artifact.write_text("\t".join(row) + "\n", encoding="utf-8")
        source = {
            "schema": hydrate.SCHEMA,
            "status": "PASS",
            "release_sha": RELEASE,
            "cutoff_utc": hydrate.CUTOFF_UTC,
            "artifact_sha256": hydrate._digest_path(self.artifact),
            "row_count": 1,
            "source_counts": {"GROUP_1": 1},
            "value_free": True,
            "secrets_disclosed": False,
        }
        self.source_receipt.write_bytes(hydrate._canonical(source))

        receiver = sqlite3.connect(self.receiver_path)
        receiver.execute(
            "CREATE TABLE fact_deliveries("
            "stream_id TEXT,delivery_sequence INTEGER,fact_id TEXT,"
            "fact_revision INTEGER,payload_hash TEXT,payload_json TEXT,"
            "payload_compacted_at_utc TEXT,received_at_utc TEXT,"
            "PRIMARY KEY(stream_id,delivery_sequence))"
        )
        receiver.execute(
            "INSERT INTO fact_deliveries VALUES(?,?,?,?,?,?,?,?)",
            (
                "market.fact.coin.group.1",
                9,
                FACT,
                1,
                PAYLOAD,
                "",
                TIME,
                TIME,
            ),
        )
        receiver.commit()
        receiver.close()

        store = sqlite3.connect(self.store_path)
        store.executescript(
            """
            CREATE TABLE private_fact_adapter_deliveries(
              stream_id TEXT,delivery_sequence INTEGER,fact_id TEXT,
              fact_revision INTEGER,payload_hash TEXT,status TEXT,
              reason_code TEXT,applied_at_utc TEXT,
              PRIMARY KEY(stream_id,delivery_sequence));
            CREATE TABLE private_fact_adapter_projections(
              fact_id TEXT PRIMARY KEY,stream_id TEXT,source_sequence INTEGER,
              fact_revision INTEGER,event_key BLOB,payload_hash TEXT,status TEXT,
              occurred_at_utc TEXT,available_at_utc TEXT,parsed_at_utc TEXT,
              transferred_at_utc TEXT,adapted_at_utc TEXT,updated_at_utc TEXT,
              quality_state TEXT,envelope_hash TEXT);
            CREATE TABLE private_fact_adapter_projection_revisions(
              fact_id TEXT,fact_revision INTEGER,stream_id TEXT,
              source_sequence INTEGER,delivery_sequence INTEGER,event_key BLOB,
              payload_hash TEXT,quality_state TEXT,envelope_hash TEXT,status TEXT,
              occurred_at_utc TEXT,available_at_utc TEXT,parsed_at_utc TEXT,
              transferred_at_utc TEXT,adapted_at_utc TEXT,
              PRIMARY KEY(fact_id,fact_revision));
            CREATE TABLE private_fact_adapter_migrations(
              migration_code TEXT PRIMARY KEY,applied_at_utc TEXT);
            """
        )
        store.execute(
            "INSERT INTO private_fact_adapter_deliveries VALUES(?,?,?,?,?,?,?,?)",
            (
                "market.fact.coin.group.1",
                9,
                FACT,
                1,
                PAYLOAD,
                "APPLIED",
                None,
                TIME,
            ),
        )
        store.execute(
            "INSERT INTO private_fact_adapter_projections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                FACT,
                "market.fact.coin.group.1",
                7,
                1,
                bytes.fromhex(EVENT),
                PAYLOAD,
                "APPLIED",
                TIME,
                TIME,
                TIME,
                TIME,
                TIME,
                TIME,
                None,
                None,
            ),
        )
        store.commit()
        store.close()

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            release_sha=RELEASE,
            artifact=str(self.artifact),
            source_receipt=str(self.source_receipt),
            expected_source_receipt_sha256=hydrate._digest_path(
                self.source_receipt
            ),
            receiver_db=str(self.receiver_path),
            market_store_db=str(self.store_path),
            backup=str(self.backup),
            receipt=str(self.output),
        )

    def test_apply_restores_only_value_free_lineage_and_creates_backup(self) -> None:
        result = hydrate.apply_lineage(self.args())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["counts"]["projections_updated"], 1)
        self.assertEqual(result["counts"]["revisions_inserted"], 1)
        self.assertTrue(self.backup.is_file())
        store = sqlite3.connect(self.store_path)
        projection = store.execute(
            "SELECT quality_state,envelope_hash FROM private_fact_adapter_projections"
        ).fetchone()
        revision = store.execute(
            "SELECT quality_state,envelope_hash FROM "
            "private_fact_adapter_projection_revisions"
        ).fetchone()
        store.close()
        self.assertEqual(projection, ("ELIGIBLE", ENVELOPE))
        self.assertEqual(revision, ("ELIGIBLE", ENVELOPE))

    def test_apply_fails_closed_on_delivery_identity_mismatch(self) -> None:
        receiver = sqlite3.connect(self.receiver_path)
        receiver.execute("UPDATE fact_deliveries SET payload_hash=?", ("f" * 64,))
        receiver.commit()
        receiver.close()
        with self.assertRaisesRegex(
            hydrate.LineageHydrationError, "lineage_delivery_mismatch"
        ):
            hydrate.apply_lineage(self.args())
        store = sqlite3.connect(self.store_path)
        self.assertEqual(
            store.execute(
                "SELECT quality_state FROM private_fact_adapter_projections"
            ).fetchone()[0],
            None,
        )
        store.close()

    def test_row_rejects_wrong_stream_for_source(self) -> None:
        fields = next(hydrate._rows(self.artifact))
        wrong = list(fields)
        wrong[2] = "market.fact.coin.group.2"
        with self.assertRaisesRegex(
            hydrate.LineageHydrationError, "lineage_identity_invalid"
        ):
            hydrate._row(wrong)


if __name__ == "__main__":
    unittest.main()
