import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
CI_HOST_PATHS = {
    "CERTS_HOST_PATH": "/tmp/trading-bot-ci-certs",
    "PRODUCTION_COIN_INFERENCE_SNAPSHOT_HOST_DIR": "/tmp/trading-bot-ci-coin-inference",
    "PRODUCTION_PRODUCT_ESTIMATOR_APP_SNAPSHOT_HOST_DIR": "/tmp/trading-bot-ci-product-estimator",
}
CI_MUTATION_ENV = {
    "E2E_TARGET_ENV": "local",
    "E2E_ALLOW_LOCAL_MUTATION": "local-dev-only",
    "E2E_BACKEND_BASE_URL": "http://127.0.0.1:8000",
    "E2E_APP_CONTAINER_NAME": "trading_bot_app",
    "E2E_REDIS_CONTAINER_NAME": "trading_bot_redis",
}


class BrowserCIBootstrapContractTests(unittest.TestCase):
    def workflows(self):
        for name in ("merge-gate.yml", "pre-release-gate.yml"):
            workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
            yield name, workflow["jobs"]["frontend-browser-matrix"]

    def test_security_os_artifact_token_has_only_browser_job_read_permissions_and_step_scope(self):
        for name, job in self.workflows():
            with self.subTest(workflow=name):
                self.assertEqual(job['permissions'], {'contents': 'read', 'actions': 'read'})
                self.assertNotIn('GH_TOKEN', job.get('env', {}))
                token_steps = [step for step in job['steps'] if 'GH_TOKEN' in step.get('env', {})]
                self.assertEqual(len(token_steps), 1)
                self.assertEqual(token_steps[0]['name'], 'Bootstrap backend stack')
                self.assertEqual(token_steps[0]['env'], {'GH_TOKEN': '${{ github.token }}'})

    def test_security_os_bootstrap_prepares_before_build_and_refuses_without_cache(self):
        source = (ROOT / 'scripts/bootstrap_ci_backend.sh').read_bytes()
        for failure in ('0', '17', '19', '23'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(prefix='owned-ci-bootstrap-') as directory:
                project = Path(directory)
                scripts = project / 'scripts'; scripts.mkdir()
                target = scripts / 'bootstrap_ci_backend.sh'; target.write_bytes(source)
                binary = project / 'bin'; binary.mkdir()
                python = binary / 'python3'
                python.write_text('''#!/bin/sh
set -eu
[ "$1" = -I ] && [ "$2" = -B ]
[ "$4" = --cache-dir ]
case "${3##*/}" in
  prepare_app_security_os_cache.py) kind=os; directory=security-os-capsule; failure=17 ;;
  prepare_sqlite_wal_os_cache.py) kind=sqlite; directory=sqlite-wal-capsule; failure=19 ;;
  prepare_tar_acl_os_cache.py) kind=tar; directory=tar-acl-capsule; failure=23 ;;
  *) exit 97 ;;
esac
[ "$5" = "$CASE_ROOT/pip_packages/$directory" ]
[ -d "${5%/*}" ]
printf 'cache-%s\\n' "$kind" >> "$TRACE_FILE"
if [ "$CACHE_FAILURE" = "$failure" ]; then exit "$failure"; fi
exit 0
''')
                docker = binary / 'docker'
                docker.write_text('''#!/bin/sh
set -eu
case "$1 $2" in
  'build -t') printf 'build\\n' >> "$TRACE_FILE" ;;
  'compose version') printf 'version\\n' >> "$TRACE_FILE" ;;
  'compose up') printf 'up\\n' >> "$TRACE_FILE" ;;
  'compose exec') printf 'ready\\n' >> "$TRACE_FILE" ;;
  'compose ps'|'compose logs') : ;;
  *) exit 97 ;;
esac
''')
                python.chmod(0o755); docker.chmod(0o755)
                environment = {
                    'PATH': str(binary) + os.pathsep + os.defpath,
                    'APP_ENV_FILE': str(ROOT / 'config/unit-test.env.example'),
                    'CASE_ROOT': str(project), 'TRACE_FILE': str(project / 'trace'),
                    'CACHE_FAILURE': failure,
                }
                with patch('socket.socket', side_effect=AssertionError('network denied')), \
                     patch('socket.getaddrinfo', side_effect=AssertionError('network denied')), \
                     patch('urllib.request.urlopen', side_effect=AssertionError('network denied')), \
                     patch('os.system', side_effect=AssertionError('ambient shell denied')):
                    result = subprocess.run(
                        ['bash', '--noprofile', '--norc', str(target)],
                        cwd=project, env=environment, capture_output=True, text=True, timeout=10,
                    )
                events = (project / 'trace').read_text().splitlines()
                caches=['cache-os','cache-sqlite','cache-tar']
                length={'0':3,'17':1,'19':2,'23':3}[failure]
                self.assertEqual(events[:1+length], ['version', *caches[:length]])
                if failure == '0':
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertLess(events.index('cache-tar'), events.index('build'))
                    self.assertLess(events.index('build'), events.index('up'))
                    self.assertIn('ready', events)
                else:
                    self.assertEqual(result.returncode, int(failure), result.stderr + result.stdout)
                    self.assertNotIn('build', events)
                    self.assertNotIn('up', events)

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

    def test_mutations_are_explicit_only_on_hosted_disposable_loopback_stack(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
        helper = (ROOT / "frontend/e2e/helpers/mutationRuntime.ts").read_text()
        self.assertIn("const LOCAL_MUTATION_CONFIRM = 'local-dev-only'", helper)
        self.assertIn("process.env.E2E_ALLOW_LOCAL_MUTATION !== LOCAL_MUTATION_CONFIRM", helper)
        self.assertIn("targetEnvironment === 'local'", helper)
        self.assertIn("mutating e2e tests require E2E_TARGET_ENV=staging", helper)
        self.assertIn("E2E_APP_CONTAINER_NAME is required for staging mutation tests", helper)
        self.assertIn("staging mutation e2e must not target the production/default backend URL", helper)
        self.assertEqual(compose["services"]["app"]["container_name"], CI_MUTATION_ENV["E2E_APP_CONTAINER_NAME"])
        self.assertEqual(compose["services"]["redis"]["container_name"], CI_MUTATION_ENV["E2E_REDIS_CONTAINER_NAME"])
        self.assertIn("127.0.0.1:8000:8000", compose["services"]["app"]["ports"])
        for name, job in self.workflows():
            with self.subTest(workflow=name):
                self.assertEqual(job["runs-on"], "ubuntu-latest")
                steps = job["steps"]
                browser = next(step for step in steps if step["name"] == "Run Playwright browser gate")
                self.assertEqual({key: browser["env"].get(key) for key in CI_MUTATION_ENV}, CI_MUTATION_ENV)
                self.assertEqual(browser["env"]["CI"], "true")
                self.assertEqual(browser["working-directory"], "frontend")
                # .env configures containers; it must not masquerade as host authorization.
                self.assertNotIn("source ", browser["run"])
                self.assertNotIn("E2E_ALLOW_LOCAL_MUTATION", job.get("env", {}))
                write = next(step for step in steps if step["name"] == "Write CI environment file")
                self.assertIn("ENVIRONMENT=staging", write["run"])
                verify = next(step for step in steps if step["name"] == "Verify browser CI backend surface")
                self.assertIn('environment != "staging"', verify["run"])
                self.assertLess(steps.index(verify), steps.index(browser))

    def test_notification_login_helper_checks_both_exact_unread_states(self):
        source = (ROOT / "frontend/src/views/DashboardView.vue").read_text()
        tests = (ROOT / "frontend/e2e/notifications.spec.ts").read_text()
        helper = tests.split("async function loginWithSeededSession", 1)[1].split("function chatMessageContent", 1)[0]
        self.assertIn(":label=\"hasUnreadNotifications ? 'اعلان‌های خوانده‌نشده' : 'اعلان‌ها'\"", source)
        self.assertIn("page.locator('button.notif-btn:visible')", helper)
        self.assertIn("expect(notificationButton).toHaveCount(1)", helper)
        self.assertIn("expect(notificationButton).toBeVisible()", helper)
        self.assertIn("/^(?:اعلان‌ها|اعلان‌های خوانده‌نشده)$/u", helper)
        self.assertNotIn(".first()", helper)
        self.assertNotIn(".click()", helper)

    def test_registration_assertions_follow_existing_ui_without_losing_negatives(self):
        tests = (ROOT / "frontend/e2e/registration-stage7.spec.ts").read_text()
        login = (ROOT / "frontend/src/views/LoginView.vue").read_text()
        invitation = (ROOT / "frontend/src/views/InviteLanding.vue").read_text()
        self.assertIn("'دریافت کد تأیید'", login)
        self.assertEqual(tests.count("name: 'دریافت کد تأیید', exact: true"), 2)
        self.assertNotIn("دریافت کد تایید", tests)
        self.assertIn("'ادامه ثبت‌نام در وب‌اپ'", invitation)
        self.assertIn("name: 'ادامه ثبت‌نام در وب‌اپ', exact: true", tests)
        self.assertIn("<span v-if=\"expiresAt\">تا {{ formatIranDateTime(expiresAt) }}</span>", invitation)
        self.assertIn("toHaveText(`تا ${formatIranDateTime(invitationExpiresAt)}`)", tests)
        self.assertEqual(tests.count("new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString()"), 2)
        for assertion in (
            "getByText('ثبت‌نام با تلگرام')).toHaveCount(0)",
            "name: 'ارسال مجدد کد' })).toHaveCount(0)",
            "getByText(/ارسال خودکار پیامک/)).toHaveCount(0)",
            "getByText('ثبت‌نام قبلاً تکمیل شده است')).toBeVisible()",
            "getByText(/کد ابتدا در تلگرام ارسال شد؛ 00:(39|40) تا ارسال خودکار پیامک/)",
            "getByText(/00:(29|30) تا ارسال مجدد/)",
        ):
            self.assertIn(assertion, tests)
        self.assertNotIn("test.skip", tests)
        self.assertNotIn("test.fixme", tests)
