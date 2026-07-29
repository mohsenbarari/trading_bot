from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import unittest

from scripts import production_shadow_convergence_blob_roundtrip as BLOB
from scripts import production_shadow_convergence_blob_roundtrip_collector as MODULE


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
IDENTITY = {
    "campaign_id": "22222222-2222-4222-8222-222222222222",
    "operation_id": "11111111-1111-4111-8111-111111111111",
    "release_sha": "a" * 40,
    "release_tree_sha": "b" * 40,
    "manifest_sha256": "c" * 64,
    "plan_sha256": "d" * 64,
    "approval_sha256": "e" * 64,
}


def entry(commitment: str = "1" * 64) -> dict[str, str]:
    return {
        "object_commitment_sha256": commitment,
        "source_version_id_sha256": "2" * 64,
        "target_head_version_id_sha256": "2" * 64,
        "target_get_version_id_sha256": "2" * 64,
        "source_payload_sha256": "3" * 64,
        "target_payload_sha256": "3" * 64,
    }


def collector_input(
    *, source: str = "webapp_fi", target: str = "webapp_ir", role: str = "webapp_fi"
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema": MODULE.COLLECTOR_INPUT_SCHEMA,
        "status": MODULE.COLLECTOR_STATUS,
        **IDENTITY,
        "collector_release_sha": IDENTITY["release_sha"],
        "collector_release_tree_sha": IDENTITY["release_tree_sha"],
        "role": role,
        "scope": BLOB.SCOPE,
        "source_site": source,
        "target_site": target,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "transport": MODULE.TRANSPORT,
        "object_storage_private": True,
        "object_storage_versioned": True,
        "keyring_sha256": "4" * 64,
        "entries": [entry()],
        "collector_input_sha256": BLOB.ZERO_SHA256,
    }
    document["collector_input_sha256"] = MODULE._input_digest(document)
    return document


class BlobRoundtripCollectorContractTests(unittest.TestCase):
    def test_reduces_full_exact_version_readback_to_a_gate_role_proof(self) -> None:
        document = collector_input()
        proof = MODULE.build_role_proof(
            document,
            identity=IDENTITY,
            source_site="webapp_fi",
            target_site="webapp_ir",
            role="webapp_fi",
            now=NOW,
        )
        self.assertEqual(proof["readback_sample_count"], proof["local_object_count"])
        self.assertEqual(
            BLOB.validate_role_proof(
                proof,
                identity=IDENTITY,
                source_site="webapp_fi",
                target_site="webapp_ir",
                role="webapp_fi",
                now=NOW,
            ),
            (proof, NOW),
        )
        serialized = json.dumps(document, sort_keys=True)
        self.assertNotIn("object_key", serialized)
        self.assertNotIn("version_id\"", serialized)
        self.assertNotIn("bucket", serialized)

    def test_requires_exact_release_private_versioned_and_full_readback(self) -> None:
        cases: list[tuple[str, dict[str, object]]] = []
        release = collector_input()
        release["collector_release_sha"] = "f" * 40
        cases.append(("release", release))
        private = collector_input()
        private["object_storage_private"] = False
        cases.append(("private", private))
        versioned = collector_input()
        versioned["object_storage_versioned"] = False
        cases.append(("versioned", versioned))
        version = collector_input()
        version["entries"] = [entry()]
        version["entries"][0]["target_get_version_id_sha256"] = "9" * 64
        cases.append(("VersionId", version))
        payload = collector_input()
        payload["entries"] = [entry()]
        payload["entries"][0]["target_payload_sha256"] = "9" * 64
        cases.append(("payload", payload))
        for label, document in cases:
            with self.subTest(label=label):
                document["collector_input_sha256"] = MODULE._input_digest(document)
                with self.assertRaises(MODULE.BlobRoundtripCollectorContractError):
                    MODULE.build_role_proof(
                        document,
                        identity=IDENTITY,
                        source_site="webapp_fi",
                        target_site="webapp_ir",
                        role="webapp_fi",
                        now=NOW,
                    )

    def test_requires_sorted_unique_redacted_commitments_and_canonical_payload(self) -> None:
        unordered = collector_input()
        unordered["entries"] = [entry("7" * 64), entry("6" * 64)]
        unordered["collector_input_sha256"] = MODULE._input_digest(unordered)
        with self.assertRaisesRegex(MODULE.BlobRoundtripCollectorContractError, "ordered"):
            MODULE.build_role_proof(
                unordered,
                identity=IDENTITY,
                source_site="webapp_fi",
                target_site="webapp_ir",
                role="webapp_fi",
                now=NOW,
            )
        document = collector_input()
        payload = MODULE._canonical_json(document) + b"\n"
        self.assertEqual(MODULE.parse_collector_input_payload(payload), document)
        with self.assertRaises(MODULE.BlobRoundtripCollectorContractError):
            MODULE.parse_collector_input_payload(payload + b" ")

    def test_rejects_post_digest_mutation_and_unbound_role(self) -> None:
        altered = copy.deepcopy(collector_input())
        altered["keyring_sha256"] = "9" * 64
        with self.assertRaisesRegex(MODULE.BlobRoundtripCollectorContractError, "digest"):
            MODULE.build_role_proof(
                altered,
                identity=IDENTITY,
                source_site="webapp_fi",
                target_site="webapp_ir",
                role="webapp_fi",
                now=NOW,
            )
        with self.assertRaisesRegex(MODULE.BlobRoundtripCollectorContractError, "role"):
            MODULE.build_role_proof(
                collector_input(),
                identity=IDENTITY,
                source_site="webapp_fi",
                target_site="webapp_ir",
                role="bot_fi",
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
