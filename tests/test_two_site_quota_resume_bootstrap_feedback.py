"""Hermetic unit tests for bootstrap correction feedback builder."""
from __future__ import annotations

import hashlib
import json
import unittest

try:
    from scripts.two_site_quota_resume_remainder_feedback import (
        ProposalError,
        build_bootstrap_correction_feedback,
    )
except ImportError:
    from two_site_quota_resume_remainder_feedback import (
        ProposalError,
        build_bootstrap_correction_feedback,
    )


class TestTwoSiteQuotaResumeBootstrapFeedback(unittest.TestCase):
    def setUp(self) -> None:
        self.worker_sha = "a" * 64
        self.findings = ["finding 1", "finding 2: \u200c test"]

    def test_exact_valid_payload_and_signature(self) -> None:
        seen_signatures: set[str] = set()
        canonical_bytes = json.dumps(
            self.findings,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        expected_sig = hashlib.sha256(canonical_bytes).hexdigest()

        payload, signature, new_seen = build_bootstrap_correction_feedback(
            findings=self.findings,
            worker_sha=self.worker_sha,
            correction_count=0,
            seen_bootstrap_signatures=seen_signatures,
        )

        self.assertEqual(signature, expected_sig)
        self.assertEqual(payload["reviewer"], "test-gate")
        self.assertEqual(payload["reviewed_proposal_sha256"], self.worker_sha)
        self.assertEqual(payload["findings"], self.findings)
        self.assertEqual(
            payload["test_evidence"],
            [
                "gate=P1-00-characterization-bootstrap",
                f"proposal_sha256={self.worker_sha}",
                "finding_count=2",
            ],
        )
        self.assertEqual(new_seen, {expected_sig})

    def test_repeated_signature_rejection(self) -> None:
        canonical_bytes = json.dumps(
            self.findings,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        sig = hashlib.sha256(canonical_bytes).hexdigest()
        seen = {sig}

        with self.assertRaises(ProposalError) as ctx:
            build_bootstrap_correction_feedback(
                findings=self.findings,
                worker_sha=self.worker_sha,
                correction_count=1,
                seen_bootstrap_signatures=seen,
            )
        self.assertIn("repeated bootstrap signature", str(ctx.exception))

    def test_correction_count_bounds_and_exhaustion(self) -> None:
        _, _, _ = build_bootstrap_correction_feedback(
            findings=self.findings,
            worker_sha=self.worker_sha,
            correction_count=1,
            seen_bootstrap_signatures=set(),
        )

        with self.assertRaises(ProposalError) as ctx_ex:
            build_bootstrap_correction_feedback(
                findings=self.findings,
                worker_sha=self.worker_sha,
                correction_count=2,
                seen_bootstrap_signatures=set(),
            )
        self.assertIn("correction exhaustion", str(ctx_ex.exception))

        for invalid_count in (-1, 3, True, 1.0, "0"):
            with self.assertRaises(ProposalError):
                build_bootstrap_correction_feedback(
                    findings=self.findings,
                    worker_sha=self.worker_sha,
                    correction_count=invalid_count,  # type: ignore[arg-type]
                    seen_bootstrap_signatures=set(),
                )

    def test_bad_types_and_bounds(self) -> None:
        bad_shas = ["not-a-sha", "A" * 64, "a" * 63, 123, ""]
        for bad_sha in bad_shas:
            with self.assertRaises(ProposalError):
                build_bootstrap_correction_feedback(
                    findings=self.findings,
                    worker_sha=bad_sha,  # type: ignore[arg-type]
                    correction_count=0,
                    seen_bootstrap_signatures=set(),
                )

        bad_seen = [["a" * 64], None, {"not-a-sha"}, {"A" * 64}]
        for item in bad_seen:
            with self.assertRaises(ProposalError):
                build_bootstrap_correction_feedback(
                    findings=self.findings,
                    worker_sha=self.worker_sha,
                    correction_count=0,
                    seen_bootstrap_signatures=item,  # type: ignore[arg-type]
                )

        bad_findings_list = [
            "not-a-list",
            [],
            [f"finding_{i}" for i in range(33)],
            [""],
            ["   "],
            [123],
        ]
        for bad_findings in bad_findings_list:
            with self.assertRaises(ProposalError):
                build_bootstrap_correction_feedback(
                    findings=bad_findings,  # type: ignore[arg-type]
                    worker_sha=self.worker_sha,
                    correction_count=0,
                    seen_bootstrap_signatures=set(),
                )

    def test_immutability_and_detachment(self) -> None:
        findings_input = ["bootstrap finding"]
        prior_sig = "b" * 64
        seen_input = {prior_sig}

        payload, sig, new_seen = build_bootstrap_correction_feedback(
            findings=findings_input,
            worker_sha=self.worker_sha,
            correction_count=0,
            seen_bootstrap_signatures=seen_input,
        )

        self.assertIsNot(payload["findings"], findings_input)
        self.assertIsNot(new_seen, seen_input)
        self.assertEqual(seen_input, {prior_sig})
        payload["findings"].append("extra")
        self.assertEqual(findings_input, ["bootstrap finding"])
        self.assertEqual(new_seen, {prior_sig, sig})


if __name__ == "__main__":
    unittest.main()
