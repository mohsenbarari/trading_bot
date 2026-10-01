"""Offline regression tests for zero-echo patch recipe, coordinator prompt guidance, and code_mode."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import two_site_codex_autopilot as ap

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = ROOT / "docs/refactor/two-server/templates/CODEX_AUTOPILOT_PROMPT.md"

NODE_BIN = shutil.which("node")
if not NODE_BIN:
    raise RuntimeError("node executable is required but not found in PATH")

REALISTIC_PERSIAN_PATCH = (
    "*** Begin Patch\n"
    "*** Update File: config/messages_fa.json\n"
    "@@\n"
    '- "greeting": "سلام دنیا",\n'
    '+ "greeting": "درود به همگی",\n'
    '+ "description": "این یک وصله آزمایشی است با حروف فارسی و نشانههای یونیکد: گچپژ ۱۲۳",\n'
    "*** End Patch\n"
)


def extract_recipe(prompt_text: str) -> str:
    start_marker = "// BEGIN_ZERO_ECHO_APPLY"
    end_marker = "// END_ZERO_ECHO_APPLY"
    start_idx = prompt_text.find(start_marker)
    if start_idx == -1:
        raise ValueError(f"Marker {start_marker} not found in prompt")
    end_idx = prompt_text.find(end_marker, start_idx)
    if end_idx == -1:
        raise ValueError(f"Marker {end_marker} not found in prompt")
    recipe = prompt_text[start_idx + len(start_marker):end_idx].strip()
    return recipe


def run_recipe_in_node(
    recipe_code: str,
    tmp_path: Path,
    exec_result: dict | None = None,
    exec_error: str | None = None,
    apply_error: str | None = None,
    task: str = "tasks/task-001.json",
    worker: str = "worker-w1",
    reviewer: str = "reviewer-r1",
    sha: str = "a" * 64,
) -> tuple[subprocess.CompletedProcess, dict]:
    applied_file = tmp_path / "applied_patch.txt"
    harness = f"""
import fs from 'node:fs';

const execCalls = [];
const applyCalls = [];
const emittedText = [];

const text = (val) => {{
  emittedText.push(typeof val === 'string' ? val : JSON.stringify(val));
}};

const originalLog = console.log;
console.log = (...args) => {{
  const line = args.map(a => (typeof a === 'string' ? a : JSON.stringify(a))).join(' ');
  emittedText.push(line);
}};

const task = {json.dumps(task)};
const worker = {json.dumps(worker)};
const reviewer = {json.dumps(reviewer)};
const sha = {json.dumps(sha)};

const tools = {{
  exec_command: async (opts) => {{
    execCalls.push(opts);
    {"throw new Error(" + json.dumps(exec_error) + ");" if exec_error else ("return " + json.dumps(exec_result) + ";")}
  }},
  apply_patch: async (patchStr) => {{
    applyCalls.push(patchStr);
    fs.writeFileSync({json.dumps(str(applied_file))}, patchStr, 'utf-8');
    {"throw new Error(" + json.dumps(apply_error) + ");" if apply_error else "return { success: true };"}
  }}
}};

let recipeError = null;
try {{
{recipe_code}
}} catch (err) {{
  recipeError = err?.message || String(err);
}}

const report = {{
  execCalls,
  applyCount: applyCalls.length,
  emittedText,
  recipeError,
}};

originalLog(JSON.stringify(report));
if (recipeError) {{
  process.exit(1);
}}
"""
    proc = subprocess.run(
        [NODE_BIN, "--input-type=module", "-e", harness],
        capture_output=True,
        text=True,
        timeout=15,
    )
    report = {}
    stdout_lines = proc.stdout.strip().splitlines()
    if stdout_lines:
        try:
            report = json.loads(stdout_lines[-1])
        except json.JSONDecodeError:
            pass
    return proc, report


def test_recipe_applies_persian_unicode_patch_zero_echo(tmp_path):
    prompt_text = PROMPT_PATH.read_text("utf-8")
    recipe = extract_recipe(prompt_text)
    proc, report = run_recipe_in_node(
        recipe,
        tmp_path,
        exec_result={"exit_code": 0, "output": REALISTIC_PERSIAN_PATCH},
        task="tasks/zero-echo-test.json",
        worker="worker-persian-1",
        reviewer="reviewer-persian-1",
        sha="b" * 64,
    )
    assert proc.returncode == 0
    assert report.get("recipeError") is None
    assert report.get("applyCount") == 1

    applied_file = tmp_path / "applied_patch.txt"
    assert applied_file.exists()
    applied_content = applied_file.read_text("utf-8")
    assert applied_content == REALISTIC_PERSIAN_PATCH
    assert applied_file.read_bytes() == REALISTIC_PERSIAN_PATCH.encode("utf-8")
    assert applied_content.endswith("\n")

    assert report.get("emittedText") == []
    assert "سلام دنیا" not in proc.stdout
    assert "درود به همگی" not in proc.stdout
    assert "گچپژ" not in proc.stdout
    assert REALISTIC_PERSIAN_PATCH not in proc.stdout
    assert REALISTIC_PERSIAN_PATCH not in proc.stderr

    exec_calls = report.get("execCalls", [])
    assert len(exec_calls) == 1
    call = exec_calls[0]
    assert call.get("max_output_tokens") >= 200000
    cmd = call.get("cmd", "")
    assert "patch" in cmd
    assert "--task tasks/zero-echo-test.json" in cmd
    assert "--worker worker-persian-1" in cmd
    assert "--reviewer reviewer-persian-1" in cmd
    assert f"--sha {'b' * 64}" in cmd


@pytest.mark.parametrize("bad_result", [
    {"exit_code": 1, "output": REALISTIC_PERSIAN_PATCH},
    {"exit_code": 2, "output": REALISTIC_PERSIAN_PATCH},
    {"output": REALISTIC_PERSIAN_PATCH},
    {"exit_code": 0, "session_id": "session-abc-123", "output": REALISTIC_PERSIAN_PATCH},
    {"exit_code": 0, "sessionId": "session-abc-123", "output": REALISTIC_PERSIAN_PATCH},
    {"exit_code": 0, "output": 123456},
    {"exit_code": 0, "output": None},
    {"exit_code": 0, "output": REALISTIC_PERSIAN_PATCH.replace("*** Begin Patch\n", "")},
    {"exit_code": 0, "output": REALISTIC_PERSIAN_PATCH.replace("*** End Patch\n", "")},
    {"exit_code": 0, "output": "random string without patch framing"},
])
def test_recipe_rejects_malformed_exec_results_without_apply(tmp_path, bad_result):
    prompt_text = PROMPT_PATH.read_text("utf-8")
    recipe = extract_recipe(prompt_text)
    proc, report = run_recipe_in_node(recipe, tmp_path, exec_result=bad_result)
    assert proc.returncode != 0
    assert report.get("recipeError") is not None
    assert report.get("applyCount") == 0
    assert not (tmp_path / "applied_patch.txt").exists()


def test_recipe_fails_on_rejected_exec_promise_without_apply(tmp_path):
    prompt_text = PROMPT_PATH.read_text("utf-8")
    recipe = extract_recipe(prompt_text)
    proc, report = run_recipe_in_node(recipe, tmp_path, exec_error="helper execution failed")
    assert proc.returncode != 0
    assert "helper execution failed" in str(report.get("recipeError"))
    assert report.get("applyCount") == 0
    assert not (tmp_path / "applied_patch.txt").exists()


def test_recipe_propagates_rejected_apply_promise(tmp_path):
    prompt_text = PROMPT_PATH.read_text("utf-8")
    recipe = extract_recipe(prompt_text)
    proc, report = run_recipe_in_node(
        recipe,
        tmp_path,
        exec_result={"exit_code": 0, "output": REALISTIC_PERSIAN_PATCH},
        apply_error="disk read-only on apply",
    )
    assert proc.returncode != 0
    assert "disk read-only on apply" in str(report.get("recipeError"))
    assert report.get("applyCount") == 1


def test_prompt_contains_coordinator_guidance_and_preserves_gates():
    prompt_text = PROMPT_PATH.read_text("utf-8")

    assert "Do not repeatedly discover already-documented helper APIs by reading helper source" in prompt_text
    assert "use\n   documented commands, inspect exact source only for an actual error or missing contract" in prompt_text or \
           "use documented commands, inspect exact source only for an actual error or missing contract" in prompt_text.replace("\n   ", " ")
    assert "For coverage discovery, start with exact files from previous next_action and bounded" in prompt_text
    assert "not broad matches of common words that dump many fixture/assertion lines" in prompt_text
    assert "Preserve all mandatory skill/memory/Stage/Assignment startup and dependency-source reads" in prompt_text

    assert "BEGIN_ZERO_ECHO_APPLY" in prompt_text
    assert "END_ZERO_ECHO_APPLY" in prompt_text
    assert "tools.exec_command" in prompt_text
    assert "max_output_tokens: 200000" in prompt_text
    assert "tools.apply_patch(result.output)" in prompt_text
    assert "Never call text(result), text(result.output), console.log, shell cat/tee" in prompt_text

    assert "python3 scripts/two_site_cycle_packet.py packet --task <path> --worker <id>" in prompt_text
    assert "(use --full only when full candidate context is needed; all changed code must be reviewed)" in prompt_text
    assert "Rerun python3 scripts/two_site_cycle_tools.py review --task <path> --worker <id>" in prompt_text
    assert "--reviewer <id> --sha <sha> --applied BEFORE staging" in prompt_text
    assert "inspect git diff --stat / --name-status / --check and exact staged paths/modes, without reprinting" in prompt_text
    assert "Run actual focused tests plus proportional regression" in prompt_text
    assert "Never claim tests not run" in prompt_text
    assert "Use gpt-6.1-sol/xhigh" in prompt_text

    assert "Use final Git metadata escalation only for narrowly scoped git add -- <exact accepted proposal paths>" in prompt_text
    assert "Prohibit git add -A, git add ., globs, unrelated paths" in prompt_text
    assert "single-parent commit on refactor/two-site-architecture" in prompt_text


def test_prompt_size_growth_is_bounded():
    prompt_text = PROMPT_PATH.read_text("utf-8")
    prompt_bytes = len(prompt_text.encode("utf-8"))
    assert prompt_bytes < ap.MAX_PROMPT_BYTES
    # Current mandatory safety/workflow clauses measure 17262 bytes; leaves <738 bytes headroom.
    assert prompt_bytes < 18000
    recipe = extract_recipe(prompt_text)
    assert len(recipe.encode("utf-8")) < 2000


def test_codex_command_enables_code_mode_and_preserves_arguments(tmp_path):
    codex_bin = tmp_path / "codex"
    root_dir = tmp_path / "repo"
    run_dir = tmp_path / "run"
    prompt_str = "prompt instructions for test"

    cmd = ap.codex_command(codex_bin, root_dir, run_dir, prompt_str)

    assert cmd.count("--enable") == 1
    enable_idx = cmd.index("--enable")
    assert cmd[enable_idx + 1] == "code_mode"

    assert cmd[0] == str(codex_bin)
    assert cmd[1] == "exec"
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == ap.MODEL
    assert f'model_reasoning_effort="{ap.REASONING}"' in cmd
    assert ap.MODEL == "gpt-6.1-sol"
    assert ap.REASONING == "xhigh"

    assert "--approve-for-me" in cmd
    assert "--ephemeral" in cmd
    assert "--json" in cmd
    assert "--cd" in cmd
    cd_idx = cmd.index("--cd")
    assert cmd[cd_idx + 1] == str(root_dir)
    assert "--output-schema" in cmd
    schema_idx = cmd.index("--output-schema")
    assert cmd[schema_idx + 1] == str(root_dir / ap.SCHEMA_REL)
    assert "--output-last-message" in cmd
    output_idx = cmd.index("--output-last-message")
    assert cmd[output_idx + 1] == str(run_dir / "result.json")
    assert cmd[-1] == prompt_str
