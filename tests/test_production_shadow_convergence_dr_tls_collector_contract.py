from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import unittest

from scripts import production_shadow_convergence_dr_tls as TLS
from scripts import production_shadow_convergence_dr_tls_collector_contract as MODULE


IDENTITY = {
    "campaign_id": "22222222-2222-4222-8222-222222222222",
    "operation_id": "11111111-1111-4111-8111-111111111111",
    "release_sha": "a" * 40,
    "release_tree_sha": "b" * 40,
    "manifest_sha256": "c" * 64,
    "plan_sha256": "d" * 64,
    "approval_sha256": "e" * 64,
}
NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def plan() -> dict[str, object]:
    return MODULE.build_plan(
        identity=IDENTITY,
        phase_started_at=NOW - timedelta(minutes=1),
        role="bot_fi",
        origin_role="bot_fi",
        destination_role="webapp_ir",
        runtime_target_binding_sha256="1" * 64,
        app_image_id="sha256:" + "2" * 64,
        collector_source_manifest_sha256="3" * 64,
        network_policy_sha256="4" * 64,
    )


def output(collector_plan: dict[str, object], *, captured_at: datetime = NOW) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": MODULE.OUTPUT_SCHEMA,
        "status": "observed-redacted",
        **IDENTITY,
        "role": collector_plan["role"],
        "origin_role": collector_plan["origin_role"],
        "destination_role": collector_plan["destination_role"],
        "collector_plan_sha256": collector_plan["collector_plan_sha256"],
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "observed_at": captured_at.isoformat().replace("+00:00", "Z"),
        "protocol": "TLSv1.3",
        "status_code": 200,
        "certificate_sha256": "5" * 64,
        "peer_handshake_sha256": "6" * 64,
        "ca_bundle_sha256": "7" * 64,
        "collector_output_sha256": TLS.ZERO_SHA256,
    }
    document["collector_output_sha256"] = MODULE._output_digest(document)
    return document


class DrTlsCollectorContractTests(unittest.TestCase):
    def test_exact_release_plan_and_redacted_output_reduce_to_existing_peer_proof(self) -> None:
        collector_plan = plan()
        self.assertEqual(MODULE.validate_plan(collector_plan, identity=IDENTITY), collector_plan)
        receipt = output(collector_plan)
        payload = MODULE._canonical_json(receipt) + b"\n"
        self.assertEqual(MODULE.parse_collector_output(payload), receipt)
        checked, proof = MODULE.validate_collector_output(receipt, plan=collector_plan, identity=IDENTITY, now=NOW)
        self.assertEqual(checked, receipt)
        self.assertEqual(MODULE.reduce_to_peer_proof(receipt, plan=collector_plan, identity=IDENTITY, now=NOW), proof)
        self.assertEqual(
            TLS.validate_proof(proof, identity=IDENTITY, origin_role="bot_fi", destination_role="webapp_ir", role="bot_fi", now=NOW)[0],
            proof,
        )

    def test_parser_and_output_reject_noncanonical_or_unredacted_data(self) -> None:
        collector_plan = plan()
        receipt = output(collector_plan)
        payload = MODULE._canonical_json(receipt) + b"\n"
        with self.assertRaises(MODULE.DrTlsCollectorContractError):
            MODULE.parse_collector_output(payload + b" ")
        unredacted = copy.deepcopy(receipt)
        unredacted["endpoint"] = "https://private.example.invalid/healthz"
        unredacted["collector_output_sha256"] = MODULE._output_digest(unredacted)
        with self.assertRaises(MODULE.DrTlsCollectorContractError):
            MODULE.validate_collector_output(unredacted, plan=collector_plan, identity=IDENTITY, now=NOW)

    def test_rejects_plan_release_source_policy_and_output_binding_drift(self) -> None:
        collector_plan = plan()
        release_drift = copy.deepcopy(collector_plan)
        release_drift["release_sha"] = "f" * 40
        release_drift["collector_plan_sha256"] = MODULE._plan_digest(release_drift)
        with self.assertRaises(MODULE.DrTlsCollectorContractError):
            MODULE.validate_plan(release_drift, identity=IDENTITY)
        source_drift = copy.deepcopy(collector_plan)
        source_drift["collector_source_manifest_sha256"] = "f" * 64
        source_drift["collector_plan_sha256"] = MODULE._plan_digest(source_drift)
        self.assertEqual(MODULE.validate_plan(source_drift, identity=IDENTITY), source_drift)
        with self.assertRaises(MODULE.DrTlsCollectorContractError):
            MODULE.validate_collector_output(output(collector_plan), plan=source_drift, identity=IDENTITY, now=NOW)
        receipt = output(collector_plan)
        receipt["role"] = "webapp_ir"
        receipt["collector_output_sha256"] = MODULE._output_digest(receipt)
        with self.assertRaises(MODULE.DrTlsCollectorContractError):
            MODULE.validate_collector_output(receipt, plan=collector_plan, identity=IDENTITY, now=NOW)

    def test_rejects_stale_or_unsuccessful_handshake(self) -> None:
        collector_plan = plan()
        stale = output(collector_plan, captured_at=NOW - MODULE.MAX_SOURCE_AGE - timedelta(seconds=1))
        with self.assertRaises(MODULE.DrTlsCollectorContractError):
            MODULE.validate_collector_output(stale, plan=collector_plan, identity=IDENTITY, now=NOW)
        failed = output(collector_plan)
        failed["status_code"] = 503
        failed["collector_output_sha256"] = MODULE._output_digest(failed)
        with self.assertRaises(MODULE.DrTlsCollectorContractError):
            MODULE.validate_collector_output(failed, plan=collector_plan, identity=IDENTITY, now=NOW)


if __name__ == "__main__":
    unittest.main()
