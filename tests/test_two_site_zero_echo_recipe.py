"""Offline zero-echo application contracts for deterministic Qoder helpers."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import two_site_apply_reviewed as apply_helper
from scripts import two_site_codex_autopilot as ap
from scripts.two_site_ai_config import AI_CONFIG_REL, AI_RUNTIME_REL
from tests.two_site_generation_fixture import install_current_contract


ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md"
PERSIAN_CONTENT = '{"greeting":"درود به همگی","description":"گچپژ ۱۲۳ — نشانه‌های یونیکد"}\n'


@pytest.fixture
def prepared_apply(tmp_path, monkeypatch):
    target = tmp_path / "messages_fa.json"
    original = '{"greeting":"سلام دنیا"}\n'.encode()
    target.write_bytes(original)
    target.chmod(0o640)
    item = {"path": target.name, "is_new": False,
            "orig_sha": hashlib.sha256(original).hexdigest(), "new_content": PERSIAN_CONTENT}
    prepared = ({"head": "a" * 40}, {}, {}, {}, [item])
    prepare = Mock(return_value=prepared)
    approval = Mock(return_value={"result": {"decision": "APPROVE"}})

    def postcheck(*_args, **kwargs):
        assert kwargs == {"applied": True}
        assert target.read_bytes() == PERSIAN_CONTENT.encode()
        return {"state": "BYTES_VALID"}

    checked = Mock(side_effect=postcheck)
    monkeypatch.setattr(apply_helper.packet, "_prepare_proposal", prepare)
    monkeypatch.setattr(apply_helper.final_review, "validate_review_receipt", approval)
    monkeypatch.setattr(apply_helper.ct, "review", checked)
    return tmp_path, target, original, item, prepare, approval, checked


def invoke_cli(root, monkeypatch):
    monkeypatch.setattr("sys.argv", ["two_site_apply_reviewed.py", "--root", str(root),
        "--task", "task.json", "--worker", "worker", "--reviewer", "reviewer", "--sha", "b" * 64])
    return apply_helper.main()


def test_helper_applies_exact_persian_unicode_bytes_without_echo(prepared_apply, monkeypatch, capsys):
    root, target, _original, _item, prepare, approval, checked = prepared_apply
    assert invoke_cli(root, monkeypatch) == 0
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["state"] == "APPLIED_BYTES_VERIFIED" and result["git_committed"] is False
    assert result["changed_paths"] == [target.name]
    assert target.read_bytes() == PERSIAN_CONTENT.encode()
    assert target.stat().st_mode & 0o777 == 0o640
    assert prepare.call_count == 2 and checked.call_count == 1
    assert approval.call_args.kwargs == {"expected_source_head": "a" * 40, "require_approve": True}
    assert output.err == "" and "درود" not in output.out and "گچپژ" not in output.out
    assert PERSIAN_CONTENT not in output.out


@pytest.mark.parametrize("failure", ["missing", "stale", "changes-required", "ambiguous"])
def test_missing_or_rejected_approval_never_applies(prepared_apply, monkeypatch, capsys, failure):
    root, target, original, _item, prepare, approval, checked = prepared_apply
    approval.side_effect = apply_helper.final_review.QoderReviewError(failure)
    assert invoke_cli(root, monkeypatch) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "APPLY_FAILED" and result["applied_paths"] == []
    assert result["requires_reconciliation"] is False
    assert target.read_bytes() == original and prepare.call_count == 1
    checked.assert_not_called()


@pytest.mark.parametrize("mutation", ["path-escape", "absolute-path", "wrong-sha", "wrong-type", "invalid-utf8"])
def test_malformed_prepared_bytes_are_not_applied(prepared_apply, monkeypatch, capsys, mutation):
    root, target, original, item, _prepare, _approval, checked = prepared_apply
    if mutation == "path-escape":
        item["path"] = "../escape.json"
    elif mutation == "absolute-path":
        item["path"] = str(target)
    elif mutation == "wrong-sha":
        item["orig_sha"] = "c" * 64
    elif mutation == "wrong-type":
        item["new_content"] = 123
    else:
        item["new_content"] = "\ud800"
    assert invoke_cli(root, monkeypatch) == 2
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["state"] == "APPLY_FAILED" and result["attempted_paths"] == []
    assert target.read_bytes() == original and PERSIAN_CONTENT not in output.out
    checked.assert_not_called()


def test_helper_failure_is_propagated_without_payload_echo(prepared_apply, monkeypatch, capsys):
    root, target, original, _item, prepare, approval, checked = prepared_apply
    prepare.side_effect = RuntimeError(PERSIAN_CONTENT)
    assert invoke_cli(root, monkeypatch) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "RuntimeError"
    assert target.read_bytes() == original and PERSIAN_CONTENT not in output.out + output.err
    approval.assert_not_called()
    checked.assert_not_called()


def test_write_failure_is_propagated_without_source_echo(prepared_apply, monkeypatch, capsys):
    root, target, original, _item, _prepare, _approval, checked = prepared_apply
    monkeypatch.setattr(apply_helper, "_stage", Mock(side_effect=OSError(PERSIAN_CONTENT)))
    assert invoke_cli(root, monkeypatch) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["code"] == "OSError"
    assert target.read_bytes() == original and PERSIAN_CONTENT not in output.out + output.err
    checked.assert_not_called()


def test_prompt_uses_deterministic_helpers_and_preserves_gates():
    prompt = PROMPT_PATH.read_text()
    assert "Do not repeatedly discover already-documented helper APIs by reading helper source" in prompt
    assert "Preserve all mandatory skill/memory/Stage/Assignment startup and dependency-source reads" in prompt
    review = prompt.index("python3 scripts/two_site_qoder_review.py --task")
    apply = prompt.index("python3 scripts/two_site_apply_reviewed.py --task")
    staging = prompt.index("Use ordinary Bash git add -- <exact accepted proposal paths>")
    assert review < apply < staging
    assert "bound APPROVE verdict" in prompt and "missing, stale or ambiguous approval" in prompt
    assert "Never print, manually rewrite or regenerate" in prompt
    assert "--reviewer <id> --sha <sha> --applied BEFORE staging" in prompt
    assert "Never claim tests not run" in prompt
    assert "Supervisor exact-proposal validation" in prompt
    assert "functions.exec" not in prompt and "tools.apply_patch" not in prompt
    assert "require_escalated" not in prompt and "code_mode" not in prompt
    assert "signing-policy bypass" in prompt and "hooks bypass" in prompt


def test_prompt_size_remains_bounded():
    # The old 18KB snapshot budget predated independent Qoder review and its
    # containment/native-ownership contracts. The runtime bound stays enforced.
    assert len(PROMPT_PATH.read_bytes()) < ap.MAX_PROMPT_BYTES


def test_active_coordinator_command_uses_configured_qoder_without_codex_js(tmp_path):
    install_current_contract(tmp_path)
    schema = tmp_path / ap.SCHEMA_REL
    schema.parent.mkdir(parents=True, exist_ok=True)
    schema.write_bytes((ROOT / ap.SCHEMA_REL).read_bytes())
    tool = tmp_path / "qoder-fixture"
    tool.write_text("#!/bin/sh\nexit 0\n")
    run = tmp_path / "run"
    run.mkdir()
    runtime = tmp_path / AI_RUNTIME_REL
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text(json.dumps({"schema_version": 1, "coordinator": {
        "path": str(tool), "version": "fixture", "sha256": "a" * 64}}))
    runtime.chmod(0o600)
    settings = json.loads((tmp_path / AI_CONFIG_REL).read_text())["roles"]["coordinator"]
    command = ap.coordinator_command(tmp_path, tool, run, "bounded coordinator prompt")
    assert command[0] == str(tool)
    assert command[command.index("--model") + 1] == settings["model"]
    assert command[command.index("--reasoning-effort") + 1] == settings["reasoning_effort"]
    assert command[-1] == "--print" and "bounded coordinator prompt" not in command
    assert "--no-session-persistence" in command and "--strict-mcp-config" in command
    system = command[command.index("--append-system-prompt") + 1]
    assert "TERMINAL_RESULT_SCHEMA_JSON:" in system
    assert "code_mode" not in command and "--approve-for-me" not in command
