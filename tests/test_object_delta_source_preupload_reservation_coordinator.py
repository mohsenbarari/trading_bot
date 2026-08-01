from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.application_writer_term import ApplicationWriterTermError, ValidatedWriterTerm
from core.object_delta_runtime_binding import ObjectDeltaSourceRuntimeBinding
from core.object_delta_source_preupload_authorization import (
    project_authorized_object_delta_source_preupload_attempt,
)
from core.object_delta_source_preupload_reservation_coordinator import (
    ObjectDeltaSourcePreuploadReservationCoordinatorError,
    reserve_authorized_object_delta_source_preupload,
)
from core.object_delta_source_publication_attempt import (
    SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY,
    SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE,
)
from core.object_delta_source_publication_attempt_persistence import (
    AuthorizedObjectDeltaSourcePreuploadReservation,
    ObjectDeltaSourcePublicationAttemptPersistenceError,
    reserve_authorized_object_delta_source_preupload_attempt,
)
from models.object_delta import ObjectDeltaStream
from tests.test_object_delta_source_preupload_authorization import (
    ObjectDeltaSourcePreuploadAuthorizationTests,
)
from tests.test_object_delta_source_publication_attempt_persistence import (
    _AttemptSession,
    reservation_row,
)


NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


class ObjectDeltaSourcePreuploadReservationCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture = ObjectDeltaSourcePreuploadAuthorizationTests(methodName="runTest")
        fixture.setUp()
        self.fixture = fixture
        self.authorization = fixture.authorize()
        self.attempt = project_authorized_object_delta_source_preupload_attempt(
            self.authorization
        )
        self.binding = fixture.binding
        self.writer_term = ValidatedWriterTerm(
            holder_site=self.binding.source_site,
            writer_epoch=self.attempt.intent.writer_epoch,
            lease_id=self.attempt.intent.writer_lease_id,
            issued_at=NOW,
            expires_at=NOW + timedelta(seconds=60),
            witness_transition_id="witness-transition-20260801",
        )

    def _settings(self, *, enabled: object = True) -> SimpleNamespace:
        return SimpleNamespace(
            object_delta_source_preupload_reservation_enabled=enabled,
            application_writer_term_enforced=True,
            application_writer_term_local_site=self.binding.source_site,
            application_writer_term_lease_file=Path("/etc/trading-bot/writer-term.json"),
            application_writer_term_safety_margin_seconds=5,
            application_writer_term_max_lease_duration_seconds=90,
        )

    def _stream(self) -> ObjectDeltaStream:
        value = self.attempt.intent.stream
        return ObjectDeltaStream(
            id=701,
            source_site=value.source_site,
            destination_site=value.destination_site,
            campaign_id=value.campaign_id,
            release_sha=value.release_sha,
            stream_generation_id=value.stream_generation_id,
            next_sequence=self.attempt.intent.last_sequence + 1,
        )

    def _session(self, **overrides: object) -> _AttemptSession:
        values: dict[str, object] = {"stream": self._stream()}
        values.update(overrides)
        return _AttemptSession(**values)

    def test_default_off_fails_before_binding_term_or_persistence_io(self) -> None:
        session = self._session()
        with (
            patch(
                "core.object_delta_source_preupload_reservation_coordinator._settings_from_root_runtime",
                return_value=self._settings(enabled=False),
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.binding_from_settings",
                side_effect=AssertionError("binding must not be read while disabled"),
            ) as binding_loader,
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.require_active_writer_term",
                side_effect=AssertionError("term must not be read while disabled"),
            ) as term_loader,
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.reserve_authorized_object_delta_source_preupload_attempt",
                side_effect=AssertionError("persistence must not run while disabled"),
            ) as reserve,
            self.assertRaisesRegex(
                ObjectDeltaSourcePreuploadReservationCoordinatorError,
                "runtime is disabled",
            ),
        ):
            asyncio.run(reserve_authorized_object_delta_source_preupload(session, object()))
        binding_loader.assert_not_called()
        term_loader.assert_not_called()
        reserve.assert_not_called()
        self.assertEqual([], session.statements)

    def test_missing_root_binding_fails_before_term_or_persistence(self) -> None:
        session = self._session()
        with (
            patch(
                "core.object_delta_source_preupload_reservation_coordinator._settings_from_root_runtime",
                return_value=self._settings(),
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.binding_from_settings",
                return_value=None,
            ) as binding_loader,
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.require_active_writer_term",
                side_effect=AssertionError("term must not be read without a root binding"),
            ) as term_loader,
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.reserve_authorized_object_delta_source_preupload_attempt",
                side_effect=AssertionError("persistence must not run without a root binding"),
            ) as reserve,
            self.assertRaisesRegex(
                ObjectDeltaSourcePreuploadReservationCoordinatorError,
                "root runtime binding is required",
            ),
        ):
            asyncio.run(
                reserve_authorized_object_delta_source_preupload(session, self.authorization)
            )
        binding_loader.assert_called_once()
        term_loader.assert_not_called()
        reserve.assert_not_called()
        self.assertEqual([], session.statements)

    def test_authorized_pin_must_match_the_root_binding_before_term_or_persistence(self) -> None:
        session = self._session()
        other_binding = replace(
            self.binding,
            expected_registry_fingerprint="f" * 16,
        )
        with (
            patch(
                "core.object_delta_source_preupload_reservation_coordinator._settings_from_root_runtime",
                return_value=self._settings(),
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.binding_from_settings",
                return_value=other_binding,
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.require_active_writer_term",
                side_effect=AssertionError("term must not be read for a mismatched pin"),
            ) as term_loader,
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.reserve_authorized_object_delta_source_preupload_attempt",
                side_effect=AssertionError("persistence must not run for a mismatched pin"),
            ) as reserve,
            self.assertRaisesRegex(
                ObjectDeltaSourcePreuploadReservationCoordinatorError,
                "pin does not match the root runtime binding",
            ),
        ):
            asyncio.run(
                reserve_authorized_object_delta_source_preupload(session, self.authorization)
            )
        term_loader.assert_not_called()
        reserve.assert_not_called()
        self.assertEqual([], session.statements)

    def test_stale_or_mismatched_writer_term_fails_before_persistence(self) -> None:
        cases = (
            (
                ApplicationWriterTermError("expired"),
                "fresh Writer Witness term is unavailable",
            ),
            (
                replace(self.writer_term, writer_epoch=self.writer_term.writer_epoch + 1),
                "fresh Writer Witness term does not match locked evidence",
            ),
        )
        for term_result, expected in cases:
            with self.subTest(term_result=type(term_result).__name__):
                session = self._session()
                with (
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator._settings_from_root_runtime",
                        return_value=self._settings(),
                    ),
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator.binding_from_settings",
                        return_value=self.binding,
                    ),
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator.require_active_writer_term",
                        side_effect=(
                            term_result
                            if isinstance(term_result, Exception)
                            else None
                        ),
                        return_value=(
                            None if isinstance(term_result, Exception) else term_result
                        ),
                    ) as term_loader,
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator.reserve_authorized_object_delta_source_preupload_attempt",
                        side_effect=AssertionError("persistence must not run for an invalid term"),
                    ) as reserve,
                    self.assertRaisesRegex(
                        ObjectDeltaSourcePreuploadReservationCoordinatorError,
                        expected,
                    ),
                ):
                    asyncio.run(
                        reserve_authorized_object_delta_source_preupload(
                            session,
                            self.authorization,
                        )
                    )
                term_loader.assert_called_once()
                reserve.assert_not_called()
                self.assertEqual([], session.statements)

    def test_raw_intent_or_attempt_is_rejected_before_persistence(self) -> None:
        for raw in (self.authorization.intent, self.authorization.attempt):
            with self.subTest(raw_type=type(raw).__name__):
                session = self._session()
                with (
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator._settings_from_root_runtime",
                        return_value=self._settings(),
                    ),
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator.binding_from_settings",
                        return_value=self.binding,
                    ),
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator.require_active_writer_term",
                        side_effect=AssertionError("term must not be read for raw publication data"),
                    ) as term_loader,
                    patch(
                        "core.object_delta_source_preupload_reservation_coordinator.reserve_authorized_object_delta_source_preupload_attempt",
                        side_effect=AssertionError("persistence must not run for raw publication data"),
                    ) as reserve,
                    self.assertRaisesRegex(
                        ObjectDeltaSourcePreuploadReservationCoordinatorError,
                        "opaque authorized locked snapshot",
                    ),
                ):
                    asyncio.run(reserve_authorized_object_delta_source_preupload(session, raw))
                term_loader.assert_not_called()
                reserve.assert_not_called()
                self.assertEqual([], session.statements)

    def test_reserves_and_replays_the_exact_attempt_without_owning_the_transaction(self) -> None:
        reserve_session = self._session()
        with (
            patch(
                "core.object_delta_source_preupload_reservation_coordinator._settings_from_root_runtime",
                return_value=self._settings(),
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.binding_from_settings",
                return_value=self.binding,
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.require_active_writer_term",
                return_value=self.writer_term,
            ),
        ):
            reserved = asyncio.run(
                reserve_authorized_object_delta_source_preupload(
                    reserve_session,
                    self.authorization,
                )
            )
        self.assertEqual(SOURCE_PUBLICATION_ATTEMPT_ACTION_RESERVE, reserved.action)
        self.assertEqual(self.attempt, reserved.state)
        self.assertEqual(self.attempt.attempt_id, reserved.attempt_row.attempt_id)
        self.assertEqual(1, reserve_session.flush_count)
        self.assertEqual(0, reserve_session.begin_count)
        self.assertEqual(0, reserve_session.commit_count)
        self.assertEqual(0, reserve_session.rollback_count)

        existing = reservation_row(self.attempt)
        replay_session = self._session(by_attempt=existing, by_object_key=existing)
        with (
            patch(
                "core.object_delta_source_preupload_reservation_coordinator._settings_from_root_runtime",
                return_value=self._settings(),
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.binding_from_settings",
                return_value=self.binding,
            ),
            patch(
                "core.object_delta_source_preupload_reservation_coordinator.require_active_writer_term",
                return_value=self.writer_term,
            ),
        ):
            replayed = asyncio.run(
                reserve_authorized_object_delta_source_preupload(
                    replay_session,
                    self.authorization,
                )
            )
        self.assertEqual(SOURCE_PUBLICATION_ATTEMPT_ACTION_REPLAY, replayed.action)
        self.assertEqual([], replay_session.added)
        self.assertEqual(0, replay_session.flush_count)

    def test_persistence_seam_rejects_raw_or_forged_authority_before_sql(self) -> None:
        session = self._session()
        for raw in (self.authorization, self.authorization.intent, self.authorization.attempt):
            with self.subTest(raw_type=type(raw).__name__):
                with self.assertRaisesRegex(
                    ObjectDeltaSourcePublicationAttemptPersistenceError,
                    "reservation capability is required",
                ):
                    asyncio.run(
                        reserve_authorized_object_delta_source_preupload_attempt(session, raw)
                    )
        forged = AuthorizedObjectDeltaSourcePreuploadReservation(
            authorization=self.authorization,
            attempt=self.attempt,
            writer_term=self.writer_term,
        )
        with self.assertRaisesRegex(
            ObjectDeltaSourcePublicationAttemptPersistenceError,
            "was not verified",
        ):
            asyncio.run(
                reserve_authorized_object_delta_source_preupload_attempt(session, forged)
            )
        self.assertEqual([], session.statements)


class ObjectDeltaSourcePreuploadReservationCoordinatorStaticTests(unittest.TestCase):
    def test_coordinator_has_no_external_transport_and_no_legacy_test_only_bridge(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "object_delta_source_preupload_reservation_coordinator.py"
        )
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.add(node.module)
        forbidden = {
            "age",
            "aiohttp",
            "boto3",
            "botocore",
            "http",
            "httpx",
            "requests",
            "socket",
            "subprocess",
            "urllib",
        }
        self.assertFalse(
            [
                value
                for value in imports
                if value in forbidden or value.startswith(("boto.", "urllib."))
            ]
        )
        self.assertNotIn("_legacy_test_only_", text)
        banned_calls = {"begin", "commit", "rollback", "put_object", "open", "write_bytes"}
        self.assertFalse(
            [
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in banned_calls
            ]
        )

    def test_public_reservation_seam_does_not_delegate_to_legacy_test_only_helpers(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "core"
            / "object_delta_source_publication_attempt_persistence.py"
        )
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        public = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "reserve_authorized_object_delta_source_preupload_attempt"
        )
        names = {
            node.id
            for node in ast.walk(public)
            if isinstance(node, ast.Name)
        }
        self.assertFalse({name for name in names if name.startswith("_legacy_test_only_")})


if __name__ == "__main__":
    unittest.main()
