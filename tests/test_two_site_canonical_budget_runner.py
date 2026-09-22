"""Hermetic regression tests for canonical proposal budget vs raw transport."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        MODEL,
        ProposalError,
        _parse_stream,
        load_prior_worker,
        serialize_json,
        sha,
        validate_result,
    )
except ImportError:
    from two_site_proposal_runner import (
        MAX_CONTEXT,
        MODEL,
        ProposalError,
        _parse_stream,
        load_prior_worker,
        serialize_json,
        sha,
        validate_result,
    )


class TestCanonicalBudgetRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.task = {
            "task_id": "test-budget-task",
            "goal": "hermetic test-only task goal",
            "context_files": ["tests/test_sample.py"],
            "allowed_files": ["tests/test_sample.py"],
            "requirements": ["req1"],
            "test_selectors": ["tests/test_sample.py"],
        }
        self.orig_text = "val = 1\n" + ("# pad\n" * 12000)
        self.sources = {
            "tests/test_sample.py": {
                "sha256": sha(self.orig_text.encode("utf-8")),
                "content": self.orig_text,
            }
        }
        self.raw_result = {
            "status": "PROPOSAL_READY",
            "summary": "apply replacement",
            "covered_requirements": [1],
            "findings": [],
            "changes": [
                {
                    "path": "tests/test_sample.py",
                    "original_sha256": self.sources["tests/test_sample.py"]["sha256"],
                    "content": None,
                    "replacements": [{"old": "val = 1\n", "new": "val = 2\n"}],
                }
            ],
        }

    def test_compact_replacement_materializing_above_64000_passes_and_loads(self) -> None:
        raw_json = json.dumps(self.raw_result)
        self.assertLess(len(raw_json.encode("utf-8")), 64000)
        stream = "\n".join([
            json.dumps({"event": "init", "conversation_id": "c-1", "init": {"model": MODEL}}),
            json.dumps({"event": "result", "result": {"conversation_id": "c-1", "status": "SUCCESS", "response": raw_json}}),
        ])
        result, envelope = _parse_stream(stream, self.task, self.sources, "worker")
        canonical_bytes = serialize_json(result)
        self.assertGreater(len(canonical_bytes), 64000)
        self.assertLess(len(canonical_bytes), MAX_CONTEXT)
        self.assertEqual(envelope["conversation_id"], "c-1")

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "run1"
            run_dir.mkdir(parents=True)
            prop_text = canonical_bytes.decode("utf-8")
            prop_path = run_dir / "result.json"
            prop_sha = sha(canonical_bytes)
            (run_dir / "task.json").write_text(
                json.dumps({"task": self.task, "source_head": "h1", "sources": self.sources})
            )
            (run_dir / "status.json").write_text(
                json.dumps({
                    "role": "worker",
                    "state": "PROPOSAL_READY",
                    "result_sha256": prop_sha,
                    "run_id": "run-1",
                    "conversation_id": "c-1",
                })
            )
            candidate, worker_status, bound_sha = load_prior_worker(
                root, prop_path, prop_text, self.task, self.sources, "h1"
            )
            self.assertEqual(candidate["status"], "PROPOSAL_READY")
            self.assertEqual(bound_sha, prop_sha)
            self.assertEqual(worker_status["run_id"], "run-1")

    def test_padded_oversized_single_json_rejects_before_normalize(self) -> None:
        padded = dict(self.raw_result, summary="s" * 65000)
        padded_raw = json.dumps(padded)
        self.assertGreater(len(padded_raw.encode("utf-8")), 64000)
        stream = "\n".join([
            json.dumps({"event": "init", "conversation_id": "c-2", "init": {"model": MODEL}}),
            json.dumps({"event": "result", "result": {"conversation_id": "c-2", "status": "SUCCESS", "response": padded_raw}}),
        ])
        with patch("scripts.two_site_proposal_runner.normalize_result") as mock_norm:
            with self.assertRaises(ProposalError) as ctx:
                _parse_stream(stream, self.task, self.sources, "worker")
            self.assertIn("test-only raw transport exceeds budget", str(ctx.exception))
            mock_norm.assert_not_called()

    def test_forged_prior_binding_and_oversized_canonical_reject(self) -> None:
        canonical_bytes = serialize_json(self.raw_result)
        prop_text = canonical_bytes.decode("utf-8")
        prop_sha = sha(canonical_bytes)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            run_dir = root / "run_forged"
            run_dir.mkdir(parents=True)
            prop_path = run_dir / "result.json"
            (run_dir / "task.json").write_text(
                json.dumps({"task": self.task, "source_head": "h1", "sources": self.sources})
            )
            (run_dir / "status.json").write_text(
                json.dumps({
                    "role": "worker",
                    "state": "PROPOSAL_READY",
                    "result_sha256": "0" * 64,
                    "run_id": "run-f",
                    "conversation_id": "c-f",
                })
            )
            with self.assertRaises(ProposalError) as ctx:
                load_prior_worker(root, prop_path, prop_text, self.task, self.sources, "h1")
            self.assertIn("proposal is not bound to one completed worker run", str(ctx.exception))

        huge_orig = "val = 1\n" + ("# pad\n" * 28000)
        huge_sources = {
            "tests/test_sample.py": {
                "sha256": sha(huge_orig.encode("utf-8")),
                "content": huge_orig,
            }
        }
        huge_raw = dict(self.raw_result)
        huge_raw["changes"] = [{
            "path": "tests/test_sample.py",
            "original_sha256": huge_sources["tests/test_sample.py"]["sha256"],
            "content": None,
            "replacements": [{"old": "val = 1\n", "new": "val = 2\n"}],
        }]
        huge_stream = "\n".join([
            json.dumps({"event": "init", "conversation_id": "c-h", "init": {"model": MODEL}}),
            json.dumps({"event": "result", "result": {"conversation_id": "c-h", "status": "SUCCESS", "response": json.dumps(huge_raw)}}),
        ])
        with self.assertRaises(ProposalError) as ctx:
            _parse_stream(huge_stream, self.task, huge_sources, "worker")
        self.assertIn("canonical proposal exceeds the safe context budget", str(ctx.exception))

    def test_canonical_correction_validation_bypasses_raw_cap(self) -> None:
        rebased = {
            "status": "PROPOSAL_READY",
            "summary": "rebased canonical correction",
            "covered_requirements": [1],
            "findings": [],
            "changes": [
                {
                    "path": "tests/test_sample.py",
                    "original_sha256": self.sources["tests/test_sample.py"]["sha256"],
                    "content": "val = 9\n" + ("# pad\n" * 12000),
                }
            ],
        }
        valid = validate_result(
            rebased, self.task, self.sources, "worker", enforce_raw_transport=False
        )
        self.assertEqual(valid["status"], "PROPOSAL_READY")
        with self.assertRaises(ProposalError) as ctx:
            validate_result(
                rebased, self.task, self.sources, "worker", enforce_raw_transport=True
            )
        self.assertIn("test-only raw transport exceeds budget", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
