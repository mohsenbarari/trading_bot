"""Hermetic unit tests for two-site terminal capacity classifier."""

from __future__ import annotations

import math
import unittest

from scripts.two_site_terminal_capacity_classifier import (
    AUTOPILOT_ERROR_MESSAGE,
    CAPACITY_MESSAGE,
    classify_terminal_capacity_stream,
    failed_run_matches_repository,
)


class TestTerminalCapacityClassifier(unittest.TestCase):
    def setUp(self) -> None:
        self.valid_thread_id = "thread_test_123"
        self.five_event_stream = (
            b'{"type":"thread.started","thread_id":"thread_test_123"}\n'
            b'{"type":"item.completed","item":{"type":"error","id":"item_err_1","message":"Rate limit warning"}}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.four_event_stream = (
            b'{"type":"thread.started","thread_id":"thread_test_123"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.expected_head = "6b9ed96828a3fbf5e85bde014c345dbdd652abd4"
        self.expected_refs = {
            "refs/heads/main": "6b9ed96828a3fbf5e85bde014c345dbdd652abd4",
            "refs/tags/v1.0.0": "d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5",
        }
        self.expected_scope = {
            "assignment_id": "WA-AE03-CAPACITY-CLASSIFIER-20260930-001",
            "stage_id": "P1-00b",
            "sha256": "cf476b101211d4044efe8167193c5f42b98c9c406c012ea8e0a62483a5868c5b",
        }
        self.valid_status = {
            "state": "FAILED",
            "head_before": self.expected_head,
            "protected_refs": dict(self.expected_refs),
            "assignment_scope": dict(self.expected_scope),
            "started_at": 1727680000.0,
            "finished_at": 1727680010.5,
            "error": AUTOPILOT_ERROR_MESSAGE,
            "cycle": 1,
            "pid": 4242,
            "exit_code": 1,
        }

    def test_same_observed_incident_success(self) -> None:
        self.assertTrue(classify_terminal_capacity_stream(self.five_event_stream))
        self.assertTrue(
            failed_run_matches_repository(
                self.valid_status,
                self.expected_head,
                self.expected_refs,
                self.expected_scope,
            )
        )

    def test_four_event_variant_success(self) -> None:
        self.assertTrue(classify_terminal_capacity_stream(self.four_event_stream))

    def test_stream_adversarial_duplicated_and_different_errors(self) -> None:
        # Duplicate error event
        dup_error = (
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(dup_error))

        # Different error message in error event
        diff_error = (
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Rate limit exceeded."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(diff_error))

        # Different error message in turn.failed
        diff_turn_failed = (
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Rate limit exceeded."}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(diff_turn_failed))

    def test_stream_adversarial_missing_terminal_and_extra_events(self) -> None:
        # Missing turn.failed terminal event
        missing_terminal = (
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(missing_terminal))

        # Extra event appended
        extra_event = self.five_event_stream + b'{"type":"turn.completed"}\n'
        self.assertFalse(classify_terminal_capacity_stream(extra_event))

        # Reordered events
        reordered = (
            b'{"type":"turn.started"}\n'
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(reordered))

    def test_stream_adversarial_tool_agent_and_extra_keys(self) -> None:
        # Extra tool key on top-level event
        stream_with_tool = (
            b'{"type":"thread.started","thread_id":"t1","tool":"bash"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(stream_with_tool))

        # Extra agent key on turn.started
        stream_with_agent = (
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"turn.started","agent":"researcher"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(stream_with_agent))

        # Extra output key in turn.failed error
        stream_with_output = (
            b'{"type":"thread.started","thread_id":"t1"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model.","output":"fail"}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(stream_with_output))

    def test_stream_adversarial_bad_json_utf8_oversize_newlines(self) -> None:
        # Non-bytes
        self.assertFalse(classify_terminal_capacity_stream("not-bytes"))  # type: ignore[arg-type]
        # Empty bytes
        self.assertFalse(classify_terminal_capacity_stream(b""))
        # Missing final newline
        self.assertFalse(classify_terminal_capacity_stream(self.four_event_stream[:-1]))
        # Double final newline
        self.assertFalse(classify_terminal_capacity_stream(self.four_event_stream + b"\n"))
        # Carriage return
        self.assertFalse(classify_terminal_capacity_stream(self.four_event_stream.replace(b"\n", b"\r\n")))
        # Invalid JSON
        bad_json = self.four_event_stream.replace(b'"turn.started"', b'{invalid}')
        self.assertFalse(classify_terminal_capacity_stream(bad_json))
        # Duplicate keys
        dup_key_stream = (
            b'{"type":"thread.started","thread_id":"t1","thread_id":"t2"}\n'
            b'{"type":"turn.started"}\n'
            b'{"type":"error","message":"Selected model is at capacity. Please try a different model."}\n'
            b'{"type":"turn.failed","error":{"message":"Selected model is at capacity. Please try a different model."}}\n'
        )
        self.assertFalse(classify_terminal_capacity_stream(dup_key_stream))
        # Non-UTF-8 bytes
        self.assertFalse(classify_terminal_capacity_stream(b"\xff\xfe\n\n\n\n"))
        # Oversized
        oversized = self.four_event_stream + (b" " * 70000) + b"\n"
        self.assertFalse(classify_terminal_capacity_stream(oversized))

    def test_status_adversarial_head_refs_and_scope(self) -> None:
        # Wrong head_before
        bad_head = dict(self.valid_status, head_before="0000000000000000000000000000000000000000")
        self.assertFalse(
            failed_run_matches_repository(bad_head, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Mismatched protected_refs
        bad_refs = dict(self.valid_status, protected_refs={"refs/heads/main": "different_sha"})
        self.assertFalse(
            failed_run_matches_repository(bad_refs, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Scope missing sha256
        bad_scope_dict = {"assignment_id": "A1", "stage_id": "S1"}
        bad_scope = dict(self.valid_status, assignment_scope=bad_scope_dict)
        self.assertFalse(
            failed_run_matches_repository(bad_scope, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Scope with extra keys
        extra_scope = dict(self.expected_scope, extra="value")
        status_extra_scope = dict(self.valid_status, assignment_scope=extra_scope)
        self.assertFalse(
            failed_run_matches_repository(status_extra_scope, self.expected_head, self.expected_refs, self.expected_scope)
        )

    def test_status_adversarial_state_and_generic_exit(self) -> None:
        # Wrong state SUCCESS
        bad_state = dict(self.valid_status, state="SUCCESS")
        self.assertFalse(
            failed_run_matches_repository(bad_state, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Generic exit=1 error without AutopilotError prefix
        generic_exit = dict(self.valid_status, error="Codex CLI exited with status 1")
        self.assertFalse(
            failed_run_matches_repository(generic_exit, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Short exit status
        short_exit = dict(self.valid_status, error="exit status 1")
        self.assertFalse(
            failed_run_matches_repository(short_exit, self.expected_head, self.expected_refs, self.expected_scope)
        )

    def test_status_adversarial_timestamps(self) -> None:
        # started_at > finished_at
        inverted_time = dict(self.valid_status, started_at=1727680050.0, finished_at=1727680010.0)
        self.assertFalse(
            failed_run_matches_repository(inverted_time, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # NaN timestamp
        nan_time = dict(self.valid_status, started_at=float("nan"))
        self.assertFalse(
            failed_run_matches_repository(nan_time, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Infinite timestamp
        inf_time = dict(self.valid_status, finished_at=float("inf"))
        self.assertFalse(
            failed_run_matches_repository(inf_time, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # String timestamp
        str_time = dict(self.valid_status, started_at="2026-09-30T00:00:00Z")
        self.assertFalse(
            failed_run_matches_repository(str_time, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Boolean timestamp
        bool_time = dict(self.valid_status, started_at=True)
        self.assertFalse(
            failed_run_matches_repository(bool_time, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Identical started_at and finished_at is valid
        same_time = dict(self.valid_status, started_at=1727680000, finished_at=1727680000)
        self.assertTrue(
            failed_run_matches_repository(same_time, self.expected_head, self.expected_refs, self.expected_scope)
        )

    def test_status_adversarial_result_receipt_claims(self) -> None:
        # Result claim present
        result_claim = dict(self.valid_status, result={"status": "SUCCESS"})
        self.assertFalse(
            failed_run_matches_repository(result_claim, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Receipt claim present
        receipt_claim = dict(self.valid_status, receipt="receipt_tx_999")
        self.assertFalse(
            failed_run_matches_repository(receipt_claim, self.expected_head, self.expected_refs, self.expected_scope)
        )

        # Result None (no claim) is accepted alongside valid telemetry keys
        no_claim = dict(self.valid_status, result=None, hostname="builder-01", attempt=1)
        self.assertTrue(
            failed_run_matches_repository(no_claim, self.expected_head, self.expected_refs, self.expected_scope)
        )

    def test_status_invalid_types_and_malformed(self) -> None:
        # Non-dict status
        self.assertFalse(
            failed_run_matches_repository("FAILED", self.expected_head, self.expected_refs, self.expected_scope)  # type: ignore[arg-type]
        )
        # Non-string expected head
        self.assertFalse(
            failed_run_matches_repository(self.valid_status, None, self.expected_refs, self.expected_scope)  # type: ignore[arg-type]
        )
        # Non-dict expected refs
        self.assertFalse(
            failed_run_matches_repository(self.valid_status, self.expected_head, [], self.expected_scope)  # type: ignore[arg-type]
        )
        # Non-dict expected scope
        self.assertFalse(
            failed_run_matches_repository(self.valid_status, self.expected_head, self.expected_refs, None)  # type: ignore[arg-type]
        )


if __name__ == "__main__":
    unittest.main()
