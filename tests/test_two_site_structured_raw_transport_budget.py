#!/usr/bin/env python3
"""Hermetic unit tests for worker test-only structured_output raw transport budget."""
from __future__ import annotations

import json
import unittest

try:
    from scripts.two_site_proposal_runner import (
        MAX_CONTEXT,
        MODEL,
        WORKER_MODEL,
        ProposalError,
        _parse_stream,
        sha,
    )
except ImportError:
    from two_site_proposal_runner import (
        MAX_CONTEXT,
        MODEL,
        WORKER_MODEL,
        ProposalError,
        _parse_stream,
        sha,
    )


def _make_stream(
    envelope_payload: dict,
    conversation_id: str = "conv-test-structured-01",
    model: str = WORKER_MODEL,
) -> str:
    init_event = {
        "event": "init",
        "conversation_id": conversation_id,
        "init": {"model": model},
    }
    result_event = {
        "event": "result",
        "conversation_id": conversation_id,
        "result": {
            "status": "SUCCESS",
            "conversation_id": conversation_id,
            **envelope_payload,
        },
    }
    return f"{json.dumps(init_event)}\n{json.dumps(result_event)}\n"


def _make_test_only_setup(
    file_path: str = "tests/test_target_characterization.py",
    content: str = "",
) -> tuple[dict, dict, str]:
    content_sha = sha(content.encode("utf-8"))
    task = {
        "task_id": "AE01-STRUCTURED-BUDGET-TEST",
        "goal": "Hermetic test goal for structured raw transport budget verification.",
        "context_files": [file_path],
        "allowed_files": [file_path],
        "requirements": ["Requirement 1"],
        "test_selectors": [file_path],
    }
    sources = {
        file_path: {
            "sha256": content_sha,
            "content": content,
        }
    }
    return task, sources, content_sha


class TestStructuredRawTransportBudget(unittest.TestCase):
    def test_compact_structured_replacement_accepted_and_materialized(self) -> None:
        prefix = "def test_alpha():\n    assert True\n\n# Synthetic padding lines\n"
        padding = ("# " + ("z" * 70) + "\n") * 950
        suffix = "def test_omega():\n    assert True\n"
        base_content = prefix + padding + suffix
        self.assertGreater(len(base_content.encode("utf-8")), 65536)

        target_path = "tests/test_target_characterization.py"
        task, sources, content_sha = _make_test_only_setup(target_path, base_content)

        proposal = {
            "status": "PROPOSAL_READY",
            "summary": "Update test_alpha assertion in existing test suite.",
            "covered_requirements": [1],
            "findings": [],
            "changes": [
                {
                    "path": target_path,
                    "original_sha256": content_sha,
                    "content": None,
                    "replacements": [
                        {
                            "old": "def test_alpha():\n    assert True\n",
                            "new": "def test_alpha():\n    assert 2 + 2 == 4\n",
                        }
                    ],
                }
            ],
        }
        raw_transport_bytes = len(json.dumps(proposal, ensure_ascii=False).encode("utf-8"))
        self.assertLess(raw_transport_bytes, 64000)

        raw_stream = _make_stream({"structured_output": proposal})
        result, envelope = _parse_stream(raw_stream, task, sources, "worker", expected_model=WORKER_MODEL)

        self.assertEqual(result["status"], "PROPOSAL_READY")
        self.assertEqual(len(result["changes"]), 1)
        materialized = result["changes"][0]["content"]
        self.assertIn("assert 2 + 2 == 4", materialized)
        self.assertNotIn("def test_alpha():\n    assert True", materialized)
        self.assertGreater(len(materialized.encode("utf-8")), 65536)
        self.assertLess(len(materialized.encode("utf-8")), MAX_CONTEXT)
        self.assertEqual(envelope["conversation_id"], "conv-test-structured-01")

    def test_oversized_structured_raw_rejected_before_normalization(self) -> None:
        base_content = "def test_sample():\n    assert True\n"
        target_path = "tests/test_target_characterization.py"
        task, sources, content_sha = _make_test_only_setup(target_path, base_content)

        oversized_summary = "A" * 65000
        proposal = {
            "status": "PROPOSAL_READY",
            "summary": oversized_summary,
            "covered_requirements": [1],
            "findings": [],
            "changes": [
                {
                    "path": target_path,
                    "original_sha256": content_sha,
                    "content": None,
                    "replacements": [
                        {
                            "old": "NONEXISTENT_TARGET_STRING_DO_NOT_NORMALIZE",
                            "new": "def test_sample():\n    assert False\n",
                        }
                    ],
                }
            ],
        }
        raw_transport_bytes = len(json.dumps(proposal, ensure_ascii=False).encode("utf-8"))
        self.assertGreater(raw_transport_bytes, 64000)

        raw_stream = _make_stream({"structured_output": proposal})
        with self.assertRaises(ProposalError) as ctx:
            _parse_stream(raw_stream, task, sources, "worker", expected_model=WORKER_MODEL)

        self.assertEqual(str(ctx.exception), "test-only raw transport exceeds budget")

    def test_canonical_max_context_cap_enforced_after_normalization(self) -> None:
        base_prefix = "def test_large():\n    pass\n# padding\n"
        base_padding = ("# " + ("w" * 70) + "\n") * 2150
        base_content = base_prefix + base_padding
        self.assertLess(len(base_content.encode("utf-8")), MAX_CONTEXT)

        target_path = "tests/test_target_characterization.py"
        task, sources, content_sha = _make_test_only_setup(target_path, base_content)

        expanded_text = "# " + ("y" * 10000) + "\n"
        proposal = {
            "status": "PROPOSAL_READY",
            "summary": "Expand test file past MAX_CONTEXT.",
            "covered_requirements": [1],
            "findings": [],
            "changes": [
                {
                    "path": target_path,
                    "original_sha256": content_sha,
                    "content": None,
                    "replacements": [
                        {
                            "old": "def test_large():\n    pass\n",
                            "new": f"def test_large():\n    pass\n{expanded_text}",
                        }
                    ],
                }
            ],
        }
        raw_transport_bytes = len(json.dumps(proposal, ensure_ascii=False).encode("utf-8"))
        self.assertLess(raw_transport_bytes, 64000)

        raw_stream = _make_stream({"structured_output": proposal})
        with self.assertRaises(ProposalError) as ctx:
            _parse_stream(raw_stream, task, sources, "worker", expected_model=WORKER_MODEL)

        self.assertEqual(str(ctx.exception), "canonical proposal exceeds the safe context budget")

    def test_verifier_structured_output_not_bounded_by_worker_transport(self) -> None:
        base_content = "def test_sample():\n    assert True\n"
        target_path = "tests/test_target_characterization.py"
        task, sources, _ = _make_test_only_setup(target_path, base_content)

        proposal = {
            "status": "VERIFIED",
            "summary": "Verified successfully: " + ("V" * 64500),
            "covered_requirements": [1],
            "findings": [],
            "changes": [],
        }
        raw_transport_bytes = len(json.dumps(proposal, ensure_ascii=False).encode("utf-8"))
        self.assertGreater(raw_transport_bytes, 64000)

        raw_stream = _make_stream({"structured_output": proposal}, model=MODEL)
        result, envelope = _parse_stream(raw_stream, task, sources, "verifier", expected_model=MODEL)

        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["changes"], [])
        self.assertEqual(envelope["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
