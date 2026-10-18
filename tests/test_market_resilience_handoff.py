import copy
import unittest
from scripts.handoff_market_receiver_resilience import validate_prior_runtime


class PriorProcessorRepairTests(unittest.TestCase):
    def prior(self):
        release = "a" * 40
        old = {"Config": {"Env": ["MARKET_PIPELINE_RELEASE_SHA=" + release],
                           "Labels": {"org.opencontainers.image.revision": release}},
               "State": {"Running": False, "Status": "restarting", "ExitCode": 1, "OOMKilled": False},
               "RestartCount": 100}
        health = {"schema": "market_processor/4.0", "role": "market-processor", "release_sha": release}
        return release, old, health

    def test_explicit_verified_exit_one_loop_can_be_repaired(self):
        release, old, health = self.prior()
        validate_prior_runtime(old, "market-processor", release, health)

    def test_stopped_oom_unknown_release_and_other_roles_are_refused(self):
        release, old, health = self.prior()
        for field, value in (("Status", "exited"), ("ExitCode", 137), ("OOMKilled", True)):
            changed = copy.deepcopy(old)
            changed["State"][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                validate_prior_runtime(changed, "market-processor", release, health)
        with self.assertRaises(RuntimeError):
            validate_prior_runtime(old, "market-fact-receiver", release, health)
        with self.assertRaises(RuntimeError):
            validate_prior_runtime(old, "market-processor", "b" * 40, health)
        with self.assertRaises(RuntimeError):
            validate_prior_runtime(old, "market-processor", release, {**health, "schema": "unknown"})

    def test_ordinary_handoff_does_not_implicitly_accept_crashed_prior(self):
        _, old, health = self.prior()
        with self.assertRaises(RuntimeError):
            validate_prior_runtime(old, "market-processor", None, health)
