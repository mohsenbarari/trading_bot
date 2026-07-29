from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import unittest

from scripts import production_shadow_destination_firewall_collector_contract as MODULE


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
IDENTITY = {
    "campaign_id": "22222222-2222-4222-8222-222222222222",
    "operation_id": "11111111-1111-4111-8111-111111111111",
    "release_sha": "a" * 40,
    "release_tree_sha": "b" * 40,
    "manifest_sha256": "c" * 64,
    "plan_sha256": "d" * 64,
    "approval_sha256": "e" * 64,
    "phase_started_at": (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
}


def allowlist() -> list[str]:
    return ["1" * 64, "2" * 64]


def receipt(*, role: str = "webapp_ir") -> dict[str, object]:
    document: dict[str, object] = {
        "schema": MODULE.COLLECTOR_INPUT_SCHEMA,
        "status": MODULE.COLLECTOR_STATUS,
        **IDENTITY,
        "collector_release_sha": IDENTITY["release_sha"],
        "collector_release_tree_sha": IDENTITY["release_tree_sha"],
        "collector_source_manifest_sha256": "f" * 64,
        "collector_origin": MODULE.COLLECTOR_ORIGIN,
        "scope": MODULE.COLLECTOR_SCOPE,
        "role": role,
        "captured_at": (NOW - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "expected_allowlist": allowlist(),
        "observed_allowlist": allowlist(),
        "operation_rule_count": 2,
        "unexpected_destination_count": 0,
        "missing_destination_count": 0,
        "forbidden_egress_count": 0,
        "provider_readback_sha256": "9" * 64,
        "collector_input_sha256": "0" * 64,
    }
    document["collector_input_sha256"] = MODULE._input_digest(document)
    return document


class DestinationFirewallCollectorContractTests(unittest.TestCase):
    def test_exact_release_bound_receipt_reduces_to_existing_role_proof(self) -> None:
        raw, proof = MODULE.validate_collector_input(
            receipt(),
            identity=IDENTITY,
            expected_allowlist=allowlist(),
            role="webapp_ir",
            now=NOW,
        )
        self.assertEqual(raw["collector_release_sha"], IDENTITY["release_sha"])
        self.assertEqual(proof["role"], "webapp_ir")
        self.assertEqual(proof["expected_allowlist"], allowlist())
        self.assertNotIn("provider", json.dumps(proof, sort_keys=True))

    def test_parser_accepts_only_bounded_canonical_ascii_json(self) -> None:
        document = receipt()
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
        self.assertEqual(MODULE.parse_collector_input_payload(payload), document)
        with self.assertRaises(MODULE.DestinationFirewallCollectorContractError):
            MODULE.parse_collector_input_payload(payload.rstrip())
        with self.assertRaises(MODULE.DestinationFirewallCollectorContractError):
            MODULE.parse_collector_input_payload(b'{"schema":1,"schema":2}\n')

    def test_rejects_release_source_provider_and_identity_drift(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        for key, value in (
            ("collector_release_sha", "f" * 40),
            ("collector_source_manifest_sha256", "0" * 64),
            ("provider_readback_sha256", "0" * 64),
            ("release_tree_sha", "f" * 40),
            ("collector_origin", "role-local-untrusted-readback"),
        ):
            candidate = receipt()
            candidate[key] = value
            candidate["collector_input_sha256"] = MODULE._input_digest(candidate)
            cases.append((key, candidate))
        for label, candidate in cases:
            with self.subTest(label=label):
                with self.assertRaises(MODULE.DestinationFirewallCollectorContractError):
                    MODULE.validate_collector_input(
                        candidate,
                        identity=IDENTITY,
                        expected_allowlist=allowlist(),
                        role="webapp_ir",
                        now=NOW,
                    )

    def test_rejects_nonzero_violations_and_allowlist_or_digest_drift(self) -> None:
        for label, mutate in (
            ("violation", lambda value: value.__setitem__("forbidden_egress_count", 1)),
            ("allowlist", lambda value: value.__setitem__("observed_allowlist", ["3" * 64])),
            ("extra", lambda value: value.__setitem__("raw_provider_url", "https://provider.invalid")),
            ("digest", lambda value: value.__setitem__("collector_input_sha256", "f" * 64)),
        ):
            candidate = copy.deepcopy(receipt())
            mutate(candidate)
            if label != "digest":
                candidate["collector_input_sha256"] = MODULE._input_digest(candidate)
            with self.subTest(label=label):
                with self.assertRaises(MODULE.DestinationFirewallCollectorContractError):
                    MODULE.validate_collector_input(
                        candidate,
                        identity=IDENTITY,
                        expected_allowlist=allowlist(),
                        role="webapp_ir",
                        now=NOW,
                    )


if __name__ == "__main__":
    unittest.main()
