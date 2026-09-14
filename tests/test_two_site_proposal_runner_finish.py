"""Focused regression suite for safe Antigravity finish-submission classification."""
from __future__ import annotations

import json
import unittest

from scripts import two_site_proposal_runner as runner

_OMIT = object()
SESSION = "session-test-001"


def _valid_params(**overrides) -> dict:
    params = {
        "status": "PROPOSAL_READY",
        "summary": "Safe finish submission parameters.",
        "covered_requirements": [1, 2],
        "findings": [],
        "changes": [{"path": "test_new.py", "original_sha256": None, "content": "assert 1 == 1\n"}],
    }
    params.update(overrides)
    return params


def _valid_result(**overrides) -> dict:
    res = {
        "status": "PROPOSAL_READY",
        "summary": "Validated full proposal implementation.",
        "covered_requirements": [1, 2],
        "findings": [],
        "changes": [{"path": "test_new.py", "original_sha256": None, "content": "assert 1 == 1\n"}],
    }
    res.update(overrides)
    return res


def _build_tool_info(*, name="finish", parameters=_OMIT, extra=None) -> dict:
    info = {}
    if name is not _OMIT:
        info["name"] = name
    if parameters is not _OMIT:
        info["parameters"] = parameters
    if extra:
        info.update(extra)
    return info


def _build_step(
    *,
    step_type="tool",
    tool_name="finish",
    tool_info=_OMIT,
    subagent_info=_OMIT,
    conversation_id=_OMIT,
    extra=None,
) -> dict:
    step = {}
    if step_type is not _OMIT:
        step["step_type"] = step_type
    if tool_name is not _OMIT:
        step["tool_name"] = tool_name
    if tool_info is not _OMIT:
        step["tool_info"] = tool_info
    if subagent_info is not _OMIT:
        step["subagent_info"] = subagent_info
    if conversation_id is not _OMIT:
        step["conversation_id"] = conversation_id
    if extra:
        step.update(extra)
    return step


def _build_step_update_event(step, *, conversation_id=_OMIT, extra=None) -> dict:
    event = {"event": "step_update", "step_update": step}
    if conversation_id is not _OMIT:
        event["conversation_id"] = conversation_id
    if extra:
        event.update(extra)
    return event


def _build_stream(step_events=(), *, session=SESSION, structured_output=_OMIT, response=_OMIT) -> str:
    init_ev = {"event": "init", "conversation_id": session, "init": {"model": runner.MODEL}}
    res_dict = {"status": "SUCCESS", "conversation_id": session}
    if structured_output is not _OMIT:
        res_dict["structured_output"] = structured_output
    elif response is _OMIT:
        res_dict["structured_output"] = _valid_result()
    if response is not _OMIT:
        res_dict["response"] = response
    result_ev = {"event": "result", "result": res_dict}
    return "\n".join(json.dumps(e) for e in [init_ev, *step_events, result_ev]) + "\n"


def _valid_tool_error(message="Tool execution denied by policy.") -> dict:
    return {"type": "TOOL_ERROR", "message": message}


def _build_lifecycle_step(
    step_index: int = 1,
    state: str = "ACTIVE",
    *,
    parameters=_OMIT,
    error=_OMIT,
    duration_seconds=_OMIT,
    conversation_id=_OMIT,
    extra=None,
) -> dict:
    params = _valid_params() if parameters is _OMIT else parameters
    tool_info = {"name": "finish", "parameters": params}
    if state == "ERROR" and error is not None:
        tool_info["error"] = _valid_tool_error() if error is _OMIT else error
    step = {
        "step_type": "tool",
        "tool_name": "finish",
        "step_index": step_index,
        "state": state,
        "tool_info": tool_info,
    }
    if duration_seconds is not _OMIT:
        step["duration_seconds"] = duration_seconds
    if conversation_id is not _OMIT:
        step["conversation_id"] = conversation_id
    if extra:
        step.update(extra)
    return step


def _build_lifecycle_event(
    step_index: int = 1,
    state: str = "ACTIVE",
    *,
    parameters=_OMIT,
    error=_OMIT,
    duration_seconds=_OMIT,
    step_cid=_OMIT,
    env_cid=_OMIT,
    step_extra=None,
    event_extra=None,
) -> dict:
    step = _build_lifecycle_step(
        step_index,
        state,
        parameters=parameters,
        error=error,
        duration_seconds=duration_seconds,
        conversation_id=step_cid,
        extra=step_extra,
    )
    return _build_step_update_event(step, conversation_id=env_cid, extra=event_extra)


class TestTwoSiteProposalRunnerFinish(unittest.TestCase):
    def setUp(self):
        self.task = {
            "task_id": "TEST-001",
            "goal": "Add focused regression test.",
            "context_files": ["source.py"],
            "allowed_files": ["test_new.py"],
            "requirements": ["Requirement 1", "Requirement 2"],
            "test_selectors": ["test_new.py"],
        }
        self.sources = {
            "source.py": {"sha256": runner.sha(b"VALUE = 1\n"), "content": "VALUE = 1\n"},
            "test_new.py": {"sha256": None, "content": None},
        }

    def _parse(self, ndjson: str):
        return runner.parse_stream(ndjson, self.task, self.sources, "worker")

    def _valid_step_event(self, *, step_cid=_OMIT, env_cid=_OMIT, params=None):
        step = _build_step(
            tool_info=_build_tool_info(parameters=_valid_params() if params is None else params),
            conversation_id=step_cid,
        )
        return _build_step_update_event(step, conversation_id=env_cid)

    def test_permitted_safe_finish_step_and_conversation_matching(self):
        cases = [
            ("omitted_both", _OMIT, _OMIT),
            ("matched_step_only", SESSION, _OMIT),
            ("matched_env_only", _OMIT, SESSION),
            ("matched_both", SESSION, SESSION),
        ]
        for name, step_cid, env_cid in cases:
            with self.subTest(case=name):
                event = self._valid_step_event(step_cid=step_cid, env_cid=env_cid)
                result, envelope = self._parse(_build_stream([event]))
                self.assertEqual(result["status"], "PROPOSAL_READY")
                self.assertEqual(envelope["conversation_id"], SESSION)

    def test_ordinary_step_type_finish_lifecycle_event_is_non_effectful(self):
        lifecycle = _build_step_update_event({"step_type": "finish"})
        lifecycle_named = _build_step_update_event({"step_type": "finish", "phase": "completed"})
        safe_finish = self._valid_step_event()

        res1, env1 = self._parse(_build_stream([lifecycle, lifecycle_named]))
        self.assertEqual(res1["status"], "PROPOSAL_READY")
        self.assertEqual(env1["conversation_id"], SESSION)

        res2, env2 = self._parse(_build_stream([lifecycle, safe_finish, lifecycle_named]))
        self.assertEqual(res2["status"], "PROPOSAL_READY")
        self.assertEqual(env2["conversation_id"], SESSION)

    def test_at_most_one_safe_finish_submission_allowed(self):
        step1 = self._valid_step_event()
        step2 = self._valid_step_event()
        with self.assertRaisesRegex(runner.ProposalError, "tool attempt invalidates"):
            self._parse(_build_stream([step1, step2]))

    def test_safe_finish_tool_parameters_are_never_canonical(self):
        canonical = _valid_result(
            changes=[{"path": "test_new.py", "original_sha256": None, "content": "assert 1 == 1\n# full\n"}]
        )
        shortened_params = _valid_params(
            changes=[{"path": "test_new.py", "original_sha256": None, "content": "short"}]
        )
        event = self._valid_step_event(params=shortened_params)
        raw = _build_stream([event], structured_output=canonical)
        result, envelope = self._parse(raw)

        self.assertEqual(result, canonical)
        self.assertEqual(result["changes"][0]["content"], "assert 1 == 1\n# full\n")
        self.assertNotEqual(result["changes"][0]["content"], "short")
        self.assertEqual(envelope["structured_output"], canonical)

    def test_rejection_of_invalid_conversation_id_at_envelope_and_nested_locations(self):
        invalid_cids = [None, "", False, 123, "different-session"]
        valid_step = _build_step(tool_info=_build_tool_info(parameters=_valid_params()))

        for bad in invalid_cids:
            with self.subTest(location="envelope", value=repr(bad)):
                ev = _build_step_update_event(valid_step, conversation_id=bad)
                with self.assertRaises(runner.ProposalError):
                    self._parse(_build_stream([ev]))

            with self.subTest(location="nested_step", value=repr(bad)):
                step = _build_step(
                    tool_info=_build_tool_info(parameters=_valid_params()),
                    conversation_id=bad,
                )
                ev = _build_step_update_event(step)
                with self.assertRaises(runner.ProposalError):
                    self._parse(_build_stream([ev]))

    def test_table_rejection_of_invalid_or_malformed_tool_steps(self):
        valid_params = _valid_params()
        cases = [
            ("wrong_step_type_subagent", _build_step(step_type="subagent", tool_info=_build_tool_info(parameters=valid_params))),
            ("wrong_step_type_tool_use", _build_step(step_type="tool_use", tool_info=_build_tool_info(parameters=valid_params))),
            ("missing_step_type", _build_step(step_type=_OMIT, tool_info=_build_tool_info(parameters=valid_params))),
            ("step_type_false", _build_step(step_type=False, tool_info=_build_tool_info(parameters=valid_params))),
            ("step_type_empty_str", _build_step(step_type="", tool_info=_build_tool_info(parameters=valid_params))),
            ("step_type_null", _build_step(step_type=None, tool_info=_build_tool_info(parameters=valid_params))),
            ("non_finish_tool_name", _build_step(tool_name="read_file", tool_info=_build_tool_info(name="read_file", parameters=valid_params))),
            ("tool_name_mismatch", _build_step(tool_name="finish", tool_info=_build_tool_info(name="other", parameters=valid_params))),
            ("missing_tool_name", _build_step(tool_name=_OMIT, tool_info=_build_tool_info(parameters=valid_params))),
            ("tool_name_false", _build_step(tool_name=False, tool_info=_build_tool_info(parameters=valid_params))),
            ("tool_name_empty_str", _build_step(tool_name="", tool_info=_build_tool_info(parameters=valid_params))),
            ("tool_name_null", _build_step(tool_name=None, tool_info=_build_tool_info(parameters=valid_params))),
            ("subagent_info_dict", _build_step(tool_info=_build_tool_info(parameters=valid_params), subagent_info={"id": "child"})),
            ("subagent_info_empty_dict", _build_step(tool_info=_build_tool_info(parameters=valid_params), subagent_info={})),
            ("subagent_info_empty_str", _build_step(tool_info=_build_tool_info(parameters=valid_params), subagent_info="")),
            ("subagent_info_null", _build_step(tool_info=_build_tool_info(parameters=valid_params), subagent_info=None)),
            ("subagent_info_false", _build_step(tool_info=_build_tool_info(parameters=valid_params), subagent_info=False)),
            ("tool_info_omitted", _build_step(tool_info=_OMIT)),
            ("tool_info_null", _build_step(tool_info=None)),
            ("tool_info_empty_str", _build_step(tool_info="")),
            ("tool_info_false", _build_step(tool_info=False)),
            ("tool_info_list", _build_step(tool_info=[])),
            ("tool_info_empty_dict", _build_step(tool_info={})),
            ("tool_info_missing_parameters", _build_step(tool_info={"name": "finish"})),
            ("tool_info_missing_name", _build_step(tool_info={"parameters": valid_params})),
            ("tool_info_extra_keys", _build_step(tool_info=_build_tool_info(parameters=valid_params, extra={"extra": 1}))),
            ("params_missing_status", _build_step(tool_info=_build_tool_info(parameters={k: v for k, v in valid_params.items() if k != "status"}))),
            ("params_missing_summary", _build_step(tool_info=_build_tool_info(parameters={k: v for k, v in valid_params.items() if k != "summary"}))),
            ("params_missing_covered", _build_step(tool_info=_build_tool_info(parameters={k: v for k, v in valid_params.items() if k != "covered_requirements"}))),
            ("params_missing_findings", _build_step(tool_info=_build_tool_info(parameters={k: v for k, v in valid_params.items() if k != "findings"}))),
            ("params_missing_changes", _build_step(tool_info=_build_tool_info(parameters={k: v for k, v in valid_params.items() if k != "changes"}))),
            ("params_extra_key", _build_step(tool_info=_build_tool_info(parameters=dict(valid_params, extra=1)))),
            ("params_toolAction", _build_step(tool_info=_build_tool_info(parameters=dict(valid_params, toolAction="submit")))),
            ("params_empty_dict", _build_step(tool_info=_build_tool_info(parameters={}))),
            ("params_null", _build_step(tool_info=_build_tool_info(parameters=None))),
            ("params_not_dict", _build_step(tool_info=_build_tool_info(parameters="invalid"))),
            ("params_list", _build_step(tool_info=_build_tool_info(parameters=[]))),
            ("params_false", _build_step(tool_info=_build_tool_info(parameters=False))),
        ]
        for name, step in cases:
            with self.subTest(case=name):
                ev = _build_step_update_event(step)
                with self.assertRaises(runner.ProposalError):
                    self._parse(_build_stream([ev]))

    def test_table_rejection_of_tool_markers_at_invalid_envelope_locations(self):
        cases = [
            ("env_tool_name_finish", {"tool_name": "finish"}),
            ("env_tool_name_empty_str", {"tool_name": ""}),
            ("env_tool_name_null", {"tool_name": None}),
            ("env_tool_name_false", {"tool_name": False}),
            ("env_tool_info_dict", {"tool_info": {"name": "finish"}}),
            ("env_tool_info_empty_dict", {"tool_info": {}}),
            ("env_tool_info_null", {"tool_info": None}),
            ("env_tool_info_false", {"tool_info": False}),
            ("env_subagent_info_dict", {"subagent_info": {"id": "sub-1"}}),
            ("env_subagent_info_empty_dict", {"subagent_info": {}}),
            ("env_subagent_info_empty_str", {"subagent_info": ""}),
            ("env_subagent_info_null", {"subagent_info": None}),
            ("env_subagent_info_false", {"subagent_info": False}),
            ("env_step_type_tool", {"step_type": "tool"}),
            ("env_step_type_subagent", {"step_type": "subagent"}),
        ]
        for name, marker in cases:
            with self.subTest(case=name):
                ev = {"event": "step_update", "step_update": {}, **marker}
                with self.assertRaisesRegex(runner.ProposalError, "tool attempt invalidates"):
                    self._parse(_build_stream([ev]))

    def test_lifecycle_active_error_active_success_accepts_structured_output(self):
        ev1 = _build_lifecycle_event(1, "ACTIVE", duration_seconds=0.01)
        ev2 = _build_lifecycle_event(1, "ERROR", error=_valid_tool_error("policy denied"), duration_seconds=0.04)
        ev3 = _build_lifecycle_event(2, "ACTIVE", duration_seconds=0.02)
        canonical = _valid_result()
        raw = _build_stream([ev1, ev2, ev3], structured_output=canonical)
        result, envelope = self._parse(raw)
        self.assertEqual(result, canonical)
        self.assertEqual(envelope["status"], "SUCCESS")
        self.assertEqual(envelope["conversation_id"], SESSION)

    def test_lifecycle_three_failed_submissions_followed_by_success(self):
        events = []
        for idx in (1, 2, 3):
            events.append(_build_lifecycle_event(idx, "ACTIVE", duration_seconds=0.01 * idx))
            events.append(_build_lifecycle_event(idx, "ERROR", error=_valid_tool_error(f"failed attempt {idx}"), duration_seconds=0.05 * idx))
        canonical = _valid_result()
        raw = _build_stream(events, structured_output=canonical)
        result, envelope = self._parse(raw)
        self.assertEqual(result, canonical)
        self.assertEqual(envelope["status"], "SUCCESS")
        self.assertEqual(envelope["conversation_id"], SESSION)

    def test_lifecycle_terminal_truncation_remains_non_retryable(self):
        ev = _build_lifecycle_event(1, "ACTIVE", duration_seconds=0.02)
        init_ev = {"event": "init", "conversation_id": SESSION, "init": {"model": runner.MODEL}}
        res_dict = {
            "status": "ERROR",
            "conversation_id": SESSION,
            "error": "MODEL_OUTPUT_TRUNCATED: output token limit exceeded",
            "structured_output": _valid_result(),
        }
        raw = "\n".join(json.dumps(e) for e in [init_ev, ev, {"event": "result", "result": res_dict}]) + "\n"
        with self.assertRaisesRegex(runner.ProposalError, "MODEL_OUTPUT_TRUNCATED: rescope to a smaller task"):
            self._parse(raw)

    def test_lifecycle_tool_parameters_cannot_replace_or_alter_terminal_bytes(self):
        canonical = _valid_result(
            changes=[{"path": "test_new.py", "original_sha256": None, "content": "assert 1 == 1\n# full\n"}]
        )
        shortened_params = _valid_params(
            changes=[{"path": "test_new.py", "original_sha256": None, "content": "short"}]
        )
        ev1 = _build_lifecycle_event(1, "ACTIVE", parameters=shortened_params, duration_seconds=0.01)
        ev2 = _build_lifecycle_event(1, "ERROR", parameters=shortened_params, error=_valid_tool_error(), duration_seconds=0.05)
        ev3 = _build_lifecycle_event(2, "ACTIVE", parameters=shortened_params, duration_seconds=0.02)
        raw = _build_stream([ev1, ev2, ev3], structured_output=canonical)
        result, envelope = self._parse(raw)
        self.assertEqual(result, canonical)
        self.assertEqual(result["changes"][0]["content"], "assert 1 == 1\n# full\n")
        self.assertNotEqual(result["changes"][0]["content"], "short")
        self.assertEqual(envelope["structured_output"], canonical)

    def test_table_driven_lifecycle_rejections(self):
        valid_params = _valid_params()
        alt_params = _valid_params(summary="Different summary for testing parameter tampering.")
        cases = [
            ("duplicate_active_same_index", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ACTIVE"),
            ]),
            ("active_then_active_different_index", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(2, "ACTIVE"),
            ]),
            ("duplicate_error", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR"),
                _build_lifecycle_event(1, "ERROR"),
            ]),
            ("error_without_prior_active", [
                _build_lifecycle_event(1, "ERROR"),
            ]),
            ("error_index_mismatch", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(2, "ERROR"),
            ]),
            ("changed_parameters_in_error", [
                _build_lifecycle_event(1, "ACTIVE", parameters=valid_params),
                _build_lifecycle_event(1, "ERROR", parameters=alt_params),
            ]),
            ("non_increasing_index_reused", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR"),
                _build_lifecycle_event(1, "ACTIVE"),
            ]),
            ("decreasing_index", [
                _build_lifecycle_event(2, "ACTIVE"),
                _build_lifecycle_event(2, "ERROR"),
                _build_lifecycle_event(1, "ACTIVE"),
            ]),
            ("more_than_three_distinct_submissions", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR"),
                _build_lifecycle_event(2, "ACTIVE"),
                _build_lifecycle_event(2, "ERROR"),
                _build_lifecycle_event(3, "ACTIVE"),
                _build_lifecycle_event(3, "ERROR"),
                _build_lifecycle_event(4, "ACTIVE"),
            ]),
            ("unknown_state_pending", [
                _build_lifecycle_event(1, "PENDING"),
            ]),
            ("unknown_state_failed", [
                _build_lifecycle_event(1, "FAILED"),
            ]),
            ("unknown_state_empty", [
                _build_lifecycle_event(1, ""),
            ]),
            ("unknown_state_none", [
                _build_lifecycle_event(1, None),
            ]),
            ("step_index_zero", [
                _build_lifecycle_event(0, "ACTIVE"),
            ]),
            ("step_index_negative", [
                _build_lifecycle_event(-1, "ACTIVE"),
            ]),
            ("step_index_string", [
                _build_lifecycle_event("1", "ACTIVE"),
            ]),
            ("step_index_bool", [
                _build_lifecycle_event(True, "ACTIVE"),
            ]),
            ("step_index_none", [
                _build_lifecycle_event(None, "ACTIVE"),
            ]),
            ("duration_seconds_negative", [
                _build_lifecycle_event(1, "ACTIVE", duration_seconds=-0.5),
            ]),
            ("duration_seconds_string", [
                _build_lifecycle_event(1, "ACTIVE", duration_seconds="0.5s"),
            ]),
            ("duration_seconds_bool", [
                _build_lifecycle_event(1, "ACTIVE", duration_seconds=True),
            ]),
            ("extra_step_key_active", [
                _build_lifecycle_event(1, "ACTIVE", step_extra={"unexpected": True}),
            ]),
            ("extra_step_key_error", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR", step_extra={"unexpected": True}),
            ]),
            ("active_tool_info_extra_key", [
                _build_step_update_event(_build_step(
                    tool_info=_build_tool_info(parameters=valid_params, extra={"extra": 1}),
                    extra={"step_index": 1, "state": "ACTIVE"},
                )),
            ]),
            ("error_tool_info_extra_key", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_step_update_event(_build_step(
                    tool_info=_build_tool_info(parameters=valid_params, extra={"error": _valid_tool_error(), "extra": 1}),
                    extra={"step_index": 1, "state": "ERROR"},
                )),
            ]),
            ("error_tool_info_missing_error", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_step_update_event(_build_step(
                    tool_info=_build_tool_info(parameters=valid_params),
                    extra={"step_index": 1, "state": "ERROR"},
                )),
            ]),
            ("error_not_dict", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR", error="failed"),
            ]),
            ("error_wrong_type", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR", error={"type": "OTHER_ERROR", "message": "fail"}),
            ]),
            ("error_missing_message", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR", error={"type": "TOOL_ERROR"}),
            ]),
            ("error_message_not_string", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR", error={"type": "TOOL_ERROR", "message": 123}),
            ]),
            ("error_extra_key", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR", error={"type": "TOOL_ERROR", "message": "fail", "code": 1}),
            ]),
            ("error_message_unbounded", [
                _build_lifecycle_event(1, "ACTIVE"),
                _build_lifecycle_event(1, "ERROR", error={"type": "TOOL_ERROR", "message": "x" * 4097}),
            ]),
            ("mixing_legacy_then_lifecycle", [
                self._valid_step_event(),
                _build_lifecycle_event(1, "ACTIVE"),
            ]),
            ("mixing_lifecycle_then_legacy", [
                _build_lifecycle_event(1, "ACTIVE"),
                self._valid_step_event(),
            ]),
        ]
        for name, stream_events in cases:
            with self.subTest(case=name):
                with self.assertRaises(runner.RetryableProviderSessionError):
                    self._parse(_build_stream(stream_events))


if __name__ == "__main__":
    unittest.main()
