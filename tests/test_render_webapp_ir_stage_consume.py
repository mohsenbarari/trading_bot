#!/usr/bin/env python3
"""Focused tests for the transient normal WA-IR stage renderer."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render_webapp_ir_stage_consume.py"
SPEC = importlib.util.spec_from_file_location("render_webapp_ir_stage_consume", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
renderer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = renderer
SPEC.loader.exec_module(renderer)


RELEASE_SHA = "2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5"
BUNDLE_ID = "20260730T120000Z-1234567890abcdef12345678"
BOOTSTRAP_CANDIDATE = (
    "/srv/trading-bot-three-site-staging-data/wa-ir-bootstrap/received-"
    "5dec9be6d5fd79f096b8f8f68e0dbd4bb6b6eead-20260730T110000Z-abcdef123456abcdef123456"
)


def write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


class NormalStageConsumeRendererTests(unittest.TestCase):
    def _consumer_config(self, root: Path) -> Path:
        path = root / "consumer.json"
        payload = {
            "schema": renderer.stage.CONFIG_SCHEMA,
            "endpoint": "https://s3.ir-thr-at1.arvanstorage.ir",
            "region": "ir-thr-at1",
            "bucket": "private-stage-bucket",
            "prefix": "campaigns/wa-ir-stage",
            "age_binary": "/usr/bin/age",
            "age_identity_file": renderer.WA_IR_BOOTSTRAP_IDENTITY_FILE,
            "workspace": "/srv/trading-bot-three-site-staging-data/wa-ir-standby/workspace",
            "source_site": "webapp_fi",
            "source_signing_public_key_base64": base64.b64encode(b"p" * 32).decode("ascii"),
            "webapp_fi_source_attestation_public_key_base64": base64.b64encode(b"f" * 32).decode("ascii"),
            "webapp_fi_controller_authorization_public_key_base64": base64.b64encode(b"c" * 32).decode("ascii"),
            "maximum_artifact_bytes": 20 * 1024 * 1024 * 1024,
        }
        write_private(path, json.dumps(payload, sort_keys=True).encode("utf-8"))
        return path

    def _receipt(self) -> tuple[dict[str, object], str]:
        base = "/".join(
            (
                "campaigns/wa-ir-stage",
                "release-artifacts",
                "v1",
                "webapp_fi",
                "webapp_ir",
                RELEASE_SHA,
                BUNDLE_ID,
            )
        )
        version_id = "version-001"
        manifest_key = base + "/manifest.json.age"
        url = (
            "https://s3.ir-thr-at1.arvanstorage.ir/private-stage-bucket/"
            + manifest_key
            + "?versionId="
            + version_id
            + "&X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=example&X-Amz-Signature=signature"
        )
        artifacts = []
        for name in renderer.EXPECTED_ARTIFACT_NAMES:
            artifacts.append(
                {
                    "name": name,
                    "sha256": "a" * 64,
                    "bytes": 42,
                    "object_key": base + "/artifacts/" + name + ".age",
                    "version_id": "version-" + name,
                    "ciphertext_sha256": "b" * 64,
                    "ciphertext_bytes": 58,
                    "bindings": {},
                }
            )
        return (
            {
                "schema": renderer.stage.PUBLISH_RECEIPT_SCHEMA,
                "status": "published",
                "source_site": "webapp_fi",
                "destination_site": "webapp_ir",
                "release_sha": RELEASE_SHA,
                "bundle_id": BUNDLE_ID,
                "published_at": "2026-07-30T12:00:00Z",
                "artifacts": artifacts,
                "manifest": {
                    "object_key": manifest_key,
                    "version_id": version_id,
                    "ciphertext_sha256": "c" * 64,
                    "ciphertext_bytes": 99,
                    "presigned_url": url,
                },
            },
            url,
        )

    def _render(self, root: Path, receipt: dict[str, object] | None = None) -> tuple[str, str]:
        config = self._consumer_config(root)
        payload, url = self._receipt() if receipt is None else (receipt, str(receipt["manifest"]["presigned_url"]))  # type: ignore[index]
        command = renderer.render_consume_command(
            publish_receipt_bytes=json.dumps(payload, sort_keys=True).encode("utf-8"),
            consumer_config=config,
            bootstrap_candidate=BOOTSTRAP_CANDIDATE,
            staging_root=renderer.WA_IR_STAGING_ROOT,
            expected_release_sha=RELEASE_SHA,
        )
        return command, url

    def test_renders_one_strict_ssh_command_with_url_as_final_remote_argument(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wa-ir-normal-render-") as temporary:
            command, url = self._render(Path(temporary))
            outer = shlex.split(command)
            self.assertEqual(
                ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", renderer.REMOTE_HOST],
                outer[:-1],
            )
            remote = shlex.split(outer[-1])
            self.assertEqual("/usr/bin/python3", remote[0])
            self.assertEqual(
                BOOTSTRAP_CANDIDATE + "/scripts/manage_webapp_ir_artifact_stage.py",
                remote[3],
            )
            self.assertIn(BOOTSTRAP_CANDIDATE + "/config/consumer.json", remote)
            self.assertEqual(["--manifest-url", url], remote[-2:])
            self.assertNotIn(url, " ".join(remote[:-1]))
            self.assertNotIn("scp", command)
            self.assertNotIn("rsync", command)

    def test_rejects_url_not_bound_to_the_exact_manifest_version(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wa-ir-normal-render-") as temporary:
            receipt, url = self._receipt()
            receipt["manifest"] = dict(receipt["manifest"])  # type: ignore[arg-type,index]
            receipt["manifest"]["presigned_url"] = url.replace("versionId=version-001", "versionId=other")  # type: ignore[index]
            with self.assertRaisesRegex(renderer.NormalStageRenderError, "not safely bound"):
                self._render(Path(temporary), receipt)
            receipt, url = self._receipt()
            receipt["manifest"] = dict(receipt["manifest"])  # type: ignore[arg-type,index]
            receipt["manifest"]["presigned_url"] = url + "&AWSAccessKeyId=legacy&Signature=legacy&Expires=1"  # type: ignore[index]
            with self.assertRaisesRegex(renderer.NormalStageRenderError, "exactly one signed-request envelope"):
                self._render(Path(temporary), receipt)

    def test_rejects_wrong_release_artifact_set_and_remote_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wa-ir-normal-render-") as temporary:
            root = Path(temporary)
            config = self._consumer_config(root)
            receipt, _url = self._receipt()
            receipt["artifacts"] = receipt["artifacts"][:-1]  # type: ignore[index]
            with self.assertRaisesRegex(renderer.NormalStageRenderError, "artifact set"):
                renderer.render_consume_command(
                    publish_receipt_bytes=json.dumps(receipt).encode("utf-8"),
                    consumer_config=config,
                    bootstrap_candidate=BOOTSTRAP_CANDIDATE,
                    staging_root=renderer.WA_IR_STAGING_ROOT,
                    expected_release_sha=RELEASE_SHA,
                )
            receipt, _url = self._receipt()
            with self.assertRaisesRegex(renderer.NormalStageRenderError, "bootstrap namespace"):
                renderer.render_consume_command(
                    publish_receipt_bytes=json.dumps(receipt).encode("utf-8"),
                    consumer_config=config,
                    bootstrap_candidate="/tmp/untrusted-candidate",
                    staging_root=renderer.WA_IR_STAGING_ROOT,
                    expected_release_sha=RELEASE_SHA,
                )
            with self.assertRaisesRegex(renderer.NormalStageRenderError, "artifact-stage namespace"):
                renderer.render_consume_command(
                    publish_receipt_bytes=json.dumps(receipt).encode("utf-8"),
                    consumer_config=config,
                    bootstrap_candidate=BOOTSTRAP_CANDIDATE,
                    staging_root="/srv/other",
                    expected_release_sha=RELEASE_SHA,
                )

    def test_rejects_duplicate_json_keys_and_non_private_consumer_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wa-ir-normal-render-") as temporary:
            root = Path(temporary)
            config = self._consumer_config(root)
            duplicate = b'{"schema":"one","schema":"two"}'
            with self.assertRaisesRegex(renderer.NormalStageRenderError, "duplicate JSON keys"):
                renderer.render_consume_command(
                    publish_receipt_bytes=duplicate,
                    consumer_config=config,
                    bootstrap_candidate=BOOTSTRAP_CANDIDATE,
                    staging_root=renderer.WA_IR_STAGING_ROOT,
                    expected_release_sha=RELEASE_SHA,
                )
            config.chmod(0o644)
            receipt, _url = self._receipt()
            with self.assertRaisesRegex(renderer.NormalStageRenderError, "consumer config is unsafe"):
                renderer.render_consume_command(
                    publish_receipt_bytes=json.dumps(receipt).encode("utf-8"),
                    consumer_config=config,
                    bootstrap_candidate=BOOTSTRAP_CANDIDATE,
                    staging_root=renderer.WA_IR_STAGING_ROOT,
                    expected_release_sha=RELEASE_SHA,
                )

    def test_reads_a_bounded_publish_receipt_only_from_stdin_memory(self) -> None:
        payload = b'{"schema":"transient"}'
        stream = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
        with mock.patch.object(renderer.sys, "stdin", stream):
            self.assertEqual(payload, renderer._read_publish_receipt_stdin())

    def test_renderer_has_no_remote_execution_or_file_write_capability(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("subprocess", "os.system", "os.exec", "shell=True", "scp ", "rsync "):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("write_text", source)
        self.assertNotIn("write_bytes", source)
        self.assertNotIn("open(", source)


if __name__ == "__main__":
    unittest.main()
