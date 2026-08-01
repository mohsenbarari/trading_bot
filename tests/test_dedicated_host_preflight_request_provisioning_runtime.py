"""Local fake-adapter tests for FI -> Object Storage -> WA-IR request setup.

No test opens a network connection, imports boto3, invokes age, or touches a
real host path.  The fake object store is deliberately only an in-memory
exact-version GET/PUT surface.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import dedicated_host_preflight_fi_request_provisioning_runtime as fi_runtime
from core import dedicated_host_preflight_ir_request_provisioning as protocol
from core import dedicated_host_preflight_ir_request_provisioning_runtime as ir_runtime
from core import dedicated_host_preflight_ir_witness_attestation as attestation
from core import dedicated_host_preflight_ir_witness_attestation_runtime as attester_runtime
from core import physical_arvan_immutability_preflight as preflight
from core import physical_arvan_s3_role_local_credential_reader as credential_reader
from core.dedicated_host_preflight_receipt import canonical_json_bytes
from core.physical_age_v1_adapter import PhysicalAgeV1DecryptorConfig, PhysicalAgeV1EncryptorConfig
from core.physical_arvan_exact_version_pull import RootOwnedArvanExactVersionPullConfig
from scripts.dedicated_host_preflight_manifest import READONLY_REQUEST_SCHEMA


NOW = datetime(2026, 7, 31, 15, 0, tzinfo=timezone.utc)
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-preflight-request"
CAMPAIGN = "preflight-request-runtime-20260731"
OPERATION = "11111111-2222-4333-8444-555555555555"
RELEASE = "a" * 40
MANIFEST = "b" * 64
RECIPIENT = "age1" + "a" * 30
FI_ACCESS = "FI-PROVISIONING-ACCESS-20260731"
IR_ACCESS = "IR-PROVISIONING-ACCESS-20260731"


def _identity(access: str) -> str:
    return hashlib.sha256(
        b"gold-trade-arvan-s3-machine-user-identity-v1\x00" + access.encode("ascii")
    ).hexdigest()


def _public(signer: Ed25519PrivateKey) -> bytes:
    return signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _key_id(value: bytes) -> str:
    return "ed25519-sha256:" + hashlib.sha256(value).hexdigest()


class _Body:
    def __init__(self, payload: bytes) -> None:
        self._source = io.BytesIO(payload)

    def read(self, amount: int = -1) -> bytes:
        return self._source.read(amount)

    def close(self) -> None:
        return None


class _MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.put_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, str]] = []

    def put_object(self, **request: object) -> dict[str, str]:
        self.put_calls.append(dict(request))
        key = request["Key"]
        body = request["Body"]
        if not isinstance(key, str) or not isinstance(body, bytes):
            raise AssertionError("invalid fake put")
        version = "version-" + hashlib.sha256(body).hexdigest()[:16]
        pair = (key, version)
        if pair in self.objects:
            raise AssertionError("fake immutable collision")
        metadata = request["Metadata"]
        if not isinstance(metadata, dict):
            raise AssertionError("invalid fake metadata")
        self.objects[pair] = (body, dict(metadata))
        return {"VersionId": version}

    def get_object(self, **request: str) -> dict[str, object]:
        self.get_calls.append(dict(request))
        body, metadata = self.objects[(request["Key"], request["VersionId"])]
        return {
            "Key": request["Key"],
            "VersionId": request["VersionId"],
            "ContentLength": len(body),
            "Metadata": dict(metadata),
            "Body": _Body(body),
        }


class _FakeEncryptor:
    def encrypt(self, *, recipient: str, plaintext_path: Path, ciphertext_path: Path) -> None:
        if recipient != RECIPIENT:
            raise AssertionError("unexpected recipient")
        ciphertext_path.write_bytes(b"age-encryption.org/v1\n" + plaintext_path.read_bytes())
        ciphertext_path.chmod(0o600)


class _FakeDecryptor:
    def decrypt(self, *, expected_recipient: str, ciphertext_path: Path, plaintext_path: Path) -> None:
        if expected_recipient != RECIPIENT:
            raise AssertionError("unexpected recipient")
        value = ciphertext_path.read_bytes()
        assert value.startswith(b"age-encryption.org/v1\n")
        plaintext_path.write_bytes(value[len(b"age-encryption.org/v1\n") :])
        plaintext_path.chmod(0o600)


def _request(wa_ir_public: bytes) -> attestation.ParsedWaIrWitnessAttestationRequest:
    readonly = {
        "schema": READONLY_REQUEST_SCHEMA,
        "campaign_id": CAMPAIGN,
        "operation_id": OPERATION,
        "release_sha": RELEASE,
        "role": "webapp_ir",
        "manifest_sha256": MANIFEST,
    }
    return attestation.parse_wa_ir_witness_attestation_request(
        canonical_json_bytes(
            {
                "schema": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_SCHEMA,
                "version": 1,
                "purpose": attestation.WA_IR_WITNESS_ATTESTATION_REQUEST_PURPOSE,
                "readonly_request": readonly,
                "readonly_request_sha256": hashlib.sha256(canonical_json_bytes(readonly) + b"\n").hexdigest(),
                "attestation_id": "66666666-7777-4888-8999-aaaaaaaaaaaa",
                "nonce": "A" * 22,
                "maximum_validity_seconds": 120,
                "wa_ir_attestation_key_id": _key_id(wa_ir_public),
            }
        )
        + b"\n"
    )


def _preflight() -> preflight.VerifiedPhysicalArvanImmutabilityPreflight:
    binding = preflight.PhysicalArvanImmutabilityPreflightBinding(
        campaign_id=CAMPAIGN,
        release_sha=RELEASE,
        source_site="webapp_fi",
        destination_site="webapp_ir",
        route_binding_sha256="c" * 64,
        endpoint=ENDPOINT,
        region=REGION,
        bucket=BUCKET,
        minimum_retention_days=90,
    )
    denied_fi = tuple(
        preflight.PhysicalArvanDeniedOperationObservation(operation=item, outcome="access-denied")
        for item in ("DeleteObject", "DeleteObjectVersion", "PutObject:overwrite")
    )
    denied_ir = tuple(
        preflight.PhysicalArvanDeniedOperationObservation(operation=item, outcome="access-denied")
        for item in ("DeleteObject", "DeleteObjectVersion", "ListBucket", "ListObjectVersions", "PutObject")
    )
    observation = preflight.build_physical_arvan_immutability_preflight_observation(
        binding=binding,
        versioning_status="Enabled",
        acl_posture="private-canonical-owner-only-v1",
        retention_mode="provider-verified-immutable-retention-v1",
        retention_policy_evidence_sha256="d" * 64,
        retention_days=180,
        credential_restrictions=(
            preflight.PhysicalArvanCredentialRestrictionObservation(
                role="fi-publisher",
                credential_posture="scoped-credential-probed",
                credential_identity_sha256=_identity(FI_ACCESS),
                allowed_operations=(
                    "GetBucketAcl", "GetBucketVersioning", "GetObjectLockConfiguration",
                    "PutObject:create-only", "ListObjectVersions:exact-key",
                    "GetObjectRetention:exact-version", "GetObject:exact-version", "HeadObject:exact-version",
                ),
                denied_operations=denied_fi,
            ),
            preflight.PhysicalArvanCredentialRestrictionObservation(
                role="ir-receiver",
                credential_posture="scoped-credential-probed",
                credential_identity_sha256=_identity(IR_ACCESS),
                allowed_operations=("GetObject:exact-version", "HeadObject:exact-version"),
                denied_operations=denied_ir,
            ),
            preflight.PhysicalArvanCredentialRestrictionObservation(
                role="witness-controller", credential_posture="no-object-storage-credential-issued",
                credential_identity_sha256=None, allowed_operations=(), denied_operations=(),
            ),
        ),
        disposable_probe=preflight.PhysicalArvanDisposableImmutabilityProbe(
            object_key=f"physical-preflight/{CAMPAIGN}/arvan-immutability/immutable-probe-20260731.age",
            version_id="probe-version-20260731",
            ciphertext_sha256="e" * 64,
            ciphertext_bytes=427,
            delete_version_outcome="access-denied",
            delete_marker_outcome="access-denied",
            exact_version_get_outcome="exact-version-get-succeeded",
            retrieved_version_id="probe-version-20260731",
            retrieved_ciphertext_sha256="e" * 64,
            retrieved_ciphertext_bytes=427,
        ),
        observed_at=NOW,
    )
    return preflight.verify_physical_arvan_immutability_preflight(observation, binding=binding, now=NOW)


@unittest.skipUnless(os.geteuid() == 0, "root-only local request provisioners require root")
class RequestProvisioningRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="preflight-request-runtime-")
        self.root = Path(self.temporary.name).resolve()
        self.root.chmod(0o700)
        self.fi_security = self.root / "fi-security"
        self.fi_state = self.root / "fi-state"
        self.ir_security = self.root / "ir-security"
        self.ir_age = self.root / "ir-age"
        self.ir_state_parent = self.root / "ir-state"
        self.fi_age = self.root / "fi-age"
        for path in (self.fi_security, self.fi_state, self.ir_security, self.ir_age, self.ir_state_parent, self.fi_age):
            path.mkdir(mode=0o700)
            path.chmod(0o700)
        self.fi_signer = Ed25519PrivateKey.generate()
        self.wa_ir_signer = Ed25519PrivateKey.generate()
        self.fi_public = _public(self.fi_signer)
        self.request = _request(_public(self.wa_ir_signer))
        self.fi_key = self.fi_security / "request-key.json"
        private = self.fi_signer.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        self.fi_key.write_bytes(
            canonical_json_bytes(
                {
                    "schema": protocol.FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_SCHEMA,
                    "version": 1,
                    "purpose": protocol.FI_WA_IR_PREFLIGHT_REQUEST_PROVISIONING_KEY_PURPOSE,
                    "algorithm": "ed25519",
                    "private_key_base64": base64.b64encode(private).decode("ascii"),
                    "public_key_sha256": hashlib.sha256(self.fi_public).hexdigest(),
                    "key_id": _key_id(self.fi_public),
                }
            )
            + b"\n"
        )
        self.fi_key.chmod(0o400)
        self.fi_locator = self.fi_state / "locator.json"
        self.ir_locator = self.ir_security / "locator.json"
        self.ir_request = self.ir_security / "request.json"
        self.ir_identity = self.ir_security / "age-identity.txt"
        self.ir_identity.write_text("fake only\n", encoding="ascii")
        self.ir_identity.chmod(0o400)
        self.ir_replay = self.ir_state_parent / "replay"
        self.store = _MemoryObjectStorage()
        self.preflight = _preflight()
        self.paths = mock.patch.multiple(
            fi_runtime,
            FIXED_FI_WA_IR_REQUEST_PROVISIONING_KEY_FILE=self.fi_key,
            FIXED_FI_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE=self.fi_locator,
        )
        self.ir_paths = mock.patch.multiple(
            ir_runtime,
            FIXED_WA_IR_REQUEST_PROVISIONING_LOCATOR_FILE=self.ir_locator,
            FIXED_WA_IR_REQUEST_PROVISIONING_AGE_IDENTITY_FILE=self.ir_identity,
            FIXED_WA_IR_REQUEST_PROVISIONING_AGE_WORKSPACE_ROOT=self.ir_age,
            FIXED_WA_IR_REQUEST_PROVISIONING_REPLAY_STATE_ROOT=self.ir_replay,
        )
        self.target_path = mock.patch.object(
            attester_runtime,
            "FIXED_WA_IR_WITNESS_ATTESTATION_REQUEST_FILE",
            self.ir_request,
        )
        self.paths.start()
        self.ir_paths.start()
        self.target_path.start()
        self.addCleanup(self.paths.stop)
        self.addCleanup(self.ir_paths.stop)
        self.addCleanup(self.target_path.stop)
        self.addCleanup(self.temporary.cleanup)

    def _pull_config(self) -> RootOwnedArvanExactVersionPullConfig:
        return RootOwnedArvanExactVersionPullConfig(
            endpoint=ENDPOINT, region=REGION, bucket=BUCKET,
            maximum_ciphertext_bytes=32 * 1024, enabled=True,
        )

    def _fi_age_config(self) -> PhysicalAgeV1EncryptorConfig:
        return PhysicalAgeV1EncryptorConfig(
            workspace_root=self.fi_age, recipient=RECIPIENT, enabled=True,
            maximum_plaintext_bytes=16 * 1024, maximum_ciphertext_bytes=16 * 1024,
        )

    def _ir_age_config(self) -> PhysicalAgeV1DecryptorConfig:
        return PhysicalAgeV1DecryptorConfig(
            workspace_root=self.ir_age, identity_path=self.ir_identity, recipient=RECIPIENT, enabled=True,
            maximum_plaintext_bytes=16 * 1024, maximum_ciphertext_bytes=16 * 1024,
        )

    @staticmethod
    def _route() -> credential_reader.ArvanS3RoleLocalRouteFacts:
        return credential_reader.ArvanS3RoleLocalRouteFacts(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )

    @staticmethod
    def _credential(access: str, secret: str) -> credential_reader.ArvanS3RoleLocalCredentialFacts:
        return credential_reader.ArvanS3RoleLocalCredentialFacts(
            access_key=access, secret_key=secret, identity_sha256=_identity(access), device=1, inode=1
        )

    def _fi_runtime(self) -> fi_runtime.RootOwnedFiWaIrPreflightRequestProvisioningRuntime:
        return fi_runtime.RootOwnedFiWaIrPreflightRequestProvisioningRuntime(
            fi_runtime.RootOwnedFiWaIrPreflightRequestProvisioningRuntimeConfig(
                exact_pull_config=self._pull_config(), age_encryptor_config=self._fi_age_config(),
                preflight=self.preflight, expected_fi_request_signer_public_key=self.fi_public, enabled=True,
            ),
            clock=lambda: NOW,
            credential_admitter=lambda _config: (self._route(), self._credential(FI_ACCESS, "fi-secret")),
            raw_s3_client_builder=lambda **_kwargs: self.store,
            age_encryptor_factory=lambda _config: _FakeEncryptor(),
        )

    def _ir_runtime(
        self,
        locator_sha256: str,
        *,
        now: datetime = NOW,
    ) -> ir_runtime.RootOwnedWaIrPreflightRequestProvisioningReceiver:
        return ir_runtime.RootOwnedWaIrPreflightRequestProvisioningReceiver(
            ir_runtime.RootOwnedWaIrPreflightRequestProvisioningReceiverConfig(
                exact_pull_config=self._pull_config(), age_decryptor_config=self._ir_age_config(),
                preflight=self.preflight, expected_fi_request_signer_public_key=self.fi_public,
                expected_locator_sha256=locator_sha256, enabled=True,
            ),
            clock=lambda: now,
            credential_admitter=lambda _config: (self._route(), self._credential(IR_ACCESS, "ir-secret")),
            raw_s3_client_builder=lambda **_kwargs: self.store,
            age_decryptor_factory=lambda _config: _FakeDecryptor(),
        )

    def test_signed_payload_is_create_only_readbacked_then_exact_pulled_and_installed_once(self) -> None:
        publisher = self._fi_runtime()
        signed = publisher.sign_request(request=self.request)
        publication = publisher.publish_signed_request(canonical_payload=signed)
        self.assertEqual(publication.canonical_locator, self.fi_locator.read_bytes())
        self.assertEqual(1, len(self.store.put_calls))
        self.assertEqual("*", self.store.put_calls[0]["IfNoneMatch"])
        self.assertEqual(1, len(self.store.get_calls))  # FI exact readback

        # This test models only the required external non-secret locator relay;
        # it is deliberately not a runtime FI->IR connection.
        self.ir_locator.write_bytes(publication.canonical_locator)
        self.ir_locator.chmod(0o600)
        receiver = self._ir_runtime(publication.locator_sha256)
        result = receiver.install()
        self.assertEqual(self.request.canonical_request, self.ir_request.read_bytes())
        self.assertEqual(publication.request_sha256, result.request_sha256)
        self.assertEqual(2, len(self.store.get_calls))  # one WA-IR exact GET
        self.assertTrue(all(set(call) == {"Bucket", "Key", "VersionId"} for call in self.store.get_calls))
        with self.assertRaisesRegex(
            ir_runtime.DedicatedHostPreflightIrRequestProvisioningRuntimeError,
            "WA_IR_REQUEST_PROVISIONING_REPLAY_REJECTED",
        ):
            receiver.install()
        self.assertEqual(2, len(self.store.get_calls))

    def test_tampered_or_non_controller_locator_is_rejected_before_receiver_credentials(self) -> None:
        publisher = self._fi_runtime()
        publication = publisher.publish_signed_request(canonical_payload=publisher.sign_request(request=self.request))
        altered = json.loads(publication.canonical_locator)
        altered["object"]["version_id"] = "latest"
        self.ir_locator.write_bytes(canonical_json_bytes(altered) + b"\n")
        self.ir_locator.chmod(0o600)
        receiver = self._ir_runtime(publication.locator_sha256)
        before = len(self.store.get_calls)
        with self.assertRaisesRegex(
            ir_runtime.DedicatedHostPreflightIrRequestProvisioningRuntimeError,
            "WA_IR_REQUEST_PROVISIONING_LOCATOR_REJECTED",
        ):
            receiver.install()
        self.assertEqual(before, len(self.store.get_calls))
        source = Path(ir_runtime.__file__).read_text(encoding="utf-8")
        self.assertNotIn("dedicated_host_preflight_controller", source)
        self.assertNotIn("dedicated_host_preflight_runtime_transport", source)
        self.assertFalse(hasattr(receiver, "deliver"))
        self.assertFalse(hasattr(receiver, "collect"))

    def test_expired_signed_locator_is_rejected_before_exact_get(self) -> None:
        publisher = self._fi_runtime()
        publication = publisher.publish_signed_request(canonical_payload=publisher.sign_request(request=self.request))
        self.ir_locator.write_bytes(publication.canonical_locator)
        self.ir_locator.chmod(0o600)
        receiver = self._ir_runtime(publication.locator_sha256, now=NOW + timedelta(seconds=121))
        before = len(self.store.get_calls)
        with self.assertRaisesRegex(
            ir_runtime.DedicatedHostPreflightIrRequestProvisioningRuntimeError,
            "WA_IR_REQUEST_PROVISIONING_LOCATOR_REJECTED",
        ):
            receiver.install()
        self.assertEqual(before, len(self.store.get_calls))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
