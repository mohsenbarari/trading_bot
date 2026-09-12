import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHILD_RUNNER_SCRIPT = r"""
import asyncio
import json
from pathlib import Path

# Verify child cwd contains mini_app_dist/index.html before importing main
cwd = Path.cwd()
dist_dir = cwd / "mini_app_dist"
index_html = dist_dir / "index.html"
assert dist_dir.is_dir(), f"Expected {dist_dir} to be a directory"
assert index_html.is_file(), f"Expected {index_html} to exist before importing main"

# Requirement 1 & 2: Import main in fresh interpreter where mini_app_dist exists
import main
from fastapi.responses import JSONResponse

# Prove conditional registration contract
assert hasattr(main, "serve_frontend"), "Expected main.serve_frontend to exist"
assert callable(main.serve_frontend), "Expected main.serve_frontend to be callable"

target_path = "/{full_path:path}"
matching_routes = [r for r in main.app.routes if getattr(r, "path", None) == target_path]
assert len(matching_routes) == 1, (
    f"Expected exactly one {target_path} route in app.routes, got {len(matching_routes)}"
)
route = matching_routes[0]
assert route.endpoint is main.serve_frontend, (
    f"Expected route endpoint to be main.serve_frontend, got {route.endpoint}"
)
matching_endpoints = [r for r in main.app.routes if getattr(r, "endpoint", None) is main.serve_frontend]
assert len(matching_endpoints) == 1, (
    f"Expected exactly one route with endpoint main.serve_frontend, got {len(matching_endpoints)}"
)

# Requirement 3 & 4: StaticDirSentinel to prove static_dir is not consulted or joined
class StaticDirSentinel:
    def __init__(self):
        self.operations = []

    def __truediv__(self, other):
        self.operations.append(("__truediv__", str(other)))
        raise AssertionError(f"main.static_dir was joined with {other!r}")

    def __rtruediv__(self, other):
        self.operations.append(("__rtruediv__", str(other)))
        raise AssertionError(f"main.static_dir was reverse-joined with {other!r}")

    def __getattr__(self, name):
        self.operations.append(("__getattr__", name))
        raise AssertionError(f"main.static_dir.{name} was consulted")

    def __fspath__(self):
        self.operations.append(("__fspath__", None))
        raise AssertionError("main.static_dir was converted to fspath")

    def __bool__(self):
        self.operations.append(("__bool__", None))
        raise AssertionError("main.static_dir was tested for truthiness")

    def __repr__(self):
        return "<StaticDirSentinel>"

sentinel = StaticDirSentinel()
original_static_dir = main.static_dir
main.static_dir = sentinel

async def execute_characterization_contract():
    expected_body = b'{"detail":"Not Found"}'

    # Requirement 3: API-prefix cases api/missing and api/
    api_prefix_cases = ["api/missing", "api/"]
    api_results = []
    for path in api_prefix_cases:
        res = await main.serve_frontend(path)
        api_results.append(res)

    assert len(api_results) == 2, f"Expected exactly 2 API-prefix results, got {len(api_results)}"
    for res in api_results:
        assert isinstance(res, JSONResponse), f"Expected JSONResponse, got {type(res)}"
        assert res.status_code == 404, f"Expected status_code 404, got {res.status_code}"
        assert res.body == expected_body, (
            f"Expected raw JSON body {expected_body!r}, got {res.body!r}"
        )

    # Prove static_dir was not consulted or joined
    assert sentinel.operations == [], (
        f"Expected no operations on static_dir during API-prefix calls, got {sentinel.operations}"
    )

    # Requirement 4: Blocked-probe cases openapi.json, docs, redoc, docs/index.html, redoc/index.html
    blocked_probe_cases = [
        "openapi.json",
        "docs",
        "redoc",
        "docs/index.html",
        "redoc/index.html",
    ]
    probe_results = []
    for path in blocked_probe_cases:
        res = await main.serve_frontend(path)
        probe_results.append(res)

    assert len(probe_results) == 5, f"Expected exactly 5 probe results, got {len(probe_results)}"
    for res in probe_results:
        assert isinstance(res, JSONResponse), f"Expected JSONResponse, got {type(res)}"
        assert res.status_code == 404, f"Expected status_code 404, got {res.status_code}"
        assert res.body == expected_body, (
            f"Expected raw JSON body {expected_body!r}, got {res.body!r}"
        )

    # Prove static_dir was not consulted or joined
    assert sentinel.operations == [], (
        f"Expected no operations on static_dir during probe calls, got {sentinel.operations}"
    )

    return {
        "status": "ok",
        "api_cases_count": len(api_results),
        "probe_cases_count": len(probe_results),
        "static_dir_operations": len(sentinel.operations),
    }

try:
    report = asyncio.run(execute_characterization_contract())
finally:
    main.static_dir = original_static_dir

print("__CHARACTERIZATION_REPORT__:" + json.dumps(report))
"""


class TestP100bCharacterizationMainFrontendEarlyReturns(unittest.TestCase):
    def test_main_serve_frontend_early_returns_in_child_process(self):
        repo_root = Path(__file__).resolve().parents[1]

        child_env = dict(os.environ)
        child_env["PYTHONPATH"] = f"{repo_root}:{child_env.get('PYTHONPATH', '')}".rstrip(":")
        if "APP_ENV_FILE" in child_env:
            app_env_path = Path(child_env["APP_ENV_FILE"])
            if not app_env_path.is_absolute():
                child_env["APP_ENV_FILE"] = str((repo_root / app_env_path).resolve())

        with tempfile.TemporaryDirectory(prefix="p1_00b_main_frontend_") as tmp_dir:
            tmp_path = Path(tmp_dir)
            dist_dir = tmp_path / "mini_app_dist"
            dist_dir.mkdir(parents=True, exist_ok=True)
            (dist_dir / "index.html").write_text(
                "<!doctype html><html><head><title>Characterization</title></head><body></body></html>",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, "-c", CHILD_RUNNER_SCRIPT],
                cwd=tmp_path,
                env=child_env,
                capture_output=True,
                text=True,
                check=False,
            )

            # Requirement 5: Fail with useful bounded child stdout/stderr
            if proc.returncode != 0:
                bounded_stdout = proc.stdout[-3000:] if len(proc.stdout) > 3000 else proc.stdout
                bounded_stderr = proc.stderr[-3000:] if len(proc.stderr) > 3000 else proc.stderr
                self.fail(
                    f"Child process failed with return code {proc.returncode}.\n"
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
                f"Child did not emit expected report line. stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
            )
            report = json.loads(report_line)
            self.assertEqual(report.get("status"), "ok")
            self.assertEqual(report.get("api_cases_count"), 2)
            self.assertEqual(report.get("probe_cases_count"), 5)
            self.assertEqual(report.get("static_dir_operations"), 0)


if __name__ == "__main__":
    unittest.main()
