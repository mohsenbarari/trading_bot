from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts import attest_writer_witness_client as client
from scripts import manage_writer_witness_attestation_transport as transport


NOW = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
NL = bytes((10,))


def _write(path: Path, raw: bytes, *, mode: int = 0o600) -> None:
    path.write_bytes(raw)
    path.chmod(mode)


def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("ascii")).hexdigest()


def _receipt(site: str) -> dict:
    return {
        "schema": client.CLIENT_ATTESTATION_SCHEMA,
        "status": "attested",
        "site": site,
        "mode": "fenced_fi_writer" if site == "webapp_fi" else "writer",
        "observed_at": NOW.isoformat(),
        "request_id": f"{site}-nonce",
        "tls_verified": True,
        "witness_endpoint_sha256": _hash("endpoint"),
        "ca_bundle_sha256": _hash("ca"),
        "pinned_witness_public_key": "A" * 44,
        "runtime_profile_sha256": _hash("profile"),
        "release_manifest_sha256": _hash("manifest"),
        "profile": {
            "lease_duration_seconds": 60,
            "renew_interval_seconds": 10,
            "safety_margin_seconds": 15,
        },
        "witness_attestation": {
            "caller_site": site,
            "caller_key_id_sha256": _hash(site),
        },
    }


class WriterWitnessAttestationTransportTests(unittest.TestCase):
    def _directories(self, root: Path) -> tuple[Path, Path, Path]:
        source = root / "source"
        publish = root / "publish"
        controller = root / "controller"
        for directory in (source, publish, controller):
            directory.mkdir(mode=0o700)
        return source, publish, controller

    def test_seal_bind_and_receive_create_only_version_bound_nonsecret_receipt(self):
        with tempfile.TemporaryDirectory(prefix="witness-transport-") as raw:
            root = Path(raw)
            source, publish, controller = self._directories(root)
            source_receipt = root / "fi.json"
            receipt_raw = client._canonical_json_bytes(_receipt("webapp_fi")) + NL
            _write(source_receipt, receipt_raw)
            sealed = transport.seal_receipt(
                attestation_path=source_receipt,
                destination_directory=source,
            )
            envelope = next(source.iterdir())
            self.assertEqual(envelope.stat().st_mode & 0o777, 0o400)
            bound = transport.bind_published_version(
                envelope_path=envelope,
                object_version_id="version-fi-001",
                destination_directory=publish,
            )
            publish_receipt = next(publish.iterdir())
            imported = controller / "webapp-fi.json"
            received = transport.receive_sealed_receipt(
                envelope_path=envelope,
                publish_receipt_path=publish_receipt,
                expected_site="webapp_fi",
                destination=imported,
            )
            self.assertEqual(imported.read_bytes(), receipt_raw)
            self.assertEqual(imported.stat().st_mode & 0o777, 0o400)
            self.assertEqual(bound["object_version_id"], "version-fi-001")
            self.assertEqual(received["object_version_id"], "version-fi-001")
            encoded = json.dumps(
                {"sealed": sealed, "bound": bound, "received": received},
                sort_keys=True,
            )
            self.assertNotIn("https://", encoded)
            self.assertNotIn("secret", encoded.lower())
            self.assertIn("object_key", encoded)
            with self.assertRaisesRegex(
                transport.WriterWitnessAttestationTransportError,
                "cannot create",
            ):
                transport.seal_receipt(
                    attestation_path=source_receipt,
                    destination_directory=source,
                )

    def test_replay_wrong_site_tampered_payload_and_invalid_version_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="witness-transport-negative-") as raw:
            root = Path(raw)
            source, publish, controller = self._directories(root)
            source_receipt = root / "fi.json"
            _write(source_receipt, client._canonical_json_bytes(_receipt("webapp_fi")) + NL)
            transport.seal_receipt(
                attestation_path=source_receipt,
                destination_directory=source,
            )
            envelope = next(source.iterdir())
            with self.assertRaisesRegex(
                transport.WriterWitnessAttestationTransportError,
                "VersionId",
            ):
                transport.bind_published_version(
                    envelope_path=envelope,
                    object_version_id="bad" + chr(10) + "version",
                    destination_directory=publish,
                )
            with self.assertRaisesRegex(
                transport.WriterWitnessAttestationTransportError,
                "VersionId",
            ):
                transport.bind_published_version(
                    envelope_path=envelope,
                    object_version_id="https://storage.example.invalid/version-fi-002",
                    destination_directory=publish,
                )
            transport.bind_published_version(
                envelope_path=envelope,
                object_version_id="version-fi-002",
                destination_directory=publish,
            )
            publish_receipt = next(publish.iterdir())
            with self.assertRaisesRegex(
                transport.WriterWitnessAttestationTransportError,
                "transport receipt binding",
            ):
                transport.receive_sealed_receipt(
                    envelope_path=envelope,
                    publish_receipt_path=publish_receipt,
                    expected_site="webapp_ir",
                    destination=controller / "wrong-site.json",
                )
            original = envelope.read_bytes()
            _write(envelope, b"{}" + NL)
            envelope.chmod(0o400)
            with self.assertRaisesRegex(
                transport.WriterWitnessAttestationTransportError,
                "sealed attestation envelope",
            ):
                transport.receive_sealed_receipt(
                    envelope_path=envelope,
                    publish_receipt_path=publish_receipt,
                    expected_site="webapp_fi",
                    destination=controller / "tampered.json",
                )
            _write(envelope, original)
            envelope.chmod(0o400)
            publish_value = json.loads(publish_receipt.read_text(encoding="utf-8"))
            publish_value["envelope_sha256"] = "0" * 64
            _write(publish_receipt, transport._canonical(publish_value) + NL)
            publish_receipt.chmod(0o400)
            with self.assertRaisesRegex(
                transport.WriterWitnessAttestationTransportError,
                "transport receipt binding",
            ):
                transport.receive_sealed_receipt(
                    envelope_path=envelope,
                    publish_receipt_path=publish_receipt,
                    expected_site="webapp_fi",
                    destination=controller / "tampered-publish.json",
                )


if __name__ == "__main__":
    unittest.main()
