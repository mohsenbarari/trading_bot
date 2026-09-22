"""Tests for two-site readiness contract exports and golden digest equivalence."""
from __future__ import annotations

import hashlib
import json
import unittest

try:
    from scripts import two_site_proposal_runner as runner
    from scripts import two_site_readiness_contract as contract
except ImportError:
    import two_site_proposal_runner as runner
    import two_site_readiness_contract as contract

EXPECTED_GOLDEN_SHA256 = (
    "1d6f290bcabfd2ff1a457bb34fb3daf7580b9589f74262db7d2393472c9c868d"
)


def _compute_digest(codes, descriptions, details, model_codes) -> str:
    values = {
        "_TASK_BOUNDARY_CODES": sorted(list(codes)),
        "_TASK_BOUNDARY_DESCRIPTIONS": descriptions,
        "_READINESS_ISSUE_DETAILS": details,
        "_MODEL_CONTEXT_CODES": sorted(list(model_codes)),
    }
    raw = json.dumps(
        values, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class TestTwoSiteReadinessContractExports(unittest.TestCase):
    def test_golden_sha256_prior_value_equivalence(self) -> None:
        contract_digest = _compute_digest(
            contract._TASK_BOUNDARY_CODES,
            contract._TASK_BOUNDARY_DESCRIPTIONS,
            contract._READINESS_ISSUE_DETAILS,
            contract._MODEL_CONTEXT_CODES,
        )
        runner_digest = _compute_digest(
            runner._TASK_BOUNDARY_CODES,
            runner._TASK_BOUNDARY_DESCRIPTIONS,
            runner._READINESS_ISSUE_DETAILS,
            runner._MODEL_CONTEXT_CODES,
        )
        self.assertEqual(contract_digest, EXPECTED_GOLDEN_SHA256)
        self.assertEqual(runner_digest, EXPECTED_GOLDEN_SHA256)

    def test_alias_identities_and_class_codes(self) -> None:
        self.assertIs(runner._TASK_BOUNDARY_CODES, contract._TASK_BOUNDARY_CODES)
        self.assertIs(
            runner._TASK_BOUNDARY_DESCRIPTIONS, contract._TASK_BOUNDARY_DESCRIPTIONS
        )
        self.assertIs(
            runner._READINESS_ISSUE_DETAILS, contract._READINESS_ISSUE_DETAILS
        )
        self.assertIs(runner._MODEL_CONTEXT_CODES, contract._MODEL_CONTEXT_CODES)
        self.assertIs(runner._TaskBoundaryError.CODES, contract._TASK_BOUNDARY_CODES)
        self.assertIs(runner._TaskBoundaryError._CODES, contract._TASK_BOUNDARY_CODES)

    def test_literal_types_and_invariants(self) -> None:
        self.assertIsInstance(contract._TASK_BOUNDARY_CODES, frozenset)
        self.assertIsInstance(contract._TASK_BOUNDARY_DESCRIPTIONS, dict)
        self.assertIsInstance(contract._READINESS_ISSUE_DETAILS, dict)
        self.assertIsInstance(contract._MODEL_CONTEXT_CODES, frozenset)
        self.assertEqual(len(contract._TASK_BOUNDARY_CODES), 14)
        self.assertEqual(len(contract._TASK_BOUNDARY_DESCRIPTIONS), 8)
        self.assertEqual(len(contract._READINESS_ISSUE_DETAILS), 17)
        self.assertEqual(len(contract._MODEL_CONTEXT_CODES), 3)


if __name__ == "__main__":
    unittest.main()
