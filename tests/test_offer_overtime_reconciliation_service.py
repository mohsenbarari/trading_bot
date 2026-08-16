import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import offer_overtime_reconciliation_service as recon
from core.services.offer_overtime_request_service import OvertimeRequestError, OvertimeRequestErrorCode
from models.offer import OfferStatus
from models.offer_request import OfferRequestStatus, OfferRequestWorkflow


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def make_row(**overrides):
    data = {
        "request_public_id": "orq_ot_1",
        "offer_public_id": "ofr_ot_1",
        "offer_owner_user_id": 7,
        "request_home_server": "foreign",
        "workflow_kind": OfferRequestWorkflow.OVERTIME,
        "result_status": OfferRequestStatus.OVERTIME_PRESENTED,
        "decision_deadline_at": NOW - timedelta(seconds=1),
        "received_at": NOW - timedelta(minutes=5),
        "created_at": NOW - timedelta(minutes=5),
        "telegram_message_id": 55,
        "telegram_delivery_job_id": None,
        "local_offer_id": 11,
        "offer": SimpleNamespace(
            id=11,
            status=OfferStatus.ACTIVE,
            created_at=NOW - timedelta(minutes=10),
            overtime_minutes_snapshot=5,
            offer_public_id="ofr_ot_1",
        ),
        "resulting_trade_id": None,
        "terminal_reason": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def scalars(self):
        return FakeScalars(self._rows)

    def scalar_one_or_none(self):
        return self._scalar

    def all(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, execute_results):
        self._results = list(execute_results)
        self.flushed = False

    async def execute(self, _stmt):
        if not self._results:
            raise AssertionError("unexpected execute")
        return self._results.pop(0)

    async def flush(self):
        self.flushed = True


class OvertimeReconciliationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_detects_overdue_presented_without_repair_in_dry_run(self):
        row = make_row()
        db = FakeDB(
            [
                FakeResult([row]),  # nonterminal scan
                FakeResult([]),  # completed_trade anomalies
                FakeResult([]),  # status counts
                FakeResult([]),  # silent owners
            ]
        )
        with patch.object(recon, "get_trading_settings", return_value=SimpleNamespace(offer_expiry_minutes=2)):
            report = await recon.reconcile_overtime_requests(
                db,
                dry_run=True,
                now=NOW,
                normal_lifetime_minutes=2,
            )
        self.assertTrue(report.dry_run)
        self.assertEqual(report.finding_counts.get("overdue_presented_decision"), 1)
        self.assertEqual(report.repaired, ())
        self.assertFalse(db.flushed)

    async def test_apply_repairs_overdue_presented_via_expire_decision(self):
        row = make_row()
        db = FakeDB(
            [
                FakeResult([row]),  # findings scan
                FakeResult([]),  # completed anomalies
                FakeResult([]),  # status counts
                FakeResult([]),  # silent owners
                FakeResult([row]),  # lock repair rows
            ]
        )
        expire = AsyncMock(return_value=row)
        with patch.object(recon, "get_trading_settings", return_value=SimpleNamespace(offer_expiry_minutes=2)), patch.object(
            recon, "expire_decision", new=expire
        ):
            report = await recon.reconcile_overtime_requests(
                db,
                dry_run=False,
                now=NOW,
                normal_lifetime_minutes=2,
            )
        self.assertEqual(len(report.repaired), 1)
        self.assertEqual(report.repaired[0].issue, "overdue_presented_decision")
        expire.assert_awaited_once()
        self.assertTrue(db.flushed)

    async def test_apply_skips_when_expire_decision_rejects(self):
        row = make_row()
        db = FakeDB(
            [
                FakeResult([row]),
                FakeResult([]),
                FakeResult([]),
                FakeResult([]),
                FakeResult([row]),
            ]
        )
        expire = AsyncMock(
            side_effect=OvertimeRequestError(
                OvertimeRequestErrorCode.ALREADY_TERMINAL,
                "done",
            )
        )
        with patch.object(recon, "get_trading_settings", return_value=SimpleNamespace(offer_expiry_minutes=2)), patch.object(
            recon, "expire_decision", new=expire
        ):
            report = await recon.reconcile_overtime_requests(
                db,
                dry_run=False,
                now=NOW,
                normal_lifetime_minutes=2,
            )
        self.assertEqual(report.repaired, ())
        self.assertTrue(any(item.issue == "overdue_presented_decision" for item in report.skipped))

    async def test_delivering_sweeper_invalidates_overdue_missing_message(self):
        row = make_row(
            result_status=OfferRequestStatus.OVERTIME_DELIVERING,
            telegram_message_id=None,
            received_at=NOW - timedelta(seconds=121),
        )
        db = FakeDB([FakeResult([row])])
        invalidate = AsyncMock(return_value=row)
        with patch.object(recon, "invalidate_request", new=invalidate):
            repaired = await recon.expire_overdue_delivering_requests(
                db,
                now=NOW,
                flush=True,
            )
        self.assertEqual(repaired, 1)
        invalidate.assert_awaited_once()
        self.assertEqual(
            invalidate.await_args.kwargs["reason"],
            "overtime_delivery_reconcile_timeout",
        )
        self.assertTrue(db.flushed)

    async def test_delivering_sweeper_skips_rows_inside_grace(self):
        row = make_row(
            result_status=OfferRequestStatus.OVERTIME_DELIVERING,
            telegram_message_id=None,
            received_at=NOW - timedelta(seconds=30),
        )
        db = FakeDB([FakeResult([row])])
        invalidate = AsyncMock(return_value=row)
        with patch.object(recon, "invalidate_request", new=invalidate):
            repaired = await recon.expire_overdue_delivering_requests(
                db,
                now=NOW,
                flush=True,
            )
        self.assertEqual(repaired, 0)
        invalidate.assert_not_awaited()
        self.assertFalse(db.flushed)

    async def test_observability_summary_omits_requester_identity(self):
        summary = {
            "status": "action_required",
            "server_mode": "foreign",
            "status_counts": {"overtime_presented": 1},
            "finding_counts": {"overdue_presented_decision": 1},
            "silent_owner_count": 2,
            "sampled_finding_count": 1,
        }
        with patch.object(
            recon,
            "reconcile_overtime_requests",
            new=AsyncMock(
                return_value=recon.OvertimeReconciliationReport(
                    dry_run=True,
                    findings=(
                        recon.OvertimeReconciliationFinding(
                            issue="overdue_presented_decision",
                            request_public_id="orq_x",
                            offer_public_id="ofr_x",
                            offer_owner_user_id=9,
                        ),
                    ),
                    repaired=(),
                    skipped=(),
                    finding_counts={"overdue_presented_decision": 1},
                    status_counts={"overtime_presented": 1},
                    silent_owner_count=2,
                )
            ),
        ):
            payload = await recon.overtime_observability_summary(
                object(),
                server_mode="foreign",
                now=NOW,
            )
        self.assertEqual(payload["silent_owner_count"], 2)
        serialized = str(payload)
        self.assertNotIn("requester", serialized.lower())
        self.assertNotIn("mobile", serialized.lower())


class OvertimeObservabilityPrivacyTests(unittest.TestCase):
    def test_safe_context_never_accepts_requester_fields(self):
        from core.overtime_observability import safe_overtime_log_context

        context = safe_overtime_log_context(
            event="create",
            result="queued",
            request_public_id="orq_1",
            offer_public_id="ofr_1",
            offer_owner_user_id=3,
        )
        self.assertEqual(context["request_public_id"], "orq_1")
        self.assertNotIn("requester_user_id", context)
        self.assertNotIn("mobile_number", context)


if __name__ == "__main__":
    unittest.main()
