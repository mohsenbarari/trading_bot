from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_provider_broker as broker
from tests.test_two_site_quota_broker_claim_recovery import _make_continuation_request


def _valid_response(req: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "request_id": req["request_id"],
        "run_prefix": req["run_prefix"],
        "source_head": req["source_head"],
        "state": "COMPLETED",
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "finished_at": time.time(),
    }


class TwoSiteQuotaBrokerServeGateTest(unittest.TestCase):
    def test_default_off_gate_rejects_without_adapter_or_executor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _state_root, _status_path, requests, claimed, responses = broker.broker_paths(root)
            req = _make_continuation_request()
            req_id = req["request_id"]
            broker._atomic_new_json(requests / f"{req_id}.json", req, broker.MAX_REQUEST_BYTES)
            mock_executor = mock.MagicMock()
            with mock.patch.object(broker, "execute_intent_bound_continuation") as mock_adapter:
                exit_code = broker.serve(
                    root,
                    once=True,
                    install_signal_handlers=False,
                    executor=mock_executor,
                )
            self.assertEqual(exit_code, 0)
            mock_adapter.assert_not_called()
            mock_executor.assert_not_called()
            resp_path = responses / f"{req_id}.json"
            self.assertTrue(resp_path.is_file())
            self.assertFalse((claimed / f"{req_id}.json").exists())
            with resp_path.open("r", encoding="utf-8") as f:
                resp = json.load(f)
            self.assertEqual(resp["state"], "BROKER_ERROR")
            self.assertEqual(resp["request_id"], req_id)
            self.assertIn("quota continuation is disabled", resp["stderr"])

    def test_explicit_on_gate_invokes_adapter_once_and_publishes_response(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _state_root, _status_path, requests, claimed, responses = broker.broker_paths(root)
            req = _make_continuation_request()
            req_id = req["request_id"]
            broker._atomic_new_json(requests / f"{req_id}.json", req, broker.MAX_REQUEST_BYTES)
            expected_resp = _valid_response(req)
            with mock.patch.object(broker, "execute_intent_bound_continuation", return_value=expected_resp) as mock_adapter, \
                    mock.patch.object(broker, "detect_quota_response", side_effect=AssertionError("detect called")) as mock_detect, \
                    mock.patch.object(broker, "publish_terminal_quota_record", side_effect=AssertionError("publish called")) as mock_publish:
                exit_code = broker.serve(
                    root,
                    once=True,
                    install_signal_handlers=False,
                    quota_continuation_enabled=True,
                )
            self.assertEqual(exit_code, 0)
            mock_adapter.assert_called_once()
            mock_detect.assert_not_called()
            mock_publish.assert_not_called()
            resp_path = responses / f"{req_id}.json"
            self.assertTrue(resp_path.is_file())
            self.assertFalse((claimed / f"{req_id}.json").exists())
            with resp_path.open("r", encoding="utf-8") as f:
                resp = json.load(f)
            self.assertEqual(resp, expected_resp)

    def test_adapter_runtime_error_yields_broker_error_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _state_root, _status_path, requests, claimed, responses = broker.broker_paths(root)
            req = _make_continuation_request()
            req_id = req["request_id"]
            broker._atomic_new_json(requests / f"{req_id}.json", req, broker.MAX_REQUEST_BYTES)
            with mock.patch.object(broker, "execute_intent_bound_continuation", side_effect=RuntimeError("simulated adapter failure")) as mock_adapter:
                exit_code = broker.serve(
                    root,
                    once=True,
                    install_signal_handlers=False,
                    quota_continuation_enabled=True,
                )
            self.assertEqual(exit_code, 0)
            mock_adapter.assert_called_once()
            resp_path = responses / f"{req_id}.json"
            self.assertTrue(resp_path.is_file())
            self.assertFalse((claimed / f"{req_id}.json").exists())
            with resp_path.open("r", encoding="utf-8") as f:
                resp = json.load(f)
            self.assertEqual(resp["state"], "BROKER_ERROR")
            self.assertEqual(resp["request_id"], req_id)
            self.assertIn("RuntimeError: simulated adapter failure", resp["stderr"])


if __name__ == "__main__":
    unittest.main()
