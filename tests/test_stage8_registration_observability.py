import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.background_job_authority import (
    JOB_OTP_SMS_FALLBACK,
    JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
)
from core.log_redaction import REDACTED, redact
from core.metrics import metrics_response_body, record_registration_job_health, registry
from core.registration_observability import (
    JOB_HEARTBEAT_MAX_AGE_SECONDS,
    OTP_FALLBACK_MAX_LAG_SECONDS,
    REGISTRATION_PENDING_MAX_AGE_SECONDS,
    dual_platform_registration_health,
    record_registration_job_snapshot,
    refresh_registration_job_metrics,
    registration_health_log_fields,
    summarize_otp_fallback_queue,
    summarize_registration_intent_queue,
)
from scripts import report_production_alerts


NOW = datetime(2026, 7, 12, 4, 0, tzinfo=timezone.utc)


class FakeResult:
    def __init__(self, row):
        self.row = row

    def one(self):
        return self.row


class FakeDB:
    def __init__(self, row):
        self.row = row
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.row)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.zcard_value = 0
        self.zrange_value = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, **kwargs):
        self.values[key] = value
        return True

    async def zcard(self, _key):
        return self.zcard_value

    async def zrange(self, _key, _start, _stop, **_kwargs):
        return self.zrange_value


def settings_for(server_mode):
    return SimpleNamespace(
        server_mode=server_mode,
        telegram_registration_reconciliation_enabled=True,
        registration_sync_v2_enabled=True,
        telegram_login_otp_enabled=True,
        otp_sms_auto_fallback_enabled=True,
    )


class Stage8QueueSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_summary_is_bounded_and_outage_aware(self):
        oldest = NOW - timedelta(minutes=7)
        healthy = await summarize_registration_intent_queue(
            FakeDB((3, oldest, 0)),
            now=NOW,
        )
        outage = await summarize_registration_intent_queue(
            FakeDB((2, oldest, 1)),
            now=NOW,
        )

        self.assertEqual(healthy.pending_count, 3)
        self.assertEqual(healthy.oldest_pending_age_seconds, 420)
        self.assertTrue(healthy.connectivity_healthy)
        self.assertFalse(outage.connectivity_healthy)

    async def test_otp_summary_uses_only_sorted_set_count_and_oldest_score(self):
        redis = FakeRedis()
        redis.zcard_value = 4
        redis.zrange_value = [(b"opaque-request-id", NOW.timestamp() - 2.75)]

        summary = await summarize_otp_fallback_queue(redis, now=NOW)

        self.assertEqual(summary.pending_count, 4)
        self.assertAlmostEqual(summary.lag_seconds, 2.75)
        self.assertEqual(summary.oldest_pending_age_seconds, summary.lag_seconds)


class Stage8SnapshotTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        registry.reset()

    async def test_snapshot_preserves_last_error_and_updates_last_success(self):
        redis = FakeRedis()
        with patch("core.registration_observability.record_registration_job_health"):
            failed = await record_registration_job_snapshot(
                redis,
                job_name=JOB_OTP_SMS_FALLBACK,
                server_mode="iran",
                result="error",
                pending_count=2,
                oldest_pending_age_seconds=3,
                batch_size=0,
                batch_duration_ms=8,
                lag_seconds=3,
                error_code="TimeoutError",
                observed_at=NOW,
            )
            recovered = await record_registration_job_snapshot(
                redis,
                job_name=JOB_OTP_SMS_FALLBACK,
                server_mode="iran",
                result="success",
                pending_count=0,
                oldest_pending_age_seconds=0,
                batch_size=2,
                batch_duration_ms=4,
                observed_at=NOW + timedelta(seconds=1),
            )

        self.assertEqual(failed["last_error_code"], "timeouterror")
        self.assertEqual(recovered["last_error_at"], failed["last_error_at"])
        self.assertEqual(recovered["last_success_at"], (NOW + timedelta(seconds=1)).isoformat())
        serialized = json.dumps(recovered, sort_keys=True)
        for forbidden in ("09121112233", "12345", "invitation_token", "telegram_id", "address"):
            self.assertNotIn(forbidden, serialized)

    async def test_snapshot_serialization_rejects_nonfinite_numbers(self):
        redis = FakeRedis()
        with patch("core.registration_observability.record_registration_job_health"):
            snapshot = await record_registration_job_snapshot(
                redis,
                job_name=JOB_OTP_SMS_FALLBACK,
                server_mode="iran",
                result="success",
                pending_count=1,
                oldest_pending_age_seconds=float("inf"),
                batch_size=1,
                batch_duration_ms=float("nan"),
                lag_seconds=float("-inf"),
                observed_at=NOW,
            )

        self.assertEqual(snapshot["oldest_pending_age_seconds"], 0)
        self.assertEqual(snapshot["batch_duration_ms"], 0)
        self.assertEqual(snapshot["lag_seconds"], 0)
        serialized = next(iter(redis.values.values()))
        self.assertNotIn("Infinity", serialized)
        self.assertNotIn("NaN", serialized)

    async def test_health_exposes_role_aware_status_and_approved_thresholds(self):
        redis = FakeRedis()
        with patch("core.registration_observability.record_registration_job_health"):
            await record_registration_job_snapshot(
                redis,
                job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                server_mode="foreign",
                result="success",
                pending_count=1,
                oldest_pending_age_seconds=10,
                batch_size=1,
                batch_duration_ms=5,
                observed_at=NOW - timedelta(seconds=30),
            )

        health = await dual_platform_registration_health(
            redis,
            settings_obj=settings_for("foreign"),
            now=NOW,
        )
        jobs = health["jobs"]

        self.assertEqual(health["thresholds"], {
            "heartbeat_max_age_seconds": JOB_HEARTBEAT_MAX_AGE_SECONDS,
            "registration_pending_max_age_seconds": REGISTRATION_PENDING_MAX_AGE_SECONDS,
            "otp_fallback_max_lag_seconds": OTP_FALLBACK_MAX_LAG_SECONDS,
        })
        self.assertEqual(jobs[JOB_TELEGRAM_REGISTRATION_RECONCILIATION]["status"], "healthy")
        self.assertTrue(jobs[JOB_TELEGRAM_REGISTRATION_RECONCILIATION]["expected_on_this_server"])
        self.assertEqual(jobs[JOB_OTP_SMS_FALLBACK]["status"], "not_expected")

    async def test_stale_and_missing_heartbeats_are_explicit(self):
        redis = FakeRedis()
        with patch("core.registration_observability.record_registration_job_health"):
            await record_registration_job_snapshot(
                redis,
                job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
                server_mode="foreign",
                result="success",
                pending_count=0,
                oldest_pending_age_seconds=0,
                batch_size=0,
                batch_duration_ms=1,
                observed_at=NOW - timedelta(seconds=61),
            )

        foreign = await dual_platform_registration_health(
            redis, settings_obj=settings_for("foreign"), now=NOW
        )
        iran = await dual_platform_registration_health(
            FakeRedis(), settings_obj=settings_for("iran"), now=NOW
        )

        self.assertEqual(
            foreign["jobs"][JOB_TELEGRAM_REGISTRATION_RECONCILIATION]["status"],
            "stale",
        )
        self.assertEqual(iran["jobs"][JOB_OTP_SMS_FALLBACK]["status"], "missing")

    async def test_corrupt_snapshot_is_fail_safe_and_cannot_expose_arbitrary_error_text(self):
        redis = FakeRedis()
        key = "observability:registration_job:telegram_registration_reconciliation"
        redis.values[key] = json.dumps({
            "job_name": JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
            "server_mode": "not-a-server",
            "heartbeat_at": "invalid",
            "last_error_code": "09121112233 unsafe error",
            "pending_count": "not-a-number",
            "oldest_pending_age_seconds": float("inf"),
            "batch_duration_ms": float("nan"),
            "connectivity_healthy": "false",
        })

        health = await dual_platform_registration_health(
            redis, settings_obj=settings_for("foreign"), now=NOW
        )
        job = health["jobs"][JOB_TELEGRAM_REGISTRATION_RECONCILIATION]

        self.assertEqual(job["status"], "missing")
        self.assertEqual(job["last_error_code"], "internal_error")
        self.assertIsNone(job["last_result"])
        self.assertEqual(job["pending_count"], 0)
        self.assertEqual(job["oldest_pending_age_seconds"], 0)
        self.assertEqual(job["batch_duration_ms"], 0)
        self.assertFalse(job["connectivity_healthy"])

        redis.values[key] = b"\xff"
        missing = await dual_platform_registration_health(
            redis, settings_obj=settings_for("foreign"), now=NOW
        )
        self.assertEqual(
            missing["jobs"][JOB_TELEGRAM_REGISTRATION_RECONCILIATION]["status"],
            "missing",
        )

    async def test_metrics_have_only_bounded_job_and_server_labels(self):
        redis = FakeRedis()
        await record_registration_job_snapshot(
            redis,
            job_name=JOB_TELEGRAM_REGISTRATION_RECONCILIATION,
            server_mode="foreign",
            result="success",
            pending_count=2,
            oldest_pending_age_seconds=12,
            batch_size=1,
            batch_duration_ms=7,
            observed_at=NOW,
        )

        body = metrics_response_body()
        self.assertIn("trading_bot_registration_job_heartbeat_timestamp_seconds", body)
        self.assertIn('job_name="telegram_registration_reconciliation"', body)
        self.assertIn('server_mode="foreign"', body)
        for forbidden in ("mobile", "telegram_id", "user_name", "invitation_token", "address"):
            self.assertNotIn(forbidden, body)

    def test_metrics_reject_nonfinite_snapshot_values(self):
        record_registration_job_health({
            "job_name": JOB_OTP_SMS_FALLBACK,
            "server_mode": "iran",
            "heartbeat_at": "9999999999-01-01T00:00:00+00:00",
            "pending_count": 1,
            "oldest_pending_age_seconds": float("inf"),
            "batch_duration_ms": float("nan"),
            "lag_seconds": float("-inf"),
            "last_result": "success",
        })

        body = metrics_response_body()
        self.assertNotIn("+Inf", body)
        self.assertNotIn("-Inf", body)
        self.assertNotIn("NaN", body)

    async def test_metrics_scrape_can_hydrate_snapshot_in_another_api_worker(self):
        redis = FakeRedis()
        with patch("core.registration_observability.record_registration_job_health") as record:
            await record_registration_job_snapshot(
                redis,
                job_name=JOB_OTP_SMS_FALLBACK,
                server_mode="iran",
                result="success",
                pending_count=1,
                oldest_pending_age_seconds=0,
                batch_size=0,
                batch_duration_ms=1,
                observed_at=NOW,
            )
            record.reset_mock()
            await refresh_registration_job_metrics(redis)

        record.assert_called_once()
        self.assertEqual(record.call_args.args[0]["job_name"], JOB_OTP_SMS_FALLBACK)
        self.assertFalse(record.call_args.kwargs["count_cycle"])

    async def test_flat_log_fields_survive_secret_redaction(self):
        redis = FakeRedis()
        health = await dual_platform_registration_health(
            redis, settings_obj=settings_for("iran"), now=NOW
        )

        fields = registration_health_log_fields(health)
        redacted = redact(fields)

        self.assertNotIn("otp", " ".join(fields).lower())
        self.assertEqual(redacted, fields)
        self.assertNotIn(REDACTED, redacted.values())


class Stage8AlertTests(unittest.TestCase):
    def _host(self, registration, otp):
        return {
            "role": "foreign",
            "postgres": {},
            "redis": {},
            "disk": {"filesystems": []},
            "backup": {"ok": True, "latest_status": "ok", "age_seconds": 1},
            "sync": {
                "unsynced_change_log_count": 0,
                "oldest_unsynced_age_seconds": 0,
                "redis_queues": {},
                "registration_jobs": {
                    "jobs": {
                        JOB_TELEGRAM_REGISTRATION_RECONCILIATION: registration,
                        JOB_OTP_SMS_FALLBACK: otp,
                    }
                },
            },
        }

    def test_alerts_cover_heartbeat_healthy_pending_and_otp_lag(self):
        host = self._host(
            {
                "enabled": True,
                "expected_on_this_server": True,
                "heartbeat_age_seconds": 61,
                "oldest_pending_age_seconds": 301,
                "connectivity_healthy": True,
            },
            {
                "enabled": True,
                "expected_on_this_server": True,
                "heartbeat_age_seconds": None,
                "lag_seconds": 2.1,
            },
        )

        alerts = report_production_alerts.evaluate_host_report(host)
        metrics = {item["metric"] for item in alerts}

        self.assertIn("telegram_registration_reconciliation:heartbeat_age_seconds", metrics)
        self.assertIn("otp_sms_fallback:heartbeat_age_seconds", metrics)
        self.assertIn("telegram_registration_reconciliation:oldest_pending_age_seconds", metrics)
        self.assertIn("otp_sms_fallback:lag_seconds", metrics)

    def test_registration_age_alert_is_suppressed_during_connectivity_outage(self):
        host = self._host(
            {
                "enabled": True,
                "expected_on_this_server": True,
                "heartbeat_age_seconds": 1,
                "oldest_pending_age_seconds": 900,
                "connectivity_healthy": False,
            },
            {"enabled": False, "expected_on_this_server": False},
        )

        alerts = report_production_alerts.evaluate_host_report(host)

        self.assertNotIn(
            "telegram_registration_reconciliation:oldest_pending_age_seconds",
            {item["metric"] for item in alerts},
        )


class Stage8AuditVocabularyTests(unittest.TestCase):
    def test_otp_audit_outcomes_use_existing_strict_vocabulary(self):
        from api.routers.auth import _otp_audit_result

        self.assertEqual(_otp_audit_result("accepted"), "success")
        self.assertEqual(_otp_audit_result("sent"), "success")
        self.assertEqual(_otp_audit_result("not_linked"), "denied")
        self.assertEqual(_otp_audit_result("ambiguous"), "failure")


if __name__ == "__main__":
    unittest.main()
