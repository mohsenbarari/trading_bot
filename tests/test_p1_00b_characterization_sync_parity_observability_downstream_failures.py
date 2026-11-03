from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from collections.abc import Mapping
from contextlib import ExitStack
from datetime import datetime
import hashlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import fastapi

import api.routers.sync as sync_router
import core.sync_parity as sync_parity
import core.sync_parity_observability as sync_parity_observability


OBSERVABILITY_KEY_SENTINEL = "obs-key-sentinel"
SERVER_MODE_SENTINEL = "server-mode-sentinel"
NON_LOOPBACK_CLIENT_HOST = "198.51.100.10"
SNAPSHOT_ENDPOINT_PATH = "/api/sync/parity/snapshot"
STATUS_ENDPOINT_PATH = "/api/sync/parity/status"


class _InertDbProbe:
    def __init__(self) -> None:
        self.recorded_attributes: list[str] = []

    def __getattr__(self, name: str) -> object:
        self.recorded_attributes.append(name)
        raise AttributeError(name)


class _LoggerSpy:
    def __init__(self) -> None:
        self.warning_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.info_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.other_calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def warning(self, *args: object, **kwargs: object) -> None:
        self.warning_calls.append((args, dict(kwargs)))

    def info(self, *args: object, **kwargs: object) -> None:
        self.info_calls.append((args, dict(kwargs)))

    def __getattr__(self, name: str) -> object:
        def _record_other(*args: object, **kwargs: object) -> None:
            self.other_calls.append((name, args, dict(kwargs)))

        return _record_other


class SyncParityObservabilityDownstreamFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.snapshot_request = self._build_observability_request(SNAPSHOT_ENDPOINT_PATH)
        self.status_request = self._build_observability_request(STATUS_ENDPOINT_PATH)

    @staticmethod
    def _build_observability_request(path: str) -> SimpleNamespace:
        return SimpleNamespace(
            headers={"X-Observability-Api-Key": OBSERVABILITY_KEY_SENTINEL},
            url=SimpleNamespace(path=path),
            client=SimpleNamespace(host=NON_LOOPBACK_CLIENT_HOST),
        )

    @staticmethod
    def _enter_shared_settings_patches(stack: ExitStack) -> None:
        stack.enter_context(
            patch.object(
                sync_router.settings,
                "observability_api_key",
                OBSERVABILITY_KEY_SENTINEL,
            )
        )
        stack.enter_context(
            patch.object(
                sync_router.settings,
                "server_mode",
                SERVER_MODE_SENTINEL,
            )
        )

    async def test_parity_snapshot_builder_valueerror_becomes_400_before_server_mode_decoration(
        self,
    ) -> None:
        orig_router_builder = sync_router.build_database_parity_snapshot
        orig_core_builder = sync_parity.build_database_parity_snapshot
        orig_get_redis_client = sync_router.get_redis_client
        orig_record_parity_summary = sync_router.record_sync_parity_summary
        orig_audit_log = sync_router.audit_log
        orig_observability_api_key = sync_router.settings.observability_api_key
        orig_server_mode = sync_router.settings.server_mode
        orig_get_snapshot = sync_router.get_sync_parity_snapshot
        orig_record_status = sync_router.record_sync_parity_status
        orig_require_key = sync_router._require_observability_key
        orig_coerce_max_age = sync_router._coerce_parity_status_max_age_seconds
        orig_summarize_exception = sync_router._summarize_exception
        orig_core_summarize = sync_parity_observability.summarize_parity_comparison
        orig_router_summarize = sync_router.summarize_parity_comparison

        db_probe = _InertDbProbe()
        builder_error = ValueError("parity-snapshot-builder-rejected")
        builder_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def fake_build_database_parity_snapshot(
            *args: object,
            **kwargs: object,
        ) -> dict[str, object]:
            builder_calls.append((args, dict(kwargs)))
            raise builder_error

        get_redis_spy = MagicMock()
        record_summary_spy = MagicMock()
        audit_log_spy = MagicMock()

        unreturned_sentinel = object()
        returned_value: object = unreturned_sentinel

        with ExitStack() as stack:
            self._enter_shared_settings_patches(stack)
            stack.enter_context(
                patch.object(
                    sync_router,
                    "build_database_parity_snapshot",
                    new=fake_build_database_parity_snapshot,
                )
            )
            stack.enter_context(
                patch.object(sync_router, "get_redis_client", new=get_redis_spy)
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "record_sync_parity_summary",
                    new=record_summary_spy,
                )
            )
            stack.enter_context(
                patch.object(sync_router, "audit_log", new=audit_log_spy)
            )
            with self.assertRaises(fastapi.HTTPException) as exc_ctx:
                returned_value = await sync_router.get_sync_parity_snapshot(
                    request=self.snapshot_request,
                    mode="deep",
                    max_rows_per_table=7,
                    db=db_probe,
                )

        exc = exc_ctx.exception
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.detail, "parity-snapshot-builder-rejected")
        self.assertEqual(exc.detail, str(builder_error))
        self.assertIs(exc.__cause__, builder_error)

        self.assertEqual(len(builder_calls), 1)
        call_args, call_kwargs = builder_calls[0]
        self.assertEqual(len(call_args), 1)
        self.assertEqual(call_args, (db_probe,))
        self.assertIs(call_args[0], db_probe)
        self.assertEqual(call_kwargs, {"mode": "deep", "max_rows_per_table": 7})

        self.assertEqual(db_probe.recorded_attributes, [])

        self.assertNotIn(SERVER_MODE_SENTINEL, exc.detail)
        self.assertIs(returned_value, unreturned_sentinel)
        self.assertNotIsInstance(returned_value, Mapping)

        get_redis_spy.assert_not_called()
        record_summary_spy.assert_not_called()
        audit_log_spy.assert_not_called()

        self.assertIs(sync_router.build_database_parity_snapshot, orig_router_builder)
        self.assertIs(sync_router._require_observability_key, orig_require_key)
        self.assertIs(sync_router.get_sync_parity_snapshot, orig_get_snapshot)
        self.assertIs(sync_parity.build_database_parity_snapshot, orig_core_builder)
        self.assertIs(sync_router.get_redis_client, orig_get_redis_client)
        self.assertIs(sync_router.record_sync_parity_summary, orig_record_parity_summary)
        self.assertIs(sync_router.audit_log, orig_audit_log)
        self.assertIs(
            sync_router.settings.observability_api_key,
            orig_observability_api_key,
        )
        self.assertIs(sync_router.settings.server_mode, orig_server_mode)
        self.assertIs(sync_router.record_sync_parity_status, orig_record_status)
        self.assertIs(
            sync_router._coerce_parity_status_max_age_seconds,
            orig_coerce_max_age,
        )
        self.assertIs(sync_router._summarize_exception, orig_summarize_exception)
        self.assertIs(
            sync_parity_observability.summarize_parity_comparison,
            orig_core_summarize,
        )
        self.assertIs(sync_router.summarize_parity_comparison, orig_router_summarize)

    async def test_parity_status_redis_store_failure_becomes_503_and_skips_parity_summary_telemetry(
        self,
    ) -> None:
        orig_record_status = sync_router.record_sync_parity_status
        orig_get_snapshot = sync_router.get_sync_parity_snapshot
        orig_require_key = sync_router._require_observability_key
        orig_coerce_max_age = sync_router._coerce_parity_status_max_age_seconds
        orig_summarize_exception = sync_router._summarize_exception
        orig_core_summarize = sync_parity_observability.summarize_parity_comparison
        orig_router_summarize = sync_router.summarize_parity_comparison
        orig_get_redis_client = sync_router.get_redis_client
        orig_record_parity_summary = sync_router.record_sync_parity_summary
        orig_audit_log = sync_router.audit_log
        orig_logger = sync_router.logger
        orig_observability_api_key = sync_router.settings.observability_api_key
        orig_server_mode = sync_router.settings.server_mode
        orig_max_age_seconds = sync_router.settings.sync_parity_status_max_age_seconds

        comparison = {
            "status": "business_drift",
            "mode": "deep",
            "compared_at": "2026-06-28T05:00:00Z",
            "severity_counts": {
                "business_drift": 2,
                "critical_drift": 0,
                "incomplete": 1,
            },
        }
        expected_input_snapshot = {
            "status": "business_drift",
            "mode": "deep",
            "compared_at": "2026-06-28T05:00:00Z",
            "severity_counts": {
                "business_drift": 2,
                "critical_drift": 0,
                "incomplete": 1,
            },
        }

        store_error = RuntimeError("parity-status-store-unavailable")
        get_redis_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        redis_set_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def fake_redis_set(*args: object, **kwargs: object) -> None:
            redis_set_calls.append((args, dict(kwargs)))
            raise store_error

        fake_redis_client = SimpleNamespace(set=fake_redis_set)

        def fake_get_redis_client(*args: object, **kwargs: object) -> SimpleNamespace:
            get_redis_calls.append((args, dict(kwargs)))
            return fake_redis_client

        logger_spy = _LoggerSpy()
        record_summary_spy = MagicMock()
        audit_log_spy = MagicMock()

        unreturned_sentinel = object()
        returned_value: object = unreturned_sentinel

        with ExitStack() as stack:
            self._enter_shared_settings_patches(stack)
            stack.enter_context(
                patch.object(
                    sync_router.settings,
                    "sync_parity_status_max_age_seconds",
                    900,
                )
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "get_redis_client",
                    new=fake_get_redis_client,
                )
            )
            stack.enter_context(
                patch.object(
                    sync_router,
                    "record_sync_parity_summary",
                    new=record_summary_spy,
                )
            )
            stack.enter_context(
                patch.object(sync_router, "audit_log", new=audit_log_spy)
            )
            stack.enter_context(
                patch.object(sync_router, "logger", new=logger_spy)
            )
            with self.assertRaises(fastapi.HTTPException) as exc_ctx:
                returned_value = await sync_router.record_sync_parity_status(
                    request=self.status_request,
                    comparison=comparison,
                )

        exc = exc_ctx.exception
        self.assertEqual(exc.status_code, 503)
        self.assertEqual(exc.detail, "Could not store sync parity status")
        self.assertIs(exc.__cause__, store_error)

        self.assertEqual(get_redis_calls, [((), {})])

        self.assertEqual(len(redis_set_calls), 1)
        set_args, set_kwargs = redis_set_calls[0]
        self.assertEqual(len(set_args), 2)
        stored_value = set_args[1]
        self.assertIsInstance(stored_value, str)
        self.assertEqual(set_args, ("sync:parity:latest_comparison", stored_value))
        self.assertEqual(set_kwargs, {"ex": 86400})

        self.assertNotIn(", ", stored_value)
        self.assertNotIn(": ", stored_value)
        decoded_key_order = list(json.loads(stored_value))
        self.assertEqual(decoded_key_order, sorted(decoded_key_order))

        parsed_summary = json.loads(stored_value)
        self.assertIsInstance(parsed_summary, dict)

        self.assertEqual(parsed_summary["server_mode"], SERVER_MODE_SENTINEL)
        stored_at = parsed_summary["stored_at"]
        self.assertIsInstance(stored_at, str)
        self.assertTrue(stored_at.endswith("Z"))
        parsed_stored_at = datetime.fromisoformat(stored_at[:-1] + "+00:00")
        self.assertIsNotNone(parsed_stored_at.tzinfo)

        self.assertEqual(parsed_summary["status"], "business_drift")
        self.assertIs(parsed_summary["fresh"], False)
        self.assertEqual(parsed_summary["max_age_seconds"], 900)
        self.assertEqual(parsed_summary["mode"], "deep")
        self.assertEqual(parsed_summary["observed_at"], "2026-06-28T05:00:00Z")
        self.assertEqual(parsed_summary["table_count"], 0)
        self.assertEqual(parsed_summary["truncated_table_count"], 0)
        self.assertEqual(parsed_summary["duplicate_identity_count"], 0)
        self.assertEqual(parsed_summary["business_drift_count"], 2)
        self.assertEqual(parsed_summary["critical_drift_count"], 0)
        self.assertEqual(parsed_summary["incomplete_count"], 1)
        self.assertEqual(parsed_summary["non_business_difference_count"], 0)
        self.assertEqual(parsed_summary["artifact_metadata"], {"snapshot_mode": "deep"})
        self.assertIs(parsed_summary["artifact_metadata_complete"], False)

        expected_missing_fields = [
            "local_server_mode",
            "peer_server_mode",
            "local_release_sha",
            "peer_release_sha",
            "local_table_count",
            "peer_table_count",
            "local_snapshot_at",
            "peer_snapshot_at",
            "comparison_artifact_hash",
            "artifact_reference",
        ]
        self.assertEqual(len(expected_missing_fields), 10)
        self.assertEqual(
            expected_missing_fields,
            [
                field
                for field in sync_parity_observability.ARTIFACT_METADATA_REQUIRED_FIELDS
                if field != "snapshot_mode"
            ],
        )
        self.assertEqual(
            parsed_summary["artifact_metadata_missing_fields"],
            expected_missing_fields,
        )

        age_seconds = parsed_summary["age_seconds"]
        self.assertIsInstance(age_seconds, float)
        self.assertGreater(age_seconds, 0.0)

        expected_summary = {
            "status": "business_drift",
            "fresh": False,
            "age_seconds": age_seconds,
            "max_age_seconds": 900,
            "mode": "deep",
            "observed_at": "2026-06-28T05:00:00Z",
            "table_count": 0,
            "truncated_table_count": 0,
            "duplicate_identity_count": 0,
            "business_drift_count": 2,
            "critical_drift_count": 0,
            "incomplete_count": 1,
            "non_business_difference_count": 0,
            "artifact_metadata": {"snapshot_mode": "deep"},
            "artifact_metadata_complete": False,
            "artifact_metadata_missing_fields": expected_missing_fields,
            "server_mode": SERVER_MODE_SENTINEL,
            "stored_at": stored_at,
        }
        self.assertEqual(parsed_summary, expected_summary)
        self.assertEqual(
            stored_value,
            json.dumps(
                expected_summary,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

        expected_error_digest = hashlib.sha256(
            b"parity-status-store-unavailable"
        ).hexdigest()[:16]
        self.assertEqual(len(logger_spy.warning_calls), 1)
        warn_args, warn_kwargs = logger_spy.warning_calls[0]
        self.assertEqual(warn_args, ("Could not store sync parity status",))
        self.assertEqual(
            warn_kwargs,
            {
                "extra": {
                    "event": "sync.parity_status.store_failed",
                    "log_class": "integration",
                    "server_mode": SERVER_MODE_SENTINEL,
                    "error_type": "RuntimeError",
                    "error_digest": expected_error_digest,
                }
            },
        )

        self.assertEqual(logger_spy.info_calls, [])
        self.assertEqual(logger_spy.other_calls, [])

        record_summary_spy.assert_not_called()
        audit_log_spy.assert_not_called()

        self.assertIs(returned_value, unreturned_sentinel)
        self.assertNotIsInstance(returned_value, Mapping)
        self.assertEqual(comparison, expected_input_snapshot)

        self.assertIs(sync_router.record_sync_parity_status, orig_record_status)
        self.assertIs(sync_router._require_observability_key, orig_require_key)
        self.assertIs(
            sync_parity_observability.summarize_parity_comparison,
            orig_core_summarize,
        )
        self.assertIs(sync_router.summarize_parity_comparison, orig_router_summarize)
        self.assertIs(sync_router.get_redis_client, orig_get_redis_client)
        self.assertIs(sync_router.record_sync_parity_summary, orig_record_parity_summary)
        self.assertIs(sync_router.logger, orig_logger)
        self.assertIs(sync_router.audit_log, orig_audit_log)
        self.assertIs(
            sync_router.settings.observability_api_key,
            orig_observability_api_key,
        )
        self.assertIs(sync_router.settings.server_mode, orig_server_mode)
        self.assertIs(
            sync_router.settings.sync_parity_status_max_age_seconds,
            orig_max_age_seconds,
        )
        self.assertIs(sync_router.get_sync_parity_snapshot, orig_get_snapshot)
        self.assertIs(
            sync_router._coerce_parity_status_max_age_seconds,
            orig_coerce_max_age,
        )
        self.assertIs(sync_router._summarize_exception, orig_summarize_exception)


if __name__ == "__main__":
    unittest.main()
