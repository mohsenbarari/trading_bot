"""Tests for two_site_compact_review_context."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from scripts import two_site_proposal_runner as runner
from scripts.two_site_compact_review_context import (
    CompactReviewError,
    build_one_file_compact_review_material,
    parse_one_file_compact_review_spec,
)


def _init_repo(tmp_path: Path) -> tuple[str, str, str]:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "CI"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@example.com"], cwd=tmp_path, check=True, capture_output=True)

    body = "def sample_function():\n    return 'payload'\n\n"
    padding = "# " + ("x" * 100 + "\n") * 85
    content = body + padding
    tracked_file = tmp_path / "target_pkg.py"
    tracked_file.write_text(content, encoding="utf-8")

    subprocess.run(["git", "add", "target_pkg.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add target"], cwd=tmp_path, check=True, capture_output=True)

    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return head, content, sha


class TestCompactReviewContext(unittest.TestCase):
    def test_build_material_success_determinism_and_no_leak(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            head, content, sha = _init_repo(root)
            path = "target_pkg.py"
            new_test_path = "tests/test_new.py"

            task = {
                "task_id": "T-1",
                "allowed_files": [path, new_test_path, "other.py"],
            }
            sources = {
                path: {"sha256": sha, "content": content},
                "other.py": {"sha256": "abc" * 21 + "a", "content": "other_content\n"},
            }
            transport = {
                "changed_paths": [path, new_test_path],
                "unified_diff": "--- a/target_pkg.py\n+++ b/target_pkg.py\n",
            }
            receipt = {"status": "PASSED", "tests_run": 2}

            material1 = build_one_file_compact_review_material(
                root, task, sources, transport, receipt, head, path, ["sample_function"]
            )
            material2 = build_one_file_compact_review_material(
                root, task, sources, transport, receipt, head, path, ["sample_function"]
            )

            self.assertEqual(material1["source_head"], head)
            self.assertEqual(material1["task"], task)
            self.assertEqual(
                material1["scope_marker"],
                "EXCERPT_NOT_FULL_SOURCE_CODEX_FULL_REVIEW_REQUIRED",
            )
            self.assertEqual(material1["proposal"], transport)
            self.assertEqual(material1["pre_review_test_evidence"], receipt)
            self.assertEqual(
                material1["source_manifest"],
                {path: sha, "other.py": "abc" * 21 + "a"},
            )
            self.assertNotIn(path, material1["other_original_sources"])
            self.assertEqual(
                material1["other_original_sources"]["other.py"],
                sources["other.py"],
            )

            excerpt = material1["exact_original_source_excerpt"]
            self.assertEqual(excerpt["path"], path)
            self.assertEqual(excerpt["symbols"], ["sample_function"])
            self.assertEqual(excerpt["sha256"], sha)

            ser1 = json.dumps(material1, ensure_ascii=False, sort_keys=True)
            ser2 = json.dumps(material2, ensure_ascii=False, sort_keys=True)
            self.assertEqual(ser1, ser2)
            self.assertNotIn(content, ser1)

    def test_stale_head_and_sha_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            head, content, sha = _init_repo(root)
            path = "target_pkg.py"
            task = {"allowed_files": [path]}
            sources = {path: {"sha256": sha, "content": content}}
            transport = {"changed_paths": [path]}
            receipt = {"status": "PASSED"}

            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources, transport, receipt, "0" * 40, path, ["sample_function"]
                )

            sources_bad_sha = {path: {"sha256": "0" * 64, "content": content}}
            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources_bad_sha, transport, receipt, head, path, ["sample_function"]
                )

            sources_upper_sha = {path: {"sha256": sha.upper(), "content": content}}
            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources_upper_sha, transport, receipt, head, path, ["sample_function"]
                )

    def test_nonexistent_symbol_and_wrong_changed_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            head, content, sha = _init_repo(root)
            path = "target_pkg.py"
            task = {"allowed_files": [path]}
            sources = {path: {"sha256": sha, "content": content}}
            transport = {"changed_paths": [path]}
            receipt = {"status": "PASSED"}

            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources, transport, receipt, head, path, ["missing_symbol"]
                )

            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources, {"changed_paths": ["other.py"]}, receipt, head, path, ["sample_function"]
                )

            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources, {"changed_paths": [path, path]}, receipt, head, path, ["sample_function"]
                )

            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources, {"changed_paths": [{"unhashable": 1}]}, receipt, head, path, ["sample_function"]
                )

            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources, transport, receipt, head, path, [{"unhashable": 1}]
                )

    def test_overbudget_and_secret_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            head, content, sha = _init_repo(root)
            path = "target_pkg.py"
            task = {"allowed_files": [path]}
            transport = {"changed_paths": [path]}
            receipt = {"status": "PASSED"}

            sources_huge = {
                path: {"sha256": sha, "content": content},
                "huge.py": {"sha256": "0" * 64, "content": "x" * 130000},
            }
            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources_huge, transport, receipt, head, path, ["sample_function"]
                )

            fake_secret = "gh" + "p_" + ("x" * 36)
            self.assertTrue(bool(runner.SECRET.search(fake_secret)))
            receipt_secret = {"status": "PASSED", "token": fake_secret}
            sources_norm = {path: {"sha256": sha, "content": content}}
            with self.assertRaises(CompactReviewError):
                build_one_file_compact_review_material(
                    root, task, sources_norm, transport, receipt_secret, head, path, ["sample_function"]
                )


class TestParseOneFileCompactReviewSpec(unittest.TestCase):
    def test_absence(self) -> None:
        self.assertIsNone(parse_one_file_compact_review_spec("Simple task description"))
        self.assertIsNone(
            parse_one_file_compact_review_spec(
                "Task\nCANONICAL_SOURCE_EXCERPTS_JSON:\n[{\"path\": \"foo.py\"}]"
            )
        )
        self.assertIsNone(
            parse_one_file_compact_review_spec(
                "Task mentioning COMPACT_REVIEW_SYMBOLS_JSON: in prose without marker"
            )
        )

    def test_valid_standalone_and_before_canonical_marker(self) -> None:
        goal_standalone = (
            "Refactor feature\n\n"
            "COMPACT_REVIEW_SYMBOLS_JSON:\n"
            '{"path": "scripts/two_site_compact_review_context.py", "symbols": ["fn1", "fn2"]}'
        )
        parsed = parse_one_file_compact_review_spec(goal_standalone)
        self.assertEqual(
            parsed,
            ("scripts/two_site_compact_review_context.py", ["fn1", "fn2"]),
        )

        canonical_pkg = '[{"path": "other.py", "symbols": ["other_fn"]}]'
        goal_with_canonical = (
            "Refactor feature\n\n"
            "COMPACT_REVIEW_SYMBOLS_JSON:\n"
            '{"path": "scripts/two_site_compact_review_context.py", "symbols": ["fn1"]}\n\n'
            f"CANONICAL_SOURCE_EXCERPTS_JSON:\n{canonical_pkg}"
        )
        parsed_with_can = parse_one_file_compact_review_spec(goal_with_canonical)
        self.assertEqual(
            parsed_with_can,
            ("scripts/two_site_compact_review_context.py", ["fn1"]),
        )
        can_idx = goal_with_canonical.find("\nCANONICAL_SOURCE_EXCERPTS_JSON:\n")
        self.assertNotEqual(can_idx, -1)
        pkg_text = goal_with_canonical[can_idx + len("\nCANONICAL_SOURCE_EXCERPTS_JSON:\n"):]
        self.assertEqual(json.loads(pkg_text), json.loads(canonical_pkg))

    def test_duplicate_and_oversized_markers_fail_closed(self) -> None:
        dup_goal = (
            "Task\n\n"
            "COMPACT_REVIEW_SYMBOLS_JSON:\n"
            '{"path": "a.py", "symbols": ["s"]}\n\n'
            "COMPACT_REVIEW_SYMBOLS_JSON:\n"
            '{"path": "b.py", "symbols": ["s"]}'
        )
        with self.assertRaises(CompactReviewError):
            parse_one_file_compact_review_spec(dup_goal)

        huge_symbols = ["s" + "x" * 4096]
        huge_goal = (
            "Task\n\n"
            "COMPACT_REVIEW_SYMBOLS_JSON:\n"
            + json.dumps({"path": "a.py", "symbols": huge_symbols})
        )
        with self.assertRaises(CompactReviewError):
            parse_one_file_compact_review_spec(huge_goal)

    def test_malformed_and_trailing_json_fail_closed(self) -> None:
        for bad_payload in [
            "{not valid json",
            '{"path": "a.py", "symbols": ["s"]} trailing junk',
            '{"path": "a.py", "symbols": ["s"]} {"second": "object"}',
            '{"path": "a.py", "path": "dup.py", "symbols": ["s"]}',
            "",
            "   ",
        ]:
            goal = f"Task\n\nCOMPACT_REVIEW_SYMBOLS_JSON:\n{bad_payload}"
            with self.assertRaises(CompactReviewError):
                parse_one_file_compact_review_spec(goal)

    def test_wrong_keys_and_types_fail_closed(self) -> None:
        for bad_payload in [
            '["not a dict"]',
            "12345",
            '"just a string"',
            '{"path": "a.py"}',
            '{"symbols": ["s"]}',
            '{"path": "a.py", "symbols": ["s"], "extra": 1}',
            '{"path": 123, "symbols": ["s"]}',
            '{"path": "a.py", "symbols": "not a list"}',
            '{"path": "a.py", "symbols": [123]}',
        ]:
            goal = f"Task\n\nCOMPACT_REVIEW_SYMBOLS_JSON:\n{bad_payload}"
            with self.assertRaises(CompactReviewError):
                parse_one_file_compact_review_spec(goal)

    def test_invalid_paths_fail_closed(self) -> None:
        for bad_path in [
            "/abs/path.py",
            "C:/outside.py",
            "scripts\\win.py",
            "null\x00byte.py",
            "scripts//empty.py",
            "./dot.py",
            "scripts/./dot.py",
            "../dotdot.py",
            "scripts/../dotdot.py",
            "not_python.txt",
            "not_python.pyc",
            "",
        ]:
            goal = (
                "Task\n\n"
                "COMPACT_REVIEW_SYMBOLS_JSON:\n"
                + json.dumps({"path": bad_path, "symbols": ["sym"]})
            )
            with self.assertRaises(CompactReviewError):
                parse_one_file_compact_review_spec(goal)

        goal_drive = (
            "Task\n\n"
            "COMPACT_REVIEW_SYMBOLS_JSON:\n"
            '{"path": "C:/outside.py", "symbols": ["sym"]}'
        )
        with self.assertRaises(CompactReviewError):
            parse_one_file_compact_review_spec(goal_drive)

    def test_invalid_symbols_fail_closed(self) -> None:
        for bad_symbols in [
            [],
            ["dup", "dup"],
            ["123bad_identifier"],
            ["bad-hyphen"],
            ["bad space"],
            [""],
            ["valid", "dup", "valid"],
        ]:
            goal = (
                "Task\n\n"
                "COMPACT_REVIEW_SYMBOLS_JSON:\n"
                + json.dumps({"path": "valid.py", "symbols": bad_symbols})
            )
            with self.assertRaises(CompactReviewError):
                parse_one_file_compact_review_spec(goal)


if __name__ == "__main__":
    unittest.main()
