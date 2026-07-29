from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import production_shadow_convergence_blob_roundtrip as BLOB
from scripts import production_shadow_convergence_blob_roundtrip_assembler as MODULE
from scripts import production_shadow_convergence_blob_roundtrip_collector as COLLECTOR
from scripts import production_shadow_convergence_blob_roundtrip_reference as REFERENCE


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


def _payload(document: dict[str, object]) -> bytes:
    return COLLECTOR._canonical_json(document) + b"\n"


def _collector_input(
    *,
    source_site: str,
    target_site: str,
    role: str,
    object_marker: str,
    version_marker: str,
    payload_marker: str,
    keyring_marker: str,
) -> bytes:
    document: dict[str, object] = {
        "schema": COLLECTOR.COLLECTOR_INPUT_SCHEMA,
        "status": COLLECTOR.COLLECTOR_STATUS,
        **IDENTITY,
        "collector_release_sha": IDENTITY["release_sha"],
        "collector_release_tree_sha": IDENTITY["release_tree_sha"],
        "role": role,
        "scope": BLOB.SCOPE,
        "source_site": source_site,
        "target_site": target_site,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "transport": COLLECTOR.TRANSPORT,
        "object_storage_private": True,
        "object_storage_versioned": True,
        "keyring_sha256": keyring_marker * 64,
        "entries": [
            {
                "object_commitment_sha256": object_marker * 64,
                "source_version_id_sha256": version_marker * 64,
                "target_head_version_id_sha256": version_marker * 64,
                "target_get_version_id_sha256": version_marker * 64,
                "source_payload_sha256": payload_marker * 64,
                "target_payload_sha256": payload_marker * 64,
            }
        ],
        "collector_input_sha256": BLOB.ZERO_SHA256,
    }
    document["collector_input_sha256"] = COLLECTOR._input_digest(document)
    return _payload(document)


def collector_payloads() -> list[bytes]:
    payloads: list[bytes] = []
    markers = (("1", "2", "3", "4"), ("5", "6", "7", "8"))
    for (source_site, target_site), pair_markers in zip(BLOB.PAIRS, markers, strict=True):
        for role in (source_site, target_site):
            payloads.append(
                _collector_input(
                    source_site=source_site,
                    target_site=target_site,
                    role=role,
                    object_marker=pair_markers[0],
                    version_marker=pair_markers[1],
                    payload_marker=pair_markers[2],
                    keyring_marker=pair_markers[3],
                )
            )
    return payloads


class BlobRoundtripAssemblerTests(unittest.TestCase):
    def test_assembles_exactly_four_canonical_redacted_inputs(self) -> None:
        observation = MODULE.assemble_observation(
            collector_input_payloads=collector_payloads(),
            identity=IDENTITY,
            now=NOW,
        )
        self.assertEqual(BLOB.validate_observation(observation, identity=IDENTITY, now=NOW), observation)
        self.assertEqual(observation["schema"], BLOB.OBSERVATION_SCHEMA)
        self.assertEqual(len(observation["scopes"]), len(BLOB.PAIRS))
        serialized = json.dumps(observation, sort_keys=True)
        self.assertNotIn("collector_input_sha256", serialized)
        self.assertNotIn("source_version_id_sha256", serialized)
        self.assertNotIn("entries", serialized)

    def test_install_delegates_only_to_existing_reference_contract(self) -> None:
        reference = REFERENCE.BlobRoundtripObservationReference(
            path=Path("/evidence/blob.json"), sha256="f" * 64
        )
        with mock.patch.object(
            MODULE.REFERENCE, "install_observation", return_value=(reference, "created")
        ) as install:
            self.assertEqual(
                MODULE.assemble_and_install(
                    collector_input_payloads=collector_payloads(),
                    evidence_root=Path("/evidence"),
                    identity=IDENTITY,
                    now=NOW,
                ),
                (reference, "created"),
            )
        install.assert_called_once()
        observation = install.call_args.args[0]
        self.assertEqual(BLOB.validate_observation(observation, identity=IDENTITY, now=NOW), observation)
        self.assertEqual(install.call_args.kwargs["evidence_root"], Path("/evidence"))
        self.assertEqual(install.call_args.kwargs["identity"], IDENTITY)
        self.assertEqual(install.call_args.kwargs["now"], NOW)

    def test_install_uses_the_reference_create_only_layout_without_collector_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence_root = Path(temporary) / "evidence"
            evidence_root.mkdir(mode=0o700)
            os.chmod(evidence_root, 0o700)
            reference, outcome = MODULE.assemble_and_install(
                collector_input_payloads=collector_payloads(),
                evidence_root=evidence_root,
                identity=IDENTITY,
                now=NOW,
            )
            self.assertEqual(outcome, "created")
            installed = REFERENCE.validate_reference(
                REFERENCE.reference_document(reference),
                evidence_root=evidence_root,
                identity=IDENTITY,
                now=NOW,
            )
            self.assertEqual(
                installed,
                MODULE.assemble_observation(
                    collector_input_payloads=collector_payloads(), identity=IDENTITY, now=NOW
                ),
            )
            payload = reference.path.read_text(encoding="ascii")
            self.assertNotIn("collector_input_sha256", payload)
            self.assertNotIn("source_version_id_sha256", payload)

    def test_rejects_noncanonical_or_incomplete_inputs_before_installation(self) -> None:
        cases = {
            "noncanonical": lambda payloads: payloads.__setitem__(0, payloads[0] + b" "),
            "duplicate": lambda payloads: payloads.__setitem__(-1, payloads[0]),
            "incomplete": lambda payloads: payloads.pop(),
        }
        for label, mutate in cases.items():
            with self.subTest(label=label), mock.patch.object(MODULE.REFERENCE, "install_observation") as install:
                payloads = collector_payloads()
                mutate(payloads)
                with self.assertRaises(MODULE.BlobRoundtripAssemblerError):
                    MODULE.assemble_and_install(
                        collector_input_payloads=payloads,
                        evidence_root=Path("/evidence"),
                        identity=IDENTITY,
                        now=NOW,
                    )
                install.assert_not_called()

    def test_rejects_collector_release_drift_before_installation(self) -> None:
        payloads = collector_payloads()
        drifted = COLLECTOR.parse_collector_input_payload(payloads[0])
        drifted = copy.deepcopy(drifted)
        drifted["collector_release_sha"] = "f" * 40
        drifted["collector_input_sha256"] = COLLECTOR._input_digest(drifted)
        payloads[0] = _payload(drifted)
        with mock.patch.object(MODULE.REFERENCE, "install_observation") as install:
            with self.assertRaisesRegex(MODULE.BlobRoundtripAssemblerError, "collector input is invalid"):
                MODULE.assemble_and_install(
                    collector_input_payloads=payloads,
                    evidence_root=Path("/evidence"),
                    identity=IDENTITY,
                    now=NOW,
                )
            install.assert_not_called()


if __name__ == "__main__":
    unittest.main()
