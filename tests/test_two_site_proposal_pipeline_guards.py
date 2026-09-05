import io, json, os, stat, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path: sys.path.insert(0, p)

try:
    import scripts.two_site_proposal_pipeline as pipe
except ModuleNotFoundError:
    import two_site_proposal_pipeline as pipe

FORBIDDEN_GIT = {"add", "commit", "reset", "checkout", "switch", "fetch", "push", "merge"}
EXPECTED_KEYS = {
    "state", "task_id", "source_head", "final_worker_run_id", "final_reviewer_run_id",
    "proposal_sha256", "changed_paths", "proposal_path", "reviewer_path", "correction_count", "blockers"
}

def _dummy_task_dict():
    return {"task_id": "T1", "goal": "G1", "context_files": ["tracked.py"], "allowed_files": ["tracked.py"], "requirements": ["R1"], "test_selectors": ["S1"]}

class TestProposalPipelineGuards(unittest.TestCase):
    def test_check_state_guards_and_git_recording(self):
        recorded = []
        dummy_task, dummy_sources = _dummy_task_dict(), {"tracked.py": {"sha256": "s", "content": "c"}}
        head, refs = "a" * 40, {r: "f" * 40 for r in pipe.PROTECTED_REFS}
        base = {"branch": pipe.BRANCH, "head": head, "status": "", "refs": refs, "task": dummy_task, "sources": dummy_sources}
        state = dict(base)
        def fake_git(_r, *a):
            recorded.append(a)
            if a[0] == "branch": return state["branch"]
            if a[0] == "rev-parse": return state["head"]
            if a[0] == "status": return state["status"]
            if a[0] == "show-ref":
                if state["refs"].get(a[-1]) is None: raise subprocess.CalledProcessError(1, "git")
                return state["refs"][a[-1]]
            return ""
        cases = [
            ("wrong branch", {"branch": "bad"}, "branch drift"),
            ("changed HEAD", {"head": "b" * 40}, "HEAD drift"),
            ("dirty status", {"status": "M f.py"}, "worktree drift"),
            ("task drift", {"task": {"task_id": "T2"}}, "task or source"),
            ("source drift", {"sources": {}}, "task or source"),
        ] + [(f"drift {r}", {"refs": {k: ("x" if k == r else "f" * 40) for k in pipe.PROTECTED_REFS}}, "protected refs drift") for r in pipe.PROTECTED_REFS]
        root = Path("/tmp/mock_root")
        with patch.object(pipe, "git", side_effect=fake_git), patch.object(pipe, "load_task", side_effect=lambda _r, _p: (state["task"], state["sources"])):
            pipe.check_state(root, head, refs, root / "task.json", dummy_task, dummy_sources)
            for name, overrides, err in cases:
                state.clear(); state.update(base); state.update(overrides)
                with self.assertRaises(pipe.ProposalError, msg=name) as ctx:
                    pipe.check_state(root, head, refs, root / "task.json", dummy_task, dummy_sources)
                self.assertIn(err, str(ctx.exception), name)
        self.assertTrue(recorded)
        for call in recorded:
            self.assertTrue(FORBIDDEN_GIT.isdisjoint(call), f"Forbidden git command in {call}")

    def test_run_pipeline_guards_and_real_task_reader(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            (root / "tracked.py").write_text("# code\n")
            task_dict = _dummy_task_dict()
            task_file, outside = root / "task.json", Path(td) / "out.json"
            task_file.write_text(json.dumps(task_dict))
            outside.write_text(json.dumps(task_dict))
            mock_run = MagicMock()
            with patch.object(pipe, "runner_run", mock_run):
                with self.assertRaises(pipe.ProposalError) as ctx:
                    pipe.run_pipeline(root, outside, "pfx", 300, None, {})
                self.assertIn("inside root", str(ctx.exception))
                for pfx in ("", "-bad", "bad/slash", "a" * 65):
                    with self.assertRaises(pipe.ProposalError): pipe.run_pipeline(root, task_file, pfx, 300, None, {})
                for to in (9, 901):
                    with self.assertRaises(pipe.ProposalError): pipe.run_pipeline(root, task_file, "pfx", to, None, {})
                cur_head = "not_40_hex"
                def fake_git(_r, *a):
                    if a[0] == "branch": return pipe.BRANCH
                    if a[0] == "rev-parse": return cur_head
                    if a[0] == "status": return ""
                    return "1" * 40
                with patch.object(pipe, "git", side_effect=fake_git):
                    with self.assertRaises(pipe.ProposalError) as ctx:
                        pipe.run_pipeline(root, task_file, "pfx-head", 300, None, {})
                    self.assertIn("invalid HEAD", str(ctx.exception))
                    cur_head = "a" * 40
                    sym_task = root / "sym_task.json"
                    sym_task.symlink_to(task_file)
                    with self.assertRaises(pipe.ProposalError) as ctx:
                        pipe.run_pipeline(root, sym_task, "pfx-sym", 300, None, {})
                    self.assertIn("symlink or path escape", str(ctx.exception))
                    ext_file = Path(td) / "ext.json"
                    ext_file.write_text(json.dumps(task_dict))
                    hard_task = root / "hard_task.json"
                    os.link(ext_file, hard_task)
                    with self.assertRaises(pipe.ProposalError) as ctx:
                        pipe.run_pipeline(root, hard_task, "pfx-hard", 300, None, {})
                    self.assertIn("hardlinked and cannot be trusted", str(ctx.exception))
                mock_run.assert_not_called()

    def test_pipeline_lock_modes_and_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            with pipe.pipeline_lock(root) as pdir:
                lock = pdir / ".pipeline.lock"
                self.assertTrue(lock.exists())
                self.assertEqual(stat.S_IMODE(lock.stat().st_mode), 0o600)
                with self.assertRaises(pipe.ProposalError) as ctx:
                    with pipe.pipeline_lock(root): pass
                self.assertIn("already active", str(ctx.exception))
            with pipe.pipeline_lock(root) as pdir2:
                self.assertTrue((pdir2 / ".pipeline.lock").exists())
            root_sym = Path(td) / "sym"
            pdir_sym = root_sym / ".local/two-site-refactor/pipeline"
            pdir_sym.parent.mkdir(parents=True)
            sym_target = Path(td) / "target_dir"
            sym_target.mkdir()
            pdir_sym.symlink_to(sym_target)
            with self.assertRaises(pipe.ProposalError) as ctx:
                with pipe.pipeline_lock(root_sym): pass
            self.assertIn("symlink", str(ctx.exception))
            root_lsym = Path(td) / "lsym"
            ldir = root_lsym / ".local/two-site-refactor/pipeline"
            ldir.mkdir(parents=True)
            (root_lsym / "dummy").write_text("x")
            (ldir / ".pipeline.lock").symlink_to(root_lsym / "dummy")
            with self.assertRaises(pipe.ProposalError):
                with pipe.pipeline_lock(root_lsym): pass
            root_hard = Path(td) / "hard"
            hdir = root_hard / ".local/two-site-refactor/pipeline"
            hdir.mkdir(parents=True)
            ext = Path(td) / "ext_lock"
            ext.write_text("x")
            os.link(ext, hdir / ".pipeline.lock")
            with self.assertRaises(pipe.ProposalError) as ctx:
                with pipe.pipeline_lock(root_hard): pass
            self.assertIn("trusted regular file", str(ctx.exception))

    def test_main_stdout_contract_and_exit_codes(self):
        argv = ["prog", "--task", "task.json", "--run-prefix", "pfx"]
        src_marker, prop_marker = "MARKER_SRC_XYZ", "MARKER_PROP_ABC"
        scenario = {"source": src_marker, "proposal": prop_marker}
        def run_main(mock_side):
            buf = io.StringIO()
            with patch("sys.argv", argv), patch.object(pipe, "run_pipeline", side_effect=mock_side), redirect_stdout(buf):
                code = pipe.main()
            return code, buf.getvalue()
        def ok_fn(_r, _t, _p, _to, _c, state):
            _ = scenario
            state.update(state="READY_FOR_CODEX", task_id="T1")
        code, out = run_main(ok_fn)
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(set(data.keys()), EXPECTED_KEYS)
        self.assertEqual(len(data), 11)
        self.assertEqual(data["blockers"], [])
        self.assertNotIn(src_marker, out)
        self.assertNotIn(prop_marker, out)
        self.assertNotIn("Traceback", out)
        marker = "DELIBERATE_ERR_MARKER_abc"
        code, out = run_main(pipe.ProposalError(marker))
        self.assertEqual(code, 1)
        data = json.loads(out.strip())
        self.assertEqual(set(data.keys()), EXPECTED_KEYS)
        self.assertEqual(len(data), 11)
        self.assertEqual(data["state"], "TASK_BLOCKED")
        self.assertEqual(data["blockers"], [marker])
        self.assertIn(marker, out)
        self.assertNotIn(src_marker, out)
        self.assertNotIn(prop_marker, out)
        self.assertNotIn("Traceback", out)
        code, out = run_main(RuntimeError("E" * 300))
        self.assertEqual(code, 1)
        data = json.loads(out.strip())
        self.assertEqual(data["state"], "TASK_BLOCKED")
        self.assertLessEqual(len(data["blockers"][0]), 200)
        for exc in (KeyboardInterrupt, SystemExit):
            with patch("sys.argv", argv), patch.object(pipe, "run_pipeline", side_effect=exc):
                with self.assertRaises(exc): pipe.main()

    def test_evidence_dir_collision_and_tracked_source_integrity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            root.mkdir()
            src = root / "tracked.py"
            src_bytes = b"# immutable content\n"
            src.write_bytes(src_bytes)
            task_file = root / "task.json"
            task_file.write_text(json.dumps(_dummy_task_dict()))
            pdir = root / ".local/two-site-refactor/pipeline"
            ev_dir = pdir / "coll-pfx"
            ev_dir.mkdir(parents=True)
            (ev_dir / "sentinel.txt").write_text("keep_me")
            with self.assertRaises(pipe.ProposalError) as ctx:
                pipe.run_pipeline(root, task_file, "coll-pfx", 300, None, {})
            self.assertIn("already exists", str(ctx.exception))
            self.assertEqual((ev_dir / "sentinel.txt").read_text(), "keep_me")
            def fake_git(_r, *a):
                if a[0] == "branch": return pipe.BRANCH
                if a[0] == "rev-parse": return "a" * 40
                if a[0] == "status": return ""
                return "1" * 40
            with patch.object(pipe, "git", side_effect=fake_git), patch.object(pipe, "runner_run", side_effect=pipe.ProposalError("stop")):
                try: pipe.run_pipeline(root, task_file, "new-pfx", 300, None, {})
                except pipe.ProposalError: pass
            self.assertTrue((pdir / "new-pfx").exists())
            self.assertEqual(src.read_bytes(), src_bytes)

if __name__ == "__main__":
    unittest.main()
