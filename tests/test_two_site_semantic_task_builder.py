"""Offline unit tests for two_site_semantic_task_builder."""
from __future__ import annotations
import copy, hashlib, pytest
from pathlib import Path
from scripts import two_site_proposal_runner as runner
from scripts.two_site_semantic_task_builder import SemanticTaskBuilderError, build_task_identity, validate_task_identity

def _setup_repo(tmp_path: Path, stage: str = "stage_1") -> tuple[Path, dict]:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "gap.md").write_text("# Gap\ndetails", encoding="utf-8")
    (root / "ctx.py").write_text("def ctx_func(): pass\n", encoding="utf-8")
    exc_bytes = b"def exc_func():\n    return 42\n"
    (root / "exc.py").write_bytes(exc_bytes)
    excerpts = [{"path": "exc.py", "sha256": hashlib.sha256(exc_bytes).hexdigest(), "symbols": ["exc_func"], "ranges": [{"start": 0, "end": 16, "text": "def exc_func():\n"}]}]
    return root, {"gap": "gap.md#anchor", "scenario": "scen_1", "context_files": ["ctx.py"], "allowed_files": ["new.py"], "excerpts": excerpts}

@pytest.mark.parametrize("missing_allowed", [False, True])
def test_valid_manifest_and_bounded_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_allowed: bool) -> None:
    root, args = _setup_repo(tmp_path)
    monkeypatch.setattr("scripts.two_site_semantic_task_builder.load_active_assignment_scope", lambda *a, **k: {"stage_id": "stage_1"})
    reader_calls = []
    orig_bounded = runner.bounded_file_bytes
    def spy_bounded(r, target, max_bytes, label):
        reader_calls.append((r, target, max_bytes, label))
        return orig_bounded(r, target, max_bytes, label)
    monkeypatch.setattr(runner, "bounded_file_bytes", spy_bounded)
    if not missing_allowed:
        (root / "new.py").write_text("print('hello')\n", encoding="utf-8")
    ident = build_task_identity(root, args["gap"], args["scenario"], args["context_files"], args["allowed_files"], args["excerpts"])
    assert (ident["source_manifest"]["new.py"] is None) if missing_allowed else (ident["source_manifest"]["new.py"] is not None)
    assert ident["source_manifest"]["ctx.py"] is not None and ident["source_manifest"]["gap.md"] is not None
    assert len(reader_calls) >= 2
    for r, target, max_bytes, label in reader_calls:
        assert r == root and isinstance(target, Path) and max_bytes == runner.MAX_CONTEXT and isinstance(label, str)
    ident["source_manifest"]["mutated"] = "bad"
    ident["symbol_groups"].append({"path": "fake", "symbols": []})
    val = validate_task_identity(root, args["gap"], args["scenario"], args["context_files"], args["allowed_files"], args["excerpts"], build_task_identity(root, args["gap"], args["scenario"], args["context_files"], args["allowed_files"], args["excerpts"]))
    assert "mutated" not in val["source_manifest"]

@pytest.mark.parametrize("variation", ["stage", "scenario", "symbols", "source"])
def test_task_id_irrelevance_and_sensitivity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variation: str) -> None:
    root, args = _setup_repo(tmp_path)
    stage_box = ["stage_1"]
    monkeypatch.setattr("scripts.two_site_semantic_task_builder.load_active_assignment_scope", lambda *a, **k: {"stage_id": stage_box[0]})
    base = build_task_identity(root, args["gap"], args["scenario"], args["context_files"], args["allowed_files"], args["excerpts"])
    assert "task_id" not in base and "HEAD" not in base
    gap, scen, ctx, alw, exc = args["gap"], args["scenario"], args["context_files"], args["allowed_files"], copy.deepcopy(args["excerpts"])
    if variation == "stage": stage_box[0] = "stage_2"
    elif variation == "scenario": scen = "scen_2"
    elif variation == "symbols": exc[0]["symbols"] = ["exc_func", "extra_func"]
    elif variation == "source": (root / "ctx.py").write_text("def ctx_func(): return 1\n", encoding="utf-8")
    varied = build_task_identity(root, gap, scen, ctx, alw, exc)
    assert varied["fingerprint"] != base["fingerprint"]
    if variation == "source": assert varied["source_sha256"] != base["source_sha256"]

@pytest.mark.parametrize("case", ["missing_ctx", "missing_gap", "missing_exc", "missing_both", "stale_exc"])
def test_missing_and_stale_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    root, args = _setup_repo(tmp_path)
    monkeypatch.setattr("scripts.two_site_semantic_task_builder.load_active_assignment_scope", lambda *a, **k: {"stage_id": "stage_1"})
    gap, scen, ctx, alw, exc = args["gap"], args["scenario"], list(args["context_files"]), list(args["allowed_files"]), copy.deepcopy(args["excerpts"])
    if case == "missing_ctx": (root / "ctx.py").unlink()
    elif case == "missing_gap": (root / "gap.md").unlink()
    elif case == "missing_exc": (root / "exc.py").unlink()
    elif case == "missing_both": ctx.append("both.py"); alw.append("both.py")
    elif case == "stale_exc": exc[0]["sha256"] = "0" * 64
    with pytest.raises(SemanticTaskBuilderError):
        build_task_identity(root, gap, scen, ctx, alw, exc)

@pytest.mark.parametrize("kind", ["not_list", "dup_list", "bad_path", "numeric_pair_range", "range_not_dict", "range_bool", "range_inverted", "range_dup", "range_overlap", "range_adjacent", "exc_dup", "exc_overlap", "forged_fp", "forged_src", "root_missing", "root_file"])
def test_malformed_inputs_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    root, args = _setup_repo(tmp_path)
    monkeypatch.setattr("scripts.two_site_semantic_task_builder.load_active_assignment_scope", lambda *a, **k: {"stage_id": "stage_1"})
    gap, scen, ctx, alw, exc = args["gap"], args["scenario"], list(args["context_files"]), list(args["allowed_files"]), copy.deepcopy(args["excerpts"])
    target_root = root
    if kind == "not_list": ctx = "not_a_list"
    elif kind == "dup_list": ctx = ["ctx.py", "ctx.py"]
    elif kind == "bad_path": ctx = ["/abs/ctx.py"]
    elif kind == "numeric_pair_range": exc[0]["ranges"] = [(0, 10)]
    elif kind == "range_not_dict": exc[0]["ranges"] = ["not_a_dict"]
    elif kind == "range_bool": exc[0]["ranges"] = [{"start": True, "end": 10, "text": "a"}]
    elif kind == "range_inverted": exc[0]["ranges"] = [{"start": 10, "end": 5, "text": "a"}]
    elif kind == "range_dup": exc[0]["ranges"] = [{"start": 0, "end": 5, "text": "a"}, {"start": 0, "end": 5, "text": "b"}]
    elif kind == "range_overlap": exc[0]["ranges"] = [{"start": 0, "end": 10, "text": "a"}, {"start": 5, "end": 15, "text": "b"}]
    elif kind in ("range_adjacent", "range_adjacent_overlap"): exc[0]["ranges"] = [{"start": 1, "end": 2, "text": "a"}, {"start": 2, "end": 3, "text": "b"}]
    elif kind == "exc_dup": exc.append(copy.deepcopy(exc[0]))
    elif kind == "exc_overlap": exc[0]["path"] = "ctx.py"
    elif kind in ("forged_fp", "forged_src"):
        ident = build_task_identity(root, gap, scen, ctx, alw, exc)
        ident["fingerprint" if kind == "forged_fp" else "source_sha256"] = "0" * 64
        with pytest.raises(SemanticTaskBuilderError):
            validate_task_identity(root, gap, scen, ctx, alw, exc, ident)
        return
    elif kind == "root_missing": target_root = tmp_path / "nonexistent"
    elif kind == "root_file": target_root = root / "ctx.py"
    with pytest.raises(SemanticTaskBuilderError):
        build_task_identity(target_root, gap, scen, ctx, alw, exc)
