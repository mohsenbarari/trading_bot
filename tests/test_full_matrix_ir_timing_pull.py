from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from scripts.full_matrix_live.scenario_handlers import _run_timing_emitter
from scripts.full_matrix_live.site_agent import SiteAgentError, _timing_cleanup, _timing_emit


def _emitter() -> dict:
    return {
        "schema": "three-site-full-matrix-timing-emitter-v1",
        "status": "passed",
        "role": "webapp_ir",
        "fixture_prefix": "FMX_1234567890ABCDEF_TIM",
        "correlation_prefix": "fmxtiming:8a2c91",
        "sample_count": 4,
        "target_rps": 10.0,
        "observed_emit_rps": 9.0,
        "started_epoch": 1.0,
        "finished_epoch": 2.0,
        "three_site_writer_fence": True,
        "production_touched": False,
        "samples": [],
    }


class FullMatrixIrTimingPullTests(unittest.TestCase):
    def test_controller_uses_only_closed_object_storage_timing_operation(self):
        plan = {"_roles": {"webapp_ir": {"transport": "object-storage-agent"}}}
        response = {
            "result": {
                "schema": "three-site-full-matrix-site-agent-result-v1",
                "status": "passed",
                "role": "webapp_ir",
                "operation": "timing_emit",
                "result": {"status": "passed", "emitter": _emitter()},
            }
        }
        with patch(
            "scripts.full_matrix_live.scenario_handlers.run_role_agent_operation",
            return_value=response,
        ) as agent, patch(
            "scripts.full_matrix_live.scenario_handlers.run_compose_role_service",
        ) as compose:
            payload = _run_timing_emitter(
                object(),
                plan,
                role_name="webapp_ir",
                fixture_prefix="FMX_1234567890ABCDEF_TIM",
                correlation_prefix="fmxtiming:8a2c91",
                samples_per_route=2,
                target_rps=10.0,
            )
        self.assertEqual(payload["role"], "webapp_ir")
        self.assertFalse(compose.called)
        self.assertEqual(agent.call_args.kwargs["operation"], "timing_emit")
        context = agent.call_args.kwargs["context"]
        self.assertEqual(
            set(context),
            {"fixture_prefix", "correlation_prefix", "samples_per_route", "target_rps"},
        )
        self.assertNotIn("command", context)
        self.assertNotIn("path", context)

    def test_agent_rejects_any_nonclosed_timing_context_before_execution(self):
        request = {
            "context": {
                "fixture_prefix": "FMX_1234567890ABCDEF_TIM",
                "correlation_prefix": "fmxtiming:8a2c91",
                "samples_per_route": 2,
                "target_rps": 10.0,
                "command": "forbidden",
            }
        }
        with patch("scripts.full_matrix_live.site_agent._run") as run:
            with self.assertRaises(SiteAgentError):
                _timing_emit(request)
        self.assertFalse(run.called)

    def test_agent_rejects_an_oversize_probe_prefix_before_execution(self):
        request = {
            "context": {
                "fixture_prefix": "FMX_1234567890ABCDEF_TIM",
                "correlation_prefix": "fmxtiming:1234567890abcdef",
                "samples_per_route": 2,
                "target_rps": 10.0,
            }
        }
        self.assertGreater(len(request["context"]["correlation_prefix"]), 24)
        with patch("scripts.full_matrix_live.site_agent._run") as run:
            with self.assertRaises(SiteAgentError):
                _timing_emit(request)
        self.assertFalse(run.called)

    def test_agent_uses_pinned_ir_timing_program(self):
        request = {
            "release_sha": "a" * 40,
            "context": {
                "fixture_prefix": "FMX_1234567890ABCDEF_TIM",
                "correlation_prefix": "fmxtiming:8a2c91",
                "samples_per_route": 2,
                "target_rps": 10.0,
            },
        }
        with patch(
            "scripts.full_matrix_live.site_agent._verify_release",
            return_value="a" * 40,
        ), patch(
            "scripts.full_matrix_live.site_agent._run",
            return_value=json.dumps(_emitter()),
        ) as run:
            result = _timing_emit(request)
        self.assertEqual(result["emitter"]["role"], "webapp_ir")
        command = run.call_args.args[0]
        self.assertIn("webapp_ir_api", command)
        self.assertIn("/app/scripts/full_matrix_live/timing_probe.py", command)
        self.assertIn("webapp_ir", command)

    def test_agent_timing_cleanup_rejects_extra_context_before_execution(self):
        request = {
            "context": {
                "fixture_prefix": "FMX_1234567890ABCDEF_TIM",
                "command": "forbidden",
            }
        }
        with patch("scripts.full_matrix_live.site_agent._run") as run:
            with self.assertRaises(SiteAgentError):
                _timing_cleanup(request)
        self.assertFalse(run.called)

    def test_agent_timing_cleanup_uses_pinned_ir_program(self):
        cleanup = {
            "schema": "three-site-full-matrix-timing-emitter-v1",
            "status": "passed",
            "action": "cleanup",
            "role": "webapp_ir",
            "fixture_prefix": "FMX_1234567890ABCDEF_TIM",
            "production_touched": False,
        }
        request = {
            "release_sha": "a" * 40,
            "context": {"fixture_prefix": "FMX_1234567890ABCDEF_TIM"},
        }
        with patch(
            "scripts.full_matrix_live.site_agent._verify_release",
            return_value="a" * 40,
        ), patch(
            "scripts.full_matrix_live.site_agent._run",
            return_value=json.dumps(cleanup),
        ) as run:
            result = _timing_cleanup(request)
        self.assertEqual(result["cleanup"], cleanup)
        command = run.call_args.args[0]
        self.assertIn("webapp_ir_api", command)
        self.assertIn("/app/scripts/full_matrix_live/timing_probe.py", command)
        self.assertIn("--cleanup-only", command)


if __name__ == "__main__":
    unittest.main()
