"""Static AST regressions for retired direct WA-FI <-> WA-IR routes.

This test deliberately does not import the inspected implementation modules.
It verifies actual call nodes rather than accepting a marker string in a
comment, docstring, or unrelated helper.
"""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
FENCE_CALL = "assert_legacy_direct_fi_ir_transport_retired"


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _functions(path: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _call_lines(node: ast.AST, dotted_names: tuple[str, ...]) -> list[int]:
    return [
        call.lineno
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and _dotted_name(call.func) in dotted_names
    ]


class LegacyDirectPeerTransportStaticAstTests(unittest.TestCase):
    def test_every_core_peer_url_call_has_a_registered_fenced_boundary(self) -> None:
        # This inventory is AST-derived. A future direct core caller cannot
        # evade the retirement review merely by avoiding a source-text marker.
        expected = {
            ("core/customer_invite.py", "_fetch_iran_sync_health"),
            ("core/customer_invite_forwarding.py", "forward_customer_invite_to_iran"),
            ("core/invitation_creation_forwarding.py", "forward_standard_invitation_to_iran"),
            ("core/offer_expiry_forwarding.py", "forward_offer_expiry_to_home_server"),
            ("core/session_authority.py", "fetch_remote_session_authority"),
            ("core/telegram_otp_transport.py", "forward_telegram_otp_delivery"),
            ("core/telegram_registration_transport.py", "_post_signed_iran_command"),
            ("core/trade_forwarding.py", "forward_trade_to_home_server"),
        }
        actual: set[tuple[str, str]] = set()

        for source_path in (REPO_ROOT / "core").rglob("*.py"):
            relative_path = source_path.relative_to(REPO_ROOT).as_posix()
            if relative_path == "core/server_routing.py":
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=relative_path)
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _call_lines(function, ("peer_server_url_for",)):
                    actual.add((relative_path, function.name))

        self.assertEqual(actual, expected)
        for path, function_name in actual:
            with self.subTest(path=path, function=function_name):
                self.assertTrue(_call_lines(_functions(path)[function_name], (FENCE_CALL,)))

    def test_outbound_callers_have_an_executable_fence_before_network_primitives(self) -> None:
        # ``network_calls`` are deliberately concrete AST call targets, not
        # source-text markers.  The asserted fence must precede all of them.
        specs = (
            ("core/trade_forwarding.py", "forward_trade_to_home_server", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/offer_expiry_forwarding.py", "forward_offer_expiry_to_home_server", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/session_authority.py", "fetch_remote_session_authority", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/customer_invite_forwarding.py", "forward_customer_invite_to_iran", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/invitation_creation_forwarding.py", "forward_standard_invitation_to_iran", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/telegram_registration_transport.py", "_post_signed_iran_command", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/telegram_otp_transport.py", "forward_telegram_otp_delivery", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/customer_invite.py", "_fetch_iran_sync_health", ("peer_server_url_for", "httpx.AsyncClient")),
            ("core/sync_push.py", "_get_client", ("assert_runtime_sync_transport_allowed", "httpx.Client")),
            ("core/sync_push.py", "_do_push", ("time.time", "_get_client", "client.post")),
            ("core/sync_push.py", "push_sync_direct", ("default_peer_server_url", "_executor.submit")),
            ("core/sync_worker.py", "send_sync_item", ("time.time", "client.post")),
            ("core/sync_worker.py", "main", ("assert_background_job_authority", "redis.Redis", "default_peer_server_url")),
        )

        for path, function_name, network_calls in specs:
            with self.subTest(path=path, function=function_name):
                function = _functions(path)[function_name]
                fence_lines = _call_lines(function, (FENCE_CALL,))
                primitive_lines = _call_lines(function, network_calls)
                self.assertTrue(fence_lines, "missing executable direct-transport fence call")
                self.assertTrue(primitive_lines, "regression fixture no longer has a direct primitive")
                self.assertLess(min(fence_lines), min(primitive_lines))

    def test_iran_notification_relay_executes_fence_before_any_legacy_push(self) -> None:
        function = _functions("core/notifications.py")["send_telegram_message"]
        self.assertTrue(_call_lines(function, (FENCE_CALL,)))
        self.assertFalse(_call_lines(function, ("push_sync_direct",)))

    def test_iran_connectivity_target_factory_is_only_a_fence(self) -> None:
        function = _functions("core/connectivity.py")["_iran_connectivity_target_url"]
        self.assertTrue(_call_lines(function, (FENCE_CALL,)))
        self.assertFalse(_call_lines(function, ("getattr", "httpx.AsyncClient")))

    def test_retired_sync_routes_have_real_fastapi_dependency_calls(self) -> None:
        path = "api/routers/sync.py"
        tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for function_name in ("receive_sync_data", "resync_from_changelog"):
            with self.subTest(function=function_name):
                function = functions[function_name]
                dependency_calls = [
                    call
                    for decorator in function.decorator_list
                    for call in ast.walk(decorator)
                    if isinstance(call, ast.Call)
                    and _dotted_name(call.func) == "Depends"
                    and call.args
                    and isinstance(call.args[0], ast.Name)
                    and call.args[0].id == "_reject_retired_legacy_direct_sync_transport"
                ]
                self.assertEqual(len(dependency_calls), 1)

        guard = functions["_reject_retired_legacy_direct_sync_transport"]
        exceptions = [
            call
            for call in ast.walk(guard)
            if isinstance(call, ast.Call) and _dotted_name(call.func) == "HTTPException"
        ]
        self.assertEqual(len(exceptions), 1)
        status_values = [
            keyword.value.value
            for keyword in exceptions[0].keywords
            if keyword.arg == "status_code" and isinstance(keyword.value, ast.Constant)
        ]
        self.assertEqual(status_values, [410])


if __name__ == "__main__":
    unittest.main()
