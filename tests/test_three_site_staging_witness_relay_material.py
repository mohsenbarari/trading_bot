from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from core.human_approval_issuer import (
    authenticate_and_issue_session,
    create_enrollment,
    totp_code,
)
from core.human_approval import HumanApprovalError, RELAY_RECEIPT_SCHEMA
from scripts import install_three_site_staging_witness_relay_material as installer
from scripts.build_three_site_staging_witness_relay_material import (
    ALLOWED_ENV_CHANGES,
    COMPOSE_NAME,
    ENV_NAME,
    FINAL_FILE_MODES,
    MANIFEST_NAME,
    POLICY_NAME,
    PREPARED_FILE_MODES,
    PREPARED_MANIFEST_NAME,
    REQUIRED_MATRIX_ACTIONS,
    SESSION_NAME,
    WitnessRelayMaterialError,
    _manifest_bytes,
    _publish_new_bundle,
    derive_prepared_revision,
    finalize_revision,
    read_exact_material_file,
    validate_prepared_campaign,
    verify_final_structure,
    verify_prepared_structure,
)
from scripts.install_three_site_staging_witness_relay_material import (
    InertRelayInstallError,
    install_inert_bundle,
)
from scripts.render_three_site_staging_role_compose import (
    canonical_role_env_bytes,
    parse_env_values,
    referenced_environment_names,
    render_role_compose,
)
from tests import test_three_site_staging_campaign_bundle as campaign_fixtures


REVISION_ID = "relay-11111111-r001"
SESSION_PATH = (
    "/var/lib/trading-bot/human-approvals/"
    "staging-session-relay-11111111-r001.json"
)
POLICY_PATH = (
    "/var/lib/trading-bot/human-approvals/"
    "staging-policy-relay-11111111-r001.json"
)
RELAY_SECRET = "relay-secret-that-is-new-and-long-enough-0000000001"


class ThreeSiteStagingWitnessRelayMaterialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        campaign_fixtures.ThreeSiteStagingCampaignBundleTests.setUpClass()
        cls.fixture = campaign_fixtures.ThreeSiteStagingCampaignBundleTests(
            "test_four_role_campaign_proves_cross_host_secret_contract"
        )
        cls.canonical = cls.fixture.canonical
        cls.inventory = cls.fixture.inventory
        cls.policy = cls.fixture.policy
        cls.approval = cls.fixture.approval
        cls.bundles = cls.fixture._bundles()
        cls.base_compose, cls.base_env = cls.bundles["witness"]

    def _derive(self, *, policy=None):  # noqa: ANN001
        return derive_prepared_revision(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            inventory=self.inventory,
            approval_policy=policy or self.policy,
            revision_id=REVISION_ID,
            session_file=SESSION_PATH,
            policy_file=POLICY_PATH,
            relay_key_id="relay-orchestrator-r001",
            relay_secret=RELAY_SECRET,
            created_at=datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc),
        )

    def _prepared_directory(
        self,
        parent: Path,
        *,
        policy=None,  # noqa: ANN001
    ) -> tuple[Path, bytes, bytes, dict]:
        compose, env, manifest = self._derive(policy=policy)
        directory = parent / "prepared"
        _publish_new_bundle(
            directory,
            {
                COMPOSE_NAME: (compose, 0o640),
                ENV_NAME: (env, 0o600),
                MANIFEST_NAME: (_manifest_bytes(manifest), 0o600),
            },
        )
        return directory, compose, env, manifest

    def test_prepared_revision_changes_exactly_six_fields_and_validates_without_attestation(
        self,
    ) -> None:
        compose, env, manifest = self._derive()
        base_values = parse_env_values(self.base_env.decode())
        revised_values = parse_env_values(env.decode())
        changed = {
            name
            for name in set(base_values) | set(revised_values)
            if base_values.get(name) != revised_values.get(name)
        }

        self.assertEqual(changed, ALLOWED_ENV_CHANGES)
        self.assertEqual(compose, self.base_compose)
        self.assertEqual(
            revised_values["STAGING_SOURCE_ROOT"],
            f"/srv/trading-bot-three-site/releases/{self.inventory['release_sha']}",
        )
        self.assertEqual(
            revised_values["STAGING_HUMAN_APPROVAL_RELAY_SESSION_FILE"],
            SESSION_PATH,
        )
        self.assertEqual(
            revised_values["STAGING_HUMAN_APPROVAL_RELAY_POLICY_FILE"],
            POLICY_PATH,
        )
        self.assertNotIn(RELAY_SECRET, json.dumps(manifest, sort_keys=True))

        result = validate_prepared_campaign(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            prepared_compose_bytes=compose,
            prepared_env_bytes=env,
            manifest=manifest,
            inventory=self.inventory,
            approval=self.approval,
            approval_policy=self.policy,
            other_bundles={
                role: self.bundles[role]
                for role in ("bot-fi", "webapp-fi", "webapp-ir")
            },
        )
        self.assertEqual(
            result["status"],
            "prepared-relay-material-verified-not-image-attested",
        )
        self.assertFalse(result["file_attestation"])
        self.assertFalse(result["image_attestation"])
        self.assertFalse(result["activation"])

    def test_prepared_validation_rejects_any_seventh_change_or_attestation_claim(
        self,
    ) -> None:
        compose, env, manifest = self._derive()
        role_payload = render_role_compose(
            self.canonical,
            role="witness",
            project_namespace=self.inventory.get(
                "compose_project_namespace", "trading-bot-three-site-staging"
            ),
        )
        values = parse_env_values(env.decode())
        values["STAGING_CGROUP_PARENT"] = "unexpected-seventh-change"
        tampered = canonical_role_env_bytes(
            values,
            required_names=referenced_environment_names(role_payload),
        )
        tampered_manifest = json.loads(json.dumps(manifest))
        tampered_manifest["prepared"]["role_env_sha256"] = __import__(
            "hashlib"
        ).sha256(tampered).hexdigest()
        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "exact six-field"
        ):
            verify_prepared_structure(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                prepared_compose_bytes=compose,
                prepared_env_bytes=tampered,
                manifest=tampered_manifest,
                inventory=self.inventory,
                approval_policy=self.policy,
            )

        manifest["attestations"]["images"] = True
        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "overstates"
        ):
            verify_prepared_structure(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                prepared_compose_bytes=compose,
                prepared_env_bytes=env,
                manifest=manifest,
                inventory=self.inventory,
                approval_policy=self.policy,
            )

    def test_prepared_validation_never_uses_an_ambient_witness_key(self) -> None:
        compose, env, manifest = self._derive()
        with (
            mock.patch.dict(
                "os.environ",
                {"WRITER_WITNESS_PUBLIC_KEY": "ambient-key-must-not-be-used"},
            ),
            self.assertRaisesRegex(
                WitnessRelayMaterialError, "explicit campaign-bound Witness key"
            ),
        ):
            validate_prepared_campaign(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                prepared_compose_bytes=compose,
                prepared_env_bytes=env,
                manifest=manifest,
                inventory=self.inventory,
                approval={"schema": RELAY_RECEIPT_SCHEMA},
                approval_policy=self.policy,
                other_bundles={
                    role: self.bundles[role]
                    for role in ("bot-fi", "webapp-fi", "webapp-ir")
                },
            )

    def test_derivation_requires_disabled_baseline_and_six_real_changes(self) -> None:
        values = parse_env_values(self.base_env.decode())
        role_payload = render_role_compose(
            self.canonical,
            role="witness",
            project_namespace=self.inventory.get(
                "compose_project_namespace", "trading-bot-three-site-staging"
            ),
        )
        required = referenced_environment_names(role_payload)
        values["STAGING_HUMAN_APPROVAL_RELAY_ENABLED"] = "true"
        unsafe_base = canonical_role_env_bytes(values, required_names=required)
        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "disabled relay baseline"
        ):
            derive_prepared_revision(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=unsafe_base,
                inventory=self.inventory,
                approval_policy=self.policy,
                revision_id=REVISION_ID,
                session_file=SESSION_PATH,
                policy_file=POLICY_PATH,
                relay_key_id="relay-orchestrator-r001",
                relay_secret=RELAY_SECRET,
            )

        values = parse_env_values(self.base_env.decode())
        values["STAGING_SOURCE_ROOT"] = (
            f"/srv/trading-bot-three-site/releases/{self.inventory['release_sha']}"
        )
        already_immutable = canonical_role_env_bytes(
            values, required_names=required
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "six changes required"
        ):
            derive_prepared_revision(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=already_immutable,
                inventory=self.inventory,
                approval_policy=self.policy,
                revision_id=REVISION_ID,
                session_file=SESSION_PATH,
                policy_file=POLICY_PATH,
                relay_key_id="relay-orchestrator-r001",
                relay_secret=RELAY_SECRET,
            )

        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "must contain the relay revision ID"
        ):
            derive_prepared_revision(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                inventory=self.inventory,
                approval_policy=self.policy,
                revision_id=REVISION_ID,
                session_file=(
                    "/var/lib/trading-bot/human-approvals/"
                    "staging-session-different-revision.json"
                ),
                policy_file=POLICY_PATH,
                relay_key_id="relay-orchestrator-r001",
                relay_secret=RELAY_SECRET,
            )

    def test_final_bundle_requires_real_matching_fresh_session_and_policy(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment = create_enrollment(
            operator="person-1",
            password="test approval passphrase value",
            now=now,
            scrypt_n=2**14,
        )
        compose, env, prepared = self._derive(policy=enrollment.policy_payload)
        prepared_bytes = _manifest_bytes(prepared)
        policy_bytes = (
            json.dumps(enrollment.policy_payload, sort_keys=True, indent=2).encode()
            + b"\n"
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "real staging approval session"
        ):
            finalize_revision(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                prepared_compose_bytes=compose,
                prepared_env_bytes=env,
                prepared_manifest=prepared,
                prepared_manifest_bytes=prepared_bytes,
                inventory=self.inventory,
                policy=enrollment.policy_payload,
                policy_bytes=policy_bytes,
                session={},
                session_bytes=b"{}\n",
                now=now,
            )

        session, _state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=enrollment.state_payload,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password="test approval passphrase value",
            totp=totp_code(enrollment.totp_secret, at=now)[1],
            recovery_code=None,
            release_sha=self.inventory["release_sha"],
            allowed_actions=list(REQUIRED_MATRIX_ACTIONS),
            ttl_seconds=48 * 60 * 60,
            now=now,
        )
        session_bytes = json.dumps(session, sort_keys=True, indent=2).encode() + b"\n"
        final = finalize_revision(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            prepared_compose_bytes=compose,
            prepared_env_bytes=env,
            prepared_manifest=prepared,
            prepared_manifest_bytes=prepared_bytes,
            inventory=self.inventory,
            policy=enrollment.policy_payload,
            policy_bytes=policy_bytes,
            session=session,
            session_bytes=session_bytes,
            created_at=now + timedelta(seconds=1),
            now=now + timedelta(seconds=1),
        )
        result = verify_final_structure(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            final_compose_bytes=compose,
            final_env_bytes=env,
            prepared_manifest=prepared,
            prepared_manifest_bytes=prepared_bytes,
            final_manifest=final,
            inventory=self.inventory,
            policy=enrollment.policy_payload,
            policy_bytes=policy_bytes,
            session=session,
            session_bytes=session_bytes,
            now=now + timedelta(seconds=1),
        )
        self.assertEqual(result["stage"], "final")
        self.assertFalse(result["file_attestation"])
        self.assertFalse(result["image_attestation"])
        self.assertIn("expires_at", final["final"])
        self.assertNotIn("signature", final["final"])
        with self.assertRaisesRegex(HumanApprovalError, "expired"):
            verify_final_structure(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                final_compose_bytes=compose,
                final_env_bytes=env,
                prepared_manifest=prepared,
                prepared_manifest_bytes=prepared_bytes,
                final_manifest=final,
                inventory=self.inventory,
                policy=enrollment.policy_payload,
                policy_bytes=policy_bytes,
                session=session,
                session_bytes=session_bytes,
                now=now + timedelta(hours=49),
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            final_directory = root / "final"
            _publish_new_bundle(
                final_directory,
                {
                    COMPOSE_NAME: (compose, 0o640),
                    ENV_NAME: (env, 0o600),
                    SESSION_NAME: (session_bytes, 0o600),
                    POLICY_NAME: (policy_bytes, 0o600),
                    PREPARED_MANIFEST_NAME: (prepared_bytes, 0o600),
                    MANIFEST_NAME: (_manifest_bytes(final), 0o600),
                },
            )
            inert_root = root / "material-revisions"
            inert_root.mkdir(mode=0o700)
            installed = install_inert_bundle(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                inventory=self.inventory,
                approval_policy=enrollment.policy_payload,
                bundle_directory=final_directory,
                inert_root=inert_root,
            )
            installed_files = {
                path.name for path in Path(installed["destination"]).iterdir()
            }
            self.assertEqual(installed_files, set(FINAL_FILE_MODES))
            self.assertEqual(
                (Path(installed["destination"]) / SESSION_NAME).read_bytes(),
                session_bytes,
            )
            self.assertEqual(installed["stage"], "final")
            self.assertFalse(installed["activation"])

    def test_inert_install_is_create_exclusive_and_never_touches_current_or_services(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            bundle, _compose, _env, _manifest = self._prepared_directory(root)
            inert_root = root / "material-revisions"
            inert_root.mkdir(mode=0o700)

            result = install_inert_bundle(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                inventory=self.inventory,
                approval_policy=self.policy,
                bundle_directory=bundle,
                inert_root=inert_root,
            )
            destination = inert_root / REVISION_ID
            before = {
                path.name: (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in destination.iterdir()
            }
            self.assertEqual(result["status"], "installed-inert-not-activated")
            self.assertEqual(set(before), set(PREPARED_FILE_MODES))
            self.assertFalse(result["current_changed"])
            self.assertFalse(result["service_changed"])
            self.assertFalse(result["activation"])

            with self.assertRaisesRegex(
                InertRelayInstallError, "already exists"
            ):
                install_inert_bundle(
                    canonical_compose=self.canonical,
                    base_compose_bytes=self.base_compose,
                    base_env_bytes=self.base_env,
                    inventory=self.inventory,
                    approval_policy=self.policy,
                    bundle_directory=bundle,
                    inert_root=inert_root,
                )
            after = {
                path.name: (path.read_bytes(), path.stat().st_mode & 0o777)
                for path in destination.iterdir()
            }
            self.assertEqual(after, before)

            current = root / "current"
            current.mkdir(mode=0o700)
            with self.assertRaisesRegex(
                InertRelayInstallError, "current"
            ):
                install_inert_bundle(
                    canonical_compose=self.canonical,
                    base_compose_bytes=self.base_compose,
                    base_env_bytes=self.base_env,
                    inventory=self.inventory,
                    approval_policy=self.policy,
                    bundle_directory=bundle,
                    inert_root=current,
                )

    def test_inert_install_cleans_its_new_directory_after_partial_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            bundle, _compose, _env, _manifest = self._prepared_directory(root)
            inert_root = root / "material-revisions"
            inert_root.mkdir(mode=0o700)
            real_write = installer.write_secure_new_bytes
            calls = 0

            def fail_second(*args, **kwargs):  # noqa: ANN002, ANN003
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected write failure")
                return real_write(*args, **kwargs)

            with (
                mock.patch.object(
                    installer, "write_secure_new_bytes", side_effect=fail_second
                ),
                self.assertRaisesRegex(OSError, "injected"),
            ):
                install_inert_bundle(
                    canonical_compose=self.canonical,
                    base_compose_bytes=self.base_compose,
                    base_env_bytes=self.base_env,
                    inventory=self.inventory,
                    approval_policy=self.policy,
                    bundle_directory=bundle,
                    inert_root=inert_root,
                )
            self.assertFalse((inert_root / REVISION_ID).exists())
            self.assertEqual(list(inert_root.iterdir()), [])

    def test_inert_installer_rejects_live_system_roots_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            bundle, _compose, _env, _manifest = self._prepared_directory(root)
            with self.assertRaisesRegex(InertRelayInstallError, "forbidden"):
                install_inert_bundle(
                    canonical_compose=self.canonical,
                    base_compose_bytes=self.base_compose,
                    base_env_bytes=self.base_env,
                    inventory=self.inventory,
                    approval_policy=self.policy,
                    bundle_directory=bundle,
                    inert_root=Path("/etc"),
                )

    def test_material_reader_rejects_broad_modes_symlinks_and_hardlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            source = root / "material.env"
            source.write_text("KEY=value\n", encoding="utf-8")
            source.chmod(0o644)
            with self.assertRaisesRegex(
                WitnessRelayMaterialError, "mode-0600"
            ):
                read_exact_material_file(
                    source, expected_mode=0o600, label="test material"
                )

            source.chmod(0o600)
            alias = root / "material-link.env"
            alias.symlink_to(source)
            with self.assertRaisesRegex(
                WitnessRelayMaterialError, "securely open"
            ):
                read_exact_material_file(
                    alias, expected_mode=0o600, label="test material"
                )

            alias.unlink()
            hardlink = root / "material-hardlink.env"
            hardlink.hardlink_to(source)
            with self.assertRaisesRegex(
                WitnessRelayMaterialError, "single-link"
            ):
                read_exact_material_file(
                    source, expected_mode=0o600, label="test material"
                )


if __name__ == "__main__":
    unittest.main()
