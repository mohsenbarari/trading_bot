from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from scripts import orchestrate_production_shadow_convergence_gate as BRIDGE
from scripts import production_shadow_destination_firewall_observation as MODULE


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
CAMPAIGN_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "11111111-1111-4111-8111-111111111111"
RELEASE_SHA = "a" * 40
TREE_SHA = "b" * 40
MANIFEST_SHA = "c" * 64
PLAN_SHA = "d" * 64
APPROVAL_SHA = "e" * 64


def identity() -> dict[str, str]:
    return {
        "campaign_id": CAMPAIGN_ID,
        "operation_id": OPERATION_ID,
        "release_sha": RELEASE_SHA,
        "release_tree_sha": TREE_SHA,
        "manifest_sha256": MANIFEST_SHA,
        "plan_sha256": PLAN_SHA,
        "approval_sha256": APPROVAL_SHA,
        "phase_started_at": (NOW - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }


def allowlists() -> dict[str, list[str]]:
    return {
        role: sorted([str(index + 1) * 64, str(index + 5) * 64])
        for index, role in enumerate(MODULE.ROLES)
    }


def proof(role: str, *, observed_at: datetime = NOW, observed: list[str] | None = None, violations: int = 0) -> dict[str, object]:
    expected = allowlists()[role]
    document: dict[str, object] = {
        "schema": MODULE.FIREWALL_PROOF_SCHEMA,
        "status": "observed-redacted",
        **{key: value for key, value in identity().items() if key != "phase_started_at"},
        "role": role,
        "captured_at": (observed_at - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "expected_allowlist": expected,
        "observed_allowlist": expected if observed is None else observed,
        "operation_rule_count": len(expected),
        "unexpected_destination_count": violations,
        "missing_destination_count": 0,
        "forbidden_egress_count": 0,
        "readback_sha256": "0" * 64,
    }
    document["readback_sha256"] = MODULE._proof_digest(document)
    return document


def proofs() -> dict[str, dict[str, object]]:
    return {role: proof(role) for role in MODULE.ROLES}


class DestinationFirewallObservationTests(unittest.TestCase):
    def test_reduces_exact_redacted_proofs_to_gate_compatible_observation(self) -> None:
        document = MODULE.build_destination_firewall_observation(
            identity=identity(),
            expected_allowlists=allowlists(),
            role_provider_proofs=proofs(),
            now=NOW,
        )
        self.assertEqual(document["schema"], BRIDGE.FIREWALL_OBSERVATION_SCHEMA)
        self.assertEqual(set(document["roles"]), set(MODULE.ROLES))
        self.assertNotIn("127.0.0.1", str(document))
        context = SimpleNamespace(
            manifest={
                "campaign_id": CAMPAIGN_ID,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "release_tree_sha": TREE_SHA,
                "artifacts": {"cutover_approval_sha256": APPROVAL_SHA},
            },
            manifest_sha256=MANIFEST_SHA,
            plan_sha256=PLAN_SHA,
        )
        self.assertEqual(BRIDGE._validate_firewall_observation(document, context=context), NOW)

    def test_rejects_allowlist_violation_identity_or_digest_drift(self) -> None:
        cases: list[tuple[str, dict[str, dict[str, object]]]] = []
        mismatch = proofs()
        mismatch["webapp_ir"] = proof("webapp_ir", observed=sorted(["f" * 64]))
        cases.append(("allowlist", mismatch))
        violation = proofs()
        violation["bot_fi"] = proof("bot_fi", violations=1)
        cases.append(("violation", violation))
        identity_drift = proofs()
        identity_drift["witness"] = copy.deepcopy(identity_drift["witness"])
        identity_drift["witness"]["release_sha"] = "f" * 40
        cases.append(("identity", identity_drift))
        digest = proofs()
        digest["webapp_fi"] = copy.deepcopy(digest["webapp_fi"])
        digest["webapp_fi"]["readback_sha256"] = "f" * 64
        cases.append(("digest", digest))
        for label, candidate in cases:
            with self.subTest(label=label):
                with self.assertRaises(MODULE.DestinationFirewallObservationError):
                    MODULE.build_destination_firewall_observation(
                        identity=identity(),
                        expected_allowlists=allowlists(),
                        role_provider_proofs=candidate,
                        now=NOW,
                    )

    def test_rejects_stale_or_skewed_proofs_and_observation_reduction_drift(self) -> None:
        stale = proofs()
        stale["bot_fi"] = proof("bot_fi", observed_at=NOW - timedelta(minutes=16))
        with self.assertRaisesRegex(MODULE.DestinationFirewallObservationError, "freshness"):
            MODULE.build_destination_firewall_observation(
                identity=identity(), expected_allowlists=allowlists(), role_provider_proofs=stale, now=NOW
            )
        skewed = proofs()
        skewed["witness"] = proof("witness", observed_at=NOW - timedelta(minutes=3))
        with self.assertRaisesRegex(MODULE.DestinationFirewallObservationError, "skew"):
            MODULE.build_destination_firewall_observation(
                identity=identity(), expected_allowlists=allowlists(), role_provider_proofs=skewed, now=NOW
            )
        source = proofs()
        document = MODULE.build_destination_firewall_observation(
            identity=identity(), expected_allowlists=allowlists(), role_provider_proofs=source, now=NOW
        )
        altered = copy.deepcopy(document)
        altered["roles"]["bot_fi"]["operation_rule_count"] = 99
        altered["allowlist_set_sha256"] = MODULE._sha256(altered["roles"])
        with self.assertRaisesRegex(MODULE.DestinationFirewallObservationError, "binding"):
            MODULE.validate_destination_firewall_observation(
                altered,
                identity=identity(),
                expected_allowlists=allowlists(),
                role_provider_proofs=source,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
