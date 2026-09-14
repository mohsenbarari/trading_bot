"""Unit tests for RetryableProviderSessionError classification in two_site_proposal_runner."""
from __future__ import annotations

import json
import unittest

try:
    from scripts.two_site_proposal_runner import (
        MODEL,
        ProposalError,
        RetryableProviderOutputError,
        RetryableProviderSessionError,
        is_retryable_zero_token_stream,
        parse_stream,
    )
except ImportError:
    from two_site_proposal_runner import (
        MODEL,
        ProposalError,
        RetryableProviderOutputError,
        RetryableProviderSessionError,
        is_retryable_zero_token_stream,
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
        "sha256": "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
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
            "original_sha256": "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
            "content": "new_file_content\n",
            "replacements": [],
        }
    ],
}

EXPECTED_NORMALIZED_PROPOSAL = {
    "status": "PROPOSAL_READY",
    "summary": "Implement minimal change",
    "covered_requirements": [1],
    "findings": [],
    "changes": [
        {
            "path": "file.py",
            "original_sha256": "1355e9731dc401dd2f73831607210ce164884eb2bda373d314b40239303ca3f2",
            "content": "new_file_content\n",
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
        self.assertTrue(issubclass(RetryableProviderOutputError, RetryableProviderSessionError))

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

    def test_bad_function_call_error_is_narrowly_retryable(self):
        envelope = {
            "conversation_id": "session-test-id",
            "status": "ERROR",
            "error": "Your previous response contained an improperly formatted function call.",
        }
        with self.assertRaises(RetryableProviderOutputError) as ctx:
            parse_stream(make_stream(envelope), VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual("provider returned an invalid response envelope", str(ctx.exception))

    def test_bad_function_call_with_external_gate_is_not_retryable(self):
        envelope = {
            "conversation_id": "session-test-id",
            "status": "ERROR",
            "error": "Quota limit; previous response had an improperly formatted function call.",
        }
        with self.assertRaises(ProposalError) as ctx:
            parse_stream(make_stream(envelope), VALID_TASK, VALID_SOURCES, "worker")
        self.assertNotIsInstance(ctx.exception, RetryableProviderSessionError)

    def test_empty_success_transport_is_narrowly_retryable(self):
        invalid = dict(VALID_PROPOSAL)
        invalid["changes"] = []
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": json.dumps(invalid),
        }
        with self.assertRaises(RetryableProviderOutputError) as ctx:
            parse_stream(make_stream(envelope), VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual("provider returned unusable proposal transport", str(ctx.exception))

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


def make_zero_token_stream(
    mutator: object = None,
    *,
    session: str = "session-test-id",
    result_top_cid: object = None,
) -> str:
    res_ev: dict[str, object] = {
        "event": "result",
        "result": {
            "conversation_id": session,
            "status": "ERROR",
            "error": "Agent execution terminated due to error.",
            "response": "",
            "denied_actions": [],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        },
    }
    if result_top_cid is not None:
        res_ev["conversation_id"] = result_top_cid
    events: list[object] = [
        {"event": "init", "conversation_id": session, "init": {"model": MODEL}},
        res_ev,
    ]
    if callable(mutator):
        res = mutator(events)
        if res is not None:
            events = res
    elif isinstance(mutator, list):
        events = mutator
    elif isinstance(mutator, str):
        return mutator
    return "\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n"


class TestIsRetryableZeroTokenStream(unittest.TestCase):
    def test_retained_cycle_59_shape_and_result_id_variants(self):
        self.assertTrue(is_retryable_zero_token_stream(make_zero_token_stream()))
        self.assertTrue(
            is_retryable_zero_token_stream(
                make_zero_token_stream(
                    lambda ev: ev[1]["result"].update(response=None, denied_actions=None)
                )
            )
        )
        self.assertTrue(
            is_retryable_zero_token_stream(
                make_zero_token_stream(result_top_cid="session-test-id")
            )
        )
        self.assertFalse(
            is_retryable_zero_token_stream(
                make_zero_token_stream(result_top_cid="mismatched-session")
            )
        )
        self.assertFalse(is_retryable_zero_token_stream(None))  # type: ignore[arg-type]
        self.assertFalse(is_retryable_zero_token_stream(""))
        self.assertFalse(is_retryable_zero_token_stream("   \n"))
        self.assertFalse(
            is_retryable_zero_token_stream(
                make_zero_token_stream(), expected_model="other-model"
            )
        )

    def test_rejections_via_mutation_table(self):
        mutations: list[tuple[str, object]] = [
            ("missing_init", lambda ev: [ev[1]]),
            ("duplicate_init", lambda ev: [ev[0], ev[0], ev[1]]),
            ("malformed_init", lambda ev: ev[0].update(init="not-a-dict")),
            ("missing_result", lambda ev: [ev[0]]),
            ("duplicate_result", lambda ev: [ev[0], ev[1], ev[1]]),
            ("malformed_result", lambda ev: ev[1].update(result="not-a-dict")),
            ("non_dict_event", lambda ev: ["not-a-dict", ev[1]]),
            ("malformed_step_update", lambda ev: ev.insert(1, {"event": "step_update", "step_update": "not-a-dict"})),
            ("wrong_model", lambda ev: ev[0]["init"].update(model="gemini-other")),
            ("missing_session", lambda ev: ev[0].pop("conversation_id")),
            ("empty_session", lambda ev: ev[0].update(conversation_id="   ")),
            ("non_string_session", lambda ev: ev[0].update(conversation_id=123)),
            ("mismatched_result_envelope_session", lambda ev: ev[1]["result"].update(conversation_id="wrong-session")),
            ("mismatched_other_event_session", lambda ev: ev.insert(1, {"event": "step_update", "conversation_id": "other"})),
            ("malformed_ndjson", "{not-valid-json\n"),
            ("duplicate_key_ndjson", '{"event": "init", "event": "init"}\n'),
            ("absent_usage", lambda ev: ev[1]["result"].pop("usage")),
            ("empty_usage", lambda ev: ev[1]["result"].update(usage={})),
            ("non_dict_usage_int", lambda ev: ev[1]["result"].update(usage=0)),
            ("non_dict_usage_str", lambda ev: ev[1]["result"].update(usage="none")),
            ("non_dict_usage_list", lambda ev: ev[1]["result"].update(usage=[0])),
            ("boolean_false_usage", lambda ev: ev[1]["result"].update(usage={"prompt_tokens": False})),
            ("boolean_true_usage", lambda ev: ev[1]["result"].update(usage={"prompt_tokens": True})),
            ("nonzero_usage_positive", lambda ev: ev[1]["result"].update(usage={"prompt_tokens": 1})),
            ("nonzero_usage_negative", lambda ev: ev[1]["result"].update(usage={"prompt_tokens": -1})),
            ("nonzero_usage_mixed", lambda ev: ev[1]["result"].update(usage={"prompt_tokens": 0, "completion_tokens": 2})),
            ("structured_output_dict", lambda ev: ev[1]["result"].update(structured_output={"res": 1})),
            ("structured_output_bool", lambda ev: ev[1]["result"].update(structured_output=True)),
            ("nonempty_response", lambda ev: ev[1]["result"].update(response="unexpected text")),
            ("non_string_response", lambda ev: ev[1]["result"].update(response={"text": "hi"})),
            ("nonempty_denials", lambda ev: ev[1]["result"].update(denied_actions=[{"action": "bash"}])),
            ("event_tool_name", lambda ev: ev.insert(1, {"tool_name": "run_command"})),
            ("event_tool_info", lambda ev: ev.insert(1, {"tool_info": {"name": "run_command"}})),
            ("event_subagent_info", lambda ev: ev.insert(1, {"subagent_info": {"id": "sub-1"}})),
            ("event_step_type_tool", lambda ev: ev.insert(1, {"step_type": "tool"})),
            ("event_step_type_subagent", lambda ev: ev.insert(1, {"step_type": "subagent"})),
            ("step_update_tool_name", lambda ev: ev.insert(1, {"event": "step_update", "step_update": {"tool_name": "x"}})),
            ("step_update_tool_info", lambda ev: ev.insert(1, {"event": "step_update", "step_update": {"tool_info": {}}})),
            ("step_update_subagent_info", lambda ev: ev.insert(1, {"event": "step_update", "step_update": {"subagent_info": {}}})),
            ("step_update_step_type_tool", lambda ev: ev.insert(1, {"event": "step_update", "step_update": {"step_type": "tool"}})),
            ("step_update_step_type_subagent", lambda ev: ev.insert(1, {"event": "step_update", "step_update": {"step_type": "subagent"}})),
            ("status_not_error", lambda ev: ev[1]["result"].update(status="SUCCESS")),
            ("error_auth", lambda ev: ev[1]["result"].update(error="Authentication failure")),
            ("error_quota", lambda ev: ev[1]["result"].update(error="Quota exceeded for project")),
            ("error_rate_limit", lambda ev: ev[1]["result"].update(error="Rate limit exceeded")),
            ("error_timeout", lambda ev: ev[1]["result"].update(error="Gateway time-out")),
            ("error_deadline", lambda ev: ev[1]["result"].update(error="Deadline exceeded")),
            ("error_network", lambda ev: ev[1]["result"].update(error="Network unreachable")),
            ("error_connect", lambda ev: ev[1]["result"].update(error="Failed to connect")),
            ("error_truncation", lambda ev: ev[1]["result"].update(error="output tokens limit exceeded")),
            ("error_function_call", lambda ev: ev[1]["result"].update(error="improperly formatted function call")),
            ("error_arbitrary_engine", lambda ev: ev[1]["result"].update(error="Internal server engine error")),
        ]
        for name, mutator in mutations:
            with self.subTest(name=name):
                raw = make_zero_token_stream(mutator)
                self.assertFalse(is_retryable_zero_token_stream(raw), f"Expected false for {name}")


class TestIdenticalJsonResponseDeduplication(unittest.TestCase):
    def test_unchanged_valid_single_response(self):
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": json.dumps(VALID_PROPOSAL),
        }
        raw = make_stream(envelope)
        result, env = parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(result, EXPECTED_NORMALIZED_PROPOSAL)
        self.assertEqual(env["status"], "SUCCESS")
        self.assertEqual(env["conversation_id"], "session-test-id")

    def test_exactly_two_identical_worker_documents_accepted(self):
        multi = f"{json.dumps(VALID_PROPOSAL)}\n{json.dumps(VALID_PROPOSAL)}"
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        result, env = parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(result, EXPECTED_NORMALIZED_PROPOSAL)
        self.assertEqual(env["status"], "SUCCESS")

    def test_exactly_four_identical_worker_documents_accepted(self):
        multi = "\n\n".join([json.dumps(VALID_PROPOSAL)] * 4)
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        result, env = parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(result, EXPECTED_NORMALIZED_PROPOSAL)
        self.assertEqual(env["status"], "SUCCESS")

    def test_exactly_four_identical_verifier_documents_with_empty_changes_accepted(self):
        verifier_proposal = {
            "status": "VERIFIED",
            "summary": "Verified proposal payload.",
            "covered_requirements": [1],
            "findings": [],
            "changes": [],
        }
        multi = "\n".join([json.dumps(verifier_proposal)] * 4)
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        result, env = parse_stream(raw, VALID_TASK, VALID_SOURCES, "verifier")
        self.assertEqual(result, verifier_proposal)
        self.assertEqual(env["status"], "SUCCESS")

    def test_five_identical_documents_rejected(self):
        multi = "\n".join([json.dumps(VALID_PROPOSAL)] * 5)
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(str(ctx.exception), "CLI response is not one valid JSON document")

    def test_differing_field_rejected(self):
        different_proposal = dict(VALID_PROPOSAL, summary="Differing summary text")
        multi = f"{json.dumps(VALID_PROPOSAL)}\n{json.dumps(different_proposal)}"
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(str(ctx.exception), "CLI response is not one valid JSON document")

    def test_int_vs_float_rejected(self):
        float_proposal = dict(VALID_PROPOSAL, covered_requirements=[1.0])
        multi = f"{json.dumps(VALID_PROPOSAL)}\n{json.dumps(float_proposal)}"
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(str(ctx.exception), "CLI response is not one valid JSON document")

    def test_duplicate_key_in_later_document_rejected(self):
        dup_later = (
            '{"status": "PROPOSAL_READY", "summary": "first", "summary": "second", '
            '"covered_requirements": [1], "findings": [], "changes": []}'
        )
        multi = f"{json.dumps(VALID_PROPOSAL)}\n{dup_later}"
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(str(ctx.exception), "CLI response is not one valid JSON document")

    def test_trailing_prose_rejected(self):
        multi = f"{json.dumps(VALID_PROPOSAL)}\n{json.dumps(VALID_PROPOSAL)}\nHere is extra prose commentary."
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(str(ctx.exception), "CLI response is not one valid JSON document")

    def test_malformed_later_json_rejected(self):
        multi = f"{json.dumps(VALID_PROPOSAL)}\n" + '{"status": "PROPOSAL_READY", "summary": incomplete'
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(str(ctx.exception), "CLI response is not one valid JSON document")

    def test_no_whitespace_direct_concatenation_rejected(self):
        multi = f"{json.dumps(VALID_PROPOSAL)}{json.dumps(VALID_PROPOSAL)}"
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(RetryableProviderSessionError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertEqual(str(ctx.exception), "CLI response is not one valid JSON document")

    def test_duplicate_key_in_first_document_preserves_proposal_error(self):
        dup_first = (
            '{"status": "PROPOSAL_READY", "summary": "first", "summary": "second", '
            '"covered_requirements": [1], "findings": [], "changes": []}'
        )
        multi = f"{dup_first}\n{json.dumps(VALID_PROPOSAL)}"
        envelope = {
            "conversation_id": "session-test-id",
            "status": "SUCCESS",
            "response": multi,
        }
        raw = make_stream(envelope)
        with self.assertRaises(ProposalError) as ctx:
            parse_stream(raw, VALID_TASK, VALID_SOURCES, "worker")
        self.assertNotIsInstance(ctx.exception, RetryableProviderSessionError)
        self.assertIn("duplicate JSON key", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
