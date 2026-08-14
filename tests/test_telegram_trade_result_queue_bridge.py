import unittest
from types import SimpleNamespace

from core.telegram_delivery_trade_result_binding import (
    trade_result_queue_job_id_from_receipt,
    trade_result_queue_reconciliation_job_id_from_receipt,
    trade_result_queue_reconciliation_worker_id,
    trade_result_queue_receipt_worker_id,
    trade_result_receipt_is_bound_to_job,
)


class TelegramTradeResultQueueBindingTests(unittest.TestCase):
    def test_marker_round_trip_is_strict_and_job_specific(self):
        receipt = SimpleNamespace(
            worker_id=trade_result_queue_receipt_worker_id(987654)
        )

        self.assertEqual(
            trade_result_queue_job_id_from_receipt(receipt),
            987654,
        )
        self.assertTrue(trade_result_receipt_is_bound_to_job(receipt, 987654))
        self.assertFalse(trade_result_receipt_is_bound_to_job(receipt, 987655))

    def test_foreign_or_malformed_worker_ids_are_not_adopted(self):
        for worker_id in (
            None,
            "telegram-trade-delivery",
            "telegram-delivery-queue-v1:trade-result:",
            "telegram-delivery-queue-v1:trade-result:0",
            "telegram-delivery-queue-v1:trade-result:-1",
            "telegram-delivery-queue-v1:trade-result:1.5",
            "telegram-delivery-queue-v1:trade-result:۱۲",
        ):
            with self.subTest(worker_id=worker_id):
                self.assertIsNone(
                    trade_result_queue_job_id_from_receipt(
                        SimpleNamespace(worker_id=worker_id)
                    )
                )

    def test_marker_builder_rejects_non_positive_or_implicit_integer_values(self):
        for job_id in (None, True, 0, -1, "1"):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                trade_result_queue_receipt_worker_id(job_id)

    def test_reconciliation_marker_is_durable_but_never_a_queue_binding(self):
        receipt = SimpleNamespace(
            worker_id=trade_result_queue_reconciliation_worker_id(4242)
        )

        self.assertEqual(
            trade_result_queue_reconciliation_job_id_from_receipt(receipt),
            4242,
        )
        self.assertIsNone(trade_result_queue_job_id_from_receipt(receipt))
        self.assertFalse(trade_result_receipt_is_bound_to_job(receipt, 4242))

if __name__ == "__main__":
    unittest.main()
