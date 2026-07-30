#!/usr/bin/env python3
"""Focused unit tests for the non-retrying WA-IR seven-object controller."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_webapp_ir_seven_object_stage.py"
SPEC = importlib.util.spec_from_file_location("run_webapp_ir_seven_object_stage", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
wrapper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = wrapper
SPEC.loader.exec_module(wrapper)


CONTROL_SHA = "c" * 40
CONTROL_TREE = "d" * 40
BOOTSTRAP_ID = "20260730T120000Z-1234567890abcdef12345678"
BOOTSTRAP_URL = "https://example.invalid/private/bootstrap?versionId=bootstrap-v1&signature=bootstrap"
MANIFEST_URL = "https://example.invalid/private/manifest?versionId=manifest-v1&signature=manifest"


def publisher_config() -> SimpleNamespace:
    return SimpleNamespace(
        source_site=wrapper.SOURCE_SITE,
        endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
        region="ir-thr-at1",
        bucket="private-stage-bucket",
        prefix="campaign/wa-ir",
    )


def consumer_config() -> SimpleNamespace:
    return SimpleNamespace(
        source_site=wrapper.SOURCE_SITE,
        endpoint="https://s3.ir-thr-at1.arvanstorage.ir",
        region="ir-thr-at1",
        bucket="private-stage-bucket",
        prefix="campaign/wa-ir",
        age_binary="/usr/bin/age",
        age_identity_file=Path("/etc/trading-bot-three-site/wa-ir/artifact-stage-2c08.agekey"),
        workspace=Path("/srv/trading-bot-three-site-staging-data/wa-ir-standby/workspace"),
        source_signing_public_key=b"p" * 32,
        webapp_fi_source_attestation_public_key=b"f" * 32,
        webapp_fi_controller_authorization_public_key=b"c" * 32,
        maximum_artifact_bytes=20 * 1024 * 1024 * 1024,
    )


def prepared_bootstrap_config(*, consumer: SimpleNamespace | None = None) -> dict[str, object]:
    consumer = consumer or consumer_config()
    return {
        "schema": wrapper.stage.CONFIG_SCHEMA,
        "endpoint": consumer.endpoint,
        "region": consumer.region,
        "bucket": consumer.bucket,
        "prefix": consumer.prefix,
        "age_binary": consumer.age_binary,
        "age_identity_file": str(consumer.age_identity_file),
        "workspace": str(consumer.workspace),
        "source_site": consumer.source_site,
        "source_signing_public_key_base64": base64.b64encode(consumer.source_signing_public_key).decode("ascii"),
        "webapp_fi_source_attestation_public_key_base64": base64.b64encode(
            consumer.webapp_fi_source_attestation_public_key
        ).decode("ascii"),
        "webapp_fi_controller_authorization_public_key_base64": base64.b64encode(
            consumer.webapp_fi_controller_authorization_public_key
        ).decode("ascii"),
        "maximum_artifact_bytes": consumer.maximum_artifact_bytes,
    }


def prepared_bootstrap_package(*, consumer: SimpleNamespace | None = None) -> dict[str, object]:
    return {"consumer_config": prepared_bootstrap_config(consumer=consumer)}


def bootstrap_receipt(url: str = BOOTSTRAP_URL) -> dict[str, object]:
    return {
        "schema": wrapper.stage.BOOTSTRAP_PUBLISH_RECEIPT_SCHEMA,
        "status": "published",
        "source_site": wrapper.SOURCE_SITE,
        "destination_site": wrapper.DESTINATION_SITE,
        "control_commit": CONTROL_SHA,
        "control_tree": CONTROL_TREE,
        "bootstrap_id": BOOTSTRAP_ID,
        "published_at": "2026-07-30T12:00:00Z",
        "bootstrap": {
            "object_key": "campaign/wa-ir/bootstrap/artifact.age",
            "version_id": "bootstrap-v1",
            "ciphertext_sha256": "a" * 64,
            "ciphertext_bytes": 71,
            "plaintext_sha256": "b" * 64,
            "plaintext_bytes": 55,
            "manifest_sha256": "e" * 64,
            "preparation_receipt_sha256": "f" * 64,
            "presigned_url": url,
        },
    }


def normal_receipt(url: str = MANIFEST_URL, *, names: tuple[str, ...] = wrapper.EXPECTED_NORMAL_ARTIFACTS) -> dict[str, object]:
    artifacts = [
        {
            "name": name,
            "sha256": "1" * 64,
            "bytes": 42,
            "object_key": "campaign/wa-ir/release/artifacts/" + name + ".age",
            "version_id": "version-" + name,
            "ciphertext_sha256": "2" * 64,
            "ciphertext_bytes": 57,
            "bindings": {},
        }
        for name in names
    ]
    return {
        "schema": wrapper.stage.PUBLISH_RECEIPT_SCHEMA,
        "status": "published",
        "source_site": wrapper.SOURCE_SITE,
        "destination_site": wrapper.DESTINATION_SITE,
        "release_sha": wrapper.EXPECTED_APPLICATION_RELEASE_SHA,
        "bundle_id": "20260730T120001Z-abcdefabcdefabcdefabcdef",
        "published_at": "2026-07-30T12:00:01Z",
        "artifacts": artifacts,
        "manifest": {
            "object_key": "campaign/wa-ir/release/manifest.json.age",
            "version_id": "manifest-v1",
            "ciphertext_sha256": "3" * 64,
            "ciphertext_bytes": 91,
            "presigned_url": url,
        },
    }


def normal_inputs(*, control_sha: str = CONTROL_SHA) -> list[object]:
    release = wrapper.EXPECTED_APPLICATION_RELEASE_SHA
    common = {"release_sha": release}
    return [
        wrapper.stage.ArtifactInput(
            name="control-release-bundle",
            path=Path("/prepared/control-release-bundle"),
            bindings={**common, "control_release_sha": control_sha},
        ),
        wrapper.stage.ArtifactInput(
            name="image-bundle",
            path=Path("/prepared/image-bundle"),
            bindings=common,
        ),
        wrapper.stage.ArtifactInput(
            name="image-manifest",
            path=Path("/prepared/image-manifest"),
            bindings=common,
        ),
        wrapper.stage.ArtifactInput(
            name="release-bundle",
            path=Path("/prepared/release-bundle"),
            bindings=common,
        ),
        wrapper.stage.ArtifactInput(
            name="release-provenance",
            path=Path("/prepared/release-provenance"),
            bindings={"application_release_sha": release, "control_release_sha": control_sha},
        ),
    ]


def rendered_command(url: str) -> str:
    remote = "remote-control -- " + shlex.quote(url)
    return shlex.join(
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            wrapper.bootstrap_renderer.REMOTE_HOST,
            remote,
        ]
    )


class SevenObjectStageControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bootstrap_verifier = mock.Mock(return_value=prepared_bootstrap_package())
        self.bootstrap_preparer = mock.patch.object(
            wrapper,
            "_load_bootstrap_preparer",
            return_value=SimpleNamespace(
                verify_prepared_bootstrap_package=self.bootstrap_verifier,
            ),
        )
        self.bootstrap_preparer.start()
        self.addCleanup(self.bootstrap_preparer.stop)

    def _common_patches(self, *, parsed: list[object] | None = None):
        artifacts = normal_inputs() if parsed is None else parsed
        return (
            mock.patch.object(wrapper.os, "geteuid", return_value=0),
            mock.patch.object(wrapper.stage, "load_publisher_config", return_value=publisher_config()),
            mock.patch.object(wrapper.stage, "load_consumer_config", return_value=consumer_config()),
            mock.patch.object(wrapper.stage, "_publisher_public_key", return_value=b"p" * 32),
            mock.patch.object(wrapper.stage, "parse_artifact_specifications", return_value=artifacts),
            mock.patch.object(wrapper.stage, "apply_artifact_bindings", side_effect=lambda parsed, _bindings: parsed),
        )

    def _run(self, **overrides: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "publisher_config_path": Path("/root/private/publisher.json"),
            "consumer_config_path": Path("/root/private/consumer.json"),
            "bootstrap_package_directory": Path("/srv/stage/bootstrap-package"),
            "bootstrap_preparation_receipt": Path("/srv/stage/bootstrap-package/bootstrap-preparation-receipt.json"),
            "artifact_specs": ["release-bundle=/prepared/release-bundle"],
            "binding_specs": ["release-bundle=release_sha=" + wrapper.EXPECTED_APPLICATION_RELEASE_SHA],
        }
        arguments.update(overrides)
        return wrapper.run_stage(**arguments)  # type: ignore[arg-type]

    def test_default_main_does_not_load_config_or_contact_any_host(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(wrapper, "run_stage") as run_stage,
            mock.patch("sys.stdout", output),
        ):
            self.assertEqual(0, wrapper.main([]))
        run_stage.assert_not_called()
        self.assertEqual({"object_count": 0, "status": "not_applied"}, json.loads(output.getvalue()))

    def test_success_uses_memory_receipts_and_returns_url_free_evidence(self) -> None:
        events: list[tuple[str, object]] = []
        runner_arguments: list[list[str]] = []

        def ssh_runner(arguments):
            runner_arguments.append(list(arguments))
            return subprocess.CompletedProcess(list(arguments), 0)

        root, publisher, consumer, public_key, parser, binder = self._common_patches()
        with (
            root,
            publisher,
            consumer,
            public_key,
            parser,
            binder,
            mock.patch.object(wrapper.stage, "create_s3_client", return_value=object()) as create_client,
            mock.patch.object(
                wrapper.stage,
                "publish_bootstrap_package",
                side_effect=lambda *args, **kwargs: events.append(("bootstrap", kwargs)) or bootstrap_receipt(),
            ) as publish_bootstrap,
            mock.patch.object(
                wrapper.bootstrap_renderer,
                "render_receive_command",
                side_effect=lambda **kwargs: events.append(("bootstrap-render", kwargs)) or rendered_command(BOOTSTRAP_URL),
            ),
            mock.patch.object(
                wrapper.stage,
                "publish_bundle",
                side_effect=lambda *args, **kwargs: events.append(("normal", kwargs)) or normal_receipt(),
            ) as publish_normal,
            mock.patch.object(
                wrapper.normal_renderer,
                "render_consume_command",
                side_effect=lambda **kwargs: events.append(("normal-render", kwargs)) or rendered_command(MANIFEST_URL),
            ),
        ):
            evidence = self._run(ssh_runner=ssh_runner)

        self.assertEqual(1, create_client.call_count)
        self.assertEqual(1, publish_bootstrap.call_count)
        self.assertEqual(1, publish_normal.call_count)
        self.assertEqual(["bootstrap", "bootstrap-render", "normal", "normal-render"], [name for name, _ in events])
        self.assertEqual(3, len(runner_arguments))
        self.assertTrue(all(arguments[0] == "ssh" for arguments in runner_arguments))
        self.assertTrue(all(arguments[-1] for arguments in runner_arguments))
        self.assertIn("install -d -o root -g root -m 700", runner_arguments[0][-1])
        self.assertNotIn(BOOTSTRAP_URL, runner_arguments[0][-1])
        self.assertNotIn(MANIFEST_URL, runner_arguments[0][-1])
        self.assertEqual(7, evidence["object_count"])
        rendered_evidence = json.dumps(evidence, sort_keys=True)
        self.assertNotIn(BOOTSTRAP_URL, rendered_evidence)
        self.assertNotIn(MANIFEST_URL, rendered_evidence)
        bootstrap_renderer_arguments = events[1][1]
        normal_renderer_arguments = events[3][1]
        self.assertIn(BOOTSTRAP_URL.encode("utf-8"), bootstrap_renderer_arguments["publish_receipt_bytes"])
        self.assertIn(MANIFEST_URL.encode("utf-8"), normal_renderer_arguments["publish_receipt_bytes"])
        self.assertEqual(
            wrapper.BOOTSTRAP_ROOT + "/received-" + CONTROL_SHA + "-" + BOOTSTRAP_ID,
            normal_renderer_arguments["bootstrap_candidate"],
        )

    def test_invalid_normal_input_stops_before_client_or_bootstrap_publish(self) -> None:
        root, publisher, consumer, public_key, parser, binder = self._common_patches(parsed=normal_inputs()[:-1])
        with (
            root,
            publisher,
            consumer,
            public_key,
            parser,
            binder,
            mock.patch.object(wrapper.stage, "create_s3_client") as create_client,
            mock.patch.object(wrapper.stage, "publish_bootstrap_package") as publish_bootstrap,
        ):
            with self.assertRaisesRegex(wrapper.SevenObjectStageError, "exactly the five"):
                self._run()
        create_client.assert_not_called()
        publish_bootstrap.assert_not_called()

    def test_signing_key_mismatch_stops_before_client_or_bootstrap_publish(self) -> None:
        mismatched_consumer = consumer_config()
        mismatched_consumer.source_signing_public_key = b"q" * 32
        with (
            mock.patch.object(wrapper.os, "geteuid", return_value=0),
            mock.patch.object(wrapper.stage, "load_publisher_config", return_value=publisher_config()),
            mock.patch.object(wrapper.stage, "load_consumer_config", return_value=mismatched_consumer),
            mock.patch.object(wrapper.stage, "_publisher_public_key", return_value=b"p" * 32),
            mock.patch.object(wrapper.stage, "parse_artifact_specifications", return_value=normal_inputs()),
            mock.patch.object(wrapper.stage, "apply_artifact_bindings", side_effect=lambda parsed, _bindings: parsed),
            mock.patch.object(wrapper.stage, "create_s3_client") as create_client,
            mock.patch.object(wrapper.stage, "publish_bootstrap_package") as publish_bootstrap,
        ):
            with self.assertRaisesRegex(wrapper.SevenObjectStageError, "does not match the pinned consumer key"):
                self._run()
        create_client.assert_not_called()
        publish_bootstrap.assert_not_called()

    def test_mismatched_prepared_bootstrap_provenance_pin_stops_before_ssh_or_s3(self) -> None:
        for field in (
            "webapp_fi_source_attestation_public_key_base64",
            "webapp_fi_controller_authorization_public_key_base64",
        ):
            with self.subTest(field=field):
                prepared = prepared_bootstrap_package()
                packaged = prepared["consumer_config"]
                assert isinstance(packaged, dict)
                packaged[field] = base64.b64encode(b"x" * 32).decode("ascii")
                self.bootstrap_verifier.return_value = prepared
                root, publisher, consumer, public_key, parser, binder = self._common_patches()
                with (
                    root,
                    publisher,
                    consumer,
                    public_key,
                    parser,
                    binder,
                    mock.patch.object(wrapper, "_prepare_bootstrap_root") as prepare_root,
                    mock.patch.object(wrapper.stage, "create_s3_client") as create_client,
                    mock.patch.object(wrapper.stage, "publish_bootstrap_package") as publish_bootstrap,
                ):
                    with self.assertRaisesRegex(wrapper.SevenObjectStageError, "provenance key pins do not match"):
                        self._run()
                prepare_root.assert_not_called()
                create_client.assert_not_called()
                publish_bootstrap.assert_not_called()

    def test_root_is_required_before_controller_configuration_is_read(self) -> None:
        with (
            mock.patch.object(wrapper.os, "geteuid", return_value=1000),
            mock.patch.object(wrapper.stage, "load_publisher_config") as load_publisher,
        ):
            with self.assertRaisesRegex(wrapper.SevenObjectStageError, "must run as root"):
                self._run()
        load_publisher.assert_not_called()

    def test_bootstrap_root_prepare_failure_stops_before_client_or_object_publish(self) -> None:
        root, publisher, consumer, public_key, parser, binder = self._common_patches()
        with (
            root,
            publisher,
            consumer,
            public_key,
            parser,
            binder,
            mock.patch.object(wrapper.stage, "create_s3_client") as create_client,
            mock.patch.object(wrapper.stage, "publish_bootstrap_package") as publish_bootstrap,
        ):
            with self.assertRaisesRegex(wrapper.SevenObjectStageError, "bootstrap root could not be prepared"):
                self._run(ssh_runner=lambda arguments: subprocess.CompletedProcess(list(arguments), 23))
        create_client.assert_not_called()
        publish_bootstrap.assert_not_called()

    def test_failed_bootstrap_ssh_stops_without_normal_publish_or_retry(self) -> None:
        root, publisher, consumer, public_key, parser, binder = self._common_patches()
        with (
            root,
            publisher,
            consumer,
            public_key,
            parser,
            binder,
            mock.patch.object(wrapper.stage, "create_s3_client", return_value=object()),
            mock.patch.object(wrapper.stage, "publish_bootstrap_package", return_value=bootstrap_receipt()) as publish_bootstrap,
            mock.patch.object(wrapper.bootstrap_renderer, "render_receive_command", return_value=rendered_command(BOOTSTRAP_URL)),
            mock.patch.object(wrapper.stage, "publish_bundle") as publish_normal,
        ):
            runner_results = iter((0, 23))
            with self.assertRaisesRegex(wrapper.SevenObjectStageError, "stage cannot continue") as error:
                self._run(
                    ssh_runner=lambda arguments: subprocess.CompletedProcess(list(arguments), next(runner_results))
                )
        self.assertEqual(1, publish_bootstrap.call_count)
        publish_normal.assert_not_called()
        self.assertEqual(1, error.exception.evidence["object_count"])
        self.assertNotIn(BOOTSTRAP_URL, json.dumps(error.exception.evidence, sort_keys=True))

    def test_malformed_normal_receipt_stops_before_normal_ssh_and_does_not_retry(self) -> None:
        calls: list[list[str]] = []
        malformed = normal_receipt(names=wrapper.EXPECTED_NORMAL_ARTIFACTS[:-1])
        root, publisher, consumer, public_key, parser, binder = self._common_patches()
        with (
            root,
            publisher,
            consumer,
            public_key,
            parser,
            binder,
            mock.patch.object(wrapper.stage, "create_s3_client", return_value=object()),
            mock.patch.object(wrapper.stage, "publish_bootstrap_package", return_value=bootstrap_receipt()),
            mock.patch.object(wrapper.bootstrap_renderer, "render_receive_command", return_value=rendered_command(BOOTSTRAP_URL)),
            mock.patch.object(wrapper.stage, "publish_bundle", return_value=malformed) as publish_normal,
            mock.patch.object(wrapper.normal_renderer, "render_consume_command") as render_normal,
        ):
            with self.assertRaisesRegex(wrapper.SevenObjectStageError, "cannot be accepted"):
                self._run(
                    ssh_runner=lambda arguments: calls.append(list(arguments)) or subprocess.CompletedProcess(list(arguments), 0)
                )
        self.assertEqual(1, publish_normal.call_count)
        render_normal.assert_not_called()
        self.assertEqual(2, len(calls))

    def test_main_drops_url_bearing_exception_text(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(
                wrapper,
                "run_stage",
                side_effect=wrapper.SevenObjectStageError(
                    "receipt https://secret.example.invalid/object",
                    evidence={
                        "presigned_url": "https://secret.example.invalid/object",
                        "nested": {"message": "seen at https://secret.example.invalid/object"},
                        "version_id": "safe-version",
                    },
                ),
            ),
            mock.patch("sys.stdout", output),
        ):
            result = wrapper.main(
                [
                    "--apply",
                    "--publisher-config",
                    "/root/private/publisher.json",
                    "--consumer-config",
                    "/root/private/consumer.json",
                    "--bootstrap-package-directory",
                    "/srv/stage/bootstrap-package",
                    "--bootstrap-preparation-receipt",
                    "/srv/stage/bootstrap-package/bootstrap-preparation-receipt.json",
                ]
            )
        self.assertEqual(2, result)
        self.assertNotIn("https://secret.example.invalid", output.getvalue())
        payload = json.loads(output.getvalue())
        self.assertEqual("SevenObjectStageError", payload["error_class"])
        self.assertEqual("safe-version", payload["evidence"]["version_id"])
        self.assertNotIn("presigned_url", payload["evidence"])

    def test_default_ssh_runner_forces_shell_false_and_discards_output(self) -> None:
        completed = subprocess.CompletedProcess(["ssh"], 0)
        with mock.patch.object(wrapper.subprocess, "run", return_value=completed) as run:
            self.assertIs(completed, wrapper._run_ssh_silently(["ssh", "root@example.invalid", "control"]))
        arguments, keywords = run.call_args
        self.assertEqual(["ssh", "root@example.invalid", "control"], arguments[0])
        self.assertFalse(keywords["shell"])
        self.assertIs(subprocess.DEVNULL, keywords["stdin"])
        self.assertIs(subprocess.DEVNULL, keywords["stdout"])
        self.assertIs(subprocess.DEVNULL, keywords["stderr"])
        self.assertEqual(wrapper.SSH_TIMEOUT_SECONDS, keywords["timeout"])

    def test_wrapper_source_has_no_direct_payload_transfer_or_shell_execution(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("scp", source)
        self.assertNotIn("rsync", source)
        self.assertNotIn("shell=True", source)


if __name__ == "__main__":
    unittest.main()
