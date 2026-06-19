import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services import offer_publication_reconciliation_service as service
from models.offer import OfferStatus
from models.offer_publication_state import OfferPublicationStatus, OfferPublicationSurface


class FakeDB:
    def __init__(self):
        self.added = []
        self.commit = AsyncMock()

    def add(self, item):
        self.added.append(item)


class FakeSummaryResult:
    def __init__(self, *, rows=None, scalar_value=0):
        self._rows = list(rows or [])
        self._scalar_value = scalar_value

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar_value


class FakeSummaryDB:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _stmt):
        if not self.results:
            raise AssertionError("Unexpected execute call")
        return self.results.pop(0)


def make_offer(**overrides):
    data = {
        "id": 7,
        "offer_public_id": "ofr_7",
        "home_server": "foreign",
        "status": OfferStatus.ACTIVE,
        "version_id": 2,
        "channel_message_id": None,
        "user": SimpleNamespace(id=11),
        "commodity": SimpleNamespace(id=3),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_state(**overrides):
    data = {
        "id": 70,
        "status": OfferPublicationStatus.FAILED,
        "telegram_message_id": None,
        "surface": OfferPublicationSurface.TELEGRAM_CHANNEL,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class OfferPublicationReconciliationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_reports_foreign_publication_findings_without_repair(self):
        offer = make_offer()
        candidate = service.PublicationReconciliationCandidate(
            issue="failed_telegram_publication",
            offer=offer,
            state=make_state(),
            surface=OfferPublicationSurface.TELEGRAM_CHANNEL,
        )

        with patch(
            "core.services.offer_publication_reconciliation_service.load_foreign_telegram_reconciliation_candidates",
            new=AsyncMock(return_value=[candidate]),
        ):
            report = await service.reconcile_offer_publications(FakeDB(), server_mode="foreign", dry_run=True)

        self.assertEqual(report["status"], "action_required")
        self.assertEqual(report["processed"], 1)
        self.assertEqual(report["findings"][0]["result"], "reported")
        self.assertEqual(report["findings"][0]["issue"], "failed_telegram_publication")

    async def test_foreign_repair_retries_failed_telegram_publication(self):
        db = FakeDB()
        offer = make_offer()
        candidate = service.PublicationReconciliationCandidate(
            issue="failed_telegram_publication",
            offer=offer,
            state=make_state(),
            surface=OfferPublicationSurface.TELEGRAM_CHANNEL,
        )

        with patch(
            "core.services.offer_publication_reconciliation_service.load_foreign_telegram_reconciliation_candidates",
            new=AsyncMock(return_value=[candidate]),
        ), patch(
            "core.services.offer_publication_reconciliation_service.publish_offer_to_telegram_channel_once",
            new=AsyncMock(return_value=SimpleNamespace(message_id=555, skipped_reason=None, error_code=None)),
        ) as publish_mock:
            report = await service.reconcile_offer_publications(
                db,
                server_mode="foreign",
                dry_run=False,
                send_offer_to_channel=AsyncMock(return_value=555),
            )

        self.assertEqual(report["status"], "repaired")
        self.assertEqual(report["repaired"], 1)
        publish_mock.assert_awaited_once()
        db.commit.assert_awaited_once()

    async def test_foreign_repair_backfills_offer_message_id_from_publication_state(self):
        db = FakeDB()
        offer = make_offer(channel_message_id=None)
        state = make_state(status=OfferPublicationStatus.SENT, telegram_message_id=777)
        candidate = service.PublicationReconciliationCandidate(
            issue="offer_missing_legacy_message_id",
            offer=offer,
            state=state,
            surface=OfferPublicationSurface.TELEGRAM_CHANNEL,
        )

        with patch(
            "core.services.offer_publication_reconciliation_service.load_foreign_telegram_reconciliation_candidates",
            new=AsyncMock(return_value=[candidate]),
        ):
            report = await service.reconcile_offer_publications(db, server_mode="foreign", dry_run=False)

        self.assertEqual(report["status"], "repaired")
        self.assertEqual(offer.channel_message_id, 777)
        self.assertEqual(report["findings"][0]["reason"], "legacy_offer_message_id_backfilled")
        db.commit.assert_awaited_once()

    async def test_iran_repair_creates_visible_webapp_publication_state(self):
        db = FakeDB()
        offer = make_offer(home_server="iran")
        candidate = service.PublicationReconciliationCandidate(
            issue="active_offer_without_webapp_state",
            offer=offer,
            state=None,
            surface=OfferPublicationSurface.WEBAPP_MARKET,
        )

        with patch(
            "core.services.offer_publication_reconciliation_service.load_iran_webapp_reconciliation_candidates",
            new=AsyncMock(return_value=[candidate]),
        ):
            report = await service.reconcile_offer_publications(db, server_mode="iran", dry_run=False)

        self.assertEqual(report["status"], "repaired")
        self.assertEqual(report["repaired"], 1)
        self.assertEqual(db.added[0].surface, OfferPublicationSurface.WEBAPP_MARKET)
        self.assertEqual(db.added[0].publication_owner_server, "iran")
        self.assertEqual(db.added[0].status, OfferPublicationStatus.VISIBLE)
        db.commit.assert_awaited_once()

    async def test_reconciliation_is_idempotent_when_no_candidates_exist(self):
        db = FakeDB()
        with patch(
            "core.services.offer_publication_reconciliation_service.load_foreign_telegram_reconciliation_candidates",
            new=AsyncMock(return_value=[]),
        ):
            report = await service.reconcile_offer_publications(db, server_mode="foreign", dry_run=False)

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["processed"], 0)
        db.commit.assert_not_awaited()

    async def test_publication_summary_reports_state_counts_and_sync_backlog_findings(self):
        db = FakeSummaryDB(
            FakeSummaryResult(rows=[("telegram_channel", "failed", 2), ("webapp_market", "visible", 5)]),
            FakeSummaryResult(scalar_value=1),
            FakeSummaryResult(scalar_value=2),
            FakeSummaryResult(scalar_value=0),
            FakeSummaryResult(scalar_value=1),
            FakeSummaryResult(scalar_value=0),
            FakeSummaryResult(scalar_value=3),
            FakeSummaryResult(scalar_value=0),
        )

        summary = await service.publication_observability_summary(
            db,
            server_mode="foreign",
            unsynced_by_table={"offer_publication_states": 4, "offers": 6},
        )

        self.assertEqual(summary["status"], "action_required")
        self.assertEqual(summary["state_counts"]["telegram_channel"]["failed"], 2)
        self.assertEqual(summary["state_counts"]["webapp_market"]["visible"], 5)
        self.assertEqual(summary["finding_counts"]["failed_telegram_publication"], 2)
        self.assertEqual(summary["finding_counts"]["active_offer_without_webapp_state"], 3)
        self.assertEqual(summary["finding_counts"]["unsynced_publication_state_backlog"], 4)
        self.assertEqual(summary["finding_counts"]["unsynced_offer_backlog"], 6)


if __name__ == "__main__":
    unittest.main()
