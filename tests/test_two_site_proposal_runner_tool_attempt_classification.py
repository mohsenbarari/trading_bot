from __future__ import annotations

import json
import unittest

from scripts import two_site_proposal_runner
from scripts.two_site_proposal_runner import (
    ForbiddenToolAttemptError,
    RetryableProviderSessionError,
    _has_forbidden_stream_evidence,
    _is_explicit_forbidden_evidence,
    _parse_stream,
)

WORKER_MODEL = getattr(
    two_site_proposal_runner,
    "WORKER_MODEL",
    getattr(two_site_proposal_runner, "MODEL", "claude-opus-4-6-thinking"),
)


def _build_stream(
    events: list[dict],
    conversation_id: str = "session-classify-test-001",
    model: str = WORKER_MODEL,
    envelope_override: dict | None = None,
) -> str:
    lines: list[str] = []
    lines.append(
        json.dumps({
            "event": "init",
            "conversation_id": conversation_id,
            "init": {"model": model},
        })
    )
    for ev in events:
        lines.append(json.dumps(ev))
    envelope = {
        "conversation_id": conversation_id,
        "status": "SUCCESS",
        "structured_output": {
            "status": "PROPOSAL_READY",
            "summary": "ok",
            "covered_requirements": [1],
            "findings": [],
            "changes": [],
        },
    }
    if envelope_override:
        envelope.update(envelope_override)
    lines.append(
        json.dumps({
            "event": "result",
            "conversation_id": conversation_id,
            "result": envelope,
        })
    )
    return "\n".join(lines) + "\n"


class TestToolAttemptClassification(unittest.TestCase):
    def test_explicit_forbidden_evidence_helper_positives(self) -> None:
        self.assertTrue(_is_explicit_forbidden_evidence({"tool_name": "run_command"}))
        self.assertTrue(_is_explicit_forbidden_evidence({"tool_name": "view_file"}))
        self.assertTrue(
            _is_explicit_forbidden_evidence({"tool_info": {"name": "run_command"}})
        )
        self.assertTrue(_is_explicit_forbidden_evidence({"step_type": "subagent"}))
        self.assertTrue(_is_explicit_forbidden_evidence({"subagent_info": {}}))
        self.assertTrue(
            _is_explicit_forbidden_evidence({"subagent_info": {"name": "worker"}})
        )

    def test_explicit_forbidden_evidence_helper_negatives(self) -> None:
        self.assertFalse(_is_explicit_forbidden_evidence({"tool_name": "finish"}))
        self.assertFalse(
            _is_explicit_forbidden_evidence({"tool_info": {"name": "finish"}})
        )
        self.assertFalse(_is_explicit_forbidden_evidence({"tool_name": ""}))
        self.assertFalse(_is_explicit_forbidden_evidence({"tool_info": {}}))
        self.assertFalse(_is_explicit_forbidden_evidence({"step_type": "tool"}))
        self.assertFalse(_is_explicit_forbidden_evidence({}))
        self.assertFalse(_is_explicit_forbidden_evidence(None))
        self.assertFalse(_is_explicit_forbidden_evidence("non-dict"))

    def test_has_forbidden_stream_evidence(self) -> None:
        self.assertFalse(_has_forbidden_stream_evidence([]))
        self.assertFalse(
            _has_forbidden_stream_evidence([
                {
                    "event": "step_update",
                    "step_update": {"tool_name": "finish"},
                }
            ])
        )
        self.assertTrue(
            _has_forbidden_stream_evidence([
                {
                    "event": "step_update",
                    "step_update": {"tool_name": "run_command"},
                }
            ])
        )
        self.assertTrue(
            _has_forbidden_stream_evidence([{"tool_name": "run_command"}])
        )
        self.assertTrue(
            _has_forbidden_stream_evidence([
                {"event": "step_update", "step_update": {"step_type": "subagent"}}
            ])
        )

    def test_explicit_run_command_step_raises_forbidden_tool_attempt_error(self) -> None:
        cid = "session-run-cmd-001"
        stream = _build_stream(
            events=[
                {
                    "event": "step_update",
                    "conversation_id": cid,
                    "step_update": {
                        "conversation_id": cid,
                        "tool_name": "run_command",
                    },
                }
            ],
            conversation_id=cid,
        )
        with self.assertRaises(ForbiddenToolAttemptError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIn(
            "tool attempt invalidates a proposal-only run", str(exc_info.exception)
        )
        self.assertIsInstance(exc_info.exception, RetryableProviderSessionError)

    def test_explicit_tool_info_step_raises_forbidden_tool_attempt_error(self) -> None:
        cid = "session-tool-info-001"
        stream = _build_stream(
            events=[
                {
                    "event": "step_update",
                    "conversation_id": cid,
                    "step_update": {
                        "conversation_id": cid,
                        "tool_info": {"name": "run_command"},
                    },
                }
            ],
            conversation_id=cid,
        )
        with self.assertRaises(ForbiddenToolAttemptError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIn(
            "tool attempt invalidates a proposal-only run", str(exc_info.exception)
        )

    def test_explicit_subagent_step_type_raises_forbidden_tool_attempt_error(self) -> None:
        cid = "session-subagent-type-001"
        stream = _build_stream(
            events=[
                {
                    "event": "step_update",
                    "conversation_id": cid,
                    "step_update": {
                        "conversation_id": cid,
                        "step_type": "subagent",
                    },
                }
            ],
            conversation_id=cid,
        )
        with self.assertRaises(ForbiddenToolAttemptError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIn(
            "tool attempt invalidates a proposal-only run", str(exc_info.exception)
        )

    def test_explicit_subagent_info_step_raises_forbidden_tool_attempt_error(self) -> None:
        cid = "session-subagent-info-001"
        stream = _build_stream(
            events=[
                {
                    "event": "step_update",
                    "conversation_id": cid,
                    "step_update": {
                        "conversation_id": cid,
                        "subagent_info": {"name": "code_researcher"},
                    },
                }
            ],
            conversation_id=cid,
        )
        with self.assertRaises(ForbiddenToolAttemptError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIn(
            "tool attempt invalidates a proposal-only run", str(exc_info.exception)
        )

    def test_denied_actions_without_forbidden_event_raises_generic_retryable_session_error(
        self,
    ) -> None:
        cid = "session-denied-generic-001"
        stream = _build_stream(
            events=[],
            conversation_id=cid,
            envelope_override={"denied_actions": ["some_denied_action"]},
        )
        with self.assertRaises(RetryableProviderSessionError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIs(type(exc_info.exception), RetryableProviderSessionError)
        self.assertFalse(isinstance(exc_info.exception, ForbiddenToolAttemptError))
        self.assertIn(
            "CLI did not complete one successful session", str(exc_info.exception)
        )

    def test_denied_actions_with_forbidden_event_raises_typed_forbidden_tool_attempt_error(
        self,
    ) -> None:
        cid = "session-denied-forbidden-001"
        stream = _build_stream(
            events=[
                {
                    "event": "step_update",
                    "conversation_id": cid,
                    "step_update": {
                        "conversation_id": cid,
                        "tool_name": "run_command",
                    },
                }
            ],
            conversation_id=cid,
            envelope_override={"denied_actions": ["run_command"]},
        )
        with self.assertRaises(ForbiddenToolAttemptError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIn(
            "tool attempt invalidates a proposal-only run", str(exc_info.exception)
        )

    def test_missing_or_empty_tool_name_event_raises_generic_retryable_session_error(
        self,
    ) -> None:
        cid = "session-malformed-tool-001"
        stream = _build_stream(
            events=[
                {
                    "event": "tool",
                    "conversation_id": cid,
                    "tool_name": "",
                }
            ],
            conversation_id=cid,
        )
        with self.assertRaises(RetryableProviderSessionError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIs(type(exc_info.exception), RetryableProviderSessionError)
        self.assertFalse(isinstance(exc_info.exception, ForbiddenToolAttemptError))

    def test_finish_tool_name_on_event_raises_generic_retryable_session_error(
        self,
    ) -> None:
        cid = "session-finish-on-event-001"
        stream = _build_stream(
            events=[
                {
                    "event": "tool",
                    "conversation_id": cid,
                    "tool_name": "finish",
                }
            ],
            conversation_id=cid,
        )
        with self.assertRaises(RetryableProviderSessionError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIs(type(exc_info.exception), RetryableProviderSessionError)
        self.assertFalse(isinstance(exc_info.exception, ForbiddenToolAttemptError))

    def test_unnamed_tool_step_type_event_raises_generic_retryable_session_error(
        self,
    ) -> None:
        cid = "session-unnamed-tool-001"
        stream = _build_stream(
            events=[
                {
                    "event": "tool",
                    "conversation_id": cid,
                    "step_type": "tool",
                }
            ],
            conversation_id=cid,
        )
        with self.assertRaises(RetryableProviderSessionError) as exc_info:
            _parse_stream(stream, {}, {}, role="worker", expected_model=WORKER_MODEL)
        self.assertIs(type(exc_info.exception), RetryableProviderSessionError)
        self.assertFalse(isinstance(exc_info.exception, ForbiddenToolAttemptError))


if __name__ == "__main__":
    unittest.main()
