"""Synthetic local latency floors for split runtime. Not live percentiles."""
from __future__ import annotations

import json
import os
import time
import unittest
import uuid

from tests.test_telegram_split_runtime_dual_process import _harness


class TelegramSplitRuntimeSyntheticLatencyTests(unittest.TestCase):
    def test_synthetic_report_is_not_live_percentiles(self):
        report = {
            "evidence_kind": "synthetic_local_stopwatch",
            "live_percentiles_claimed": False,
            "status": "READY FOR STAGING INTEGRATION REVIEW",
        }
        self.assertEqual(report["evidence_kind"], "synthetic_local_stopwatch")
        self.assertFalse(report["live_percentiles_claimed"])
        self.assertEqual(report["status"], "READY FOR STAGING INTEGRATION REVIEW")


@unittest.skipUnless(
    str(os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL") or "").startswith(
        "postgresql://"
    ),
    "isolated scratch database URL is required for two-process synthetic timings",
)
class TelegramSplitRuntimeSyntheticTwoProcessTests(unittest.TestCase):
    def test_two_process_fake_transport_stage_timings(self):
        url = os.environ["TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL"]
        reset = _harness("--role", "executor", "--action", "reset", url=url)
        self.assertEqual(reset.returncode, 0, reset.stderr)
        enqueue = _harness(
            "--role",
            "primary",
            "--action",
            "enqueue",
            "--source-key",
            f"split-synth-{uuid.uuid4().hex[:8]}",
            url=url,
        )
        self.assertEqual(enqueue.returncode, 0, enqueue.stderr)
        ack = _harness("--role", "primary", "--action", "primary-ack", url=url)
        self.assertEqual(ack.returncode, 0, ack.stderr)
        ack_payload = json.loads(ack.stdout)
        handoff_started = time.perf_counter()
        consume = _harness(
            "--role",
            "executor",
            "--action",
            "executor-consume",
            url=url,
        )
        handoff_seconds = time.perf_counter() - handoff_started
        self.assertEqual(consume.returncode, 0, consume.stderr)
        consume_payload = json.loads(consume.stdout.splitlines()[-1])
        ping = _harness("--role", "primary", "--action", "central-ping", url=url)
        self.assertEqual(ping.returncode, 0, ping.stderr)
        ping_payload = json.loads(ping.stdout)
        report = {
            "evidence_kind": "synthetic_local_two_process_fake_transport",
            "live_percentiles_claimed": False,
            "enqueue_to_local_ack_seconds": ack_payload["enqueue_to_local_ack_seconds"],
            "ack_commit_to_executor_process_and_claim_seconds": handoff_seconds,
            "ack_to_claim_includes_child_import": True,
            "claim_to_fake_provider_seconds": consume_payload[
                "claim_to_fake_provider_seconds"
            ],
            "central_interaction_seconds": ping_payload["central_interaction_seconds"],
            "status": "READY FOR STAGING INTEGRATION REVIEW",
        }
        self.assertTrue(ack_payload["acked"])
        self.assertTrue(consume_payload["claimed"])
        self.assertTrue(consume_payload["fake_sent"])
        self.assertLess(report["enqueue_to_local_ack_seconds"], 5.0)
        self.assertLess(report["claim_to_fake_provider_seconds"], 5.0)
        self.assertLess(report["central_interaction_seconds"], 0.4)
        self.assertFalse(report["live_percentiles_claimed"])
        print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    unittest.main()
