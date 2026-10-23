import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_HOST_PATHS = {
    "CERTS_HOST_PATH": "/tmp/trading-bot-ci-certs",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR": "/tmp/trading-bot-ci-coin-inference",
    "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR": "/tmp/trading-bot-ci-product-estimator",
}


class BrowserCIBootstrapContractTests(unittest.TestCase):
    def workflows(self):
        for name in ("merge-gate.yml", "pre-release-gate.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
            yield name, workflow["jobs"]["frontend-browser-matrix"]

    def test_all_bootstrapped_host_binds_use_prepared_ci_paths(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        bootstrap = (ROOT / "scripts/bootstrap_ci_backend.sh").read_text()
        started = set()
        for command in re.findall(r"(?m)^compose up (.+)$", bootstrap):
            started.update(token for token in shlex.split(command) if not token.startswith("-"))
        self.assertEqual(started, {"app", "migration", "db", "redis"})

        required = set()
        for service in started:
            for mount in compose["services"][service].get("volumes", []):
                if isinstance(mount, dict):
                    if mount.get("type") != "bind":
                        continue
                    source = mount["source"]
                else:
                    if not mount.startswith(("${", "/", "./", "../")):
                        continue  # Named Docker volumes are not host bind paths.
                    source = mount.rsplit(":", 1)[0]
                variable = re.fullmatch(r"\$\{([A-Z_]+):-([^}]+)\}", source)
                self.assertIsNotNone(variable, f"Unmanaged CI bind source: {source}")
                required.add(variable.group(1))
        self.assertEqual(required, set(CI_HOST_PATHS))

        for name, job in self.workflows():
            with self.subTest(workflow=name):
                self.assertEqual({key: job["env"].get(key) for key in required}, CI_HOST_PATHS)
                steps = job["steps"]
                prepare = next(step for step in steps if step["name"] == "Prepare CI host paths")
                bootstrap_step = next(step for step in steps if step["name"] == "Bootstrap backend stack")
                self.assertLess(steps.index(prepare), steps.index(bootstrap_step))
                self.assertEqual(
                    shlex.split(prepare["run"].replace("\\\n", "")),
                    ["mkdir", "-p", "--", *(f"${key}" for key in CI_HOST_PATHS)],
                )

    def test_ci_path_preparation_creates_only_empty_disposable_directories(self):
        for name, job in self.workflows():
            with self.subTest(workflow=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                environment = {"PATH": os.defpath}
                environment.update({key: str(root / key.lower()) for key in CI_HOST_PATHS})
                prepare = next(step for step in job["steps"] if step["name"] == "Prepare CI host paths")
                self.assertEqual(
                    shlex.split(prepare["run"].replace("\\\n", "")),
                    ["mkdir", "-p", "--", *(f"${key}" for key in CI_HOST_PATHS)],
                )
                subprocess.run(
                    ["bash", "--noprofile", "--norc", "-eu", "-c", prepare["run"]],
                    env=environment,
                    check=True,
                    timeout=5,
                )
                self.assertEqual(len(list(root.iterdir())), len(CI_HOST_PATHS))
                for key in CI_HOST_PATHS:
                    path = Path(environment[key])
                    self.assertTrue(path.is_dir())
                    self.assertEqual(list(path.iterdir()), [])

    def test_snapshot_mount_safety_and_browser_gates_remain_enabled(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        mounts = {
            mount["target"]: mount
            for mount in compose["services"]["app"]["volumes"]
            if isinstance(mount, dict)
        }
        self.assertTrue(mounts["/app/runtime/coin-inference"]["read_only"])
        product = mounts["/app/runtime/product-estimator"]
        self.assertTrue(product["read_only"])
        self.assertIs(product["bind"]["create_host_path"], False)
        for name, job in self.workflows():
            with self.subTest(workflow=name):
                self.assertEqual(job["strategy"]["matrix"]["browser"], ["chromium", "firefox", "webkit"])
                self.assertEqual(job["strategy"]["matrix"]["shard"], [1, 2, 3, 4])
                browser = next(step for step in job["steps"] if step["name"] == "Run Playwright browser gate")
                self.assertIn("npm run test:e2e", browser["run"])
                self.assertNotIn("continue-on-error", browser)

    def test_bind_path_regression_is_mandatory_before_browser_jobs(self):
        for name, job in self.workflows():
            with self.subTest(workflow=name):
                workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
                observability = workflow["jobs"]["observability-gate"]
                step = next(step for step in observability["steps"] if step["name"] == "Run focused observability gate")
                self.assertIn("python3 -m unittest -v tests.test_browser_ci_bootstrap_contract", step["run"])
                self.assertNotIn("continue-on-error", step)
                self.assertIn("observability-gate", job["needs"])
