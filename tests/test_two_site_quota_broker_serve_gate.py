from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import traceback
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
    def test_wait_only_requires_same_idle_broker_and_claim_cleanup_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _state, _status, requests, claimed, responses = broker.broker_paths(root)
            req = _make_continuation_request()
            response = _valid_response(req)
            response_path = responses / (req["request_id"] + ".json")
            claim = claimed / (req["request_id"] + ".json")
            broker._atomic_new_json(response_path, response, broker.MAX_RESPONSE_BYTES)
            broker._atomic_new_json(claim, req, broker.MAX_REQUEST_BYTES)
            states = [dict(state="RUNNING", pid=123, started_at=10.0, updated_at=time.time(), request_id=req["request_id"]),
                      dict(state="IDLE", pid=123, started_at=10.0, updated_at=time.time())]

            def sleep(_seconds):
                self.assertTrue(response_path.is_file())
                claim.unlink()  # Controlled fixture simulates Broker's own cleanup.

            with mock.patch.object(broker, "_read_broker_status", side_effect=states), \
                 mock.patch.object(broker.time, "sleep", side_effect=sleep) as slept, \
                 mock.patch.object(broker, "_atomic_new_json", side_effect=AssertionError("enqueue forbidden")), \
                 mock.patch.object(broker, "execute_intent_bound_continuation", side_effect=AssertionError("Provider forbidden")), \
                 mock.patch("scripts.two_site_quota_broker_route._check_pre_dispatch_stop"):
                actual = broker._wait_existing_response(root, req, (123, 10.0), req["timeout"])
            self.assertEqual(actual, response)
            slept.assert_called_once_with(broker.POLL_SECONDS)
            self.assertEqual(list(requests.iterdir()), [])
            self.assertEqual(response_path.read_text(), json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def test_wait_only_rejects_restart_stop_and_deadline_without_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _state, _status, _requests, _claimed, responses = broker.broker_paths(root)
            req = _make_continuation_request()
            path = responses / (req["request_id"] + ".json")
            broker._atomic_new_json(path, _valid_response(req), broker.MAX_RESPONSE_BYTES)
            raw = path.read_bytes()
            with mock.patch.object(broker, "_read_broker_status", return_value=dict(state="IDLE", pid=123, started_at=11.0, updated_at=time.time())), \
                 mock.patch("scripts.two_site_quota_broker_route._check_pre_dispatch_stop"):
                with self.assertRaisesRegex(broker.BrokerError, "heartbeat.*mismatched"):
                    broker._wait_existing_response(root, req, (123, 10.0), req["timeout"])
            with mock.patch("scripts.two_site_quota_broker_route._check_pre_dispatch_stop", side_effect=broker.BrokerError("STOP fixture")):
                with self.assertRaisesRegex(broker.BrokerError, "STOP fixture"):
                    broker._wait_existing_response(root, req, (123, 10.0), req["timeout"])
            (root / "STOP").symlink_to(root / "missing-stop-target")
            with mock.patch("scripts.two_site_quota_broker_route._check_pre_dispatch_stop"):
                with self.assertRaisesRegex(broker.BrokerError, "wait stopped"):
                    broker._wait_existing_response(root, req, (123, 10.0), req["timeout"])
            (root / "STOP").unlink()
            with mock.patch.object(broker.time, "monotonic", side_effect=[0.0, 31.0]), \
                 mock.patch.object(broker, "_overall_timeout", return_value=0) as bound, \
                 mock.patch("scripts.two_site_quota_broker_route._check_pre_dispatch_stop"):
                with self.assertRaisesRegex(broker.BrokerError, "deadline exceeded"):
                    broker._wait_existing_response(root, req, (123, 10.0), req["timeout"])
            bound.assert_called_once_with(req["timeout"])
            self.assertEqual(path.read_bytes(), raw)

    def test_cli_opt_in_is_explicit_and_default_off(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            for flag, expected in (([], {}), (["--quota-continuation-enabled"], {"quota_continuation_enabled": True})):
                with mock.patch("sys.argv", ["broker", "--root", str(root), "serve", *flag]), \
                     mock.patch.object(broker, "canonical_root", return_value=root), \
                     mock.patch.object(broker, "serve", return_value=0) as serve:
                    self.assertEqual(broker.main(), 0)
                serve.assert_called_once_with(root, **expected)

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
                    mock.patch.object(broker, "publish_terminal_quota_record", side_effect=AssertionError("publish called")) as mock_publish, \
                    mock.patch('scripts.two_site_quota_continuation_terminal_storage.publish_terminal_continuation_record'):
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
                with self.assertRaises(broker.BrokerError) as cm:
                    broker.serve(
                        root,
                        once=True,
                        install_signal_handlers=False,
                        quota_continuation_enabled=True,
                    )
                self.assertNotIn("simulated adapter failure", str(cm.exception))
                self.assertIsNone(cm.exception.__cause__)
                self.assertNotIn(
                    "simulated adapter failure",
                    "".join(traceback.format_exception(cm.exception)),
                )
                mock_adapter.assert_called_once()
                resp_path = responses / f"{req_id}.json"
                self.assertFalse(resp_path.exists())
                claim_path = claimed / f"{req_id}.json"
                self.assertTrue(claim_path.is_file())
                claimed_bytes_before = claim_path.read_bytes()
                with self.assertRaises(broker.BrokerError):
                    broker._recover_interrupted_claims(root, claimed, responses)
                self.assertEqual(mock_adapter.call_count, 1)
                self.assertTrue(claim_path.is_file())
                self.assertEqual(claim_path.read_bytes(), claimed_bytes_before)
                self.assertFalse(resp_path.exists())


if __name__ == "__main__":
    unittest.main()
