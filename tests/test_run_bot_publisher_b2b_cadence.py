import unittest

from run_bot import publisher_b2b_dispatch_cycle_sleep_seconds


class PublisherB2BDispatchCadenceTests(unittest.TestCase):
    def test_partial_batch_accounts_for_network_time_inside_cadence(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=1,
                elapsed_seconds=0.3,
                batch_limit=8,
            ),
            0.2,
        )

    def test_slow_claimed_cycle_does_not_add_another_full_interval(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=1,
                elapsed_seconds=0.8,
                batch_limit=8,
            ),
            0.0,
        )

    def test_idle_cycle_keeps_the_same_cadence(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=0,
                elapsed_seconds=0.3,
                batch_limit=8,
            ),
            0.2,
        )

    def test_full_batch_drains_without_waiting_for_the_idle_interval(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=8,
                elapsed_seconds=0.1,
                batch_limit=8,
            ),
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
