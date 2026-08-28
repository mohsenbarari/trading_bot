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


def _values(role: str, feed_mode: str = "PRIVATE_SHADOW") -> dict[str, str]:
    primary = feed_mode == "PRIVATE_PRIMARY"
    values = {
        "MARKET_PIPELINE_PROJECT_NAME": "market-private-pipeline-production",
        "MARKET_PIPELINE_RELEASE_SHA": RELEASE_SHA,
        "MARKET_PIPELINE_IMAGE": IMAGE_ID,
        "MARKET_PIPELINE_MODE": "live",
        "MARKET_PIPELINE_FEED_MODE": feed_mode,
        "MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY": "1" if primary else "0",
        "MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE": feed_mode,
        "MARKET_PRIVATE_BIND_IP": "10.240.1.10" if role == "bot" else "10.240.1.20",
    }
    if role == "web":
        values["MARKET_WEB_DATA_ROOT"] = "/tmp/test-market-web-data"
    return values


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

    def test_primary_prepare_is_explicit_and_not_reported_as_shadow(self) -> None:
        with (
            mock.patch.object(
                rollout,
                "_validate_env",
                return_value=_values("bot", "PRIVATE_PRIMARY"),
            ),
            mock.patch.object(rollout, "_ids", return_value=[]),
            mock.patch.object(
                rollout,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ),
        ):
            payload = rollout.prepare(
                role="bot",
                release_root=self.root,
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
                feed_mode="PRIVATE_PRIMARY",
            )
        self.assertEqual(payload["feed_mode"], "PRIVATE_PRIMARY")
        self.assertFalse(payload["private_shadow_only"])
        rollout._validate_journal(
            payload,
            role="bot",
            release_sha=RELEASE_SHA,
            image_id=IMAGE_ID,
            env_sha256=rollout._sha256(self.env),
            project="market-private-pipeline-production",
            feed_mode="PRIVATE_PRIMARY",
        )

    def test_primary_receiver_live_starting_unblocks_bootstrap_but_not_pass(self) -> None:
        with (
            mock.patch.object(
                rollout,
                "_validate_env",
                return_value=_values("web", "PRIVATE_PRIMARY"),
            ),
            mock.patch.object(rollout, "_ids", return_value=[]),
            mock.patch.object(
                rollout,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ),
        ):
            payload = rollout.prepare(
                role="web",
                release_root=self.root,
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
                feed_mode="PRIVATE_PRIMARY",
            )
        row = payload["services"][0]
        row.update(
            {
                "state": "starting",
                "container_id": CONTAINER_ID,
                "created_by_release": True,
            }
        )
        payload["status"] = "in_progress"
        rollout._write_journal(self.journal, payload)
        with (
            mock.patch.object(
                rollout,
                "_validate_env",
                return_value=_values("web", "PRIVATE_PRIMARY"),
            ),
            mock.patch.object(rollout, "_ids", return_value=[CONTAINER_ID]),
            mock.patch.object(
                rollout,
                "_identity",
                return_value={"running": True, "healthy": False},
            ),
            mock.patch.object(rollout, "_primary_bootstrap_ready", return_value=True),
        ):
            result = rollout.start_service(
                role="web",
                release_root=self.root,
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
                service="estimator-snapshot-receiver",
                feed_mode="PRIVATE_PRIMARY",
            )
        self.assertEqual(result["services"][0]["state"], "bootstrap_ready")
        self.assertEqual(result["status"], "in_progress")

    def test_primary_receiver_is_revisited_after_sender_and_promotes_to_pass(self) -> None:
        with (
            mock.patch.object(
                rollout,
                "_validate_env",
                return_value=_values("web", "PRIVATE_PRIMARY"),
            ),
            mock.patch.object(rollout, "_ids", return_value=[]),
            mock.patch.object(
                rollout,
                "_run",
                return_value=subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            ),
        ):
            payload = rollout.prepare(
                role="web",
                release_root=self.root,
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
                feed_mode="PRIVATE_PRIMARY",
            )
        for index, row in enumerate(payload["services"]):
            row["state"] = "bootstrap_ready" if index == 0 else "healthy"
            row["container_id"] = f"{index + 1:x}" * 64
            row["created_by_release"] = True
        payload["status"] = "in_progress"
        rollout._write_journal(self.journal, payload)
        receiver_id = payload["services"][0]["container_id"]
        with (
            mock.patch.object(
                rollout,
                "_validate_env",
                return_value=_values("web", "PRIVATE_PRIMARY"),
            ),
            mock.patch.object(rollout, "_ids", return_value=[receiver_id]),
            mock.patch.object(
                rollout,
                "_identity",
                return_value={"running": True, "healthy": True},
            ),
        ):
            result = rollout.start_service(
                role="web",
                release_root=self.root,
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
                service="estimator-snapshot-receiver",
                feed_mode="PRIVATE_PRIMARY",
            )
        self.assertEqual(result["services"][0]["state"], "healthy")
        self.assertEqual(result["status"], "PASS")

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

    def test_interrupted_legacy_pending_owner_can_be_rolled_back(self) -> None:
        payload = self._prepared()
        row = payload["services"][0]
        row.update(
            {
                "state": "pending",
                "container_id": CONTAINER_ID,
                "created_by_release": True,
            }
        )
        payload["status"] = "in_progress"
        rollout._write_journal(self.journal, payload)
        removed = False

        def ids(_project: str, service: str, *, running: bool = False) -> list[str]:
            del running
            return [] if removed or service != row["service"] else [CONTAINER_ID]

        def run(arguments: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
            nonlocal removed
            if arguments[1] == "rm":
                removed = True
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", side_effect=ids),
            mock.patch.object(rollout, "_identity", return_value={}),
            mock.patch.object(rollout, "_run", side_effect=run),
        ):
            result = rollout.rollback(
                role="bot",
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
            )
        self.assertTrue(removed)
        self.assertEqual(result["status"], "ROLLED_BACK")

    def test_create_intent_is_durable_before_compose_and_can_resume_without_owner(
        self,
    ) -> None:
        self._prepared()
        compose_attempts = 0
        owner_present = False

        def ids(
            _project: str, service: str, *, running: bool = False
        ) -> list[str]:
            del running
            if service != "market-fact-receiver" or not owner_present:
                return []
            return [CONTAINER_ID]

        def run(
            _arguments: list[str], *, label: str, allow_failure: bool = False
        ) -> subprocess.CompletedProcess[str]:
            nonlocal compose_attempts, owner_present
            del allow_failure
            self.assertEqual(label, "rollout_service_start")
            compose_attempts += 1
            if compose_attempts == 1:
                raise RuntimeError("synthetic_crash_before_create")
            owner_present = True
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        common = {
            "role": "bot",
            "release_root": self.root,
            "env_file": self.env,
            "journal": self.journal,
            "release_sha": RELEASE_SHA,
            "image_id": IMAGE_ID,
            "service": "market-fact-receiver",
        }
        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", side_effect=ids),
            mock.patch.object(rollout, "_run", side_effect=run),
            mock.patch.object(
                rollout,
                "_identity",
                return_value={"running": True, "healthy": True},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "crash_before_create"):
                rollout.start_service(**common)
            interrupted = rollout._read_journal(self.journal)
            self.assertEqual(
                interrupted["services"][0]["state"], "create_prepared"
            )
            self.assertIsNone(interrupted["services"][0]["container_id"])
            result = rollout.start_service(**common)

        self.assertEqual(compose_attempts, 2)
        self.assertEqual(result["services"][0]["container_id"], CONTAINER_ID)
        self.assertEqual(result["services"][0]["state"], "healthy")

    def test_sigkill_after_create_before_identity_wal_adopts_exact_owner(
        self,
    ) -> None:
        self._prepared()
        owner_present = False
        crash_after_create = True

        def ids(
            _project: str, service: str, *, running: bool = False
        ) -> list[str]:
            nonlocal crash_after_create
            del running
            if service != "market-fact-receiver" or not owner_present:
                return []
            if crash_after_create:
                crash_after_create = False
                raise RuntimeError("synthetic_sigkill_after_create")
            return [CONTAINER_ID]

        def run(
            _arguments: list[str], *, label: str, allow_failure: bool = False
        ) -> subprocess.CompletedProcess[str]:
            nonlocal owner_present
            del allow_failure
            self.assertEqual(label, "rollout_service_start")
            owner_present = True
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        common = {
            "role": "bot",
            "release_root": self.root,
            "env_file": self.env,
            "journal": self.journal,
            "release_sha": RELEASE_SHA,
            "image_id": IMAGE_ID,
            "service": "market-fact-receiver",
        }
        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", side_effect=ids),
            mock.patch.object(rollout, "_run", side_effect=run),
            mock.patch.object(
                rollout,
                "_identity",
                return_value={"running": True, "healthy": True},
            ) as identity,
        ):
            with self.assertRaisesRegex(RuntimeError, "sigkill_after_create"):
                rollout.start_service(**common)
            interrupted = rollout._read_journal(self.journal)
            self.assertEqual(
                interrupted["services"][0]["state"], "create_prepared"
            )
            result = rollout.start_service(**common)

        self.assertEqual(result["services"][0]["container_id"], CONTAINER_ID)
        self.assertEqual(result["services"][0]["state"], "healthy")
        identity.assert_called()

    def test_create_intent_rejects_an_unbound_owner_fail_closed(self) -> None:
        payload = self._prepared()
        payload["status"] = "in_progress"
        payload["services"][0]["state"] = "create_prepared"
        rollout._write_journal(self.journal, payload)

        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", return_value=[CONTAINER_ID]),
            mock.patch.object(
                rollout,
                "_identity",
                side_effect=rollout.RolloutError(
                    "rollout_container_identity_mismatch"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                rollout.RolloutError, "container_identity_mismatch"
            ):
                rollout.start_service(
                    role="bot",
                    release_root=self.root,
                    env_file=self.env,
                    journal=self.journal,
                    release_sha=RELEASE_SHA,
                    image_id=IMAGE_ID,
                    service="market-fact-receiver",
                )
        self.assertEqual(
            rollout._read_journal(self.journal)["services"][0]["state"],
            "create_prepared",
        )

    def test_rollback_recovers_exact_owner_created_after_create_intent(self) -> None:
        payload = self._prepared()
        payload["status"] = "in_progress"
        payload["services"][0]["state"] = "create_prepared"
        rollout._write_journal(self.journal, payload)
        owner_present = True

        def ids(
            _project: str, service: str, *, running: bool = False
        ) -> list[str]:
            del running
            if service != "market-fact-receiver" or not owner_present:
                return []
            return [CONTAINER_ID]

        def run(
            arguments: list[str], *, label: str, allow_failure: bool = False
        ) -> subprocess.CompletedProcess[str]:
            nonlocal owner_present
            del label, allow_failure
            if arguments[1] == "rm":
                owner_present = False
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")

        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", side_effect=ids),
            mock.patch.object(rollout, "_identity", return_value={}),
            mock.patch.object(rollout, "_run", side_effect=run),
        ):
            result = rollout.rollback(
                role="bot",
                env_file=self.env,
                journal=self.journal,
                release_sha=RELEASE_SHA,
                image_id=IMAGE_ID,
            )

        self.assertFalse(owner_present)
        self.assertEqual(result["status"], "ROLLED_BACK")
        self.assertFalse(result["rollback_state_deleted"])
        self.assertTrue(
            all(row["state"] == "rolled_back" for row in result["services"])
        )

    def test_rollback_rejects_an_untracked_owner_fail_closed(self) -> None:
        payload = self._prepared()

        def ids(
            _project: str, service: str, *, running: bool = False
        ) -> list[str]:
            del running
            return [CONTAINER_ID] if service == "market-store-adapter" else []

        with (
            mock.patch.object(rollout, "_validate_env", return_value=_values("bot")),
            mock.patch.object(rollout, "_ids", side_effect=ids),
        ):
            with self.assertRaisesRegex(
                rollout.RolloutError, "rollback_untracked_owner"
            ):
                rollout.rollback(
                    role="bot",
                    env_file=self.env,
                    journal=self.journal,
                    release_sha=RELEASE_SHA,
                    image_id=IMAGE_ID,
                )

        result = rollout._read_journal(self.journal)
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["services"][1]["state"], "pending")

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

    def test_primary_cli_requires_distinct_confirmation(self) -> None:
        with mock.patch.object(rollout, "prepare") as prepare:
            with contextlib.redirect_stderr(io.StringIO()):
                code = rollout.main(
                    [
                        "prepare", "--role", "bot", "--release-root", "/srv/release",
                        "--env-file", "/srv/bot.env", "--journal", "/root/journal.json",
                        "--release-sha", RELEASE_SHA, "--image-id", IMAGE_ID,
                        "--feed-mode", "PRIVATE_PRIMARY",
                        "--confirm", rollout.CONFIRMATION,
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
