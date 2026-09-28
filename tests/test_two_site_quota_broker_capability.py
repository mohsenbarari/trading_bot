from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_provider_broker as broker
from tests.test_two_site_quota_broker_claim_recovery import _make_continuation_request
from tests.test_two_site_quota_broker_serve_gate import _valid_response


class TwoSiteQuotaBrokerCapabilityTest(unittest.TestCase):
    def test_idle_readiness_marker_across_all_sites_and_omitted_gate(self) -> None:
        for gate_arg, expected in [(None, False), (False, False), (True, True)]:
            with self.subTest(gate_arg=gate_arg):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    status_calls: list[dict[str, Any]] = []
                    orig_write = broker._write_status

                    def spy_write(path: Any, status: str, **kwargs: Any) -> None:
                        if status == "IDLE":
                            status_calls.append(kwargs)
                        orig_write(path, status, **kwargs)

                    def stop_on_idle(_seconds: float) -> None:
                        broker._STOP = True

                    kwargs: dict[str, Any] = {"once": False, "install_signal_handlers": False}
                    if gate_arg is not None:
                        kwargs["quota_continuation_enabled"] = gate_arg

                    with mock.patch.object(broker, "_write_status", side_effect=spy_write), \
                            mock.patch.object(broker.time, "sleep", side_effect=stop_on_idle):
                        exit_code = broker.serve(root, **kwargs)

                    self.assertEqual(exit_code, 0)
                    self.assertEqual(len(status_calls), 2)
                    for kw in status_calls:
                        ready = kw["quota_continuation_ready"]
                        self.assertIs(type(ready), bool)
                        self.assertIs(ready, expected)

    def test_prequeued_continuation_exercises_disabled_and_enabled_gate(self) -> None:
        for gate_enabled, expected_state in [(False, "BROKER_ERROR"), (True, "COMPLETED")]:
            with self.subTest(gate_enabled=gate_enabled):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    _, _, requests, claimed, responses = broker.broker_paths(root)
                    req = _make_continuation_request()
                    req_id = req["request_id"]
                    req_path = requests / f"{req_id}.json"
                    broker._atomic_new_json(req_path, req, broker.MAX_REQUEST_BYTES)
                    self.assertEqual(req_path.stat().st_mode & 0o777, 0o600)

                    status_calls: list[dict[str, Any]] = []
                    orig_write = broker._write_status

                    def spy_write(path: Any, status: str, **kwargs: Any) -> None:
                        if status == "IDLE":
                            status_calls.append(kwargs)
                        orig_write(path, status, **kwargs)

                    expected_resp = _valid_response(req)
                    with mock.patch.object(broker, "_write_status", side_effect=spy_write), \
                            mock.patch.object(
                                broker,
                                "execute_intent_bound_continuation",
                                return_value=expected_resp,
                            ) as mock_adapter:
                        exit_code = broker.serve(
                            root,
                            once=True,
                            install_signal_handlers=False,
                            quota_continuation_enabled=gate_enabled,
                        )

                    self.assertEqual(exit_code, 0)
                    if gate_enabled:
                        mock_adapter.assert_called_once()
                    else:
                        mock_adapter.assert_not_called()

                    resp_path = responses / f"{req_id}.json"
                    self.assertTrue(resp_path.is_file())
                    self.assertFalse((claimed / f"{req_id}.json").exists())
                    with resp_path.open("r", encoding="utf-8") as f:
                        resp = json.load(f)

                    self.assertEqual(resp["state"], expected_state)
                    if gate_enabled:
                        self.assertEqual(resp, expected_resp)
                    else:
                        self.assertIn("quota continuation is disabled", resp["stderr"])

                    self.assertEqual(len(status_calls), 2)
                    for kw in status_calls:
                        ready = kw["quota_continuation_ready"]
                        self.assertIs(type(ready), bool)
                        self.assertIs(ready, gate_enabled)

    def test_nonbool_gate_rejects_pre_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, status_path, requests, _, _ = broker.broker_paths(root)
            req = _make_continuation_request()
            req_id = req["request_id"]
            req_path = requests / f"{req_id}.json"
            broker._atomic_new_json(req_path, req, broker.MAX_REQUEST_BYTES)

            orig_write = broker._write_status
            with mock.patch.object(broker, "_write_status", side_effect=orig_write) as spy_write:
                with self.assertRaises(broker.BrokerError) as cm:
                    broker.serve(
                        root,
                        once=True,
                        install_signal_handlers=False,
                        quota_continuation_enabled=1,
                    )

            self.assertEqual(str(cm.exception), "quota_continuation_enabled must be an exact bool")
            spy_write.assert_not_called()
            self.assertFalse(status_path.exists())
            self.assertTrue(req_path.is_file())


if __name__ == "__main__":
    unittest.main()
