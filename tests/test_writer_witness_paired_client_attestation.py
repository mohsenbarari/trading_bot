import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import attest_writer_witness_client as client_attestation
from scripts import prepare_writer_witness_immutable_release as control
from scripts import verify_writer_witness_paired_attestation as pair_attestation


NOW = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
PROFILE = control._load_profile(control.DEFAULT_PROFILE_PATH)


def _write(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.write_bytes(payload)
    path.chmod(mode)


def _public_key_base64(private: Ed25519PrivateKey) -> str:
    return base64.b64encode(
        private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")


def _agent_config(
    *,
    site: str,
    root: Path,
    public_key: str,
    key_id: str | None = None,
) -> Path:
    secret = root / f"{site}.secret"
    public = root / f"{site}.public"
    ca = root / f"{site}.ca.pem"
    _write(secret, b"a" * 64 + b"\n")
    _write(public, (public_key + "\n").encode("ascii"), mode=0o644)
    _write(ca, b"non-secret-test-ca-bytes\n", mode=0o644)
    if site == "webapp_fi":
        payload = {
            "schema": control.AGENT_CONFIG_SCHEMA,
            "mode": "fenced_fi_writer",
            "site": site,
            "lease_file": "/var/lib/trading-bot-three-site/writer-terms/writer-lease.json",
            "fenced_preflight_config": "/etc/trading-bot-three-site/webapp-fi-fenced-writer-preflight.json",
            "runtime": {
                "compose_file": "/srv/trading-bot-three-site/control-releases/example/deploy/production/docker-compose.webapp-fi-writer-2c08.yml",
                "env_file": "/root/secure-envs/trading-bot/wa-fi-fenced-writer-runtime.env",
                "selection_env_file": None,
                "services": ["app", "bot"],
            },
        }
    else:
        payload = {
            "schema": control.AGENT_CONFIG_SCHEMA,
            "mode": "writer",
            "site": site,
            "lease_file": "/var/lib/trading-bot-three-site/writer-terms/writer-lease.json",
            "release_provenance": {
                "receipt": "/var/lib/trading-bot-three-site/release-provenance/example.json",
                "application_release_sha": "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
                "application_release_root": "/srv/trading-bot-three-site/releases/2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5",
            },
            "runtime": {
                "compose_file": "/srv/trading-bot-three-site/control-releases/example/deploy/production/docker-compose.webapp-ir-promoted-2c08.yml",
                "env_file": "/root/secure-envs/trading-bot/wa-ir-promotion.env",
                "selection_env_file": "/var/lib/trading-bot-three-site/selected-wa-ir-candidate.env",
                "services": ["db", "redis", "app"],
            },
        }
    payload["witness"] = {
        "url": "https://witness.example.invalid",
        "key_id": key_id or f"{site}-key",
        "secret_file": str(secret),
        "public_key_file": str(public),
        "ca_bundle": str(ca),
        "timeout_seconds": 3,
        "lease_duration_seconds": 60,
        "safety_margin_seconds": 15,
        "renew_interval_seconds": 10,
    }
    path = root / f"{site}.json"
    _write(path, json.dumps(payload, sort_keys=True).encode("utf-8"))
    return path


def _signed_witness_payload(
    *,
    private: Ed25519PrivateKey,
    site: str,
    key_id: str,
    request_id: str,
    now: datetime,
) -> dict[str, object]:
    unsigned: dict[str, object] = {
        "contract_version": 2,
        "request_id": request_id,
        "caller_site": site,
        "caller_key_id_sha256": hashlib.sha256(key_id.encode("utf-8")).hexdigest(),
        "witness_time": now.isoformat(),
        "runtime_profile_sha256": PROFILE["source_runtime_profile_sha256"],
        "release_manifest_sha256": PROFILE["source_release_manifest_sha256"],
        "witness_public_key": _public_key_base64(private),
        "profile": PROFILE["witness"],
    }
    return {
        **unsigned,
        "witness_signature": base64.b64encode(
            private.sign(client_attestation._canonical_json_bytes(unsigned))
        ).decode("ascii"),
    }


class _Response:
    def __init__(self, payload: bytes, *, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit: int) -> bytes:
        return self.payload


class WriterWitnessPairedClientAttestationTests(unittest.TestCase):
    def _receipts(
        self,
        root: Path,
        *,
        fi_request_id: str = "fi-attestation-nonce",
        ir_request_id: str = "ir-attestation-nonce",
        fi_key_id: str | None = None,
        ir_key_id: str | None = None,
    ) -> tuple[dict, dict]:
        private = Ed25519PrivateKey.generate()
        public_key = _public_key_base64(private)
        fi_config = _agent_config(
            site="webapp_fi",
            root=root,
            public_key=public_key,
            key_id=fi_key_id,
        )
        ir_config = _agent_config(
            site="webapp_ir",
            root=root,
            public_key=public_key,
            key_id=ir_key_id,
        )

        def response(*, headers, **_kwargs):
            return _signed_witness_payload(
                private=private,
                site=headers["X-Writer-Witness-Site"],
                key_id=headers["X-Writer-Witness-Key-Id"],
                request_id=headers["X-Writer-Witness-Request-Id"],
                now=NOW,
            )

        with patch.object(client_attestation, "_request_attestation", side_effect=response):
            fi = client_attestation.attest_client(
                agent_config_path=fi_config,
                now=NOW,
                request_id=fi_request_id,
            )
            ir = client_attestation.attest_client(
                agent_config_path=ir_config,
                now=NOW,
                request_id=ir_request_id,
            )
        return fi, ir

    def test_local_client_receipt_uses_own_ca_and_never_exposes_secret_or_url(self):
        with tempfile.TemporaryDirectory(prefix="witness-client-attestation-") as raw:
            receipt, _ = self._receipts(Path(raw))

        encoded = json.dumps(receipt, sort_keys=True)
        self.assertEqual(receipt["status"], "attested")
        self.assertTrue(receipt["tls_verified"])
        self.assertEqual(receipt["runtime_profile_sha256"], PROFILE["source_runtime_profile_sha256"])
        self.assertEqual(receipt["release_manifest_sha256"], PROFILE["source_release_manifest_sha256"])
        self.assertNotIn("a" * 64, encoded)
        self.assertNotIn("https://", encoded)
        self.assertIn("witness_endpoint_sha256", receipt)

    def test_https_request_constructs_ca_pinned_hostname_verifying_context(self):
        context = MagicMock()
        payload = json.dumps({"ok": True}).encode("utf-8")
        with patch.object(client_attestation.ssl, "create_default_context", return_value=context) as create_context, patch.object(
            client_attestation,
            "_open_https",
            return_value=_Response(payload),
        ):
            result = client_attestation._request_attestation(
                base_url="https://witness.example.invalid",
                headers={"Accept": "application/json"},
                ca_bundle=b"non-secret-test-ca-bytes\\n",
                timeout_seconds=3,
            )

        self.assertEqual(result, {"ok": True})
        create_context.assert_called_once_with(cadata="non-secret-test-ca-bytes\\n")
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_transport_explicitly_disables_environment_proxies_and_redirects(self):
        request = client_attestation.urlrequest.Request("https://witness.example.invalid")
        context = MagicMock()
        opener = MagicMock()
        with patch.object(client_attestation.urlrequest, "build_opener", return_value=opener) as build_opener:
            client_attestation._open_https(request, context=context, timeout=3)

        handlers = build_opener.call_args.args
        self.assertIsInstance(handlers[0], client_attestation.urlrequest.ProxyHandler)
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], client_attestation._NoRedirect)
        opener.open.assert_called_once_with(request, timeout=3)

    def test_client_rejects_unsafe_endpoint_and_nonce_before_the_request(self):
        with self.assertRaisesRegex(client_attestation.WriterWitnessClientAttestationError, "Witness URL"):
            client_attestation._validate_url("https://witness.example.invalid/extra")
        with self.assertRaisesRegex(client_attestation.WriterWitnessClientAttestationError, "nonce"):
            client_attestation._request_headers(
                key_id="fi-key",
                secret="a" * 64,
                site="webapp_fi",
                request_id="good\\r\\nbad",
                now=NOW,
            )
        self.assertEqual(
            client_attestation._validate_url("https://WITNESS.example.invalid/"),
            "https://witness.example.invalid",
        )

    def test_pair_verifier_rechecks_signed_nonce_time_tls_and_exact_hashes(self):
        with tempfile.TemporaryDirectory(prefix="witness-paired-attestation-") as raw:
            root = Path(raw)
            fi, ir = self._receipts(root)
            fi_path = root / "fi-receipt.json"
            ir_path = root / "ir-receipt.json"
            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))

            verified = pair_attestation.verify_paired_attestations(
                webapp_fi_attestation_path=fi_path,
                webapp_ir_attestation_path=ir_path,
                verification_time=NOW + timedelta(seconds=5),
            )

            self.assertEqual(verified["status"], "verified")
            self.assertTrue(verified["compatible"])
            self.assertEqual(
                verified["source_release_manifest_sha256"],
                PROFILE["source_release_manifest_sha256"],
            )
            self.assertNotEqual(
                verified["clients"]["webapp_fi"]["request_id"],
                verified["clients"]["webapp_ir"]["request_id"],
            )
            self.assertNotEqual(
                verified["clients"]["webapp_fi"]["caller_key_id_sha256"],
                verified["clients"]["webapp_ir"]["caller_key_id_sha256"],
            )
            self.assertNotIn("https://", json.dumps(verified, sort_keys=True))
            self.assertNotIn("a" * 64, json.dumps(verified, sort_keys=True))

            bad = json.loads(ir_path.read_text(encoding="utf-8"))
            bad["release_manifest_sha256"] = "0" * 64
            _write(ir_path, client_attestation._canonical_json_bytes(bad))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "profile or release manifest",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    verification_time=NOW + timedelta(seconds=5),
                )

    def test_client_rejects_a_signed_response_for_the_wrong_release_manifest(self):
        with tempfile.TemporaryDirectory(prefix="witness-client-release-mismatch-") as raw:
            root = Path(raw)
            private = Ed25519PrivateKey.generate()
            config = _agent_config(
                site="webapp_fi",
                root=root,
                public_key=_public_key_base64(private),
            )

            def response(*, headers, **_kwargs):
                payload = _signed_witness_payload(
                    private=private,
                    site=headers["X-Writer-Witness-Site"],
                    key_id=headers["X-Writer-Witness-Key-Id"],
                    request_id=headers["X-Writer-Witness-Request-Id"],
                    now=NOW,
                )
                payload["release_manifest_sha256"] = "0" * 64
                unsigned = {key: value for key, value in payload.items() if key != "witness_signature"}
                payload["witness_signature"] = base64.b64encode(
                    private.sign(client_attestation._canonical_json_bytes(unsigned))
                ).decode("ascii")
                return payload

            with patch.object(client_attestation, "_request_attestation", side_effect=response), self.assertRaisesRegex(
                client_attestation.WriterWitnessClientAttestationError,
                "profile or release manifest is unexpected",
            ):
                client_attestation.attest_client(
                    agent_config_path=config,
                    now=NOW,
                    request_id="wrong-release-nonce",
                )

    def test_client_rejects_a_signature_or_public_key_that_is_not_its_pinned_key(self):
        with tempfile.TemporaryDirectory(prefix="witness-client-signature-") as raw:
            root = Path(raw)
            expected_private = Ed25519PrivateKey.generate()
            different_private = Ed25519PrivateKey.generate()
            config = _agent_config(
                site="webapp_fi",
                root=root,
                public_key=_public_key_base64(expected_private),
            )

            def wrong_key_response(*, headers, **_kwargs):
                return _signed_witness_payload(
                    private=different_private,
                    site=headers["X-Writer-Witness-Site"],
                    key_id=headers["X-Writer-Witness-Key-Id"],
                    request_id=headers["X-Writer-Witness-Request-Id"],
                    now=NOW,
                )

            with patch.object(client_attestation, "_request_attestation", side_effect=wrong_key_response), self.assertRaisesRegex(
                client_attestation.WriterWitnessClientAttestationError,
                "differs from the client-pinned key",
            ):
                client_attestation.attest_client(
                    agent_config_path=config,
                    now=NOW,
                    request_id="wrong-key-nonce",
                )

            def bad_signature_response(*, headers, **_kwargs):
                payload = _signed_witness_payload(
                    private=expected_private,
                    site=headers["X-Writer-Witness-Site"],
                    key_id=headers["X-Writer-Witness-Key-Id"],
                    request_id=headers["X-Writer-Witness-Request-Id"],
                    now=NOW,
                )
                payload["witness_signature"] = base64.b64encode(b"x" * 64).decode("ascii")
                return payload

            with patch.object(client_attestation, "_request_attestation", side_effect=bad_signature_response), self.assertRaisesRegex(
                client_attestation.WriterWitnessClientAttestationError,
                "signature is invalid",
            ):
                client_attestation.attest_client(
                    agent_config_path=config,
                    now=NOW,
                    request_id="bad-signature-nonce",
                )

    def test_pair_rejects_replayed_nonce_stale_observation_and_trust_drift(self):
        with tempfile.TemporaryDirectory(prefix="witness-paired-rejection-") as raw:
            root = Path(raw)
            fi, ir = self._receipts(
                root,
                fi_request_id="shared-nonce",
                ir_request_id="shared-nonce",
            )
            fi_path = root / "fi-receipt.json"
            ir_path = root / "ir-receipt.json"
            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))

            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "distinct nonces",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    verification_time=NOW + timedelta(seconds=1),
                )

            fi, ir = self._receipts(root)
            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))

            same_client_fi, same_client_ir = self._receipts(
                root,
                fi_key_id="shared-client-key",
                ir_key_id="shared-client-key",
            )
            _write(fi_path, client_attestation._canonical_json_bytes(same_client_fi))
            _write(ir_path, client_attestation._canonical_json_bytes(same_client_ir))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "distinct authenticated client identities",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    verification_time=NOW + timedelta(seconds=1),
                )

            fi, ir = self._receipts(root)
            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))

            # Receipt paths are not labels: a valid IR proof cannot be
            # substituted for FI (or vice versa), because the signed caller
            # site must match the expected local lease-agent identity.
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "WebApp-FI live attestation identity is invalid",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=ir_path,
                    webapp_ir_attestation_path=fi_path,
                    verification_time=NOW + timedelta(seconds=1),
                )

            forged_fi_envelope = dict(ir)
            forged_fi_envelope["site"] = "webapp_fi"
            forged_fi_envelope["mode"] = "fenced_fi_writer"
            _write(fi_path, client_attestation._canonical_json_bytes(forged_fi_envelope))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "caller site does not match",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    verification_time=NOW + timedelta(seconds=1),
                )
            _write(fi_path, client_attestation._canonical_json_bytes(fi))

            # A stale FI observation cannot be refreshed by a newer IR proof.
            stale = dict(fi)
            stale["observed_at"] = (NOW - timedelta(seconds=61)).isoformat()
            _write(fi_path, client_attestation._canonical_json_bytes(stale))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "stale",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    verification_time=NOW,
                )

            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            drift = dict(ir)
            drift["ca_bundle_sha256"] = "0" * 64
            _write(ir_path, client_attestation._canonical_json_bytes(drift))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "identical TLS/endpoint Witness trust binding",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    verification_time=NOW + timedelta(seconds=1),
                )


if __name__ == "__main__":
    unittest.main()
