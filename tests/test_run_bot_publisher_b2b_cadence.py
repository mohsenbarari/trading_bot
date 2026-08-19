import unittest

from run_bot import publisher_b2b_dispatch_cycle_sleep_seconds


class PublisherB2BDispatchCadenceTests(unittest.TestCase):
    def test_claimed_cycle_accounts_for_network_time_inside_cadence(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=1,
                elapsed_seconds=0.3,
            ),
            0.2,
        )

    def test_slow_claimed_cycle_does_not_add_another_full_interval(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=1,
                elapsed_seconds=0.8,
            ),
            0.0,
        )

    def test_idle_cycle_does_not_add_a_second_interval(self):
        self.assertEqual(
            publisher_b2b_dispatch_cycle_sleep_seconds(
                interval_seconds=0.5,
                claimed_count=0,
                elapsed_seconds=0.3,
            ),
            0.2,
        )


if __name__ == "__main__":
    unittest.main()
