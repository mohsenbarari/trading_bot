"""Unit tests for RetryableProviderSessionError classification in two_site_proposal_runner."""
from __future__ import annotations

import json
import unittest

try:
    from scripts.two_site_proposal_runner import (
        MODEL,
        ProposalError,
        RetryableProviderSessionError,
        parse_stream,
    )
except ImportError:
    from two_site_proposal_runner import (
        MODEL,
        ProposalError,
        RetryableProviderSessionError,
        parse_stream,
    )

VALID_TASK = {
    "task_id": "T1",
    "goal": "Test goal",
    "context_files": ["file.py"],
    "allowed_files": ["file.py"],
    "requirements": ["req1"],
    "test_selectors": ["test1"],
}

VALID_SOURCES = {
    "file.py": {
        "sha256": "abc123oldhash",
        "content": "old_file_content\n",
    }
}

VALID_PROPOSAL = {
    "status": "PROPOSAL_READY",
    "summary": "Implement minimal change",
    "covered_requirements": [1],
    "findings": [],
    "changes": [
        {
            "path": "file.py",
            "original_sha256": "abc123oldhash",
            "content": "new_file_content\n",
            "replacements": [],
        }
    ],
}


def make_stream(
    terminal_envelope: dict,
    session: str = "session-test-id",
    model: str = MODEL,
    extra_events: list[dict] | None = None,
) -> str:
    events = [
        {"event": "init", "conversation_id": session, "init": {"model": model}}
    ]
    if extra_events:
        events.extend(extra_events)
    events.append({
        "event": "result",
        "conversation_id": session,
        "result": terminal_envelope,
    })
    return "\n".join(json.dumps(e) for e in events) + "\n"


class TestRetryableProviderSessionError(unittest.TestCase):
    def test_subtype_hierarchy(self):
        self.assertTrue(issubclass(RetryableProviderSessionError, ProposalError))
        self.assertTrue(issubclass(RetryableProviderSessionError, ValueError))

    def test_denied_actions_raises_retryable(self):
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "denied_actions": [{"action": "tool_call", "tool": "run_command"}],
            "response": json.dumps(VALID_PROPOSAL),
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertIn("CLI did not complete one successful session", str(ctx.exception))

    def test_non_finish_tool_event_raises_retryable(self):
        tool_step = {
            "event": "step_update",
            "conversation_id": "session-test-id",
            "step_update": {
                "conversation_id": "session-test-id",
                "step_type": "tool",
                "tool_name": "run_command",
                "tool_info": {
                    "name": "run_command",
                    "parameters": {"command": "echo test"},
                },
            },
        }
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": json.dumps(VALID_PROPOSAL),
        }
        raw = make_stream(envelope, extra_events=[tool_step])
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertIn("tool attempt invalidates a proposal-only run", str(ctx.exception))

    def test_missing_response_text_raises_retryable(self):
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertIn("CLI result has no response text", str(ctx.exception))

    def test_ordinary_error_status_not_retryable(self):
        envelope = {
            "conversation_id": "session-test-id",
            "status": "ERROR",
            "error": "Generic engine failure",
        }
        raw = make_stream(envelope)
        with self.assertRaises(ProposalError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertNotIsInstance(ctx.exception, RetryableProviderSessionError)
        self.assertIn("CLI did not complete one successful session", str(ctx.exception))

    def test_conversation_mismatch_not_retryable(self):
        envelope = {
            "conversation_id": "different-session-id",
            "status": "SUCCESS",
            "response": json.dumps(VALID_PROPOSAL),
        }
        raw = make_stream(envelope, session="session-test-id")
        with self.assertRaises(ProposalError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertNotIsInstance(ctx.exception, RetryableProviderSessionError)
        self.assertIn("CLI did not complete one successful session", str(ctx.exception))

    def test_model_output_truncated_not_retryable(self):
        envelope = {
            "conversation_id": "session-test-id",
            "status": "ERROR",
            "error": "output tokens limit exceeded",
        }
        raw = make_stream(envelope)
        with self.assertRaises(ProposalError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertNotIsInstance(ctx.exception, RetryableProviderSessionError)
        self.assertIn("MODEL_OUTPUT_TRUNCATED: rescope to a smaller task", str(ctx.exception))

    def test_invalid_proposal_content_not_retryable(self):
        invalid_proposal = dict(VALID_PROPOSAL)
        invalid_proposal["covered_requirements"] = []
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": json.dumps(invalid_proposal),
        }
        raw = make_stream(envelope)
        with self.assertRaises(ProposalError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertNotIsInstance(ctx.exception, RetryableProviderSessionError)
        self.assertIn("success omits requirements or has unresolved findings", str(ctx.exception))

    def test_canonical_finish_submission_allowed(self):
        finish_event = {
            "event": "step_update",
            "conversation_id": "session-test-id",
            "step_update": {
                "conversation_id": "session-test-id",
                "step_type": "tool",
                "tool_name": "finish",
                "tool_info": {
                    "name": "finish",
                    "parameters": VALID_PROPOSAL,
                },
            },
        }
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": json.dumps(VALID_PROPOSAL),
        }
        raw = make_stream(envelope, extra_events=[finish_event])
        result, env = parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(result["status"], "PROPOSAL_READY")
        self.assertEqual(env["conversation_id"], "session-test-id")


if __name__ == "__main__":
    unittest.main()
