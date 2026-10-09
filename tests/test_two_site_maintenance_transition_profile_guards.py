"""Hermetic regression companion for AE03 trusted private profile guards."""

from __future__ import annotations

import copy
import os
import unittest
from typing import Any

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import scripts.two_site_maintenance_transition_record as s
import tests.test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_work_cost_retained_call_frame import _inspect_graph

_PROP_SHA = "1" * 64
_TEST_SHA = "2" * 64
_RUNTIME_JSON = "docs/refactor/two-server/templates/gemini-runtime-contract.json"
_RUNNER_PY = "scripts/two_site_proposal_runner.py"
_BROKER_PY = "scripts/two_site_provider_broker.py"
_GENERIC_BROKER = "scripts/two_site_broker.py"
_VALID_PATH = "scripts/two_site_cycle_tools.py"


class _CopiedFrozenSet(frozenset):
    pass


def _cache_graph(root: Any) -> list[tuple[Any, str, Any]]:
    records: list[tuple[Any, str, Any]] = []
    seen: set[int] = set()

    def _walk(curr: Any, depth: int) -> None:
        if depth > 16:
            raise ValueError(h._ERR_BOUND)
        nid = id(curr)
        if nid in seen:
            return
        t = type(curr)
        if t is list:
            seen.add(nid)
            shallow = list(curr)
            records.append((curr, "LIST", shallow))
            for elem in shallow:
                _walk(elem, depth + 1)
        elif t is dict:
            seen.add(nid)
            items = list(curr.items())
            records.append((curr, "DICT", items))
            for v in curr.values():
                _walk(v, depth + 1)
        elif t is set:
            seen.add(nid)
            shallow = list(curr)
            records.append((curr, "SET", shallow))
            for m in shallow:
                _walk(m, depth + 1)

    _walk(root, 0)
    return records


def _restore_graph(records: list[tuple[Any, str, Any]]) -> None:
    for obj, tag, data in reversed(records):
        if tag == "LIST":
            obj.clear()
            obj.extend(data)
        elif tag == "DICT":
            obj.clear()
            for k, v in data:
                obj[k] = v
        elif tag == "SET":
            obj.clear()
            obj.update(data)


def _capture_root_snapshot(root: Any) -> Any:
    if type(root) in (set, frozenset):
        return (
            "SAFE_SET",
            type(root),
            id(root),
            tuple((id(m), m, type(m) is str, m if type(m) is str else None) for m in root),
        )
    live_refs: list[Any] = []

    def _collect(node: Any, depth: int) -> None:
        if depth > 16:
            raise ValueError(h._ERR_BOUND)
        t = type(node)
        if t in (list, dict):
            live_refs.append(node)
            children = node if t is list else node.values()
            for x in children:
                _collect(x, depth + 1)

    _collect(root, 0)
    return ("NATIVE_TREE", h.capture_custody(root), _inspect_graph(root)[0], live_refs)


def _assert_root_custody(root: Any, snap: Any) -> None:
    if snap[0] == "SAFE_SET":
        _, exp_t, exp_id, exp_members = snap
        if type(root) is not exp_t or id(root) != exp_id or len(root) != len(exp_members):
            raise AssertionError(h._ERR_CUSTODY)
        matched: set[int] = set()
        for m in root:
            found = False
            for idx, (_orig_id, orig_ref, is_str, str_val) in enumerate(exp_members):
                if idx not in matched and m is orig_ref:
                    if is_str and (type(m) is not str or m != str_val):
                        raise AssertionError(h._ERR_CUSTODY)
                    matched.add(idx)
                    found = True
                    break
            if not found:
                raise AssertionError(h._ERR_CUSTODY)
    elif snap[0] == "NATIVE_TREE":
        _, val_snap, orig_fp, orig_refs = snap
        h.assert_custody_unchanged(root, val_snap)
        if _inspect_graph(root)[0] != orig_fp:
            raise AssertionError(h._ERR_CUSTODY)
        curr_refs: list[Any] = []

        def _collect(node: Any, depth: int) -> None:
            if depth > 16:
                raise ValueError(h._ERR_BOUND)
            t = type(node)
            if t in (list, dict):
                curr_refs.append(node)
                children = node if t is list else node.values()
                for x in children:
                    _collect(x, depth + 1)

        _collect(root, 0)
        if len(curr_refs) != len(orig_refs) or any(
            a is not b for a, b in zip(curr_refs, orig_refs)
        ):
            raise AssertionError(h._ERR_CUSTODY)
    else:
        raise AssertionError(h._ERR_CUSTODY)


def _run_guarded(
    roots: list[Any],
    fn: Any,
    *args: Any,
    expected: Any = None,
    **kwargs: Any,
) -> Any:
    cached = [_cache_graph(r) for r in roots]
    snaps = [_capture_root_snapshot(r) for r in roots]
    h.reset_hook_counters()
    try:
        with h.armed_hooks():
            try:
                res = fn(*args, **kwargs)
            finally:
                for r, snap in zip(roots, snaps):
                    _assert_root_custody(r, snap)
                if h.get_total_hook_calls() != 0:
                    raise AssertionError("foreign hooks were unexpectedly invoked")
    finally:
        for r_records in cached:
            _restore_graph(r_records)
    if expected is not None:
        h.assert_exact_typed(res, expected)
    return res


def _make_commit(
    idx: int, parent_sha: str, head_sha: str, paths: list[str] | None = None
) -> dict[str, Any]:
    return {
        "head": head_sha,
        "parent": parent_sha,
        "paths": [_VALID_PATH] if paths is None else paths,
        "proposal_sha256": _PROP_SHA,
        "reviewer_run_id": f"reviewer-run-{idx}",
        "test_evidence_sha256": _TEST_SHA,
        "worker_run_id": f"worker-run-{idx}",
    }


def _make_chain(
    n: int, paths: list[str] | None = None
) -> tuple[str, str, list[dict[str, Any]]]:
    prior_head, curr = "0" * 40, "0" * 40
    commits: list[dict[str, Any]] = []
    for i in range(n):
        head = f"{i + 1:040x}"
        commits.append(_make_commit(i, curr, head, paths))
        curr = head
    return prior_head, curr, commits


def _make_expected_chain(
    n: int, paths: list[str] | None = None
) -> list[dict[str, Any]]:
    p = [_VALID_PATH] if paths is None else paths
    prior, res = "0" * 40, []
    for i in range(n):
        h_sha = f"{i + 1:040x}"
        res.append({
            "head": h_sha,
            "parent": prior,
            "paths": p,
            "proposal_sha256": _PROP_SHA,
            "reviewer_run_id": f"reviewer-run-{i}",
            "test_evidence_sha256": _TEST_SHA,
            "worker_run_id": f"worker-run-{i}",
        })
        prior = h_sha
    return res


class TwoSiteMaintenanceTransitionProfileGuardsTest(unittest.TestCase):
    def test_profile_matrix_on_all_three_helpers(self) -> None:
        bad_profiles = (object(), "bad", 1, None, h.ForeignClassAllHooks)
        paths_in, exp_paths = [_VALID_PATH], [_VALID_PATH]
        p_h, f_h, c1 = _make_chain(1)
        exp_c1 = _make_expected_chain(1)

        for p in (None, s._PROFILE_V1, s._PROFILE_V2):
            kw = {} if p is None else {"profile": p}
            self.assertEqual(getattr(s, "_validate_commit_path")(_VALID_PATH, **kw), _VALID_PATH)
        for bp in bad_profiles:
            with self.assertRaises(s.MaintenanceTransitionRecordError):
                _run_guarded([], getattr(s, "_validate_commit_path"), _VALID_PATH, profile=bp)

        widened_v1 = s.ALLOWED_MAINTENANCE_CONTROL_PATHS | {_RUNNER_PY}
        copied_set, copied_fz = set(s.ALLOWED_MAINTENANCE_CONTROL_PATHS), _CopiedFrozenSet(s.ALLOWED_MAINTENANCE_CONTROL_PATHS)
        self.assertIsNot(copied_set, s.ALLOWED_MAINTENANCE_CONTROL_PATHS)
        self.assertIsNot(copied_fz, s.ALLOWED_MAINTENANCE_CONTROL_PATHS)

        for fn, r_in, exp, args in (
            (getattr(s, "_validate_commit_paths"), paths_in, exp_paths, (paths_in, set())),
            (getattr(s, "_validate_maintenance_commits"), c1, exp_c1, (c1, p_h, f_h, set())),
        ):
            for kw in ({}, {"profile": s._PROFILE_V1}, {"profile": s._PROFILE_V2}):
                _run_guarded([r_in, exp], fn, *args, expected=exp, **kw)
            _run_guarded([r_in, exp], fn, *args, s._ALLOWED_MAINTENANCE_CONTROL_PATHS_V2, profile=s._PROFILE_V2, expected=exp)
            for bp in bad_profiles:
                with self.assertRaises(s.MaintenanceTransitionRecordError):
                    _run_guarded([r_in], fn, *args, profile=bp)
            _run_guarded([r_in, exp], fn, *args, widened_v1, profile=s._PROFILE_V1, expected=exp)
            for bad_c in (copied_set, copied_fz, widened_v1, h.ForeignClassAllHooks):
                with self.assertRaises(s.MaintenanceTransitionRecordError):
                    _run_guarded([r_in], fn, *args, bad_c, profile=s._PROFILE_V2)

    def test_control_blob_paths_guard_under_v2(self) -> None:
        paths_in, exp_paths = [_VALID_PATH], [_VALID_PATH]
        p_h, f_h, c1 = _make_chain(1)
        exp_c1 = _make_expected_chain(1)
        good_controls = {"scripts/two_site_cycle_metrics.py"}
        bad_containers = [h.ForeignDict(), h.ForeignList(), h.ForeignClassAllHooks, ["scripts/two_site_cycle_tools.py"]]
        bad_member_sets = [{h.ForeignClassAllHooks}, {123}, {"scripts/two_site_cycle_tools.py", h.ForeignOpaqueLeaf()}]

        for fn, r_in, exp, args in (
            (getattr(s, "_validate_commit_paths"), paths_in, exp_paths, (paths_in,)),
            (getattr(s, "_validate_maintenance_commits"), c1, exp_c1, (c1, p_h, f_h)),
        ):
            _run_guarded([r_in, exp], fn, *args, good_controls, profile=s._PROFILE_V2, expected=exp)
            for bad_c in bad_containers + bad_member_sets:
                with self.assertRaises(s.MaintenanceTransitionRecordError):
                    _run_guarded([r_in], fn, *args, bad_c, profile=s._PROFILE_V2)

        leaf_set = {h.ForeignOpaqueLeaf()}
        snap = _capture_root_snapshot(leaf_set)
        h.reset_hook_counters()
        with h.armed_hooks():
            _assert_root_custody(leaf_set, snap)
            self.assertEqual(h.get_total_hook_calls(), 0)

        for fn, r_in, exp, args in (
            (getattr(s, "_validate_commit_paths"), paths_in, exp_paths, (paths_in,)),
            (getattr(s, "_validate_maintenance_commits"), c1, exp_c1, (c1, p_h, f_h)),
        ):
            _run_guarded([r_in, exp], fn, *args, good_controls, profile=s._PROFILE_V2, expected=exp)

    def test_commit_paths_v2_protected_and_runtime_rules(self) -> None:
        nonempty_ctrls = {"scripts/two_site_cycle_metrics.py"}
        for forbidden in (_RUNNER_PY, _BROKER_PY):
            for c_set in (nonempty_ctrls, set()):
                with self.assertRaises(s.MaintenanceTransitionRecordError):
                    getattr(s, "_validate_commit_paths")([forbidden], c_set, profile=s._PROFILE_V2)

        for c_set in (set(), nonempty_ctrls):
            self.assertEqual(getattr(s, "_validate_commit_paths")([_GENERIC_BROKER], c_set, profile=s._PROFILE_V2), [_GENERIC_BROKER])
        with self.assertRaises(s.MaintenanceTransitionRecordError):
            getattr(s, "_validate_commit_paths")([_GENERIC_BROKER], {_GENERIC_BROKER}, profile=s._PROFILE_V2)

        unapproved = "scripts/two_site_unapproved.py"
        with self.assertRaises(s.MaintenanceTransitionRecordError):
            getattr(s, "_validate_commit_paths")([unapproved], {unapproved}, profile=s._PROFILE_V2)

        self.assertEqual(getattr(s, "_validate_commit_path")(_RUNTIME_JSON, profile=s._PROFILE_V2), _RUNTIME_JSON)
        self.assertEqual(getattr(s, "_validate_commit_paths")([_RUNTIME_JSON], {_RUNTIME_JSON}, profile=s._PROFILE_V2), [_RUNTIME_JSON])
        with self.assertRaises(s.MaintenanceTransitionRecordError):
            getattr(s, "_validate_commit_path")(_RUNTIME_JSON, profile=s._PROFILE_V1)

        bad_json_variants = [
            "docs/refactor/two-server/templates/GEMINI-RUNTIME-CONTRACT.JSON",
            _RUNTIME_JSON + ".bak",
            "docs/refactor/two-server/templates/../templates/gemini-runtime-contract.json",
            "docs/refactor/two-server/templates/*.json",
            "docs/refactor/two-server/templates/other.json",
        ]
        for bad_p in bad_json_variants:
            with self.assertRaises(s.MaintenanceTransitionRecordError):
                getattr(s, "_validate_commit_path")(bad_p, profile=s._PROFILE_V2)

        for bad_paths in (
            [_VALID_PATH, _RUNTIME_JSON], [_VALID_PATH, _VALID_PATH], [], [f"scripts/two_site_test_{i}.py" for i in range(257)]
        ):
            with self.assertRaises(s.MaintenanceTransitionRecordError):
                getattr(s, "_validate_commit_paths")(bad_paths, set(), profile=s._PROFILE_V2)

    def test_maintenance_commits_counts_and_chain_validations(self) -> None:
        for n in (1, 12):
            p_h, f_h, c = _make_chain(n)
            exp_c = _make_expected_chain(n)
            _run_guarded([c, exp_c], getattr(s, "_validate_maintenance_commits"), c, p_h, f_h, set(), profile=s._PROFILE_V1, expected=exp_c)
            _run_guarded([c, exp_c], getattr(s, "_validate_maintenance_commits"), c, p_h, f_h, set(), profile=s._PROFILE_V2, expected=exp_c)

        p_h13, f_h13, c13 = _make_chain(13)
        exp_c13 = _make_expected_chain(13)
        with self.assertRaises(s.MaintenanceTransitionRecordError):
            getattr(s, "_validate_maintenance_commits")(c13, p_h13, f_h13, set(), profile=s._PROFILE_V1)
        _run_guarded([c13, exp_c13], getattr(s, "_validate_maintenance_commits"), c13, p_h13, f_h13, set(), profile=s._PROFILE_V2, expected=exp_c13)

        p_h32, f_h32, c32 = _make_chain(32)
        exp_c32 = _make_expected_chain(32)
        _run_guarded([c32, exp_c32], getattr(s, "_validate_maintenance_commits"), c32, p_h32, f_h32, set(), profile=s._PROFILE_V2, expected=exp_c32)

        with self.assertRaises(s.MaintenanceTransitionRecordError):
            _run_guarded([], getattr(s, "_validate_maintenance_commits"), [], "0" * 40, "1" * 40, set(), profile=s._PROFILE_V2)

        p_h33, f_h33, c33 = _make_chain(33)
        c33[-1] = h.ForeignClassAllHooks
        with self.assertRaises(s.MaintenanceTransitionRecordError):
            _run_guarded([], getattr(s, "_validate_maintenance_commits"), c33, p_h33, f_h33, set(), profile=s._PROFILE_V2)

        c13_armed = copy.deepcopy(c13)
        c13_armed[-1] = h.ForeignClassAllHooks
        with self.assertRaises(s.MaintenanceTransitionRecordError):
            _run_guarded([], getattr(s, "_validate_maintenance_commits"), c13_armed, p_h13, f_h13, set(), profile=s._PROFILE_V1)

        p_h2, f_h2, c2 = _make_chain(2)
        bad_cases = [
            lambda: [c2[0], dict(c2[1], head=c2[0]["head"])],
            lambda: [c2[0], dict(c2[1], parent="f" * 40)],
            lambda: [dict(c2[0], worker_run_id=c2[0]["reviewer_run_id"]), c2[1]],
            lambda: [dict(c2[0], proposal_sha256="bad"), c2[1]],
            lambda: [dict(c2[0], test_evidence_sha256="bad"), c2[1]],
        ]
        for make_bad in bad_cases:
            with self.assertRaises(s.MaintenanceTransitionRecordError):
                getattr(s, "_validate_maintenance_commits")(make_bad(), p_h2, f_h2, set())
        for bad_p, bad_f in (("f" * 40, f_h2), (p_h2, "f" * 40), (f_h2, f_h2)):
            with self.assertRaises(s.MaintenanceTransitionRecordError):
                getattr(s, "_validate_maintenance_commits")(c2, bad_p, bad_f, set())

    def test_detachment_and_crosscall_independence(self) -> None:
        paths, exp_paths = [_VALID_PATH], [_VALID_PATH]
        out_paths = _run_guarded([paths, exp_paths], getattr(s, "_validate_commit_paths"), paths, set(), expected=exp_paths)
        self.assertIsNot(out_paths, paths)

        p_h, f_h, commits = _make_chain(1)
        expected_prior = _make_expected_chain(1)
        out_commits = _run_guarded(
            [commits, expected_prior, out_paths, exp_paths],
            getattr(s, "_validate_maintenance_commits"),
            commits,
            p_h,
            f_h,
            set(),
            expected=expected_prior,
        )
        self.assertIsNot(out_commits, commits)
        self.assertIsNot(out_commits[0], commits[0])
        self.assertIsNot(out_commits[0]["paths"], commits[0]["paths"])

        orig_prior_child = out_commits[0]
        orig_prior_paths = out_commits[0]["paths"]
        orig_exp_paths_0 = exp_paths[0]

        _, _, commits2 = _make_chain(2)
        exp_c2 = _make_expected_chain(2)
        peer_roots = [commits2, exp_c2, out_commits, expected_prior, out_paths, exp_paths]

        _run_guarded(
            peer_roots,
            getattr(s, "_validate_maintenance_commits"),
            commits2,
            "0" * 40,
            f"{2:040x}",
            set(),
            expected=exp_c2,
        )

        def replace_prior_child_wrapper(*a: Any, **kw: Any) -> Any:
            ret = getattr(s, "_validate_maintenance_commits")(*a, **kw)
            out_commits[0] = copy.deepcopy(orig_prior_child)
            return ret

        with self.assertRaises(AssertionError) as ctx_prior:
            _run_guarded(
                peer_roots,
                replace_prior_child_wrapper,
                commits2,
                "0" * 40,
                f"{2:040x}",
                set(),
                expected=exp_c2,
            )
        self.assertEqual(str(ctx_prior.exception), h._ERR_CUSTODY)
        self.assertIs(out_commits[0], orig_prior_child)
        self.assertIs(out_commits[0]["paths"], orig_prior_paths)

        def replace_prior_paths_wrapper(*a: Any, **kw: Any) -> Any:
            ret = getattr(s, "_validate_maintenance_commits")(*a, **kw)
            out_commits[0]["paths"] = list(orig_prior_paths)
            return ret

        with self.assertRaises(AssertionError) as ctx_paths:
            _run_guarded(
                peer_roots,
                replace_prior_paths_wrapper,
                commits2,
                "0" * 40,
                f"{2:040x}",
                set(),
                expected=exp_c2,
            )
        self.assertEqual(str(ctx_paths.exception), h._ERR_CUSTODY)
        self.assertIs(out_commits[0], orig_prior_child)
        self.assertIs(out_commits[0]["paths"], orig_prior_paths)

        def mutate_oracle_wrapper(*a: Any, **kw: Any) -> Any:
            ret = getattr(s, "_validate_maintenance_commits")(*a, **kw)
            exp_paths.append(_VALID_PATH)
            return ret

        with self.assertRaises(AssertionError) as ctx_oracle:
            _run_guarded(
                peer_roots,
                mutate_oracle_wrapper,
                commits2,
                "0" * 40,
                f"{2:040x}",
                set(),
                expected=exp_c2,
            )
        self.assertEqual(str(ctx_oracle.exception), h._ERR_CUSTODY)
        self.assertEqual(len(exp_paths), 1)
        self.assertIs(exp_paths[0], orig_exp_paths_0)

        _run_guarded(
            peer_roots,
            getattr(s, "_validate_maintenance_commits"),
            commits2,
            "0" * 40,
            f"{2:040x}",
            set(),
            expected=exp_c2,
        )
        h.assert_exact_typed(out_commits, expected_prior)
        h.assert_exact_typed(exp_paths, [_VALID_PATH])

    def test_witness_kills_real_bypass_and_caller_append(self) -> None:
        p_h, f_h, commits = _make_chain(1)
        expected = _make_expected_chain(1)
        orig_commit_0 = commits[0]

        fk = h.ForeignStrKey("caller_extra")
        child_dict = {"inner": "data"}
        caller_ctx = {fk: child_dict}
        orig_child = child_dict
        aux_roots = [commits, caller_ctx]

        _run_guarded(aux_roots, getattr(s, "_validate_maintenance_commits"), commits, p_h, f_h, set(), expected=expected)

        def foreign_child_only_wrapper(*a: Any, **kw: Any) -> Any:
            ret = getattr(s, "_validate_maintenance_commits")(*a, **kw)
            caller_ctx[fk] = {"inner": "data"}
            return ret

        with self.assertRaises(AssertionError) as ctx_f:
            _run_guarded(aux_roots, foreign_child_only_wrapper, commits, p_h, f_h, set(), expected=expected)
        self.assertEqual(str(ctx_f.exception), h._ERR_CUSTODY)
        self.assertIs(caller_ctx[fk], orig_child)
        self.assertIs(commits[0], orig_commit_0)

        _run_guarded(aux_roots, getattr(s, "_validate_maintenance_commits"), commits, p_h, f_h, set(), expected=expected)

        def ordinary_child_only_wrapper(*a: Any, **kw: Any) -> Any:
            ret = getattr(s, "_validate_maintenance_commits")(*a, **kw)
            commits[0] = copy.deepcopy(commits[0])
            return ret

        with self.assertRaises(AssertionError) as ctx_o:
            _run_guarded(aux_roots, ordinary_child_only_wrapper, commits, p_h, f_h, set(), expected=expected)
        self.assertEqual(str(ctx_o.exception), h._ERR_CUSTODY)
        self.assertIs(commits[0], orig_commit_0)
        self.assertIs(caller_ctx[fk], orig_child)

        _run_guarded(aux_roots, getattr(s, "_validate_maintenance_commits"), commits, p_h, f_h, set(), expected=expected)

        def caller_append_wrapper(*a: Any, **kw: Any) -> Any:
            ret = getattr(s, "_validate_maintenance_commits")(*a, **kw)
            commits.append(_make_commit(1, f_h, f_h))
            return ret

        with self.assertRaises(AssertionError) as ctx_a:
            _run_guarded([commits], caller_append_wrapper, commits, p_h, f_h, set(), expected=expected)
        self.assertEqual(str(ctx_a.exception), h._ERR_CUSTODY)
        self.assertEqual(len(commits), 1)
        self.assertIs(commits[0], orig_commit_0)

        _run_guarded([commits], getattr(s, "_validate_maintenance_commits"), commits, p_h, f_h, set(), expected=expected)

        p_h12, f_h12, c12 = _make_chain(12)
        exp12 = _make_expected_chain(12)
        _run_guarded([c12], getattr(s, "_validate_maintenance_commits"), c12, p_h12, f_h12, set(), expected=exp12)

        orig_commits_fn = s._validate_maintenance_commits

        def corrupt_returned_sha(*a: Any, **kw: Any) -> Any:
            ret = orig_commits_fn(*a, **kw)
            corrupted = copy.deepcopy(ret)
            corrupted[-1]["proposal_sha256"] = "9" * 64
            return corrupted

        try:
            s._validate_maintenance_commits = corrupt_returned_sha
            with self.assertRaises(AssertionError) as ctx_corrupt:
                _run_guarded([c12], getattr(s, "_validate_maintenance_commits"), c12, p_h12, f_h12, set(), expected=exp12)
            self.assertEqual(str(ctx_corrupt.exception), h._ERR_TYPED)
        finally:
            s._validate_maintenance_commits = orig_commits_fn

        _run_guarded([c12], getattr(s, "_validate_maintenance_commits"), c12, p_h12, f_h12, set(), expected=exp12)

        orig_paths_fn = s._validate_commit_paths

        def bypass_paths(paths: Any, control_blob_paths: Any, *a: Any, **kw: Any) -> Any:
            return list(paths)

        def assert_protected_rejected() -> None:
            try:
                getattr(s, "_validate_commit_paths")([_RUNNER_PY], set(), profile=s._PROFILE_V2)
            except s.MaintenanceTransitionRecordError:
                return
            raise AssertionError("protected path guard failed to reject runner")

        assert_protected_rejected()
        try:
            s._validate_commit_paths = bypass_paths
            with self.assertRaises(AssertionError):
                assert_protected_rejected()
        finally:
            s._validate_commit_paths = orig_paths_fn
        assert_protected_rejected()


if __name__ == "__main__":
    unittest.main()
