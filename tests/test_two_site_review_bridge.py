"""Stage review bridge with synthetic local CLI only; never contact a provider."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import two_site_ai_config as config
from scripts import two_site_review_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]


def write(path, value, *, raw=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if raw else json.dumps(value).encode()); path.chmod(0o600)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root = tmp_path
    write(root / config.AI_CONFIG_REL, (ROOT / config.AI_CONFIG_REL).read_bytes(), raw=True)
    write(root / bridge.AUTHORIZATION_REL,
          ("authorization: APPROVED\n" + bridge.AUTHORIZATION_SCOPE + "\n").encode(), raw=True)
    binary = root / "fake-qodercli"
    binary.write_text('''#!/usr/bin/python3
import json,sys
if '--version' in sys.argv: print('1.2.3')
elif 'status' in sys.argv: print(json.dumps({'logged_in':True}))
else:
 sys.stdin.read()
 model=sys.argv[sys.argv.index('--model')+1]
 print(json.dumps({'type':'system','subtype':'init','session_id':'synthetic-schema-session','model':model,'tools':['Read','mcp_list'],'mcp_servers':[],'plugins':[]}))
 print(json.dumps({'type':'result','subtype':'success','is_error':False,'stop_reason':'end_turn','session_id':'synthetic-schema-session','num_turns':1,'result':json.dumps({'verdict':'APPROVE'})}))
'''); binary.chmod(0o700)
    write(root / config.AI_RUNTIME_REL, {"schema_version": 1, "coordinator": {
        "path": str(binary), "version": "1.2.3", "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}})
    schema = root / "docs/schema.json"
    write(schema, {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
        "required": ["verdict"], "properties": {"verdict": {"enum": ["APPROVE"]}}, "additionalProperties": False})
    output_dir = root / "output"; output_dir.mkdir(mode=0o700)
    monkeypatch.setenv("TWO_SITE_COORDINATOR_SANDBOX", "1")
    return root, schema, output_dir / "receipt.json"


def test_check_only_uses_identity_and_has_no_generation(setup, monkeypatch):
    root, _schema, _output = setup
    from scripts import two_site_qoder_driver
    def forbidden(*args, **kwargs): raise AssertionError("Check may not start generation")
    monkeypatch.setattr(two_site_qoder_driver, "qoder_command", forbidden)
    result = bridge.check(root)
    assert result["state"] == "READY" and result["provider"] == "qoder"
    assert result["model_calls"] == 0
    assert result["model"] == config.role_settings(root, "final_reviewer")["model"]


def test_schema_valid_result_and_truthful_execution_sidecar_are_published(setup):
    root, schema, output = setup
    proof = bridge.review(root, schema, output, "SYNTHETIC SOURCE DIFF ONLY")
    assert json.loads(output.read_text()) == {"verdict": "APPROVE"}
    assert json.loads(Path(str(output) + ".execution.json").read_text()) == proof
    assert proof["provider"] == "qoder" and proof["exit_code"] == 0
    assert proof["normal_exit_observed"] is True
    assert proof["model_config"] == config.role_settings(root, "final_reviewer")
    assert proof["session_id"] == "synthetic-schema-session"
    assert proof["result_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert proof["schema_role_aliases"]["CODEX_FINAL_REVIEWER"].startswith("historical SCHEMA_ROLE")
    assert "SYNTHETIC SOURCE DIFF ONLY" not in json.dumps(proof)
    assert (root / proof["run_dir"] / "stream.jsonl").is_file()


def test_old_openai_authorization_is_not_qoder_consent(setup):
    root, schema, output = setup
    write(root / bridge.AUTHORIZATION_REL,
          b"authorization: APPROVED\nscope: TWO_SITE_REFACTOR_SOURCE_AND_EVIDENCE_TO_OPENAI_CODEX\n", raw=True)
    with pytest.raises(bridge.ReviewBridgeError, match="NOT_AUTHORIZED"):
        bridge.review(root, schema, output, "Synthetic")
    assert not output.exists()


def test_schema_failure_does_not_publish_an_eligible_receipt(setup):
    root, schema, output = setup
    value = json.loads(schema.read_text()); value["required"].append("missing")
    write(schema, value)
    with pytest.raises(bridge.ReviewBridgeError, match="RESULT_SCHEMA"):
        bridge.review(root, schema, output, "Synthetic")
    assert not output.exists() and not Path(str(output) + ".execution.json").exists()


def test_remote_schema_reference_is_rejected_before_dispatch(setup, monkeypatch):
    root, schema, output = setup
    write(schema, {"$ref": "https://example.invalid/not-permitted.json"})
    monkeypatch.setattr(bridge.driver, "qoder_identity", lambda *a: pytest.fail("No CLI probe or model allowed"))
    with pytest.raises(bridge.ReviewBridgeError, match="EXTERNAL_SCHEMA_REF"):
        bridge.review(root, schema, output, "Synthetic")


def test_output_custody_and_no_overwrite_are_preserved(setup):
    root, schema, output = setup
    output.write_text("ORIGINAL")
    with pytest.raises(bridge.ReviewBridgeError, match="OUTPUT_EXISTS"):
        bridge.review(root, schema, output, "Synthetic")
    assert output.read_text() == "ORIGINAL"
    output.unlink(); output.parent.chmod(0o755)
    with pytest.raises(bridge.ReviewBridgeError, match="OUTPUT_CUSTODY"):
        bridge.review(root, schema, output, "Synthetic")


def test_all_legacy_wrappers_keep_gate_checks_but_have_no_codex_network_path():
    for name in ("final", "closeout", "governance"):
        source = (ROOT / f"scripts/two_site_codex_{name}_review.sh").read_text()
        assert 'command -v codex' not in source and '"$codex_bin"' not in source
        assert 'codex_model=' not in source and 'model_reasoning_effort' not in source
        assert "QODER_SOURCE_REVIEW.md" in source and "TO_OPENAI_CODEX" not in source
        assert '"$review_bridge" --root "$repo_root"' in source
        assert '--schema "$schema_path" --output-last-message' in source
        assert ".execution.json" in source and "published_receipt_sha256" in source
        assert "historical SCHEMA_ROLE" in source
        assert "reviewer_authority" in source and "reviewed_epoch" in source
        assert "cat-file -e" in source and "refactor/two-site-architecture" in source
    closeout = (ROOT / "scripts/two_site_codex_closeout_review.sh").read_text()
    verify_branch = closeout.split("if $verify_commit; then", 1)[1].split("exit 0", 1)[0]
    assert "$review_bridge" not in verify_branch and "qoder" not in verify_branch.lower()
    assert "single-parent" in verify_branch and "patch_hash_commit" in verify_branch
