"""Adversarial tests for local-only fresh WA-IR bootstrap preparation.

These tests use only public synthetic metadata and a temporary local state
directory.  They never publish, encrypt, contact Object Storage, connect to a
host, or execute a bootstrap archive.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from uuid import UUID, uuid4

from core import physical_wa_ir_bootstrap_bundle_builder as builder


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_wa_ir_bootstrap_bundle_builder.py"
)

_CAMPAIGN = "wa-ir-bootstrap-20260731"
_RELEASE_SHA = "a" * 40
_CONTROL_RELEASE_SHA = "b" * 40
_RELEASE_BUNDLE_SHA256 = "c" * 64
_IMAGE_SET_SHA256 = "d" * 64
_RELEASE_PROVENANCE_SHA256 = "e" * 64
_AGE_RECIPIENT = "age1" + "q" * 52
_ALTERNATE_AGE_RECIPIENT = "age1" + "p" * 52


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


class PhysicalWaIrBootstrapBundleBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.geteuid() != 0:
            self.skipTest("root-owned local freshness state requires root")
        self.temporary = tempfile.TemporaryDirectory()
        self.state_root = Path(self.temporary.name)
        self.state_root.chmod(0o700)
        self.now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _config(self, **changes: object) -> builder.PhysicalWaIrBootstrapBundleBuilderConfig:
        fields: dict[str, object] = {
            "state_root": self.state_root,
            "enabled": True,
        }
        fields.update(changes)
        return builder.PhysicalWaIrBootstrapBundleBuilderConfig(**fields)

    def _sealed_release(
        self,
        *,
        campaign_id: str = _CAMPAIGN,
        release_sha: str = _RELEASE_SHA,
        sealed_at: datetime | None = None,
        seal_id: UUID | None = None,
    ) -> builder.SealedWaIrBootstrapExactReleaseBinding:
        return builder.seal_wa_ir_bootstrap_exact_release_binding(
            builder.WaIrBootstrapExactReleaseBinding(
                campaign_id=campaign_id,
                release_sha=release_sha,
                control_release_sha=_CONTROL_RELEASE_SHA,
                release_bundle_sha256=_RELEASE_BUNDLE_SHA256,
                image_set_sha256=_IMAGE_SET_SHA256,
                release_provenance_sha256=_RELEASE_PROVENANCE_SHA256,
                source_site="webapp_fi",
                destination_site="webapp_ir",
                seal_id=uuid4() if seal_id is None else seal_id,
                sealed_at=self.now if sealed_at is None else sealed_at,
            )
        )

    def _recipient(
        self,
        *,
        campaign_id: str = _CAMPAIGN,
        recipient: str = _AGE_RECIPIENT,
        issued_at: datetime | None = None,
        generation_id: UUID | None = None,
    ) -> builder.VerifiedWaIrBootstrapFreshAgeRecipient:
        return builder.verify_wa_ir_bootstrap_fresh_age_recipient(
            builder.WaIrBootstrapFreshAgeRecipient(
                campaign_id=campaign_id,
                recipient=recipient,
                recipient_public_sha256=_sha256(recipient),
                generation_id=uuid4() if generation_id is None else generation_id,
                issued_at=self.now if issued_at is None else issued_at,
            )
        )

    def _raw_locator(
        self,
        *,
        sealed: builder.SealedWaIrBootstrapExactReleaseBinding,
        recipient: builder.VerifiedWaIrBootstrapFreshAgeRecipient,
        bootstrap_id: UUID | None = None,
        locator_id: UUID | None = None,
        issued_at: datetime | None = None,
        object_key: str | None = None,
        version_id: str = "bootstrap-version-20260731-A_+=",
        **changes: object,
    ) -> builder.WaIrBootstrapImmutableObjectLocatorExpectation:
        bootstrap = uuid4() if bootstrap_id is None else bootstrap_id
        fields: dict[str, object] = {
            "campaign_id": sealed.campaign_id,
            "release_sha": sealed.release_sha,
            "sealed_release_binding_sha256": sealed.binding_sha256,
            "source_site": "webapp_fi",
            "destination_site": "webapp_ir",
            "bootstrap_id": bootstrap,
            "locator_id": uuid4() if locator_id is None else locator_id,
            "locator_nonce": "abcdefghijklmnopqrstuv",
            "issued_at": self.now if issued_at is None else issued_at,
            "object_key": (
                builder.expected_wa_ir_bootstrap_object_key(
                    campaign_id=sealed.campaign_id,
                    release_sha=sealed.release_sha,
                    sealed_release_binding_sha256=sealed.binding_sha256,
                    bootstrap_id=bootstrap,
                )
                if object_key is None
                else object_key
            ),
            "version_id": version_id,
            "ciphertext_sha256": "f" * 64,
            "ciphertext_bytes": 12_288,
            "plaintext_sha256": "1" * 64,
            "plaintext_bytes": 8_192,
            "encryption": "age-v1",
            "immutability": "versioned-create-only-readback-v1",
            "age_recipient": recipient.recipient,
        }
        fields.update(changes)
        return builder.WaIrBootstrapImmutableObjectLocatorExpectation(**fields)

    def _locator(
        self,
        *,
        sealed: builder.SealedWaIrBootstrapExactReleaseBinding,
        recipient: builder.VerifiedWaIrBootstrapFreshAgeRecipient,
        **changes: object,
    ) -> builder.VerifiedWaIrBootstrapImmutableObjectLocatorExpectation:
        return builder.verify_wa_ir_bootstrap_immutable_locator_expectation(
            self._raw_locator(sealed=sealed, recipient=recipient, **changes)
        )

    def _prepared(
        self,
        *,
        config: builder.PhysicalWaIrBootstrapBundleBuilderConfig | None = None,
        sealed: builder.SealedWaIrBootstrapExactReleaseBinding | None = None,
        recipient: builder.VerifiedWaIrBootstrapFreshAgeRecipient | None = None,
        locator: builder.VerifiedWaIrBootstrapImmutableObjectLocatorExpectation | None = None,
    ) -> tuple[
        builder.PreparedPhysicalWaIrBootstrapDescriptor,
        builder.PhysicalWaIrBootstrapBundleBuilderConfig,
        builder.SealedWaIrBootstrapExactReleaseBinding,
        builder.VerifiedWaIrBootstrapFreshAgeRecipient,
        builder.VerifiedWaIrBootstrapImmutableObjectLocatorExpectation,
    ]:
        bound_config = self._config() if config is None else config
        bound_sealed = self._sealed_release() if sealed is None else sealed
        bound_recipient = self._recipient() if recipient is None else recipient
        bound_locator = (
            self._locator(sealed=bound_sealed, recipient=bound_recipient)
            if locator is None
            else locator
        )
        descriptor = builder.prepare_fresh_wa_ir_bootstrap_descriptor(
            config=bound_config,
            sealed_release=bound_sealed,
            fresh_recipient=bound_recipient,
            locator_expectation=bound_locator,
            now=self.now,
        )
        return descriptor, bound_config, bound_sealed, bound_recipient, bound_locator

    def _assert_refusal(self, code: str, callback: object) -> None:
        with self.assertRaises(builder.PhysicalWaIrBootstrapBundleBuilderError) as raised:
            callback()  # type: ignore[operator]
        self.assertEqual(raised.exception.code, code)

    def test_prepares_and_reviews_a_redacted_local_only_descriptor(self) -> None:
        descriptor, config, sealed, recipient, locator = self._prepared()

        review = builder.review_fresh_wa_ir_bootstrap_descriptor(
            descriptor,
            config=config,
            sealed_release=sealed,
            fresh_recipient=recipient,
            locator_expectation=locator,
            now=self.now,
        )

        self.assertEqual(review["schema"], builder.PHYSICAL_WA_IR_BOOTSTRAP_DESCRIPTOR_SCHEMA)
        self.assertEqual(review["status"], "prepared-local-only")
        self.assertFalse(review["publish_authorized"])
        self.assertFalse(review["execution_authorized"])
        self.assertEqual(review["direct_fi_to_ir_control"], "forbidden")
        self.assertEqual(review["campaign_id"], _CAMPAIGN)
        self.assertEqual(review["release_sha"], _RELEASE_SHA)
        self.assertEqual(review["age_recipient"], _AGE_RECIPIENT)
        self.assertEqual(review["object"]["object_key"], locator.object_key)
        self.assertEqual(review["object"]["version_id"], locator.version_id)
        self.assertEqual(
            review["descriptor_sha256"],
            hashlib.sha256(
                json.dumps(
                    {key: value for key, value in review.items() if key != "descriptor_sha256"},
                    allow_nan=False,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("ascii")
            ).hexdigest(),
        )
        rendered = descriptor.canonical_descriptor.decode("ascii")
        for forbidden in ("endpoint", "bucket", "://", "secret", "credential", "private", "password"):
            self.assertNotIn(forbidden, rendered.lower())

        freshness_directory = self.state_root / "physical-wa-ir-bootstrap-bundle-builder"
        markers = sorted(freshness_directory.iterdir())
        self.assertEqual(len(markers), 5)
        for marker in markers:
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            self.assertEqual(marker.stat().st_uid, 0)
            marker_body = marker.read_text(encoding="ascii")
            self.assertTrue(marker_body.startswith("gold-trade-physical-wa-ir-bootstrap-freshness-marker-v1:"))
            self.assertNotIn(_AGE_RECIPIENT, marker_body)

    def test_default_off_refuses_before_creating_reuse_state(self) -> None:
        sealed = self._sealed_release()
        recipient = self._recipient()
        locator = self._locator(sealed=sealed, recipient=recipient)
        config = self._config(enabled=False)

        self._assert_refusal(
            "WA_IR_BOOTSTRAP_BUILDER_DISABLED",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=config,
                sealed_release=sealed,
                fresh_recipient=recipient,
                locator_expectation=locator,
                now=self.now,
            ),
        )
        self.assertFalse((self.state_root / "physical-wa-ir-bootstrap-bundle-builder").exists())

    def test_config_is_root_private_and_default_off(self) -> None:
        self.assertFalse(builder.PHYSICAL_WA_IR_BOOTSTRAP_BUNDLE_BUILDER_DEFAULT_ENABLED)
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_STATE_ROOT_UNSAFE",
            lambda: builder.validate_physical_wa_ir_bootstrap_bundle_builder_config(
                builder.PhysicalWaIrBootstrapBundleBuilderConfig()
            ),
        )
        self.state_root.chmod(0o755)
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_STATE_ROOT_UNSAFE",
            lambda: builder.validate_physical_wa_ir_bootstrap_bundle_builder_config(self._config()),
        )

    def test_raw_or_missing_capabilities_are_refused(self) -> None:
        sealed = self._sealed_release()
        recipient = self._recipient()
        locator = self._locator(sealed=sealed, recipient=recipient)
        raw_release = builder.WaIrBootstrapExactReleaseBinding(
            campaign_id=sealed.campaign_id,
            release_sha=sealed.release_sha,
            control_release_sha=sealed.control_release_sha,
            release_bundle_sha256=sealed.release_bundle_sha256,
            image_set_sha256=sealed.image_set_sha256,
            release_provenance_sha256=sealed.release_provenance_sha256,
            source_site="webapp_fi",
            destination_site="webapp_ir",
            seal_id=sealed.seal_id,
            sealed_at=sealed.sealed_at,
        )
        raw_recipient = builder.WaIrBootstrapFreshAgeRecipient(
            campaign_id=recipient.campaign_id,
            recipient=recipient.recipient,
            recipient_public_sha256=recipient.recipient_public_sha256,
            generation_id=recipient.generation_id,
            issued_at=recipient.issued_at,
        )
        raw_locator = self._raw_locator(sealed=sealed, recipient=recipient)

        self._assert_refusal(
            "WA_IR_BOOTSTRAP_RELEASE_SEAL_REQUIRED",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(),
                sealed_release=raw_release,  # type: ignore[arg-type]
                fresh_recipient=recipient,
                locator_expectation=locator,
                now=self.now,
            ),
        )
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_FRESH_RECIPIENT_REQUIRED",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(),
                sealed_release=sealed,
                fresh_recipient=raw_recipient,  # type: ignore[arg-type]
                locator_expectation=locator,
                now=self.now,
            ),
        )
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_REQUIRED",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(),
                sealed_release=sealed,
                fresh_recipient=recipient,
                locator_expectation=raw_locator,  # type: ignore[arg-type]
                now=self.now,
            ),
        )
        self.assertFalse((self.state_root / "physical-wa-ir-bootstrap-bundle-builder").exists())

    def test_stale_seal_recipient_and_locator_are_refused(self) -> None:
        stale = self.now - timedelta(seconds=181)
        current_sealed = self._sealed_release()
        current_recipient = self._recipient()
        current_locator = self._locator(sealed=current_sealed, recipient=current_recipient)
        stale_sealed = self._sealed_release(sealed_at=stale)
        stale_recipient = self._recipient(issued_at=stale)
        stale_locator = self._locator(
            sealed=current_sealed,
            recipient=current_recipient,
            issued_at=stale,
        )

        self._assert_refusal(
            "WA_IR_BOOTSTRAP_RELEASE_SEAL_STALE",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(),
                sealed_release=stale_sealed,
                fresh_recipient=current_recipient,
                locator_expectation=current_locator,
                now=self.now,
            ),
        )
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_FRESH_RECIPIENT_STALE",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(),
                sealed_release=current_sealed,
                fresh_recipient=stale_recipient,
                locator_expectation=current_locator,
                now=self.now,
            ),
        )
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_IMMUTABLE_LOCATOR_STALE",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(),
                sealed_release=current_sealed,
                fresh_recipient=current_recipient,
                locator_expectation=stale_locator,
                now=self.now,
            ),
        )
        self.assertFalse((self.state_root / "physical-wa-ir-bootstrap-bundle-builder").exists())

    def test_private_or_historic_recipient_is_refused(self) -> None:
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_RECIPIENT_INVALID",
            lambda: builder.verify_wa_ir_bootstrap_fresh_age_recipient(
                builder.WaIrBootstrapFreshAgeRecipient(
                    campaign_id=_CAMPAIGN,
                    recipient="AGE-SECRET-KEY-1THIS-IS-NOT-AN-IDENTITY",
                    recipient_public_sha256=_sha256("AGE-SECRET-KEY-1THIS-IS-NOT-AN-IDENTITY"),
                    generation_id=uuid4(),
                    issued_at=self.now,
                )
            ),
        )
        sealed = self._sealed_release()
        recipient = self._recipient()
        locator = self._locator(sealed=sealed, recipient=recipient)
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_HISTORIC_RECIPIENT_FORBIDDEN",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(
                    denied_historic_recipient_public_sha256s=(recipient.recipient_public_sha256,)
                ),
                sealed_release=sealed,
                fresh_recipient=recipient,
                locator_expectation=locator,
                now=self.now,
            ),
        )
        self.assertFalse((self.state_root / "physical-wa-ir-bootstrap-bundle-builder").exists())

    def test_historic_campaign_or_bootstrap_reuse_is_refused_durably(self) -> None:
        descriptor, config, sealed, recipient, locator = self._prepared()
        del descriptor
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_HISTORIC_REUSE_REJECTED",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=config,
                sealed_release=sealed,
                fresh_recipient=recipient,
                locator_expectation=locator,
                now=self.now,
            ),
        )

        fresh_recipient = self._recipient(recipient=_ALTERNATE_AGE_RECIPIENT)
        fresh_locator = self._locator(sealed=sealed, recipient=fresh_recipient)
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_HISTORIC_REUSE_REJECTED",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=config,
                sealed_release=sealed,
                fresh_recipient=fresh_recipient,
                locator_expectation=fresh_locator,
                now=self.now,
            ),
        )

    def test_bound_inputs_and_locator_coordinates_must_be_exact(self) -> None:
        sealed = self._sealed_release()
        recipient = self._recipient()
        alternate = self._recipient(recipient=_ALTERNATE_AGE_RECIPIENT)
        mismatched_locator = self._locator(sealed=sealed, recipient=alternate)
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_INPUT_BINDING_MISMATCH",
            lambda: builder.prepare_fresh_wa_ir_bootstrap_descriptor(
                config=self._config(),
                sealed_release=sealed,
                fresh_recipient=recipient,
                locator_expectation=mismatched_locator,
                now=self.now,
            ),
        )

        raw = self._raw_locator(sealed=sealed, recipient=recipient)
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_LOCATOR_INVALID",
            lambda: builder.verify_wa_ir_bootstrap_immutable_locator_expectation(
                replace(raw, version_id="latest")
            ),
        )
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_LOCATOR_INVALID",
            lambda: builder.verify_wa_ir_bootstrap_immutable_locator_expectation(
                replace(raw, version_id="opaque/latest")
            ),
        )
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_LOCATOR_INVALID",
            lambda: builder.verify_wa_ir_bootstrap_immutable_locator_expectation(
                replace(raw, object_key="physical-wa-ir-bootstrap/*/wildcard")
            ),
        )
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_OBJECT_KEY_INVALID",
            lambda: builder.expected_wa_ir_bootstrap_object_key(
                campaign_id="bad*campaign",
                release_sha=_RELEASE_SHA,
                sealed_release_binding_sha256=sealed.binding_sha256,
                bootstrap_id=uuid4(),
            ),
        )

    def test_tampering_and_review_do_not_bypass_or_consume_new_markers(self) -> None:
        descriptor, config, sealed, recipient, locator = self._prepared()
        freshness_directory = self.state_root / "physical-wa-ir-bootstrap-bundle-builder"
        marker_count = len(list(freshness_directory.iterdir()))
        review = builder.review_fresh_wa_ir_bootstrap_descriptor(
            descriptor,
            config=config,
            sealed_release=sealed,
            fresh_recipient=recipient,
            locator_expectation=locator,
            now=self.now,
        )
        self.assertEqual(review["descriptor_sha256"], descriptor.descriptor_sha256)
        self.assertEqual(len(list(freshness_directory.iterdir())), marker_count)

        object.__setattr__(descriptor, "canonical_descriptor", b'{"credential":"never"}\n')
        self._assert_refusal(
            "WA_IR_BOOTSTRAP_PREPARED_DESCRIPTOR_TAMPERED",
            lambda: builder.review_fresh_wa_ir_bootstrap_descriptor(
                descriptor,
                config=config,
                sealed_release=sealed,
                fresh_recipient=recipient,
                locator_expectation=locator,
                now=self.now,
            ),
        )
        self.assertEqual(len(list(freshness_directory.iterdir())), marker_count)

    def test_local_only_source_has_no_transport_or_execution_dependency(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(MODULE_PATH))
        imported_roots: set[str] = set()
        call_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    call_names.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    call_names.add(node.func.attr)
        self.assertTrue(imported_roots.issubset({
            "__future__", "dataclasses", "datetime", "hashlib", "json", "os",
            "pathlib", "re", "stat", "typing", "uuid", "core",
        }))
        self.assertFalse(imported_roots & {
            "boto3", "botocore", "paramiko", "requests", "socket", "subprocess", "urllib",
        })
        self.assertFalse(call_names & {
            "connect", "get_object", "list_objects", "put_object", "run", "Popen", "system",
        })


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
