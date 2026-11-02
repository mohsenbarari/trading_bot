"""Exercise the actual dashboard handler with synthetic streams, never a server.

Load the exact factory AST rather than importing collector/model dependencies or
constructing a live SQLite SessionStore. Only its external constructors are mocked.
"""
from __future__ import annotations

import ast
from email.message import Message
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import parse_qs, urlsplit


SOURCE = Path(__file__).resolve().parents[1] / "apps/coin_rate_estimator/live_server.py"


def actual_factory():
    import inspect
    lines = SOURCE.read_text(encoding="utf-8").splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if line.startswith("def handler_factory(")]
    assert len(starts) == 1, "exactly one top-level dashboard handler factory is required"
    start = starts[0]
    # Tokenize only this real function block. Unrelated dashboard rendering uses
    # Python 3.12 f-strings and must not prevent CI's Python 3.11 handler tests.
    block = "".join(inspect.getblock(lines[start:]))
    syntax = ast.parse(block, str(SOURCE), feature_version=(3, 11))
    assert len(syntax.body) == 1 and isinstance(syntax.body[0], ast.FunctionDef)
    factory = syntax.body[0]
    assert factory.name == "handler_factory"
    ast.increment_lineno(factory, start)
    names = {"MAX_LOGIN_FORM_BYTES", "LOGIN_BODY_TIMEOUT_SECONDS"}
    constants = []
    for index, line in enumerate(lines):
        if not any(re.match(rf"^{name}\s*=", line) for name in names):
            continue
        statement = ast.parse(line, str(SOURCE), feature_version=(3, 11))
        assert len(statement.body) == 1 and isinstance(statement.body[0], ast.Assign)
        node = statement.body[0]
        assert len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
        assert type(ast.literal_eval(node.value)) in (int, float)
        constants.append(ast.increment_lineno(node, index))
    assert len(constants) == 2 and {node.targets[0].id for node in constants} == names
    import time
    namespace = {
        "BaseHTTPRequestHandler": BaseHTTPRequestHandler, "HTTPStatus": HTTPStatus,
        "RUNTIME_ROOT": Path("/synthetic-dashboard-only"), "SessionStore": Mock(),
        "ConditionReviewService": Mock(), "DEFAULT_CONDITION_OWNER_PACK": Path("/synthetic-owner"),
        "DEFAULT_CONDITION_MODEL": Path("/synthetic-model"),
        "_env_bounded_int": lambda _name, default, **_kwargs: default,
        "urlsplit": urlsplit, "parse_qs": parse_qs, "re": re, "time": time,
        "hmac": hmac, "json": json,
        "load_dashboard_credentials": Mock(return_value=("operator", "test pass")),
        "render_login_page": Mock(return_value=b"synthetic invalid credentials"),
    }
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *constants, factory], type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)
    assert namespace["handler_factory"].__code__.co_firstlineno == start + 1
    return namespace


class DashboardLoginBodySecurityTests(unittest.TestCase):
    def setUp(self):
        for target in ("subprocess.run", "subprocess.Popen", "socket.socket.connect",
                       "socket.create_connection", "socket.getaddrinfo", "urllib.request.urlopen",
                       "urllib.request.OpenerDirector.open", "sqlite3.connect"):
            blocker = patch(target, side_effect=AssertionError("dashboard fixture cannot use host/network/DB"))
            blocker.start(); self.addCleanup(blocker.stop)
        self.scope = actual_factory()
        self.sessions = self.scope["SessionStore"].return_value
        self.sessions.create_session.return_value = "synthetic-token"
        self.Handler = self.scope["handler_factory"](
            "/estimate", Mock(), market_db=Path("/synthetic-market"),
            conversation_db=Path("/synthetic-conversation"), calibration_db=Path("/synthetic-calibration"),
            feedback_db=Path("/synthetic-feedback"), calibration_corpus_db=Path("/synthetic-corpus"),
            coin_group_staging_db=None, write_token=None, refresh_estimate=Mock(), group_live_control=Mock(),
        )

    def request(self, body=b"", *, lengths=None, encoding=None, reader=None, previous_timeout=None):
        handler = self.Handler.__new__(self.Handler)
        handler.path = "/estimate/login"
        handler.headers = Message()
        for value in lengths or []:
            handler.headers.add_header("Content-Length", value)
        if encoding is not None:
            handler.headers.add_header("Transfer-Encoding", encoding)
        handler.rfile = reader or Mock(wraps=io.BytesIO(body))
        handler.wfile = io.BytesIO()
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = previous_timeout
        handler.close_connection = False
        handler._headers = Mock()
        handler._redirect_with_cookie = Mock()
        handler.do_POST()
        return handler

    def assert_rejected_before_read(self, lengths, status, *, encoding=None):
        handler = self.request(lengths=lengths, encoding=encoding)
        self.assertEqual(handler._headers.call_args.args[0], status)
        self.assertTrue(handler.close_connection)
        handler.rfile.read1.assert_not_called()
        handler.rfile.read.assert_not_called()
        handler.connection.gettimeout.assert_not_called()
        self.scope["load_dashboard_credentials"].assert_not_called()
        self.sessions.create_session.assert_not_called()

    def test_oversized_length_rejected_without_read_or_credential_access(self):
        for value in ("8193", "1000000000", "9" * 6000):
            with self.subTest(length=value[:20]):
                self.assert_rejected_before_read([value], HTTPStatus.REQUEST_ENTITY_TOO_LARGE)

    def test_malformed_negative_and_non_ascii_lengths_rejected_before_read(self):
        for value in ("-1", "+1", "1.0", "abc", "", "1,1", "٣"):
            with self.subTest(length=value):
                self.assert_rejected_before_read([value], HTTPStatus.BAD_REQUEST)

    def test_duplicate_content_lengths_even_equal_are_rejected(self):
        self.assert_rejected_before_read(["1", "1"], HTTPStatus.BAD_REQUEST)

    def test_chunked_or_other_transfer_encoding_is_never_read_as_a_login_form(self):
        for encoding in ("chunked", "identity"):
            with self.subTest(encoding=encoding):
                self.assert_rejected_before_read(["1"], HTTPStatus.BAD_REQUEST, encoding=encoding)

    def test_ordinary_urlencoded_login_retains_session_and_redirect_contract(self):
        body = b"username=operator&password=test+pass"
        handler = self.request(body, lengths=[str(len(body))])
        self.sessions.create_session.assert_called_once_with("operator")
        handler._headers.assert_not_called()
        handler._redirect_with_cookie.assert_called_once_with(
            "/estimate", "coin_session_token=synthetic-token; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax",
        )
        self.assertFalse(handler.close_connection)
        handler.connection.settimeout.assert_called_with(None)

    def test_missing_and_zero_content_length_keep_empty_invalid_credentials_behavior(self):
        for lengths in ([], ["0"]):
            with self.subTest(lengths=lengths):
                handler = self.request(lengths=lengths)
                self.assertEqual(handler._headers.call_args.args[0], HTTPStatus.UNAUTHORIZED)
                handler.rfile.read1.assert_not_called()
                handler.connection.gettimeout.assert_not_called()
                self.assertFalse(handler.close_connection)
        self.sessions.create_session.assert_not_called()

    def test_incorrect_credentials_still_fail_without_creating_a_session(self):
        body = b"username=operator&password=incorrect"
        handler = self.request(body, lengths=[str(len(body))])
        self.assertEqual(handler._headers.call_args.args[0], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(handler.wfile.getvalue(), b"synthetic invalid credentials")
        self.sessions.create_session.assert_not_called()
        handler._redirect_with_cookie.assert_not_called()

    def test_short_body_is_bad_request_before_credentials_and_restores_timeout(self):
        handler = self.request(b"abc", lengths=["5"], previous_timeout=2.0)
        self.assertEqual(handler._headers.call_args.args[0], HTTPStatus.BAD_REQUEST)
        self.assertEqual(handler.rfile.read1.call_args_list, [call(5), call(2)])
        self.assertTrue(handler.close_connection)
        handler.connection.settimeout.assert_called_with(2.0)
        self.scope["load_dashboard_credentials"].assert_not_called()

    def test_read_timeout_returns_408_and_restores_socket_timeout(self):
        reader = Mock()
        reader.read1.side_effect = TimeoutError("synthetic stalled body")
        handler = self.request(lengths=["5"], reader=reader)
        self.assertEqual(handler._headers.call_args.args[0], HTTPStatus.REQUEST_TIMEOUT)
        self.assertTrue(handler.close_connection)
        handler.connection.settimeout.assert_called_with(None)
        self.scope["load_dashboard_credentials"].assert_not_called()

    def test_trickle_cannot_extend_total_deadline_or_read_after_it(self):
        reader = Mock()
        reader.read1.side_effect = [b"a", b"b", AssertionError("read after total deadline")]
        with patch.object(self.scope["time"], "monotonic", side_effect=[10.0, 10.0, 14.9, 15.0]):
            handler = self.request(lengths=["3"], reader=reader)
        self.assertEqual(handler._headers.call_args.args[0], HTTPStatus.REQUEST_TIMEOUT)
        self.assertEqual(reader.read1.call_count, 2)
        self.assertAlmostEqual(handler.connection.settimeout.call_args_list[1].args[0], 0.1)
        handler.connection.settimeout.assert_called_with(None)
        self.scope["load_dashboard_credentials"].assert_not_called()

    def test_existing_shorter_timeout_is_not_extended(self):
        body = b"username=operator&password=test+pass"
        handler = self.request(body, lengths=[str(len(body))], previous_timeout=0.5)
        self.assertEqual(handler.connection.settimeout.call_args_list, [call(0.5), call(0.5)])
        self.sessions.create_session.assert_called_once_with("operator")

    def test_exact_8k_form_is_accepted_and_read_is_bounded(self):
        body = b"username=operator&password=test+pass&ignored="
        body += b"x" * (8192 - len(body))
        handler = self.request(body, lengths=["8192"])
        handler.rfile.read1.assert_called_once_with(8192)
        self.sessions.create_session.assert_called_once_with("operator")

    def test_valid_leading_zeros_do_not_trigger_unbounded_integer_conversion(self):
        handler = self.request(b"x", lengths=["0" * 6000 + "1"])
        handler.rfile.read1.assert_called_once_with(1)
        self.assertEqual(handler._headers.call_args.args[0], HTTPStatus.UNAUTHORIZED)

    def test_read_error_is_bad_request_and_never_creates_a_session(self):
        reader = Mock()
        reader.read1.side_effect = OSError("synthetic disconnected client")
        handler = self.request(lengths=["5"], reader=reader)
        self.assertEqual(handler._headers.call_args.args[0], HTTPStatus.BAD_REQUEST)
        self.assertTrue(handler.close_connection)
        self.sessions.create_session.assert_not_called()


if __name__ == "__main__":
    unittest.main()
