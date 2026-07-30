import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
        raise
    return module


evidence = _load_module(
    "build_webapp_fi_source_evidence_test",
    ROOT / "scripts" / "build_webapp_fi_source_evidence.py",
)
binding_module = _load_module(
    "webapp_fi_source_campaign_binding_for_source_evidence_test",
    ROOT / "scripts" / "webapp_fi_source_campaign_binding.py",
)
source_adoption_fixture_module = _load_module(
    "webapp_fi_source_adoption_fixture_for_source_evidence_test",
    ROOT / "tests" / "test_webapp_fi_source_adoption.py",
)


def _private_file(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    os.chmod(path, 0o600)
    return path


def _private_json(path, value):
    return _private_file(path, evidence.canonical_json_bytes(value) + b"\n")


class WebAppFiSourceEvidenceTests(unittest.TestCase):
    """Exercise a real verified candidate with all Docker calls fixture-mocked."""

    def setUp(self):
        self.fixture = source_adoption_fixture_module.WebAppFiSourceAdoptionTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.campaign = source_adoption_fixture_module.CAMPAIGN
        self.installed = self.fixture._install()
        runtime, role_path, ssh_public, static_path, certificate = self.fixture._runtime_and_config(self.installed)
        self.fixture._enroll(self.installed, role_path, ssh_public, certificate)
        self.attestation = self.fixture._attest(
            self.installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            attestation_id="evidence-attestation",
        )
        self.exported = self.fixture._export(
            self.installed,
            runtime,
            role_path,
            ssh_public,
            static_path,
            certificate,
            self.attestation,
            export_id="evidence-export",
        )
        self.campaign_root = self.fixture.root / "campaigns"
        self.campaign_root.mkdir(mode=0o700)
        self.campaign_directory = self.campaign_root / self.campaign
        self.campaign_directory.mkdir(mode=0o700)
        self.binding_path = self.campaign_directory / evidence.SOURCE_PHASE_DIRECTORY / evidence.CAMPAIGN_BINDING_FILENAME
        self.binding_path.parent.mkdir(mode=0o700)
        binding = binding_module.build_campaign_binding(
            campaign_id=self.campaign,
            application_release_sha=self.fixture.release,
            application_release_tree=self.attestation["descriptor_claim"]["application_release_tree"],
            expected_alembic_revision=source_adoption_fixture_module.REVISION,
            control_commit=self.fixture.control_commit,
            control_tree=self.fixture.control_tree,
        )
        _private_json(self.binding_path, binding)
        self.binding_payload = self.binding_path.read_bytes()
        self.signer_path = self.campaign_directory / evidence.FI_SOURCE_SIGNER_DIRECTORY / evidence.FI_SOURCE_SIGNER_KEY_NAME
        _private_file(self.signer_path, self.fixture.fi_private.read_bytes())
        self.export_root = self.fixture.root / "source-exports"
        self.export_root.mkdir(mode=0o700)
        os.chmod(self.export_root, 0o700)
        export_campaign = self.export_root / self.campaign
        export_campaign.mkdir(mode=0o700)
        os.chmod(export_campaign, 0o700)
        export_directory = export_campaign / "evidence-export"
        export_directory.mkdir(mode=0o700)
        os.chmod(export_directory, 0o700)
        self.export_receipt = (
            export_directory / evidence.IMAGE_EXPORT_RECEIPT_NAME
        )
        _private_file(self.export_receipt, Path(self.exported["receipt_path"]).read_bytes())
        self.evidence_root = self.fixture.root / "source-evidence"
        self.evidence_root.mkdir(mode=0o700)
        self.candidate_script = Path(self.installed["candidate"]) / evidence.THIS_SCRIPT_RELATIVE

    def _run(self, *, evidence_id, apply):
        with (
            patch.object(evidence, "CAMPAIGN_ROOT", self.campaign_root),
            patch.object(evidence, "FI_SOURCE_EXPORT_ROOT", self.export_root),
            patch.object(evidence, "FI_SOURCE_EVIDENCE_ROOT", self.evidence_root),
            patch.object(evidence, "__file__", str(self.candidate_script)),
        ):
            return evidence.build_source_evidence(
                install_receipt=Path(self.installed["receipt_path"]),
                attestation_id="evidence-attestation",
                export_id="evidence-export",
                evidence_id=evidence_id,
                apply=apply,
            )

    def _output(self, evidence_id):
        return self.evidence_root / self.campaign / evidence_id / evidence.SOURCE_EVIDENCE_FILENAME

    def test_plan_then_apply_seals_a_signed_binding_and_loads_candidate_closure(self):
        with patch.object(evidence, "__file__", str(self.candidate_script)):
            _, _, provenance, installed = evidence._load_verified_installed_adoption(
                Path(self.installed["receipt_path"])
            )
        candidate = Path(installed["candidate"])
        self.assertEqual(candidate / evidence.PROVENANCE_VERIFIER_SCRIPT_RELATIVE, Path(provenance.__file__))
        self.assertEqual(
            candidate / evidence.IMAGE_ARCHIVE_CONTRACT_SCRIPT_RELATIVE,
            Path(provenance.image_contract.__file__),
        )
        plan = self._run(evidence_id="evidence-one", apply=False)
        self.assertEqual("planned", plan["status"])
        self.assertFalse(self._output("evidence-one").exists())

        result = self._run(evidence_id="evidence-one", apply=True)
        output = self._output("evidence-one")
        self.assertEqual("sealed", result["status"])
        self.assertEqual(str(output), result["output_path"])
        self.assertTrue(output.is_file())
        self.assertEqual(0o600, stat.S_IMODE(output.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(output.parent.stat().st_mode))
        payload = output.read_bytes()
        verified = evidence.verify_source_evidence_envelope_payload(
            payload=payload,
            expected_campaign_binding_payload=self.binding_payload,
            pinned_source_signing_public_key_base64=result["source_signing_public_key_base64"],
            verification_time=json.loads(payload)["created_at"],
        )
        self.assertEqual(self.campaign, verified["campaign_id"])
        self.assertEqual(self.attestation["attestation_sha256"], verified["role_attestation_sha256"])
        self.assertEqual(evidence.sha256_bytes(self.export_receipt.read_bytes()), verified["image_export_receipt_sha256"])
        envelope = json.loads(payload)
        self.assertEqual(self.binding_payload, evidence.canonical_json_bytes(envelope["campaign_binding"]["payload"]) + b"\n")
        self.assertTrue((candidate / evidence.PROVENANCE_VERIFIER_SCRIPT_RELATIVE).is_file())
        self.assertTrue((candidate / evidence.IMAGE_ARCHIVE_CONTRACT_SCRIPT_RELATIVE).is_file())

    def test_outer_unknown_field_and_binding_raw_or_semantic_mismatch_are_rejected(self):
        result = self._run(evidence_id="evidence-unknown", apply=True)
        payload = self._output("evidence-unknown").read_bytes()
        mutated = json.loads(payload)
        mutated["unexpected"] = True
        with self.assertRaisesRegex(evidence.SourceEvidenceError, "schema"):
            evidence.verify_source_evidence_envelope_payload(
                payload=evidence.canonical_json_bytes(mutated) + b"\n",
                expected_campaign_binding_payload=self.binding_payload,
                pinned_source_signing_public_key_base64=result["source_signing_public_key_base64"],
            )

        bad_binding = binding_module.build_campaign_binding(
            campaign_id=self.campaign,
            application_release_sha=self.fixture.release,
            application_release_tree="a" * 40,
            expected_alembic_revision=source_adoption_fixture_module.REVISION,
            control_commit=self.fixture.control_commit,
            control_tree=self.fixture.control_tree,
        )
        _private_json(self.binding_path, bad_binding)
        with self.assertRaisesRegex(evidence.SourceEvidenceError, "does not match"):
            self._run(evidence_id="evidence-binding-mismatch", apply=True)
        self.assertFalse(self._output("evidence-binding-mismatch").exists())

    def test_url_bearing_or_signature_tampered_proof_is_rejected_before_output(self):
        image = json.loads(self.export_receipt.read_bytes())
        image["object_storage_export_required"]["control_url"] = "https://forbidden.example/one-shot"
        _private_json(self.export_receipt, image)
        with self.assertRaisesRegex(evidence.SourceEvidenceError, "forbidden URL"):
            self._run(evidence_id="evidence-url", apply=True)
        self.assertFalse(self._output("evidence-url").exists())

        _private_file(self.export_receipt, Path(self.exported["receipt_path"]).read_bytes())
        image = json.loads(self.export_receipt.read_bytes())
        image["source_signature"]["signature_base64"] = "A" * 88
        _private_json(self.export_receipt, image)
        with self.assertRaisesRegex(evidence.SourceEvidenceError, "cannot be verified"):
            self._run(evidence_id="evidence-tampered-proof", apply=True)
        self.assertFalse(self._output("evidence-tampered-proof").exists())

    def test_tampered_candidate_builder_and_existing_output_are_never_reused(self):
        self.candidate_script.write_bytes(self.candidate_script.read_bytes() + b"\n# tampered\n")
        os.chmod(self.candidate_script, 0o600)
        with self.assertRaisesRegex(evidence.SourceEvidenceError, "hash changed"):
            self._run(evidence_id="evidence-tampered-helper", apply=True)
        self.assertFalse(self._output("evidence-tampered-helper").exists())

        shutil.copy2(ROOT / "scripts" / evidence.THIS_SCRIPT_RELATIVE.split("/", 1)[1], self.candidate_script)
        os.chmod(self.candidate_script, 0o600)
        result = self._run(evidence_id="evidence-once", apply=True)
        original = self._output("evidence-once").read_bytes()
        with self.assertRaisesRegex(evidence.SourceEvidenceError, "reuse or overwrite"):
            self._run(evidence_id="evidence-once", apply=True)
        self.assertEqual(original, self._output("evidence-once").read_bytes())
        self.assertEqual("sealed", result["status"])

    def test_missing_co_shipped_image_contract_fails_closed_without_ambient_fallback(self):
        contract = Path(self.installed["candidate"]) / evidence.IMAGE_ARCHIVE_CONTRACT_SCRIPT_RELATIVE
        contract.unlink()
        with self.assertRaisesRegex(evidence.SourceEvidenceError, "image_archive_contract.py"):
            self._run(evidence_id="evidence-missing-closure", apply=True)
        self.assertFalse(self._output("evidence-missing-closure").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
