from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import (
    LiveMatrixError,
    _recovery_delivery_fault,
    _recovery_delivery_resume_emit,
    _recovery_timing_cleanup,
)
from scripts.full_matrix_live import site_agent
from scripts.full_matrix_live.site_agent import SiteAgentError


RELEASE = "a" * 40
CAMPAIGN = "full-matrix-recovery-test"
FAULT_ID = "FMX_1234567890ABCDEF_REC"


def _request(action: str, **extra: object) -> dict[str, object]:
    context: dict[str, object] = {"action": action, "fault_id": FAULT_ID}
    context.update(extra)
    return {
        "campaign_id": CAMPAIGN,
        "release_sha": RELEASE,
        "context": context,
    }


def _controller_response(action: str) -> dict[str, object]:
    return {
        "result": {
            "schema": "three-site-full-matrix-site-agent-result-v1",
            "status": "passed",
            "role": "webapp_ir",
            "operation": "recovery_delivery_fault",
            "result": {
                "status": "passed",
                "action": action,
                "fault_id": FAULT_ID,
                "phase": "paused" if action == "pause" else "resumed",
            },
        }
    }


def _emitter() -> dict[str, object]:
    return {
        "schema": "three-site-full-matrix-timing-emitter-v1",
        "status": "passed",
        "role": "webapp_ir",
        "fixture_prefix": "FMX_1234567890ABCDEF_LIVE",
        "correlation_prefix": "fmxtiming:123456live",
        "sample_count": 4,
        "target_rps": 10.0,
        "observed_emit_rps": 8.0,
        "started_epoch": 1.0,
        "finished_epoch": 2.0,
        "three_site_writer_fence": True,
        "production_touched": False,
        "samples": [],
    }


def _resume_emit_request() -> dict[str, object]:
    return {
        "campaign_id": CAMPAIGN,
        "release_sha": RELEASE,
        "context": {
            "fault_id": FAULT_ID,
            "fixture_prefix": "FMX_1234567890ABCDEF_LIVE",
            "correlation_prefix": "fmxtiming:123456live",
            "samples_per_route": 2,
            "target_rps": 10.0,
        },
    }


class FullMatrixRecoveryDeliveryFaultTests(unittest.TestCase):
    def test_controller_uses_only_closed_object_storage_operation(self):
        plan = {"_roles": {"webapp_ir": {"transport": "object-storage-agent"}}}
        with patch(
            "scripts.full_matrix_live.scenario_handlers.run_role_agent_operation",
            return_value=_controller_response("pause"),
        ) as agent:
            result = _recovery_delivery_fault(plan, action="pause", fault_id=FAULT_ID)
        self.assertEqual(result["phase"], "paused")
        self.assertEqual(agent.call_args.kwargs["operation"], "recovery_delivery_fault")
        context = agent.call_args.kwargs["context"]
        self.assertEqual(context, {"action": "pause", "fault_id": FAULT_ID})
        self.assertNotIn("command", context)
        self.assertNotIn("path", context)

    def test_controller_rejects_nonexact_agent_response(self):
        plan = {"_roles": {"webapp_ir": {"transport": "object-storage-agent"}}}
        response = _controller_response("pause")
        response["result"]["result"]["phase"] = "resumed"  # type: ignore[index]
        with patch(
            "scripts.full_matrix_live.scenario_handlers.run_role_agent_operation",
            return_value=response,
        ):
            with self.assertRaises(LiveMatrixError):
                _recovery_delivery_fault(plan, action="pause", fault_id=FAULT_ID)

    def test_recovery_cleanup_uses_only_closed_ir_operation(self):
        plan = {"_roles": {"webapp_ir": {"transport": "object-storage-agent"}}}
        cleanup = {
            "schema": "three-site-full-matrix-timing-emitter-v1",
            "status": "passed",
            "action": "cleanup",
            "role": "webapp_ir",
            "fixture_prefix": "FMX_1234567890ABCDEF_REC",
            "production_touched": False,
        }
        response = {
            "result": {
                "schema": "three-site-full-matrix-site-agent-result-v1",
                "status": "passed",
                "role": "webapp_ir",
                "operation": "timing_cleanup",
                "result": {"status": "passed", "cleanup": cleanup},
            }
        }
        with patch(
            "scripts.full_matrix_live.scenario_handlers.run_role_agent_operation",
            return_value=response,
        ) as agent:
            result = _recovery_timing_cleanup(
                plan, fixture_prefix="FMX_1234567890ABCDEF_REC"
            )
        self.assertEqual(result, cleanup)
        self.assertEqual(agent.call_args.kwargs["operation"], "timing_cleanup")
        self.assertEqual(
            agent.call_args.kwargs["context"],
            {"fixture_prefix": "FMX_1234567890ABCDEF_REC"},
        )

    def test_controller_resume_emit_uses_only_closed_ir_operation(self):
        plan = {"_roles": {"webapp_ir": {"transport": "object-storage-agent"}}}
        response = {
            "result": {
                "schema": "three-site-full-matrix-site-agent-result-v1",
                "status": "passed",
                "role": "webapp_ir",
                "operation": "recovery_delivery_resume_emit",
                "result": {
                    "status": "passed",
                    "fault_id": FAULT_ID,
                    "phase": "resumed_with_live_emit",
                    "emitter": _emitter(),
                },
            }
        }
        with patch(
            "scripts.full_matrix_live.scenario_handlers.run_role_agent_operation",
            return_value=response,
        ) as agent:
            result = _recovery_delivery_resume_emit(
                object(),
                plan,
                fault_id=FAULT_ID,
                fixture_prefix="FMX_1234567890ABCDEF_LIVE",
                correlation_prefix="fmxtiming:123456live",
                samples_per_route=2,
                target_rps=10.0,
            )
        self.assertEqual(result, _emitter())
        self.assertEqual(
            agent.call_args.kwargs["operation"], "recovery_delivery_resume_emit"
        )
        self.assertEqual(
            set(agent.call_args.kwargs["context"]),
            {
                "fault_id", "fixture_prefix", "correlation_prefix",
                "samples_per_route", "target_rps",
            },
        )

    def test_agent_rejects_extra_context_before_command_execution(self):
        with patch("scripts.full_matrix_live.site_agent._run") as run:
            with self.assertRaises(SiteAgentError):
                site_agent._recovery_delivery_fault(_request("pause", command="forbidden"))
        self.assertFalse(run.called)

    def test_pause_replay_is_idempotent_and_resume_cleans_exact_state(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            state_path = Path(raw_dir) / "recovery-delivery-fault.json"
            with patch.object(site_agent, "_RECOVERY_FAULT_STATE", state_path), patch(
                "scripts.full_matrix_live.site_agent._verify_release", return_value=RELEASE
            ), patch(
                "scripts.full_matrix_live.site_agent._run", return_value=""
            ) as run, patch(
                "scripts.full_matrix_live.site_agent._ir_delivery_running", return_value=False
            ):
                paused = site_agent._recovery_delivery_fault(_request("pause"))
            self.assertEqual(paused["phase"], "paused")
            stored = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["phase"], "paused")
            self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
            stop = run.call_args.args[0]
            self.assertEqual(
                stop,
                [
                    "docker", "compose", "--env-file",
                    "/root/secure-envs/full-matrix/roles/webapp-ir.env",
                    "-f", "/root/secure-envs/full-matrix/roles/webapp-ir.compose.yml",
                    "stop", "--timeout", "30", "webapp_ir_dr_delivery",
                ],
            )
            with patch.object(site_agent, "_RECOVERY_FAULT_STATE", state_path), patch(
                "scripts.full_matrix_live.site_agent._verify_release", return_value=RELEASE
            ), patch(
                "scripts.full_matrix_live.site_agent._run"
            ) as replay_run, patch(
                "scripts.full_matrix_live.site_agent._ir_delivery_running", return_value=False
            ):
                replay = site_agent._recovery_delivery_fault(_request("pause"))
            self.assertEqual(replay["phase"], "paused")
            self.assertFalse(replay_run.called)
            with patch.object(site_agent, "_RECOVERY_FAULT_STATE", state_path), patch(
                "scripts.full_matrix_live.site_agent._verify_release", return_value=RELEASE
            ), patch(
                "scripts.full_matrix_live.site_agent._run", return_value=""
            ) as resume_run, patch(
                "scripts.full_matrix_live.site_agent._ir_delivery_running", return_value=True
            ):
                resumed = site_agent._recovery_delivery_fault(_request("resume"))
            self.assertEqual(resumed["phase"], "resumed")
            self.assertFalse(state_path.exists())
            self.assertEqual(
                resume_run.call_args.args[0][-4:],
                ["up", "-d", "--no-deps", "webapp_ir_dr_delivery"],
            )

    def test_resume_refuses_mismatched_retained_fault_before_command(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            state_path = Path(raw_dir) / "recovery-delivery-fault.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema": "three-site-full-matrix-recovery-delivery-fault-v1",
                        "campaign_id": CAMPAIGN,
                        "release_sha": RELEASE,
                        "fault_id": "FMX_1234567890ABCDEF_OTHER",
                        "phase": "paused",
                    }
                ),
                encoding="utf-8",
            )
            state_path.chmod(0o600)
            with patch.object(site_agent, "_RECOVERY_FAULT_STATE", state_path), patch(
                "scripts.full_matrix_live.site_agent._verify_release", return_value=RELEASE
            ), patch("scripts.full_matrix_live.site_agent._run") as run:
                with self.assertRaises(SiteAgentError):
                    site_agent._recovery_delivery_fault(_request("resume"))
            self.assertFalse(run.called)

    def test_resume_is_safe_idempotent_when_prior_response_was_lost(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            state_path = Path(raw_dir) / "recovery-delivery-fault.json"
            with patch.object(site_agent, "_RECOVERY_FAULT_STATE", state_path), patch(
                "scripts.full_matrix_live.site_agent._verify_release", return_value=RELEASE
            ), patch("scripts.full_matrix_live.site_agent._run") as run, patch(
                "scripts.full_matrix_live.site_agent._ir_delivery_running", return_value=True
            ):
                result = site_agent._recovery_delivery_fault(_request("resume"))
            self.assertEqual(result["phase"], "resumed")
            self.assertFalse(run.called)

    def test_agent_resume_emit_starts_pinned_service_then_local_emitter(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            state_path = Path(raw_dir) / "recovery-delivery-fault.json"
            state_path.write_text(
                json.dumps(
                    {
                        "schema": "three-site-full-matrix-recovery-delivery-fault-v1",
                        "campaign_id": CAMPAIGN,
                        "release_sha": RELEASE,
                        "fault_id": FAULT_ID,
                        "phase": "paused",
                    }
                ),
                encoding="utf-8",
            )
            state_path.chmod(0o600)
            with patch.object(site_agent, "_RECOVERY_FAULT_STATE", state_path), patch(
                "scripts.full_matrix_live.site_agent._verify_release", return_value=RELEASE
            ), patch(
                "scripts.full_matrix_live.site_agent._run",
                side_effect=["", json.dumps(_emitter())],
            ) as run, patch(
                "scripts.full_matrix_live.site_agent._ir_delivery_running", return_value=True
            ):
                result = site_agent._recovery_delivery_resume_emit(_resume_emit_request())
            self.assertEqual(result["phase"], "resumed_with_live_emit")
            self.assertFalse(state_path.exists())
            self.assertEqual(
                run.call_args_list[0].args[0][-4:],
                ["up", "-d", "--no-deps", "webapp_ir_dr_delivery"],
            )
            command = run.call_args_list[1].args[0]
            self.assertIn("webapp_ir_api", command)
            self.assertIn("/app/scripts/full_matrix_live/timing_probe.py", command)

    def test_agent_resume_emit_rejects_extra_context_before_execution(self):
        request = _resume_emit_request()
        request["context"]["command"] = "forbidden"  # type: ignore[index]
        with patch("scripts.full_matrix_live.site_agent._run") as run:
            with self.assertRaises(SiteAgentError):
                site_agent._recovery_delivery_resume_emit(request)
        self.assertFalse(run.called)


if __name__ == "__main__":
    unittest.main()
