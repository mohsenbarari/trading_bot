from collections import Counter
from dataclasses import replace
import hashlib
import json
import unittest

from core.telegram_queue_stage4_workload import (
    Stage4WorkloadValidationError,
    build_stage4_workload,
    validate_stage4_workload,
)


FIXTURE = {
    "schema_version": 1,
    "source": "production_read_only_sanitized",
    "commodities": [
        {"key": "commodity-a", "active": True},
        {"key": "commodity-b", "active": True},
        {"key": "commodity-c", "active": True},
    ],
}


class TelegramQueueStage4WorkloadTests(unittest.TestCase):
    def test_three_seeded_traces_have_exact_business_quotas(self):
        hashes = set()
        for seed in (2026071501, 2026071502, 2026071503):
            workload = build_stage4_workload(seed=seed, fixture=FIXTURE)
            summary = validate_stage4_workload(
                workload,
                commodities=("commodity-a", "commodity-b", "commodity-c"),
            )
            hashes.add(workload.trace_sha256)
            self.assertEqual(summary["valid_offers"], 1800)
            self.assertEqual(summary["invalid_offers"], 400)
            self.assertEqual(summary["lot_shapes"], {"none": 900, "three": 450, "two": 450})
            self.assertEqual(summary["manual_expiry_requests"], 180)
            self.assertEqual(summary["expiry_trade_races"], 18)
            self.assertEqual(summary["trade_modes"], {
                "concurrent": 162,
                "normal": 270,
                "partial_then_complete": 108,
            })
            self.assertEqual(summary["admin_jobs"], 125)
            self.assertEqual(summary["active_owners"], 80)
            peak_seconds = {
                second
                for start, end in workload.peak_windows
                for second in range(start, end)
            }
            per_second = Counter(
                int(event["at_ms"]) // 1000
                for event in workload.events
                if event["event_type"] == "offer_submit_valid"
            )
            self.assertTrue(all(8 <= per_second[second] <= 12 for second in peak_seconds))
        self.assertEqual(len(hashes), 3)

    def test_lifecycle_events_never_precede_submission_across_many_seeds(self):
        for seed in range(1, 26):
            workload = build_stage4_workload(seed=seed, fixture=FIXTURE)
            submissions = {
                event["business_event_id"]: int(event["at_ms"])
                for event in workload.events
                if event["event_type"] == "offer_submit_valid"
            }
            dependent = {
                "offer_confirm",
                "trade_request",
                "manual_expiry_request",
            }
            self.assertTrue(
                all(
                    int(event["at_ms"])
                    > submissions[event["business_event_id"]]
                    for event in workload.events
                    if event["event_type"] in dependent
                ),
                seed,
            )

    def test_validator_rejects_causally_impossible_lifecycle_trace(self):
        workload = build_stage4_workload(seed=3, fixture=FIXTURE)
        events = list(workload.events)
        submission_times = {
            event["business_event_id"]: int(event["at_ms"])
            for event in events
            if event["event_type"] == "offer_submit_valid"
        }
        target_index = next(
            index
            for index, event in enumerate(events)
            if event["event_type"] == "manual_expiry_request"
        )
        target = events[target_index]
        events[target_index] = {
            **target,
            "at_ms": submission_times[target["business_event_id"]],
        }
        events.sort(key=lambda row: (int(row["at_ms"]), str(row["event_id"])))
        rendered = "\n".join(
            json.dumps(
                event,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for event in events
        ) + "\n"
        mutated = replace(
            workload,
            events=tuple(events),
            trace_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        )
        with self.assertRaisesRegex(
            Stage4WorkloadValidationError,
            "lifecycle_event_precedes_submission",
        ):
            validate_stage4_workload(
                mutated,
                commodities=("commodity-a", "commodity-b", "commodity-c"),
            )

    def test_fixture_rejects_identity_fields(self):
        unsafe = {
            **FIXTURE,
            "commodities": [
                {"key": "commodity-a", "active": True, "user_id": 99}
            ],
        }
        with self.assertRaisesRegex(
            Stage4WorkloadValidationError,
            "contains_identity_field",
        ):
            build_stage4_workload(seed=1, fixture=unsafe)

    def test_mutated_trace_fails_closed(self):
        workload = build_stage4_workload(seed=1, fixture=FIXTURE)
        events = tuple(
            event
            for event in workload.events
            if event["event_id"] != "invalid-001"
        )
        with self.assertRaisesRegex(
            Stage4WorkloadValidationError,
            "trace_hash_mismatch",
        ):
            validate_stage4_workload(
                replace(workload, events=events),
                commodities=("commodity-a", "commodity-b", "commodity-c"),
            )

    def test_trace_hash_rejects_mutated_event_even_when_quotas_still_match(self):
        workload = build_stage4_workload(seed=2, fixture=FIXTURE)
        events = list(workload.events)
        events[0] = {**events[0], "at_ms": int(events[0]["at_ms"]) + 1}
        with self.assertRaisesRegex(
            Stage4WorkloadValidationError,
            "trace_hash_mismatch",
        ):
            validate_stage4_workload(
                replace(workload, events=tuple(events)),
                commodities=("commodity-a", "commodity-b", "commodity-c"),
            )


if __name__ == "__main__":
    unittest.main()
