"""Unit tests for scripts/two_site_approved_main_merge_entry.py under AE03."""

from __future__ import annotations

import contextlib
import copy
import os
import pathlib
import sys
import traceback
import unittest
from unittest.mock import patch

# Pre-import collection size guard
_THIS_FILE = pathlib.Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent.parent
_SRC_FILE = _REPO_ROOT / "scripts" / "two_site_approved_main_merge_entry.py"

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
    ApprovedMainMergeEntryError,
    validate_approved_main_merge_entry,
)

GOLDEN_ENTRY: dict[str, object] = {
    "head": "c" * 40,
    "parents": ["a" * 40, "b" * 40],
    "paths": [
        "deploy/market-data/compose.yml",
        "scripts/project_group_market_to_estimator.py",
    ],
    "proposal_sha256": "d" * 64,
    "reviewer_run_id": "reviewer-merge-1",
    "test_evidence_sha256": "e" * 64,
    "worker_run_id": "worker-merge-1",
}

GOLDEN_KWARGS: dict[str, object] = {
    "prior_integration_head": "a" * 40,
    "approved_main_sha": "b" * 40,
    "control_blob_paths": frozenset({"scripts/two_site_codex_autopilot.py"}),
}

GOLDEN_EXPECTED: dict[str, object] = {
    "head": "c" * 40,
    "parents": ["a" * 40, "b" * 40],
    "paths": [
        "deploy/market-data/compose.yml",
        "scripts/project_group_market_to_estimator.py",
    ],
    "proposal_sha256": "d" * 64,
    "reviewer_run_id": "reviewer-merge-1",
    "test_evidence_sha256": "e" * 64,
    "worker_run_id": "worker-merge-1",
}


class TestTwoSiteApprovedMainMergeEntry(unittest.TestCase):
    """Characterization and boundary tests for validate_approved_main_merge_entry."""

    def assert_negative_mutation(
        self,
        mutate_fn,
        *,
        expected_error_cls=ApprovedMainMergeEntryError,
        expected_message="invalid approved main merge entry",
        expected_exc_instance=None,
        fault_ctx=None,
    ):
        entry_before = copy.deepcopy(GOLDEN_ENTRY)
        kwargs_before = copy.deepcopy(GOLDEN_KWARGS)
        oracle_before = copy.deepcopy(GOLDEN_EXPECTED)

        res_before = validate_approved_main_merge_entry(
            copy.deepcopy(GOLDEN_ENTRY), **copy.deepcopy(GOLDEN_KWARGS)
        )
        self.assertEqual(res_before, oracle_before)

        holder = {
            "entry": copy.deepcopy(GOLDEN_ENTRY),
            "kwargs": copy.deepcopy(GOLDEN_KWARGS),
        }
        mutate_fn(holder)

        bad_entry_snap = copy.deepcopy(holder["entry"])
        bad_kwargs_snap = copy.deepcopy(holder["kwargs"])

        ctx_manager = fault_ctx if fault_ctx is not None else contextlib.nullcontext()
        with ctx_manager:
            with self.assertRaises(expected_error_cls) as ctx:
                validate_approved_main_merge_entry(holder["entry"], **holder["kwargs"])

        exc = ctx.exception
        if expected_exc_instance is not None:
            self.assertIs(exc, expected_exc_instance)
        if expected_message is not None:
            self.assertEqual(str(exc), expected_message)
        if issubclass(expected_error_cls, ApprovedMainMergeEntryError):
            self.assertIsNone(exc.__cause__)
            self.assertTrue(exc.__suppress_context__)

        self.assertEqual(GOLDEN_ENTRY, entry_before)
        self.assertEqual(GOLDEN_KWARGS, kwargs_before)
        self.assertEqual(GOLDEN_EXPECTED, oracle_before)
        self.assertEqual(holder["entry"], bad_entry_snap)
        self.assertEqual(holder["kwargs"], bad_kwargs_snap)

        res_after = validate_approved_main_merge_entry(
            copy.deepcopy(GOLDEN_ENTRY), **copy.deepcopy(GOLDEN_KWARGS)
        )
        self.assertEqual(res_after, oracle_before)
        return exc

    def test_golden_success_and_kwargs_variants(self):
        res = validate_approved_main_merge_entry(
            copy.deepcopy(GOLDEN_ENTRY), **copy.deepcopy(GOLDEN_KWARGS)
        )
        self.assertEqual(res, GOLDEN_EXPECTED)
        self.assertEqual(len(res), 7)
        self.assertEqual(set(res.keys()), set(GOLDEN_EXPECTED.keys()))

        paths_32 = [f"scripts/p_{i:02d}.py" for i in range(32)]
        e32 = copy.deepcopy(GOLDEN_ENTRY)
        e32["paths"] = paths_32
        r32 = validate_approved_main_merge_entry(e32, **copy.deepcopy(GOLDEN_KWARGS))
        self.assertEqual(r32["paths"], paths_32)

        u_entry = copy.deepcopy(GOLDEN_ENTRY)
        u_entry["paths"] = ["docs/گزارش\u200cها.md", "src/file.txt"]
        ru = validate_approved_main_merge_entry(u_entry, **copy.deepcopy(GOLDEN_KWARGS))
        self.assertEqual(ru["paths"], ["docs/گزارش\u200cها.md", "src/file.txt"])

        root_entry = copy.deepcopy(GOLDEN_ENTRY)
        root_entry["paths"] = ["README.md", "deploy/compose.yml"]
        rr = validate_approved_main_merge_entry(root_entry, **copy.deepcopy(GOLDEN_KWARGS))
        self.assertEqual(rr["paths"], ["README.md", "deploy/compose.yml"])

    def test_returned_nested_detachment(self):
        entry = copy.deepcopy(GOLDEN_ENTRY)
        kwargs = copy.deepcopy(GOLDEN_KWARGS)
        res = validate_approved_main_merge_entry(entry, **kwargs)
        self.assertEqual(res, GOLDEN_EXPECTED)

        res["parents"].append("extra_parent")
        res["parents"][0] = "f" * 40
        res["paths"].append("extra_path.py")
        res["paths"][0] = "mutated_path.py"

        self.assertEqual(entry, GOLDEN_ENTRY)
        self.assertEqual(GOLDEN_ENTRY["parents"], ["a" * 40, "b" * 40])
        self.assertEqual(len(GOLDEN_ENTRY["paths"]), 2)

        res2 = validate_approved_main_merge_entry(entry, **kwargs)
        self.assertEqual(res2, GOLDEN_EXPECTED)
        self.assertEqual(res2["parents"], ["a" * 40, "b" * 40])

    def test_caller_hashes_dimensions_and_zero_calls(self):
        class StrSub(str):
            pass

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

        for bh in bad_hashes:
            self.assert_negative_mutation(lambda h, v=bh: h["kwargs"].__setitem__("prior_integration_head", v))
            self.assert_negative_mutation(lambda h, v=bh: h["kwargs"].__setitem__("approved_main_sha", v))

        self.assert_negative_mutation(lambda h: h["kwargs"].__setitem__("approved_main_sha", "a" * 40))

        @contextlib.contextmanager
        def zero_calls_ctx():
            with patch(
                "scripts.two_site_approved_main_merge_entry._validate_run_id"
            ) as m_run, patch(
                "scripts.two_site_approved_main_merge_entry._validate_control_path"
            ) as m_ctrl:
                yield
                m_run.assert_not_called()
                m_ctrl.assert_not_called()

        def mutate_zero_calls_p(h):
            h["entry"] = {}
            h["kwargs"]["prior_integration_head"] = "bad_sha"
            h["kwargs"]["control_blob_paths"] = frozenset({"/invalid/control"})

        self.assert_negative_mutation(mutate_zero_calls_p, fault_ctx=zero_calls_ctx())

        def mutate_zero_calls_a(h):
            h["entry"] = {}
            h["kwargs"]["approved_main_sha"] = "bad_sha"
            h["kwargs"]["control_blob_paths"] = frozenset({"/invalid/control"})

        self.assert_negative_mutation(mutate_zero_calls_a, fault_ctx=zero_calls_ctx())

    def test_control_container_and_paths(self):
        class MyFS(frozenset):
            pass

        for bad_container in [
            {"scripts/two_site_codex_autopilot.py"},
            ["scripts/two_site_codex_autopilot.py"],
            {"a": "b"},
            None,
            MyFS({"scripts/two_site_codex_autopilot.py"}),
        ]:
            self.assert_negative_mutation(
                lambda h, c=bad_container: h["kwargs"].__setitem__("control_blob_paths", c)
            )

        @contextlib.contextmanager
        def zero_ctrl_calls_ctx():
            with patch(
                "scripts.two_site_approved_main_merge_entry._validate_control_path"
            ) as m_ctrl:
                yield
                m_ctrl.assert_not_called()

        too_many_controls = frozenset({f"/invalid/ctrl_{i:03d}.py" for i in range(257)})
        self.assert_negative_mutation(
            lambda h: h["kwargs"].__setitem__("control_blob_paths", too_many_controls),
            fault_ctx=zero_ctrl_calls_ctx(),
        )

        self.assert_negative_mutation(
            lambda h: h["kwargs"].__setitem__("control_blob_paths", frozenset({"/absolute/path"}))
        )
        self.assert_negative_mutation(
            lambda h: h["kwargs"].__setitem__("control_blob_paths", frozenset({""}))
        )

        self.assert_negative_mutation(
            lambda h: h["entry"].__setitem__(
                "paths", ["scripts/two_site_codex_autopilot.py", "src/other.py"]
            )
        )

    def test_entry_container_and_keys(self):
        class DictSub(dict):
            pass

        for bad_entry in [
            ["not", "a", "dict"],
            "string_entry",
            123,
            None,
            DictSub(copy.deepcopy(GOLDEN_ENTRY)),
        ]:
            self.assert_negative_mutation(lambda h, be=bad_entry: h.__setitem__("entry", be))

        for k in GOLDEN_ENTRY.keys():
            self.assert_negative_mutation(lambda h, key=k: h["entry"].pop(key))

        self.assert_negative_mutation(lambda h: h["entry"].__setitem__("extra_field", "unexpected"))

        for k in ["head", "parents", "paths", "worker_run_id", "reviewer_run_id", "proposal_sha256", "test_evidence_sha256"]:
            self.assert_negative_mutation(lambda h, key=k: h["entry"].__setitem__(key, 12345))

    def test_head_and_parents_variations(self):
        class S(str):
            pass

        for bad_head in ["c" * 39, "c" * 41, "C" + "c" * 39, "z" * 40, S("c" * 40), 99]:
            self.assert_negative_mutation(lambda h, v=bad_head: h["entry"].__setitem__("head", v))

        self.assert_negative_mutation(lambda h: h["entry"].__setitem__("head", "a" * 40))
        self.assert_negative_mutation(lambda h: h["entry"].__setitem__("head", "b" * 40))

        for bad_parents in [
            ("a" * 40, "b" * 40),
            ["a" * 40],
            ["a" * 40, "b" * 40, "c" * 40],
            ["b" * 40, "a" * 40],
            ["f" * 40, "b" * 40],
            ["a" * 40, "f" * 40],
            ["a" * 40, "a" * 40],
            ["a" * 40, 123],
            [S("a" * 40), "b" * 40],
        ]:
            self.assert_negative_mutation(lambda h, bp=bad_parents: h["entry"].__setitem__("parents", bp))

    def test_paths_variations(self):
        class S(str):
            pass

        paths_33 = [f"scripts/p_{i:02d}.py" for i in range(33)]
        self.assert_negative_mutation(lambda h: h["entry"].__setitem__("paths", paths_33))

        for bad_p in [
            ("deploy/a.py",),
            [],
            [123],
            [S("deploy/a.py")],
            ["deploy/a.py", "deploy/a.py"],
            ["scripts/b.py", "deploy/a.py"],
            ["/deploy/a.py"],
            ["deploy\\a.py"],
            ["deploy/./a.py"],
            ["./deploy/a.py"],
            ["deploy/../a.py"],
            ["deploy//a.py"],
            ["deploy/*.py"],
            ["deploy/?a.py"],
            ["deploy/[a].py"],
            ["deploy/{a}.py"],
            ["deploy/\x00a.py"],
            ["deploy/\na.py"],
            ["deploy/\x7fa.py"],
            ["deploy/\x1fa.py"],
            [".git/config"],
            ["deploy/.git/foo"],
            [".local/share"],
            ["deploy/.local/foo"],
            [".env"],
            [".env.local"],
            ["deploy/.env.prod"],
            ["credentials"],
            ["deploy/credentials/key.pem"],
            ["secrets"],
            ["deploy/secrets/key.pem"],
            ["C:deploy/a.py"],
            ["deploy:a.py"],
            [" deploy/a.py"],
            ["deploy/a.py "],
            ["a" * 1025],
        ]:
            self.assert_negative_mutation(lambda h, bp=bad_p: h["entry"].__setitem__("paths", bp))

    def test_ids_and_hashes_variations(self):
        for bad_id in ["", "@bad", "id with space", 123, ["id"]]:
            self.assert_negative_mutation(lambda h, bi=bad_id: h["entry"].__setitem__("worker_run_id", bi))
            self.assert_negative_mutation(lambda h, bi=bad_id: h["entry"].__setitem__("reviewer_run_id", bi))

        self.assert_negative_mutation(lambda h: h["entry"].__setitem__("reviewer_run_id", "worker-merge-1"))

        for bad_h64 in ["d" * 63, "d" * 65, "D" + "d" * 63, "g" * 64, 123, None]:
            self.assert_negative_mutation(lambda h, bh=bad_h64: h["entry"].__setitem__("proposal_sha256", bh))
            self.assert_negative_mutation(lambda h, bh=bad_h64: h["entry"].__setitem__("test_evidence_sha256", bh))

    def test_lower_helpers_wrapping_and_sentinels_and_traceback(self):
        self.assert_negative_mutation(
            lambda h: None,
            fault_ctx=patch(
                "scripts.two_site_approved_main_merge_entry._validate_run_id",
                side_effect=ValueError("run_id_failure"),
            ),
        )

        self.assert_negative_mutation(
            lambda h: None,
            fault_ctx=patch(
                "scripts.two_site_approved_main_merge_entry._validate_control_path",
                side_effect=ValueError("control_failure"),
            ),
        )

        ki_sentinel = KeyboardInterrupt("stop-now")
        self.assert_negative_mutation(
            lambda h: None,
            expected_error_cls=KeyboardInterrupt,
            expected_message="stop-now",
            expected_exc_instance=ki_sentinel,
            fault_ctx=patch(
                "scripts.two_site_approved_main_merge_entry._validate_run_id",
                side_effect=ki_sentinel,
            ),
        )

        se_sentinel = SystemExit(77)
        self.assert_negative_mutation(
            lambda h: None,
            expected_error_cls=SystemExit,
            expected_message="77",
            expected_exc_instance=se_sentinel,
            fault_ctx=patch(
                "scripts.two_site_approved_main_merge_entry._validate_control_path",
                side_effect=se_sentinel,
            ),
        )

        marker = "UNIQUE_MARKER_TRACE_771122"
        exc = self.assert_negative_mutation(
            lambda h: None,
            fault_ctx=patch(
                "scripts.two_site_approved_main_merge_entry._validate_run_id",
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
