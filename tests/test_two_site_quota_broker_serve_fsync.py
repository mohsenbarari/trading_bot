from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import json
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest import mock

from scripts import two_site_provider_broker as broker
from tests.test_two_site_quota_broker_claim_recovery import _make_continuation_request
from tests.test_two_site_quota_broker_serve_gate import _valid_response


class TestTwoSiteQuotaBrokerServeFsync(unittest.TestCase):
    def test_serve_fsync_ordering_on_continuation_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, _, requests, claimed, responses = broker.broker_paths(root)
            req = _make_continuation_request()
            req_id = req["request_id"]
            broker._atomic_new_json(requests / f"{req_id}.json", req, broker.MAX_REQUEST_BYTES)

            response_path = broker._response_path(responses, req_id)
            claim_path = claimed / f"{req_id}.json"

            post_pub_fsyncs: list[tuple[Path, bool]] = []
            response_published = False

            orig_atomic = broker._atomic_new_json
            orig_fsync = broker.fsync_private_record_directory

            def spy_atomic(path: Any, *args: Any, **kwargs: Any) -> Any:
                nonlocal response_published
                res = orig_atomic(path, *args, **kwargs)
                if Path(path) == response_path:
                    response_published = True
                return res

            def spy_fsync(dir_path: Any, *args: Any, **kwargs: Any) -> Any:
                res = orig_fsync(dir_path, *args, **kwargs)
                if response_published:
                    post_pub_fsyncs.append((Path(dir_path), claim_path.exists()))
                return res

            with mock.patch.object(
                broker,
                "execute_intent_bound_continuation",
                side_effect=lambda _root, r: _valid_response(r),
            ) as mock_adapter, mock.patch.object(
                broker, "_atomic_new_json", side_effect=spy_atomic
            ), mock.patch.object(
                broker, "fsync_private_record_directory", side_effect=spy_fsync
            ):
                exit_code = broker.serve(
                    root,
                    once=True,
                    install_signal_handlers=False,
                    quota_continuation_enabled=True,
                )

            self.assertEqual(exit_code, 0)
            mock_adapter.assert_called_once()
            self.assertEqual(
                post_pub_fsyncs,
                [(responses, True), (claimed, False)],
            )
            self.assertTrue(response_path.exists())
            self.assertFalse(claim_path.exists())

    def test_serve_fsync_failure_on_response_preserves_claim_and_recovery_hard_stops(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, _, requests, claimed, responses = broker.broker_paths(root)
            req = _make_continuation_request()
            req_id = req["request_id"]
            broker._atomic_new_json(requests / f"{req_id}.json", req, broker.MAX_REQUEST_BYTES)

            response_path = broker._response_path(responses, req_id)
            claim_path = claimed / f"{req_id}.json"

            orig_fsync = broker.fsync_private_record_directory

            def fail_response_fsync(dir_path: Any, *args: Any, **kwargs: Any) -> Any:
                if Path(dir_path) == responses and response_path.exists():
                    raise OSError("simulated response fsync failure")
                return orig_fsync(dir_path, *args, **kwargs)

            with mock.patch.object(
                broker,
                "execute_intent_bound_continuation",
                side_effect=lambda _root, r: _valid_response(r),
            ) as mock_adapter:
                with mock.patch.object(
                    broker, "fsync_private_record_directory", side_effect=fail_response_fsync
                ):
                    with self.assertRaises(OSError) as cm:
                        broker.serve(
                            root,
                            once=True,
                            install_signal_handlers=False,
                            quota_continuation_enabled=True,
                        )
                    self.assertIn("simulated response fsync failure", str(cm.exception))

                self.assertTrue(response_path.exists())
                self.assertTrue(claim_path.exists())
                mock_adapter.assert_called_once()

                with self.assertRaises(broker.BrokerError):
                    broker._recover_interrupted_claims(root, claimed, responses)

                self.assertTrue(response_path.exists())
                saved_resp = json.loads(response_path.read_text(encoding="utf-8"))
                self.assertEqual(saved_resp["request_id"], req_id)
                self.assertEqual(saved_resp["state"], "COMPLETED")
                self.assertTrue(claim_path.exists())
                mock_adapter.assert_called_once()


if __name__ == "__main__":
    unittest.main()
