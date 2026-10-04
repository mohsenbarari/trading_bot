"""Unit tests for scripts/two_site_main_transition_sequence.py under AE03."""

from __future__ import annotations

import contextlib
import copy
import os
import pathlib
import sys
import traceback
import unittest
from typing import Any
from unittest.mock import patch

# Pre-import collection size guard
_THIS_FILE = pathlib.Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
_SRC_FILE = _REPO_ROOT / "scripts" / "two_site_main_transition_sequence.py"

_src_stat = os.stat(_SRC_FILE)
_test_stat = os.stat(_THIS_FILE)

with open(_SRC_FILE, "rb") as _f_src:
    _src_bytes = _f_src.read(6001)
with open(_THIS_FILE, "rb") as _f_test:
    _test_bytes = _f_test.read(24001)

if len(_src_bytes) > 6000 or len(_src_bytes) != _src_stat.st_size:
    raise AssertionError("Source size exceeds 6000 bytes or does not match stat")
if len(_test_bytes) > 24000 or len(_test_bytes) != _test_stat.st_size:
    raise AssertionError("Test size exceeds 24000 bytes or does not match stat")
if _src_stat.st_size + _test_stat.st_size > 30000:
    raise AssertionError("Combined size exceeds 30000 bytes")

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.two_site_approved_main_merge_entry import (
    validate_approved_main_merge_entry,
)
from scripts.two_site_main_transition_sequence import (
    MainTransitionSequenceError,
    validate_main_transition_sequence,
)
from scripts.two_site_maintenance_transition_record import (
    _validate_maintenance_commits,
)


def _make_commit(
    head: str,
    parent: str,
    paths: list[str] | None = None,
    worker_run_id: str = "worker-run-1",
    reviewer_run_id: str = "reviewer-run-1",
) -> dict[str, Any]:
    return {
        "head": head,
        "parent": parent,
        "paths": paths or ["scripts/two_site_worker.py"],
        "proposal_sha256": "7" * 64,
        "reviewer_run_id": reviewer_run_id,
        "test_evidence_sha256": "8" * 64,
        "worker_run_id": worker_run_id,
    }


def _build_sequence_data(pre_count: int, post_count: int):
    prior_head = "0" * 40
    approved_main_sha = "f" * 40
    controls = frozenset({"scripts/two_site_codex_autopilot.py"})

    pre_commits: list[dict[str, Any]] = []
    current = prior_head
    for i in range(pre_count):
        h = f"{i + 1:040x}"
        pre_commits.append(
            _make_commit(
                h,
                current,
                worker_run_id=f"worker-pre-{i + 1}",
                reviewer_run_id=f"reviewer-pre-{i + 1}",
            )
        )
        current = h
    pre_endpoint = current

    main_head = "a" * 40
    main_entry: dict[str, Any] = {
        "head": main_head,
        "parents": [pre_endpoint, approved_main_sha],
        "paths": ["src/feature.py"],
        "proposal_sha256": "9" * 64,
        "reviewer_run_id": "reviewer-main-1",
        "test_evidence_sha256": "6" * 64,
        "worker_run_id": "worker-main-1",
    }

    post_commits: list[dict[str, Any]] = []
    current = main_head
    for i in range(post_count):
        h = f"{100 + i:040x}"
        post_commits.append(
            _make_commit(
                h,
                current,
                worker_run_id=f"worker-post-{i + 1}",
                reviewer_run_id=f"reviewer-post-{i + 1}",
            )
        )
        current = h
    new_head = current

    kwargs: dict[str, Any] = {
        "prior_head": prior_head,
        "new_head": new_head,
        "approved_main_sha": approved_main_sha,
        "control_blob_paths": controls,
    }

    expected: dict[str, Any] = {
        "main_integration": copy.deepcopy(main_entry),
        "post_main_commits": copy.deepcopy(post_commits),
        "pre_main_commits": copy.deepcopy(pre_commits),
    }

    return pre_commits, main_entry, post_commits, kwargs, expected


class StrSub(str):
    pass


class ListSub(list):
    pass


class DictSub(dict):
    pass


class FSSub(frozenset):
    pass


class CustomBase(BaseException):
    pass


class TestTwoSiteMainTransitionSequence(unittest.TestCase):
    """Characterization and boundary tests for validate_main_transition_sequence."""

    def assert_negative_mutation(
        self,
        mutate_fn,
        *,
        expected_error_cls=MainTransitionSequenceError,
        expected_message="invalid main transition sequence",
        expected_exc_instance=None,
        fault_ctx=None,
        base_pre_count=2,
        base_post_count=2,
    ):
        pre, main, post, kwargs, expected = _build_sequence_data(
            base_pre_count, base_post_count
        )
        pre_before = copy.deepcopy(pre)
        main_before = copy.deepcopy(main)
        post_before = copy.deepcopy(post)
        kwargs_before = copy.deepcopy(kwargs)
        oracle_before = copy.deepcopy(expected)

        res_before = validate_main_transition_sequence(
            copy.deepcopy(pre),
            copy.deepcopy(main),
            copy.deepcopy(post),
            **copy.deepcopy(kwargs),
        )
        self.assertEqual(res_before, oracle_before)

        holder = {
            "pre": copy.deepcopy(pre),
            "main": copy.deepcopy(main),
            "post": copy.deepcopy(post),
            "kwargs": copy.deepcopy(kwargs),
        }
        mutate_fn(holder)

        bad_snap = copy.deepcopy(holder)

        ctx_manager = fault_ctx if fault_ctx is not None else contextlib.nullcontext()
        with ctx_manager:
            with self.assertRaises(expected_error_cls) as ctx:
                validate_main_transition_sequence(
                    holder["pre"],
                    holder["main"],
                    holder["post"],
                    **holder["kwargs"],
                )

        exc = ctx.exception
        if expected_exc_instance is not None:
            self.assertIs(exc, expected_exc_instance)
        if expected_message is not None:
            self.assertEqual(str(exc), expected_message)
        if issubclass(expected_error_cls, MainTransitionSequenceError):
            self.assertIsNone(exc.__cause__)
            self.assertTrue(exc.__suppress_context__)

        self.assertEqual(pre, pre_before)
        self.assertEqual(main, main_before)
        self.assertEqual(post, post_before)
        self.assertEqual(kwargs, kwargs_before)
        self.assertEqual(expected, oracle_before)
        self.assertEqual(holder, bad_snap)

        res_after = validate_main_transition_sequence(
            copy.deepcopy(pre),
            copy.deepcopy(main),
            copy.deepcopy(post),
            **copy.deepcopy(kwargs),
        )
        self.assertEqual(res_after, oracle_before)
        return exc

    def test_positive_configurations_and_detachment(self):
        configs = [(0, 0), (2, 2), (13, 0), (0, 13), (15, 16)]
        for pre_c, post_c in configs:
            pre, main, post, kwargs, expected = _build_sequence_data(pre_c, post_c)
            res = validate_main_transition_sequence(
                copy.deepcopy(pre),
                copy.deepcopy(main),
                copy.deepcopy(post),
                **copy.deepcopy(kwargs),
            )
            self.assertEqual(res, expected)
            self.assertEqual(set(res.keys()), {"pre_main_commits", "main_integration", "post_main_commits"})
            self.assertEqual(len(res["pre_main_commits"]), pre_c)
            self.assertEqual(len(res["post_main_commits"]), post_c)

        pre, main, post, kwargs, expected = _build_sequence_data(2, 2)
        res = validate_main_transition_sequence(pre, main, post, **kwargs)
        res["pre_main_commits"].append({"head": "bad"})
        res["pre_main_commits"][0]["paths"].append("mutated.py")
        res["main_integration"]["parents"].append("extra")
        res["post_main_commits"].append({"head": "bad"})
        res["post_main_commits"][0]["paths"].append("mutated.py")

        self.assertEqual(len(pre), 2)
        self.assertEqual(len(pre[0]["paths"]), 1)
        self.assertEqual(len(main["parents"]), 2)
        self.assertEqual(len(post), 2)

        res2 = validate_main_transition_sequence(pre, main, post, **kwargs)
        self.assertEqual(res2, expected)

    def test_spies_prove_chunk_endpoints_and_args(self):
        pre, main, post, kwargs, expected = _build_sequence_data(13, 13)
        with patch(
            "scripts.two_site_main_transition_sequence._validate_maintenance_commits",
            side_effect=_validate_maintenance_commits,
        ) as spy_maint, patch(
            "scripts.two_site_main_transition_sequence.validate_approved_main_merge_entry",
            side_effect=validate_approved_main_merge_entry,
        ) as spy_main:
            res = validate_main_transition_sequence(pre, main, post, **kwargs)
            self.assertEqual(res, expected)

            self.assertEqual(spy_maint.call_count, 4)
            c0 = spy_maint.call_args_list[0]
            self.assertEqual(len(c0.args[0]), 12)
            self.assertEqual(c0.kwargs["prior_head"], "0" * 40)
            self.assertEqual(c0.kwargs["new_repo_head"], f"{12:040x}")
            self.assertEqual(c0.kwargs["control_blob_paths"], set(kwargs["control_blob_paths"]))

            c1 = spy_maint.call_args_list[1]
            self.assertEqual(len(c1.args[0]), 1)
            self.assertEqual(c1.kwargs["prior_head"], f"{12:040x}")
            self.assertEqual(c1.kwargs["new_repo_head"], f"{13:040x}")

            self.assertEqual(spy_main.call_count, 1)
            cm = spy_main.call_args_list[0]
            self.assertEqual(cm.kwargs["prior_integration_head"], f"{13:040x}")
            self.assertEqual(cm.kwargs["approved_main_sha"], "f" * 40)
            self.assertEqual(cm.kwargs["control_blob_paths"], kwargs["control_blob_paths"])

            c2 = spy_maint.call_args_list[2]
            self.assertEqual(len(c2.args[0]), 12)
            self.assertEqual(c2.kwargs["prior_head"], "a" * 40)
            self.assertEqual(c2.kwargs["new_repo_head"], f"{100 + 11:040x}")

            c3 = spy_maint.call_args_list[3]
            self.assertEqual(len(c3.args[0]), 1)
            self.assertEqual(c3.kwargs["prior_head"], f"{100 + 11:040x}")
            self.assertEqual(c3.kwargs["new_repo_head"], f"{100 + 12:040x}")

    def test_caller_hashes_dimensions_and_zero_calls(self):
        bad_hashes = [
            "a" * 39,
            "a" * 41,
            "",
            "A" + "a" * 39,
            "g" * 40,
            StrSub("a" * 40),
            None,
            True,
            False,
            12345,
            b"a" * 40,
            [[["a" * 40]]],
            {"head": "a" * 40},
        ]
        for key in ["prior_head", "new_head", "approved_main_sha"]:
            for bh in bad_hashes:
                self.assert_negative_mutation(lambda h, k=key, v=bh: h["kwargs"].__setitem__(k, v))

        self.assert_negative_mutation(lambda h: h["kwargs"].__setitem__("prior_head", h["kwargs"]["new_head"]))
        self.assert_negative_mutation(lambda h: h["kwargs"].__setitem__("prior_head", h["kwargs"]["approved_main_sha"]))
        self.assert_negative_mutation(lambda h: h["kwargs"].__setitem__("new_head", h["kwargs"]["approved_main_sha"]))

        @contextlib.contextmanager
        def zero_calls_ctx():
            with patch("scripts.two_site_main_transition_sequence._validate_control_path") as m_c, \
                 patch("scripts.two_site_main_transition_sequence._validate_maintenance_commits") as m_m, \
                 patch("scripts.two_site_main_transition_sequence.validate_approved_main_merge_entry") as m_a:
                yield
                m_c.assert_not_called()
                m_m.assert_not_called()
                m_a.assert_not_called()

        def mutate_zero(h):
            h["kwargs"]["prior_head"] = "bad_hex"
            h["kwargs"]["control_blob_paths"] = frozenset({"/invalid/ctrl"})
            h["pre"] = "not_a_list"

        self.assert_negative_mutation(mutate_zero, fault_ctx=zero_calls_ctx())

    def test_controls_and_zero_calls_on_257(self):
        for bad_c in [
            {"scripts/two_site_codex_autopilot.py"},
            ["scripts/two_site_codex_autopilot.py"],
            {"a": "b"},
            None,
            FSSub({"scripts/two_site_codex_autopilot.py"}),
        ]:
            self.assert_negative_mutation(lambda h, c=bad_c: h["kwargs"].__setitem__("control_blob_paths", c))

        @contextlib.contextmanager
        def zero_ctrl_ctx():
            with patch("scripts.two_site_main_transition_sequence._validate_control_path") as m_c:
                yield
                m_c.assert_not_called()

        too_many = frozenset({f"/bad/ctrl_{i:03d}.py" for i in range(257)})
        self.assert_negative_mutation(
            lambda h: h["kwargs"].__setitem__("control_blob_paths", too_many),
            fault_ctx=zero_ctrl_ctx(),
        )

        self.assert_negative_mutation(
            lambda h: h["kwargs"].__setitem__("control_blob_paths", frozenset({"/absolute/path"}))
        )
        self.assert_negative_mutation(
            lambda h: h["kwargs"].__setitem__("control_blob_paths", frozenset({""}))
        )

    def test_commit_counts_and_zero_calls_on_33(self):
        @contextlib.contextmanager
        def zero_lower_ctx():
            with patch("scripts.two_site_main_transition_sequence._validate_control_path") as m_c, \
                 patch("scripts.two_site_main_transition_sequence._validate_maintenance_commits") as m_m, \
                 patch("scripts.two_site_main_transition_sequence.validate_approved_main_merge_entry") as m_a:
                yield
                m_c.assert_not_called()
                m_m.assert_not_called()
                m_a.assert_not_called()

        for bad_part in ["not_a_list", (1, 2), {"head": "x"}, None, ListSub([])]:
            self.assert_negative_mutation(
                lambda h, bp=bad_part: h.__setitem__("pre", bp),
                fault_ctx=zero_lower_ctx(),
            )
            self.assert_negative_mutation(
                lambda h, bp=bad_part: h.__setitem__("post", bp),
                fault_ctx=zero_lower_ctx(),
            )

        pre33, main33, post33, kwargs33, _ = _build_sequence_data(16, 16)

        def mutate_33(h):
            h["pre"] = copy.deepcopy(pre33)
            h["main"] = copy.deepcopy(main33)
            h["post"] = copy.deepcopy(post33)
            h["kwargs"] = copy.deepcopy(kwargs33)

        self.assert_negative_mutation(
            mutate_33,
            fault_ctx=zero_lower_ctx(),
            base_pre_count=15,
            base_post_count=16,
        )

    def test_entry_key_receipt_path_delegations(self):
        for part in ["pre", "post"]:
            self.assert_negative_mutation(lambda h, p=part: h[p].__setitem__(0, "not_dict"))
            self.assert_negative_mutation(lambda h, p=part: h[p][0].pop("head"))
            self.assert_negative_mutation(lambda h, p=part: h[p][0].pop("proposal_sha256"))
            self.assert_negative_mutation(lambda h, p=part: h[p][0].__setitem__("proposal_sha256", "bad_hex"))
            self.assert_negative_mutation(lambda h, p=part: h[p][0].__setitem__("paths", ["/invalid/path"]))

    def test_graph_links_gaps_duplicates_and_drift(self):
        self.assert_negative_mutation(lambda h: h["pre"][0].__setitem__("parent", "1" * 40))
        self.assert_negative_mutation(lambda h: h["pre"][1].__setitem__("parent", "9" * 40))
        self.assert_negative_mutation(
            lambda h: h["pre"][12].__setitem__("parent", "9" * 40),
            base_pre_count=13,
            base_post_count=1,
        )
        self.assert_negative_mutation(lambda h: h["main"].__setitem__("parents", ["9" * 40, "f" * 40]))
        self.assert_negative_mutation(lambda h: h["main"].__setitem__("parents", [h["main"]["parents"][0], "9" * 40]))
        self.assert_negative_mutation(lambda h: h["main"].__setitem__("parents", ["f" * 40, h["main"]["parents"][0]]))
        self.assert_negative_mutation(lambda h: h["post"][0].__setitem__("parent", "9" * 40))

        self.assert_negative_mutation(
            lambda h: h["pre"][12].__setitem__("head", h["pre"][0]["head"]),
            base_pre_count=13,
            base_post_count=1,
        )
        self.assert_negative_mutation(lambda h: h["post"][0].__setitem__("head", h["pre"][0]["head"]))
        self.assert_negative_mutation(lambda h: h["pre"][0].__setitem__("head", "a" * 40))
        self.assert_negative_mutation(lambda h: h["pre"][0].__setitem__("head", "0" * 40))
        self.assert_negative_mutation(lambda h: h["pre"][0].__setitem__("head", "f" * 40))
        self.assert_negative_mutation(lambda h: h["post"][0].__setitem__("head", "f" * 40))
        self.assert_negative_mutation(lambda h: h["kwargs"].__setitem__("new_head", "9" * 40))

    def test_main_integration_malformed(self):
        for bad_m in ["string_entry", [1, 2], 123, None, DictSub(copy.deepcopy(_build_sequence_data(2, 2)[1]))]:
            self.assert_negative_mutation(lambda h, bm=bad_m: h.__setitem__("main", bm))

        self.assert_negative_mutation(lambda h: h["main"].pop("parents"))
        self.assert_negative_mutation(lambda h: h["main"].pop("head"))
        self.assert_negative_mutation(lambda h: h["main"].__setitem__("unexpected_extra", 99))

    def test_lower_helper_wrapping_sentinels_and_trace(self):
        for target in [
            "scripts.two_site_main_transition_sequence._validate_maintenance_commits",
            "scripts.two_site_main_transition_sequence.validate_approved_main_merge_entry",
            "scripts.two_site_main_transition_sequence._validate_control_path",
        ]:
            self.assert_negative_mutation(
                lambda h: None,
                fault_ctx=patch(target, side_effect=ValueError("internal_error")),
            )

        ki_sentinel = KeyboardInterrupt("stop_sequence")
        self.assert_negative_mutation(
            lambda h: None,
            expected_error_cls=KeyboardInterrupt,
            expected_message="stop_sequence",
            expected_exc_instance=ki_sentinel,
            fault_ctx=patch(
                "scripts.two_site_main_transition_sequence._validate_maintenance_commits",
                side_effect=ki_sentinel,
            ),
        )

        se_sentinel = SystemExit(42)
        self.assert_negative_mutation(
            lambda h: None,
            expected_error_cls=SystemExit,
            expected_message="42",
            expected_exc_instance=se_sentinel,
            fault_ctx=patch(
                "scripts.two_site_main_transition_sequence._validate_control_path",
                side_effect=se_sentinel,
            ),
        )

        cb_sentinel = CustomBase("custom_base_sentinel")
        self.assert_negative_mutation(
            lambda h: None,
            expected_error_cls=CustomBase,
            expected_message="custom_base_sentinel",
            expected_exc_instance=cb_sentinel,
            fault_ctx=patch(
                "scripts.two_site_main_transition_sequence.validate_approved_main_merge_entry",
                side_effect=cb_sentinel,
            ),
        )

        marker = "UNIQUE_CALLER_MARKER_SEQ_998877"
        exc = self.assert_negative_mutation(
            lambda h: None,
            fault_ctx=patch(
                "scripts.two_site_main_transition_sequence._validate_maintenance_commits",
                side_effect=ValueError(marker),
            ),
        )
        formatted = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.assertNotIn(marker, formatted)

    def test_file_size_guards(self):
        src_size = os.stat(_SRC_FILE).st_size
        test_size = os.stat(_THIS_FILE).st_size
        self.assertLessEqual(src_size, 6000)
        self.assertLessEqual(test_size, 24000)
        self.assertLessEqual(src_size + test_size, 30000)


if __name__ == "__main__":
    unittest.main()
