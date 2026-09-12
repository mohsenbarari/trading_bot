import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHILD_PRESENT_RUNNER_SCRIPT = r"""
import asyncio
import json
from pathlib import Path

# Requirement 3: In the present-directory child, create mini_app_dist/index.html before importing main
cwd = Path.cwd()
dist_dir = cwd / "mini_app_dist"
dist_dir.mkdir(parents=True, exist_ok=True)
index_html = dist_dir / "index.html"
index_html.write_text(
    "<!doctype html><html><head><title>Root Present</title></head><body><h1>Present</h1></body></html>",
    encoding="utf-8",
)

assert dist_dir.is_dir(), f"Expected {dist_dir} to be a directory"
assert index_html.is_file(), f"Expected {index_html} to exist before importing main"

# Import main only inside the child interpreter
import main
from fastapi.responses import FileResponse


async def run_present_characterization():
    result = await main.root()

    assert isinstance(result, FileResponse), f"Expected fastapi.responses.FileResponse, got {type(result)}"
    assert Path(result.path).resolve() == index_html.resolve(), (
        f"Expected FileResponse path {index_html.resolve()}, got {Path(result.path).resolve()}"
    )
    assert result.status_code == 200, f"Expected status_code 200, got {result.status_code}"
    assert result.headers.get("referrer-policy") == "no-referrer", (
        f"Expected Referrer-Policy header 'no-referrer', got {result.headers.get('referrer-policy')}"
    )
    assert result.headers.get("Referrer-Policy") == "no-referrer", (
        f"Expected Referrer-Policy header 'no-referrer', got {result.headers.get('Referrer-Policy')}"
    )

    return {
        "branch": "present",
        "status": "ok",
        "response_type": type(result).__name__,
        "status_code": result.status_code,
        "referrer_policy": result.headers.get("Referrer-Policy"),
        "target_path": str(index_html.resolve()),
        "result_path": str(Path(result.path).resolve()),
        "path_matches": Path(result.path).resolve() == index_html.resolve(),
    }


report = asyncio.run(run_present_characterization())
print("__CHARACTERIZATION_REPORT__:" + json.dumps(report))
"""

CHILD_ABSENT_RUNNER_SCRIPT = r"""
import asyncio
import json
from pathlib import Path

# Requirement 4: In the absent-directory child, prove mini_app_dist does not exist before importing main
cwd = Path.cwd()
dist_dir = cwd / "mini_app_dist"
assert not dist_dir.exists(), f"Expected {dist_dir} to not exist before importing main"

# Import main only inside the child interpreter
import main


async def run_absent_characterization():
    result = await main.root()

    assert type(result) is dict, f"Expected built-in dict, got {type(result)}"
    expected_message = "Trading Bot API is running 🚀"
    expected_dict = {"message": expected_message}
    assert result == expected_dict, f"Expected {expected_dict!r}, got {result!r}"
    assert set(result.keys()) == {"message"}, f"Expected exactly keys {{'message'}}, got {result.keys()}"
    assert result["message"] == expected_message, f"Expected message {expected_message!r}, got {result.get('message')!r}"
    assert len(result) == 1, f"Expected dict length 1, got {len(result)}"

    return {
        "branch": "absent",
        "status": "ok",
        "response_type": type(result).__name__,
        "is_builtin_dict": type(result) is dict,
        "result": result,
    }


report = asyncio.run(run_absent_characterization())
print("__CHARACTERIZATION_REPORT__:" + json.dumps(report))
"""


class TestP100bCharacterizationMainRootBranches(unittest.TestCase):
    def _run_child(self, branch_name: str, script: str, cwd_path: Path) -> dict:
        repo_root = Path(__file__).resolve().parents[1]

        child_env = dict(os.environ)
        child_env["PYTHONPATH"] = f"{repo_root}:{child_env.get('PYTHONPATH', '')}".rstrip(":")
        if "APP_ENV_FILE" in child_env:
            app_env_path = Path(child_env["APP_ENV_FILE"])
            if not app_env_path.is_absolute():
                child_env["APP_ENV_FILE"] = str((repo_root / app_env_path).resolve())
        else:
            default_env = repo_root / "config" / "unit-test.env.example"
            if default_env.exists():
                child_env["APP_ENV_FILE"] = str(default_env.resolve())

        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=cwd_path,
            env=child_env,
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0:
            bounded_stdout = proc.stdout[-3000:] if len(proc.stdout) > 3000 else proc.stdout
            bounded_stderr = proc.stderr[-3000:] if len(proc.stderr) > 3000 else proc.stderr
            self.fail(
                f"Child process for branch '{branch_name}' failed with return code {proc.returncode}.\n"
                f"--- Child stdout (bounded) ---\n{bounded_stdout}\n"
                f"--- Child stderr (bounded) ---\n{bounded_stderr}"
            )

        report_line = None
        for line in proc.stdout.splitlines():
            if line.startswith("__CHARACTERIZATION_REPORT__:"):
                report_line = line[len("__CHARACTERIZATION_REPORT__:") :]
                break

        self.assertIsNotNone(
            report_line,
            f"Child for branch '{branch_name}' did not emit expected report line. stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )
        return json.loads(report_line)

    def test_main_root_branches_characterization(self):
        with tempfile.TemporaryDirectory(prefix="p1_00b_main_root_present_") as tmp_present_dir:
            tmp_present_path = Path(tmp_present_dir)
            present_report = self._run_child("present", CHILD_PRESENT_RUNNER_SCRIPT, tmp_present_path)

            self.assertEqual(present_report.get("status"), "ok")
            self.assertEqual(present_report.get("branch"), "present")
            self.assertEqual(present_report.get("response_type"), "FileResponse")
            self.assertEqual(present_report.get("status_code"), 200)
            self.assertEqual(present_report.get("referrer_policy"), "no-referrer")
            self.assertTrue(present_report.get("path_matches"))

        with tempfile.TemporaryDirectory(prefix="p1_00b_main_root_absent_") as tmp_absent_dir:
            tmp_absent_path = Path(tmp_absent_dir)
            absent_report = self._run_child("absent", CHILD_ABSENT_RUNNER_SCRIPT, tmp_absent_path)

            self.assertEqual(absent_report.get("status"), "ok")
            self.assertEqual(absent_report.get("branch"), "absent")
            self.assertEqual(absent_report.get("response_type"), "dict")
            self.assertTrue(absent_report.get("is_builtin_dict"))
            self.assertEqual(
                absent_report.get("result"),
                {"message": "Trading Bot API is running 🚀"},
            )


if __name__ == "__main__":
    unittest.main()
