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


class TwoSiteBrokerCliModuleIdentityTest(unittest.TestCase):
    @staticmethod
    def _cli_code():
        import ast
        path = Path(broker.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        # Execute the actual full CLI module, omitting only automatic main().
        # Tests below call that real main explicitly with bounded scratch serve.
        last = tree.body.pop()
        if not (isinstance(last, ast.If)
                and ast.unparse(last.test) == "__name__ == '__main__'"
                and ast.unparse(last.body[0]) == "raise SystemExit(main())"):
            raise AssertionError("unexpected real CLI terminal entrypoint")
        return compile(tree, str(path), "exec"), path

    def _cli_namespace(self):
        from contextlib import contextmanager
        import sys
        from types import ModuleType
        import scripts

        @contextmanager
        def isolated():
            code, path = self._cli_code()
            cli = ModuleType("__main__")
            cli.__file__ = str(path)
            cli.__package__ = None
            cli.__spec__ = None
            # Restore both import-cache and package identities even on failure.
            with mock.patch.dict(sys.modules), mock.patch.object(scripts, "two_site_provider_broker"):
                sys.modules["__main__"] = cli
                sys.modules.pop("scripts.two_site_provider_broker", None)
                del scripts.two_site_provider_broker
                exec(code, cli.__dict__)
                yield cli
        return isolated()

    def test_direct_cli_main_signal_reaches_deep_dispatch_guard_without_provider(self) -> None:
        import importlib
        import io
        import sys
        from scripts import two_site_quota_broker_route as route

        with tempfile.TemporaryDirectory() as td, self._cli_namespace() as cli:
            root = Path(td).resolve()
            self.assertIs(importlib.import_module("scripts.two_site_provider_broker"), cli)
            self.assertIs(sys.modules["__main__"], cli)
            _state, _status, requests, claimed, responses = cli.broker_paths(root)
            request = _make_continuation_request()
            cli._atomic_new_json(requests / (request["request_id"] + ".json"), request, cli.MAX_REQUEST_BYTES)
            handlers = {}
            actual_serve = cli.serve

            def install(signum, handler):
                handlers[signum] = handler

            def one_request(directory, **kwargs):
                self.assertEqual(kwargs, {"quota_continuation_enabled": True})
                return actual_serve(directory, once=True, **kwargs)

            def after_outer_guard(directory, admitted):
                # Exact gap: serve has passed its own immediate _STOP check.
                handler = handlers[cli.signal.SIGTERM]
                self.assertIs(handler, cli._signal_stop)
                self.assertIs(handler.__globals__, cli.__dict__)
                handler(cli.signal.SIGTERM, None)  # No OS signal is delivered.
                self.assertIs(cli._STOP, True)
                return route.execute_intent_bound_continuation(directory, admitted)

            def direct_heartbeat(_path, operation, *args, **_kwargs):
                return operation(*args)  # No fixture background thread.

            with mock.patch("sys.argv", [str(Path(cli.__file__)), "--root", str(root),
                                         "serve", "--quota-continuation-enabled"]), \
                 mock.patch("sys.stderr", new=io.StringIO()), \
                 mock.patch.object(cli.os, "umask", return_value=0o077), \
                 mock.patch.object(cli, "canonical_root", return_value=root), \
                 mock.patch.object(cli, "serve", side_effect=one_request) as serving, \
                 mock.patch.object(cli.signal, "signal", side_effect=install), \
                 mock.patch.object(cli, "_execute_with_heartbeat", side_effect=direct_heartbeat), \
                 mock.patch.object(cli, "execute_intent_bound_continuation", side_effect=after_outer_guard) as adapter, \
                 mock.patch.object(route, "validate_quota_continuation_request", return_value=(request, None, None)), \
                 mock.patch.object(route, "read_quota_resume_submission_intent"), \
                 mock.patch.object(route, "dispatch_quota_continuation_once", side_effect=AssertionError("Provider forbidden")) as dispatch, \
                 mock.patch.object(route, "build_continuation_broker_response", side_effect=AssertionError("response builder forbidden")) as response:
                self.assertEqual(cli.main(), 2)
            serving.assert_called_once_with(root, quota_continuation_enabled=True)
            adapter.assert_called_once_with(root, request)
            dispatch.assert_not_called()
            response.assert_not_called()
            self.assertEqual(set(handlers), {cli.signal.SIGTERM, cli.signal.SIGHUP, cli.signal.SIGINT})
            self.assertTrue((claimed / (request["request_id"] + ".json")).is_file())
            self.assertFalse((responses / (request["request_id"] + ".json")).exists())
            self.assertEqual(list(requests.iterdir()), [])
        self.assertIs(importlib.import_module("scripts.two_site_provider_broker"), broker)

    def test_cli_alias_closes_old_split_stop_miss_and_preserves_file_stop(self) -> None:
        import importlib
        import sys
        import scripts
        from scripts import two_site_quota_broker_route as route

        with tempfile.TemporaryDirectory() as td, self._cli_namespace() as cli:
            root = Path(td)
            cli._signal_stop(cli.signal.SIGTERM, None)
            self.assertIs(cli._STOP, True)
            # Negative control recreates the old separate canonical module:
            # the real deep guard then misses the CLI-owned signal.
            with mock.patch.dict(sys.modules, {"scripts.two_site_provider_broker": broker}), \
                 mock.patch.object(scripts, "two_site_provider_broker", broker, create=True), \
                 mock.patch.object(broker, "_STOP", False):
                self.assertIsNone(route._check_pre_dispatch_stop(root))
            with self.assertRaisesRegex(cli.BrokerError, "stop requested before continuation dispatch"):
                route._check_pre_dispatch_stop(root)
            cli._STOP = False
            self.assertIsNone(route._check_pre_dispatch_stop(root))
            stop = root / ".local/two-site-refactor/autopilot/STOP"
            stop.parent.mkdir(parents=True)
            for broken in (False, True):
                with self.subTest(broken_symlink=broken):
                    if broken:
                        stop.symlink_to(stop.parent / "missing-target")
                    else:
                        stop.write_text("STOP", encoding="utf-8")
                    try:
                        with self.assertRaisesRegex(cli.BrokerError, "stop requested before continuation dispatch"):
                            route._check_pre_dispatch_stop(root)
                    finally:
                        stop.unlink()
        self.assertIs(importlib.import_module("scripts.two_site_provider_broker"), broker)

    def test_cli_alias_rejects_conflicting_cache_or_package_before_project_imports(self) -> None:
        import builtins
        import sys
        from types import ModuleType
        import scripts

        code, path = self._cli_code()
        original_import = builtins.__import__
        for conflict in ("cached_module", "cached_none", "package_attribute"):
            with self.subTest(conflict=conflict):
                cli = ModuleType("__main__")
                cli.__file__ = str(path)
                cli.__package__ = None
                project_imports = []

                def observed_import(name, *args, **kwargs):
                    if name == "scripts" or name.startswith(("scripts.", "two_site_")):
                        project_imports.append(name)
                    return original_import(name, *args, **kwargs)

                with mock.patch.dict(sys.modules), \
                     mock.patch.object(scripts, "two_site_provider_broker", broker, create=True), \
                     mock.patch("builtins.__import__", side_effect=observed_import):
                    sys.modules["__main__"] = cli
                    if conflict == "package_attribute":
                        sys.modules.pop("scripts.two_site_provider_broker", None)
                    else:
                        sys.modules["scripts.two_site_provider_broker"] = broker if conflict == "cached_module" else None
                    with self.assertRaisesRegex(RuntimeError, "conflicting canonical Broker"):
                        exec(code, cli.__dict__)
                    self.assertEqual(project_imports, [])
                    self.assertNotIn("BrokerError", cli.__dict__)
        self.assertIs(sys.modules["scripts.two_site_provider_broker"], broker)
        self.assertIs(scripts.two_site_provider_broker, broker)


if __name__ == "__main__":
    unittest.main()
