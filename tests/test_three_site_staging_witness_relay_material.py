from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

from core.human_approval import RELAY_RECEIPT_SCHEMA, staging_session_scope_sha256
from core.human_approval_issuer import (
    DEFAULT_ACTIONS,
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
from scripts.render_three_site_staging_role_compose import parse_env_values
from scripts.rotate_three_site_staging_witness_relay_session import (
    CRASH_POINTS,
    InjectedRotationCrash,
    WitnessRelayRotationError,
    rotate_witness_relay_session,
)
from tests import test_three_site_staging_campaign_bundle as campaign_fixtures


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
                        "com.docker.compose.project": values[
                            "STAGING_STORAGE_NAMESPACE"
                        ],
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
        return enrollment, old_session, new_session, state

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

    def _install_final(
        self,
        root: Path,
        *,
        enrollment,
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

    def test_final_bundle_requires_exact_session_scope_and_exposes_scope_hash(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, old_session, _new_session, state = self._issue_pair(now=now)
        material = self._material_directory(Path("/tmp/witness-relay-contract"))
        compose, env, prepared = self._derive(
            material_directory=material,
            policy=enrollment.policy_payload,
            created_at=now,
        )
        prepared_bytes = _manifest_bytes(prepared)
        policy_bytes = _manifest_bytes(enrollment.policy_payload)
        session_bytes = _manifest_bytes(old_session)
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
                policy=enrollment.policy_payload,
                policy_bytes=policy_bytes,
                session=overbroad,
                session_bytes=_manifest_bytes(overbroad),
                now=overbroad_at,
            )

    def test_final_publish_and_install_create_exact_active_and_unmounted_controls(
        self,
    ) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, old_session, _new_session, _state = self._issue_pair(now=now)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, _final, session_bytes, policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
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

    def test_rotation_is_atomic_idempotent_and_has_no_active_residue(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, old_session, new_session, _state = self._issue_pair(now=now)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, final, old_bytes, _policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
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

    def test_every_rotation_crash_point_reconciles_to_zero_active_residue(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, old_session, new_session, _state = self._issue_pair(now=now)
        for crash_point in sorted(CRASH_POINTS):
            with self.subTest(crash_point=crash_point), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
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

    def test_rotation_rejects_file_bind_and_unsafe_active_directory(self) -> None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, old_session, new_session, _state = self._issue_pair(now=now)
        for mutation in ("file-bind", "broad-directory"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
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
        enrollment, old_session, new_session, _state = self._issue_pair(now=now)
        for mutation in ("legacy-file-mounts", "different-relay-secret"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                root.chmod(0o700)
                destination, final, old_bytes, _policy_bytes = self._install_final(
                    root,
                    enrollment=enrollment,
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
        now = datetime.now(timezone.utc).replace(microsecond=0)
        enrollment, old_session, new_session, _state = self._issue_pair(now=now)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            destination, final, live_bytes, _policy_bytes = self._install_final(
                root,
                enrollment=enrollment,
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
