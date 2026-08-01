from __future__ import annotations

import unittest
from unittest.mock import patch

from core.external_effect_execution_gate import ExternalEffectExecutionGateError
from core import offer_publication_worker
from core import telegram_admin_broadcast_worker
from core import telegram_notification_outbox_worker
from core import trade_delivery_worker


class ExternalEffectExecutionGateWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_worker_cannot_recover_or_claim_when_gate_is_blocked(self) -> None:
        with patch(
            "core.telegram_notification_outbox_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_notification_outbox_worker.require_application_writer_term",
            return_value=None,
        ), patch(
            "core.telegram_notification_outbox_worker.require_external_effect_execution_authorization",
            side_effect=ExternalEffectExecutionGateError("authorization expired"),
        ), patch("core.telegram_notification_outbox_worker.AsyncSessionLocal") as sessions, patch(
            "core.telegram_notification_outbox_worker.claim_and_deliver_next_telegram_notification_outbox"
        ) as claim:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await telegram_notification_outbox_worker.run_telegram_notification_outbox_delivery_cycle(limit=1)

        sessions.assert_not_called()
        claim.assert_not_called()

    async def test_admin_broadcast_worker_cannot_recover_or_claim_when_gate_is_blocked(self) -> None:
        with patch(
            "core.telegram_admin_broadcast_worker.assert_background_job_authority"
        ), patch(
            "core.telegram_admin_broadcast_worker.require_application_writer_term",
            return_value=None,
        ), patch(
            "core.telegram_admin_broadcast_worker.require_external_effect_execution_authorization",
            side_effect=ExternalEffectExecutionGateError("authorization expired"),
        ), patch("core.telegram_admin_broadcast_worker.AsyncSessionLocal") as sessions, patch(
            "core.telegram_admin_broadcast_worker.claim_and_deliver_next_telegram_admin_broadcast_receipt"
        ) as claim:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await telegram_admin_broadcast_worker.run_telegram_admin_broadcast_delivery_cycle(limit=1)

        sessions.assert_not_called()
        claim.assert_not_called()

    async def test_trade_delivery_worker_cannot_recover_or_claim_when_gate_is_blocked(self) -> None:
        with patch("core.trade_delivery_worker.assert_background_job_authority"), patch(
            "core.trade_delivery_worker.require_application_writer_term",
            return_value=None,
        ), patch(
            "core.trade_delivery_worker.require_external_effect_execution_authorization",
            side_effect=ExternalEffectExecutionGateError("authorization expired"),
        ), patch("core.trade_delivery_worker.AsyncSessionLocal") as sessions, patch(
            "core.trade_delivery_worker.claim_and_deliver_next_telegram_receipt"
        ) as claim:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await trade_delivery_worker.run_telegram_trade_delivery_cycle(limit=1)

        sessions.assert_not_called()
        claim.assert_not_called()

    async def test_offer_publication_worker_cannot_reconcile_or_send_when_gate_is_blocked(self) -> None:
        with patch("core.offer_publication_worker.assert_background_job_authority"), patch(
            "core.offer_publication_worker.require_application_writer_term",
            return_value=None,
        ), patch(
            "core.offer_publication_worker.require_external_effect_execution_authorization",
            side_effect=ExternalEffectExecutionGateError("authorization expired"),
        ), patch("core.offer_publication_worker.AsyncSessionLocal") as sessions, patch(
            "core.offer_publication_worker.reconcile_offer_publications"
        ) as reconcile:
            with self.assertRaisesRegex(ExternalEffectExecutionGateError, "expired"):
                await offer_publication_worker.run_offer_telegram_publication_cycle(limit=1)

        sessions.assert_not_called()
        reconcile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
