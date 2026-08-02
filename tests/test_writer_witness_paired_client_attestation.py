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
from scripts import writer_witness_rotation_lifecycle as lifecycle


NOW = datetime(2026, 8, 2, 3, 0, tzinfo=timezone.utc)
PROFILE = control._load_profile(control.DEFAULT_PROFILE_PATH)
NL = bytes((10,))


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


def _agent_config(site: str, root: Path, public_key: str, key_id: str | None) -> Path:
    secret = root / f"{site}.secret"
    public = root / f"{site}.public"
    ca = root / f"{site}.ca.pem"
    _write(secret, b"a" * 64 + NL)
    _write(public, (public_key + chr(10)).encode("ascii"), mode=0o644)
    _write(ca, b"non-secret-test-ca-bytes" + NL, mode=0o644)
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


def _witness_response(
    private: Ed25519PrivateKey,
    site: str,
    key_id: str,
    request_id: str,
    now: datetime = NOW,
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
    def __init__(self, payload: bytes):
        self.payload = payload
        self.status = 200

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
        fi_key_id: str = "fi-key",
        ir_key_id: str = "ir-key",
        fi_nonce: str = "fi-nonce",
        ir_nonce: str = "ir-nonce",
        private: Ed25519PrivateKey | None = None,
    ) -> tuple[dict, dict]:
        private = private or Ed25519PrivateKey.generate()
        public = _public_key_base64(private)
        fi_config = _agent_config("webapp_fi", root, public, fi_key_id)
        ir_config = _agent_config("webapp_ir", root, public, ir_key_id)

        def response(*, headers, **_kwargs):
            return _witness_response(
                private,
                headers["X-Writer-Witness-Site"],
                headers["X-Writer-Witness-Key-Id"],
                headers["X-Writer-Witness-Request-Id"],
            )

        with patch.object(client_attestation, "_request_attestation", side_effect=response):
            fi = client_attestation.attest_client(
                agent_config_path=fi_config,
                now=NOW,
                request_id=fi_nonce,
            )
            ir = client_attestation.attest_client(
                agent_config_path=ir_config,
                now=NOW,
                request_id=ir_nonce,
            )
        return fi, ir

    def _state(self, root: Path, label: str) -> Path:
        parent = root / label
        parent.mkdir(mode=0o700)
        return parent / lifecycle.STATE_DIRECTORY_NAME

    def _receipt_paths(
        self,
        root: Path,
        fi: dict,
        ir: dict,
        label: str,
    ) -> tuple[Path, Path]:
        fi_path = root / f"fi-{label}.json"
        ir_path = root / f"ir-{label}.json"
        _write(fi_path, client_attestation._canonical_json_bytes(fi) + NL)
        _write(ir_path, client_attestation._canonical_json_bytes(ir) + NL)
        return fi_path, ir_path

    def _activate(
        self,
        root: Path,
        state: Path,
        fi: dict,
        ir: dict,
        label: str,
        policy_id: str,
        now: datetime = NOW,
        not_after: datetime | None = None,
    ) -> tuple[dict, Path, Path]:
        fi_path, ir_path = self._receipt_paths(root, fi, ir, label)
        result = pair_attestation.create_rotation_policy(
            webapp_fi_attestation_path=fi_path,
            webapp_ir_attestation_path=ir_path,
            policy_id=policy_id,
            webapp_fi_generation=f"fi-{label}",
            webapp_ir_generation=f"ir-{label}",
            not_after=not_after or now + timedelta(hours=1),
            _rotation_state_directory_for_test=state,
            _verification_time_for_test=now,
        )
        return result, fi_path, ir_path

    def _verify(self, state: Path, fi: Path, ir: Path, now: datetime = NOW + timedelta(seconds=1)) -> dict:
        return pair_attestation.verify_paired_attestations(
            webapp_fi_attestation_path=fi,
            webapp_ir_attestation_path=ir,
            _rotation_state_directory_for_test=state,
            _verification_time_for_test=now,
        )

    # Keep the Release-0 client-proof cases separately named: their names are
    # part of the audit trail for this hardening change.
    def test_local_client_receipt_uses_own_ca_and_never_exposes_secret_or_url(self):
        with tempfile.TemporaryDirectory(prefix="witness-client-") as raw:
            receipt, _ir = self._receipts(Path(raw))
        encoded = json.dumps(receipt, sort_keys=True)
        self.assertEqual(receipt["status"], "attested")
        self.assertTrue(receipt["tls_verified"])
        self.assertEqual(
            receipt["runtime_profile_sha256"],
            PROFILE["source_runtime_profile_sha256"],
        )
        self.assertEqual(
            receipt["release_manifest_sha256"],
            PROFILE["source_release_manifest_sha256"],
        )
        self.assertNotIn("https://", encoded)
        self.assertNotIn("a" * 64, encoded)

    def test_https_request_constructs_ca_pinned_hostname_verifying_context(self):
        context = MagicMock()
        payload = json.dumps({"ok": True}).encode("utf-8")
        with patch.object(
            client_attestation.ssl,
            "create_default_context",
            return_value=context,
        ) as create_context, patch.object(
            client_attestation,
            "_open_https",
            return_value=_Response(payload),
        ):
            self.assertEqual(
                client_attestation._request_attestation(
                    base_url="https://witness.example.invalid",
                    headers={"Accept": "application/json"},
                    ca_bundle=b"non-secret-test-ca-bytes" + NL,
                    timeout_seconds=3,
                ),
                {"ok": True},
            )
        create_context.assert_called_once_with(
            cadata="non-secret-test-ca-bytes" + chr(10)
        )
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_transport_explicitly_disables_environment_proxies_and_redirects(self):
        opener = MagicMock()
        with patch.object(client_attestation.urlrequest, "build_opener", return_value=opener) as build:
            client_attestation._open_https(
                client_attestation.urlrequest.Request("https://witness.example.invalid"),
                context=MagicMock(),
                timeout=3,
            )
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], client_attestation._NoRedirect)

    def test_client_rejects_unsafe_endpoint_and_nonce_before_the_request(self):
        with self.assertRaisesRegex(client_attestation.WriterWitnessClientAttestationError, "Witness URL"):
            client_attestation._validate_url("https://witness.example.invalid/extra")
        with self.assertRaisesRegex(client_attestation.WriterWitnessClientAttestationError, "nonce"):
            client_attestation._request_headers(
                key_id="fi",
                secret="a" * 64,
                site="webapp_fi",
                request_id="bad" + chr(13) + chr(10) + "nonce",
                now=NOW,
            )
        self.assertEqual(
            client_attestation._validate_url("https://WITNESS.example.invalid/"),
            "https://witness.example.invalid",
        )

    def test_client_rejects_a_signature_or_public_key_that_is_not_its_pinned_key(self):
        with tempfile.TemporaryDirectory(prefix="witness-client-key-") as raw:
            root = Path(raw)
            expected = Ed25519PrivateKey.generate()
            wrong = Ed25519PrivateKey.generate()
            config = _agent_config("webapp_fi", root, _public_key_base64(expected), "fi-key")
            with patch.object(
                client_attestation,
                "_request_attestation",
                side_effect=lambda **kwargs: _witness_response(
                    wrong,
                    kwargs["headers"]["X-Writer-Witness-Site"],
                    kwargs["headers"]["X-Writer-Witness-Key-Id"],
                    kwargs["headers"]["X-Writer-Witness-Request-Id"],
                ),
            ), self.assertRaisesRegex(
                client_attestation.WriterWitnessClientAttestationError,
                "differs from the client-pinned key",
            ):
                client_attestation.attest_client(
                    agent_config_path=config,
                    now=NOW,
                    request_id="wrong-key",
                )
            def bad_signature(*, headers, **_kwargs):
                payload = _witness_response(
                    expected,
                    headers["X-Writer-Witness-Site"],
                    headers["X-Writer-Witness-Key-Id"],
                    headers["X-Writer-Witness-Request-Id"],
                )
                payload["witness_signature"] = base64.b64encode(b"x" * 64).decode("ascii")
                return payload
            with patch.object(
                client_attestation,
                "_request_attestation",
                side_effect=bad_signature,
            ), self.assertRaisesRegex(
                client_attestation.WriterWitnessClientAttestationError,
                "signature is invalid",
            ):
                client_attestation.attest_client(
                    agent_config_path=config,
                    now=NOW,
                    request_id="bad-signature",
                )

    def test_client_rejects_a_signed_response_for_the_wrong_release_manifest(self):
        with tempfile.TemporaryDirectory(prefix="witness-client-release-") as raw:
            root = Path(raw)
            private = Ed25519PrivateKey.generate()
            config = _agent_config("webapp_fi", root, _public_key_base64(private), "fi-key")

            def wrong_release(*, headers, **_kwargs):
                payload = _witness_response(
                    private,
                    headers["X-Writer-Witness-Site"],
                    headers["X-Writer-Witness-Key-Id"],
                    headers["X-Writer-Witness-Request-Id"],
                )
                payload["release_manifest_sha256"] = "0" * 64
                unsigned = {
                    key: value for key, value in payload.items() if key != "witness_signature"
                }
                payload["witness_signature"] = base64.b64encode(
                    private.sign(client_attestation._canonical_json_bytes(unsigned))
                ).decode("ascii")
                return payload

            with patch.object(
                client_attestation,
                "_request_attestation",
                side_effect=wrong_release,
            ), self.assertRaisesRegex(
                client_attestation.WriterWitnessClientAttestationError,
                "profile or release manifest is unexpected",
            ):
                client_attestation.attest_client(
                    agent_config_path=config,
                    now=NOW,
                    request_id="wrong-release",
                )

    def test_pair_verifier_rechecks_signed_nonce_time_tls_and_exact_hashes(self):
        with tempfile.TemporaryDirectory(prefix="witness-first-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            fi, ir = self._receipts(root)
            created, fi_path, ir_path = self._activate(
                root, state, fi, ir, "g1", "witness-current-g1"
            )
            verified = self._verify(state, fi_path, ir_path)
            self.assertEqual(created["status"], "activated")
            self.assertEqual(created["rotation_sequence"], 1)
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
            self.assertEqual(verified["credential_rotation_policy"]["sequence"], 1)
            self.assertEqual(
                verified["credential_rotation_policy"]["policy_id"],
                created["policy_id"],
            )
            encoded = json.dumps({"created": created, "verified": verified}, sort_keys=True)
            self.assertNotIn("https://", encoded)
            self.assertNotIn("a" * 64, encoded)

    def test_create_only_policy_builder_derives_verified_hashes_and_rejects_mismatch_or_replace(self):
        with tempfile.TemporaryDirectory(prefix="witness-create-only-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            fi, ir = self._receipts(root)
            first, _fi_path, _ir_path = self._activate(
                root, state, fi, ir, "first", "witness-no-replace"
            )
            snapshot = lifecycle.resolve_current_policy(
                profile_sha256=pair_attestation._profile_sha256(PROFILE),
                state_directory=state,
            )
            original = snapshot.policy_raw
            new_fi, new_ir = self._receipts(
                root,
                fi_key_id="fi-replacement",
                ir_key_id="ir-replacement",
            )
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "cannot create and activate",
            ):
                self._activate(
                    root,
                    state,
                    new_fi,
                    new_ir,
                    "replacement",
                    "witness-no-replace",
                    now=NOW + timedelta(seconds=3),
            )
            self.assertEqual(snapshot.policy_path.read_bytes(), original)
            self.assertEqual(first["rotation_sequence"], 1)

            # A fresh candidate state cannot turn two independently signed
            # Witness trust contracts into one policy.
            mismatch_state = self._state(root, "mismatch")
            trusted = Ed25519PrivateKey.generate()
            rogue = Ed25519PrivateKey.generate()
            mismatch_fi, _unused_ir = self._receipts(root, private=trusted)
            _unused_fi, mismatch_ir = self._receipts(root, private=rogue)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "identical TLS/endpoint Witness trust binding",
            ):
                self._activate(
                    root,
                    mismatch_state,
                    mismatch_fi,
                    mismatch_ir,
                    "mismatch",
                    "witness-mismatch",
                )
            self.assertFalse(mismatch_state.exists())

    def test_two_consecutive_rotations_activate_new_fi_ir_keys_and_preserve_old_policy(self):
        with tempfile.TemporaryDirectory(prefix="witness-rotation-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            private = Ed25519PrivateKey.generate()
            fi1, ir1 = self._receipts(
                root,
                fi_key_id="fi-key-one",
                ir_key_id="ir-key-one",
                private=private,
            )
            first, fi1_path, ir1_path = self._activate(
                root, state, fi1, ir1, "one", "witness-current-one"
            )
            old = lifecycle.resolve_current_policy(
                profile_sha256=pair_attestation._profile_sha256(PROFILE),
                state_directory=state,
            )
            old_raw = old.policy_raw
            fi2, ir2 = self._receipts(
                root,
                fi_key_id="fi-key-two",
                ir_key_id="ir-key-two",
                private=private,
            )
            second, fi2_path, ir2_path = self._activate(
                root,
                state,
                fi2,
                ir2,
                "two",
                "witness-current-two",
                now=NOW + timedelta(seconds=3),
            )
            verified = self._verify(
                state,
                fi2_path,
                ir2_path,
                now=NOW + timedelta(seconds=4),
            )
            self.assertEqual(first["rotation_sequence"], 1)
            self.assertEqual(second["rotation_sequence"], 2)
            self.assertEqual(verified["credential_rotation_policy"]["sequence"], 2)
            self.assertEqual(old.policy_path.read_bytes(), old_raw)
            self.assertEqual(old.policy_path.stat().st_mode & 0o777, 0o400)
            paths = lifecycle._state_paths(state, create=False)
            self.assertEqual(len(list(paths.policies.iterdir())), 2)
            current = lifecycle.resolve_current_policy(
                profile_sha256=pair_attestation._profile_sha256(PROFILE),
                state_directory=state,
            )
            self.assertEqual(current.ledger_entries, 2)
            self.assertEqual(current.sequence, 2)
            self.assertNotEqual(current.ledger_sha256, old.ledger_sha256)
            self.assertRegex(current.ledger_sha256, r"^[0-9a-f]{64}$")
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "caller credential identity does not match",
            ):
                self._verify(
                    state,
                    fi1_path,
                    ir1_path,
                    now=NOW + timedelta(seconds=4),
                )

    def test_pair_rejects_previous_or_expired_exact_current_credential_policy(self):
        with tempfile.TemporaryDirectory(prefix="witness-ttl-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            private = Ed25519PrivateKey.generate()
            fi, ir = self._receipts(root, private=private)
            _created, fi_path, ir_path = self._activate(
                root,
                state,
                fi,
                ir,
                "short",
                "witness-short",
                not_after=NOW + timedelta(seconds=61),
            )
            previous_fi, _same_ir = self._receipts(
                root,
                fi_key_id="fi-previous-key",
                ir_key_id="ir-key",
                private=private,
            )
            _write(fi_path, client_attestation._canonical_json_bytes(previous_fi) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "caller credential identity does not match",
            ):
                self._verify(
                    state,
                    fi_path,
                    ir_path,
                    now=NOW + timedelta(seconds=1),
                )
            _write(fi_path, client_attestation._canonical_json_bytes(fi) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "exact-current credential rotation policy is not active",
            ):
                self._verify(
                    state,
                    fi_path,
                    ir_path,
                    now=NOW + timedelta(seconds=62),
                )
            other = self._state(root, "too-long")
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "TTL exceeds the trusted profile ceiling",
            ):
                self._activate(
                    root,
                    other,
                    fi,
                    ir,
                    "too-long",
                    "witness-too-long",
                    not_after=NOW
                    + timedelta(
                        seconds=PROFILE["client_credential_rotation"][
                            "maximum_policy_ttl_seconds"
                        ]
                        + 1
                    ),
                )
            self.assertFalse(other.exists())

    def test_policy_loader_rejects_a_handcrafted_ttl_over_profile_ceiling(self):
        with tempfile.TemporaryDirectory(prefix="witness-policy-schema-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            fi, ir = self._receipts(root)
            self._activate(root, state, fi, ir, "valid", "witness-valid")
            snapshot = lifecycle.resolve_current_policy(
                profile_sha256=pair_attestation._profile_sha256(PROFILE),
                state_directory=state,
            )
            payload = json.loads(snapshot.policy_raw.decode("utf-8"))
            issued = datetime.fromisoformat(payload["issued_at"])
            payload["not_after"] = (
                issued
                + timedelta(
                    seconds=PROFILE["client_credential_rotation"][
                        "maximum_policy_ttl_seconds"
                    ]
                    + 1
                )
            ).isoformat()
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "policy TTL is invalid",
            ):
                pair_attestation._load_rotation_policy(
                    control._canonical_json_bytes(payload) + NL,
                    profile=PROFILE,
                )

    def test_selector_rollback_and_selector_policy_tamper_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="witness-tamper-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            private = Ed25519PrivateKey.generate()
            fi1, ir1 = self._receipts(root, private=private)
            self._activate(root, state, fi1, ir1, "one", "witness-one")
            paths = lifecycle._state_paths(state, create=False)
            previous_current = paths.current_selector.read_bytes()
            fi2, ir2 = self._receipts(
                root,
                fi_key_id="fi-two",
                ir_key_id="ir-two",
                private=private,
            )
            _created, fi2_path, ir2_path = self._activate(
                root,
                state,
                fi2,
                ir2,
                "two",
                "witness-two",
                now=NOW + timedelta(seconds=3),
            )
            latest_current = paths.current_selector.read_bytes()
            _write(paths.current_selector, previous_current)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "credential rotation lifecycle is invalid",
            ):
                self._verify(
                    state,
                    fi2_path,
                    ir2_path,
                    now=NOW + timedelta(seconds=4),
                )
            _write(paths.current_selector, latest_current)
            snapshot = lifecycle.resolve_current_policy(
                profile_sha256=pair_attestation._profile_sha256(PROFILE),
                state_directory=state,
            )
            selector = paths.selectors / snapshot.selector_filename
            selector_raw = selector.read_bytes()
            _write(selector, selector_raw.replace(b"selector", b"tamper", 1))
            selector.chmod(0o400)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "credential rotation lifecycle is invalid",
            ):
                self._verify(
                    state,
                    fi2_path,
                    ir2_path,
                    now=NOW + timedelta(seconds=4),
                )

    def test_crash_window_blocks_verification_and_only_next_rotation_recovers_exact_head(self):
        """A verifier cannot repair a pointer/ledger crash window by itself."""

        with tempfile.TemporaryDirectory(prefix="witness-crash-window-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            private = Ed25519PrivateKey.generate()
            fi1, ir1 = self._receipts(root, private=private)
            original_write = lifecycle._write_immutable

            def interrupt_activation(path: Path, payload: bytes, *, field: str) -> None:
                if field == "Writer Witness immutable activation":
                    raise lifecycle.WriterWitnessRotationLifecycleError("simulated interruption")
                original_write(path, payload, field=field)

            with patch.object(lifecycle, "_write_immutable", side_effect=interrupt_activation):
                with self.assertRaisesRegex(
                    pair_attestation.WriterWitnessPairAttestationError,
                    "cannot create and activate",
                ):
                    self._activate(
                        root,
                        state,
                        fi1,
                        ir1,
                        "crash-one",
                        "witness-crash-one",
                    )
            paths = lifecycle._state_paths(state, create=False)
            old_policy = paths.policies / lifecycle.policy_filename("witness-crash-one")
            old_raw = old_policy.read_bytes()
            self.assertEqual(old_policy.stat().st_mode & 0o777, 0o400)
            self.assertEqual(list(paths.activations.iterdir()), [])
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "credential rotation lifecycle is invalid",
            ):
                self._verify(
                    state,
                    root / "fi-crash-one.json",
                    root / "ir-crash-one.json",
                )
            self.assertEqual(list(paths.activations.iterdir()), [])

            fi2, ir2 = self._receipts(
                root,
                fi_key_id="fi-crash-two",
                ir_key_id="ir-crash-two",
                private=private,
            )
            recovered, fi2_path, ir2_path = self._activate(
                root,
                state,
                fi2,
                ir2,
                "crash-two",
                "witness-crash-two",
                now=NOW + timedelta(seconds=3),
            )
            self.assertEqual(recovered["rotation_sequence"], 2)
            self.assertEqual(old_policy.read_bytes(), old_raw)
            self.assertEqual(len(list(paths.activations.iterdir())), 2)
            self.assertEqual(
                self._verify(
                    state,
                    fi2_path,
                    ir2_path,
                    now=NOW + timedelta(seconds=4),
                )["credential_rotation_policy"]["sequence"],
                2,
            )

    def test_pair_rejects_replayed_nonce_swap_stale_observation_and_trust_drift(self):
        with tempfile.TemporaryDirectory(prefix="witness-pair-negative-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            private = Ed25519PrivateKey.generate()
            fi, ir = self._receipts(root, private=private)
            _created, fi_path, ir_path = self._activate(
                root, state, fi, ir, "same", "witness-same"
            )
            replay_fi, replay_ir = self._receipts(
                root,
                fi_nonce="same",
                ir_nonce="same",
                private=private,
            )
            _write(fi_path, client_attestation._canonical_json_bytes(replay_fi) + NL)
            _write(ir_path, client_attestation._canonical_json_bytes(replay_ir) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "distinct nonces",
            ):
                self._verify(state, fi_path, ir_path)
            fresh = self._state(root, "fresh")
            fi, ir = self._receipts(root, private=private)
            _created, fi_path, ir_path = self._activate(
                root, fresh, fi, ir, "fresh", "witness-fresh"
            )
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "WebApp-FI live attestation identity is invalid",
            ):
                self._verify(fresh, ir_path, fi_path)
            stale = dict(fi)
            stale["observed_at"] = (NOW - timedelta(seconds=61)).isoformat()
            _write(fi_path, client_attestation._canonical_json_bytes(stale) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "stale",
            ):
                self._verify(fresh, fi_path, ir_path, now=NOW)
            _write(fi_path, client_attestation._canonical_json_bytes(fi) + NL)
            drift = dict(ir)
            drift["ca_bundle_sha256"] = "0" * 64
            _write(ir_path, client_attestation._canonical_json_bytes(drift) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "root-controlled Witness trust binding",
            ):
                self._verify(fresh, fi_path, ir_path)

    def test_pair_rejects_coordinated_receipt_trust_and_profile_policy_drift(self):
        with tempfile.TemporaryDirectory(prefix="witness-pair-profile-") as raw:
            root = Path(raw)
            state = self._state(root, "state")
            fi, ir = self._receipts(root)
            _created, fi_path, ir_path = self._activate(
                root, state, fi, ir, "profile", "witness-profile"
            )
            bad_release = dict(ir)
            bad_release["release_manifest_sha256"] = "0" * 64
            _write(ir_path, client_attestation._canonical_json_bytes(bad_release) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "profile or release manifest",
            ):
                self._verify(state, fi_path, ir_path)
            forged_site = dict(ir)
            forged_site["site"] = "webapp_fi"
            forged_site["mode"] = "fenced_fi_writer"
            _write(ir_path, client_attestation._canonical_json_bytes(forged_site) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "WebApp-IR live attestation identity is invalid",
            ):
                self._verify(state, fi_path, ir_path)

            # Even two mutually consistent rogue receipts cannot move the
            # pinned Witness trust contract without a new immutable policy.
            rogue_fi, rogue_ir = self._receipts(
                root,
                private=Ed25519PrivateKey.generate(),
            )
            _write(fi_path, client_attestation._canonical_json_bytes(rogue_fi) + NL)
            _write(ir_path, client_attestation._canonical_json_bytes(rogue_ir) + NL)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "root-controlled Witness trust binding",
            ):
                self._verify(state, fi_path, ir_path)

            # A root-side attempt to hand-edit the immutable policy is caught
            # before policy semantics are trusted, because its ledger hash no
            # longer matches the selector/activation head.
            _write(fi_path, client_attestation._canonical_json_bytes(fi) + NL)
            _write(ir_path, client_attestation._canonical_json_bytes(ir) + NL)
            snapshot = lifecycle.resolve_current_policy(
                profile_sha256=pair_attestation._profile_sha256(PROFILE),
                state_directory=state,
            )
            changed_policy = json.loads(snapshot.policy_raw.decode("utf-8"))
            changed_policy["profile"]["profile_sha256"] = "0" * 64
            _write(snapshot.policy_path, control._canonical_json_bytes(changed_policy) + NL)
            snapshot.policy_path.chmod(0o400)
            with self.assertRaisesRegex(
                pair_attestation.WriterWitnessPairAttestationError,
                "credential rotation lifecycle is invalid",
            ):
                self._verify(state, fi_path, ir_path)

    def test_pair_requires_root_and_cli_has_no_profile_policy_or_clock_override(self):
        with patch.object(pair_attestation.os, "geteuid", return_value=1000), self.assertRaisesRegex(
            pair_attestation.WriterWitnessPairAttestationError,
            "must run as root",
        ):
            pair_attestation.verify_paired_attestations(
                webapp_fi_attestation_path=Path("/tmp/fi.json"),
                webapp_ir_attestation_path=Path("/tmp/ir.json"),
                _rotation_state_directory_for_test=Path(
                    "/tmp/state/" + lifecycle.STATE_DIRECTORY_NAME
                ),
            )
        base = [
            "--webapp-fi-attestation",
            "/tmp/fi.json",
            "--webapp-ir-attestation",
            "/tmp/ir.json",
        ]
        for forbidden in (
            ["--verification-time", NOW.isoformat()],
            ["--rotation-policy", "/tmp/policy.json"],
            ["--rotation-state", "/tmp/state"],
            ["--profile", "/tmp/profile.json"],
            ["--maximum-age-seconds", "60"],
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                pair_attestation._parser().parse_args(base + forbidden)

    def test_lifecycle_rejects_a_symlinked_or_nonprivate_parent_chain(self):
        with tempfile.TemporaryDirectory(prefix="witness-state-chain-") as raw:
            root = Path(raw)
            real_parent = root / "real-parent"
            real_parent.mkdir(mode=0o700)
            indirect_parent = root / "indirect-parent"
            indirect_parent.symlink_to(real_parent, target_is_directory=True)
            state = indirect_parent / lifecycle.STATE_DIRECTORY_NAME
            with self.assertRaisesRegex(
                lifecycle.WriterWitnessRotationLifecycleError,
                "parent chain",
            ):
                lifecycle._state_paths(state, create=True)


if __name__ == "__main__":
    unittest.main()
