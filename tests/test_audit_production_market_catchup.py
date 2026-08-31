from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from core.market_intelligence.external_quote_capture import Quote, quote_event
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_fact_projection import (
    initialize_export_ledger,
    observation_fact_semantics,
)
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)
from core.market_intelligence.private_capture import (
    CaptureEngine,
    CaptureState,
    DurableEventSpool,
    QuarantineEventIdentity,
    utc_text,
)
from core.market_intelligence import private_capture_telegram as telegram_capture
from core.market_intelligence.private_pipeline_contracts import content_hash
from scripts import audit_production_market_catchup as audit
from tests.test_market_pipeline_stage4_capture import audited_resolution_bundle


UTC = timezone.utc
RELEASE = "a" * 40
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _summary(count: int, digest: str = "d" * 64) -> dict[str, object]:
    return {"count": count, "digest": digest}


def _web_artifact() -> dict[str, object]:
    sources: dict[str, object] = {}
    for source in sorted(audit.LIVE_CAPTURE_SOURCES):
        sources[source] = {
            "capture": {
                **_summary(1),
                "head_sequence": 1,
                "last_available_at_utc": audit.CUTOFF_UTC,
                "explicit_backfill_accepted": (
                    1 if source in audit.BACKFILL_SOURCES else 0
                ),
                "terminal_required": (
                    1
                    if source in audit.LIVE_CAPTURE_SOURCES
                    else 0
                ),
                "observed": True,
            },
            "processor": {**_summary(1), "consumed": 1, "head_sequence": 1},
            "parsed": {
                **_summary(1),
                "quality_counts": {},
                "last_available_at_utc": audit.CUTOFF_UTC,
            },
            "archive": _summary(1),
            "revision_history": _summary(1),
            "configured": True,
            "health_observed": True,
        }
        if source in audit.BACKFILL_SOURCES:
            sources[source]["explicit_backfill_lineage"] = {
                **_summary(1),
                "parsed": 1,
                "filtered": 0,
                "dispositions": {"PARSER_EXECUTED": 1},
                "pending": 0,
            }
        if source in audit.LIVE_CAPTURE_SOURCES:
            disposition = (
                "EXTERNAL_MATERIALIZED"
                if source in audit.EXTERNAL_SOURCES
                else "PARSER_EXECUTED"
            )
            sources[source]["terminal_lineage"] = {
                **_summary(1),
                "epoch_utc": audit.CUTOFF_UTC,
                "parsed": 1,
                "filtered": 0,
                "dispositions": {disposition: 1},
                "pending": 0,
            }
    public_binding = {
        "release_sha": RELEASE,
        "feed_mode": "PRIVATE_PRIMARY",
        "backfill_cutoff_utc": audit.CUTOFF_UTC,
        "backfill_sources": sorted(audit.BACKFILL_SOURCES),
        "archive_enabled": True,
    }
    return {
        "schema": audit.WEB_SCHEMA,
        "role": "web",
        "observed_at_utc": audit._stamp(NOW),
        "release_sha": RELEASE,
        "binding": {
            **public_binding,
            "binding_sha256": audit.sha256(audit._canonical(public_binding)).hexdigest(),
        },
        "source_inventory": sorted(audit.LIVE_CAPTURE_SOURCES),
        "backfill": {
            source: {
                "status": "complete",
                "cutoff_utc": audit.CUTOFF_UTC,
                "attempted": 1,
                "accepted": 1,
                "duplicate": 0,
                "quarantined": 0,
                "exhaustion": "source_exhausted",
            }
            for source in sorted(audit.BACKFILL_SOURCES)
        },
        "sources": sources,
        "transport": {"producer_heads": {}, "acknowledged_heads": {}},
        "quarantine": {
            "account1": 0,
            "account2": 0,
            "backfill": 0,
            "processor_rejected": 0,
            "export_rejected": 0,
        },
        "upstream_time_gaps_allowed": True,
        "secrets_disclosed": False,
    }


def _bot_artifact() -> dict[str, object]:
    return {
        "schema": audit.BOT_SCHEMA,
        "role": "bot",
        "observed_at_utc": audit._stamp(NOW),
        "release_sha": RELEASE,
        "source_inventory": sorted(audit.LIVE_CAPTURE_SOURCES),
        "sources": {
            source: {
                "received_facts": _summary(1),
                "revision_history": _summary(1),
                "model_visible": _summary(1),
                "snapshot_input_traced": _summary(1),
                "audit_only": 0,
            }
            for source in sorted(audit.LIVE_CAPTURE_SOURCES)
        },
        "receiver_checkpoints": {},
        "snapshot": {
            "snapshot_id": "c" * 64,
            "snapshot_version": 1,
            "feed_mode": "PRIVATE_PRIMARY",
            "status": "OK",
            "generated_at_utc": audit._stamp(NOW),
            "input_snapshot_hash": "e" * 64,
            "input_lineage": _summary(0),
        },
        "quarantine": {"receiver_rejected": 0, "adapter_rejected": 0},
        "secrets_disclosed": False,
    }


class ProductionMarketCatchupAuditTests(unittest.TestCase):
    def test_settle_live_tail_window_binds_first_pair_and_waits_only_to_floor(
        self,
    ) -> None:
        web = _web_artifact()
        bot = _bot_artifact()
        web_digest = "1" * 64
        bot_digest = "2" * 64
        moments = iter(
            (
                NOW + timedelta(seconds=5),
                NOW + timedelta(seconds=20),
            )
        )
        sleeper = mock.Mock()

        result = audit.settle_live_tail_window(
            previous_web=web,
            previous_bot=bot,
            expected_release_sha=RELEASE,
            previous_web_sha256=web_digest,
            previous_bot_sha256=bot_digest,
            now_fn=lambda: next(moments),
            sleep_fn=sleeper,
        )

        self.assertEqual(result["schema"], audit.SETTLE_SCHEMA)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["minimum_window_seconds"], 20)
        self.assertEqual(result["maximum_window_seconds"], 300)
        self.assertEqual(result["waited_seconds"], 15.0)
        self.assertTrue(result["read_only"])
        self.assertFalse(result["payload_values_included"])
        self.assertEqual(
            result["previous_evidence"]["previous_web"]["sha256"],
            web_digest,
        )
        self.assertEqual(
            result["previous_evidence"]["previous_bot"]["sha256"],
            bot_digest,
        )
        sleeper.assert_called_once_with(15.0)
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("sources", encoded)
        self.assertNotIn("price", encoded.lower())

    def test_settle_live_tail_window_rejects_stale_or_incoherent_pair(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "live_tail_settle_window_expired"
        ):
            audit.settle_live_tail_window(
                previous_web=_web_artifact(),
                previous_bot=_bot_artifact(),
                expected_release_sha=RELEASE,
                previous_web_sha256="1" * 64,
                previous_bot_sha256="2" * 64,
                now_fn=lambda: NOW + timedelta(seconds=301),
                sleep_fn=mock.Mock(),
            )

        bot = _bot_artifact()
        bot["observed_at_utc"] = audit._stamp(NOW + timedelta(seconds=121))
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "live_tail_artifact_time_incoherent"
        ):
            audit.settle_live_tail_window(
                previous_web=_web_artifact(),
                previous_bot=bot,
                expected_release_sha=RELEASE,
                previous_web_sha256="1" * 64,
                previous_bot_sha256="2" * 64,
                now_fn=lambda: NOW,
                sleep_fn=mock.Mock(),
            )

    def test_settle_live_tail_window_rejects_clock_that_did_not_advance(
        self,
    ) -> None:
        moments = iter((NOW + timedelta(seconds=5),) * 2)
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "live_tail_settle_window_invalid"
        ):
            audit.settle_live_tail_window(
                previous_web=_web_artifact(),
                previous_bot=_bot_artifact(),
                expected_release_sha=RELEASE,
                previous_web_sha256="1" * 64,
                previous_bot_sha256="2" * 64,
                now_fn=lambda: next(moments),
                sleep_fn=mock.Mock(),
            )

    def test_resolution_builder_uses_independent_terminal_and_downstream_sets(self):
        source = "MELTED_PRIMARY_FLOW"
        entries = [
            {
                "account": "account1",
                "source_code": source,
                "message_id": index,
                "revision_sha256": f"{index:064x}",
                "event_id": f"mce1_fixture_event_{index:02d}",
                "event_type": "message_snapshot",
                "origin": "explicit_backfill",
                "content_type": "text" if index == 1 else "media_only",
                "event_time_utc": audit.CUTOFF_UTC,
                "available_at_utc": audit.CUTOFF_UTC,
                "capture_status": "accepted",
                "marker_sha256": f"{index + 10:064x}",
            }
            for index in (1, 2)
        ]
        columns = (
            "account",
            "source_code",
            "message_id",
            "revision_sha256",
            "event_id",
            "event_type",
            "origin",
            "content_type",
            "event_time_utc",
            "available_at_utc",
            "capture_status",
            "marker_sha256",
        )
        manifest_document = {
            "schema": audit.REPLAY_MANIFEST_SCHEMA,
            "run_id": "1" * 64,
            "entries": [[entry[column] for column in columns] for entry in entries],
        }
        replay = {
            "run_id": "1" * 64,
            "release_sha": RELEASE,
            "cutoff_utc": audit.CUTOFF_UTC,
            "upper_bound_utc": "2026-08-28T12:00:00.000000Z",
            "source_inventory": [source],
            "manifest_count": 2,
            "manifest_sha256": sha256(
                json.dumps(
                    manifest_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
        status = {
            source: {
                "attempted": 2,
                "accepted": 2,
                "duplicate": 0,
                "quarantined": 0,
                "exhaustion": "cutoff_crossed",
            }
        }
        terminals = {
            source: [
                {
                    "event_id": entries[0]["event_id"],
                    "status": "PARSED",
                    "disposition_code": "PARSER_EXECUTED",
                },
                {
                    "event_id": entries[1]["event_id"],
                    "status": "FILTERED",
                    "disposition_code": "NON_MODEL_MEDIA_ONLY",
                },
            ]
        }
        fact = ("fact-1", source, "payload-hash")
        artifacts = {
            "web_sha256": "a" * 64,
            "bot_sha256": "b" * 64,
            "verification_sha256": "c" * 64,
        }
        kwargs = {
            "account": "account1",
            "replay_run": replay,
            "manifest_entries": entries,
            "backfill_statuses": status,
            "terminal_entries": terminals,
            "archive_rows": {source: [fact]},
            "ack_rows": {source: [fact]},
            "store_rows": {source: [fact]},
            "target_fingerprints": ["d" * 64],
            "artifacts": artifacts,
            "generated_at": NOW,
        }
        evidence = audit.build_quarantine_resolution_evidence(**kwargs)
        self.assertEqual(evidence["sources"][source]["terminal_dispositions"]["filtered"], 1)

        duplicate_terminal = {source: [*terminals[source], terminals[source][0]]}
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "manifest_terminal_mismatch"
        ):
            audit.build_quarantine_resolution_evidence(
                **{**kwargs, "terminal_entries": duplicate_terminal}
            )
        for stage in ("archive_rows", "ack_rows", "store_rows"):
            with self.subTest(stage=stage, defect="missing"):
                with self.assertRaisesRegex(
                    audit.CatchupAuditError, "downstream_mismatch"
                ):
                    audit.build_quarantine_resolution_evidence(
                        **{**kwargs, stage: {source: []}}
                    )
            with self.subTest(stage=stage, defect="extra"):
                with self.assertRaisesRegex(
                    audit.CatchupAuditError, "downstream_mismatch"
                ):
                    audit.build_quarantine_resolution_evidence(
                        **{**kwargs, stage: {source: [fact, ("extra",)]}}
                    )
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "archive_duplicate"
        ):
            audit.build_quarantine_resolution_evidence(
                **{**kwargs, "archive_rows": {source: [fact, fact]}}
            )
        tampered_entries = [dict(entry) for entry in entries]
        tampered_entries[0]["event_id"] = "mce1_tampered_event"
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "manifest_tampered"
        ):
            audit.build_quarantine_resolution_evidence(
                **{**kwargs, "manifest_entries": tampered_entries}
            )

    def test_postgres_revision_history_requires_each_outbox_ack(self) -> None:
        payload = {
            "kind": "COIN_OFFER",
            "group_code": 1,
            "instrument": "COIN_IMAM",
            "side": "SELL",
            "settlement": "CASH",
            "trade_form": "PHYSICAL",
            "offered_price_value": "187500",
            "price_unit": "PROJECT_THOUSAND_TOMAN",
            "quantity_value": "5",
            "quantity_unit": "COIN_COUNT",
        }
        fact_id = "1" * 64
        payload_hash = content_hash(payload)
        event_key = "2" * 64
        envelope_hash = "3" * 64
        stream = "market.fact.coin.group.1"
        current = "\t".join(
            (
                fact_id,
                "GROUP_1",
                "1",
                "1",
                payload_hash,
                event_key,
                "1",
                "ELIGIBLE",
                envelope_hash,
                "1",
            )
        )
        history = "\t".join(
            (
                fact_id,
                "GROUP_1",
                "1",
                "1",
                payload_hash,
                event_key,
                "1",
                "ELIGIBLE",
                envelope_hash,
                "0",
                fact_id,
                "1",
                payload_hash,
                "GROUP_1",
                "1",
                event_key,
                "ELIGIBLE",
                "parser-v1",
                "parser-v1",
                json.dumps(payload, separators=(",", ":")),
            )
        )
        with mock.patch.object(
            audit, "_command", side_effect=(current, history, f"{stream}\t1")
        ):
            with self.assertRaisesRegex(
                audit.CatchupAuditError,
                "postgres_fact_revision_evidence_invalid",
            ):
                audit._postgres_facts(
                    container="postgres",
                    user="market",
                    database="market",
                )

    def test_local_export_audit_rejects_stale_observation_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "market.sqlite"
            market = connect_market_store(path)
            initialize_market_store(market)
            event_key = derive_event_key("catchup-audit", "revision")
            upsert_observation(
                market,
                MarketObservation(
                    event_key=event_key,
                    source_code="GROUP_1",
                    source_family="GROUP",
                    event_time_utc=audit.CUTOFF_UTC,
                    available_at_utc=audit.CUTOFF_UTC,
                    instrument="COIN_IMAM",
                    market_label="GROUP_COIN_IMAM",
                    settlement_term="CASH",
                    trade_form="PHYSICAL",
                    event_type="OFFER",
                    side="SELL",
                    price="187500",
                    price_unit="PROJECT_THOUSAND_TOMAN",
                    currency="TOMAN",
                    quantity="5",
                    quantity_unit="COIN_COUNT",
                    parser_version="catchup-audit-v1",
                ),
            )
            initialize_export_ledger(market)
            row = market.execute(
                "SELECT * FROM market_observations WHERE event_key=?",
                (event_key,),
            ).fetchone()
            assert row is not None
            payload_hash, quality_state, fingerprint = observation_fact_semantics(
                market, row, source_sequence=1
            )
            fact_id = "1" * 64
            envelope_hash = "2" * 64
            inserted = str(row["inserted_at_utc"])
            market.execute(
                "INSERT INTO market_fact_export_ledger VALUES(?,?,?,?,?,?,?,?)",
                (event_key, inserted, "SUCCESS", fact_id, 1, None, 1, inserted),
            )
            market.execute(
                "INSERT INTO market_fact_export_semantics VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_key,
                    inserted,
                    fact_id,
                    1,
                    1,
                    1,
                    payload_hash,
                    quality_state,
                    fingerprint,
                    envelope_hash,
                    inserted,
                ),
            )
            market.execute(
                "INSERT INTO market_fact_export_history VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fact_id,
                    1,
                    event_key,
                    inserted,
                    1,
                    1,
                    payload_hash,
                    quality_state,
                    fingerprint,
                    envelope_hash,
                    inserted,
                ),
            )
            market.commit()
            lineage, revisions, _, _ = audit._local_export_lineage(path)
            self.assertEqual(len(lineage["GROUP_1"]), 1)
            self.assertEqual(revisions["GROUP_1"], lineage["GROUP_1"])

            market.execute(
                "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
                ("2026-08-28T12:00:01.000000Z", event_key),
            )
            market.commit()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "processor_export_ledger_stale"
            ):
                audit._local_export_lineage(path)
            market.close()

    def test_legacy_group_events_without_origin_are_narrowly_classified(self) -> None:
        def document(
            *,
            sequence: int,
            schema_version: str,
            producer_version: str,
            event_type: str,
            is_backfill: bool | None,
        ) -> dict[str, object]:
            message: dict[str, object] = {
                "message_id": str(sequence + 9),
                "published_at_utc": (
                    None if event_type == "message_deleted" else audit.CUTOFF_UTC
                ),
                "edited_at_utc": None,
                "text": None if event_type == "message_deleted" else "خ امام 190000",
                "is_forwarded": False,
            }
            if event_type != "message_deleted":
                sender: dict[str, object] = {
                    "peer_id": "0123456789abcdef",
                    "kind": "user",
                }
                if schema_version == "2.1":
                    sender.update({"telegram_id": "1001", "display_name": None})
                message.update(
                    {
                        "content_type": "text",
                        "sender": sender,
                        "reply": {"status": "not_reply", "message_id": None},
                        "is_backfill": is_backfill,
                    }
                )
            return {
                "schema": "coin_group_event",
                "schema_version": schema_version,
                "event_id": f"cge2_{sequence}" + "a" * 59,
                "event_type": event_type,
                "source": {"market": "coin", "source_id": "GROUP_1"},
                "message": message,
                "producer": {
                    "name": "coin_group_capture",
                    "version": producer_version,
                    "available_at_utc": audit.CUTOFF_UTC,
                    "capture_sequence": sequence,
                },
            }

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            documents = [
                document(
                    sequence=sequence,
                    schema_version=schema_version,
                    producer_version=producer_version,
                    event_type=event_type,
                    is_backfill=is_backfill,
                )
                for sequence, (
                    schema_version,
                    producer_version,
                    event_type,
                    is_backfill,
                ) in enumerate(
                    (
                        ("2.0", "3.0.0-docker", "message_created", False),
                        ("2.0", "3.0.0-docker", "message_created", True),
                        ("2.0", "3.0.0-docker", "message_deleted", None),
                        ("2.1", "3.1.0-docker", "message_created", False),
                        ("2.1", "3.1.0-docker", "message_created", True),
                        ("2.1", "3.1.0-docker", "message_deleted", None),
                    ),
                    start=1,
                )
            ]
            (root / "events-2026-08-28.jsonl").write_text(
                "".join(
                    json.dumps(document, separators=(",", ":")) + "\n"
                    for document in documents
                ),
                encoding="utf-8",
            )
            records = audit._scan_spool(root, stream="coin")
            self.assertEqual(
                [record.origin for record in records],
                ["live", "reconcile", "live", "live", "reconcile", "live"],
            )

        for schema_version, producer_version in (
            ("2.0", "3.1.0-docker"),
            ("2.1", "3.0.0-docker"),
        ):
            with self.subTest(
                unsupported_schema=schema_version,
                unsupported_producer=producer_version,
            ), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                unsupported = document(
                    sequence=1,
                    schema_version=schema_version,
                    producer_version=producer_version,
                    event_type="message_created",
                    is_backfill=False,
                )
                (root / "events-2026-08-28.jsonl").write_text(
                    json.dumps(unsupported, separators=(",", ":")) + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    audit.CatchupAuditError, "capture_spool_origin_invalid"
                ):
                    audit._scan_spool(root, stream="coin")

    def test_collect_web_validates_the_account2_owner_binding(self) -> None:
        def validate_health(_path: Path, **arguments: object) -> dict[str, object]:
            if arguments.get("account") == "account2":
                raise audit.CatchupAuditError("account2_owner_binding_checked")
            expected = arguments["expected_sources"]
            assert isinstance(expected, frozenset)
            return {"sources": {source: {} for source in expected}}

        with mock.patch.object(
            audit, "_parse_runtime_binding", return_value={}
        ), mock.patch.object(audit, "_validate_health", side_effect=validate_health):
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "account2_owner_binding_checked"
            ):
                audit.collect_web(
                    release_sha=RELEASE,
                    runtime_env=Path("runtime.env"),
                    account1_db=Path("account1.sqlite"),
                    account2_db=Path("account2.sqlite"),
                    external_db=Path("external.sqlite"),
                    account1_spool=Path("account1"),
                    account2_spool=Path("account2"),
                    external_spool=Path("external"),
                    processor_staging=Path("staging.sqlite"),
                    processor_market=Path("market.sqlite"),
                    account1_health=Path("account1.json"),
                    account2_health=Path("account2.json"),
                    external_health=Path("external.json"),
                    processor_health=Path("processor.json"),
                    postgres_container="postgres",
                    postgres_user="postgres",
                    postgres_database="app",
                    now=NOW,
                )

    def test_runtime_binding_requires_exact_cutoff_and_source_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "runtime.env"
            path.write_text(
                "\n".join(
                    (
                        f"MARKET_PIPELINE_RELEASE_SHA={RELEASE}",
                        "MARKET_PIPELINE_FEED_MODE=PRIVATE_PRIMARY",
                        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY=1",
                        f"MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC={audit.CUTOFF_UTC}",
                        "MARKET_CAPTURE_BACKFILL_SOURCE_CODES=GROUP_2,MELTED_PRIMARY_FLOW,GROUP_1",
                        "SECRET_VALUE=must-not-be-read-or-emitted",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            binding = audit._parse_runtime_binding(path, release_sha=RELEASE)
            self.assertEqual(binding["backfill_sources"], sorted(audit.BACKFILL_SOURCES))
            self.assertNotIn("SECRET_VALUE", json.dumps(binding))

            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "GROUP_2,MELTED_PRIMARY_FLOW,GROUP_1", "GROUP_1,GROUP_2"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "runtime_env_catchup_binding_invalid"
            ):
                audit._parse_runtime_binding(path, release_sha=RELEASE)

    def test_upstream_sparsity_is_not_an_internal_gap(self) -> None:
        before_web = _web_artifact()
        before_bot = _bot_artifact()
        before_web["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        before_bot["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        result = audit.verify(
            web=_web_artifact(),
            bot=_bot_artifact(),
            expected_release_sha=RELEASE,
            previous_web=before_web,
            previous_bot=before_bot,
            now=NOW,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["live_advanced_sources"], [])
        self.assertTrue(result["upstream_time_gaps_allowed"])
        self.assertEqual(
            set(result["evidence_artifacts"]),
            {"previous_web", "previous_bot", "web", "bot"},
        )
        self.assertEqual(
            result["evidence_binding_sha256"],
            audit.sha256(
                audit._canonical(result["evidence_artifacts"])
            ).hexdigest(),
        )

    def test_old_four_artifact_evidence_cannot_be_replayed(self) -> None:
        previous_web = _web_artifact()
        previous_bot = _bot_artifact()
        previous_web["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        previous_bot["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "catchup_artifact_stale_or_future"
        ):
            audit.verify(
                web=_web_artifact(),
                bot=_bot_artifact(),
                previous_web=previous_web,
                previous_bot=previous_bot,
                expected_release_sha=RELEASE,
                now=NOW + timedelta(seconds=audit.MAX_EVIDENCE_AGE_SECONDS + 1),
            )

    def test_external_source_requires_durable_materialization_terminal_gate(self) -> None:
        web = _web_artifact()
        del web["sources"]["WALLEX_PUBLIC_API"]["terminal_lineage"]
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "catchup_terminal_lineage_invalid"
        ):
            audit._validate_artifact(web, schema=audit.WEB_SCHEMA, role="web")

    def test_configured_but_zero_source_evidence_is_blocked(self) -> None:
        for source in sorted(audit.LIVE_CAPTURE_SOURCES):
            with self.subTest(source=source):
                web = _web_artifact()
                web["sources"][source]["capture"].update(_summary(0))
                web["sources"][source]["capture"]["head_sequence"] = 0
                web["sources"][source]["processor"].update(_summary(0))
                web["sources"][source]["processor"]["consumed"] = 0
                web["sources"][source]["processor"]["head_sequence"] = 0
                with self.assertRaisesRegex(
                    audit.CatchupAuditError, "catchup_source_evidence_invalid"
                ):
                    audit._validate_artifact(
                        web, schema=audit.WEB_SCHEMA, role="web"
                    )
        for source in sorted(audit.LIVE_CAPTURE_SOURCES):
            for field in ("model_visible", "snapshot_input_traced"):
                with self.subTest(bot_source=source, field=field):
                    bot = _bot_artifact()
                    bot["sources"][source][field] = _summary(0)
                    with self.assertRaisesRegex(
                        audit.CatchupAuditError, "catchup_source_evidence_invalid"
                    ):
                        audit._validate_artifact(
                            bot, schema=audit.BOT_SCHEMA, role="bot"
                        )

    def test_lineage_disposition_classes_must_match_status_counts(self) -> None:
        web = _web_artifact()
        terminal = web["sources"]["GROUP_1"]["terminal_lineage"]
        terminal["dispositions"] = {"NON_MODEL_SERVICE": 1}
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "catchup_terminal_lineage_invalid"
        ):
            audit._validate_artifact(web, schema=audit.WEB_SCHEMA, role="web")

        web = _web_artifact()
        explicit = web["sources"]["GROUP_1"]["explicit_backfill_lineage"]
        explicit["dispositions"] = {"DELETE_APPLIED": 1}
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "catchup_backfill_evidence_invalid"
        ):
            audit._validate_artifact(web, schema=audit.WEB_SCHEMA, role="web")

    def test_backfill_requires_nonzero_attempt_and_durable_explicit_lineage(self) -> None:
        for source in sorted(audit.BACKFILL_SOURCES):
            with self.subTest(source=source):
                web = _web_artifact()
                web["backfill"][source]["attempted"] = 0
                web["backfill"][source]["accepted"] = 0
                web["sources"][source]["capture"]["explicit_backfill_accepted"] = 0
                web["sources"][source]["explicit_backfill_lineage"].update(
                    _summary(0)
                )
                web["sources"][source]["explicit_backfill_lineage"]["parsed"] = 0
                with self.assertRaisesRegex(
                    audit.CatchupAuditError, "catchup_backfill_evidence_invalid"
                ):
                    audit._validate_artifact(
                        web, schema=audit.WEB_SCHEMA, role="web"
                    )

    def test_clean_retry_may_be_duplicate_only_when_durable_lineage_exists(self) -> None:
        web = _web_artifact()
        for source in sorted(audit.BACKFILL_SOURCES):
            web["backfill"][source]["accepted"] = 0
            web["backfill"][source]["duplicate"] = 1
        audit._validate_artifact(web, schema=audit.WEB_SCHEMA, role="web")

    def test_live_tail_advances_only_sources_that_actually_received_events(self) -> None:
        before_web = _web_artifact()
        before_bot = _bot_artifact()
        before_web["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        before_bot["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        after_web = _web_artifact()
        after_bot = _bot_artifact()
        source = "GROUP_2"
        after_web["sources"][source]["capture"].update(_summary(2, "1" * 64))
        after_web["sources"][source]["capture"]["head_sequence"] = 2
        after_web["sources"][source]["capture"]["observed"] = True
        after_web["sources"][source]["processor"].update(_summary(2, "2" * 64))
        after_web["sources"][source]["processor"]["consumed"] = 2
        after_web["sources"][source]["processor"]["head_sequence"] = 2
        after_web["sources"][source]["parsed"].update(_summary(2, "3" * 64))
        after_web["sources"][source]["archive"] = _summary(2, "4" * 64)
        after_web["sources"][source]["revision_history"] = _summary(
            2, "6" * 64
        )
        after_bot["sources"][source]["received_facts"] = _summary(2, "4" * 64)
        after_bot["sources"][source]["revision_history"] = _summary(
            2, "6" * 64
        )
        after_bot["sources"][source]["model_visible"] = _summary(2, "5" * 64)
        result = audit.verify(
            web=after_web,
            bot=after_bot,
            previous_web=before_web,
            previous_bot=before_bot,
            expected_release_sha=RELEASE,
            now=NOW,
        )
        self.assertEqual(result["live_advanced_sources"], [source])
        self.assertEqual(result["live_parser_output_advanced_sources"], [source])

    def test_live_capture_without_processor_progress_is_blocked(self) -> None:
        before_web = _web_artifact()
        before_bot = _bot_artifact()
        before_web["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        before_bot["observed_at_utc"] = audit._stamp(NOW - timedelta(seconds=30))
        after_web = _web_artifact()
        after_bot = _bot_artifact()
        after_web["sources"]["GROUP_1"]["capture"].update(_summary(2, "1" * 64))
        after_web["sources"]["GROUP_1"]["capture"]["head_sequence"] = 2
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "catchup_source_evidence_invalid"
        ):
            audit.verify(
                web=after_web,
                bot=after_bot,
                previous_web=before_web,
                previous_bot=before_bot,
                expected_release_sha=RELEASE,
                now=NOW,
            )

    def test_web_bot_fact_digest_mismatch_is_blocked(self) -> None:
        web = _web_artifact()
        bot = _bot_artifact()
        web["sources"]["GROUP_1"]["parsed"].update(_summary(1, "1" * 64))
        web["sources"]["GROUP_1"]["archive"] = _summary(1, "2" * 64)
        bot["sources"]["GROUP_1"]["received_facts"] = _summary(1, "3" * 64)
        bot["sources"]["GROUP_1"]["model_visible"] = _summary(1, "4" * 64)
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "web_bot_fact_lineage_mismatch"
        ):
            audit.verify(
                web=web,
                bot=bot,
                expected_release_sha=RELEASE,
                now=NOW,
            )

    def test_capture_prefix_comes_from_durable_seen_not_the_first_spool_row(self) -> None:
        common = {
            "stream": "external",
            "source": "WALLEX_PUBLIC_API",
            "event_id": "f" * 64,
            "event_time_utc": audit.CUTOFF_UTC,
            "available_at_utc": audit.CUTOFF_UTC,
            "explicit_backfill": False,
            "message_id": None,
            "event_type": None,
            "file_name": "events-2026-08-25.jsonl",
            "device": 1,
            "inode": 2,
            "end_offset": 10,
            "external_event_key": "e" * 64,
        }
        allowed = [
            audit.SpoolRecord(sequence=7, **common),
            audit.SpoolRecord(sequence=8, **common),
        ]
        audit._verify_capture_sequences(
            allowed,
            head=8,
            seen={("f" * 64, 7), ("f" * 64, 8)},
            external=True,
        )
        truncated = [
            audit.SpoolRecord(sequence=sequence, **common)
            for sequence in range(50, 101)
        ]
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "capture_durable_prefix_mismatch"
        ):
            audit._verify_capture_sequences(
                truncated,
                head=100,
                seen={("f" * 64, sequence) for sequence in range(1, 101)},
                external=True,
            )
        gap = [
            audit.SpoolRecord(sequence=7, **common),
            audit.SpoolRecord(sequence=9, **common),
        ]
        with self.assertRaisesRegex(
            audit.CatchupAuditError, "internal_capture_sequence_gap"
        ):
            audit._verify_capture_sequences(
                gap,
                head=9,
                seen={("f" * 64, 7), ("f" * 64, 9)},
                external=True,
            )

    def test_spool_scan_uses_real_external_contract_without_exposing_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            quote = Quote(
                source_code="WALLEX_PUBLIC_API",
                instrument="USDT_IRT",
                quote_kind="MID",
                price_value="12345",
                price_unit="TOMAN_PER_USDT",
                currency="TOMAN",
                observed_at_utc="2026-08-28T11:59:58Z",
                available_at_utc="2026-08-28T11:59:59Z",
                provenance={"method": "fixture"},
            )
            document = quote_event(quote)
            document["producer"]["capture_sequence"] = 1
            path = root / "events-2026-08-28.jsonl"
            path.write_text(json.dumps(document) + "\n", encoding="utf-8")
            records = audit._scan_spool(root, stream="external")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].source, "WALLEX_PUBLIC_API")
            self.assertNotIn("12345", repr(records[0]))

    def test_receiver_lineage_survives_payload_compaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receiver = sqlite3.connect(Path(temporary) / "receiver.sqlite")
            receiver.row_factory = sqlite3.Row
            receiver.executescript(
                """
                CREATE TABLE fact_latest(
                  fact_id TEXT,stream_id TEXT,source_sequence INTEGER,
                  fact_revision INTEGER,payload_hash TEXT,payload_json TEXT,
                  payload_compacted_at_utc TEXT
                );
                CREATE TABLE fact_deliveries(
                  fact_id TEXT,fact_revision INTEGER,delivery_sequence INTEGER,
                  payload_hash TEXT,payload_json TEXT,
                  payload_compacted_at_utc TEXT
                );
                """
            )
            fact_id = "1" * 64
            payload_hash = "2" * 64
            stream = "market.fact.coin.group.1"
            receiver.execute(
                "INSERT INTO fact_latest VALUES(?,?,?,?,?,?,?)",
                (fact_id, stream, 7, 2, payload_hash, "", audit.CUTOFF_UTC),
            )
            receiver.execute(
                "INSERT INTO fact_deliveries VALUES(?,?,?,?,?,?)",
                (fact_id, 2, 11, payload_hash, "", audit.CUTOFF_UTC),
            )
            projection_db = sqlite3.connect(Path(temporary) / "projection.sqlite")
            projection_db.row_factory = sqlite3.Row
            projection_db.execute(
                """
                CREATE TABLE p(
                  fact_id TEXT,stream_id TEXT,source_sequence INTEGER,
                  fact_revision INTEGER,payload_hash TEXT,event_key BLOB,
                  occurred_at_utc TEXT,available_at_utc TEXT,
                  quality_state TEXT,envelope_hash TEXT
                )
                """
            )
            projection_db.execute(
                "INSERT INTO p VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    fact_id,
                    stream,
                    7,
                    2,
                    payload_hash,
                    bytes.fromhex("3" * 64),
                    audit.CUTOFF_UTC,
                    audit.CUTOFF_UTC,
                    "ELIGIBLE",
                    "4" * 64,
                ),
            )
            row = projection_db.execute("SELECT * FROM p").fetchone()
            assert row is not None
            facts = audit._receiver_fact_rows(receiver, {fact_id: row})
            self.assertEqual(len(facts["GROUP_1"]), 1)
            receiver.close()
            projection_db.close()

    def test_backfill_quarantine_is_not_promotable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.sqlite"
            state = CaptureState(path, account="account2")
            cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
            try:
                state.begin_backfill("GROUP_1", cutoff, now=NOW)
                state.note_backfill_outcome("GROUP_1", cutoff, "quarantined", now=NOW)
                state.mark_backfill_complete(
                    "GROUP_1",
                    cutoff,
                    expected_attempted=1,
                    exhaustion="cutoff_crossed",
                    now=NOW,
                )
            finally:
                state.close()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "capture_backfill_incomplete_or_unaccounted"
            ):
                audit._capture_state(
                    path,
                    expected_account="account2",
                    expected_sources=audit.ACCOUNT2_SOURCES,
                )

    def test_cutoff_quarantine_cannot_be_hidden_by_a_clean_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "capture.sqlite"
            state = CaptureState(path, account="account2")
            cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
            try:
                state.note_quarantine(
                    b"pre-retry-cutoff-record",
                    "capture_message_text_required",
                    source_code="GROUP_1",
                    now=NOW - timedelta(days=1),
                )
                for source in sorted(audit.ACCOUNT2_SOURCES):
                    state.begin_backfill(source, cutoff, now=NOW)
                    state.note_backfill_outcome(source, cutoff, "accepted", now=NOW)
                    state.mark_backfill_complete(
                        source,
                        cutoff,
                        expected_attempted=1,
                        exhaustion="cutoff_crossed",
                        now=NOW,
                    )
                state.connection.execute(
                    "UPDATE capture_source_metrics SET quarantined=7"
                )
                state.connection.commit()
            finally:
                state.close()

            with self.assertRaisesRegex(
                audit.CatchupAuditError, "capture_quarantine_unresolved"
            ):
                audit._capture_state(
                    path,
                    expected_account="account2",
                    expected_sources=audit.ACCOUNT2_SOURCES,
                )

    def test_exact_resolution_is_audited_and_recurrence_reopens_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "capture.sqlite"
            state = CaptureState(path, account="account1")
            spool = DurableEventSpool(root / "spool", account="account1")
            engine = CaptureEngine(state, spool)
            cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
            moment = cutoff + timedelta(minutes=1)
            snapshot = telegram_capture.TelegramMessageSnapshot(
                message_id=77,
                published_at=moment,
                edited_at=None,
                text="95,000,000 فروش",
                has_media=False,
                media_type=None,
                action_type=None,
                entities=(),
                reply_to_message_id=None,
                reply_to_top_id=None,
                grouped_id=None,
                sender_id=7001,
                sender_kind="user",
                sender_display_name="Fixture",
                is_forwarded=False,
                via_bot=False,
                post=False,
                silent=False,
                pinned=False,
                noforwards=False,
                is_forum=False,
            )
            revision = telegram_capture._revision(snapshot)
            target = QuarantineEventIdentity(
                account="account1",
                source_code="MELTED_PRIMARY_FLOW",
                message_id=77,
                revision_sha256=revision,
                event_type="message_snapshot",
                origin="live",
            )
            state.note_event_quarantine(
                target, "CAPTURE_MESSAGE_TEXT_INVALID", now=moment
            )
            state.note_quarantine(
                b"telegram-created-event",
                "CAPTURE_MESSAGE_TEXT_REQUIRED",
                source_code="USD_HERAT",
                now=moment,
            )
            document = telegram_capture.build_market_event(
                telegram_capture.SOURCE_POLICIES["MELTED_PRIMARY_FLOW"],
                snapshot,
                event_type="message_snapshot",
                received_at=moment + timedelta(seconds=1),
                backfill=True,
                explicit_backfill=True,
            )
            accepted = engine.accept(document, now=moment + timedelta(seconds=1))
            run_id = state.begin_replay_run(
                cutoff=cutoff,
                upper_bound=moment + timedelta(minutes=1),
                source_codes=audit.ACCOUNT1_SOURCES,
                release_sha=RELEASE,
                now=moment,
            )
            for source in sorted(audit.ACCOUNT1_SOURCES):
                state.begin_backfill(source, cutoff, now=moment)
                if source == "MELTED_PRIMARY_FLOW":
                    state.note_backfill_outcome(
                        source, cutoff, "accepted", now=moment
                    )
                state.mark_backfill_complete(
                    source,
                    cutoff,
                    expected_attempted=(
                        1 if source == "MELTED_PRIMARY_FLOW" else 0
                    ),
                    exhaustion="source_exhausted",
                    now=moment,
                )
            replay_identity = QuarantineEventIdentity(
                account="account1",
                source_code="MELTED_PRIMARY_FLOW",
                message_id=77,
                revision_sha256=revision,
                event_type="message_snapshot",
                origin="explicit_backfill",
            )
            available = state.event_available_at(accepted.event_id)
            assert available is not None
            state.record_replay_manifest_entry(
                run_id=run_id,
                identity=replay_identity,
                event_id=accepted.event_id,
                content_type="text",
                event_time_utc=utc_text(moment),
                available_at_utc=available,
                capture_status="accepted",
            )
            count, digest = state.complete_replay_run(run_id, now=moment)
            legacy = state.connection.execute(
                "SELECT * FROM capture_quarantine"
            ).fetchone()
            self.assertIsNotNone(legacy)
            evidence_path, evidence_digest = audited_resolution_bundle(
                state, root, run_id, generated_at=moment
            )
            state.apply_quarantine_resolution_evidence(
                evidence_path,
                expected_sha256=evidence_digest,
                now=moment,
            )
            self.assertFalse(
                any(
                    audit._capture_quarantine_state(
                        state.connection,
                        expected_account="account1",
                        expected_sources=audit.ACCOUNT1_SOURCES,
                    ).values()
                )
            )

            state.note_event_quarantine(
                target,
                "CAPTURE_MESSAGE_TEXT_INVALID",
                now=moment + timedelta(minutes=2),
            )
            reopened = audit._capture_quarantine_state(
                state.connection,
                expected_account="account1",
                expected_sources=audit.ACCOUNT1_SOURCES,
            )
            self.assertEqual(reopened["MELTED_PRIMARY_FLOW"], 2)

            state.connection.execute("DROP TRIGGER capture_replay_manifest_no_update")
            state.connection.execute(
                "UPDATE capture_replay_manifest_entries SET marker_sha256=?",
                ("f" * 64,),
            )
            state.connection.commit()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "capture_replay_manifest_tampered"
            ):
                audit._capture_quarantine_state(
                    state.connection,
                    expected_account="account1",
                    expected_sources=audit.ACCOUNT1_SOURCES,
                )
            state.close()

    def test_explicit_backfill_requires_terminal_parser_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spool = root / "events-2026-08-28.jsonl"
            spool.write_text("{}\n", encoding="utf-8")
            info = spool.stat()
            staging_path = root / "staging.sqlite"
            staging = sqlite3.connect(staging_path)
            staging.executescript(
                """
                CREATE TABLE capture_file_cursors(
                  stream TEXT,file_path TEXT,device INTEGER,inode INTEGER,
                  byte_offset INTEGER,updated_at_utc TEXT
                );
                CREATE TABLE capture_rejected_records(
                  record_sha256 TEXT,stream TEXT,reason TEXT,
                  first_seen_at_utc TEXT,last_seen_at_utc TEXT,
                  occurrences INTEGER,expires_at_utc TEXT
                );
                CREATE TABLE capture_seen_events(
                  event_id TEXT,stream TEXT,available_at_utc TEXT,expires_at_utc TEXT
                );
                CREATE TABLE capture_dirty_market_messages(message_id INTEGER);
                CREATE TABLE capture_dirty_groups(group_number INTEGER);
                CREATE TABLE capture_projection_reconciliations(
                  reconciliation_code TEXT,completed_at_utc TEXT
                );
                CREATE TABLE capture_event_lineage_control(
                  singleton INTEGER,enabled_at_utc TEXT
                );
                CREATE TABLE capture_event_lineage(
                  event_id TEXT,stream TEXT,source_id TEXT,message_id INTEGER,
                  event_type TEXT,origin TEXT,event_time_utc TEXT,
                  available_at_utc TEXT,status TEXT,disposition_code TEXT,
                  terminal_at_utc TEXT
                );
                CREATE TABLE capture_explicit_backfill_lineage(
                  event_id TEXT,stream TEXT,source_id TEXT,message_id INTEGER,
                  event_type TEXT,event_time_utc TEXT,available_at_utc TEXT,
                  status TEXT,disposition_code TEXT,terminal_at_utc TEXT,
                  expires_at_utc TEXT
                );
                """
            )
            event_id = "event-explicit-backfill-0001"
            staging.execute(
                "INSERT INTO capture_file_cursors VALUES(?,?,?,?,?,?)",
                ("coin", str(spool), info.st_dev, info.st_ino, info.st_size, audit.CUTOFF_UTC),
            )
            staging.execute(
                "INSERT INTO capture_seen_events VALUES(?,?,?,?)",
                (event_id, "coin", audit.CUTOFF_UTC, "2026-09-01T00:00:00Z"),
            )
            staging.execute(
                "INSERT INTO capture_event_lineage_control VALUES(1,?)",
                (audit.CUTOFF_UTC,),
            )
            staging.execute(
                "INSERT INTO capture_event_lineage VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    "coin",
                    "GROUP_1",
                    1,
                    "message_created",
                    "explicit_backfill",
                    audit.CUTOFF_UTC,
                    audit.CUTOFF_UTC,
                    "PARSED",
                    "PARSER_EXECUTED",
                    audit.CUTOFF_UTC,
                ),
            )
            staging.execute(
                "INSERT INTO capture_explicit_backfill_lineage VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    "coin",
                    "GROUP_1",
                    1,
                    "message_created",
                    audit.CUTOFF_UTC,
                    audit.CUTOFF_UTC,
                    "PARSED",
                    "PARSER_EXECUTED",
                    audit.CUTOFF_UTC,
                    "2026-09-01T00:00:00Z",
                ),
            )
            staging.commit()
            staging.close()
            market_path = root / "market.sqlite"
            market = sqlite3.connect(market_path)
            market.executescript(
                """
                CREATE TABLE market_observations(event_key BLOB);
                CREATE TABLE market_observations_archive(event_key BLOB);
                """
            )
            market.commit()
            market.close()
            record = audit.SpoolRecord(
                stream="coin",
                source="GROUP_1",
                sequence=1,
                event_id=event_id,
                event_time_utc=audit.CUTOFF_UTC,
                available_at_utc=audit.CUTOFF_UTC,
                explicit_backfill=True,
                message_id=1,
                event_type="message_created",
                file_name=spool.name,
                device=info.st_dev,
                inode=info.st_ino,
                end_offset=info.st_size,
                origin="explicit_backfill",
            )
            consumed, lineage, terminal = audit._processor_consumption(
                staging_path, market_path, (record,)
            )
            self.assertEqual(consumed["GROUP_1"], {event_id})
            self.assertEqual(lineage["GROUP_1"]["parsed"], 1)
            self.assertEqual(terminal["GROUP_1"]["parsed"], 1)

            tolerated_future = audit._stamp(NOW + timedelta(seconds=5))
            rejected_future = audit._stamp(NOW + timedelta(seconds=6))
            staging = sqlite3.connect(staging_path)
            staging.execute(
                "UPDATE capture_event_lineage SET terminal_at_utc=?",
                (tolerated_future,),
            )
            staging.execute(
                "UPDATE capture_explicit_backfill_lineage SET terminal_at_utc=?",
                (tolerated_future,),
            )
            staging.commit()
            staging.close()
            audit._processor_consumption(
                staging_path, market_path, (record,), observed_at=NOW
            )

            staging = sqlite3.connect(staging_path)
            staging.execute(
                "UPDATE capture_event_lineage SET terminal_at_utc=?",
                (rejected_future,),
            )
            staging.commit()
            staging.close()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "capture_event_lineage_terminal_future"
            ):
                audit._processor_consumption(
                    staging_path, market_path, (record,), observed_at=NOW
                )

            staging = sqlite3.connect(staging_path)
            staging.execute(
                "UPDATE capture_event_lineage SET terminal_at_utc=?",
                (audit.CUTOFF_UTC,),
            )
            staging.execute(
                "UPDATE capture_explicit_backfill_lineage SET terminal_at_utc=?",
                (rejected_future,),
            )
            staging.commit()
            staging.close()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "explicit_backfill_terminal_future"
            ):
                audit._processor_consumption(
                    staging_path, market_path, (record,), observed_at=NOW
                )

            staging = sqlite3.connect(staging_path)
            staging.execute(
                "UPDATE capture_explicit_backfill_lineage SET terminal_at_utc=?",
                (audit.CUTOFF_UTC,),
            )
            staging.commit()
            staging.close()

            staging = sqlite3.connect(staging_path)
            staging.execute(
                "UPDATE capture_event_lineage SET status='FILTERED',"
                "disposition_code='UNRECOGNIZED_TERMINAL',terminal_at_utc=?",
                (audit.CUTOFF_UTC,),
            )
            staging.commit()
            staging.close()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "capture_event_lineage_not_terminal"
            ):
                audit._processor_consumption(staging_path, market_path, (record,))

            staging = sqlite3.connect(staging_path)
            staging.execute(
                "UPDATE capture_event_lineage SET status='PARSED',"
                "disposition_code='PARSER_EXECUTED',terminal_at_utc=?",
                (audit.CUTOFF_UTC,),
            )
            staging.commit()
            staging.close()

            staging = sqlite3.connect(staging_path)
            staging.execute("INSERT INTO capture_dirty_groups VALUES(1)")
            staging.commit()
            staging.close()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "processor_projection_pending"
            ):
                audit._processor_consumption(staging_path, market_path, (record,))

            staging = sqlite3.connect(staging_path)
            staging.execute("DELETE FROM capture_dirty_groups")
            staging.commit()
            staging.close()

            staging = sqlite3.connect(staging_path)
            staging.execute(
                "UPDATE capture_explicit_backfill_lineage SET status='PENDING',"
                "disposition_code='AWAITING_PARSER',terminal_at_utc=NULL"
            )
            staging.commit()
            staging.close()
            with self.assertRaisesRegex(
                audit.CatchupAuditError, "explicit_backfill_lineage_not_terminal"
            ):
                audit._processor_consumption(staging_path, market_path, (record,))


if __name__ == "__main__":
    unittest.main()
