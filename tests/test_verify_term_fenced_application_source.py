"""Focused tests for the Git-blob startup capability inspection."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from unittest import TestCase

from core import term_fenced_application_capability as capability
from scripts import verify_term_fenced_application_source as subject


REPO_ROOT = Path(__file__).resolve().parents[1]


def _head_blobs() -> dict[str, bytes]:
    return {
        name: subprocess.check_output(["git", "show", f"HEAD:{name}"], cwd=REPO_ROOT)
        for name in capability.TERM_FENCED_APPLICATION_CAPABILITY_FILES
    }


class VerifyTermFencedApplicationSourceTests(TestCase):
    def setUp(self) -> None:
        self.blobs = _head_blobs()
        self.tree = subject.SourceTree(
            root=Path("/"),
            release_sha="a" * 40,
            release_tree_sha="b" * 40,
            blobs=self.blobs,
        )

    def test_current_release_source_passes_and_build_is_deterministic(self) -> None:
        subject.validate_source_capabilities(self.blobs)
        first = subject.build_evidence(self.tree)
        second = subject.build_evidence(self.tree)
        self.assertEqual(first, second)
        evidence = subject.verify_evidence_for_source(self.tree, first)
        self.assertEqual("a" * 40, evidence.release_sha)
        self.assertEqual(hashlib.sha256(first).hexdigest(), evidence.evidence_sha256)
        self.assertEqual(
            {
                "org.opencontainers.image.revision": "a" * 40,
                capability.TERM_FENCED_IMAGE_LABEL_SOURCE_TREE: "b" * 40,
                capability.TERM_FENCED_IMAGE_LABEL_EVIDENCE_SHA256: evidence.evidence_sha256,
            },
            capability.expected_term_fenced_image_labels(evidence),
        )

    def test_missing_api_gate_and_bad_config_are_refused(self) -> None:
        no_api_gate = dict(self.blobs)
        no_api_gate["main.py"] = no_api_gate["main.py"].replace(
            b"    _validate_writer_term_api_startup()\n",
            b"    # gate intentionally removed by adversarial fixture\n",
            1,
        )
        with self.assertRaisesRegex(
            subject.TermFencedApplicationSourceError,
            "API_GATE_INVALID",
        ):
            subject.validate_source_capabilities(no_api_gate)

        bad_config = dict(self.blobs)
        bad_config["core/config.py"] = bad_config["core/config.py"].replace(
            b"single_writer_runtime_enabled: bool = False",
            b"single_writer_runtime_enabled: bool = True",
            1,
        )
        with self.assertRaisesRegex(
            subject.TermFencedApplicationSourceError,
            "CONFIG_INVALID",
        ):
            subject.validate_source_capabilities(bad_config)

    def test_missing_bot_middleware_or_source_evidence_mismatch_is_refused(self) -> None:
        no_middleware = dict(self.blobs)
        no_middleware["run_bot.py"] = no_middleware["run_bot.py"].replace(
            b"WriterTermMiddleware()",
            b"RemovedWriterTermMiddleware()",
            1,
        )
        with self.assertRaisesRegex(
            subject.TermFencedApplicationSourceError,
            "BOT_GATE_INVALID",
        ):
            subject.validate_source_capabilities(no_middleware)

        document = subject.build_evidence(self.tree)
        changed = dict(self.blobs)
        changed["core/db.py"] += b"\n# separate immutable blob mismatch\n"
        changed_tree = subject.SourceTree(
            root=Path("/"),
            release_sha="a" * 40,
            release_tree_sha="b" * 40,
            blobs=changed,
        )
        with self.assertRaisesRegex(
            subject.TermFencedApplicationSourceError,
            "EVIDENCE_MISMATCH",
        ):
            subject.verify_evidence_for_source(changed_tree, document)

        tree_changed_only = subject.SourceTree(
            root=Path("/"),
            release_sha="a" * 40,
            release_tree_sha="c" * 40,
            blobs=self.blobs,
        )
        with self.assertRaisesRegex(
            subject.TermFencedApplicationSourceError,
            "EVIDENCE_MISMATCH",
        ):
            subject.verify_evidence_for_source(tree_changed_only, document)


if __name__ == "__main__":
    import unittest

    unittest.main()
