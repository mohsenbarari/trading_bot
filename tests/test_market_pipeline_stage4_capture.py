"""Stage 4 durable capture, retention, and authority-gate tests."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from core.market_intelligence import private_capture as capture
from core.market_intelligence import private_capture_service as capture_service
from core.market_intelligence.capture_event_adapter import (
    CaptureEventContractError,
    decode_coin_group_event,
    decode_market_channel_event,
    initialize_capture_adapter,
    project_capture_changes,
    purge_capture_staging,
    stage_capture_event,
)
from core.market_intelligence.coin_group_staging import connect_coin_group_staging
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
)
from core.market_intelligence.private_capture_telegram import (
    AUTHORITY_MARKER_CONTRACT,
    SOURCE_POLICIES,
    CaptureBinding,
    TelegramCaptureConfig,
    TelegramMessageSnapshot,
    build_deleted_event,
    build_group_event,
    build_market_event,
    validate_authority_marker,
)
from core.market_intelligence import private_capture_telegram as telegram_capture
from scripts import audit_production_market_catchup as catchup_audit


UTC = timezone.utc


class ReplayManifestDigestTests(unittest.TestCase):
    def test_streaming_summary_matches_existing_canonical_contract(self):
        run_id = "a" * 64
        rows = iter(
            (
                (
                    "account1",
                    "MELTED_PRIMARY_FLOW",
                    7,
                    "b" * 64,
                    "event-1",
                    "message_snapshot",
                    "explicit_backfill",
                    "text",
                    "2026-09-03T08:00:00.000000Z",
                    "2026-09-03T08:00:01.000000Z",
                    "accepted",
                    "c" * 64,
                ),
                (
                    "account1",
                    "USD_HERAT",
                    8,
                    "d" * 64,
                    "event-2",
                    "message_snapshot",
                    "explicit_backfill",
                    "text",
                    None,
                    "2026-09-03T08:00:02.000000Z",
                    "duplicate",
                    "e" * 64,
                ),
            )
        )
        expected_rows = [
            [
                "account1",
                "MELTED_PRIMARY_FLOW",
                7,
                "b" * 64,
                "event-1",
                "message_snapshot",
                "explicit_backfill",
                "text",
                "2026-09-03T08:00:00.000000Z",
                "2026-09-03T08:00:01.000000Z",
                "accepted",
                "c" * 64,
            ],
            [
                "account1",
                "USD_HERAT",
                8,
                "d" * 64,
                "event-2",
                "message_snapshot",
                "explicit_backfill",
                "text",
                None,
                "2026-09-03T08:00:02.000000Z",
                "duplicate",
                "e" * 64,
            ],
        ]
        expected = sha256(
            capture.canonical_json(
                {
                    "schema": capture.REPLAY_MANIFEST_SCHEMA,
                    "run_id": run_id,
                    "entries": expected_rows,
                }
            )
        ).hexdigest()

        self.assertEqual(
            capture._replay_manifest_summary(rows, run_id=run_id),
            (2, expected),
        )


def audited_resolution_bundle(
    state: capture.CaptureState,
    root: Path,
    run_id: str,
    *,
    generated_at: datetime,
) -> tuple[Path, str]:
    """Build a value-free fixture from independent terminal/fact row sets."""

    run = state.connection.execute(
        "SELECT * FROM capture_replay_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    assert run is not None
    inventory = json.loads(str(run["source_inventory_json"]))
    statuses: dict[str, dict[str, object]] = {}
    terminal_entries: dict[str, list[dict[str, object]]] = {}
    archive_rows: dict[str, list[tuple[str, str]]] = {}
    for source in inventory:
        event_ids = [
            str(row[0])
            for row in state.connection.execute(
                "SELECT event_id FROM capture_replay_manifest_entries "
                "WHERE run_id=? AND source_code=? ORDER BY event_id",
                (run_id, source),
            )
        ]
        manifest_count, manifest_digest = capture.value_free_set_digest(
            (event_id,) for event_id in event_ids
        )
        terminal_rows = [
            (event_id, "PARSED", "PARSER_EXECUTED") for event_id in event_ids
        ]
        terminal_count, terminal_digest = capture.value_free_set_digest(terminal_rows)
        fact_rows = [(f"fact:{event_id}", source) for event_id in event_ids]
        fact_count, fact_digest = capture.value_free_set_digest(fact_rows)
        status = state.backfill_status(source)
        assert status is not None
        statuses[source] = {
                "attempted": int(status["attempted"]),
                "accepted": int(status["accepted"]),
                "duplicate": int(status["duplicate"]),
                "quarantined": int(status["quarantined"]),
                "exhaustion": str(status["exhaustion"]),
        }
        terminal_entries[source] = [
            {
                "event_id": event_id,
                "status": "PARSED",
                "disposition_code": "PARSER_EXECUTED",
            }
            for event_id in event_ids
        ]
        archive_rows[source] = fact_rows
    targets: list[str] = []
    for row in state.connection.execute("SELECT * FROM capture_quarantine"):
        targets.append(
            capture.quarantine_row_fingerprint(
                account=state.account,
                kind="legacy",
                marker_sha256=str(row["record_sha256"]),
                reason_code=str(row["reason_code"]),
                occurrences=int(row["occurrences"]),
                last_seen_at_utc=str(row["last_seen_at_utc"]),
            )
        )
    for row in state.connection.execute("SELECT * FROM capture_event_quarantine"):
        targets.append(
            capture.quarantine_row_fingerprint(
                account=state.account,
                kind="event",
                marker_sha256=str(row["marker_sha256"]),
                reason_code=str(row["reason_code"]),
                occurrences=int(row["occurrences"]),
                last_seen_at_utc=str(row["last_seen_at_utc"]),
                source_code=str(row["source_code"]),
                message_id=int(row["message_id"]),
                revision_sha256=str(row["revision_sha256"]),
            )
        )
    replay = {
            "run_id": run_id,
            "release_sha": str(run["release_sha"]),
            "cutoff_utc": str(run["cutoff_utc"]),
            "upper_bound_utc": str(run["upper_bound_utc"]),
            "source_inventory": inventory,
            "manifest_count": int(run["manifest_count"]),
            "manifest_sha256": str(run["manifest_sha256"]),
    }
    artifacts = {
            "web_sha256": sha256(b"independent-web-artifact").hexdigest(),
            "bot_sha256": sha256(b"independent-bot-artifact").hexdigest(),
            "verification_sha256": sha256(
                b"independent-verification-artifact"
            ).hexdigest(),
    }
    document = catchup_audit.build_quarantine_resolution_evidence(
        account=state.account,
        replay_run=replay,
        manifest_entries=[
            dict(row)
            for row in state.connection.execute(
                "SELECT account,source_code,message_id,revision_sha256,event_id,"
                "event_type,origin,content_type,event_time_utc,available_at_utc,"
                "capture_status,marker_sha256 FROM capture_replay_manifest_entries "
                "WHERE run_id=?",
                (run_id,),
            )
        ],
        backfill_statuses=statuses,
        terminal_entries=terminal_entries,
        archive_rows=archive_rows,
        ack_rows=archive_rows,
        store_rows=archive_rows,
        target_fingerprints=targets,
        artifacts=artifacts,
        generated_at=generated_at,
    )
    raw = json.dumps(
        document, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    path = root / f"resolution-{run_id}.json"
    path.write_bytes(raw)
    return path, sha256(raw).hexdigest()


def snapshot(
    message_id: int,
    *,
    published: datetime,
    text: str = "fixture market event",
    edited: datetime | None = None,
    reply_to: int | None = None,
    sender_id: int | None = 7001,
) -> TelegramMessageSnapshot:
    return TelegramMessageSnapshot(
        message_id=message_id,
        published_at=published,
        edited_at=edited,
        text=text,
        has_media=False,
        media_type=None,
        action_type=None,
        entities=(),
        reply_to_message_id=reply_to,
        reply_to_top_id=None,
        grouped_id=None,
        sender_id=sender_id,
        sender_kind="user" if sender_id is not None else "unknown",
        sender_display_name=("Test User" if sender_id is not None else None),
        is_forwarded=False,
        via_bot=False,
        post=False,
        silent=False,
        pinned=False,
        noforwards=False,
        is_forum=False,
    )


def market_document(
    source: str,
    message_id: int,
    *,
    published: datetime,
    received: datetime,
    event_type: str = "message_created",
    backfill: bool = False,
    text: str = "95,000,000 فروش",
) -> dict[str, object]:
    return build_market_event(
        SOURCE_POLICIES[source],
        snapshot(message_id, published=published, text=text),
        event_type=event_type,  # type: ignore[arg-type]
        received_at=received,
        backfill=backfill,
    )


class CaptureFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = capture.CaptureState(
            self.root / "state/capture.sqlite", account="account1"
        )
        self.spool = capture.DurableEventSpool(
            self.root / "capture", account="account1"
        )
        self.engine = capture.CaptureEngine(self.state, self.spool)

    def tearDown(self) -> None:
        self.state.close()
        self.temp.cleanup()

    def test_exact_catchup_runtime_settings_are_fail_closed(self):
        required = "MELTED_PRIMARY_FLOW,GROUP_1,GROUP_2"
        with patch.dict(
            os.environ,
            {
                "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC": "2026-08-25T09:33:00Z",
                "MARKET_CAPTURE_BACKFILL_MAX_MESSAGES": "1000000",
                "MARKET_CAPTURE_BACKFILL_SOURCE_CODES": required,
            },
            clear=False,
        ):
            cutoff, maximum, sources = capture_service._backfill_settings()
        self.assertEqual(cutoff, datetime(2026, 8, 25, 9, 33, tzinfo=UTC))
        self.assertEqual(maximum, 1_000_000)
        self.assertEqual(sources, telegram_capture.EXACT_CATCHUP_SOURCES)

        with patch.dict(
            os.environ,
            {
                "MARKET_CAPTURE_BACKFILL_NOT_BEFORE_UTC": "2026-08-25T09:33:00Z",
                "MARKET_CAPTURE_BACKFILL_SOURCE_CODES": "GROUP_1,GROUP_2",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                capture.CaptureRuntimeError, "backfill_source_codes_mismatch"
            ):
                capture_service._backfill_settings()

    def test_legacy_quarantine_expands_replay_to_complete_account_inventory(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        self.state.note_quarantine(
            b"telegram-reconcile-event",
            "CAPTURE_MESSAGE_TEXT_INVALID",
            source_code="MELTED_PRIMARY_FLOW",
            now=cutoff + timedelta(minutes=1),
        )
        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        self.assertEqual(
            provider.backfill_source_codes,
            capture.ACCOUNT_SOURCES["account1"],
        )

    def test_global_sequence_duplicate_delivery_and_first_receipt_are_stable(self):
        first_at = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        first = market_document(
            "XAUUSD", 1, published=first_at, received=first_at + timedelta(seconds=1)
        )
        second = market_document(
            "USD_HERAT",
            2,
            published=first_at + timedelta(seconds=2),
            received=first_at + timedelta(seconds=3),
        )
        replay = market_document(
            "XAUUSD",
            1,
            published=first_at,
            received=first_at + timedelta(minutes=5),
            event_type="message_snapshot",
            backfill=True,
        )
        self.assertEqual(self.engine.accept(first).sequence, 1)
        self.assertEqual(self.engine.accept(second).sequence, 2)
        duplicate = self.engine.accept(replay)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(duplicate.sequence, 1)
        rows = [
            json.loads(raw)
            for raw in next((self.root / "capture").glob("events-*.jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [row["producer"]["capture_sequence"] for row in rows], [1, 2]
        )
        self.assertEqual(
            rows[0]["producer"]["available_at_utc"], "2026-08-26T10:00:01.000000Z"
        )

    def test_event_quarantine_identity_prevents_cross_source_collision(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        revision = "b" * 64
        for source, message_id in (("XAUUSD", 10), ("USD_HERAT", 10)):
            self.state.note_event_quarantine(
                capture.QuarantineEventIdentity(
                    account="account1",
                    source_code=source,
                    message_id=message_id,
                    revision_sha256=revision,
                    event_type="message_snapshot",
                    origin="reconcile",
                ),
                "CAPTURE_MESSAGE_TEXT_INVALID",
                now=moment,
            )
        rows = self.state.connection.execute(
            "SELECT source_code,message_id,revision_sha256,occurrences "
            "FROM capture_event_quarantine ORDER BY source_code"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [
                ("USD_HERAT", 10, revision, 1),
                ("XAUUSD", 10, revision, 1),
            ],
        )

    def test_replay_run_resumes_fixed_upper_bound_after_interruption(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        first_upper = cutoff + timedelta(hours=1)
        run_id = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=first_upper,
            source_codes={"MELTED_PRIMARY_FLOW"},
            release_sha="a" * 40,
            now=cutoff,
        )
        resumed = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=cutoff + timedelta(days=1),
            source_codes={"MELTED_PRIMARY_FLOW"},
            release_sha="a" * 40,
            now=cutoff + timedelta(minutes=5),
        )
        self.assertEqual(resumed, run_id)
        self.assertEqual(self.state.replay_run_upper_bound(run_id), first_upper)
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "capture_replay_run_append_only"
        ):
            self.state.connection.execute(
                "DELETE FROM capture_replay_runs WHERE run_id=?", (run_id,)
            )

    def test_replay_completion_accepts_only_empty_retry_after_durable_manifest(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        moment = cutoff + timedelta(minutes=1)
        source = "MELTED_PRIMARY_FLOW"
        message = snapshot(91, published=moment, text="95,000,000 فروش")
        document = telegram_capture.build_market_event(
            SOURCE_POLICIES[source],
            message,
            event_type="message_snapshot",
            received_at=moment,
            backfill=True,
            explicit_backfill=True,
        )
        accepted = self.engine.accept(document, now=moment)
        run_id = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=moment + timedelta(minutes=1),
            source_codes={source},
            release_sha="a" * 40,
            now=moment,
        )
        identity = capture.QuarantineEventIdentity(
            account="account1",
            source_code=source,
            message_id=91,
            revision_sha256=telegram_capture._revision(message),
            event_type="message_snapshot",
            origin="explicit_backfill",
        )
        available = self.state.event_available_at(accepted.event_id)
        self.assertIsNotNone(available)
        self.state.record_replay_manifest_entry(
            run_id=run_id,
            identity=identity,
            event_id=accepted.event_id,
            content_type="text",
            event_time_utc=capture.utc_text(moment),
            available_at_utc=str(available),
            capture_status="accepted",
        )

        # A first empty attempt cannot certify a non-empty run manifest.
        self.state.begin_backfill(source, cutoff, now=moment)
        self.state.mark_backfill_complete(
            source,
            cutoff,
            expected_attempted=0,
            exhaustion="cutoff_crossed",
            now=moment,
        )
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "capture_replay_source_incomplete"
        ):
            self.state.complete_replay_run(run_id, now=moment)

        # On a later retry, the immutable durable manifest is retained while
        # the empty global counters no longer poison run completion.
        self.state.begin_backfill(source, cutoff, now=moment)
        self.state.mark_backfill_complete(
            source,
            cutoff,
            expected_attempted=0,
            exhaustion="cutoff_crossed",
            now=moment,
        )
        count, digest = self.state.complete_replay_run(run_id, now=moment)
        self.assertEqual(count, 1)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_exact_resolution_is_idempotent_and_new_occurrence_reopens_it(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        moment = cutoff + timedelta(minutes=1)
        source = "MELTED_PRIMARY_FLOW"
        message = snapshot(77, published=moment, text="95,000,000 فروش")
        revision = telegram_capture._revision(message)
        quarantined = capture.QuarantineEventIdentity(
            account="account1",
            source_code=source,
            message_id=77,
            revision_sha256=revision,
            event_type="message_snapshot",
            origin="live",
        )
        self.state.note_event_quarantine(
            quarantined, "CAPTURE_MESSAGE_TEXT_INVALID", now=moment
        )
        document = telegram_capture.build_market_event(
            SOURCE_POLICIES[source],
            message,
            event_type="message_snapshot",
            received_at=moment + timedelta(seconds=1),
            backfill=True,
            explicit_backfill=True,
        )
        accepted = self.engine.accept(document, now=moment + timedelta(seconds=1))
        run_id = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=moment + timedelta(minutes=1),
            source_codes={source},
            release_sha="a" * 40,
            now=moment,
        )
        self.state.begin_backfill(source, cutoff, now=moment)
        self.state.note_backfill_outcome(source, cutoff, "accepted", now=moment)
        self.state.mark_backfill_complete(
            source,
            cutoff,
            expected_attempted=1,
            exhaustion="cutoff_crossed",
            now=moment,
        )
        replay_identity = capture.QuarantineEventIdentity(
            account="account1",
            source_code=source,
            message_id=77,
            revision_sha256=revision,
            event_type="message_snapshot",
            origin="explicit_backfill",
        )
        available = self.state.event_available_at(accepted.event_id)
        self.assertIsNotNone(available)
        self.state.record_replay_manifest_entry(
            run_id=run_id,
            identity=replay_identity,
            event_id=accepted.event_id,
            content_type="text",
            event_time_utc=capture.utc_text(moment),
            available_at_utc=str(available),
            capture_status="accepted",
        )
        # A post-durable crash makes the retry report ``duplicate``; the
        # immutable manifest identity must remain idempotent.
        self.state.record_replay_manifest_entry(
            run_id=run_id,
            identity=replay_identity,
            event_id=accepted.event_id,
            content_type="text",
            event_time_utc=capture.utc_text(moment),
            available_at_utc=str(available),
            capture_status="duplicate",
        )
        manifest_columns = {
            str(row["name"])
            for row in self.state.connection.execute(
                "PRAGMA table_info(capture_replay_manifest_entries)"
            )
        }
        self.assertFalse(
            {"text", "payload", "sender_id", "sender_name"}.intersection(
                manifest_columns
            )
        )
        count, digest = self.state.complete_replay_run(run_id, now=moment)
        evidence_path, evidence_digest = audited_resolution_bundle(
            self.state, self.root, run_id, generated_at=moment
        )
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "resolution_evidence_required"
        ):
            self.state.resolve_replayed_quarantines(run_id, now=moment)

        base_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

        def variant(label: str, document: dict[str, object]) -> tuple[Path, str]:
            raw = json.dumps(
                document,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
            path = self.root / f"{label}.json"
            path.write_bytes(raw)
            return path, sha256(raw).hexdigest()

        missing = json.loads(json.dumps(base_evidence))
        missing["targets"] = []
        path, digest_value = variant("missing-target", missing)
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "target_set_mismatch"
        ):
            self.state.apply_quarantine_resolution_evidence(
                path, expected_sha256=digest_value, now=moment
            )
        extra = json.loads(json.dumps(base_evidence))
        extra["targets"].append("f" * 64)
        path, digest_value = variant("extra-target", extra)
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "target_set_mismatch"
        ):
            self.state.apply_quarantine_resolution_evidence(
                path, expected_sha256=digest_value, now=moment
            )
        duplicate = json.loads(json.dumps(base_evidence))
        duplicate["targets"].append(duplicate["targets"][0])
        path, digest_value = variant("duplicate-target", duplicate)
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "target_set_mismatch"
        ):
            self.state.apply_quarantine_resolution_evidence(
                path, expected_sha256=digest_value, now=moment
            )
        self_asserted = json.loads(json.dumps(base_evidence))
        self_asserted["sources"][source]["terminal_identity"]["digest"] = digest
        path, digest_value = variant("manifest-self-assertion", self_asserted)
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "manifest_terminal_mismatch"
        ):
            self.state.apply_quarantine_resolution_evidence(
                path, expected_sha256=digest_value, now=moment
            )
        tampered = json.loads(json.dumps(base_evidence))
        tampered["sources"][source]["store"]["digest"] = "e" * 64
        path, digest_value = variant("store-tamper", tampered)
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "downstream_mismatch"
        ):
            self.state.apply_quarantine_resolution_evidence(
                path, expected_sha256=digest_value, now=moment
            )
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "evidence_digest_mismatch"
        ):
            self.state.apply_quarantine_resolution_evidence(
                evidence_path, expected_sha256="0" * 64, now=moment
            )
        first = self.state.apply_quarantine_resolution_evidence(
            evidence_path, expected_sha256=evidence_digest, now=moment
        )
        second = self.state.apply_quarantine_resolution_evidence(
            evidence_path,
            expected_sha256=evidence_digest,
            now=moment + timedelta(seconds=1),
        )
        self.assertEqual(first["resolved"], 1)
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(
            self.state.connection.execute(
                "SELECT COUNT(*) FROM capture_quarantine_resolutions"
            ).fetchone()[0],
            1,
        )
        self.state.note_event_quarantine(
            quarantined,
            "CAPTURE_MESSAGE_TEXT_INVALID",
            now=moment + timedelta(minutes=2),
        )
        row = self.state.connection.execute(
            "SELECT occurrences,last_seen_at_utc FROM capture_event_quarantine"
        ).fetchone()
        current = capture.quarantine_row_fingerprint(
            account="account1",
            kind="event",
            marker_sha256=quarantined.marker_sha256,
            reason_code="CAPTURE_MESSAGE_TEXT_INVALID",
            occurrences=int(row["occurrences"]),
            last_seen_at_utc=str(row["last_seen_at_utc"]),
            source_code=source,
            message_id=77,
            revision_sha256=revision,
        )
        stored = self.state.connection.execute(
            "SELECT quarantine_fingerprint FROM capture_quarantine_resolutions"
        ).fetchone()[0]
        self.assertNotEqual(current, stored)

        # The same release must be able to create a later fixed-bound replay
        # generation after the recurrence.  Reusing the completed first run
        # would leave this exact fingerprint permanently unresolved.
        recurrence = moment + timedelta(minutes=2)
        second_run = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=recurrence + timedelta(minutes=1),
            source_codes={source},
            release_sha="a" * 40,
            now=recurrence,
        )
        self.assertNotEqual(second_run, run_id)
        self.state.begin_backfill(source, cutoff, now=recurrence)
        self.state.note_backfill_outcome(
            source, cutoff, "duplicate", now=recurrence
        )
        self.state.mark_backfill_complete(
            source,
            cutoff,
            expected_attempted=1,
            exhaustion="cutoff_crossed",
            now=recurrence,
        )
        self.state.record_replay_manifest_entry(
            run_id=second_run,
            identity=replay_identity,
            event_id=accepted.event_id,
            content_type="text",
            event_time_utc=capture.utc_text(moment),
            available_at_utc=str(available),
            capture_status="duplicate",
        )
        self.state.complete_replay_run(second_run, now=recurrence)
        second_path, second_digest = audited_resolution_bundle(
            self.state, self.root, second_run, generated_at=recurrence
        )
        self.assertEqual(
            self.state.apply_quarantine_resolution_evidence(
                second_path, expected_sha256=second_digest, now=recurrence
            ),
            {"resolved": 1, "inserted": 1, "unresolved": 0},
        )
        fingerprints = {
            str(row[0])
            for row in self.state.connection.execute(
                "SELECT quarantine_fingerprint "
                "FROM capture_quarantine_resolutions"
            )
        }
        self.assertEqual(len(fingerprints), 2)
        self.assertIn(current, fingerprints)

    def test_legacy_quarantine_requires_bounded_full_account_replay(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        cutoff = moment - timedelta(hours=1)
        self.state.note_quarantine(
            b"telegram-created-event",
            "CAPTURE_MESSAGE_TEXT_INVALID",
            source_code="MELTED_PRIMARY_FLOW",
            now=moment,
        )
        row = self.state.connection.execute(
            "SELECT * FROM capture_quarantine"
        ).fetchone()
        event_snapshot = snapshot(
            88, published=moment, text="95,000,000 فروش"
        )
        document = telegram_capture.build_market_event(
            SOURCE_POLICIES["MELTED_PRIMARY_FLOW"],
            event_snapshot,
            event_type="message_snapshot",
            received_at=moment + timedelta(seconds=1),
            backfill=True,
            explicit_backfill=True,
        )
        accepted = self.engine.accept(document, now=moment + timedelta(seconds=1))
        replay_identity = capture.QuarantineEventIdentity(
            account="account1",
            source_code="MELTED_PRIMARY_FLOW",
            message_id=88,
            revision_sha256=telegram_capture._revision(event_snapshot),
            event_type="message_snapshot",
            origin="explicit_backfill",
        )
        partial_run = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=moment + timedelta(minutes=1),
            source_codes={"MELTED_PRIMARY_FLOW"},
            release_sha="a" * 40,
            now=moment,
        )
        self.state.begin_backfill("MELTED_PRIMARY_FLOW", cutoff, now=moment)
        self.state.note_backfill_outcome(
            "MELTED_PRIMARY_FLOW", cutoff, "accepted", now=moment
        )
        self.state.mark_backfill_complete(
            "MELTED_PRIMARY_FLOW",
            cutoff,
            expected_attempted=1,
            exhaustion="cutoff_crossed",
            now=moment,
        )
        available = self.state.event_available_at(accepted.event_id)
        assert available is not None
        self.state.record_replay_manifest_entry(
            run_id=partial_run,
            identity=replay_identity,
            event_id=accepted.event_id,
            content_type="text",
            event_time_utc=capture.utc_text(moment),
            available_at_utc=available,
            capture_status="accepted",
        )
        partial_count, partial_digest = self.state.complete_replay_run(
            partial_run, now=moment
        )
        partial_path, partial_evidence_digest = audited_resolution_bundle(
            self.state, self.root, partial_run, generated_at=moment
        )
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError,
            "account_replay_required",
        ):
            self.state.apply_quarantine_resolution_evidence(
                partial_path,
                expected_sha256=partial_evidence_digest,
                now=moment,
            )

        full_run = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=moment + timedelta(minutes=1),
            source_codes=capture.ACCOUNT_SOURCES["account1"],
            release_sha="b" * 40,
            now=moment,
        )
        for source in sorted(capture.ACCOUNT_SOURCES["account1"]):
            self.state.begin_backfill(source, cutoff, now=moment)
            if source == "MELTED_PRIMARY_FLOW":
                self.state.note_backfill_outcome(
                    source, cutoff, "duplicate", now=moment
                )
            self.state.mark_backfill_complete(
                source,
                cutoff,
                expected_attempted=(1 if source == "MELTED_PRIMARY_FLOW" else 0),
                exhaustion="source_exhausted",
                now=moment,
            )
        self.state.record_replay_manifest_entry(
            run_id=full_run,
            identity=replay_identity,
            event_id=accepted.event_id,
            content_type="text",
            event_time_utc=capture.utc_text(moment),
            available_at_utc=available,
            capture_status="duplicate",
        )
        full_count, full_digest = self.state.complete_replay_run(
            full_run, now=moment
        )
        self.assertEqual(full_count, 1)
        self.assertRegex(full_digest, r"^[0-9a-f]{64}$")
        full_path, full_evidence_digest = audited_resolution_bundle(
            self.state, self.root, full_run, generated_at=moment
        )
        resolution_report = self.state.apply_quarantine_resolution_evidence(
            full_path, expected_sha256=full_evidence_digest, now=moment
        )
        self.assertEqual(
            resolution_report, {"resolved": 1, "inserted": 1, "unresolved": 0}
        )
        resolution = self.state.connection.execute(
            "SELECT resolution_id FROM capture_quarantine_resolutions "
            "WHERE quarantine_kind='legacy'"
        ).fetchone()[0]
        self.assertRegex(resolution, r"^[0-9a-f]{64}$")
        self.state.note_quarantine(
            b"telegram-created-event",
            "CAPTURE_MESSAGE_TEXT_INVALID",
            source_code="USD_HERAT",
            now=moment + timedelta(minutes=2),
        )
        current = self.state.connection.execute(
            "SELECT * FROM capture_quarantine"
        ).fetchone()
        reopened = capture.quarantine_row_fingerprint(
            account="account1",
            kind="legacy",
            marker_sha256=str(current["record_sha256"]),
            reason_code=str(current["reason_code"]),
            occurrences=int(current["occurrences"]),
            last_seen_at_utc=str(current["last_seen_at_utc"]),
        )
        stored = self.state.connection.execute(
            "SELECT quarantine_fingerprint FROM capture_quarantine_resolutions "
            "WHERE quarantine_kind='legacy'"
        ).fetchone()[0]
        self.assertNotEqual(reopened, stored)

    def test_489_legacy_occurrences_accept_exhaustive_full_account_proof(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        moment = cutoff + timedelta(minutes=30)
        for _ in range(1, 490):
            self.state.note_quarantine(
                b"one-repeated-legacy-record",
                "CAPTURE_MESSAGE_TEXT_INVALID",
                source_code="MELTED_PRIMARY_FLOW",
                now=moment,
            )
        legacy = self.state.connection.execute(
            "SELECT occurrences FROM capture_quarantine"
        ).fetchone()
        self.assertEqual(int(legacy["occurrences"]), 489)
        run_id = self.state.begin_replay_run(
            cutoff=cutoff,
            upper_bound=moment + timedelta(minutes=1),
            source_codes=capture.ACCOUNT_SOURCES["account1"],
            release_sha="c" * 40,
            now=moment,
        )
        for source in sorted(capture.ACCOUNT_SOURCES["account1"]):
            self.state.begin_backfill(source, cutoff, now=moment)
        # ``occurrences`` is a recurrence counter for one legacy marker, not
        # an oracle for the number of unique Telegram events.  Five unique
        # events are sufficient here to prove that an exhaustive, gap-free
        # full-account replay is accepted even though the same marker was
        # observed 489 times.
        replay_message_ids = range(1, 6)
        for message_id in replay_message_ids:
            message = snapshot(
                message_id,
                published=moment,
                text=f"95,000,000 فروش {message_id}",
            )
            document = telegram_capture.build_market_event(
                SOURCE_POLICIES["MELTED_PRIMARY_FLOW"],
                message,
                event_type="message_snapshot",
                received_at=moment + timedelta(seconds=1),
                backfill=True,
                explicit_backfill=True,
            )
            accepted = self.engine.accept(
                document, now=moment + timedelta(seconds=1)
            )
            self.state.note_backfill_outcome(
                "MELTED_PRIMARY_FLOW", cutoff, "accepted", now=moment
            )
            available = self.state.event_available_at(accepted.event_id)
            assert available is not None
            self.state.record_replay_manifest_entry(
                run_id=run_id,
                identity=capture.QuarantineEventIdentity(
                    account="account1",
                    source_code="MELTED_PRIMARY_FLOW",
                    message_id=message_id,
                    revision_sha256=telegram_capture._revision(message),
                    event_type="message_snapshot",
                    origin="explicit_backfill",
                ),
                event_id=accepted.event_id,
                content_type="text",
                event_time_utc=capture.utc_text(moment),
                available_at_utc=available,
                capture_status="accepted",
            )
        for source in sorted(capture.ACCOUNT_SOURCES["account1"]):
            self.state.mark_backfill_complete(
                source,
                cutoff,
                expected_attempted=(5 if source == "MELTED_PRIMARY_FLOW" else 0),
                exhaustion="source_exhausted",
                now=moment,
            )
        count, _ = self.state.complete_replay_run(run_id, now=moment)
        self.assertEqual(count, 5)
        evidence_path, evidence_digest = audited_resolution_bundle(
            self.state, self.root, run_id, generated_at=moment
        )
        self.assertEqual(
            self.state.apply_quarantine_resolution_evidence(
                evidence_path, expected_sha256=evidence_digest, now=moment
            ),
            {"resolved": 1, "inserted": 1, "unresolved": 0},
        )
        self.assertEqual(
            self.state.connection.execute(
                "SELECT COUNT(*) FROM capture_quarantine_resolutions"
            ).fetchone()[0],
            1,
        )

    def test_reconciliation_terminals_media_only_message_without_quarantine(self):
        now = telegram_capture.utc_now()
        valid = SimpleNamespace(
            id=102,
            date=now - timedelta(seconds=1),
            edit_date=None,
            message="2,350.50",
        )
        media_only = SimpleNamespace(
            id=101,
            date=now - timedelta(seconds=2),
            edit_date=None,
            message="",
            media=SimpleNamespace(),
        )

        class FakeClient:
            async def iter_messages(self, _entity, *, limit):
                self.limit = limit
                for item in (valid, media_only):
                    yield item

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
        )
        provider._entity_by_source["XAUUSD"] = SimpleNamespace(forum=False)
        client = FakeClient()
        asyncio.run(provider._reconcile_source(client, SOURCE_POLICIES["XAUUSD"]))

        self.assertEqual(
            self.state.connection.execute(
                "SELECT COUNT(*) FROM capture_seen"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            self.state.connection.execute(
                "SELECT COUNT(*) FROM capture_quarantine"
            ).fetchone()[0],
            0,
        )

    def test_reconciliation_stops_after_durable_watermark_overlap(self):
        now = telegram_capture.utc_now()
        seed = market_document(
            "XAUUSD",
            1000,
            published=now - timedelta(seconds=2),
            received=now - timedelta(seconds=1),
        )
        self.engine.accept(seed)
        self.assertEqual(self.state.highest_message_id("XAUUSD"), 1000)

        class FakeClient:
            yielded = 0

            async def iter_messages(self, _entity, *, limit):
                self.limit = limit
                for message_id in range(1001, 0, -1):
                    self.yielded += 1
                    yield SimpleNamespace(
                        id=message_id,
                        date=now - timedelta(seconds=1),
                        edit_date=None,
                        message="2,350.50",
                    )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
        )
        provider._entity_by_source["XAUUSD"] = SimpleNamespace(forum=False)
        client = FakeClient()
        asyncio.run(provider._reconcile_source(client, SOURCE_POLICIES["XAUUSD"]))

        self.assertEqual(client.yielded, 201)
        self.assertFalse(provider.reconciliation_truncated)
        self.assertEqual(self.state.highest_message_id("XAUUSD"), 1001)

    def test_explicit_backfill_reaches_owner_cutoff_despite_newer_watermark(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        now = cutoff + timedelta(days=3)
        self.engine.accept(
            market_document(
                "MELTED_PRIMARY_FLOW",
                1000,
                published=now,
                received=now + timedelta(seconds=1),
            )
        )

        class FakeClient:
            calls = 0

            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                self.calls += 1
                self.limit = limit
                self.reverse = reverse
                self.offset_date = offset_date
                for message_id, published in (
                    (1001, cutoff + timedelta(seconds=1)),
                    (1002, now),
                ):
                    yield SimpleNamespace(
                        id=message_id,
                        date=published,
                        edit_date=None,
                        message="2,350.50",
                    )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )
        client = FakeClient()
        asyncio.run(
            provider._backfill_source_to_cutoff(
                client, SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
            )
        )
        self.assertTrue(self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff))
        self.assertEqual(self.state.highest_message_id("MELTED_PRIMARY_FLOW"), 1002)
        self.assertTrue(
            self.state.has_message("MELTED_PRIMARY_FLOW", 1001),
            "the record below the existing high watermark must be recovered",
        )
        asyncio.run(
            provider._backfill_source_to_cutoff(
                client, SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
            )
        )
        self.assertEqual(client.calls, 1)
        self.assertTrue(client.reverse)
        self.assertEqual(client.offset_date, cutoff - timedelta(microseconds=1))
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertIsNotNone(status)
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["attempted"], 2)
        self.assertEqual(status["accepted"], 2)
        self.assertEqual(status["duplicate"], 0)
        self.assertEqual(status["quarantined"], 0)
        self.assertEqual(status["exhaustion"], "cutoff_crossed")
        heartbeat = self.state.heartbeat(
            role="market-capture-account1",
            release_sha="a" * 40,
            mode="live",
            started_at_utc=capture.utc_text(cutoff),
            last_durable_append=self.spool.last_durable_append,
        )
        backfill = heartbeat["sources"]["MELTED_PRIMARY_FLOW"]["explicit_backfill"]
        self.assertEqual(backfill, status)
        self.assertFalse(
            {"message_id", "text", "payload", "sender_id"}.intersection(backfill)
        )

    def test_explicit_backfill_limit_failure_never_marks_coverage(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        now = cutoff + timedelta(days=1)

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                self.assert_reverse = reverse
                self.offset_date = offset_date
                for message_id in range(limit):
                    yield SimpleNamespace(
                        id=message_id + 1,
                        date=now,
                        edit_date=None,
                        message="2,350.50",
                    )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "backfill_limit_exceeded"
        ):
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    FakeClient(), SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
                )
            )
        self.assertFalse(
            self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff)
        )
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["attempted"], 2_000)
        self.assertEqual(status["accepted"], 2_000)

    def test_explicit_replay_refuses_revision_newer_than_fixed_upper_bound(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        upper = cutoff + timedelta(minutes=5)
        message = SimpleNamespace(
            id=99,
            date=cutoff + timedelta(minutes=1),
            edit_date=upper + timedelta(seconds=1),
            message="95,000,000 فروش",
        )

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                yield message

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_upper_bound=upper,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )
        asyncio.run(
            provider._backfill_source_to_cutoff(
                FakeClient(), SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
            )
        )
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertEqual(status["quarantined"], 1)
        self.assertEqual(
            self.state.connection.execute(
                "SELECT reason_code FROM capture_event_quarantine"
            ).fetchone()[0],
            "CAPTURE_REPLAY_POINT_IN_TIME_REVISION_UNAVAILABLE",
        )
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "capture_replay_source_incomplete"
        ):
            self.state.complete_replay_run(str(provider._replay_run_id))

    def test_explicit_backfill_rejects_naive_cutoff_everywhere(self):
        cutoff = datetime(2026, 8, 25, 9, 33)
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "backfill_cutoff_timezone_required"
        ):
            self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff)
        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "backfill_not_before_timezone_required"
        ):
            telegram_capture.TelegramCaptureProvider(
                config,
                self.engine,
                session_path=self.root / "account1.session",
                hmac_key=None,
                stop=threading.Event(),
                backfill_not_before=cutoff,
                backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
                release_sha="a" * 40,
            )

    def test_explicit_backfill_retries_after_transport_interruption(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        now = cutoff + timedelta(hours=1)

        class FakeClient:
            calls = 0

            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                self.calls += 1
                yield SimpleNamespace(
                    id=1, date=cutoff, edit_date=None, message="2,349.50"
                )
                if self.calls == 1:
                    raise ConnectionError("fixture_transport_interrupted")
                yield SimpleNamespace(
                    id=2, date=now, edit_date=None, message="2,350.50"
                )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )
        client = FakeClient()
        with self.assertRaisesRegex(ConnectionError, "transport_interrupted"):
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    client, SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
                )
            )
        self.assertFalse(
            self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff)
        )
        asyncio.run(
            provider._backfill_source_to_cutoff(
                client, SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
            )
        )
        self.assertTrue(self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff))
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertEqual(status["run_attempts"], 2)
        self.assertEqual(status["attempted"], 2)
        self.assertEqual(status["accepted"], 1)
        self.assertEqual(status["duplicate"], 1)
        self.assertEqual(status["exhaustion"], "cutoff_crossed")

    def test_explicit_backfill_includes_exact_cutoff_across_many_results(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        total = 205

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                self.limit = limit
                self.reverse = reverse
                self.offset_date = offset_date
                for offset in range(total):
                    yield SimpleNamespace(
                        id=offset + 1,
                        date=cutoff + timedelta(seconds=offset),
                        edit_date=None,
                        message="2,350.50",
                    )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )
        asyncio.run(
            provider._backfill_source_to_cutoff(
                FakeClient(), SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
            )
        )
        self.assertTrue(self.state.has_message("MELTED_PRIMARY_FLOW", 1))
        self.assertFalse(self.state.has_message("MELTED_PRIMARY_FLOW", 999))
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertEqual(status["attempted"], total)
        self.assertEqual(status["accepted"], total)
        self.assertEqual(status["exhaustion"], "cutoff_crossed")
        self.assertEqual(self.state.sequence(), total)
        rows = [
            json.loads(raw)
            for raw in next((self.root / "capture").glob("events-*.jsonl"))
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(
            [row["producer"]["capture_sequence"] for row in rows],
            list(range(1, total + 1)),
        )

    def test_explicit_backfill_restart_deduplicates_durable_partial_work(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                for message_id in (1, 2):
                    yield SimpleNamespace(
                        id=message_id,
                        date=cutoff + timedelta(seconds=message_id - 1),
                        edit_date=None,
                        message="2,350.50",
                    )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )
        original_capture = provider._capture_message
        accepted_then_interrupted = False

        async def interrupt_after_first(*args, **kwargs):
            nonlocal accepted_then_interrupted
            result = await original_capture(*args, **kwargs)
            if not accepted_then_interrupted:
                accepted_then_interrupted = True
                raise ConnectionError("fixture_after_durable_accept")
            return result

        provider._capture_message = interrupt_after_first  # type: ignore[method-assign]
        with self.assertRaisesRegex(ConnectionError, "after_durable_accept"):
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    FakeClient(), SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
                )
            )
        self.assertTrue(self.state.has_message("MELTED_PRIMARY_FLOW", 1))
        self.assertFalse(
            self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff)
        )

        provider._capture_message = original_capture  # type: ignore[method-assign]
        asyncio.run(
            provider._backfill_source_to_cutoff(
                FakeClient(), SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
            )
        )
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertEqual(status["run_attempts"], 2)
        self.assertEqual(status["attempted"], 2)
        self.assertEqual(status["accepted"], 1)
        self.assertEqual(status["duplicate"], 1)
        self.assertTrue(self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff))

    def test_explicit_backfill_systemic_capture_error_is_not_hidden(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                yield SimpleNamespace(
                    id=1,
                    date=cutoff,
                    edit_date=None,
                    message="2,350.50",
                )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )

        async def identity_conflict(*_args, **_kwargs):
            raise capture.CaptureRuntimeError("capture_event_identity_conflict")

        provider._capture_message = identity_conflict  # type: ignore[method-assign]
        with self.assertRaisesRegex(
            capture.CaptureRuntimeError, "capture_event_identity_conflict"
        ):
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    FakeClient(), SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
                )
            )
        self.assertFalse(
            self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff)
        )
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["attempted"], 0)
        self.assertEqual(
            self.state.connection.execute(
                "SELECT COUNT(*) FROM capture_quarantine"
            ).fetchone()[0],
            0,
        )

    def test_explicit_backfill_terminals_non_model_media_without_quarantine(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)

        class FakeClient:
            corrected = False

            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                yield SimpleNamespace(
                    id=1,
                    date=cutoff,
                    edit_date=None,
                    message="2,349.50" if self.corrected else "",
                    media=None if self.corrected else SimpleNamespace(),
                )
                yield SimpleNamespace(
                    id=2,
                    date=cutoff + timedelta(seconds=1),
                    edit_date=None,
                    message="2,350.50",
                )

        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
            backfill_not_before=cutoff,
            backfill_max_messages=2_000,
            backfill_source_codes=frozenset({"MELTED_PRIMARY_FLOW"}),
            release_sha="a" * 40,
        )
        provider._entity_by_source["MELTED_PRIMARY_FLOW"] = SimpleNamespace(
            forum=False
        )
        client = FakeClient()
        asyncio.run(
            provider._backfill_source_to_cutoff(
                client, SOURCE_POLICIES["MELTED_PRIMARY_FLOW"]
            )
        )
        status = self.state.backfill_status("MELTED_PRIMARY_FLOW")
        self.assertEqual(status["status"], "complete")
        self.assertEqual(status["attempted"], 2)
        self.assertEqual(status["accepted"], 2)
        self.assertEqual(status["quarantined"], 0)
        self.assertTrue(self.state.backfill_covers("MELTED_PRIMARY_FLOW", cutoff))

    def test_explicit_group_backfill_accounts_reply_ancestor_before_cutoff(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        parent = SimpleNamespace(
            id=10,
            date=cutoff - timedelta(minutes=1),
            edit_date=None,
            message="ف 10 امام 190000",
            sender_id=7001,
            reply_to=None,
        )
        child = SimpleNamespace(
            id=11,
            date=cutoff,
            edit_date=None,
            message="خ 10",
            sender_id=7002,
            reply_to=SimpleNamespace(reply_to_msg_id=10, reply_to_top_id=None),
        )

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                yield child

            async def get_messages(self, _entity, *, ids):
                return parent if ids == 10 else None

        state = capture.CaptureState(
            self.root / "group-state/capture.sqlite", account="account2"
        )
        spool = capture.DurableEventSpool(
            self.root / "group-capture", account="account2"
        )
        try:
            engine = capture.CaptureEngine(state, spool)
            config = TelegramCaptureConfig(
                contract="market_telegram_capture_config/1.0",
                account="account2",
                api_id=1,
                api_hash="a" * 32,
                session_filename="account2.session",
                sources=tuple(
                    CaptureBinding(source_code=source, peer_id=-(index + 1))
                    for index, source in enumerate(
                        sorted(capture.ACCOUNT_SOURCES["account2"])
                    )
                ),
            )
            provider = telegram_capture.TelegramCaptureProvider(
                config,
                engine,
                session_path=self.root / "account2.session",
                hmac_key=b"k" * 32,
                stop=threading.Event(),
                backfill_not_before=cutoff,
                backfill_max_messages=2_000,
                backfill_source_codes=frozenset({"GROUP_1"}),
                release_sha="a" * 40,
            )
            provider._entity_by_source["GROUP_1"] = SimpleNamespace(
                forum=False, id=-1
            )
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    FakeClient(), SOURCE_POLICIES["GROUP_1"]
                )
            )
            status = state.backfill_status("GROUP_1")
            self.assertEqual(status["attempted"], 1)
            self.assertEqual(status["accepted"], 1)
            self.assertEqual(
                state.replay_source_manifest_count(
                    provider._ensure_replay_run(), "GROUP_1"
                ),
                1,
            )
            self.assertTrue(state.has_message("GROUP_1", 10))
            self.assertTrue(state.has_message("GROUP_1", 11))
            self.assertTrue(state.backfill_covers("GROUP_1", cutoff))
        finally:
            state.close()

    def test_invalid_reply_context_does_not_drop_valid_group_child(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        parent = SimpleNamespace(
            id=10,
            date=cutoff - timedelta(minutes=1),
            edit_date=None,
            message="",
            media=SimpleNamespace(),
            sender_id=7001,
            reply_to=None,
        )
        child = SimpleNamespace(
            id=11,
            date=cutoff,
            edit_date=None,
            message="خ 10 امام 190000",
            sender_id=7002,
            reply_to=SimpleNamespace(reply_to_msg_id=10, reply_to_top_id=None),
        )

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                yield child

            async def get_messages(self, _entity, *, ids):
                return parent if ids == 10 else None

        state = capture.CaptureState(
            self.root / "group-invalid-state/capture.sqlite", account="account2"
        )
        spool = capture.DurableEventSpool(
            self.root / "group-invalid-capture", account="account2"
        )
        try:
            engine = capture.CaptureEngine(state, spool)
            config = TelegramCaptureConfig(
                contract="market_telegram_capture_config/1.0",
                account="account2",
                api_id=1,
                api_hash="a" * 32,
                session_filename="account2.session",
                sources=tuple(
                    CaptureBinding(source_code=source, peer_id=-(index + 1))
                    for index, source in enumerate(
                        sorted(capture.ACCOUNT_SOURCES["account2"])
                    )
                ),
            )
            provider = telegram_capture.TelegramCaptureProvider(
                config,
                engine,
                session_path=self.root / "account2-invalid.session",
                hmac_key=b"k" * 32,
                stop=threading.Event(),
                backfill_not_before=cutoff,
                backfill_max_messages=2_000,
                backfill_source_codes=frozenset({"GROUP_1"}),
                release_sha="a" * 40,
            )
            provider._entity_by_source["GROUP_1"] = SimpleNamespace(
                forum=False, id=-1
            )
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    FakeClient(), SOURCE_POLICIES["GROUP_1"]
                )
            )
            status = state.backfill_status("GROUP_1")
            self.assertEqual(status["attempted"], 1)
            self.assertEqual(status["accepted"], 1)
            self.assertEqual(status["quarantined"], 0)
            self.assertTrue(state.has_message("GROUP_1", 10))
            self.assertTrue(state.has_message("GROUP_1", 11))
            self.assertEqual(
                state.connection.execute(
                    "SELECT COUNT(*) FROM capture_context_diagnostics"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                state.connection.execute(
                    "SELECT COUNT(*) FROM capture_quarantine"
                ).fetchone()[0],
                0,
            )
            self.assertTrue(state.backfill_covers("GROUP_1", cutoff))
        finally:
            state.close()

    def test_non_model_in_window_parent_is_terminal_and_does_not_block_child(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        parent = SimpleNamespace(
            id=10,
            date=cutoff,
            edit_date=None,
            message="",
            media=SimpleNamespace(),
            sender_id=7001,
            reply_to=None,
        )
        child = SimpleNamespace(
            id=11,
            date=cutoff + timedelta(seconds=1),
            edit_date=None,
            message="خ 10 امام 190000",
            sender_id=7002,
            reply_to=SimpleNamespace(reply_to_msg_id=10, reply_to_top_id=None),
        )

        class FakeClient:
            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                yield parent
                yield child

            async def get_messages(self, _entity, *, ids):
                return parent if ids == 10 else None

        state = capture.CaptureState(
            self.root / "group-in-window-invalid-state/capture.sqlite",
            account="account2",
        )
        spool = capture.DurableEventSpool(
            self.root / "group-in-window-invalid-capture", account="account2"
        )
        try:
            engine = capture.CaptureEngine(state, spool)
            config = TelegramCaptureConfig(
                contract="market_telegram_capture_config/1.0",
                account="account2",
                api_id=1,
                api_hash="a" * 32,
                session_filename="account2.session",
                sources=tuple(
                    CaptureBinding(source_code=source, peer_id=-(index + 1))
                    for index, source in enumerate(
                        sorted(capture.ACCOUNT_SOURCES["account2"])
                    )
                ),
            )
            provider = telegram_capture.TelegramCaptureProvider(
                config,
                engine,
                session_path=self.root / "account2-in-window-invalid.session",
                hmac_key=b"k" * 32,
                stop=threading.Event(),
                backfill_not_before=cutoff,
                backfill_max_messages=2_000,
                backfill_source_codes=frozenset({"GROUP_1"}),
                release_sha="a" * 40,
            )
            provider._entity_by_source["GROUP_1"] = SimpleNamespace(
                forum=False, id=-1
            )
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    FakeClient(), SOURCE_POLICIES["GROUP_1"]
                )
            )
            status = state.backfill_status("GROUP_1")
            self.assertEqual(status["attempted"], 2)
            self.assertEqual(status["accepted"], 2)
            self.assertEqual(status["quarantined"], 0)
            self.assertTrue(state.has_message("GROUP_1", 11))
            self.assertTrue(state.has_message("GROUP_1", 10))
            self.assertTrue(state.backfill_covers("GROUP_1", cutoff))
            self.assertEqual(
                state.connection.execute(
                    "SELECT COUNT(*) FROM capture_context_diagnostics"
                ).fetchone()[0],
                0,
            )
        finally:
            state.close()

    def test_two_group_children_share_one_explicit_reply_ancestor(self):
        cutoff = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        parent = SimpleNamespace(
            id=10,
            date=cutoff - timedelta(minutes=1),
            edit_date=None,
            message="ف 10 امام 190000",
            sender_id=7001,
            reply_to=None,
        )

        def child(message_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=message_id,
                date=cutoff + timedelta(seconds=message_id - 11),
                edit_date=None,
                message=f"خ {message_id}",
                sender_id=7000 + message_id,
                reply_to=SimpleNamespace(reply_to_msg_id=10, reply_to_top_id=None),
            )

        class FakeClient:
            parent_fetches = 0

            async def iter_messages(
                self, _entity, *, limit, reverse=False, offset_date=None
            ):
                yield child(11)
                yield child(12)

            async def get_messages(self, _entity, *, ids):
                self.parent_fetches += 1
                return parent if ids == 10 else None

        state = capture.CaptureState(
            self.root / "group-shared-state/capture.sqlite", account="account2"
        )
        spool = capture.DurableEventSpool(
            self.root / "group-shared-capture", account="account2"
        )
        try:
            engine = capture.CaptureEngine(state, spool)
            config = TelegramCaptureConfig(
                contract="market_telegram_capture_config/1.0",
                account="account2",
                api_id=1,
                api_hash="a" * 32,
                session_filename="account2.session",
                sources=tuple(
                    CaptureBinding(source_code=source, peer_id=-(index + 1))
                    for index, source in enumerate(
                        sorted(capture.ACCOUNT_SOURCES["account2"])
                    )
                ),
            )
            provider = telegram_capture.TelegramCaptureProvider(
                config,
                engine,
                session_path=self.root / "account2-shared.session",
                hmac_key=b"k" * 32,
                stop=threading.Event(),
                backfill_not_before=cutoff,
                backfill_max_messages=2_000,
                backfill_source_codes=frozenset({"GROUP_1"}),
                release_sha="a" * 40,
            )
            provider._entity_by_source["GROUP_1"] = SimpleNamespace(
                forum=False, id=-1
            )
            client = FakeClient()
            asyncio.run(
                provider._backfill_source_to_cutoff(
                    client, SOURCE_POLICIES["GROUP_1"]
                )
            )
            status = state.backfill_status("GROUP_1")
            self.assertEqual(status["attempted"], 2)
            self.assertEqual(status["accepted"], 2)
            self.assertEqual(
                state.replay_source_manifest_count(
                    provider._ensure_replay_run(), "GROUP_1"
                ),
                2,
            )
            self.assertEqual(client.parent_fetches, 1)
            self.assertEqual(state.sequence(), 3)
        finally:
            state.close()

    def test_fsync_failure_keeps_outbox_and_restart_recovers_without_loss(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        document = market_document(
            "XAUUSD", 3, published=moment, received=moment + timedelta(seconds=1)
        )
        real_fsync = capture.os.fsync
        failed = False

        def fail_first(descriptor: int) -> None:
            nonlocal failed
            if not failed:
                failed = True
                raise OSError(28, "fixture disk full")
            real_fsync(descriptor)

        with patch.object(capture.os, "fsync", side_effect=fail_first):
            with self.assertRaises(OSError):
                self.engine.accept(document)
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0],
            0,
        )
        # The bytes may have reached the file before fsync failed.  Startup
        # indexes that exact event and completes the durable outbox once.
        recovered_spool = capture.DurableEventSpool(
            self.root / "capture", account="account1"
        )
        recovered = capture.CaptureEngine(self.state, recovered_spool)
        self.assertEqual(recovered.drain(), 1)
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0],
            0,
        )

    def test_write_failure_never_acknowledges_internal_delivery(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        document = market_document(
            "XAUUSD", 4, published=moment, received=moment + timedelta(seconds=1)
        )
        with patch.object(capture.os, "write", side_effect=OSError(28, "fixture full")):
            with self.assertRaises(OSError):
                self.engine.accept(document)
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_outbox").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.state.connection.execute("SELECT COUNT(*) FROM capture_seen").fetchone()[0],
            0,
        )

    def test_exact_three_day_retention_compacts_mixed_file_and_is_auditable(self):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        expired = now - timedelta(days=3, seconds=1)
        boundary = now - timedelta(days=3)
        self.engine.accept(
            market_document(
                "XAUUSD", 10, published=expired, received=expired, text="old raw"
            ),
            now=expired,
        )
        self.engine.accept(
            market_document(
                "XAUUSD", 11, published=boundary, received=boundary, text="kept raw"
            ),
            now=expired,
        )
        report = self.engine.retention(now=now)
        self.assertEqual(report["spool"]["purged_records"], 1)
        spool_text = next((self.root / "capture").glob("events-*.jsonl")).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("old raw", spool_text)
        self.assertIn("kept raw", spool_text)
        audit = next((self.root / "capture").glob("retention-audit-*.jsonl")).read_text(
            encoding="utf-8"
        )
        self.assertNotIn("old raw", audit)
        self.assertIn('"purged_records":1', audit)

    def test_spool_repair_and_retention_never_buffer_whole_files(self):
        now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
        for message_id in range(100, 300):
            moment = now - timedelta(days=4 if message_id < 200 else 1)
            self.engine.accept(
                market_document(
                    "XAUUSD",
                    message_id,
                    published=moment,
                    received=moment,
                    text="x" * 4096,
                ),
                now=moment,
            )

        original_open = Path.open

        class NoBulkRead:
            def __init__(self, handle):
                self.handle = handle

            def __enter__(self):
                self.handle.__enter__()
                return self

            def __exit__(self, *args):
                return self.handle.__exit__(*args)

            def __iter__(self):
                return iter(self.handle)

            def __getattr__(self, name):
                return getattr(self.handle, name)

            def readlines(self, *_args, **_kwargs):
                raise AssertionError("capture spool must not bulk-read a file")

        def guarded_open(path, *args, **kwargs):
            handle = original_open(path, *args, **kwargs)
            mode = str(args[0] if args else kwargs.get("mode", "r"))
            return NoBulkRead(handle) if "b" in mode else handle

        with patch.object(Path, "open", guarded_open):
            repaired = capture.DurableEventSpool(
                self.root / "capture", account="account1"
            )
            report = repaired.purge(now=now)

        self.assertEqual(report["purged_records"], 100)
        self.assertEqual(len(repaired.event_ids), 100)

    def test_live_reply_cache_skips_account1_and_is_bounded_for_account2(self):
        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=tuple(
                CaptureBinding(source_code=source, peer_id=-(index + 1))
                for index, source in enumerate(
                    sorted(capture.ACCOUNT_SOURCES["account1"])
                )
            ),
        )
        provider = telegram_capture.TelegramCaptureProvider(
            config,
            self.engine,
            session_path=self.root / "account1.session",
            hmac_key=None,
            stop=threading.Event(),
        )
        provider._remember_live_reply_parent(SOURCE_POLICIES["XAUUSD"], 1)
        self.assertEqual(provider._live_seen, set())

        with patch.object(telegram_capture, "LIVE_REPLY_CACHE_MAX_ENTRIES", 2):
            provider._remember_live_reply_parent(SOURCE_POLICIES["GROUP_1"], 1)
            provider._remember_live_reply_parent(SOURCE_POLICIES["GROUP_1"], 2)
            provider._remember_live_reply_parent(SOURCE_POLICIES["GROUP_1"], 3)
        self.assertEqual(
            provider._live_seen,
            {("GROUP_1", 2), ("GROUP_1", 3)},
        )

    def test_partial_tail_is_repaired_but_corrupt_middle_fails_closed(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        self.engine.accept(
            market_document(
                "XAUUSD", 20, published=moment, received=moment + timedelta(seconds=1)
            )
        )
        path = next((self.root / "capture").glob("events-*.jsonl"))
        with path.open("ab") as handle:
            handle.write(b'{"partial":')
        repaired = capture.DurableEventSpool(self.root / "capture", account="account1")
        self.assertEqual(repaired.max_sequence, 1)
        self.assertTrue(tuple((self.root / "capture").glob("quarantine-*.jsonl")))
        original = path.read_bytes()
        path.write_bytes(b"not-json\n" + original)
        with self.assertRaisesRegex(
            capture.CaptureSpoolCorruption, "corrupt_middle"
        ):
            capture.DurableEventSpool(self.root / "capture", account="account1")

    def test_heartbeat_is_per_source_and_contains_no_raw_event(self):
        moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        self.engine.accept(
            market_document(
                "XAUUSD",
                30,
                published=moment,
                received=moment + timedelta(seconds=1),
                text="must not enter heartbeat",
            )
        )
        health = self.state.heartbeat(
            role="market-capture-account1",
            release_sha="a" * 40,
            mode="fixture",
            started_at_utc=capture.utc_text(moment),
            last_durable_append=self.spool.last_durable_append,
            now=moment + timedelta(seconds=2),
        )
        self.assertEqual(set(health["sources"]), capture.ACCOUNT_SOURCES["account1"])
        self.assertEqual(health["sources"]["XAUUSD"]["created"], 1)
        self.assertNotIn("must not enter heartbeat", json.dumps(health))


class CaptureContractTests(unittest.TestCase):
    def test_market_revision_identity_covers_pinned_and_silent_metadata(self):
        published = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        baseline = snapshot(1, published=published)
        policy = SOURCE_POLICIES["MELTED_AGGREGATE"]

        original = build_market_event(
            policy,
            baseline,
            event_type="message_created",
            received_at=published + timedelta(seconds=1),
            backfill=False,
        )
        pinned = build_market_event(
            policy,
            replace(baseline, pinned=True),
            event_type="message_created",
            received_at=published + timedelta(seconds=2),
            backfill=True,
        )
        silent = build_market_event(
            policy,
            replace(baseline, silent=True),
            event_type="message_created",
            received_at=published + timedelta(seconds=3),
            backfill=True,
        )

        self.assertEqual(len({original["event_id"], pinned["event_id"], silent["event_id"]}), 3)
        self.assertEqual(
            len(
                {
                    original["message"]["revision_sha256"],
                    pinned["message"]["revision_sha256"],
                    silent["message"]["revision_sha256"],
                }
            ),
            3,
        )

    def test_marked_group_identity_normalizes_for_anonymous_admin_detection(self):
        self.assertEqual(
            telegram_capture._bare_peer_id(-1_001_234_567_890), 1_234_567_890
        )
        self.assertEqual(telegram_capture._bare_peer_id(-12345), 12345)
        self.assertEqual(telegram_capture._bare_peer_id(12345), 12345)

    def test_group_reply_edit_delete_metadata_matches_existing_contract(self):
        key = b"k" * 32
        published = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
        group = build_group_event(
            SOURCE_POLICIES["GROUP_1"],
            snapshot(
                2,
                published=published,
                edited=published + timedelta(seconds=2),
                reply_to=1,
                text="قبوله 3 تا 188600",
            ),
            event_type="message_edited",
            received_at=published + timedelta(seconds=3),
            backfill=False,
            reply_status="resolved_from_live_stream",
            hmac_key=key,
        )
        source, decoded = capture.validate_ingress(group, "account2")
        self.assertEqual(source, "GROUP_1")
        self.assertEqual(decoded.reply_to_message_id, 1)
        self.assertEqual(decoded.edited_at_utc, "2026-08-26T10:00:02Z")
        self.assertRegex(decoded.sender_identity or "", r"^[0-9a-f]{16}$")
        self.assertEqual(decoded.sender_telegram_id, "7001")
        self.assertEqual(decoded.sender_display_name, "Test User")
        self.assertEqual(group["schema_version"], "2.1")
        deleted = build_deleted_event(
            SOURCE_POLICIES["GROUP_1"], message_id=2, received_at=published
        )
        self.assertEqual(
            capture.validate_ingress(deleted, "account2")[1].event_type,
            "message_deleted",
        )

    def test_group_backfill_updates_gap_recovery_metric(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = capture.CaptureState(root / "state.sqlite", account="account2")
            try:
                spool = capture.DurableEventSpool(root / "spool", account="account2")
                engine = capture.CaptureEngine(state, spool)
                moment = datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
                document = build_group_event(
                    SOURCE_POLICIES["GROUP_2"],
                    snapshot(8, published=moment, text="fixture recovered"),
                    event_type="message_created",
                    received_at=moment + timedelta(seconds=1),
                    backfill=True,
                    reply_status="not_reply",
                    hmac_key=b"h" * 32,
                )
                engine.accept(document)
                health = state.heartbeat(
                    role="market-capture-account2",
                    release_sha="a" * 40,
                    mode="fixture",
                    started_at_utc=capture.utc_text(moment),
                    last_durable_append=spool.last_durable_append,
                    now=moment + timedelta(seconds=2),
                )
                self.assertEqual(health["sources"]["GROUP_2"]["gap_recovered"], 1)
            finally:
                state.close()

    def test_config_requires_exact_account_allowlist(self):
        bindings = tuple(
            CaptureBinding(source_code=source, peer_id=-(index + 1))
            for index, source in enumerate(sorted(capture.ACCOUNT_SOURCES["account1"]))
        )
        config = TelegramCaptureConfig(
            contract="market_telegram_capture_config/1.0",
            account="account1",
            api_id=1,
            api_hash="a" * 32,
            session_filename="account1.session",
            sources=bindings,
        )
        self.assertEqual(
            {item.source_code for item in config.sources},
            capture.ACCOUNT_SOURCES["account1"],
        )
        with self.assertRaises(ValidationError):
            TelegramCaptureConfig(
                contract="market_telegram_capture_config/1.0",
                account="account1",
                api_id=1,
                api_hash="a" * 32,
                session_filename="account1.session",
                sources=bindings[:-1],
            )

    def test_live_authority_marker_is_release_bound_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "authority-container.json"
            marker.write_text(
                json.dumps(
                    {
                        "contract": AUTHORITY_MARKER_CONTRACT,
                        "authority": "container",
                        "role": "market-capture-account1",
                        "release_sha": "a" * 40,
                        "authorized_at_utc": "2026-08-26T10:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(marker, 0o600)
            validate_authority_marker(
                root, role="market-capture-account1", release_sha="a" * 40
            )
            with self.assertRaisesRegex(
                capture.CaptureRuntimeError, "marker_mismatch"
            ):
                validate_authority_marker(
                    root, role="market-capture-account1", release_sha="b" * 40
                )


class ExplicitBackfillAdapterTests(unittest.TestCase):
    """Owner-authorized history is unbounded only on the exact three sources."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.staging = connect_coin_group_staging(root / "capture.sqlite3")
        self.market = connect_market_store(root / "market.sqlite3")
        initialize_capture_adapter(self.staging)
        initialize_market_store(self.market)
        self.published = datetime(2026, 8, 25, 9, 33, tzinfo=UTC)
        self.received = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.staging.close()
        self.market.close()
        self.temp.cleanup()

    def _market_document(
        self,
        sequence: int,
        *,
        source: str = "MELTED_PRIMARY_FLOW",
        backfill: bool = True,
        explicit: bool = True,
        forwarded: bool = False,
        text: str = "95,000,000 فروش 5 تا بدون حواله",
        published: datetime | None = None,
    ) -> dict[str, object]:
        message = snapshot(
            sequence, published=published or self.published, text=text
        )
        if forwarded:
            message = replace(message, is_forwarded=True)
        return build_market_event(
            SOURCE_POLICIES[source],
            message,
            event_type="message_snapshot",
            received_at=self.received,
            backfill=backfill,
            explicit_backfill=explicit,
        )

    def _group_document(
        self,
        sequence: int,
        *,
        backfill: bool = True,
        explicit: bool = True,
        forwarded: bool = False,
        text: str = "امام فروش فردا 190000 / 5 تا",
    ) -> dict[str, object]:
        message = snapshot(sequence, published=self.published, text=text)
        if forwarded:
            message = replace(message, is_forwarded=True)
        return build_group_event(
            SOURCE_POLICIES["GROUP_1"],
            message,
            event_type="message_created",
            received_at=self.received,
            backfill=backfill,
            reply_status="not_reply",
            hmac_key=b"l" * 32,
            explicit_backfill=explicit,
        )

    def _lineage(self, event_id: str) -> sqlite3.Row:
        row = self.staging.execute(
            "SELECT * FROM capture_explicit_backfill_lineage WHERE event_id=?",
            (event_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        return row

    def _project(self) -> None:
        project_capture_changes(
            self.staging,
            self.market,
            as_of_utc=self.received + timedelta(seconds=1),
        )
        self.staging.commit()
        self.market.commit()

    def test_schema_v6_migrates_to_v8_lineage_without_raw_columns(self) -> None:
        self.staging.execute("DROP TABLE capture_explicit_backfill_lineage")
        self.staging.execute(
            "UPDATE capture_adapter_metadata SET schema_version=6 WHERE singleton=1"
        )
        self.staging.commit()

        initialize_capture_adapter(self.staging)

        self.assertEqual(
            self.staging.execute(
                "SELECT schema_version FROM capture_adapter_metadata WHERE singleton=1"
            ).fetchone()[0],
            8,
        )
        columns = {
            str(row["name"])
            for row in self.staging.execute(
                "PRAGMA table_info(capture_explicit_backfill_lineage)"
            ).fetchall()
        }
        self.assertIn("disposition_code", columns)
        self.assertIn("terminal_at_utc", columns)
        self.assertFalse({"text", "message_text", "raw_text"} & columns)

    def test_explicit_contract_requires_backfill_and_known_account_sources(self) -> None:
        without_backfill = self._market_document(
            1, backfill=False, explicit=True
        )
        with self.assertRaisesRegex(
            CaptureEventContractError, "explicit_backfill_flag_required"
        ):
            decode_market_channel_event(without_backfill)

        public_replay = self._market_document(
            2, source="XAUUSD", backfill=True, explicit=True
        )
        public_event = decode_market_channel_event(public_replay)
        self.assertTrue(public_event.is_explicit_backfill)
        stage_capture_event(self.staging, public_event)
        self.assertEqual(
            self.staging.execute(
                "SELECT origin FROM capture_event_lineage WHERE event_id=?",
                (public_event.event_id,),
            ).fetchone()[0],
            "explicit_backfill",
        )
        self.assertIsNone(
            self.staging.execute(
                "SELECT 1 FROM capture_explicit_backfill_lineage WHERE event_id=?",
                (public_event.event_id,),
            ).fetchone()
        )

        wrong_group_flag = self._group_document(
            3, backfill=False, explicit=True
        )
        with self.assertRaisesRegex(
            CaptureEventContractError, "explicit_backfill_flag_required"
        ):
            decode_coin_group_event(wrong_group_flag)

    def test_old_explicit_market_reaches_parser_and_lineage_terminal(self) -> None:
        document = self._market_document(10)
        event = decode_market_channel_event(document)
        report = stage_capture_event(self.staging, event)
        self.assertTrue(report.staged_change)
        self.assertEqual(self._lineage(event.event_id)["status"], "PENDING")

        self._project()

        lineage = self._lineage(event.event_id)
        self.assertEqual(lineage["status"], "PARSED")
        self.assertEqual(lineage["disposition_code"], "PARSER_EXECUTED")
        self.assertIsNotNone(
            self.market.execute(
                "SELECT 1 FROM market_observations "
                "WHERE source_code='PRIVATE_GOLD_CHANNEL' AND event_type='OFFER'"
            ).fetchone()
        )

    def test_explicit_media_and_service_are_identity_bound_terminal_filters(self) -> None:
        fixtures = (
            (
                replace(
                    snapshot(31, published=self.published, text=""),
                    has_media=True,
                    media_type="photo",
                ),
                "NON_MODEL_MEDIA_ONLY",
            ),
            (
                replace(
                    snapshot(32, published=self.published, text=""),
                    action_type="MessageActionPinMessage",
                ),
                "NON_MODEL_SERVICE",
            ),
        )
        for message, expected_disposition in fixtures:
            with self.subTest(disposition=expected_disposition):
                event = decode_market_channel_event(
                    build_market_event(
                        SOURCE_POLICIES["MELTED_PRIMARY_FLOW"],
                        message,
                        event_type="message_snapshot",
                        received_at=self.received,
                        backfill=True,
                        explicit_backfill=True,
                    )
                )
                report = stage_capture_event(self.staging, event)
                self.assertFalse(report.duplicate)
                self._project()
                lineage = self._lineage(event.event_id)
                self.assertEqual(lineage["status"], "FILTERED")
                self.assertEqual(
                    lineage["disposition_code"], expected_disposition
                )
                self.assertIsNotNone(lineage["terminal_at_utc"])

    def test_old_explicit_group_bypasses_only_six_hour_projection_bound(self) -> None:
        document = self._group_document(20)
        event = decode_coin_group_event(document)
        self.assertTrue(stage_capture_event(self.staging, event).staged_change)

        self._project()

        self.assertEqual(self._lineage(event.event_id)["status"], "PARSED")
        self.assertIsNotNone(
            self.market.execute(
                "SELECT 1 FROM market_observations WHERE source_code='GROUP_1'"
            ).fetchone()
        )

    def test_routine_old_market_and_group_remain_bounded(self) -> None:
        market = decode_market_channel_event(
            self._market_document(30, explicit=False)
        )
        group = decode_coin_group_event(self._group_document(31, explicit=False))
        self.assertFalse(stage_capture_event(self.staging, market).staged_change)
        self.assertTrue(stage_capture_event(self.staging, group).staged_change)

        self._project()

        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_explicit_backfill_lineage"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            self.market.execute(
                "SELECT COUNT(*) FROM market_observations "
                "WHERE source_code IN ('PRIVATE_GOLD_CHANNEL','GROUP_1')"
            ).fetchone()[0],
            0,
        )

    def test_explicit_delivery_and_projection_are_idempotent(self) -> None:
        event = decode_market_channel_event(self._market_document(40))
        first = stage_capture_event(self.staging, event)
        second = stage_capture_event(self.staging, event)
        self.assertTrue(first.accepted)
        self.assertTrue(second.duplicate)
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_explicit_backfill_lineage"
            ).fetchone()[0],
            1,
        )

        self._project()
        self._project()

        self.assertEqual(self._lineage(event.event_id)["status"], "PARSED")
        self.assertEqual(
            self.market.execute(
                "SELECT COUNT(*) FROM market_observations "
                "WHERE source_code='PRIVATE_GOLD_CHANNEL' AND event_type='OFFER'"
            ).fetchone()[0],
            1,
        )

    def test_routine_seen_event_is_upgraded_to_accounted_explicit_replay(self) -> None:
        explicit_document = self._market_document(45)
        routine_document = self._market_document(45, explicit=False)
        self.assertNotEqual(
            routine_document["event_id"], explicit_document["event_id"]
        )
        # Older producers could emit the routine identity for this replay.
        # The adapter must still upgrade that collision into accounted work.
        explicit_document["event_id"] = routine_document["event_id"]

        routine = stage_capture_event(
            self.staging, decode_market_channel_event(routine_document)
        )
        self.assertFalse(routine.staged_change)
        self.assertEqual(
            self.staging.execute(
                "SELECT COUNT(*) FROM capture_explicit_backfill_lineage"
            ).fetchone()[0],
            0,
        )

        explicit = decode_market_channel_event(explicit_document)
        upgraded = stage_capture_event(self.staging, explicit)
        self.assertFalse(upgraded.accepted)
        self.assertTrue(upgraded.duplicate)
        self.assertTrue(upgraded.staged_change)
        self.assertEqual(self._lineage(explicit.event_id)["status"], "PENDING")

        self._project()

        self.assertEqual(self._lineage(explicit.event_id)["status"], "PARSED")

    def test_routine_current_duplicate_is_reparsed_for_explicit_lineage(self) -> None:
        published = self.received - timedelta(minutes=10)
        routine_document = self._market_document(
            46, explicit=False, published=published
        )
        explicit_document = self._market_document(46, published=published)
        explicit_document["event_id"] = routine_document["event_id"]

        routine = stage_capture_event(
            self.staging, decode_market_channel_event(routine_document)
        )
        self.assertTrue(routine.staged_change)
        self._project()

        explicit = decode_market_channel_event(explicit_document)
        upgraded = stage_capture_event(self.staging, explicit)
        self.assertTrue(upgraded.duplicate)
        self.assertFalse(upgraded.staged_change)
        lineage = self._lineage(explicit.event_id)
        self.assertEqual(lineage["status"], "PENDING")
        self.assertEqual(
            lineage["disposition_code"], "AWAITING_CURRENT_REPARSE"
        )

        self._project()

        self.assertEqual(self._lineage(explicit.event_id)["status"], "PARSED")

    def test_valid_no_fact_forward_and_delete_are_terminal_not_silent(self) -> None:
        forwarded = decode_market_channel_event(
            self._market_document(50, forwarded=True)
        )
        stage_capture_event(self.staging, forwarded)
        self._project()
        forwarded_lineage = self._lineage(forwarded.event_id)
        self.assertEqual(forwarded_lineage["status"], "PARSED")
        self.assertEqual(
            forwarded_lineage["disposition_code"], "FORWARDED_FILTERED"
        )

        deleted_document = build_deleted_event(
            SOURCE_POLICIES["MELTED_PRIMARY_FLOW"],
            message_id=51,
            received_at=self.received,
        )
        deleted_document["producer"]["origin"] = "explicit_backfill"  # type: ignore[index]
        deleted_document["producer"]["is_backfill"] = True  # type: ignore[index]
        deleted = decode_market_channel_event(deleted_document)
        stage_capture_event(self.staging, deleted)
        deleted_lineage = self._lineage(deleted.event_id)
        self.assertEqual(deleted_lineage["status"], "FILTERED")
        self.assertEqual(deleted_lineage["disposition_code"], "DELETE_APPLIED")

        group_forward = decode_coin_group_event(
            self._group_document(52, forwarded=True)
        )
        stage_capture_event(self.staging, group_forward)
        group_lineage = self._lineage(group_forward.event_id)
        self.assertEqual(group_lineage["status"], "FILTERED")
        self.assertEqual(
            group_lineage["disposition_code"], "FORWARDED_UNSUPPORTED"
        )

    def test_lineage_is_purged_with_raw_retention(self) -> None:
        event = decode_coin_group_event(
            self._group_document(60, forwarded=True)
        )
        stage_capture_event(self.staging, event)
        self.assertIsNotNone(self._lineage(event.event_id))

        purge_capture_staging(
            self.staging,
            as_of_utc=self.received + timedelta(days=3, seconds=1),
        )

        self.assertIsNone(
            self.staging.execute(
                "SELECT 1 FROM capture_explicit_backfill_lineage WHERE event_id=?",
                (event.event_id,),
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()
