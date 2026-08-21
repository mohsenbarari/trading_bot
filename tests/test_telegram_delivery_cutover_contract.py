import unittest

from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import shlex
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

import yaml

from core.telegram_delivery_cutover_contract import (
    api_env_updates,
    api_process_contract,
    bot_env_updates,
    bot_process_contract,
    executor_count,
    executor_overlap_forbidden,
    expected_channel_id_updates,
    missing_required_env,
    present_forbidden_tokens,
    upsert_env_lines,
)
from core.telegram_gateway import TelegramGatewayResult
from core.services.telegram_offer_publication_service import (
    initial_telegram_publication_publisher_identity,
)
from scripts.cutover_telegram_delivery_queue_staging import (
    API_SURFACES,
    EXPECTED_QUEUE_IDENTITIES,
    FOREIGN_BOT_CONTAINER,
    FOREIGN_STAGING_PROJECT,
    StagingCutoverError,
    _assert_quiesced_snapshot,
    _credential_fingerprint,
    _provider_fingerprint,
    _require_clean_pushed_main,
    _username_fingerprint,
    _validated_provider_preflight_result,
    api_runtime_evidence_from_reports,
    apply_cutover,
    executor_inventory_from_observation,
    publisher_runtime_evidence_from_observation,
    redeploy_queue_v1,
)
from scripts import cutover_telegram_delivery_queue_staging as staging_cutover


class TelegramDeliveryCutoverContractTests(unittest.TestCase):
    @staticmethod
    def _descendant_program(group_path: Path, marker_path: Path) -> str:
        return (
            "import os, pathlib, signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"pathlib.Path({str(group_path)!r}).write_text(str(os.getpgrp())); "
            "time.sleep(2); "
            f"pathlib.Path({str(marker_path)!r}).write_text('orphan-ran')"
        )

    @staticmethod
    def _kill_test_group_if_present(group_path: Path) -> None:
        if not group_path.is_file():
            return
        process_group_id = int(group_path.read_text(encoding="utf-8"))
        if staging_cutover._process_group_exists(process_group_id):
            try:
                os.killpg(process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass

    @staticmethod
    def _executor_record(
        *,
        owner: str = "queue-v1",
        process_ids: tuple[int, ...] = (101,),
    ):
        queue_enabled = owner == "queue-v1"
        return {
            "name": FOREIGN_BOT_CONTAINER,
            "project": FOREIGN_STAGING_PROJECT,
            "service": "bot",
            "environment": "staging",
            "scope": "staging",
            "bot_process_count": len(process_ids),
            "_process_ids": set(process_ids),
            "runtime_env": {
                "TRADING_BOT_SERVICE": "bot",
                "SERVER_MODE": "foreign",
                "TELEGRAM_DELIVERY_EXECUTION_OWNER": owner,
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": str(
                    queue_enabled
                ).lower(),
            },
            "runtime_decision": {
                "mode": owner,
                "legacy_workers_enabled": not queue_enabled,
                "queue_worker_enabled": queue_enabled,
            },
        }

    @staticmethod
    def _publisher_values():
        values = {
            **bot_env_updates(),
            "BOT_TOKEN": "central-secret-token",
            "BOT_USERNAME": "central_staging_bot",
            "CHANNEL_ID": "-100555",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100555",
            "TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID": "1000",
            "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED": "false",
        }
        for index in range(1, 6):
            values.update(
                {
                    f"TELEGRAM_PUBLISHER_{index}_ENABLED": "true",
                    f"TELEGRAM_PUBLISHER_{index}_BOT_TOKEN": f"publisher-secret-{index}",
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_BOT_ID": str(
                        1000 + index
                    ),
                    f"TELEGRAM_PUBLISHER_{index}_EXPECTED_USERNAME": (
                        f"publisher_{index}_staging_bot"
                    ),
                }
            )
        return values

    @classmethod
    def _provider_report(cls, values):
        identities = []
        permissions = {
            "primary": [
                "can_manage_chat",
                "can_post_messages",
                "can_edit_messages",
                "can_restrict_members",
            ]
        }
        permissions.update(
            {
                f"publisher_{index}": [
                    "can_manage_chat",
                    "can_post_messages",
                    "can_edit_messages",
                    "can_delete_messages",
                ]
                for index in range(1, 6)
            }
        )
        channel_fingerprint = _provider_fingerprint("channel", -100555)
        for index, identity in enumerate(EXPECTED_QUEUE_IDENTITIES):
            if identity == "primary":
                token = values["BOT_TOKEN"]
                bot_id = int(
                    values["TELEGRAM_DELIVERY_QUEUE_EXPECTED_PRIMARY_BOT_ID"]
                )
                username = values["BOT_USERNAME"]
            else:
                publisher_index = index
                token = values[f"TELEGRAM_PUBLISHER_{publisher_index}_BOT_TOKEN"]
                bot_id = int(
                    values[
                        f"TELEGRAM_PUBLISHER_{publisher_index}_EXPECTED_BOT_ID"
                    ]
                )
                username = values[
                    f"TELEGRAM_PUBLISHER_{publisher_index}_EXPECTED_USERNAME"
                ]
            identities.append(
                {
                    "bot_identity": identity,
                    "credential_fingerprint": _credential_fingerprint(token),
                    "bot_fingerprint": _provider_fingerprint("bot", bot_id),
                    "username_fingerprint": _username_fingerprint(username),
                    "channel_fingerprint": channel_fingerprint,
                    "member_status": "administrator",
                    "effective_permissions": permissions[identity],
                }
            )
        return {
            "status": "approved",
            "identity_count": 6,
            "approved_bot_identities": list(EXPECTED_QUEUE_IDENTITIES),
            "identities": identities,
            "read_only_provider_call_count": 18,
            "sensitive_values_disclosed": False,
        }

    def test_api_contract_rejects_queue_worker_and_tokens(self):
        contract = api_process_contract()
        self.assertEqual(
            missing_required_env(
                {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                },
                contract,
            ),
            (
                "TELEGRAM_DELIVERY_EXECUTION_OWNER",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED",
                "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY",
                "TELEGRAM_MULTI_PUBLISHER_ENABLED",
                "TELEGRAM_B2B_DISPATCH_ENABLED",
                "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED",
                "TELEGRAM_PUBLISHER_1_ENABLED",
                "TELEGRAM_PUBLISHER_2_ENABLED",
                "TELEGRAM_PUBLISHER_3_ENABLED",
                "TELEGRAM_PUBLISHER_4_ENABLED",
                "TELEGRAM_PUBLISHER_5_ENABLED",
            ),
        )
        forbidden = contract.forbidden_token_keys[5]
        self.assertEqual(
            present_forbidden_tokens({forbidden: True}, contract),
            (forbidden,),
        )

    def test_bot_contract_requires_queue_owner_and_five_lane_parent_flags(self):
        contract = bot_process_contract()
        self.assertEqual(
            missing_required_env(
                {
                    "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                    "TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_EXECUTION_OWNER": "queue-v1",
                    "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
                    "TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY": "true",
                    "TELEGRAM_MULTI_PUBLISHER_ENABLED": "true",
                    "TELEGRAM_B2B_DISPATCH_ENABLED": "true",
                },
                contract,
            ),
            (),
        )
        self.assertFalse(contract.forbidden_token_keys)

    def test_legacy_and_queue_workers_may_not_overlap(self):
        self.assertTrue(
            executor_overlap_forbidden(
                legacy_workers_enabled=True,
                queue_worker_enabled=True,
            )
        )
        self.assertFalse(
            executor_overlap_forbidden(
                legacy_workers_enabled=False,
                queue_worker_enabled=True,
            )
        )

    def test_upsert_env_lines_replaces_and_appends_without_touching_other_keys(self):
        updated = upsert_env_lines(
            "KEEP=1\nTELEGRAM_DELIVERY_PRODUCER_MODE=legacy\n",
            {
                "TELEGRAM_DELIVERY_PRODUCER_MODE": "queue-v1",
                "TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED": "true",
            },
        )
        self.assertIn("KEEP=1\n", updated)
        self.assertIn("TELEGRAM_DELIVERY_PRODUCER_MODE=queue-v1\n", updated)
        self.assertIn("TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED=true\n", updated)
        self.assertNotIn("legacy", updated)

    def test_expected_channel_id_is_copied_only_when_absent_and_matching(self):
        copied = expected_channel_id_updates("CHANNEL_ID=-100111\n")
        self.assertEqual(
            copied,
            {"TELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID": "-100111"},
        )
        self.assertEqual(
            expected_channel_id_updates(
                "CHANNEL_ID=-100111\nTELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID=-100111\n"
            ),
            {},
        )
        with self.assertRaises(ValueError):
            expected_channel_id_updates(
                "CHANNEL_ID=-100111\nTELEGRAM_DELIVERY_QUEUE_EXPECTED_CHANNEL_ID=-100222\n"
            )
        with self.assertRaises(ValueError):
            expected_channel_id_updates("TELEGRAM_DELIVERY_PRODUCER_MODE=legacy\n")

    def test_process_role_env_updates_keep_tokens_off_api(self):
        api = api_env_updates()
        bot = bot_env_updates()
        forbidden = api_process_contract().forbidden_token_keys
        self.assertEqual(api["TELEGRAM_DELIVERY_EXECUTION_OWNER"], "producer-only")
        self.assertTrue(all(api[key] == "" for key in forbidden))
        self.assertEqual(bot["TELEGRAM_DELIVERY_EXECUTION_OWNER"], "queue-v1")
        self.assertTrue(all(key not in bot for key in forbidden))

    def test_executor_count_is_zero_one_or_overlap_two(self):
        self.assertEqual(
            executor_count(
                bot_running=False,
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            ),
            0,
        )
        self.assertEqual(
            executor_count(
                bot_running=True,
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            ),
            1,
        )
        self.assertEqual(
            executor_count(
                bot_running=True,
                legacy_workers_enabled=True,
                queue_worker_enabled=True,
            ),
            2,
        )

    def test_staging_executor_inventory_proves_one_queue_owner_across_both_hosts(self):
        staging = self._executor_record()
        production = {
            "name": "trading_bot_bot",
            "project": "trading_bot",
            "service": "bot",
            "environment": "production",
            "scope": "other-known-environment",
            "bot_process_count": 1,
            "_process_ids": {90},
        }
        evidence = executor_inventory_from_observation(
            foreign_containers=[production, staging],
            iran_containers=[],
            foreign_host_process_ids=[90, 101],
            iran_host_process_ids=[],
            expected_owner="queue-v1",
        )
        self.assertEqual(evidence["executor_count"], 1)
        self.assertEqual(evidence["execution_owner"], "queue-v1")
        self.assertEqual(
            evidence["containers"]["other_known_environment_process_count"], 1
        )
        self.assertFalse(evidence["process_identifiers_disclosed"])

    def test_staging_executor_inventory_rejects_duplicate_process(self):
        with self.assertRaisesRegex(
            StagingCutoverError, "executor_container_identity_mismatch"
        ):
            executor_inventory_from_observation(
                foreign_containers=[
                    self._executor_record(process_ids=(101, 102))
                ],
                iran_containers=[],
                foreign_host_process_ids=[101, 102],
                iran_host_process_ids=[],
                expected_owner="queue-v1",
            )

    def test_staging_executor_inventory_rejects_uncontained_host_process(self):
        with self.assertRaisesRegex(
            StagingCutoverError, "executor_uncontained_host_process"
        ):
            executor_inventory_from_observation(
                foreign_containers=[self._executor_record()],
                iran_containers=[],
                foreign_host_process_ids=[101, 999],
                iran_host_process_ids=[],
                expected_owner="queue-v1",
            )

    def test_staging_executor_inventory_rejects_extra_iran_executor(self):
        with self.assertRaisesRegex(
            StagingCutoverError, "extra_iran_executor_present"
        ):
            executor_inventory_from_observation(
                foreign_containers=[self._executor_record()],
                iran_containers=[
                    {
                        "name": "unapproved-iran-bot",
                        "project": "trading_bot_staging_iran",
                        "service": "bot",
                        "environment": "staging",
                        "scope": "staging",
                        "bot_process_count": 1,
                        "_process_ids": {202},
                    }
                ],
                foreign_host_process_ids=[101],
                iran_host_process_ids=[202],
                expected_owner="queue-v1",
            )

    def test_staging_executor_inventory_rejects_unknown_foreign_executor(self):
        extra = {
            "name": "unapproved-foreign-bot",
            "project": "unknown_project",
            "service": "bot",
            "environment": "staging",
            "scope": "ambiguous-unknown",
            "bot_process_count": 1,
            "_process_ids": {303},
        }
        with self.assertRaisesRegex(
            StagingCutoverError, "executor_container_scope_ambiguous"
        ):
            executor_inventory_from_observation(
                foreign_containers=[self._executor_record(), extra],
                iran_containers=[],
                foreign_host_process_ids=[101, 303],
                iran_host_process_ids=[],
                expected_owner="queue-v1",
            )

    def test_staging_executor_inventory_rejects_runtime_owner_mismatch(self):
        record = self._executor_record()
        record["runtime_decision"] = {
            "mode": "legacy",
            "legacy_workers_enabled": True,
            "queue_worker_enabled": False,
        }
        with self.assertRaisesRegex(
            StagingCutoverError, "executor_runtime_ownership_mismatch"
        ):
            executor_inventory_from_observation(
                foreign_containers=[record],
                iran_containers=[],
                foreign_host_process_ids=[101],
                iran_host_process_ids=[],
                expected_owner="queue-v1",
            )

    def test_api_runtime_evidence_requires_all_surfaces_token_free(self):
        release_sha = "a" * 40
        reports = [
            {
                "container": container,
                "role": "api",
                "service": service,
                "server_mode": host_role,
                "environment": "staging",
                "release_sha": release_sha,
                "missing_required": (),
                "forbidden_tokens_present": (),
            }
            for host_role, container, service in API_SURFACES
        ]
        evidence = api_runtime_evidence_from_reports(
            reports,
            expected_release_sha=release_sha,
            include_iran=True,
        )
        self.assertEqual(evidence["surface_count"], 4)
        self.assertTrue(evidence["all_token_free"])
        reports[-1]["forbidden_tokens_present"] = ("BOT_TOKEN",)
        with self.assertRaisesRegex(
            StagingCutoverError, "api_runtime_contract_not_ready"
        ):
            api_runtime_evidence_from_reports(
                reports,
                expected_release_sha=release_sha,
                include_iran=True,
            )

    def test_publisher_runtime_evidence_binds_five_real_distinct_lanes(self):
        source = self._publisher_values()
        evidence = publisher_runtime_evidence_from_observation(
            source_values=source,
            runtime_values=dict(source),
            provider_report=self._provider_report(source),
        )
        self.assertEqual(evidence["identity_count"], 6)
        self.assertEqual(evidence["publisher_lane_count"], 5)
        self.assertTrue(evidence["provider_identity_and_permissions_verified"])
        rendered = json.dumps(evidence, sort_keys=True)
        self.assertNotIn("central-secret-token", rendered)
        self.assertNotIn("publisher-secret-1", rendered)
        self.assertFalse(evidence["sensitive_values_disclosed"])

    def test_publisher_runtime_evidence_rejects_missing_lane(self):
        source = self._publisher_values()
        source["TELEGRAM_PUBLISHER_5_ENABLED"] = "false"
        with self.assertRaises(StagingCutoverError):
            publisher_runtime_evidence_from_observation(
                source_values=source,
                runtime_values=dict(source),
                provider_report=self._provider_report(self._publisher_values()),
            )

    def test_publisher_runtime_evidence_rejects_duplicate_credential(self):
        source = self._publisher_values()
        source["TELEGRAM_PUBLISHER_5_BOT_TOKEN"] = source[
            "TELEGRAM_PUBLISHER_4_BOT_TOKEN"
        ]
        with self.assertRaisesRegex(
            StagingCutoverError, "publisher_identities_not_distinct"
        ):
            publisher_runtime_evidence_from_observation(
                source_values=source,
                runtime_values=dict(source),
                provider_report=self._provider_report(source),
            )

    def test_publisher_runtime_evidence_rejects_wrong_provider_identity(self):
        source = self._publisher_values()
        provider = self._provider_report(source)
        provider["identities"][3]["bot_fingerprint"] = "0" * 16
        with self.assertRaisesRegex(
            StagingCutoverError, "publisher_provider_identity_mismatch"
        ):
            publisher_runtime_evidence_from_observation(
                source_values=source,
                runtime_values=dict(source),
                provider_report=provider,
            )

    def test_staging_provider_result_accepts_real_gateway_result(self):
        result = TelegramGatewayResult(
            ok=True,
            method="getMe",
            status_code=200,
            response_json={
                "ok": True,
                "result": {
                    "id": 1001,
                    "is_bot": True,
                    "username": "Publisher_1_Staging_Bot",
                },
            },
        )
        provider_result = _validated_provider_preflight_result(
            result,
            method="getMe",
            bot_identity="publisher_1",
            expected_bot_id=1001,
            expected_username="publisher_1_staging_bot",
            expected_channel_id=-100555,
            request_payload={},
        )
        self.assertEqual(provider_result["username"], "Publisher_1_Staging_Bot")

    def test_staging_provider_result_rejects_mapping_and_failed_envelopes(self):
        cases = (
            {
                "ok": True,
                "result": {
                    "id": 1001,
                    "is_bot": True,
                    "username": "publisher_1_staging_bot",
                },
            },
            TelegramGatewayResult(
                ok=False,
                method="getMe",
                status_code=503,
                response_json={
                    "ok": True,
                    "result": {
                        "id": 1001,
                        "is_bot": True,
                        "username": "publisher_1_staging_bot",
                    },
                },
            ),
            TelegramGatewayResult(
                ok=True,
                method="getMe",
                status_code=200,
                response_json={
                    "ok": True,
                    "result": {
                        "id": 1001,
                        "is_bot": True,
                        "username": "publisher_1_staging_bot",
                    },
                },
                error="provider_failure",
            ),
            TelegramGatewayResult(
                ok=True,
                method="getMe",
                status_code=200,
                response_json={"ok": True, "result": []},
            ),
        )
        for case in cases:
            with self.subTest(case=type(case).__name__), self.assertRaisesRegex(
                StagingCutoverError,
                "publisher_provider_preflight_result_invalid",
            ):
                _validated_provider_preflight_result(
                    case,
                    method="getMe",
                    bot_identity="publisher_1",
                    expected_bot_id=1001,
                    expected_username="publisher_1_staging_bot",
                    expected_channel_id=-100555,
                    request_payload={},
                )

    def test_staging_provider_result_validates_channel_and_member_contract(self):
        channel = TelegramGatewayResult(
            ok=True,
            method="getChat",
            status_code=200,
            response_json={
                "ok": True,
                "result": {"id": -100555, "type": "channel"},
            },
        )
        self.assertEqual(
            _validated_provider_preflight_result(
                channel,
                method="getChat",
                bot_identity="publisher_1",
                expected_bot_id=1001,
                expected_username="publisher_1_staging_bot",
                expected_channel_id=-100555,
                request_payload={"chat_id": -100555},
            )["type"],
            "channel",
        )

        member = TelegramGatewayResult(
            ok=True,
            method="getChatMember",
            status_code=200,
            response_json={
                "ok": True,
                "result": {
                    "status": "administrator",
                    "user": {
                        "id": 1001,
                        "is_bot": True,
                        "username": "publisher_1_staging_bot",
                    },
                    "can_manage_chat": True,
                    "can_post_messages": True,
                    "can_edit_messages": True,
                    "can_delete_messages": True,
                },
            },
        )
        self.assertEqual(
            _validated_provider_preflight_result(
                member,
                method="getChatMember",
                bot_identity="publisher_1",
                expected_bot_id=1001,
                expected_username="publisher_1_staging_bot",
                expected_channel_id=-100555,
                request_payload={"chat_id": -100555, "user_id": 1001},
            )["status"],
            "administrator",
        )

        member.response_json["result"]["can_delete_messages"] = False
        with self.assertRaisesRegex(
            StagingCutoverError,
            "publisher_provider_preflight_result_invalid",
        ):
            _validated_provider_preflight_result(
                member,
                method="getChatMember",
                bot_identity="publisher_1",
                expected_bot_id=1001,
                expected_username="publisher_1_staging_bot",
                expected_channel_id=-100555,
                request_payload={"chat_id": -100555, "user_id": 1001},
            )

    def test_staging_status_requires_executor_api_and_publisher_proofs(self):
        binding = {"head": "a" * 40}
        inventory = {
            "executor_count": 1,
            "execution_owner": "queue-v1",
            "executor_overlap": False,
        }
        bot = {
            "service": "bot",
            "server_mode": "foreign",
            "environment": "staging",
            "release_sha": "a" * 40,
            "missing_required": (),
        }
        api = {"status": "verified", "all_token_free": True}
        publishers = {"status": "verified", "publisher_lane_count": 5}
        with patch.object(
            staging_cutover, "_git_binding", return_value=binding
        ), patch.object(
            staging_cutover,
            "collect_executor_inventory",
            return_value=inventory,
        ) as executor, patch.object(
            staging_cutover, "_redacted_runtime", return_value=bot
        ), patch.object(
            staging_cutover,
            "collect_api_runtime_evidence",
            return_value=api,
        ) as api_probe, patch.object(
            staging_cutover,
            "collect_publisher_runtime_evidence",
            return_value=publishers,
        ) as publisher_probe:
            status = staging_cutover.build_status()
        executor.assert_called_once_with(expected_owner="queue-v1")
        api_probe.assert_called_once_with(
            expected_release_sha="a" * 40,
            include_iran=True,
        )
        publisher_probe.assert_called_once_with()
        self.assertTrue(status["cutover_ready"])
        self.assertTrue(status["five_publishers_verified"])
        self.assertFalse(status["secret_values_disclosed"])

    def test_contained_run_kills_term_resistant_descendant_on_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group_path = root / "group-id"
            marker_path = root / "late-marker"
            child_program = self._descendant_program(group_path, marker_path)
            leader_program = (
                "import pathlib, subprocess, sys, time; "
                f"group=pathlib.Path({str(group_path)!r}); "
                "subprocess.Popen("
                f"[sys.executable, '-c', {child_program!r}], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL); "
                "deadline=time.monotonic()+1; "
                "\nwhile not group.exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
                "time.sleep(30)"
            )
            try:
                with patch.object(
                    staging_cutover,
                    "PROCESS_GROUP_TERM_GRACE_SECONDS",
                    0.15,
                ), patch.object(
                    staging_cutover,
                    "PROCESS_GROUP_KILL_GRACE_SECONDS",
                    0.5,
                ):
                    result = staging_cutover._run(
                        [sys.executable, "-c", leader_program],
                        timeout=0.4,
                    )
                self.assertEqual(result.returncode, 124)
                self.assertIn("child_process_timeout", result.stderr)
                self.assertTrue(group_path.is_file())
                process_group_id = int(group_path.read_text(encoding="utf-8"))
                self.assertFalse(
                    staging_cutover._process_group_exists(process_group_id)
                )
                self.assertFalse(marker_path.exists())
            finally:
                self._kill_test_group_if_present(group_path)

    def test_contained_run_rejects_zero_exit_leader_with_detached_io_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            group_path = root / "group-id"
            marker_path = root / "late-marker"
            child_program = self._descendant_program(group_path, marker_path)
            leader_program = (
                "import pathlib, subprocess, sys, time; "
                f"group=pathlib.Path({str(group_path)!r}); "
                "subprocess.Popen("
                f"[sys.executable, '-c', {child_program!r}], "
                "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                "stderr=subprocess.DEVNULL); "
                "deadline=time.monotonic()+1; "
                "\nwhile not group.exists() and time.monotonic() < deadline: time.sleep(0.01)\n"
                "raise SystemExit(0)"
            )
            try:
                with patch.object(
                    staging_cutover,
                    "PROCESS_GROUP_TERM_GRACE_SECONDS",
                    0.15,
                ), patch.object(
                    staging_cutover,
                    "PROCESS_GROUP_KILL_GRACE_SECONDS",
                    0.5,
                ):
                    with self.assertRaisesRegex(
                        StagingCutoverError,
                        "child_process_group_survived_leader_exit",
                    ):
                        staging_cutover._run(
                            [sys.executable, "-c", leader_program],
                            timeout=2,
                        )
                self.assertTrue(group_path.is_file())
                process_group_id = int(group_path.read_text(encoding="utf-8"))
                self.assertFalse(
                    staging_cutover._process_group_exists(process_group_id)
                )
                self.assertFalse(marker_path.exists())
            finally:
                self._kill_test_group_if_present(group_path)

    def test_open_delivery_residue_blocks_cutover(self):
        _assert_quiesced_snapshot(
            {
                "jobs_pending": 0,
                "jobs_leased": 0,
                "jobs_ambiguous": 0,
                "pending_outcomes": 0,
                "active_resume": 0,
                "active_gates": 0,
                "dispatch_open": 0,
                "outbox_open": 0,
            }
        )
        with self.assertRaises(StagingCutoverError):
            _assert_quiesced_snapshot({"jobs_pending": 1, "outbox_open": 0})

    def test_apply_rejects_wrong_confirmation_before_any_mutation(self):
        with self.assertRaises(StagingCutoverError) as ctx:
            apply_cutover(Path("/tmp/telegram-queue-cutover-staging"), confirm="no")
        self.assertEqual(str(ctx.exception), "cutover_confirmation_mismatch")

    def test_redeploy_rejects_wrong_confirmation_before_any_observation(self):
        with patch.object(staging_cutover, "_git_binding") as git_binding:
            with self.assertRaisesRegex(
                StagingCutoverError,
                "redeploy_confirmation_mismatch",
            ):
                redeploy_queue_v1(
                    Path("/tmp/telegram-queue-cutover-staging"),
                    confirm="no",
                )
        git_binding.assert_not_called()

    def test_redeploy_requires_existing_queue_owner_and_deploys_both_peers(self):
        binding = {
            "branch": "main",
            "worktree": "clean",
            "head": "a" * 40,
            "tree": "b" * 40,
            "origin_main": "a" * 40,
        }
        inventory = {
            "executor_count": 1,
            "execution_owner": "queue-v1",
            "executor_overlap": False,
            "legacy_workers_enabled": False,
            "bot_running": True,
        }
        zero_inventory = {
            "executor_count": 0,
            "execution_owner": None,
            "executor_overlap": False,
            "legacy_workers_enabled": False,
            "bot_running": False,
        }
        status = {
            "executor_overlap": False,
            "iran_token_violation": False,
            "cutover_ready": True,
        }
        health = {"decision": "continue"}
        snapshot = {
            "jobs_pending": 0,
            "jobs_leased": 0,
            "jobs_ambiguous": 0,
            "pending_outcomes": 0,
            "active_resume": 0,
            "active_gates": 0,
            "dispatch_open": 0,
            "outbox_open": 0,
        }
        image_ref = f"{staging_cutover.STAGING_IMAGE_REPOSITORY}:{'a' * 40}"
        start_order = []
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            def mocked(name, **kwargs):
                return stack.enter_context(
                    patch.object(staging_cutover, name, **kwargs)
                )

            mocked("REDEPLOY_STATE_DIR", new=Path(directory) / "canonical-state")
            mocked("_git_binding", return_value=binding)
            mocked("_assert_redeploy_runtime_running")
            mocked("_assert_redeploy_runtime_quiesced")
            mocked(
                "collect_executor_inventory",
                side_effect=(inventory, zero_inventory, zero_inventory, inventory),
            )
            mocked("collect_health_summary", side_effect=(health, health))
            mocked("snapshot_queue_aggregates", return_value=snapshot)
            mocked(
                "_quiesce_redeploy_runtime",
                return_value=list(staging_cutover.REDEPLOY_RUNTIME_CONTAINERS),
            )
            mocked("_resume_redeploy_runtime", return_value=[])
            mocked(
                "_build_staging_frontend",
                return_value={
                    "status": "built",
                    "runtime_source_sha256": "c" * 64,
                    "frontend_sha256": "d" * 64,
                    "frontend_file_count": 10,
                    "source": "tracked-head-archive",
                    "worktree_frontend_used": False,
                },
            )
            mocked(
                "_build_prebuilt_foreign_image",
                return_value={"image_ref": image_ref, "runtime_started": False},
            )
            mocked(
                "_image_release_evidence",
                side_effect=(
                    {"role": "foreign", "runtime_started": False},
                    {"role": "iran", "runtime_started": False},
                ),
            )
            mocked(
                "_transfer_prebuilt_image_to_iran",
                return_value={"role": "iran", "runtime_started": False},
            )
            mocked(
                "_assert_prebuilt_image_parity",
                side_effect=lambda *_args, **_kwargs: start_order.append(
                    "image-parity"
                )
                or {"status": "verified_before_runtime_start"},
            )
            mocked("_assert_git_binding_unchanged")
            rsync_iran = mocked(
                "_rsync_iran_release",
                return_value={
                    "status": "synced",
                    "role": "iran",
                    "worktree_source_used": False,
                    "tracked_release": {
                        "git_head": "a" * 40,
                        "git_tree": "b" * 40,
                        "runtime_source_sha256": "c" * 64,
                    },
                    "frontend_sha256": "d" * 64,
                    "frontend_release_scoped": True,
                },
            )
            mocked(
                "_start_foreign_prebuilt_producers",
                side_effect=lambda release: start_order.append("foreign-producers")
                or {"role": "foreign", "release_sha": release},
            )
            mocked(
                "_start_iran_prebuilt_producers",
                side_effect=lambda release: start_order.append("iran-producers")
                or {"role": "iran", "release_sha": release},
            )
            mocked(
                "collect_api_runtime_evidence",
                side_effect=lambda **_kwargs: start_order.append("api-parity")
                or {"status": "verified"},
            )
            mocked(
                "_start_foreign_prebuilt_bot",
                side_effect=lambda release: start_order.append("foreign-bot")
                or {"role": "foreign", "release_sha": release},
            )
            legacy_foreign = mocked("_deploy_foreign")
            legacy_iran = mocked("_deploy_iran")
            mocked("build_status", return_value=status)
            mocked(
                "_runtime_release_evidence",
                side_effect=({"role": "foreign"}, {"role": "iran"}),
            )
            mocked("_assert_release_parity", return_value={"status": "verified"})
            receipt = redeploy_queue_v1(
                Path(directory),
                confirm=staging_cutover.REDEPLOY_CONFIRMATION,
            )

        self.assertEqual(receipt["status"], "redeployed")
        self.assertFalse(receipt["production_authorized"])
        self.assertEqual(
            start_order,
            [
                "image-parity",
                "foreign-producers",
                "iran-producers",
                "api-parity",
                "foreign-bot",
            ],
        )
        legacy_foreign.assert_not_called()
        legacy_iran.assert_not_called()
        rsync_iran.assert_called_once_with(
            expected_head="a" * 40,
            expected_tree="b" * 40,
            expected_frontend_digest="d" * 64,
        )

    def test_tracked_head_export_excludes_ignored_worktree_material(self):
        binding = staging_cutover._git_binding()
        with staging_cutover._tracked_head_export(
            expected_head=binding["head"],
            expected_tree=binding["tree"],
        ) as (export_root, evidence):
            self.assertTrue((export_root / "main.py").is_file())
            self.assertFalse((export_root / ".env").exists())
            self.assertFalse((export_root / "audit_trail/audit.jsonl").exists())
            self.assertEqual(evidence["git_head"], binding["head"])
            self.assertEqual(evidence["git_tree"], binding["tree"])
            self.assertFalse(evidence["ignored_worktree_files_exported"])

    def test_redeploy_recovery_state_is_canonical_and_requires_same_sha(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            staging_cutover, "REDEPLOY_STATE_DIR", Path(directory) / "state"
        ):
            receipt = {
                "status": "failed_forward_reconcile_required",
                "git": {"head": "a" * 40},
                "mutation_started": True,
                "runtime_mutation_started": True,
                "recovery": {"required": True},
            }
            journal = staging_cutover._write_redeploy_journal(
                receipt,
                phase="failed",
            )
            self.assertEqual(journal.parent, staging_cutover.REDEPLOY_STATE_DIR)
            recovery = staging_cutover._redeploy_recovery_state(
                expected_head="a" * 40
            )
            self.assertEqual(recovery["mode"], "recover_exact_sha")
            self.assertTrue(recovery["runtime_mutation_started"])

            # Backward-compatible recovery of the exact kill window that used
            # to persist only mutation_started before the first container stop.
            legacy_window = {
                "status": "preparing",
                "git": {"head": "a" * 40},
                "mutation_started": True,
                "runtime_mutation_started": False,
                "recovery": {"required": False},
            }
            staging_cutover._write_redeploy_journal(
                legacy_window,
                phase="ingress_quiesced",
            )
            legacy_recovery = staging_cutover._redeploy_recovery_state(
                expected_head="a" * 40
            )
            self.assertEqual(legacy_recovery["mode"], "recover_exact_sha")
            self.assertTrue(legacy_recovery["mutation_started"])
            self.assertTrue(legacy_recovery["runtime_mutation_started"])
            with self.assertRaisesRegex(
                StagingCutoverError,
                "redeploy_recovery_requires_exact_same_sha",
            ):
                staging_cutover._redeploy_recovery_state(
                    expected_head="b" * 40
                )

            contained_preflight_failure = {
                "status": "failed_before_runtime_mutation",
                "git": {"head": "a" * 40},
                "mutation_started": False,
                "runtime_mutation_started": False,
                "recovery": {
                    "required": False,
                    "strategy": "none",
                    "resume_error_code": None,
                },
            }
            staging_cutover._write_redeploy_journal(
                contained_preflight_failure,
                phase="failed",
            )
            fresh = staging_cutover._redeploy_recovery_state(
                expected_head="b" * 40
            )
            self.assertEqual(
                fresh["mode"],
                "new_after_contained_preflight_failure",
            )
            self.assertFalse(fresh["runtime_mutation_started"])

    def test_redeploy_recovery_allows_one_contained_tooling_only_successor(self):
        prior_head = "a" * 40
        successor_head = "b" * 40
        recovery_contract = {
            "required": True,
            "runtime_left_quiesced": True,
            "strategy": "rerun_exact_same_pushed_sha",
            "git_head": prior_head,
        }
        completed = lambda args, output: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=output,
            stderr="",
        )
        changed_paths = sorted(
            staging_cutover.SAFE_REDEPLOY_ORCHESTRATION_SUCCESSOR_PATHS
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_dir = root / "artifacts"
            artifact_dir.mkdir()
            failure_receipt = (
                artifact_dir / "cutover-redeploy-failure-synthetic.json"
            )
            failure_receipt.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "environment": "staging",
                        "command": "redeploy",
                        "status": "failed_forward_reconcile_required",
                        "error_code": "iran_prebuilt_producer_start_failed",
                        "git": {"head": prior_head},
                        "mutation_started": True,
                        "runtime_mutation_started": True,
                        "recovery": recovery_contract,
                        "steps": [
                            {
                                "name": "failure_containment",
                                "events": [
                                    {
                                        "container": container,
                                        "action": "already_stopped",
                                        "running": False,
                                    }
                                    for container in staging_cutover.REDEPLOY_RUNTIME_CONTAINERS
                                ],
                            }
                        ],
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            with patch.object(
                staging_cutover, "REDEPLOY_STATE_DIR", root / "state"
            ):
                journal = staging_cutover._write_redeploy_journal(
                    {
                        "status": "failed_forward_reconcile_required",
                        "git": {"head": prior_head},
                        "mutation_started": True,
                        "runtime_mutation_started": True,
                        "recovery": recovery_contract,
                    },
                    phase="failed",
                )
                request = {
                    "artifact_dir": artifact_dir,
                    "prior_head": prior_head,
                    "prior_journal_sha256": staging_cutover._sha256_file(
                        journal
                    ),
                    "failure_receipt": failure_receipt,
                    "failure_receipt_sha256": staging_cutover._sha256_file(
                        failure_receipt
                    ),
                }
                with patch.object(
                    staging_cutover,
                    "_run",
                    side_effect=[
                        completed([], f"{successor_head} {prior_head}\n"),
                        completed(
                            [],
                            "".join(f"M\t{path}\n" for path in changed_paths),
                        ),
                        completed([], ""),
                        completed([], "synthetic-binary-diff"),
                    ],
                ):
                    recovery = staging_cutover._redeploy_recovery_state(
                        expected_head=successor_head,
                        orchestration_successor_request=request,
                    )
                self.assertEqual(
                    recovery["mode"], "recover_safe_orchestration_successor"
                )
                successor = recovery["orchestration_successor"]
                self.assertTrue(successor["used"])
                self.assertEqual(successor["from_head"], prior_head)
                self.assertEqual(successor["to_head"], successor_head)
                self.assertEqual(successor["changed_paths"], changed_paths)

                # Persist the one-shot marker before containment.  A second
                # successor from this journal must fail closed without Git I/O.
                staging_cutover._write_redeploy_journal(
                    {
                        "status": "preparing",
                        "git": {"head": successor_head},
                        "mutation_started": True,
                        "runtime_mutation_started": True,
                        "recovery": {"required": False},
                        "orchestration_successor": successor,
                    },
                    phase="prechecking",
                )
                persisted = json.loads(journal.read_text(encoding="utf-8"))
                self.assertTrue(persisted["orchestration_successor"]["used"])
                with self.assertRaisesRegex(
                    StagingCutoverError,
                    "redeploy_recovery_requires_exact_same_sha",
                ):
                    staging_cutover._redeploy_recovery_state(
                        expected_head="c" * 40,
                        orchestration_successor_request=request,
                    )

    def test_redeploy_successor_requires_its_distinct_confirmation(self):
        with self.assertRaisesRegex(
            StagingCutoverError,
            "redeploy_successor_confirmation_mismatch",
        ):
            staging_cutover.redeploy_queue_v1(
                Path("/tmp/not-used"),
                confirm=staging_cutover.REDEPLOY_CONFIRMATION,
                orchestration_successor_request={"requested": True},
            )
        args = staging_cutover.parse_args(
            [
                "redeploy-successor",
                "--prior-head",
                "a" * 40,
                "--prior-journal-sha256",
                "b" * 64,
                "--failure-receipt",
                "/tmp/failure.json",
                "--failure-receipt-sha256",
                "c" * 64,
            ]
        )
        self.assertEqual(args.command, "redeploy-successor")

    def test_redeploy_evidence_rejects_shared_writable_and_hardlinked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence.json"
            evidence.write_text('{"status":"expected"}\n', encoding="utf-8")

            payload, digest = staging_cutover._read_trusted_evidence_file(
                evidence
            )
            self.assertIn(b'"expected"', payload)
            self.assertEqual(len(digest), 64)

            evidence.chmod(0o666)
            with self.assertRaisesRegex(
                StagingCutoverError,
                "redeploy_evidence_file_untrusted",
            ):
                staging_cutover._read_trusted_evidence_file(evidence)

            evidence.chmod(0o600)
            os.link(evidence, root / "second-link.json")
            with self.assertRaisesRegex(
                StagingCutoverError,
                "redeploy_evidence_file_untrusted",
            ):
                staging_cutover._read_trusted_evidence_file(evidence)

            root.chmod(0o777)
            with self.assertRaisesRegex(
                StagingCutoverError,
                "redeploy_evidence_directory_untrusted",
            ):
                staging_cutover._require_trusted_evidence_directory(root)

    @staticmethod
    def _fake_docker_environment(root: Path, marker: Path) -> dict[str, str]:
        binary_dir = root / "bin"
        binary_dir.mkdir()
        docker = binary_dir / "docker"
        docker.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$4\" > \"$FAKE_DOCKER_MARKER\"\n",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{binary_dir}:{environment.get('PATH', '')}"
        environment["FAKE_DOCKER_MARKER"] = str(marker)
        return environment

    def test_remote_image_import_uses_random_private_file_and_cleans_it(self):
        release_sha = "a" * 40
        payload = b"synthetic-image-archive"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            transfer_root = parent / "transfer"
            marker = parent / "docker-path"
            environment = self._fake_docker_environment(parent, marker)
            script = staging_cutover._remote_image_import_script(
                transfer_root=str(transfer_root),
                expected_sha256=digest,
                release_sha=release_sha,
                required_owner_uid=os.geteuid(),
            )
            result = subprocess.run(
                ["sh", "-c", "sh -c " + shlex.quote(script)],
                input=payload,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr.decode())
            loaded_path = Path(marker.read_text(encoding="utf-8").strip())
            self.assertEqual(loaded_path.parent, transfer_root)
            self.assertTrue(
                loaded_path.name.startswith(f"image-{release_sha}.")
            )
            self.assertNotEqual(
                loaded_path.name,
                f"trading-bot-staging-{release_sha}.tar",
            )
            self.assertEqual(transfer_root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(list(transfer_root.iterdir()), [])

    def test_remote_image_import_rejects_digest_mismatch_before_docker_load(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            transfer_root = parent / "transfer"
            marker = parent / "docker-path"
            environment = self._fake_docker_environment(parent, marker)
            script = staging_cutover._remote_image_import_script(
                transfer_root=str(transfer_root),
                expected_sha256="0" * 64,
                release_sha="a" * 40,
                required_owner_uid=os.geteuid(),
            )
            result = subprocess.run(
                ["sh", "-c", script],
                input=b"different-archive",
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual(list(transfer_root.iterdir()), [])

    def test_remote_image_import_rejects_symlink_transfer_root(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            actual_root = parent / "actual"
            actual_root.mkdir(mode=0o700)
            transfer_root = parent / "transfer"
            transfer_root.symlink_to(actual_root, target_is_directory=True)
            marker = parent / "docker-path"
            environment = self._fake_docker_environment(parent, marker)
            payload = b"synthetic-image-archive"
            script = staging_cutover._remote_image_import_script(
                transfer_root=str(transfer_root),
                expected_sha256=hashlib.sha256(payload).hexdigest(),
                release_sha="a" * 40,
                required_owner_uid=os.geteuid(),
            )
            result = subprocess.run(
                ["sh", "-c", script],
                input=payload,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(marker.exists())
            self.assertEqual(list(actual_root.iterdir()), [])

    def test_redeploy_journal_is_durable_before_first_runtime_stop(self):
        binding = {
            "branch": "main",
            "worktree": "clean",
            "head": "a" * 40,
            "tree": "b" * 40,
            "origin_main": "a" * 40,
        }
        inventory = {
            "executor_count": 1,
            "execution_owner": "queue-v1",
            "executor_overlap": False,
            "legacy_workers_enabled": False,
            "bot_running": True,
        }
        observed_journal: dict[str, object] = {}
        image_ref = f"{staging_cutover.STAGING_IMAGE_REPOSITORY}:{'a' * 40}"

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state_dir = Path(directory) / "state"

            def mocked(name, **kwargs):
                return stack.enter_context(
                    patch.object(staging_cutover, name, **kwargs)
                )

            def interrupt_at_first_stop():
                journal_path = state_dir / staging_cutover.REDEPLOY_JOURNAL_NAME
                observed_journal.update(
                    json.loads(journal_path.read_text(encoding="utf-8"))
                )
                raise StagingCutoverError("simulated_first_stop_kill_window")

            mocked("REDEPLOY_STATE_DIR", new=state_dir)
            mocked("_git_binding", return_value=binding)
            mocked("_assert_redeploy_runtime_running")
            mocked("collect_executor_inventory", return_value=inventory)
            mocked("collect_health_summary", return_value={"decision": "continue"})
            mocked(
                "_build_staging_frontend",
                return_value={
                    "status": "built",
                    "runtime_source_sha256": "c" * 64,
                    "frontend_sha256": "d" * 64,
                    "frontend_file_count": 10,
                    "source": "tracked-head-archive",
                    "worktree_frontend_used": False,
                },
            )
            mocked(
                "_build_prebuilt_foreign_image",
                return_value={"image_ref": image_ref, "runtime_started": False},
            )
            mocked(
                "_image_release_evidence",
                side_effect=(
                    {"role": "foreign", "runtime_started": False},
                    {"role": "iran", "runtime_started": False},
                ),
            )
            mocked(
                "_transfer_prebuilt_image_to_iran",
                return_value={"role": "iran", "runtime_started": False},
            )
            mocked(
                "_assert_prebuilt_image_parity",
                return_value={"status": "verified_before_runtime_start"},
            )
            mocked("_assert_git_binding_unchanged")
            mocked(
                "_quiesce_redeploy_runtime",
                side_effect=interrupt_at_first_stop,
            )
            fail_closed = mocked(
                "_fail_closed_redeploy_runtime",
                return_value=[
                    {"action": "already_stopped", "running": False}
                    for _ in staging_cutover.REDEPLOY_RUNTIME_CONTAINERS
                ],
            )
            resume = mocked("_resume_redeploy_runtime")

            with self.assertRaisesRegex(
                StagingCutoverError,
                "simulated_first_stop_kill_window",
            ):
                redeploy_queue_v1(
                    Path(directory),
                    confirm=staging_cutover.REDEPLOY_CONFIRMATION,
                )

        self.assertEqual(observed_journal["phase"], "quiescing_ingress")
        self.assertTrue(observed_journal["mutation_started"])
        self.assertTrue(observed_journal["runtime_mutation_started"])
        self.assertEqual(observed_journal["git"], binding)
        fail_closed.assert_called_once_with()
        resume.assert_not_called()

    def test_redeploy_recovery_precheck_preserves_mutation_marker(self):
        binding = {
            "branch": "main",
            "worktree": "clean",
            "head": "a" * 40,
            "tree": "b" * 40,
            "origin_main": "a" * 40,
        }
        observed_journal: dict[str, object] = {}
        containment = [
            {"action": "already_stopped", "running": False}
            for _ in staging_cutover.REDEPLOY_RUNTIME_CONTAINERS
        ]
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state_dir = Path(directory) / "state"

            def mocked(name, **kwargs):
                return stack.enter_context(
                    patch.object(staging_cutover, name, **kwargs)
                )

            mocked("REDEPLOY_STATE_DIR", new=state_dir)
            staging_cutover._write_redeploy_journal(
                {
                    "status": "preparing",
                    "git": binding,
                    "mutation_started": True,
                    "runtime_mutation_started": False,
                    "recovery": {"required": False},
                },
                phase="ingress_quiesced",
            )
            mocked("_git_binding", return_value=binding)
            calls = 0

            def interrupt_first_containment():
                nonlocal calls
                calls += 1
                if calls == 1:
                    observed_journal.update(
                        json.loads(
                            (state_dir / staging_cutover.REDEPLOY_JOURNAL_NAME)
                            .read_text(encoding="utf-8")
                        )
                    )
                    raise StagingCutoverError(
                        "simulated_recovery_containment_kill_window"
                    )
                return containment

            fail_closed = mocked(
                "_fail_closed_redeploy_runtime",
                side_effect=interrupt_first_containment,
            )
            resume = mocked("_resume_redeploy_runtime")

            with self.assertRaisesRegex(
                StagingCutoverError,
                "simulated_recovery_containment_kill_window",
            ):
                redeploy_queue_v1(
                    Path(directory),
                    confirm=staging_cutover.REDEPLOY_CONFIRMATION,
                )

        self.assertEqual(observed_journal["phase"], "prechecking")
        self.assertTrue(observed_journal["mutation_started"])
        self.assertTrue(observed_journal["runtime_mutation_started"])
        self.assertEqual(fail_closed.call_count, 2)
        resume.assert_not_called()

    def test_frontend_build_environment_drops_inherited_rollout_and_vite_values(self):
        with patch.dict(
            os.environ,
            {
                "VITE_PRIVATE_ACCIDENT": "must-not-reach-client",
                "VITE_API_BASE_URL": "wrong",
                "FRONTEND_BUILD_OUT_DIR": "/tmp/wrong",
                "NPM_CONFIG_CACHE": "/root/.npm",
                "npm_config_cache": "/tmp/wrong-npm-cache",
                "STAGING_FRONTEND_DIST_DIR": "/tmp/wrong-dist",
                "STAGING_SKIP_FRONTEND_BUILD": "1",
                "STAGING_VITE_API_BASE_URL": "/api",
            },
            clear=False,
        ):
            env, evidence = staging_cutover._frontend_build_environment()
        self.assertNotIn("VITE_PRIVATE_ACCIDENT", env)
        self.assertNotIn("STAGING_SKIP_FRONTEND_BUILD", env)
        self.assertNotIn("STAGING_FRONTEND_DIST_DIR", env)
        self.assertNotIn("FRONTEND_BUILD_OUT_DIR", env)
        self.assertNotIn("NPM_CONFIG_CACHE", env)
        self.assertNotIn("npm_config_cache", env)
        self.assertEqual(env["VITE_API_BASE_URL"], "/api")
        self.assertFalse(evidence["arbitrary_vite_values_inherited"])
        self.assertFalse(evidence["npm_cache_inherited"])
        self.assertFalse(evidence["skip_frontend_build_inherited"])

    def test_frontend_artifact_publish_replaces_stale_release_only_after_build(self):
        release_sha = "a" * 40
        with tempfile.TemporaryDirectory() as directory, patch.object(
            staging_cutover, "REPO_ROOT", Path(directory)
        ):
            destination = staging_cutover._staging_frontend_artifact_dir(release_sha)
            destination.mkdir(parents=True)
            (destination / "stale.js").write_text("stale", encoding="utf-8")
            staged = Path(directory) / "complete-dist"
            staged.mkdir()
            (staged / "index.html").write_text("complete", encoding="utf-8")
            evidence = staging_cutover._publish_staging_frontend_artifact(
                staged,
                destination,
            )
            self.assertFalse((destination / "stale.js").exists())
            self.assertEqual(
                (destination / "index.html").read_text(encoding="utf-8"),
                "complete",
            )
            self.assertEqual(evidence["file_count"], 1)

    def test_prebuilt_image_parity_records_engine_specific_ids_per_host(self):
        release_sha = "a" * 40
        release_tree = "b" * 40
        source_digest = "c" * 64
        frontend_digest = "d" * 64
        dependency_digest = "e" * 64
        image_ref = staging_cutover._staging_image_ref(release_sha)

        def evidence(role: str, image_id: str) -> dict[str, object]:
            return {
                "role": role,
                "git_head": release_sha,
                "git_tree": release_tree,
                "image_ref": image_ref,
                "image_id_sha256": image_id,
                "runtime_source_sha256": source_digest,
                "frontend_sha256": frontend_digest,
                "dependency_sha256": dependency_digest,
                "runtime_started": False,
                "secret_values_disclosed": False,
            }

        foreign = evidence("foreign", "f" * 64)
        iran = evidence("iran", "1" * 64)
        parity = staging_cutover._assert_prebuilt_image_parity(
            foreign,
            iran,
            expected_head=release_sha,
            expected_tree=release_tree,
            expected_source_digest=source_digest,
            expected_frontend_digest=frontend_digest,
        )
        self.assertFalse(parity["image_ids_equal"])
        self.assertTrue(parity["image_identity_recorded_per_host"])
        self.assertEqual(parity["foreign_image_id_sha256"], "f" * 64)
        self.assertEqual(parity["iran_image_id_sha256"], "1" * 64)

        iran["dependency_sha256"] = "2" * 64
        with self.assertRaisesRegex(
            StagingCutoverError,
            "prebuilt_image_content_split",
        ):
            staging_cutover._assert_prebuilt_image_parity(
                foreign,
                iran,
                expected_head=release_sha,
                expected_tree=release_tree,
                expected_source_digest=source_digest,
                expected_frontend_digest=frontend_digest,
            )

    def test_failed_frontend_build_preserves_existing_release_artifact(self):
        release_sha = "a" * 40
        release_tree = "b" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export_root = root / "export"
            frontend = export_root / "frontend"
            frontend.mkdir(parents=True)
            (frontend / "package.json").write_text("{}", encoding="utf-8")
            (frontend / "package-lock.json").write_text("{}", encoding="utf-8")

            @contextmanager
            def tracked_export(**_kwargs):
                yield export_root, {
                    "runtime_source_sha256": "c" * 64,
                    "runtime_source_file_count": 1,
                }

            class Result:
                def __init__(self, returncode):
                    self.returncode = returncode

            with patch.object(staging_cutover, "REPO_ROOT", root), patch.object(
                staging_cutover, "_tracked_head_export", tracked_export
            ), patch.object(
                staging_cutover,
                "_frontend_build_environment",
                return_value=({}, {}),
            ), patch.object(
                staging_cutover,
                "_run_contained",
                side_effect=(Result(0), Result(1)),
            ) as run_contained:
                destination = staging_cutover._staging_frontend_artifact_dir(
                    release_sha
                )
                destination.mkdir(parents=True)
                (destination / "known-good.js").write_text(
                    "known-good",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    StagingCutoverError,
                    "staging_frontend_build_failed",
                ):
                    staging_cutover._build_staging_frontend(
                        expected_head=release_sha,
                        expected_tree=release_tree,
                    )
                self.assertEqual(
                    (destination / "known-good.js").read_text(encoding="utf-8"),
                    "known-good",
                )
                install_env = run_contained.call_args_list[0].kwargs["env"]
                self.assertEqual(
                    install_env["NPM_CONFIG_CACHE"],
                    str(export_root / ".npm-cache"),
                )

    def test_rsync_contract_preserves_managed_wheel_cache(self):
        self.assertIn("/pip_packages/", staging_cutover.RSYNC_EXCLUDES)

    def test_redeploy_partial_release_failure_is_fail_closed_and_receipted(self):
        binding = {
            "branch": "main",
            "worktree": "clean",
            "head": "a" * 40,
            "tree": "b" * 40,
            "origin_main": "a" * 40,
        }
        queue_inventory = {
            "executor_count": 1,
            "execution_owner": "queue-v1",
            "executor_overlap": False,
            "legacy_workers_enabled": False,
            "bot_running": True,
        }
        zero_inventory = {
            "executor_count": 0,
            "execution_owner": None,
            "executor_overlap": False,
            "legacy_workers_enabled": False,
            "bot_running": False,
        }
        snapshot = {
            key: 0
            for key in (
                "jobs_pending",
                "jobs_leased",
                "jobs_ambiguous",
                "pending_outcomes",
                "active_resume",
                "active_gates",
                "dispatch_open",
                "outbox_open",
            )
        }
        sync_report = {
            "status": "synced",
            "role": "iran",
            "worktree_source_used": False,
            "tracked_release": {
                "git_head": "a" * 40,
                "git_tree": "b" * 40,
                "runtime_source_sha256": "c" * 64,
            },
            "frontend_sha256": "d" * 64,
            "frontend_release_scoped": True,
        }
        image_ref = f"{staging_cutover.STAGING_IMAGE_REPOSITORY}:{'a' * 40}"
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            def mocked(name, **kwargs):
                return stack.enter_context(
                    patch.object(staging_cutover, name, **kwargs)
                )

            mocked("REDEPLOY_STATE_DIR", new=Path(directory) / "canonical-state")
            mocked("_git_binding", return_value=binding)
            mocked("_assert_redeploy_runtime_running")
            mocked("_assert_redeploy_runtime_quiesced")
            mocked(
                "collect_executor_inventory",
                side_effect=(queue_inventory, zero_inventory),
            )
            mocked("collect_health_summary", return_value={"decision": "continue"})
            mocked("snapshot_queue_aggregates", return_value=snapshot)
            mocked(
                "_quiesce_redeploy_runtime",
                return_value=list(staging_cutover.REDEPLOY_RUNTIME_CONTAINERS),
            )
            resume = mocked("_resume_redeploy_runtime")
            mocked(
                "_build_staging_frontend",
                return_value={
                    "status": "built",
                    "runtime_source_sha256": "c" * 64,
                    "frontend_sha256": "d" * 64,
                    "frontend_file_count": 10,
                    "source": "tracked-head-archive",
                    "worktree_frontend_used": False,
                },
            )
            mocked(
                "_build_prebuilt_foreign_image",
                return_value={"image_ref": image_ref, "runtime_started": False},
            )
            mocked(
                "_image_release_evidence",
                side_effect=(
                    {"role": "foreign", "runtime_started": False},
                    {"role": "iran", "runtime_started": False},
                ),
            )
            mocked(
                "_transfer_prebuilt_image_to_iran",
                return_value={"role": "iran", "runtime_started": False},
            )
            mocked(
                "_assert_prebuilt_image_parity",
                return_value={"status": "verified_before_runtime_start"},
            )
            mocked("_assert_git_binding_unchanged")
            mocked("_rsync_iran_release", return_value=sync_report)
            mocked(
                "_start_foreign_prebuilt_producers",
                return_value={"status": "started_prebuilt", "role": "foreign"},
            )
            mocked(
                "_start_iran_prebuilt_producers",
                side_effect=StagingCutoverError(
                    "iran_prebuilt_producer_start_failed"
                ),
            )
            start_bot = mocked("_start_foreign_prebuilt_bot")
            legacy_foreign = mocked("_deploy_foreign")
            legacy_iran = mocked("_deploy_iran")
            fail_closed = mocked(
                "_fail_closed_redeploy_runtime",
                return_value=[
                    {"action": "already_stopped", "running": False}
                    for _ in staging_cutover.REDEPLOY_RUNTIME_CONTAINERS
                ],
            )
            with self.assertRaisesRegex(
                StagingCutoverError, "iran_prebuilt_producer_start_failed"
            ):
                redeploy_queue_v1(
                    Path(directory),
                    confirm=staging_cutover.REDEPLOY_CONFIRMATION,
                )
            receipts = list(Path(directory).glob("cutover-redeploy-failure-*.json"))
            self.assertEqual(len(receipts), 1)
            payload = json.loads(receipts[0].read_text(encoding="utf-8"))
        fail_closed.assert_called_once_with()
        resume.assert_not_called()
        start_bot.assert_not_called()
        legacy_foreign.assert_not_called()
        legacy_iran.assert_not_called()
        self.assertEqual(payload["status"], "failed_forward_reconcile_required")
        self.assertTrue(payload["recovery"]["required"])
        self.assertTrue(payload["recovery"]["runtime_left_quiesced"])

    def test_clean_pushed_main_is_required(self):
        with self.assertRaises(StagingCutoverError):
            _require_clean_pushed_main(
                {
                    "branch": "main",
                    "worktree": "dirty",
                    "head": "aaa",
                    "origin_main": "aaa",
                }
            )
        with self.assertRaises(StagingCutoverError):
            _require_clean_pushed_main(
                {
                    "branch": "main",
                    "worktree": "clean",
                    "head": "aaa",
                    "origin_main": "bbb",
                }
            )
        _require_clean_pushed_main(
            {
                "branch": "main",
                "worktree": "clean",
                "head": "aaa",
                "origin_main": "aaa",
            }
        )

    def test_staging_compose_isolates_api_tokens_from_shared_env(self):
        compose_path = (
            Path(__file__).resolve().parents[1]
            / "deploy"
            / "staging"
            / "docker-compose.staging.yml"
        )
        compose = compose_path.read_text(encoding="utf-8")
        self.assertIn("x-api-telegram-isolation", compose)
        self.assertIn("TELEGRAM_DELIVERY_PRODUCER_MODE: queue-v1", compose)
        self.assertIn("TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER: queue-v1", compose)
        for key in (
            "TELEGRAM_MULTI_PUBLISHER_ENABLED",
            "TELEGRAM_B2B_DISPATCH_ENABLED",
        ):
            self.assertIn(f'{key}: "true"', compose)
        for key in (
            "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_ENABLED",
            "TELEGRAM_PUBLISHER_1_ENABLED",
            "TELEGRAM_PUBLISHER_2_ENABLED",
            "TELEGRAM_PUBLISHER_3_ENABLED",
            "TELEGRAM_PUBLISHER_4_ENABLED",
            "TELEGRAM_PUBLISHER_5_ENABLED",
        ):
            self.assertIn(f'{key}: "false"', compose)
        self.assertIn("x-non-iran-sms-isolation", compose)
        self.assertIn("OTP_SMS_AUTO_FALLBACK_ENABLED: \"false\"", compose)
        self.assertGreaterEqual(compose.count("*api_telegram_isolation"), 5)
        self.assertIn("<<: *api_telegram_isolation", compose)
        self.assertIn("*non_iran_sms_isolation", compose)
        rendered = yaml.safe_load(compose)
        expected = api_process_contract().required
        for service in (
            "app",
            "foreign_app",
            "sync_worker",
            "foreign_sync_worker",
            "migration",
        ):
            environment = rendered["services"][service]["environment"]
            for key, value in expected.items():
                self.assertEqual(
                    str(environment.get(key)).lower(),
                    value,
                    msg=f"{service}:{key}",
                )
        api_updates = api_env_updates()
        self.assertIsNone(
            initial_telegram_publication_publisher_identity(
                multi_publisher_enabled=(
                    api_updates["TELEGRAM_MULTI_PUBLISHER_ENABLED"] == "true"
                ),
                b2b_dispatch_enabled=(
                    api_updates["TELEGRAM_B2B_DISPATCH_ENABLED"] == "true"
                ),
            )
        )


if __name__ == "__main__":
    unittest.main()
