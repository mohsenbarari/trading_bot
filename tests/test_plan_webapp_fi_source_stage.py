"""Focused plan-only coverage for the FI source-stage coordinator skeleton."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "plan_webapp_fi_source_stage.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coordinator = _load("plan_webapp_fi_source_stage_test", SCRIPT)

CAMPAIGN = "source-stage-plan-20260730"
RELEASE = "a" * 40
TREE = "b" * 40
REVISION = "f2c7d8e9a0b1"
CONTROL = "c" * 40
CONTROL_TREE = "d" * 40


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


@unittest.skipUnless(os.geteuid() == 0, "controller plans enforce root-only controls")
class SourceStagePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="source-stage-plan-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        phase = self.root / "campaigns" / CAMPAIGN / "webapp-fi-source"
        phase.mkdir(mode=0o700, parents=True)
        os.chmod(phase.parent, 0o700)
        os.chmod(phase, 0o700)
        unsigned = coordinator.role_config.binding.build_campaign_binding(
            campaign_id=CAMPAIGN,
            application_release_sha=RELEASE,
            application_release_tree=TREE,
            expected_alembic_revision=REVISION,
            control_commit=CONTROL,
            control_tree=CONTROL_TREE,
        )
        self.binding = _write_private(
            phase / "campaign-binding.json",
            coordinator.role_config.binding.canonical_json_bytes(unsigned) + b"\n",
        )
        bound = coordinator.role_config.binding.load_campaign_binding(self.binding)
        role = coordinator.role_config.build_source_role_config(
            campaign_binding=bound,
            application_container="trading_bot_app",
            sync_worker_container="trading_bot_sync_worker",
        )
        self.role_config = _write_private(phase / "source-role-config.json", _canonical(role))
        self.known_hosts = self.root / "inputs" / "fi-known_hosts"
        self.known_hosts.parent.mkdir(mode=0o700)
        self.known_hosts.write_text(
            "65.109.220.59 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixtureKnownHostKey\n",
            encoding="ascii",
        )
        self.known_hosts.chmod(0o644)

    def test_explicit_stage_actions_have_no_run_all_or_capability_flags(self) -> None:
        actions = coordinator._parser()._subparsers._group_actions[0].choices
        self.assertEqual(
            {"bootstrap-plan", "static-plan", "packet-plan", "image-plan", "evidence-plan"}, set(actions)
        )
        self.assertNotIn("run-all", actions)
        for action in actions.values():
            self.assertFalse(
                any("presigned" in "/".join(argument.option_strings) for argument in action._actions)
            )

    def test_renderer_binding_must_match_the_original_plan_context(self) -> None:
        context = coordinator.PlanContext(
            campaign_binding_path=self.binding,
            source_role_config_path=self.role_config,
            fi_known_hosts=self.known_hosts,
            campaign_id=CAMPAIGN,
            binding_sha256="1" * 64,
            application={
                "release_sha": RELEASE,
                "release_tree": TREE,
                "expected_alembic_revision": REVISION,
            },
            tooling={"control_commit": CONTROL, "control_tree": CONTROL_TREE},
            source_role_config_sha256="2" * 64,
        )
        matching = SimpleNamespace(
            campaign_id=CAMPAIGN,
            application_release_sha=RELEASE,
            application_release_tree=TREE,
            expected_alembic_revision=REVISION,
            control_commit=CONTROL,
            control_tree=CONTROL_TREE,
            binding_sha256="1" * 64,
        )
        self.assertTrue(coordinator._binding_matches_context(matching, context))
        self.assertFalse(
            coordinator._binding_matches_context(
                SimpleNamespace(**{**matching.__dict__, "binding_sha256": "3" * 64}), context
            )
        )

    def test_packet_plan_composes_pre_capability_renderer_without_a_url(self) -> None:
        bound = coordinator.role_config.binding.load_campaign_binding(self.binding)
        packet_payload = b'{"packet":"fixture"}\n'
        descriptor = {
            "object_key": "campaigns/source-stage/static-provenance/packet-20260730.age",
            "version_id": "version-fixture-001",
            "ciphertext_sha256": "1" * 64,
            "ciphertext_bytes": 2048,
            "plaintext_sha256": coordinator.hashlib.sha256(packet_payload).hexdigest(),
            "plaintext_bytes": len(packet_payload),
        }
        fake_control = SimpleNamespace(
            campaign_binding=bound,
            packet_id="packet-20260730",
            packet_path=Path("/root/secure-envs/trading-bot/packets/packet-20260730.json"),
            packet_payload=packet_payload,
            transport_receipt={"object": descriptor},
            candidate_directory=Path("/srv/trading-bot-three-site-staging-data/fi-candidate"),
            received_directory=Path("/srv/trading-bot-three-site-staging-data/fi-received"),
            fi_install_receipt_sha256="2" * 64,
        )
        with (
            mock.patch.object(
                coordinator.packet,
                "build_static_provenance_receive_control",
                return_value=fake_control,
            ) as build,
            mock.patch.object(coordinator.packet, "render_receive_install_command") as render,
        ):
            plan = coordinator.plan_packet(
                campaign_binding=self.binding,
                source_role_config=self.role_config,
                fi_known_hosts=self.known_hosts,
                source_transport_config=self.root / "inputs" / "transport.json",
                source_adoption_package_directory=self.root / "package",
                preparation_receipt=self.root / "preparation.json",
                fi_install_receipt=self.root / "install.json",
                packet_id="packet-20260730",
                transport_publish_receipt=self.root / "publish.json",
            )
        self.assertEqual("packet", plan["stage"])
        self.assertEqual("packet-20260730", plan["identifiers"]["packet_id"])
        self.assertEqual(descriptor, plan["packet"]["published_object"])
        self.assertEqual("ephemeral-object-get-capability-required", plan["packet"]["next_control"])
        self.assertFalse(plan["ssh_changed"])
        self.assertFalse(plan["object_storage_changed"])
        self.assertEqual("packet-20260730", build.call_args.kwargs["packet_id"])
        render.assert_not_called()
        rendered = json.dumps(plan, sort_keys=True).lower()
        self.assertNotIn("presigned", rendered)
        self.assertNotIn("://", rendered)
        packet_args = coordinator._parser()._subparsers._group_actions[0].choices["packet-plan"]._actions
        self.assertFalse(any("presigned" in "/".join(action.option_strings) for action in packet_args))

    def _post_packet_control(self, *, artifact_kind: str, artifact_id: str) -> SimpleNamespace:
        bound = coordinator.role_config.binding.load_campaign_binding(self.binding)
        recipient = "age1controllerrecipientfixture000000000000000000000"
        return SimpleNamespace(
            campaign_binding=bound,
            packet_id="packet-20260730",
            artifact_kind=artifact_kind,
            artifact_id=artifact_id,
            policy=SimpleNamespace(controller_age_recipient=recipient),
            request=SimpleNamespace(
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                control_commit=CONTROL,
                control_tree=CONTROL_TREE,
                source_site="webapp_fi",
                destination_site="controller",
                object_kind=artifact_kind,
                object_id=artifact_id,
                mode=coordinator.post_packet.initial.transport.SINGLE_MODE,
                recipients=(recipient,),
            ),
            fi_candidate_directory=Path("/srv/trading-bot-three-site/bootstrap/installed-fixture"),
            fi_packet_directory=Path("/srv/trading-bot-three-site/bootstrap/installed-fixture/controller-static-provenance/packet-20260730"),
            prepared_directory=Path("/root/secure-envs/trading-bot/post-packet-fixture"),
            control_packet_sha256="3" * 64,
            static_packet_receipt_sha256="4" * 64,
            source_role_config_sha256="5" * 64,
        )

    def test_image_and_evidence_plans_use_fixed_controller_recipient_routes_without_upload(self) -> None:
        cases = (
            (
                "image",
                coordinator.post_packet.RAW_APP_IMAGE,
                "image-export-20260730",
                coordinator.plan_image,
                "image_export_id",
            ),
            (
                "evidence",
                coordinator.post_packet.SOURCE_EVIDENCE,
                "evidence-20260730",
                coordinator.plan_evidence,
                "evidence_id",
            ),
        )
        for stage, kind, artifact_id, planner, identifier_name in cases:
            with self.subTest(stage=stage):
                control = self._post_packet_control(artifact_kind=kind, artifact_id=artifact_id)
                command = "ssh -o BatchMode=yes root@65.109.220.59 /usr/bin/python3 prepare-upload"
                with (
                    mock.patch.object(
                        coordinator.post_packet,
                        "build_post_packet_upload_control",
                        return_value=control,
                    ) as build,
                    mock.patch.object(
                        coordinator.post_packet,
                        "render_prepare_command",
                        return_value=command,
                    ) as render_prepare,
                    mock.patch.object(
                        coordinator.post_packet.initial.transport,
                        "source_object_key",
                        return_value="campaigns/source-stage/post-packet-fixture.age",
                    ),
                    mock.patch.object(coordinator.post_packet, "render_upload_command") as render_upload,
                ):
                    plan = planner(
                        campaign_binding=self.binding,
                        source_role_config=self.role_config,
                        fi_known_hosts=self.known_hosts,
                        source_transport_config=self.root / "inputs" / "transport.json",
                        fi_static_packet_install_receipt=self.root / "inputs" / "packet-install.json",
                        packet_id="packet-20260730",
                        **{identifier_name: artifact_id},
                    )
                self.assertEqual(stage, plan["stage"])
                self.assertEqual(kind, plan[stage]["artifact_kind"])
                self.assertEqual(artifact_id, plan["identifiers"][identifier_name])
                self.assertEqual(command, plan[stage]["rendered_prepare_command"])
                self.assertEqual("fi-post-packet-prepared-receipt-required", plan[stage]["next_control"])
                self.assertFalse(plan["ssh_changed"])
                self.assertFalse(plan["object_storage_changed"])
                self.assertEqual(kind, build.call_args.kwargs["artifact_kind"])
                self.assertEqual(artifact_id, build.call_args.kwargs["artifact_id"])
                self.assertEqual(self.known_hosts, render_prepare.call_args.kwargs["fi_known_hosts"])
                render_upload.assert_not_called()
                rendered = json.dumps(plan, sort_keys=True).lower()
                self.assertNotIn("presigned", rendered)
                self.assertNotIn("://", rendered)
                self.assertNotIn("secret", rendered)

    def test_post_packet_plan_blocks_a_non_controller_only_renderer_result_before_rendering(self) -> None:
        control = self._post_packet_control(
            artifact_kind=coordinator.post_packet.RAW_APP_IMAGE,
            artifact_id="image-export-20260730",
        )
        control.request.recipients = ("age1not-the-controller-recipient",)
        with (
            mock.patch.object(
                coordinator.post_packet,
                "build_post_packet_upload_control",
                return_value=control,
            ),
            mock.patch.object(coordinator.post_packet, "render_prepare_command") as render_prepare,
        ):
            with self.assertRaisesRegex(
                coordinator.SourceStagePlanError, "exact controller-recipient route"
            ):
                coordinator.plan_image(
                    campaign_binding=self.binding,
                    source_role_config=self.role_config,
                    fi_known_hosts=self.known_hosts,
                    source_transport_config=self.root / "inputs" / "transport.json",
                    fi_static_packet_install_receipt=self.root / "inputs" / "packet-install.json",
                    packet_id="packet-20260730",
                    image_export_id="image-export-20260730",
                )
        render_prepare.assert_not_called()

    def test_static_plan_composes_only_the_fixed_renderer_output(self) -> None:
        bound = coordinator.role_config.binding.load_campaign_binding(self.binding)
        fake_control = SimpleNamespace(
            static_output_id="initial-static-20260730",
            runtime_source_root=Path("/srv/trading-bot/current"),
            static_output_directory=Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-bootstrap/initial-static-assets-initial-static-20260730"),
            initial_control=SimpleNamespace(campaign_binding=bound, package_id="source-package"),
        )
        rendered = "ssh -o BatchMode=yes root@65.109.220.59 /usr/bin/python3"
        with (
            mock.patch.object(coordinator.static, "build_static_preparation_control", return_value=fake_control) as build,
            mock.patch.object(coordinator.static, "render_prepare_command", return_value=rendered) as render,
        ):
            plan = coordinator.plan_static(
                campaign_binding=self.binding,
                source_role_config=self.role_config,
                fi_known_hosts=self.known_hosts,
                source_transport_config=self.root / "inputs" / "transport.json",
                source_adoption_package_directory=self.root / "package",
                preparation_receipt=self.root / "preparation.json",
                fi_install_receipt=self.root / "install.json",
                static_output_id="initial-static-20260730",
            )
        self.assertEqual("static", plan["stage"])
        self.assertEqual(rendered, plan["static"]["rendered_control_command"])
        self.assertEqual("/srv/trading-bot/current", plan["static"]["runtime_source_root"])
        self.assertFalse(plan["object_storage_changed"])
        self.assertEqual("initial-static-20260730", build.call_args.kwargs["static_output_id"])
        self.assertEqual(self.known_hosts, render.call_args.kwargs["fi_known_hosts"])
        self.assertNotIn("://", json.dumps(plan, sort_keys=True))

    def test_bootstrap_plan_has_no_capability_input_or_persisted_url(self) -> None:
        prepared = {
            "package_id": "source-package",
            "application": {"release_sha": RELEASE, "expected_alembic_revision": REVISION},
            "tooling": {"control_commit": CONTROL, "control_tree": CONTROL_TREE},
        }
        published = {
            "campaign_id": CAMPAIGN,
            "object_id": "source-package",
            "object": {
                "object_key": "campaign/artifacts/source-package.age",
                "version_id": "version-fixture",
                "ciphertext_sha256": "e" * 64,
                "ciphertext_bytes": 2048,
                "plaintext_sha256": "f" * 64,
                "plaintext_bytes": 1024,
            },
        }
        with (
            mock.patch.object(coordinator.bootstrap, "_load_transport_policy", return_value=SimpleNamespace()),
            mock.patch.object(coordinator.bootstrap, "_verify_prepared_package", return_value=(prepared, {}, b"{}")),
            mock.patch.object(coordinator.bootstrap, "_read_root_only_file", return_value=b"{}"),
            mock.patch.object(coordinator.bootstrap, "_verify_generic_transport_receipt", return_value=published),
            mock.patch.object(coordinator.bootstrap, "_verify_delivery_envelope", return_value=({}, {})),
        ):
            plan = coordinator.plan_bootstrap(
                campaign_binding=self.binding,
                source_role_config=self.role_config,
                fi_known_hosts=self.known_hosts,
                source_transport_config=self.root / "inputs" / "transport.json",
                source_adoption_package_directory=self.root / "package",
                preparation_receipt=self.root / "preparation.json",
                transport_publish_receipt=self.root / "publish.json",
                delivery_envelope=self.root / "envelope.json",
                pinned_controller_public_key_base64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            )
        self.assertEqual("bootstrap", plan["stage"])
        self.assertEqual("ephemeral-object-get-capability-required", plan["bootstrap"]["next_control"])
        self.assertNotIn("presigned", json.dumps(plan, sort_keys=True).lower())
        self.assertNotIn("://", json.dumps(plan, sort_keys=True))
        bootstrap_args = coordinator._parser()._subparsers._group_actions[0].choices["bootstrap-plan"]._actions
        self.assertFalse(any("presigned" in "/".join(action.option_strings) for action in bootstrap_args))

    def test_noncanonical_role_path_blocks_before_any_stage_adapter(self) -> None:
        wrong = _write_private(self.root / "wrong-role.json", self.role_config.read_bytes())
        with self.assertRaisesRegex(coordinator.SourceStagePlanError, "fixed campaign-bound path"):
            coordinator.plan_image(
                campaign_binding=self.binding,
                source_role_config=wrong,
                fi_known_hosts=self.known_hosts,
                source_transport_config=self.root / "inputs" / "transport.json",
                fi_static_packet_install_receipt=self.root / "inputs" / "packet-install.json",
                packet_id="packet-20260730",
                image_export_id="image-20260730",
            )

    def test_fixture_wires_bootstrap_static_and_packet_plans_without_capability_or_execution(self) -> None:
        """Exercise the three wired stages as one URL-free coordinator pass.

        The fixture deliberately supplies only public, immutable metadata to
        the adapter seams.  It proves the coordinator passes one canonical
        campaign/role/host context through all three plan calls, while no
        renderer execution entrypoint, subprocess, S3 client, or transient
        capability is reached.
        """

        bound = coordinator.role_config.binding.load_campaign_binding(self.binding)
        source_transport_config = self.root / "inputs" / "transport.json"
        package = self.root / "package"
        preparation = self.root / "preparation.json"
        fi_install = self.root / "fi-install.json"
        bootstrap_publish = self.root / "bootstrap-publish.json"
        packet_publish = self.root / "packet-publish.json"
        envelope = self.root / "delivery-envelope.json"
        static_output_id = "initial-static-20260730"
        packet_id = "packet-20260730"
        static_command = (
            "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "
            "root@65.109.220.59 /usr/bin/python3 -I -B /fixed/static-preparer --apply"
        )
        bootstrap_prepared = {
            "package_id": "source-package",
            "application": {"release_sha": RELEASE, "expected_alembic_revision": REVISION},
            "tooling": {"control_commit": CONTROL, "control_tree": CONTROL_TREE},
        }
        bootstrap_receipt = {
            "campaign_id": CAMPAIGN,
            "object_id": "source-package",
            "object": {
                "object_key": "campaign/artifacts/source-package.age",
                "version_id": "version-bootstrap-001",
                "ciphertext_sha256": "1" * 64,
                "ciphertext_bytes": 2048,
                "plaintext_sha256": "2" * 64,
                "plaintext_bytes": 1024,
            },
        }
        static_control = SimpleNamespace(
            static_output_id=static_output_id,
            runtime_source_root=Path("/srv/trading-bot/current"),
            static_output_directory=Path(
                "/srv/trading-bot-three-site-staging-data/webapp-fi-source-bootstrap/"
                "initial-static-assets-initial-static-20260730"
            ),
            initial_control=SimpleNamespace(campaign_binding=bound, package_id="source-package"),
        )
        packet_payload = b'{"packet":"fixture"}\n'
        packet_descriptor = {
            "object_key": "campaign/artifacts/static-provenance/packet-20260730.age",
            "version_id": "version-packet-001",
            "ciphertext_sha256": "3" * 64,
            "ciphertext_bytes": 4096,
            "plaintext_sha256": coordinator.hashlib.sha256(packet_payload).hexdigest(),
            "plaintext_bytes": len(packet_payload),
        }
        packet_control = SimpleNamespace(
            campaign_binding=bound,
            packet_id=packet_id,
            packet_path=Path("/srv/trading-bot-three-site-staging-data/controller/packets/packet-20260730.json"),
            packet_payload=packet_payload,
            transport_receipt={"object": packet_descriptor},
            candidate_directory=Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-bootstrap/fi-candidate"),
            received_directory=Path("/srv/trading-bot-three-site-staging-data/webapp-fi-source-exchange/static-provenance-packet-20260730"),
            fi_install_receipt_sha256="4" * 64,
        )

        with (
            mock.patch.object(coordinator.bootstrap, "_load_transport_policy", return_value=SimpleNamespace()),
            mock.patch.object(coordinator.bootstrap, "_verify_prepared_package", return_value=(bootstrap_prepared, {}, b"{}")),
            mock.patch.object(coordinator.bootstrap, "_read_root_only_file", return_value=b"{}"),
            mock.patch.object(coordinator.bootstrap, "_verify_generic_transport_receipt", return_value=bootstrap_receipt),
            mock.patch.object(coordinator.bootstrap, "_verify_delivery_envelope", return_value=({}, {})),
            mock.patch.object(coordinator.bootstrap, "render_receive_command") as bootstrap_render,
            mock.patch.object(coordinator.static, "build_static_preparation_control", return_value=static_control) as static_build,
            mock.patch.object(coordinator.static, "render_prepare_command", return_value=static_command) as static_render,
            mock.patch.object(coordinator.packet, "build_static_provenance_receive_control", return_value=packet_control) as packet_build,
            mock.patch.object(coordinator.packet, "render_receive_install_command") as packet_render,
            mock.patch("subprocess.run", side_effect=AssertionError("plan-only coordinator must not execute a subprocess")),
            mock.patch.object(coordinator.packet.transport, "create_s3_client", side_effect=AssertionError("plan-only coordinator must not create S3 client")),
        ):
            bootstrap_plan = coordinator.plan_bootstrap(
                campaign_binding=self.binding,
                source_role_config=self.role_config,
                fi_known_hosts=self.known_hosts,
                source_transport_config=source_transport_config,
                source_adoption_package_directory=package,
                preparation_receipt=preparation,
                transport_publish_receipt=bootstrap_publish,
                delivery_envelope=envelope,
                pinned_controller_public_key_base64="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            )
            static_plan = coordinator.plan_static(
                campaign_binding=self.binding,
                source_role_config=self.role_config,
                fi_known_hosts=self.known_hosts,
                source_transport_config=source_transport_config,
                source_adoption_package_directory=package,
                preparation_receipt=preparation,
                fi_install_receipt=fi_install,
                static_output_id=static_output_id,
            )
            packet_plan = coordinator.plan_packet(
                campaign_binding=self.binding,
                source_role_config=self.role_config,
                fi_known_hosts=self.known_hosts,
                source_transport_config=source_transport_config,
                source_adoption_package_directory=package,
                preparation_receipt=preparation,
                fi_install_receipt=fi_install,
                packet_id=packet_id,
                transport_publish_receipt=packet_publish,
            )

        plans = (bootstrap_plan, static_plan, packet_plan)
        self.assertEqual(("bootstrap", "static", "packet"), tuple(plan["stage"] for plan in plans))
        for plan in plans:
            self.assertEqual(CAMPAIGN, plan["campaign"]["campaign_id"])
            self.assertEqual({"host": "65.109.220.59", "pinned": True}, plan["fi_host"])
            self.assertFalse(plan["object_storage_changed"])
            self.assertFalse(plan["ssh_changed"])
            self.assertFalse(plan["docker_changed"])
            self.assertFalse(plan["service_changed"])
            self.assertFalse(plan["current_changed"])
            self.assertFalse(plan["container_changed"])
            self.assertFalse(plan["volume_changed"])
            self.assertFalse(plan["application_data_changed"])
            serialized = json.dumps(plan, sort_keys=True).lower()
            self.assertNotIn("://", serialized)
            self.assertNotIn("presigned", serialized)
            self.assertNotIn("credential", serialized)
            self.assertNotIn("access_key", serialized)
            self.assertNotIn("secret", serialized)

        self.assertEqual(static_command, static_plan["static"]["rendered_control_command"])
        self.assertEqual(packet_descriptor, packet_plan["packet"]["published_object"])
        self.assertEqual("ephemeral-object-get-capability-required", bootstrap_plan["bootstrap"]["next_control"])
        self.assertEqual("ephemeral-object-get-capability-required", packet_plan["packet"]["next_control"])
        self.assertEqual(self.known_hosts, static_render.call_args.kwargs["fi_known_hosts"])
        self.assertEqual(static_output_id, static_build.call_args.kwargs["static_output_id"])
        self.assertEqual(packet_id, packet_build.call_args.kwargs["packet_id"])
        bootstrap_render.assert_not_called()
        packet_render.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
