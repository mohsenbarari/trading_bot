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


if __name__ == "__main__":
    unittest.main()
