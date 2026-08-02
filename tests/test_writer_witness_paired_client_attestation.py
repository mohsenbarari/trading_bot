import base64
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
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


def _write_rotation_policy(
    root: Path,
    *,
    fi: dict,
    ir: dict,
    profile: dict = PROFILE,
    policy_id: str = "witness-rotation-current-v1",
    fi_key_id_sha256: str | None = None,
    ir_key_id_sha256: str | None = None,
    witness_endpoint_sha256: str | None = None,
    ca_bundle_sha256: str | None = None,
    witness_public_key_sha256: str | None = None,
    issued_at: datetime = NOW - timedelta(minutes=1),
    fi_not_before: datetime = NOW - timedelta(minutes=1),
    fi_not_after: datetime = NOW + timedelta(hours=1),
    ir_not_before: datetime = NOW - timedelta(minutes=1),
    ir_not_after: datetime = NOW + timedelta(hours=1),
) -> Path:
    public_key = client_attestation._decode_public_key(
        fi["pinned_witness_public_key"],
        field="test Witness public key",
    )
    payload = {
        "schema": control.CREDENTIAL_ROTATION_POLICY_SCHEMA,
        "policy_id": policy_id,
        "issued_at": issued_at.isoformat(),
        "profile": {
            "release_id": profile["release_id"],
            "source_commit": profile["source_commit"],
            "source_runtime_profile_sha256": profile["source_runtime_profile_sha256"],
            "source_release_manifest_sha256": profile["source_release_manifest_sha256"],
            "profile_sha256": pair_attestation._profile_sha256(profile),
        },
        "witness_trust": {
            "witness_endpoint_sha256": witness_endpoint_sha256
            or fi["witness_endpoint_sha256"],
            "ca_bundle_sha256": ca_bundle_sha256 or fi["ca_bundle_sha256"],
            "witness_public_key_sha256": witness_public_key_sha256
            or hashlib.sha256(public_key).hexdigest(),
        },
        "clients": {
            "webapp_fi": {
                "site": "webapp_fi",
                "key_id_sha256": fi_key_id_sha256
                or fi["witness_attestation"]["caller_key_id_sha256"],
                "generation": "fi-current-g1",
                "not_before": fi_not_before.isoformat(),
                "not_after": fi_not_after.isoformat(),
            },
            "webapp_ir": {
                "site": "webapp_ir",
                "key_id_sha256": ir_key_id_sha256
                or ir["witness_attestation"]["caller_key_id_sha256"],
                "generation": "ir-current-g1",
                "not_before": ir_not_before.isoformat(),
                "not_after": ir_not_after.isoformat(),
            },
        },
    }
    path = root / "rotation-policy.json"
    _write(path, control._canonical_json_bytes(payload) + b"\n")
    return path


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
        private: Ed25519PrivateKey | None = None,
    ) -> tuple[dict, dict]:
        private = private or Ed25519PrivateKey.generate()
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
            policy_path = _write_rotation_policy(root, fi=fi, ir=ir)

            verified = pair_attestation.verify_paired_attestations(
                webapp_fi_attestation_path=fi_path,
                webapp_ir_attestation_path=ir_path,
                _rotation_policy_path_for_test=policy_path,
                _verification_time_for_test=NOW + timedelta(seconds=5),
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
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=5),
                )

    def test_create_only_policy_builder_derives_verified_hashes_and_rejects_mismatch_or_replace(self):
        with tempfile.TemporaryDirectory(prefix="witness-policy-builder-") as raw:
            root = Path(raw)
            fi, ir = self._receipts(root)
            fi_path = root / "fi-receipt.json"
            ir_path = root / "ir-receipt.json"
            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))
            policy_path = root / pair_attestation.ROTATION_POLICY_FILENAME

            created = pair_attestation.create_rotation_policy(
                webapp_fi_attestation_path=fi_path,
                webapp_ir_attestation_path=ir_path,
                policy_id="witness-current-20260802",
                webapp_fi_generation="fi-g1",
                webapp_ir_generation="ir-g1",
                not_after=NOW + timedelta(hours=1),
                _output_path_for_test=policy_path,
                _verification_time_for_test=NOW + timedelta(seconds=1),
            )

            raw_policy = policy_path.read_bytes()
            parsed_policy = json.loads(raw_policy.decode("utf-8"))
            self.assertEqual(raw_policy, control._canonical_json_bytes(parsed_policy) + b"\n")
            self.assertEqual(created["status"], "created")
            self.assertEqual(
                created["clients"]["webapp_fi"]["caller_key_id_sha256"],
                fi["witness_attestation"]["caller_key_id_sha256"],
            )
            self.assertEqual(
                created["witness_public_key_sha256"],
                hashlib.sha256(
                    client_attestation._decode_public_key(
                        fi["pinned_witness_public_key"],
                        field="test Witness public key",
                    )
                ).hexdigest(),
            )
            encoded = json.dumps(created, sort_keys=True)
            self.assertNotIn("https://", encoded)
            self.assertNotIn("a" * 64, encoded)
            self.assertEqual(policy_path.stat().st_mode & 0o777, 0o600)

            verified = pair_attestation.verify_paired_attestations(
                webapp_fi_attestation_path=fi_path,
                webapp_ir_attestation_path=ir_path,
                _rotation_policy_path_for_test=policy_path,
                _verification_time_for_test=NOW + timedelta(seconds=2),
            )
            self.assertEqual(verified["credential_rotation_policy"]["policy_id"], created["policy_id"])

            # A different root-supplied receipt pair cannot replace the
            # current exact-current allowlist in place.
            replacement_fi, replacement_ir = self._receipts(
                root,
                fi_key_id="fi-replacement-key",
                ir_key_id="ir-replacement-key",
            )
            _write(fi_path, client_attestation._canonical_json_bytes(replacement_fi))
            _write(ir_path, client_attestation._canonical_json_bytes(replacement_ir))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "already exists; replacement is forbidden",
            ):
                pair_attestation.create_rotation_policy(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    policy_id="witness-current-replacement",
                    webapp_fi_generation="fi-g2",
                    webapp_ir_generation="ir-g2",
                    not_after=NOW + timedelta(hours=1),
                    _output_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=2),
                )

            # Even in a new path, two independently signed but different
            # Witness trust contracts cannot be promoted into one policy.
            mismatch_parent = root / "mismatch"
            mismatch_parent.mkdir(mode=0o700)
            trusted_private = Ed25519PrivateKey.generate()
            rogue_private = Ed25519PrivateKey.generate()
            mismatch_fi, _ = self._receipts(root, private=trusted_private)
            _, mismatch_ir = self._receipts(root, private=rogue_private)
            _write(fi_path, client_attestation._canonical_json_bytes(mismatch_fi))
            _write(ir_path, client_attestation._canonical_json_bytes(mismatch_ir))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "identical TLS/endpoint Witness trust binding",
            ):
                pair_attestation.create_rotation_policy(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    policy_id="witness-current-mismatch",
                    webapp_fi_generation="fi-g3",
                    webapp_ir_generation="ir-g3",
                    not_after=NOW + timedelta(hours=1),
                    _output_path_for_test=mismatch_parent
                    / pair_attestation.ROTATION_POLICY_FILENAME,
                    _verification_time_for_test=NOW + timedelta(seconds=2),
                )
            self.assertFalse(
                (mismatch_parent / pair_attestation.ROTATION_POLICY_FILENAME).exists()
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

    def test_pair_rejects_replayed_nonce_swap_stale_observation_and_trust_drift(self):
        with tempfile.TemporaryDirectory(prefix="witness-paired-rejection-") as raw:
            root = Path(raw)
            private = Ed25519PrivateKey.generate()
            fi, ir = self._receipts(
                root,
                fi_request_id="shared-nonce",
                ir_request_id="shared-nonce",
                private=private,
            )
            fi_path = root / "fi-receipt.json"
            ir_path = root / "ir-receipt.json"
            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))
            policy_path = _write_rotation_policy(root, fi=fi, ir=ir)

            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "distinct nonces",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
                )

            fi, ir = self._receipts(root, private=private)
            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))
            policy_path = _write_rotation_policy(root, fi=fi, ir=ir)

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
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
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
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
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
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW,
                )

            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            drift = dict(ir)
            drift["ca_bundle_sha256"] = "0" * 64
            _write(ir_path, client_attestation._canonical_json_bytes(drift))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "root-controlled Witness trust binding",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
                )

    def test_pair_rejects_previous_or_expired_exact_current_credential_policy(self):
        with tempfile.TemporaryDirectory(prefix="witness-paired-rotation-") as raw:
            root = Path(raw)
            private = Ed25519PrivateKey.generate()
            current_fi, current_ir = self._receipts(
                root,
                fi_key_id="fi-current-key",
                ir_key_id="ir-current-key",
                private=private,
            )
            policy_path = _write_rotation_policy(root, fi=current_fi, ir=current_ir)
            previous_fi, current_ir = self._receipts(
                root,
                fi_key_id="fi-previous-key",
                ir_key_id="ir-current-key",
                private=private,
            )
            fi_path = root / "fi-receipt.json"
            ir_path = root / "ir-receipt.json"
            _write(fi_path, client_attestation._canonical_json_bytes(previous_fi))
            _write(ir_path, client_attestation._canonical_json_bytes(current_ir))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "caller credential identity does not match",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
                )

            _write(fi_path, client_attestation._canonical_json_bytes(current_fi))
            policy_path = _write_rotation_policy(
                root,
                fi=current_fi,
                ir=current_ir,
                fi_not_after=NOW,
            )
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "exact-current credential rotation policy is not active",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
                )

    def test_pair_rejects_coordinated_receipt_trust_and_profile_policy_drift(self):
        with tempfile.TemporaryDirectory(prefix="witness-paired-trust-") as raw:
            root = Path(raw)
            trusted_private = Ed25519PrivateKey.generate()
            fi, ir = self._receipts(root, private=trusted_private)
            policy_path = _write_rotation_policy(root, fi=fi, ir=ir)
            rogue_fi, rogue_ir = self._receipts(root, private=Ed25519PrivateKey.generate())
            fi_path = root / "fi-receipt.json"
            ir_path = root / "ir-receipt.json"
            _write(fi_path, client_attestation._canonical_json_bytes(rogue_fi))
            _write(ir_path, client_attestation._canonical_json_bytes(rogue_ir))
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "root-controlled Witness trust binding",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
                )

            _write(fi_path, client_attestation._canonical_json_bytes(fi))
            _write(ir_path, client_attestation._canonical_json_bytes(ir))
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["profile"]["profile_sha256"] = "0" * 64
            _write(policy_path, control._canonical_json_bytes(policy) + b"\n")
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "not bound to the trusted control profile",
            ):
                pair_attestation.verify_paired_attestations(
                    webapp_fi_attestation_path=fi_path,
                    webapp_ir_attestation_path=ir_path,
                    _rotation_policy_path_for_test=policy_path,
                    _verification_time_for_test=NOW + timedelta(seconds=1),
                )

    def test_pair_requires_root_and_cli_has_no_profile_policy_or_clock_override(self):
        with patch.object(pair_attestation.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            pair_attestation.WriterWitnessPairAttestationError,
            "must run as root",
        ):
            pair_attestation.verify_paired_attestations(
                webapp_fi_attestation_path=Path("/tmp/fi.json"),
                webapp_ir_attestation_path=Path("/tmp/ir.json"),
                    _rotation_policy_path_for_test=Path("/tmp/policy.json"),
            )
        base_arguments = [
            "--webapp-fi-attestation",
            "/tmp/fi.json",
            "--webapp-ir-attestation",
            "/tmp/ir.json",
        ]
        for forbidden in (
            ["--verification-time", NOW.isoformat()],
            ["--rotation-policy", "/tmp/policy.json"],
            ["--profile", "/tmp/profile.json"],
            ["--maximum-age-seconds", "60"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                pair_attestation._parser().parse_args(base_arguments + forbidden)


if __name__ == "__main__":
    unittest.main()
