"""Offline contracts for receiver-first PRIVATE_SHADOW rollout."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts import rollout_market_pipeline_shadow as rollout


RELEASE_SHA = "a" * 40
IMAGE_ID = "sha256:" + "b" * 64
CONTAINER_ID = "c" * 64


def _fixture_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)


def _values(role: str) -> dict[str, str]:
    return {
        "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-production",
        "MARKET_PIPELINE_RELEASE_SHA": RELEASE_SHA,
        "MARKET_PIPELINE_IMAGE": IMAGE_ID,
        "MARKET_PIPELINE_MODE": "live",
        "MARKET_PIPELINE_FEED_MODE": "PRIVATE_SHADOW",
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "0",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": "PRIVATE_SHADOW",
        "MARKET_PRIVATE_BIND_IP": "10.240.1.10" if role == "bot" else "10.240.1.20",
    }


class RolloutMarketPipelineShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="market-rollout-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.env = self.root / "bot.env"
        self.env.write_text("release-env\n", encoding="utf-8")
        self.env.chmod(0o600)
        self.journal = self.root / "journal.json"
        self.parent_patch = mock.patch.object(
            rollout, "_secure_parent", side_effect=_fixture_parent
        )
        self.parent_patch.start()

    def tearDown(self) -> None:
        self.parent_patch.stop()
        self.temporary.cleanup()

    def _prepared(self, role: str = "bot") -> dict[str, object]:
        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values(role)),
            mock.patch.object(rollout, "_ids", return_value=[]),
            mock.patch.object(
                rollout,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ),
        ):
            return rollout.prepare(
                role=role,
                release_root=self.root,
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
            )

    def test_prepare_has_exact_non_capture_service_order(self) -> None:
        payload = self._prepared()
        self.assertEqual(
            [row["service"] for row in payload["services"]],
            list(rollout.ROLE_SERVICES["bot"]),
        )
        self.assertFalse(payload["capture_services_started"])
        self.assertFalse(payload["product_authority_changed"])

    def test_adapter_cannot_start_before_fact_receiver(self) -> None:
        self._prepared()
        with mock.patch.object(rollout, "_validate_env", return_value=_values("bot")):
            with self.assertRaisesRegex(rollout.RolloutError, "order_violation"):
                rollout.start_service(
                    role="bot",
                    release_root=self.root,
                    env_file=self.env,
                    journal=self.journal,
                    release_sha=RELEASE_SHA,
                    image_id=IMAGE_ID,
                    service="market-store-adapter",
                )

    def test_prepare_rejects_any_prior_target_runtime(self) -> None:
        calls = 0

        def ids(_project: str, service: str, *, running: bool = False) -> list[str]:
            nonlocal calls
            calls += 1
            if service == "market-fact-receiver" and not running:
                return [CONTAINER_ID]
            return []

        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", side_effect=ids),
        ):
            with self.assertRaisesRegex(rollout.RolloutError, "separate_upgrade_gate"):
                rollout.prepare(
                    role="bot",
                    release_root=self.root,
                    env_file=self.env,
                    journal=self.journal,
                    release_sha=RELEASE_SHA,
                    image_id=IMAGE_ID,
                )
        self.assertGreater(calls, 0)

    def test_rollback_removes_only_exact_created_containers_without_volumes(self) -> None:
        payload = self._prepared()
        for index, row in enumerate(payload["services"][:2]):
            row["state"] = "healthy"
            row["container_id"] = f"{index + 1:x}" * 64
            row["created_by_release"] = True
        payload["status"] = "in_progress"
        rollout._write_journal(self.journal, payload)
        removed: set[str] = set()

        def run(arguments: list[str], *, label: str, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
            if arguments[1] == "rm":
                removed.add(arguments[-1])
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        run_mock = mock.Mock(side_effect=run)

        def ids(_project: str, service: str, *, running: bool = False) -> list[str]:
            row = next(item for item in payload["services"] if item["service"] == service)
            if row["container_id"] in removed:
                return []
            return [row["container_id"]] if row["container_id"] else []

        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", side_effect=ids),
            mock.patch.object(rollout, "_identity", return_value={}),
            mock.patch.object(rollout, "_run", run_mock),
        ):
            result = rollout.rollback(
                role="bot",
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
            )
        self.assertEqual(result["status"], "ROLLED_BACK")
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual([command[1] for command in commands], ["update", "stop", "rm"] * 2)
        self.assertFalse(any("volume" in command for command in commands))

    def test_cli_confirmation_fails_before_runtime(self) -> None:
        with mock.patch.object(rollout, "prepare") as prepare:
            with contextlib.redirect_stderr(io.StringIO()):
                code = rollout.main(
                    [
                        "prepare", "--role", "bot", "--release-root", "/srv/release",
                        "--env-file", "/srv/bot.env", "--journal", "/root/journal.json",
                        "--release-sha", RELEASE_SHA, "--image-id", IMAGE_ID,
                        "--confirm", "wrong",
                    ]
                )
        self.assertEqual(code, 1)
        prepare.assert_not_called()

    def test_no_capture_service_is_in_rollout_sequences(self) -> None:
        services = set(rollout.ROLE_SERVICES["bot"]) | set(rollout.ROLE_SERVICES["web"])
        self.assertTrue(services.isdisjoint(rollout.CAPTURE_SERVICES))
        source = Path(rollout.__file__).read_text(encoding="utf-8")
        self.assertNotIn("docker volume", source)
        self.assertNotIn("PRIVATE_PRIMARY", " ".join(services))


if __name__ == "__main__":
    unittest.main()
