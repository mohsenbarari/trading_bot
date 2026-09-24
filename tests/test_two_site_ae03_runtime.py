import json
from unittest.mock import MagicMock
import pytest
try:
    import scripts.two_site_codex_autopilot as ap
    from scripts.two_site_assignment_scope import AssignmentScope
except ModuleNotFoundError:
    import two_site_codex_autopilot as ap  # type: ignore[no-redef]
    from two_site_assignment_scope import AssignmentScope  # type: ignore[no-redef]


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    state_root = root / "state"
    (state_root / "runs").mkdir(parents=True)
    codex = root / "codex"
    codex.touch()
    scope = MagicMock(spec=AssignmentScope, assignment_id="s", stage_id="st", sha256="0" * 64)
    before = {"head": "0" * 40, "protected_refs": {}, "control_blobs": {}, "assignment_scope": scope}
    monkeypatch.setattr(ap, "validate_repository", lambda *a, **k: before)
    monkeypatch.setattr(ap, "prompt_for_cycle", lambda *a, **k: "p")
    monkeypatch.setattr(ap, "codex_identity", lambda *a, **k: {"sha256": "x", "runtime": {}})
    monkeypatch.setattr(ap, "codex_command", lambda *a, **k: ["echo"])
    monkeypatch.setattr(ap, "_atomic_new_json", lambda *a, **k: None)
    return root, codex, state_root, before


def test_before_model_ordering_and_args(run_env, monkeypatch):
    root, codex, state_root, before = run_env
    events = []
    def hook(b):
        events.append(("hook", b))
    def fake_popen(*a, **k):
        events.append(("popen",))
        raise RuntimeError("stop")
    monkeypatch.setattr(ap.subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError, match="stop"):
        ap.run_cycle(root, codex, state_root, 1, 10, before_model=hook)
    assert events == [("hook", before), ("popen",)]


def test_before_model_exception_prevents_popen(run_env, monkeypatch):
    root, codex, state_root, _ = run_env
    events = []
    monkeypatch.setattr(ap.subprocess, "Popen", lambda *a, **k: events.append("popen"))
    def err_hook(b):
        raise ValueError("hook error")
    with pytest.raises(ValueError, match="hook error"):
        ap.run_cycle(root, codex, state_root, 1, 10, before_model=err_hook)
    assert "popen" not in events
    def ki_hook(b):
        raise KeyboardInterrupt("halt")
    with pytest.raises(KeyboardInterrupt, match="halt"):
        ap.run_cycle(root, codex, state_root, 2, 10, before_model=ki_hook)
    assert "popen" not in events
    status_files = sorted((state_root / "runs").glob("*/status.json"))
    assert len(status_files) == 2
    for sf in status_files:
        assert json.loads(sf.read_text())["state"] == "FAILED"


def test_before_model_default_none_preserves_path(run_env, monkeypatch):
    root, codex, state_root, _ = run_env
    events = []
    def fake_popen(*a, **k):
        events.append("popen")
        raise RuntimeError("stop")
    monkeypatch.setattr(ap.subprocess, "Popen", fake_popen)
    with pytest.raises(RuntimeError, match="stop"):
        ap.run_cycle(root, codex, state_root, 1, 10)
    assert events == ["popen"]
