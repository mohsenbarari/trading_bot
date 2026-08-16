import unittest

from core.telegram_delivery_cutover_contract import (
    api_process_contract,
    bot_process_contract,
    executor_overlap_forbidden,
    missing_required_env,
    present_forbidden_tokens,
)


class TelegramDeliveryCutoverContractTests(unittest.TestCase):
    def test_api_contract_rejects_queue_worker_and_tokens(self):
        contract = api_process_contract()
        self.assertEqual(
            missing_required_env(
                {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                },
                contract,
            ),
            (
                "TELEGRAM_DELIVERY_EXECUTION_OWNER",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
                "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
            ),
        )
        forbidden = contract.forbidden_token_keys[5]
        self.assertEqual(
            present_forbidden_tokens({forbidden: True}, contract),
            (forbidden,),
        )

    def test_bot_contract_requires_queue_owner_and_five_lane_parent_flags(self):
        contract = bot_process_contract()
        self.assertEqual(
            missing_required_env(
                {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "true",
                    "TELEGRAM_B2B_DISPATCH_ENABLED": "true",
                },
                contract,
            ),
            (),
        )
        self.assertFalse(contract.forbidden_token_keys)

    def test_legacy_and_queue_workers_may_not_overlap(self):
        self.assertTrue(
            executor_overlap_forbidden(
                legacy_workers_enabled=True,
                queue_worker_enabled=True,
            )
        )
        self.assertFalse(
            executor_overlap_forbidden(
                legacy_workers_enabled=False,
                queue_worker_enabled=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
