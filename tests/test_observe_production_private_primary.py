from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_verify_production_private_primary_promotion import NOW, _view
from scripts import verify_production_private_primary_promotion as verifier


SCRIPT = Path(__file__).parents[1] / "scripts" / "observe_production_private_primary.py"
SPEC = importlib.util.spec_from_file_location("observe_private_primary", SCRIPT)
assert SPEC and SPEC.loader
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)

SHA = "a" * 40
TREE = "b" * 40
IMAGE = "sha256:" + "c" * 64
PROJECT = "market-primary"


def container(
    service: str,
    *,
    image: str = IMAGE,
    feed: str = "PRIVATE_PRIMARY",
    running: bool = True,
    project: str = PROJECT,
):
    env = [f"MARKET_PIPELINE_RELEASE_SHA={SHA}", f"MARKET_PIPELINE_RELEASE_TREE={TREE}", f"MARKET_PIPELINE_FEED_MODE={feed}"]
    return {
        "Id": service + "-id",
        "Image": image if service != "market-database" else "sha256:" + "d" * 64,
        "Config": {
            "Labels": {
                "com.docker.compose.project": project,
                "com.docker.compose.service": service,
                "org.opencontainers.image.revision": SHA,
                "io.gold-trade.release.tree": TREE,
            },
            "Env": env,
        },
        "State": {"Running": running, "Health": {"Status": "healthy"}},
    }


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshot.json"
        self.snapshot.write_text(json.dumps(_view(version=9)))

    def tearDown(self):
        self.temp.cleanup()

    def _receiver(self, *, pending=0, duplicate=0, rejected=0):
        path = self.root / ("receiver-web.sqlite" if pending else "receiver.sqlite")
        db = sqlite3.connect(path)
        db.executescript("""
        CREATE TABLE fact_checkpoints(stream_id TEXT,highest_contiguous_sequence INTEGER);
        CREATE TABLE receiver_counters(singleton INTEGER,duplicate_count INTEGER,rejection_count INTEGER);
        CREATE TABLE estimator_snapshot_publication_outbox(feed_mode TEXT,delivered_at_utc TEXT);
        CREATE TABLE estimator_snapshot_rejections(rejection_id INTEGER);
        """)
        db.executemany("INSERT INTO fact_checkpoints VALUES(?,?)", [("one", 5), ("two", 7)])
        db.execute("INSERT INTO receiver_counters VALUES(1,?,?)", (duplicate, rejected))
        db.executemany(
            "INSERT INTO estimator_snapshot_publication_outbox VALUES('PRIVATE_PRIMARY',?)",
            [(None,)] * pending,
        )
        db.commit(); db.close()
        return path

    def _store(self, *, gap=False):
        path = self.root / "store.sqlite"
        db = sqlite3.connect(path)
        db.executescript("""
        CREATE TABLE private_fact_adapter_checkpoints(stream_id TEXT,highest_delivery_sequence INTEGER);
        CREATE TABLE private_fact_adapter_status_counts(status TEXT,delivery_count INTEGER);
        """)
        db.executemany("INSERT INTO private_fact_adapter_checkpoints VALUES(?,?)", [("one", 5), ("two", 6 if gap else 7)])
        db.execute("INSERT INTO private_fact_adapter_status_counts VALUES('REJECTED',0)")
        db.commit(); db.close()
        return path

    def _sender(self):
        path = self.root / "sender.sqlite"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE estimator_snapshot_sender_state(singleton INTEGER,acknowledged_version INTEGER)")
        db.execute("INSERT INTO estimator_snapshot_sender_state VALUES(1,9)")
        db.commit(); db.close()
        return path

    def _bot(self, inventory=None, **kwargs):
        receiver = kwargs["receiver"] if "receiver" in kwargs else self._receiver()
        store = kwargs["store"] if "store" in kwargs else self._store()
        sender = kwargs["sender"] if "sender" in kwargs else self._sender()
        return observer.observe(
            role="bot", release_sha=SHA, release_tree=TREE, project=PROJECT,
            image_id=IMAGE, snapshot_path=self.snapshot,
            inventory=inventory or [container(s) for s in observer.BOT_SERVICES],
            health_documents=self._health(observer.BOT_SERVICES),
            receiver_db=receiver,
            market_store_db=store,
            sender_db=sender,
            now=kwargs.get("now"),
        )

    @staticmethod
    def _health(services):
        return {
            service: {"role": service, "release_sha": SHA, "mode": "live", "status": "live-ready", "feed_mode": "PRIVATE_PRIMARY"}
            for service in services if service != "market-database"
        }

    def test_bot_observation_is_exact_value_free_contract(self):
        result = self._bot()
        self.assertEqual(result["schema"], observer.SCHEMA)
        self.assertEqual(result["counts"], {"duplicate": 0, "rejected": 0, "dead_letter": 0, "open_outbox": 0, "receiver_publication_pending": 0})
        rendered = json.dumps(result)
        self.assertNotIn("published_at_utc", rendered)
        self.assertNotIn("rates", rendered)

    def test_fake_expected_identity_cannot_override_inspected_identity(self):
        bad = [container(s) for s in observer.BOT_SERVICES]
        bad[0]["Config"]["Labels"]["org.opencontainers.image.revision"] = "f" * 40
        with self.assertRaisesRegex(observer.ObservationError, "container_release_binding_invalid"):
            self._bot(inventory=bad)

    def test_duplicate_owner_fails_closed(self):
        rows = [container(s) for s in observer.BOT_SERVICES]
        rows.append(container("coin-estimator"))
        with self.assertRaisesRegex(observer.ObservationError, "owner_count_invalid"):
            self._bot(inventory=rows)

    def test_owner_in_an_older_project_fails_closed(self):
        rows = [container(s) for s in observer.BOT_SERVICES]
        rows.append(container("coin-estimator", project="market-private-shadow-old"))
        with self.assertRaisesRegex(
            observer.ObservationError, "legacy_or_unexpected_owner"
        ):
            self._bot(inventory=rows)

    def test_stopped_bluegreen_rollback_container_is_not_a_live_owner(self):
        rows = [container(s) for s in observer.BOT_SERVICES]
        rows.append(
            container(
                "coin-estimator",
                project="market-private-shadow-old",
                running=False,
            )
        )
        result = self._bot(inventory=rows)
        self.assertEqual(result["legacy_owner_count"], 0)

    def test_unlabelled_unrelated_stopped_container_is_ignored(self):
        rows = [container(s) for s in observer.BOT_SERVICES]
        rows.append({"Id": "unrelated", "Config": {"Labels": None}, "State": {"Running": False}})
        self.assertEqual(self._bot(inventory=rows)["unexpected_owner_count"], 0)

    def test_idempotent_duplicate_delivery_is_not_an_application_failure(self):
        result = self._bot(receiver=self._receiver(duplicate=3))
        self.assertEqual(result["counts"]["duplicate"], 3)

    def test_sequence_gap_fails_closed(self):
        with self.assertRaisesRegex(observer.ObservationError, "local_sequence_gap"):
            self._bot(store=self._store(gap=True))

    def test_tampered_or_non_primary_snapshot_fails_closed(self):
        value = json.loads(self.snapshot.read_text())
        value["feed_mode"] = "PRIVATE_SHADOW"
        self.snapshot.write_text(json.dumps(value))
        with self.assertRaisesRegex(observer.ObservationError, "snapshot_identity_invalid"):
            self._bot()

    def test_web_pending_publication_fails_closed(self):
        receiver = self._receiver(pending=1)
        sequences = {"producer": {"one": 5}, "acknowledged": {"one": 5}}
        counters = {"duplicate": 0, "rejected": 0, "dead_letter": 0, "open_outbox": 0, "receiver_publication_pending": 0}
        with patch.object(observer, "_postgres_evidence", return_value=(sequences, counters)):
            with self.assertRaisesRegex(observer.ObservationError, "nonzero_safety_counter"):
                observer.observe(role="web", release_sha=SHA, release_tree=TREE, project=PROJECT, image_id=IMAGE, snapshot_path=self.snapshot, inventory=[container(s) for s in observer.WEB_SERVICES], health_documents=self._health(observer.WEB_SERVICES), receiver_db=receiver)

    def test_exclusive_artifact_is_0600(self):
        destination = self.root / "evidence" / "observation.json"
        value = self._bot()
        observer._write_exclusive(destination, value)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            observer._write_exclusive(destination, value)

    def test_generated_bot_artifact_is_accepted_by_promotion_verifier(self):
        destination = self.root / "bot-observation.json"
        value = self._bot(now=NOW)
        observer._write_exclusive(destination, value)
        sequences, snapshot, digest = verifier._validate_observation(
            destination,
            role="bot",
            expected_services=verifier.BOT_SERVICES,
            sequence_fields=frozenset({"receiver", "adapter"}),
            release_sha=SHA,
            release_tree=TREE,
            image_id=IMAGE,
            project_name=PROJECT,
            now=NOW,
        )
        self.assertEqual(sequences["receiver"], {"one": 5, "two": 7})
        self.assertEqual(snapshot["snapshot_version"], 9)
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
