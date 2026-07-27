from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

from core.human_approval import (
    HumanApprovalError,
    RELAY_RECEIPT_SCHEMA,
    staging_session_scope_sha256,
)
from core.human_approval_issuer import (
    DEFAULT_ACTIONS,
    authenticate_and_issue,
    authenticate_and_issue_session,
    create_enrollment,
    totp_code,
)
from scripts import build_three_site_staging_witness_relay_material as builder
from scripts import install_three_site_staging_witness_relay_material as installer
from scripts import rotate_three_site_staging_witness_relay_session as rotation
from scripts.build_three_site_staging_witness_relay_material import (
    ACTIVE_DIRECTORY_NAME,
    ALLOWED_ENV_CHANGES,
    ARCHIVE_DIRECTORY_NAME,
    COMPOSE_NAME,
    ENV_NAME,
    FINAL_FILE_MODES,
    JOURNAL_DIRECTORY_NAME,
    MANIFEST_NAME,
    POLICY_NAME,
    PREPARED_FILE_MODES,
    PREPARED_MANIFEST_NAME,
    REQUIRED_MATRIX_ACTIONS,
    SESSION_NAME,
    WitnessRelayMaterialError,
    _manifest_bytes,
    _publish_new_bundle,
    build_campaign_validation_core,
    derive_prepared_revision,
    finalize_revision,
    read_exact_material_file,
    relay_material_approval_subject,
    validate_prepared_campaign,
    verify_final_structure,
    verify_prepared_structure,
)
from scripts.install_three_site_staging_witness_relay_material import (
    InertRelayInstallError,
    install_inert_bundle,
)
from scripts.render_three_site_staging_role_compose import parse_env_values
from scripts.rotate_three_site_staging_witness_relay_session import (
    CRASH_POINTS,
    InjectedRotationCrash,
    WitnessRelayRotationError,
    rotate_witness_relay_session,
)
from tests import test_three_site_staging_campaign_bundle as campaign_fixtures
from tests.test_three_site_staging_signed_inventory import (
    _inventory_approval_subject,
)


REVISION_ID = "relay-11111111-r001"
RELAY_SECRET = "relay-secret-that-is-new-and-long-enough-0000000001"
PASSWORD = "test approval passphrase value"
CONTAINER_ID = "c" * 64


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

    def setUp(self) -> None:
        self.runtime_destination: Path | None = None
        self.docker_inspect = mock.patch.object(
            rotation.subprocess,
            "run",
            side_effect=self._docker_inspect,
        )
        self.docker_run = self.docker_inspect.start()

    def tearDown(self) -> None:
        self.docker_inspect.stop()

    def _docker_inspect(self, *args, **_kwargs):  # noqa: ANN002, ANN003
        if self.runtime_destination is None:
            raise AssertionError("runtime destination was not installed")
        values = parse_env_values(
            (self.runtime_destination / ENV_NAME).read_text(encoding="utf-8")
        )
        payload = [
            {
                "Id": CONTAINER_ID,
                "State": {
                    "Running": True,
                    "Restarting": False,
                    "StartedAt": "2026-07-27T09:00:00.000000000Z",
                },
                "RestartCount": 0,
                "Config": {
                    "Labels": {
                        "com.docker.compose.service": "witness_api",
                        "com.docker.compose.project": (
                            f"{values['STAGING_STORAGE_NAMESPACE']}-witness"
                        ),
                    },
                    "Env": [
                        (
                            "WRITER_WITNESS_RELEASE_SHA="
                            f"{values['STAGING_RELEASE_SHA']}"
                        ),
                        "HUMAN_APPROVAL_RELAY_ENABLED=true",
                        (
                            "HUMAN_APPROVAL_RELAY_SESSION_FILE="
                            "/run/human-approval/session.json"
                        ),
                        (
                            "HUMAN_APPROVAL_RELAY_POLICY_FILE="
                            "/run/human-approval/policy.json"
                        ),
                        (
                            "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID="
                            f"{values['STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_KEY_ID']}"
                        ),
                        (
                            "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET="
                            f"{values['STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET']}"
                        ),
                    ],
                },
                "Mounts": [
                    {
                        "Type": "bind",
                        "Source": str(
                            self.runtime_destination / ACTIVE_DIRECTORY_NAME
                        ),
                        "Destination": "/run/human-approval",
                        "RW": False,
                    }
                ],
            }
        ]
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    def _material_directory(self, root: Path) -> Path:
        return (
            root
            / self.inventory["campaign_id"]
            / self.inventory["deployment_id"]
            / "material-revisions"
            / REVISION_ID
            / ACTIVE_DIRECTORY_NAME
        )

    def _derive(
        self,
        *,
        material_directory: Path,
        policy=None,  # noqa: ANN001
        created_at: datetime | None = None,
    ):
        return derive_prepared_revision(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            inventory=self.inventory,
            approval_policy=policy or self.policy,
            revision_id=REVISION_ID,
            material_directory=str(material_directory),
            relay_key_id="relay-orchestrator-r001",
            relay_secret=RELAY_SECRET,
            created_at=created_at
            or datetime(2026, 7, 27, 9, 0, tzinfo=timezone.utc),
        )

    def _issue_pair(self, *, now: datetime):
        enrollment = create_enrollment(
            operator="person-1",
            password=PASSWORD,
            now=now,
            scrypt_n=2**14,
        )
        inventory_approval, _inventory_state, _inventory_audit = (
            authenticate_and_issue(
                secrets_payload=enrollment.secrets_payload,
                state_payload=enrollment.state_payload,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password=PASSWORD,
                totp=totp_code(enrollment.totp_secret, at=now)[1],
                recovery_code=None,
                action="approve_inventory",
                environment="staging",
                subject=_inventory_approval_subject(self.inventory),
                ttl_seconds=60 * 60,
                now=now,
            )
        )
        old_session, state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=enrollment.state_payload,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password=PASSWORD,
            totp=totp_code(enrollment.totp_secret, at=now)[1],
            recovery_code=None,
            release_sha=self.inventory["release_sha"],
            allowed_actions=list(REQUIRED_MATRIX_ACTIONS),
            ttl_seconds=48 * 60 * 60,
            now=now,
        )
        second_at = now + timedelta(seconds=31)
        new_session, state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=state,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password=PASSWORD,
            totp=totp_code(enrollment.totp_secret, at=second_at)[1],
            recovery_code=None,
            release_sha=self.inventory["release_sha"],
            allowed_actions=list(REQUIRED_MATRIX_ACTIONS),
            ttl_seconds=48 * 60 * 60,
            now=second_at,
        )
        return enrollment, inventory_approval, old_session, new_session, state

    def _prepared_directory(
        self,
        parent: Path,
        *,
        material_directory: Path,
        policy=None,  # noqa: ANN001
    ):
        compose, env, manifest = self._derive(
            material_directory=material_directory,
            policy=policy,
        )
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

    def _material_gate(
        self,
        *,
        enrollment,
        inventory_approval: dict,
        compose: bytes,
        env: bytes,
        prepared: dict,
        now: datetime,
    ):
        other_bundles = {
            role: self.bundles[role]
            for role in ("bot-fi", "webapp-fi", "webapp-ir")
        }
        campaign_result = validate_prepared_campaign(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            prepared_compose_bytes=compose,
            prepared_env_bytes=env,
            manifest=prepared,
            inventory=self.inventory,
            approval=inventory_approval,
            approval_policy=enrollment.policy_payload,
            other_bundles=other_bundles,
        )
        validation_core = build_campaign_validation_core(
            prepared_compose_bytes=compose,
            prepared_env_bytes=env,
            prepared_manifest=prepared,
            inventory=self.inventory,
            inventory_approval=inventory_approval,
            approval_policy=enrollment.policy_payload,
            other_bundles=other_bundles,
            campaign_result=campaign_result,
            witness_relay_public_key=None,
        )
        material_approval, _material_state, _material_audit = (
            authenticate_and_issue(
                secrets_payload=enrollment.secrets_payload,
                state_payload=enrollment.state_payload,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password=PASSWORD,
                totp=totp_code(enrollment.totp_secret, at=now)[1],
                recovery_code=None,
                action=builder.MATERIAL_APPROVAL_ACTION,
                environment="staging",
                subject=relay_material_approval_subject(
                    validation_core,
                    prepared_manifest=prepared,
                    inventory=self.inventory,
                    approval_policy=enrollment.policy_payload,
                ),
                ttl_seconds=60 * 60,
                now=now,
            )
        )
        return other_bundles, material_approval

    def _install_final(
        self,
        root: Path,
        *,
        enrollment,
        inventory_approval: dict,
        old_session: dict,
        now: datetime,
    ):
        material_directory = self._material_directory(root)
        compose, env, prepared = self._derive(
            material_directory=material_directory,
            policy=enrollment.policy_payload,
            created_at=now,
        )
        prepared_bytes = _manifest_bytes(prepared)
        policy_bytes = _manifest_bytes(enrollment.policy_payload)
        session_bytes = _manifest_bytes(old_session)
        other_bundles, material_approval = self._material_gate(
            enrollment=enrollment,
            inventory_approval=inventory_approval,
            compose=compose,
            env=env,
            prepared=prepared,
            now=now,
        )
        final = finalize_revision(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            prepared_compose_bytes=compose,
            prepared_env_bytes=env,
            prepared_manifest=prepared,
            prepared_manifest_bytes=prepared_bytes,
            inventory=self.inventory,
            inventory_approval=inventory_approval,
            other_bundles=other_bundles,
            policy=enrollment.policy_payload,
            policy_bytes=policy_bytes,
            material_approval=material_approval,
            session=old_session,
            session_bytes=session_bytes,
            created_at=now,
            now=now,
        )
        bundle = root / "final"
        _publish_new_bundle(
            bundle,
            {
                COMPOSE_NAME: (compose, 0o640),
                ENV_NAME: (env, 0o600),
                f"{ACTIVE_DIRECTORY_NAME}/{SESSION_NAME}": (
                    session_bytes,
                    0o600,
                ),
                f"{ACTIVE_DIRECTORY_NAME}/{POLICY_NAME}": (
                    policy_bytes,
                    0o600,
                ),
                PREPARED_MANIFEST_NAME: (prepared_bytes, 0o600),
                MANIFEST_NAME: (_manifest_bytes(final), 0o600),
            },
        )
        inert_root = material_directory.parent.parent
        inert_root.mkdir(mode=0o700, parents=True)
        inert_root.chmod(0o700)
        result = install_inert_bundle(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            inventory=self.inventory,
            approval_policy=enrollment.policy_payload,
            bundle_directory=bundle,
            inert_root=inert_root,
        )
        destination = Path(result["destination"])
        self.runtime_destination = destination
        return destination, final, session_bytes, policy_bytes

    def test_prepared_revision_is_exact_five_field_directory_binding(self) -> None:
        material = self._material_directory(Path("/tmp/witness-relay-contract"))
        compose, env, manifest = self._derive(material_directory=material)
        base_values = parse_env_values(self.base_env.decode())
        revised_values = parse_env_values(env.decode())
        changed = {
            name
            for name in set(base_values) | set(revised_values)
            if base_values.get(name) != revised_values.get(name)
        }
        self.assertEqual(changed, ALLOWED_ENV_CHANGES)
        self.assertEqual(len(changed), 5)
        self.assertEqual(compose, self.base_compose)
        self.assertEqual(
            revised_values["STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR"],
            str(material),
        )
        self.assertNotIn("STAGING_HUMAN_APPROVAL_RELAY_SESSION_FILE", revised_values)
        self.assertNotIn("STAGING_HUMAN_APPROVAL_RELAY_POLICY_FILE", revised_values)
        self.assertEqual(
            manifest["required_session_actions"],
            ["failback_fi", "promote_ir", "start_full_matrix"],
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
        self.assertFalse(result["file_attestation"])
        self.assertFalse(result["image_attestation"])

    def test_prepared_revision_rejects_nonbound_path_and_enabled_baseline(self) -> None:
        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "campaign/deployment/revision-bound"
        ):
            self._derive(material_directory=Path("/tmp/unbound/active"))
        material = self._material_directory(Path("/tmp/witness-relay-contract"))
        values = parse_env_values(self.base_env.decode())
        values["STAGING_HUMAN_APPROVAL_RELAY_ENABLED"] = "true"
        required = set(parse_env_values(self.base_env.decode()))
        unsafe_base = builder.canonical_role_env_bytes(
            values,
            required_names=frozenset(required),
        )
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
                material_directory=str(material),
                relay_key_id="relay-orchestrator-r001",
                relay_secret=RELAY_SECRET,
            )

    def test_prepared_campaign_never_uses_ambient_witness_key(self) -> None:
        material = self._material_directory(Path("/tmp/witness-relay-contract"))
        compose, env, manifest = self._derive(material_directory=material)
        arguments = dict(
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
        with (
            mock.patch.dict(
                "os.environ",
                {"WRITER_WITNESS_PUBLIC_KEY": "ambient-key-must-not-be-used"},
            ),
            self.assertRaisesRegex(
                WitnessRelayMaterialError, "explicitly pinned Witness key"
            ),
        ):
            validate_prepared_campaign(**arguments)
        with mock.patch.object(
            builder,
            "verify_campaign_bundle",
            return_value={
                "file_attestation": False,
                "campaign_bundle_sha256": "a" * 64,
            },
        ) as verifier:
            validate_prepared_campaign(
                **arguments,
                witness_relay_public_key="explicit-pinned-witness-key",
            )
        self.assertEqual(
            verifier.call_args.kwargs["witness_relay_public_key"],
            "explicit-pinned-witness-key",
        )

    def test_relay_credential_cannot_reuse_base_witness_credentials(self) -> None:
        base_values = parse_env_values(self.base_env.decode("utf-8"))
        material = self._material_directory(Path("/tmp/witness-relay-isolation"))
        for field, value in (
            ("relay_key_id", base_values["WEBAPP_FI_WITNESS_KEY_ID"]),
            ("relay_secret", base_values["WEBAPP_FI_WITNESS_SECRET"]),
            ("relay_secret", base_values["WITNESS_RUNTIME_DB_PASSWORD"]),
        ):
            with self.subTest(field=field, source=value[:8]), self.assertRaisesRegex(
                WitnessRelayMaterialError,
                "reuses base Witness credential",
            ):
                derive_prepared_revision(
                    canonical_compose=self.canonical,
                    base_compose_bytes=self.base_compose,
                    base_env_bytes=self.base_env,
                    inventory=self.inventory,
                    approval_policy=self.policy,
                    revision_id=REVISION_ID,
                    material_directory=str(material),
                    relay_key_id=(
                        value if field == "relay_key_id" else "relay-orchestrator-r001"
                    ),
                    relay_secret=(
                        value if field == "relay_secret" else RELAY_SECRET
                    ),
                )

        compose, env, manifest = self._derive(material_directory=material)
        forged_values = parse_env_values(env.decode("utf-8"))
        reused_secret = base_values["WEBAPP_FI_WITNESS_SECRET"]
        forged_values[
            "STAGING_HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET"
        ] = reused_secret
        required_names = frozenset(base_values)
        forged_env = builder.canonical_role_env_bytes(
            forged_values,
            required_names=required_names,
        )
        forged_manifest = json.loads(json.dumps(manifest))
        forged_manifest["prepared"]["role_env_sha256"] = builder._sha256(forged_env)
        forged_manifest["bindings"]["relay_secret_sha256"] = builder._sha256(
            reused_secret.encode("utf-8")
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError,
            "reuses base Witness credential",
        ):
            verify_prepared_structure(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                prepared_compose_bytes=compose,
                prepared_env_bytes=forged_env,
                manifest=forged_manifest,
                inventory=self.inventory,
                approval_policy=self.policy,
            )

    def test_relay_material_rejects_dotenv_interpolation(self) -> None:
        material = self._material_directory(Path("/tmp/witness-relay-interpolation"))
        with self.assertRaisesRegex(
            WitnessRelayMaterialError,
            "secret is unsafe",
        ):
            derive_prepared_revision(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                inventory=self.inventory,
                approval_policy=self.policy,
                revision_id=REVISION_ID,
                material_directory=str(material),
                relay_key_id="relay-orchestrator-r001",
                relay_secret=(
                    "${BOT_TOKEN:?this-required-message-makes-the-value-long-enough}"
                ),
            )
        unsafe_path = (
            Path("/tmp/${RELAY_ROOT}")
            / self.inventory["campaign_id"]
            / self.inventory["deployment_id"]
            / "material-revisions"
            / REVISION_ID
            / ACTIVE_DIRECTORY_NAME
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError,
            "absolute normalized path",
        ):
            self._derive(material_directory=unsafe_path)

        compose, env, manifest = self._derive(material_directory=material)
        bot_compose, bot_env = self.bundles["bot-fi"]
        bot_values = parse_env_values(bot_env.decode("utf-8"))
        bot_values["BOT_TOKEN"] = f"${{UNSET:-{RELAY_SECRET}}}"
        bot_env = builder.canonical_role_env_bytes(
            bot_values,
            required_names=frozenset(bot_values),
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError,
            "four-role campaign validation failed",
        ):
            validate_prepared_campaign(
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
                    **{
                        role: self.bundles[role]
                        for role in ("webapp-fi", "webapp-ir")
                    },
                    "bot-fi": (bot_compose, bot_env),
                },
            )

    def test_campaign_validation_rejects_relay_reuse_of_bot_token(self) -> None:
        bot_values = parse_env_values(self.bundles["bot-fi"][1].decode("utf-8"))
        reused_secret = bot_values["BOT_TOKEN"]
        material = self._material_directory(Path("/tmp/witness-relay-cross-role"))
        compose, env, manifest = derive_prepared_revision(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            inventory=self.inventory,
            approval_policy=self.policy,
            revision_id=REVISION_ID,
            material_directory=str(material),
            relay_key_id="relay-orchestrator-r001",
            relay_secret=reused_secret,
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError,
            "four-role campaign validation failed",
        ):
            validate_prepared_campaign(
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

    def test_final_bundle_requires_exact_session_scope_and_exposes_scope_hash(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, _new_session, state = (
            self._issue_pair(now=now)
        )
        material = self._material_directory(Path("/tmp/witness-relay-contract"))
        compose, env, prepared = self._derive(
            material_directory=material,
            policy=enrollment.policy_payload,
            created_at=now,
        )
        prepared_bytes = _manifest_bytes(prepared)
        policy_bytes = _manifest_bytes(enrollment.policy_payload)
        session_bytes = _manifest_bytes(old_session)
        other_bundles, material_approval = self._material_gate(
            enrollment=enrollment,
            inventory_approval=inventory_approval,
            compose=compose,
            env=env,
            prepared=prepared,
            now=now,
        )
        final = finalize_revision(
            canonical_compose=self.canonical,
            base_compose_bytes=self.base_compose,
            base_env_bytes=self.base_env,
            prepared_compose_bytes=compose,
            prepared_env_bytes=env,
            prepared_manifest=prepared,
            prepared_manifest_bytes=prepared_bytes,
            inventory=self.inventory,
            inventory_approval=inventory_approval,
            other_bundles=other_bundles,
            policy=enrollment.policy_payload,
            policy_bytes=policy_bytes,
            material_approval=material_approval,
            session=old_session,
            session_bytes=session_bytes,
            created_at=now,
            now=now,
        )
        expected_scope = staging_session_scope_sha256(
            release_sha=self.inventory["release_sha"],
            allowed_actions=list(REQUIRED_MATRIX_ACTIONS),
        )
        self.assertEqual(final["final"]["session_scope_sha256"], expected_scope)
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
            session=old_session,
            session_bytes=session_bytes,
            now=now,
        )
        self.assertEqual(result["stage"], "final")
        tampered = json.loads(json.dumps(final))
        tampered["campaign_validation"]["role_bundles"]["bot-fi"][
            "environment_sha256"
        ] = "0" * 64
        tampered["campaign_validation"]["campaign_bundle_sha256"] = (
            builder._sha256(
                json.dumps(
                    tampered["campaign_validation"]["role_bundles"],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError,
            "material approval is invalid",
        ):
            verify_final_structure(
                canonical_compose=self.canonical,
                base_compose_bytes=self.base_compose,
                base_env_bytes=self.base_env,
                final_compose_bytes=compose,
                final_env_bytes=env,
                prepared_manifest=prepared,
                prepared_manifest_bytes=prepared_bytes,
                final_manifest=tampered,
                inventory=self.inventory,
                policy=enrollment.policy_payload,
                policy_bytes=policy_bytes,
                session=old_session,
                session_bytes=session_bytes,
                now=now,
            )

        overbroad_at = now + timedelta(seconds=62)
        overbroad, _state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=state,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password=PASSWORD,
            totp=totp_code(enrollment.totp_secret, at=overbroad_at)[1],
            recovery_code=None,
            release_sha=self.inventory["release_sha"],
            allowed_actions=[item["action"] for item in DEFAULT_ACTIONS],
            ttl_seconds=48 * 60 * 60,
            now=overbroad_at,
        )
        with self.assertRaisesRegex(
            WitnessRelayMaterialError, "exact live matrix scope"
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
                inventory_approval=inventory_approval,
                other_bundles=other_bundles,
                policy=enrollment.policy_payload,
                policy_bytes=policy_bytes,
                material_approval=material_approval,
                session=overbroad,
                session_bytes=_manifest_bytes(overbroad),
                now=overbroad_at,
            )

    def test_validate_prepared_cli_writes_a_directly_issuable_subject(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, _old, _new, _state = self._issue_pair(
            now=now
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            material = self._material_directory(root)
            prepared, _compose, _env, _manifest = self._prepared_directory(
                root,
                material_directory=material,
                policy=enrollment.policy_payload,
            )
            canonical_path = root / "canonical.yml"
            canonical_path.write_text(
                yaml.safe_dump(self.canonical, sort_keys=False),
                encoding="utf-8",
            )
            base_compose_path = root / "base.compose.yml"
            base_compose_path.write_bytes(self.base_compose)
            base_compose_path.chmod(0o640)
            base_env_path = root / "base.env"
            base_env_path.write_bytes(self.base_env)
            base_env_path.chmod(0o600)
            inventory_path = root / "inventory.json"
            inventory_path.write_bytes(_manifest_bytes(self.inventory))
            inventory_path.chmod(0o600)
            approval_path = root / "inventory-approval.json"
            approval_path.write_bytes(_manifest_bytes(inventory_approval))
            approval_path.chmod(0o600)
            policy_path = root / "policy.json"
            policy_path.write_bytes(_manifest_bytes(enrollment.policy_payload))
            policy_path.chmod(0o600)
            bundle_arguments: list[str] = []
            for role in ("bot-fi", "webapp-fi", "webapp-ir"):
                compose_path = root / f"{role}.compose.yml"
                env_path = root / f"{role}.env"
                compose_path.write_bytes(self.bundles[role][0])
                compose_path.chmod(0o640)
                env_path.write_bytes(self.bundles[role][1])
                env_path.chmod(0o600)
                bundle_arguments.extend(
                    ["--bundle", f"{role}={compose_path},{env_path}"]
                )
            subject_path = root / "material-subject.json"
            with mock.patch("builtins.print"):
                exit_code = builder.main(
                    [
                        "validate-prepared",
                        "--canonical-compose",
                        str(canonical_path),
                        "--base-witness-compose",
                        str(base_compose_path),
                        "--base-witness-env",
                        str(base_env_path),
                        "--inventory",
                        str(inventory_path),
                        "--approval",
                        str(approval_path),
                        "--approval-policy",
                        str(policy_path),
                        "--prepared-directory",
                        str(prepared),
                        "--material-approval-subject-output",
                        str(subject_path),
                        *bundle_arguments,
                    ]
                )
            self.assertEqual(exit_code, 0)
            subject = json.loads(subject_path.read_text(encoding="utf-8"))
            token, _issued_state, _audit = authenticate_and_issue(
                secrets_payload=enrollment.secrets_payload,
                state_payload=enrollment.state_payload,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password=PASSWORD,
                totp=totp_code(enrollment.totp_secret, at=now)[1],
                recovery_code=None,
                action=builder.MATERIAL_APPROVAL_ACTION,
                environment="staging",
                subject=subject,
                ttl_seconds=60 * 60,
                now=now,
            )
            self.assertEqual(
                token["action"],
                builder.MATERIAL_APPROVAL_ACTION,
            )

    def test_final_publish_and_install_create_exact_active_and_unmounted_controls(
        self,
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, _new_session, _state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, _final, session_bytes, policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
                inventory_approval=inventory_approval,
                old_session=old_session,
                now=now,
            )
            self.assertEqual(
                {path.name for path in (destination / ACTIVE_DIRECTORY_NAME).iterdir()},
                {SESSION_NAME, POLICY_NAME},
            )
            self.assertEqual(
                (destination / ACTIVE_DIRECTORY_NAME / SESSION_NAME).read_bytes(),
                session_bytes,
            )
            self.assertEqual(
                (destination / ACTIVE_DIRECTORY_NAME / POLICY_NAME).read_bytes(),
                policy_bytes,
            )
            for name in (ARCHIVE_DIRECTORY_NAME, JOURNAL_DIRECTORY_NAME):
                control = destination / name
                self.assertEqual(control.stat().st_mode & 0o777, 0o700)
                self.assertEqual(list(control.iterdir()), [])
            self.assertEqual(
                parse_env_values((destination / ENV_NAME).read_text())[
                    "STAGING_HUMAN_APPROVAL_RELAY_MATERIAL_DIR"
                ],
                str(destination / ACTIVE_DIRECTORY_NAME),
            )
            inert_root = destination.parent
            bundle = root / "final"
            with self.assertRaisesRegex(InertRelayInstallError, "already exists"):
                install_inert_bundle(
                    canonical_compose=self.canonical,
                    base_compose_bytes=self.base_compose,
                    base_env_bytes=self.base_env,
                    inventory=self.inventory,
                    approval_policy=enrollment.policy_payload,
                    bundle_directory=bundle,
                    inert_root=inert_root,
                )

    def test_inert_install_cleans_partial_revision(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, _new_session, _state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            material = self._material_directory(root)
            inert_root = material.parent.parent
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
                self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
            self.assertFalse((inert_root / REVISION_ID).exists())

    def test_prepared_bundle_is_controller_only_and_never_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            material = self._material_directory(root)
            bundle, _compose, _env, _manifest = self._prepared_directory(
                root,
                material_directory=material,
            )
            inert_root = material.parent.parent
            inert_root.mkdir(mode=0o700, parents=True)
            inert_root.chmod(0o700)
            with self.assertRaisesRegex(
                InertRelayInstallError,
                "controller-only",
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

    def test_inert_install_rejects_root_swap_before_publication(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, _new_session, _state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            material = self._material_directory(root)
            inert_root = material.parent.parent
            detached = inert_root.with_name("material-revisions-detached")
            redirected = root / "redirected"
            redirected.mkdir(mode=0o700)
            real_assert = installer._assert_inert_root
            calls = 0

            def swap_after_recheck(path):  # noqa: ANN001
                nonlocal calls
                result = real_assert(path)
                calls += 1
                if calls == 2:
                    path.rename(detached)
                    path.symlink_to(redirected, target_is_directory=True)
                return result

            with (
                mock.patch.object(
                    installer,
                    "_assert_inert_root",
                    side_effect=swap_after_recheck,
                ),
                self.assertRaisesRegex(
                    InertRelayInstallError,
                    "changed during bundle validation",
                ),
            ):
                self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
            self.assertEqual(list(redirected.iterdir()), [])
            self.assertFalse((redirected / REVISION_ID).exists())

    def test_rotation_is_atomic_idempotent_and_has_no_active_residue(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, final, old_bytes, _policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
                inventory_approval=inventory_approval,
                old_session=old_session,
                now=now,
            )
            new_path = root / "new-session.json"
            new_bytes = _manifest_bytes(new_session)
            new_path.write_bytes(new_bytes)
            new_path.chmod(0o600)
            result = rotate_witness_relay_session(
                role_compose_path=destination / COMPOSE_NAME,
                env_file_path=destination / ENV_NAME,
                new_session_path=new_path,
                expected_policy_sha256=final["final"]["policy_file_sha256"],
                container_id=CONTAINER_ID,
                now=now + timedelta(seconds=32),
            )
            self.assertFalse(result["idempotent"])
            self.assertEqual(
                result["session_scope_sha256"],
                final["final"]["session_scope_sha256"],
            )
            active = destination / ACTIVE_DIRECTORY_NAME
            self.assertEqual(
                {path.name for path in active.iterdir()},
                {SESSION_NAME, POLICY_NAME},
            )
            self.assertEqual((active / SESSION_NAME).read_bytes(), new_bytes)
            archives = list((destination / ARCHIVE_DIRECTORY_NAME).iterdir())
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_bytes(), old_bytes)
            again = rotate_witness_relay_session(
                role_compose_path=destination / COMPOSE_NAME,
                env_file_path=destination / ENV_NAME,
                new_session_path=new_path,
                expected_policy_sha256=final["final"]["policy_file_sha256"],
                container_id=CONTAINER_ID,
                now=now + timedelta(seconds=33),
            )
            self.assertTrue(again["idempotent"])
            self.assertEqual(len(list((destination / ARCHIVE_DIRECTORY_NAME).iterdir())), 1)
            self.assertEqual(self.docker_run.call_count, 4)
            self.assertEqual(
                self.docker_run.call_args.args[0],
                [
                    "/usr/bin/docker",
                    "inspect",
                    "--type",
                    "container",
                    CONTAINER_ID,
                ],
            )
            self.assertEqual(
                self.docker_run.call_args.kwargs["env"],
                {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
            rendered = yaml.safe_load((destination / COMPOSE_NAME).read_text())
            namespace = parse_env_values(
                (destination / ENV_NAME).read_text()
            )["STAGING_STORAGE_NAMESPACE"]
            self.assertEqual(rendered["name"], f"{namespace}-witness")
            self.assertNotEqual(rendered["name"], namespace)

    def test_rotation_rejects_revision_root_swap_against_pinned_fd(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, final, _old_bytes, _policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
                inventory_approval=inventory_approval,
                old_session=old_session,
                now=now,
            )
            new_path = root / "new-session.json"
            new_path.write_bytes(_manifest_bytes(new_session))
            new_path.chmod(0o600)
            detached = destination.with_name(f"{destination.name}-detached")
            redirected = root / "rotation-redirected"
            redirected.mkdir(mode=0o700)
            real_flock = rotation.fcntl.flock

            def swap_after_lock(descriptor, operation):  # noqa: ANN001
                result = real_flock(descriptor, operation)
                destination.rename(detached)
                destination.symlink_to(redirected, target_is_directory=True)
                return result

            with (
                mock.patch.object(
                    rotation.fcntl,
                    "flock",
                    side_effect=swap_after_lock,
                ),
                self.assertRaises(WitnessRelayRotationError),
            ):
                rotate_witness_relay_session(
                    role_compose_path=destination / COMPOSE_NAME,
                    env_file_path=destination / ENV_NAME,
                    new_session_path=new_path,
                    expected_policy_sha256=final["final"][
                        "policy_file_sha256"
                    ],
                    container_id=CONTAINER_ID,
                    now=now + timedelta(seconds=32),
                )
            self.assertEqual(list(redirected.iterdir()), [])

    def test_every_rotation_crash_point_reconciles_to_zero_active_residue(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )
        for crash_point in sorted(CRASH_POINTS):
            with self.subTest(crash_point=crash_point), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
                new_path = root / "new-session.json"
                new_bytes = _manifest_bytes(new_session)
                new_path.write_bytes(new_bytes)
                new_path.chmod(0o600)
                arguments = dict(
                    role_compose_path=destination / COMPOSE_NAME,
                    env_file_path=destination / ENV_NAME,
                    new_session_path=new_path,
                    expected_policy_sha256=final["final"]["policy_file_sha256"],
                    container_id=CONTAINER_ID,
                    now=now + timedelta(seconds=32),
                )
                with self.assertRaises(InjectedRotationCrash):
                    rotate_witness_relay_session(
                        **arguments,
                        crash_after=crash_point,
                    )
                recovered = rotate_witness_relay_session(**arguments)
                self.assertEqual(recovered["journal_phase"], "complete")
                active = destination / ACTIVE_DIRECTORY_NAME
                self.assertEqual(
                    {path.name for path in active.iterdir()},
                    {SESSION_NAME, POLICY_NAME},
                )
                self.assertEqual((active / SESSION_NAME).read_bytes(), new_bytes)
                archives = list((destination / ARCHIVE_DIRECTORY_NAME).iterdir())
                self.assertEqual(len(archives), 1)
                self.assertEqual(archives[0].read_bytes(), old_bytes)

    def test_pre_activation_crashes_reconcile_after_replacement_expires(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, state = (
            self._issue_pair(now=now)
        )
        for crash_point in ("journal-prepared", "archive-written", "temp-written"):
            with self.subTest(crash_point=crash_point), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
                new_path = root / "new-session.json"
                new_bytes = _manifest_bytes(new_session)
                new_path.write_bytes(new_bytes)
                new_path.chmod(0o600)
                arguments = dict(
                    role_compose_path=destination / COMPOSE_NAME,
                    env_file_path=destination / ENV_NAME,
                    new_session_path=new_path,
                    expected_policy_sha256=final["final"]["policy_file_sha256"],
                    container_id=CONTAINER_ID,
                )
                with self.assertRaises(InjectedRotationCrash):
                    rotate_witness_relay_session(
                        **arguments,
                        now=now + timedelta(seconds=32),
                        crash_after=crash_point,
                    )

                resume_at = now + timedelta(hours=49)
                recovered = rotate_witness_relay_session(
                    **arguments,
                    now=resume_at,
                )
                self.assertEqual(recovered["journal_phase"], "complete")
                active = destination / ACTIVE_DIRECTORY_NAME
                self.assertEqual(
                    {path.name for path in active.iterdir()},
                    {SESSION_NAME, POLICY_NAME},
                )
                self.assertEqual((active / SESSION_NAME).read_bytes(), new_bytes)
                archives = list((destination / ARCHIVE_DIRECTORY_NAME).iterdir())
                self.assertEqual(len(archives), 1)
                self.assertEqual(archives[0].read_bytes(), old_bytes)

                third_at = resume_at + timedelta(seconds=31)
                third_session, _third_state, _audit = authenticate_and_issue_session(
                    secrets_payload=enrollment.secrets_payload,
                    state_payload=state,
                    policy_payload=enrollment.policy_payload,
                    private_key_envelope=enrollment.private_key_envelope,
                    password=PASSWORD,
                    totp=totp_code(enrollment.totp_secret, at=third_at)[1],
                    recovery_code=None,
                    release_sha=self.inventory["release_sha"],
                    allowed_actions=list(REQUIRED_MATRIX_ACTIONS),
                    ttl_seconds=48 * 60 * 60,
                    now=third_at,
                )
                third_path = root / "third-session.json"
                third_path.write_bytes(_manifest_bytes(third_session))
                third_path.chmod(0o600)
                subsequent = rotate_witness_relay_session(
                    **{
                        **arguments,
                        "new_session_path": third_path,
                    },
                    now=third_at + timedelta(seconds=1),
                )
                self.assertEqual(subsequent["journal_phase"], "complete")
                self.assertEqual(
                    (active / SESSION_NAME).read_bytes(),
                    _manifest_bytes(third_session),
                )

    def test_journal_sidecar_only_reconciles_after_replacement_expires(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, final, old_bytes, _policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
                inventory_approval=inventory_approval,
                old_session=old_session,
                now=now,
            )
            new_path = root / "new-session.json"
            new_bytes = _manifest_bytes(new_session)
            new_path.write_bytes(new_bytes)
            new_path.chmod(0o600)
            arguments = dict(
                role_compose_path=destination / COMPOSE_NAME,
                env_file_path=destination / ENV_NAME,
                new_session_path=new_path,
                expected_policy_sha256=final["final"]["policy_file_sha256"],
                container_id=CONTAINER_ID,
            )
            with (
                mock.patch.object(
                    rotation.os,
                    "link",
                    side_effect=InjectedRotationCrash("injected before journal link"),
                ),
                self.assertRaises(InjectedRotationCrash),
            ):
                rotate_witness_relay_session(
                    **arguments,
                    now=now + timedelta(seconds=32),
                )
            journals = destination / JOURNAL_DIRECTORY_NAME
            journal_entries = list(journals.iterdir())
            self.assertEqual(len(journal_entries), 1)
            self.assertTrue(journal_entries[0].name.endswith(".json.creating"))

            resume_at = now + timedelta(hours=49)
            recovered = rotate_witness_relay_session(
                **arguments,
                now=resume_at,
            )
            self.assertEqual(recovered["journal_phase"], "complete")
            active = destination / ACTIVE_DIRECTORY_NAME
            self.assertEqual(
                {path.name for path in active.iterdir()},
                {SESSION_NAME, POLICY_NAME},
            )
            self.assertEqual((active / SESSION_NAME).read_bytes(), new_bytes)
            archives = list((destination / ARCHIVE_DIRECTORY_NAME).iterdir())
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].read_bytes(), old_bytes)

            third_at = resume_at + timedelta(seconds=31)
            third_session, _third_state, _audit = authenticate_and_issue_session(
                secrets_payload=enrollment.secrets_payload,
                state_payload=state,
                policy_payload=enrollment.policy_payload,
                private_key_envelope=enrollment.private_key_envelope,
                password=PASSWORD,
                totp=totp_code(enrollment.totp_secret, at=third_at)[1],
                recovery_code=None,
                release_sha=self.inventory["release_sha"],
                allowed_actions=list(REQUIRED_MATRIX_ACTIONS),
                ttl_seconds=48 * 60 * 60,
                now=third_at,
            )
            third_path = root / "third-session.json"
            third_path.write_bytes(_manifest_bytes(third_session))
            third_path.chmod(0o600)
            subsequent = rotate_witness_relay_session(
                **{
                    **arguments,
                    "new_session_path": third_path,
                },
                now=third_at + timedelta(seconds=1),
            )
            self.assertEqual(subsequent["journal_phase"], "complete")
            self.assertEqual(
                (active / SESSION_NAME).read_bytes(),
                _manifest_bytes(third_session),
            )

    def test_incomplete_initial_journal_sidecar_restarts_only_while_fresh(
        self,
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )

        def nonmatching_journal(payload: bytes) -> bytes:
            journal = json.loads(payload)
            journal["runtime"]["started_at"] = "2026-07-27T09:00:01.000000000Z"
            return _manifest_bytes(journal)

        residues = {
            "empty": lambda _payload: b"",
            "partial": lambda payload: payload[:17],
            "valid-but-nonmatching": nonmatching_journal,
        }
        for label, residue_for in residues.items():
            with self.subTest(residue=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
                new_path = root / "new-session.json"
                new_bytes = _manifest_bytes(new_session)
                new_path.write_bytes(new_bytes)
                new_path.chmod(0o600)
                arguments = {
                    "role_compose_path": destination / COMPOSE_NAME,
                    "env_file_path": destination / ENV_NAME,
                    "new_session_path": new_path,
                    "expected_policy_sha256": final["final"][
                        "policy_file_sha256"
                    ],
                    "container_id": CONTAINER_ID,
                }
                real_write = rotation._write_direct_new

                def leave_incomplete_sidecar(path, payload, *, label):  # noqa: ANN001
                    if (
                        path.parent.name == JOURNAL_DIRECTORY_NAME
                        and path.name.endswith(".creating")
                    ):
                        path.write_bytes(residue_for(payload))
                        path.chmod(0o600)
                        raise InjectedRotationCrash(
                            "injected SIGKILL during initial journal write"
                        )
                    return real_write(path, payload, label=label)

                with (
                    mock.patch.object(
                        rotation,
                        "_write_direct_new",
                        side_effect=leave_incomplete_sidecar,
                    ),
                    self.assertRaises(InjectedRotationCrash),
                ):
                    rotate_witness_relay_session(
                        **arguments,
                        now=now + timedelta(seconds=32),
                    )

                journals = destination / JOURNAL_DIRECTORY_NAME
                residue = list(journals.iterdir())
                self.assertEqual(len(residue), 1)
                self.assertTrue(residue[0].name.endswith(".json.creating"))
                self.assertEqual(
                    list((destination / ARCHIVE_DIRECTORY_NAME).iterdir()),
                    [],
                )
                self.assertEqual(
                    (destination / ACTIVE_DIRECTORY_NAME / SESSION_NAME).read_bytes(),
                    old_bytes,
                )

                recovered = rotate_witness_relay_session(
                    **arguments,
                    now=now + timedelta(seconds=33),
                )
                self.assertEqual(recovered["journal_phase"], "complete")
                self.assertEqual(
                    (destination / ACTIVE_DIRECTORY_NAME / SESSION_NAME).read_bytes(),
                    new_bytes,
                )
                archives = list(
                    (destination / ARCHIVE_DIRECTORY_NAME).iterdir()
                )
                self.assertEqual(len(archives), 1)
                self.assertEqual(archives[0].read_bytes(), old_bytes)
                journal_entries = list(journals.iterdir())
                self.assertEqual(len(journal_entries), 1)
                self.assertFalse(journal_entries[0].name.startswith("."))
                self.assertEqual(
                    json.loads(journal_entries[0].read_text())["phase"],
                    "complete",
                )

    def test_expired_session_cannot_clean_malformed_initial_journal_sidecar(
        self,
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, final, old_bytes, _policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
                inventory_approval=inventory_approval,
                old_session=old_session,
                now=now,
            )
            new_path = root / "new-session.json"
            new_path.write_bytes(_manifest_bytes(new_session))
            new_path.chmod(0o600)
            arguments = {
                "role_compose_path": destination / COMPOSE_NAME,
                "env_file_path": destination / ENV_NAME,
                "new_session_path": new_path,
                "expected_policy_sha256": final["final"][
                    "policy_file_sha256"
                ],
                "container_id": CONTAINER_ID,
            }
            real_write = rotation._write_direct_new
            invalid = _manifest_bytes({"schema": rotation.ROTATION_SCHEMA})

            def leave_invalid_sidecar(path, payload, *, label):  # noqa: ANN001
                if (
                    path.parent.name == JOURNAL_DIRECTORY_NAME
                    and path.name.endswith(".creating")
                ):
                    path.write_bytes(invalid)
                    path.chmod(0o600)
                    raise InjectedRotationCrash(
                        "injected SIGKILL during initial journal write"
                    )
                return real_write(path, payload, label=label)

            with (
                mock.patch.object(
                    rotation,
                    "_write_direct_new",
                    side_effect=leave_invalid_sidecar,
                ),
                self.assertRaises(InjectedRotationCrash),
            ):
                rotate_witness_relay_session(
                    **arguments,
                    now=now + timedelta(seconds=32),
                )

            def snapshot(directory: Path) -> dict[str, bytes]:
                return {
                    path.name: path.read_bytes()
                    for path in directory.iterdir()
                    if path.is_file()
                }

            active = destination / ACTIVE_DIRECTORY_NAME
            archive = destination / ARCHIVE_DIRECTORY_NAME
            journals = destination / JOURNAL_DIRECTORY_NAME
            before = (
                snapshot(active),
                snapshot(archive),
                snapshot(journals),
            )
            with self.assertRaisesRegex(HumanApprovalError, "expired"):
                rotate_witness_relay_session(
                    **arguments,
                    now=now + timedelta(hours=49),
                )
            self.assertEqual(
                (
                    snapshot(active),
                    snapshot(archive),
                    snapshot(journals),
                ),
                before,
            )
            self.assertEqual((active / SESSION_NAME).read_bytes(), old_bytes)

    def test_incomplete_rotation_blocks_a_different_replacement(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, state = (
            self._issue_pair(now=now)
        )
        third_at = now + timedelta(seconds=62)
        third_session, _third_state, _audit = authenticate_and_issue_session(
            secrets_payload=enrollment.secrets_payload,
            state_payload=state,
            policy_payload=enrollment.policy_payload,
            private_key_envelope=enrollment.private_key_envelope,
            password=PASSWORD,
            totp=totp_code(enrollment.totp_secret, at=third_at)[1],
            recovery_code=None,
            release_sha=self.inventory["release_sha"],
            allowed_actions=list(REQUIRED_MATRIX_ACTIONS),
            ttl_seconds=48 * 60 * 60,
            now=third_at,
        )
        for crash_point in sorted(CRASH_POINTS - {"journal-complete"}):
            with self.subTest(crash_point=crash_point), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, _old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
                new_path = root / "new-session.json"
                new_path.write_bytes(_manifest_bytes(new_session))
                new_path.chmod(0o600)
                third_path = root / "third-session.json"
                third_path.write_bytes(_manifest_bytes(third_session))
                third_path.chmod(0o600)
                arguments = dict(
                    role_compose_path=destination / COMPOSE_NAME,
                    env_file_path=destination / ENV_NAME,
                    expected_policy_sha256=final["final"]["policy_file_sha256"],
                    container_id=CONTAINER_ID,
                )
                with self.assertRaises(InjectedRotationCrash):
                    rotate_witness_relay_session(
                        **arguments,
                        new_session_path=new_path,
                        now=now + timedelta(seconds=32),
                        crash_after=crash_point,
                    )

                def snapshot(directory: Path) -> dict[str, bytes]:
                    return {
                        path.name: path.read_bytes()
                        for path in directory.iterdir()
                        if path.is_file()
                    }

                active = destination / ACTIVE_DIRECTORY_NAME
                archive = destination / ARCHIVE_DIRECTORY_NAME
                journals = destination / JOURNAL_DIRECTORY_NAME
                before = (
                    snapshot(active),
                    snapshot(archive),
                    snapshot(journals),
                )
                with self.assertRaises(WitnessRelayRotationError):
                    rotate_witness_relay_session(
                        **arguments,
                        new_session_path=third_path,
                        now=third_at + timedelta(seconds=1),
                    )
                self.assertEqual(
                    (
                        snapshot(active),
                        snapshot(archive),
                        snapshot(journals),
                    ),
                    before,
                )

                recovered = rotate_witness_relay_session(
                    **arguments,
                    new_session_path=new_path,
                    now=third_at + timedelta(seconds=2),
                )
                self.assertEqual(recovered["journal_phase"], "complete")
                subsequent = rotate_witness_relay_session(
                    **arguments,
                    new_session_path=third_path,
                    now=third_at + timedelta(seconds=3),
                )
                self.assertEqual(subsequent["journal_phase"], "complete")
                self.assertEqual(
                    (active / SESSION_NAME).read_bytes(),
                    _manifest_bytes(third_session),
                )

    def test_rotation_rejects_file_bind_and_unsafe_active_directory(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )
        for mutation in ("file-bind", "broad-directory"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
                new_path = root / "new-session.json"
                new_path.write_bytes(_manifest_bytes(new_session))
                new_path.chmod(0o600)
                compose_path = destination / COMPOSE_NAME
                active = destination / ACTIVE_DIRECTORY_NAME
                if mutation == "file-bind":
                    payload = yaml.safe_load(compose_path.read_text())
                    volumes = payload["services"]["witness_api"]["volumes"]
                    volumes[:] = [
                        item
                        for item in volumes
                        if "/run/human-approval" not in str(item)
                    ]
                    volumes.extend(
                        [
                            "/root/session.json:/run/human-approval/session.json:ro",
                            "/root/policy.json:/run/human-approval/policy.json:ro",
                        ]
                    )
                    compose_path.write_text(
                        yaml.safe_dump(payload, sort_keys=False),
                        encoding="utf-8",
                    )
                    compose_path.chmod(0o640)
                    expected_error = "directory bind"
                else:
                    active.chmod(0o755)
                    expected_error = "mode-0700"
                with self.assertRaisesRegex(
                    WitnessRelayRotationError, expected_error
                ):
                    rotate_witness_relay_session(
                        role_compose_path=compose_path,
                        env_file_path=destination / ENV_NAME,
                        new_session_path=new_path,
                        expected_policy_sha256=final["final"]["policy_file_sha256"],
                        container_id=CONTAINER_ID,
                        now=now + timedelta(seconds=32),
                    )
                self.assertEqual(
                    (active / SESSION_NAME).read_bytes(),
                    old_bytes,
                )
                self.assertEqual(
                    list((destination / ARCHIVE_DIRECTORY_NAME).iterdir()),
                    [],
                )

    def test_rotation_requires_exact_running_directory_bind_and_secret(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )
        for mutation in ("legacy-file-mounts", "different-relay-secret"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
                    inventory_approval=inventory_approval,
                    old_session=old_session,
                    now=now,
                )
                new_path = root / "new-session.json"
                new_path.write_bytes(_manifest_bytes(new_session))
                new_path.chmod(0o600)

                def unsafe_inspect(*args, **kwargs):  # noqa: ANN002, ANN003
                    completed = self._docker_inspect(*args, **kwargs)
                    document = json.loads(completed.stdout)[0]
                    if mutation == "legacy-file-mounts":
                        document["Mounts"] = [
                            {
                                "Type": "bind",
                                "Source": "/root/session.json",
                                "Destination": "/run/human-approval/session.json",
                                "RW": False,
                            },
                            {
                                "Type": "bind",
                                "Source": "/root/policy.json",
                                "Destination": "/run/human-approval/policy.json",
                                "RW": False,
                            },
                        ]
                    else:
                        environment = document["Config"]["Env"]
                        environment[:] = [
                            (
                                "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET="
                                "different-runtime-secret-that-is-at-least-32-bytes"
                            )
                            if item.startswith(
                                "HUMAN_APPROVAL_RELAY_ORCHESTRATOR_SECRET="
                            )
                            else item
                            for item in environment
                        ]
                    return subprocess.CompletedProcess(
                        args=completed.args,
                        returncode=0,
                        stdout=json.dumps([document]),
                        stderr="",
                    )

                self.docker_run.side_effect = unsafe_inspect
                with self.assertRaisesRegex(
                    WitnessRelayRotationError, "running Witness"
                ):
                    rotate_witness_relay_session(
                        role_compose_path=destination / COMPOSE_NAME,
                        env_file_path=destination / ENV_NAME,
                        new_session_path=new_path,
                        expected_policy_sha256=final["final"][
                            "policy_file_sha256"
                        ],
                        container_id=CONTAINER_ID,
                        now=now + timedelta(seconds=32),
                    )
                self.assertEqual(
                    (destination / ACTIVE_DIRECTORY_NAME / SESSION_NAME).read_bytes(),
                    old_bytes,
                )
                self.assertEqual(
                    list((destination / ARCHIVE_DIRECTORY_NAME).iterdir()),
                    [],
                )
                self.assertEqual(
                    list((destination / JOURNAL_DIRECTORY_NAME).iterdir()),
                    [],
                )

    def test_rotation_rejects_an_older_valid_session_before_writing(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=1)
        enrollment, inventory_approval, old_session, new_session, _state = (
            self._issue_pair(now=now)
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, final, live_bytes, _policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
                inventory_approval=inventory_approval,
                old_session=new_session,
                now=now + timedelta(seconds=32),
            )
            older_path = root / "older-session.json"
            older_path.write_bytes(_manifest_bytes(old_session))
            older_path.chmod(0o600)
            with self.assertRaisesRegex(
                WitnessRelayRotationError, "not newer"
            ):
                rotate_witness_relay_session(
                    role_compose_path=destination / COMPOSE_NAME,
                    env_file_path=destination / ENV_NAME,
                    new_session_path=older_path,
                    expected_policy_sha256=final["final"]["policy_file_sha256"],
                    container_id=CONTAINER_ID,
                    now=now + timedelta(seconds=33),
                )
            self.assertEqual(
                (destination / ACTIVE_DIRECTORY_NAME / SESSION_NAME).read_bytes(),
                live_bytes,
            )
            self.assertEqual(
                list((destination / ARCHIVE_DIRECTORY_NAME).iterdir()),
                [],
            )
            self.assertEqual(
                list((destination / JOURNAL_DIRECTORY_NAME).iterdir()),
                [],
            )

    def test_operation_bound_file_primitives_reconcile_sigkill_residue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            operation_id = "d" * 32
            first_payload = b'{"phase":"prepared"}\n'
            second_payload = b'{"phase":"complete"}\n'

            target = root / f"{operation_id}.json"
            creation_sidecar = root / f".{target.name}.creating"
            creation_sidecar.write_bytes(b'{"phase":')
            creation_sidecar.chmod(0o600)
            rotation._recoverable_create_exclusive(
                target,
                first_payload,
                label="test journal",
            )
            self.assertEqual(target.read_bytes(), first_payload)
            self.assertFalse(creation_sidecar.exists())

            linked_target = root / f"session-{'e' * 64}.json"
            linked_sidecar = root / f".{linked_target.name}.creating"
            linked_sidecar.write_bytes(first_payload)
            linked_sidecar.chmod(0o600)
            linked_target.hardlink_to(linked_sidecar)
            rotation._recoverable_create_exclusive(
                linked_target,
                first_payload,
                label="test archive",
            )
            self.assertEqual(linked_target.stat().st_nlink, 1)
            self.assertFalse(linked_sidecar.exists())

            update_sidecar = root / f".{target.name}.{operation_id}.update"
            update_sidecar.write_bytes(b'{"phase":')
            update_sidecar.chmod(0o600)
            rotation._recoverable_atomic_replace(
                target,
                second_payload,
                operation_id=operation_id,
                label="test journal",
            )
            self.assertEqual(target.read_bytes(), second_payload)
            self.assertFalse(update_sidecar.exists())

            staged = root / f".session-{operation_id}.tmp"
            staged.write_bytes(b'{"partial":')
            staged.chmod(0o600)
            rotation._recoverable_stage_file(
                staged,
                second_payload,
                durable_phase="archived",
            )
            self.assertEqual(staged.read_bytes(), second_payload)

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
            with self.assertRaises(WitnessRelayMaterialError):
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
