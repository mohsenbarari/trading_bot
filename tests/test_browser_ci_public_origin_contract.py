"""Exercise actual public-link validation on disposable CI values only."""
import ast
import builtins
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

if __package__:
    from . import test_npm_browser_ci_fixture_contract as N
else:
    import test_npm_browser_ci_fixture_contract as N


ROOT = Path(__file__).absolute().parents[1]
WORKFLOWS = ("merge-gate.yml", "pre-release-gate.yml")
EXPECTED = "https://ci-iran.invalid"


def job(name):
    data = N.C.public_bytes(ROOT / ".github/workflows" / name, 65536)
    return yaml.load(data, Loader=yaml.BaseLoader)["jobs"]["frontend-browser-matrix"]


def step(name, title):
    return next(s for s in job(name)["steps"] if s.get("name") == title)


def fixture(name, *, frontend=False):
    target = r"frontend/\.env\.local" if frontend else r"\.env"
    code = step(name, "Write CI environment file")["run"]
    block = re.search(r"cat > " + target + r" <<'EOF'\n(.*?)\nEOF", code, re.S)
    if block is None:
        raise AssertionError("explicit disposable fixture required")
    rows = {}
    for line in block[1].splitlines():
        key, value = line.split("=", 1)
        if key in rows:
            raise AssertionError("duplicate disposable setting")
        rows[key] = value
    return rows


def preflight(name):
    command = step(name, "Verify browser CI backend surface")["run"]
    block = re.search(r"docker compose exec -T app python - <<'PY'\n(.*?)\nPY(?:\n|$)", command, re.S)
    if block is None:
        raise AssertionError("actual App validator preflight required")
    return ast.parse(block[1])


def run_preflight(name, values, *, function=None):
    # Reuse the existing genuine validator AST seam. It imports no Settings,
    # dotenv or application; all settings below are owned synthetic values.
    settings = SimpleNamespace(**{k.lower(): v for k, v in values.items()})
    namespace = N.validator(settings)
    if function is not None:
        namespace["public_webapp_url_for_links"] = function
    tree = deepcopy(preflight(name))
    tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
    actual_import = builtins.__import__

    def denied(name, *args, **kwargs):
        if name.startswith(("core.config", "dotenv", "pydantic_settings")):
            raise AssertionError("configured settings import forbidden")
        return actual_import(name, *args, **kwargs)

    out, err = io.StringIO(), io.StringIO()
    try:
        with patch.object(builtins, "__import__", side_effect=denied), redirect_stdout(out), redirect_stderr(err):
            exec(compile(tree, "<owned ordinary CI preflight>", "exec"), namespace)
    finally:
        if out.getvalue() or err.getvalue():
            raise AssertionError("preflight must not emit settings")
    return namespace


class BrowserPublicOriginContractTests(unittest.TestCase):
    def test_actual_both_workflow_fixtures_pass_genuine_public_origin_validator(self):
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                values = fixture(name)
                self.assertEqual(values["ENVIRONMENT"], "staging")
                self.assertEqual(values["SERVER_MODE"], "iran")
                self.assertEqual(values["PUBLIC_WEBAPP_URL"], EXPECTED)
                self.assertEqual(values["IRAN_SERVER_DOMAIN"], "ci-iran.invalid")
                observed = run_preflight(name, values)
                self.assertEqual(observed["public_origin"], EXPECTED)
                self.assertEqual(observed["EXPECTED_CI_PUBLIC_WEBAPP_ORIGIN"], EXPECTED)

    def test_missing_HTTP_foreign_unconfigured_and_credential_origins_fail_early(self):
        changes = ({"PUBLIC_WEBAPP_URL": ""}, {"PUBLIC_WEBAPP_URL": "http://ci-iran.invalid"},
                   {"IRAN_SERVER_DOMAIN": ""}, {"FOREIGN_SERVER_DOMAIN": "ci-iran.invalid"},
                   {"PUBLIC_WEBAPP_URL": "https://wrong.invalid"},
                   {"PUBLIC_WEBAPP_URL": "https://user:OWNED_CANARY@ci-iran.invalid"})
        for name in WORKFLOWS:
            for change in changes:
                with self.subTest(workflow=name, change=tuple(change)):
                    with self.assertRaises(SystemExit) as error:
                        run_preflight(name, {**fixture(name), **change})
                    self.assertEqual(error.exception.code, "invalid_ci_public_webapp_origin")
                    self.assertNotIn("OWNED_CANARY", str(error.exception))

    def test_other_valid_public_origin_and_wrong_environment_use_fixed_codes(self):
        changes = (({"PUBLIC_WEBAPP_URL": "https://other.invalid", "IRAN_SERVER_DOMAIN": "other.invalid"},
                    "unexpected_ci_public_webapp_origin"),
                   ({"ENVIRONMENT": "OWNED_CANARY"}, "unexpected_ci_environment"),
                   ({"SERVER_MODE": "OWNED_CANARY"}, "unexpected_ci_server_mode"))
        for name in WORKFLOWS:
            for change, code in changes:
                with self.subTest(workflow=name, code=code), self.assertRaises(SystemExit) as error:
                    run_preflight(name, {**fixture(name), **change})
                self.assertEqual(error.exception.code, code)
                self.assertNotIn("OWNED_CANARY", str(error.exception))

    def test_actual_validator_is_called_and_exception_value_is_not_published(self):
        for name in WORKFLOWS:
            calls = []

            def failure(**kwargs):
                calls.append(kwargs["settings_obj"])
                raise ValueError("OWNED_CANARY_PRIVATE_SETTING")

            with self.subTest(workflow=name), self.assertRaises(SystemExit) as error:
                run_preflight(name, fixture(name), function=failure)
            self.assertEqual(len(calls), 1)
            self.assertEqual(error.exception.code, "invalid_ci_public_webapp_origin")
            self.assertIsNone(error.exception.__cause__)
            self.assertTrue(error.exception.__suppress_context__)

    def test_original_browser_transport_and_Origin_probe_remain_consistent(self):
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertEqual(fixture(name)["FRONTEND_URL"], "http://127.0.0.1:4173")
                self.assertEqual(fixture(name, frontend=True)["VITE_API_BASE_URL"], "http://127.0.0.1:8000")
                command = step(name, "Verify browser CI backend surface")["run"]
                self.assertIn("Origin: http://127.0.0.1:4173", command)
                self.assertIn("access-control-allow-origin: http://127.0.0.1:4173", command)
                self.assertNotIn("NPM_SECURITY_ISOLATED_BROWSER_FIXTURE", job(name).get("env", {}))
                self.assertNotIn("ignoreHTTPSErrors", command)

    def test_both_preflights_finish_before_complete_unchanged_twelve_cell_gate(self):
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                value = job(name)
                names = [s.get("name") for s in value["steps"]]
                self.assertLess(names.index("Bootstrap backend stack"), names.index("Verify browser CI backend surface"))
                self.assertLess(names.index("Verify browser CI backend surface"), names.index("Run Playwright browser gate"))
                self.assertEqual(value["strategy"]["matrix"]["browser"], ["chromium", "firefox", "webkit"])
                self.assertEqual(value["strategy"]["matrix"]["shard"], ["1", "2", "3", "4"])
                gate = step(name, "Run Playwright browser gate")
                self.assertNotIn("continue-on-error", gate)
                self.assertIn("npm run test:e2e", gate["run"])
                self.assertEqual(gate["env"]["E2E_TARGET_ENV"], "local")
                self.assertEqual(gate["env"]["E2E_ALLOW_LOCAL_MUTATION"], "local-dev-only")

    def test_actual_compose_consumes_disposable_file_without_public_origin_override(self):
        raw = N.C.public_bytes(ROOT / "docker-compose.yml", 32768)
        app = yaml.safe_load(raw)["services"]["app"]
        self.assertEqual(app["env_file"], [".env"])
        self.assertNotIn("PUBLIC_WEBAPP_URL", app["environment"])
        self.assertNotIn("IRAN_SERVER_DOMAIN", app["environment"])

    def test_reserved_public_link_origin_matches_existing_isolated_NPM_fixture(self):
        existing = N.fixture_env()
        for name in WORKFLOWS:
            for key in ("PUBLIC_WEBAPP_URL", "IRAN_SERVER_DOMAIN"):
                with self.subTest(workflow=name, key=key):
                    self.assertEqual(fixture(name)[key], existing[key])


if __name__ == "__main__":
    unittest.main()
