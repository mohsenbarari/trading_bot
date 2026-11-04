"""Real failed pytest observations; no exception payload or source-name export."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import two_site_agent_sandbox as sdk
from scripts import two_site_pre_review_pytest_gate as gate
from scripts import two_site_proposal_pipeline as pipeline

_spec = importlib.util.spec_from_file_location(
    "mock_diagnostic_fixture", Path(__file__).with_name("test_two_site_agent_sandbox_overlay.py")
)
_overlay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_overlay)
SUBJECT = "tests/test_mock_subject.py"


@pytest.fixture(autouse=True)
def native_route(monkeypatch):
    _overlay.install_native_namespace_adapter(monkeypatch)


@pytest.fixture
def repo(tmp_path):
    return _overlay.create_test_repo(tmp_path)


def native(repo, source):
    return sdk.run_tests(repo, [SUBJECT], overlays={SUBJECT: source},
                         timeout=20, structured_diagnostics=True)


def receipt(result):
    return {
        "state": "FAILED", "proposal_sha256": "a" * 64,
        "selectors": [SUBJECT], "unsupported_specs": [],
        "returncode": result["returncode"], "stdout_tail": result["stdout"],
        "stderr_tail": result["stderr"], "snapshot_files": result["snapshot_files"],
        "snapshot_bytes": result["snapshot_bytes"], "isolation": result["isolation"],
        "receipt_version": 2, "pytest_diagnostics": result["pytest_diagnostics"],
        "pytest_diagnostics_error": result["pytest_diagnostics_error"],
    }


def test_real_mock_failure_reaches_existing_feedback_without_changing_failure(repo):
    code = """import unittest
from unittest.mock import MagicMock, AsyncMock
class MockBoundaryTests(unittest.TestCase):
    def test_magic(self):
        private_local = MagicMock()
        self.assertIs(type(private_local), MagicMock)
    def test_async(self):
        other_private_local = AsyncMock()
        self.assertEqual(type(other_private_local), AsyncMock)
"""
    result = native(repo, code)
    assert result["returncode"] == 1
    artifact = result["pytest_diagnostics"]
    assert result["pytest_diagnostics_error"] is None
    assert artifact["partial"] is False
    assert [r["exception"] for r in artifact["records"]] == ["builtins.AssertionError"] * 2
    shapes = [r["assertion"] for r in artifact["records"]]
    assert sorted(shapes) == sorted([
        "UNITTEST_MOCK_TYPE_V1:assertIs(type(_),MagicMock);if_unittest_mock_use_isinstance_and_check_sync_async",
        "UNITTEST_MOCK_TYPE_V1:assertEqual(type(_),AsyncMock);if_unittest_mock_use_isinstance_and_check_sync_async",
    ])
    assert all(r["partial"] is False for r in artifact["records"])
    assert all(r["nodeid"].startswith(SUBJECT + "::MockBoundaryTests::") for r in artifact["records"])
    rendered = json.dumps(artifact)
    assert "private_local" not in rendered and "other_private_local" not in rendered
    feedback = pipeline.test_gate_feedback(receipt(result), "a" * 64)
    assert feedback["reviewer"] == "test-gate"
    assert "UNKNOWN" not in json.dumps(feedback)
    assert all(shape in json.dumps(feedback) for shape in shapes)
    assert "2 failed" in result["stdout"]
    good = code.replace("self.assertIs(type(private_local), MagicMock)",
                        "self.assertIsInstance(private_local, MagicMock)").replace(
        "self.assertEqual(type(other_private_local), AsyncMock)",
        "self.assertIsInstance(other_private_local, AsyncMock)")
    fixed = native(repo, good)
    assert fixed["returncode"] == 0 and "2 passed" in fixed["stdout"]
    assert fixed["pytest_diagnostics"] == {"version": 1, "records": [], "partial": False}


def test_same_mock_cause_survives_names_and_line_moves(repo):
    first = """import unittest
from unittest.mock import MagicMock
class Case(unittest.TestCase):
    def test_call(self):
        hidden = MagicMock()
        self.assertIs(type(hidden), MagicMock)
"""
    second = first.replace("hidden", "different_name").replace("        self.assertIs", "\n\n        self.assertIs")
    r1, r2 = native(repo, first), native(repo, second)
    assert r1["returncode"] == r2["returncode"] == 1
    assert pipeline.test_failure_signature(receipt(r1)) == pipeline.test_failure_signature(receipt(r2))
    assert r1["pytest_diagnostics"] == r2["pytest_diagnostics"]


def test_untrusted_messages_calls_and_non_assertion_do_not_become_facts(repo):
    code = """import unittest
from unittest.mock import MagicMock
class Case(unittest.TestCase):
    def test_message(self):
        hidden = MagicMock()
        self.assertIs(type(hidden), MagicMock, 'PRIVATE_MESSAGE_SENTINEL')
    def test_callback(self):
        def callback():
            raise AssertionError('PRIVATE_CALLBACK_SENTINEL')
        self.assertIs(callback(), MagicMock)
    def test_keyword(self):
        hidden = MagicMock()
        self.assertIs(first=type(hidden), second=MagicMock, msg='PRIVATE_KEYWORD_SENTINEL')
    def test_literal(self):
        self.assertEqual('PRIVATE_LITERAL_SENTINEL', 'different')
"""
    result = native(repo, code)
    assert result["returncode"] == 1
    artifact = result["pytest_diagnostics"]
    assert len(artifact["records"]) == 4 and artifact["partial"] is True
    assert all(r["assertion"] is None for r in artifact["records"])
    for record in artifact["records"]:
        assert record["partial"] is (record["exception"] == "builtins.AssertionError")
    assert "PRIVATE_" not in json.dumps(artifact)
    assert "PRIVATE_MESSAGE_SENTINEL" in result["stdout"]  # ordinary output retained, not trusted/exported


def test_source_oversize_keeps_unknown_with_original_failure(repo):
    code = "#" + "x" * 40000 + "\nimport unittest\nfrom unittest.mock import MagicMock\nclass Case(unittest.TestCase):\n    def test_call(self):\n        m=MagicMock()\n        self.assertIs(type(m), MagicMock)\n"
    result = native(repo, code)
    assert result["returncode"] == 1
    artifact = result["pytest_diagnostics"]
    assert artifact["partial"] is True
    assert artifact["records"][0]["assertion"] is None
    assert artifact["records"][0]["exception"] == "builtins.AssertionError"
    assert len(json.dumps(artifact).encode()) <= 8192


def test_ingress_rejects_forged_variants_exception_mismatch_and_overflow():
    fact = "UNITTEST_MOCK_TYPE_V1:assertIs(type(_),MagicMock);if_unittest_mock_use_isinstance_and_check_sync_async"
    def artifact(value=fact, exception="builtins.AssertionError"):
        return {"version": 1, "partial": False, "records": [{
            "nodeid": SUBJECT + "::Case::test_call", "phase": "call", "outcome": "failed",
            "exception": exception, "assertion": value, "partial": False,
        }]}
    accepted = artifact()
    assert sdk._diagnostic_artifact(json.dumps(accepted).encode()) == accepted
    for value in (fact + "\nPRIVATE_SENTINEL", fact.replace("MagicMock", "PrivateName"),
                  fact.replace("type(_)", "callback()"), fact.replace("assertIs", "private_method"),
                  fact + "x" * 8192, fact.replace("_", "PRIVATE", 1)):
        with pytest.raises(sdk.SandboxError):
            sdk._diagnostic_artifact(json.dumps(artifact(value)).encode())
    with pytest.raises(sdk.SandboxError):
        sdk._diagnostic_artifact(json.dumps(artifact(exception="builtins.ValueError")).encode())
    class Text(str):
        def __eq__(self, other):
            raise AssertionError("PRIVATE_CALLBACK")
    assert sdk._assertion_shape(Text(fact)) is False
    assert len(fact.encode()) < 512 and sdk.DIAGNOSTIC_LIMIT == 8192


def test_real_hasattr_magicmock_failure_is_precise_without_changing_failure(repo):
    code = """import inspect
import unittest
from unittest.mock import MagicMock, AsyncMock
class Case(unittest.TestCase):
    def test_sync_boundary(self):
        private_summary_method = MagicMock()
        self.assertFalse(hasattr(private_summary_method, 'await_count'))
"""
    result = native(repo, code)
    fact = "UNITTEST_MOCK_HASATTR_V1:assertFalse(hasattr(_,await_count));if_unittest_mock_check_isinstance_AsyncMock_and_iscoroutinefunction"
    assert result["returncode"] == 1 and "1 failed" in result["stdout"]
    artifact = result["pytest_diagnostics"]
    assert result["pytest_diagnostics_error"] is None and artifact["partial"] is False
    assert artifact["records"][0]["exception"] == "builtins.AssertionError"
    assert artifact["records"][0]["assertion"] == fact
    assert "private_summary_method" not in json.dumps(artifact)
    feedback = pipeline.test_gate_feedback(receipt(result), "a" * 64)
    assert fact in json.dumps(feedback) and "UNKNOWN" not in json.dumps(feedback)
    moved = code.replace("private_summary_method", "renamed_private").replace("        self.assertFalse", "\n\n        self.assertFalse")
    assert pipeline.test_failure_signature(receipt(result)) == pipeline.test_failure_signature(receipt(native(repo, moved)))
    fixed = code.replace("self.assertFalse(hasattr(private_summary_method, 'await_count'))", "self.assertIsInstance(private_summary_method, MagicMock)\n        self.assertNotIsInstance(private_summary_method, AsyncMock)\n        self.assertFalse(inspect.iscoroutinefunction(private_summary_method))")
    assert native(repo, fixed)["returncode"] == 0


def test_hasattr_hint_excludes_messages_callbacks_properties_keywords_and_untrusted_exceptions(repo):
    code = """import unittest
from unittest.mock import MagicMock
class Case(unittest.TestCase):
    def test_message(self):
        private = MagicMock()
        self.assertFalse(hasattr(private, 'await_count'), 'PRIVATE_MESSAGE')
    def test_callback(self):
        def private_callback(): raise AssertionError('PRIVATE_CALLBACK')
        self.assertFalse(hasattr(private_callback(), 'await_count'))
    def test_property(self):
        private = MagicMock()
        self.assertFalse(hasattr(private, 'PRIVATE_PROPERTY'))
    def test_keyword(self):
        private = MagicMock()
        self.assertFalse(expr=hasattr(private, 'await_count'))
"""
    result = native(repo, code)
    assert result["returncode"] == 1
    data = result["pytest_diagnostics"]
    assert len(data["records"]) == 4 and all(r["assertion"] is None for r in data["records"])
    assert "PRIVATE_" not in json.dumps(data) and data["partial"] is True
    fact = "UNITTEST_MOCK_HASATTR_V1:assertFalse(hasattr(_,await_count));if_unittest_mock_check_isinstance_AsyncMock_and_iscoroutinefunction"
    value = {"version": 1, "partial": False, "records": [{"nodeid": SUBJECT + "::Case::test_call", "phase": "call", "outcome": "failed", "exception": "builtins.AssertionError", "assertion": fact, "partial": False}]}
    assert sdk._diagnostic_artifact(json.dumps(value).encode()) == value
    for replacement in (fact + "\nPRIVATE_PAYLOAD", fact.replace("await_count", "PRIVATE_PROPERTY"), fact.replace("_", "callback()", 1), fact + "x" * 8192):
        changed = json.loads(json.dumps(value)); changed["records"][0]["assertion"] = replacement
        with pytest.raises(sdk.SandboxError): sdk._diagnostic_artifact(json.dumps(changed).encode())
    value["records"][0]["exception"] = "builtins.ValueError"
    with pytest.raises(sdk.SandboxError): sdk._diagnostic_artifact(json.dumps(value).encode())
