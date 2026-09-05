import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from scripts.two_site_agent_sandbox import (
    GitAncestryEvidence,
    SandboxError,
    _run_bounded_process,
    materialize_git_ancestry,
    run_tests,
    sandbox_argv,
    safe_relative,
    selectors,
)


class SandboxTests(unittest.TestCase):
    @staticmethod
    def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=check,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )

    def commit(self, repo: Path, message: str) -> str:
        self.git(repo, "add", "--all")
        self.git(repo, "-c", "user.name=Sandbox Test", "-c", "user.email=sandbox.invalid", "commit", "-m", message)
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def test_only_explicit_test_selectors(self):
        self.assertEqual(["tests/test_a.py::TestA::test_b"], selectors(["tests/test_a.py::TestA::test_b"]))
        for value in ["--override-ini=env", "tests/../.env", "tests/test_a.py;curl", "/tmp/test_a.py",
                      "tests/test_*.py", "tests//test_a.py", "tests/test_a.py --pdb"]:
            with self.subTest(value=value), self.assertRaises(SandboxError):
                selectors([value])

    def test_no_host_home_environment_or_network_exposed(self):
        argv = sandbox_argv(Path("/tmp/snapshot"), ["tests/test_a.py"])
        self.assertEqual("/usr/bin/prlimit", argv[0])
        self.assertIn("--as=4294967296", argv)
        self.assertIn("--nproc=128", argv)
        self.assertIn("--fsize=134217728", argv)
        self.assertIn("--size", argv)
        self.assertIn("536870912", argv)
        self.assertIn("--uid", argv)
        self.assertIn("65534", argv)
        self.assertIn("--disable-userns", argv)
        self.assertIn("--clearenv", argv)
        self.assertIn("--unshare-all", argv)
        self.assertNotIn("--share-net", argv)
        self.assertNotIn("--bind", argv)
        self.assertNotIn("/root/.codex", argv)
        self.assertNotIn("/root/.gemini", argv)
        self.assertNotIn("/root/.ssh", argv)
        self.assertIn("config/unit-test.env.example", argv)
        self.assertEqual("tests/test_a.py", argv[-1])
        if Path("/usr/bin/awk").is_symlink():
            target = os.readlink("/etc/alternatives/awk")
            offset = argv.index("/etc/alternatives")
            self.assertEqual(
                ["/etc/alternatives", "--symlink", target, "/etc/alternatives/awk"],
                argv[offset:offset + 4],
            )

    def test_traversal_and_absolute_paths_rejected(self):
        for value in ["../x", "/root/.env", "foo/../bar", "foo//bar", "foo\\bar", ""]:
            with self.subTest(value=value), self.assertRaises(SandboxError):
                safe_relative(value)

    def test_output_budget_terminates_the_process(self):
        with self.assertRaisesRegex(SandboxError, "output exceeded"):
            _run_bounded_process(
                [sys.executable, "-c", "import os; os.write(1, b'x' * 65536)"],
                timeout=5,
                output_limit=1024,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
            )

    def test_timeout_terminates_descendants(self):
        with tempfile.TemporaryDirectory(prefix="sandbox-timeout-test-") as temporary:
            pid_path = Path(temporary) / "child.pid"
            program = (
                "import os,signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "child=os.fork(); "
                f"open({str(pid_path)!r},'w').write(str(child)) if child else None; "
                "os._exit(0) if child else None; "
                "time.sleep(60)"
            )
            with self.assertRaisesRegex(SandboxError, "timeout"):
                _run_bounded_process(
                    [sys.executable, "-c", program],
                    timeout=0.5,
                    output_limit=1024,
                    env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
                )
            child_pid = int(pid_path.read_text())
            for _ in range(40):
                status = Path(f"/proc/{child_pid}/stat")
                try:
                    state = status.read_text().split()[2]
                except (FileNotFoundError, ProcessLookupError):
                    break
                if state == "Z":
                    break
                time.sleep(0.05)
            else:
                self.fail("timed-out descendant remained live")

    def test_process_budget_terminates_descendants(self):
        program = (
            "import os,time; "
            "[os.fork() for _ in range(3)]; "
            "time.sleep(30)"
        )
        with self.assertRaisesRegex(SandboxError, "process count exceeded"):
            _run_bounded_process(
                [sys.executable, "-c", program], timeout=5, output_limit=1024,
                process_limit=1,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
            )

    def test_genuine_commit_only_ancestry_and_awk_work_inside_bwrap(self):
        with tempfile.TemporaryDirectory(prefix="sandbox-ancestry-test-") as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            self.git(repo, "init", "-q")
            (repo / "config").mkdir()
            (repo / "config" / "unit-test.env.example").write_text("APP_ENV=test\n")
            (repo / "frontend" / "src" / "views").mkdir(parents=True)
            (repo / "frontend" / "src" / "views" / "DashboardView.vue").write_text(
                "<template><main>dashboard</main></template>\n"
            )
            (repo / "tests").mkdir()
            test_file = repo / "tests" / "test_ancestry.py"
            test_file.write_text("def test_seed():\n    assert True\n")
            ancestor = self.commit(repo, "initial")
            test_file.write_text(
                "import subprocess\n"
                "from pathlib import Path\n\n"
                "def test_real_ancestry():\n"
                f"    result = subprocess.run(['git', 'merge-base', '--is-ancestor', '{ancestor}', 'HEAD'])\n"
                "    assert result.returncode == 0\n\n"
                "def test_awk_alternative():\n"
                "    result = subprocess.run(['awk', 'BEGIN { print 7 }'], check=True, capture_output=True, text=True)\n"
                "    assert result.stdout.strip() == '7'\n\n"
                "def test_vue_source_is_present():\n"
                "    assert Path('frontend/src/views/DashboardView.vue').is_file()\n"
            )
            head = self.commit(repo, "exercise ancestry")
            evidence = GitAncestryEvidence(ancestor, head)

            snapshot = Path(temporary) / "minimal"
            snapshot.mkdir()
            metadata = materialize_git_ancestry(repo, snapshot, evidence)
            self.assertEqual(ancestor, metadata["ancestor_sha"])
            self.assertGreaterEqual(metadata["commit_objects"], 2)
            self.assertEqual(
                0,
                self.git(snapshot, "merge-base", "--is-ancestor", ancestor, head).returncode,
            )
            tree = self.git(repo, "show", "-s", "--format=%T", head).stdout.strip()
            self.assertNotEqual(0, self.git(snapshot, "cat-file", "-e", tree, check=False).returncode)
            self.assertFalse((snapshot / ".git" / "index").exists())
            self.assertFalse((snapshot / ".git" / "logs").exists())

            result = run_tests(
                repo,
                ["tests/test_ancestry.py"],
                timeout=30,
                git_ancestry=evidence,
            )
            self.assertEqual(0, result["returncode"], result["stderr"])
            self.assertIn("3 passed", result["stdout"])
            self.assertEqual(ancestor, result["git_ancestry"]["ancestor_sha"])


if __name__ == "__main__":
    unittest.main()
