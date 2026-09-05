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


if __name__ == "__main__":
    unittest.main()
