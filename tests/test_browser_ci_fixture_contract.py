"""Public source/configuration and owned files; no backend, browser or network."""
import ast
from contextlib import chdir
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
LOGIN = 'login stays keyboard-usable and hides developer shortcut'


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text())


def inline_python(step):
    return step['run'].split("<<'PYCODE'\n", 1)[1].rsplit('\nPYCODE', 1)[0]


class BrowserFixtureTests(unittest.TestCase):
    def normals(self):
        for name in ('merge-gate.yml', 'pre-release-gate.yml'):
            yield workflow(name)['jobs']['frontend-browser-matrix']

    def test_relative_browser_api_and_preserved_preview_host_match_actual_origin_guard(self):
        source = ast.parse((ROOT / 'api/routers/auth.py').read_bytes())
        node = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                    and n.name == '_assert_registration_context_same_origin')
        class Rejected(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
        values = {'Request': object, 'HTTPException': Rejected, 'urlsplit': urlsplit}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'public_same_origin_guard', 'exec'), values)
        guard = values[node.name]
        def request(host, **headers):
            return SimpleNamespace(headers={'host': host, 'origin': 'http://127.0.0.1:4173', **headers},
                                   url=SimpleNamespace(scheme='http'))
        guard(request('127.0.0.1:4173'))
        for value in (request('127.0.0.1:8000'),
                      request('127.0.0.1:8000', **{'x-forwarded-host': '127.0.0.1:4173'}),
                      request('127.0.0.1:4173', **{'sec-fetch-site': 'cross-site'})):
            with self.assertRaises(Rejected) as stopped:
                guard(value)
            self.assertEqual(stopped.exception.status_code, 403)
        vite = (ROOT / 'frontend/vite.config.ts').read_text()
        self.assertIn('changeOrigin: true', vite.split('preview:', 1)[0])
        preview = vite.split('preview:', 1)[1]
        self.assertIn("'/api'", preview)
        self.assertIn('target: devProxyTarget', preview)
        self.assertIn('changeOrigin: false', preview)
        for job in self.normals():
            write = next(s for s in job['steps'] if s['name'] == 'Write CI environment file')['run']
            self.assertIn('VITE_API_BASE_URL=\n', write)
            self.assertNotIn('VITE_API_BASE_URL=http', write)
            self.assertIn('ENVIRONMENT=staging', write)
            self.assertIn('VITE_STAGING_DEV_LOGIN=true', write)
            self.assertIn('PUBLIC_WEBAPP_URL=https://browser-ci.iran.example', write)

    def test_developer_shortcut_negative_is_moved_to_real_production_build_for_every_browser(self):
        base = (ROOT / 'frontend/playwright.config.ts').read_text()
        production = (ROOT / 'frontend/playwright.production-login.config.ts').read_text()
        case = (ROOT / 'frontend/e2e/native-app-v2-acceptance.spec.ts').read_text()
        self.assertIn('grepInvert: /' + LOGIN + '/', base)
        self.assertIn('grep: /' + LOGIN + '/', production)
        self.assertIn('grepInvert: undefined', production)
        self.assertIn("testMatch: '**/native-app-v2-acceptance.spec.ts'", production)
        self.assertIn("baseURL: 'http://127.0.0.1:4174'", production)
        self.assertIn('--outDir dist/production-login', production)
        self.assertIn("getByRole('button', { name: 'ورود سریع ۱ ساله' })).toHaveCount(0)", case)
        self.assertIn('await expect(submit).toBeFocused()', case)
        for job in self.normals():
            self.assertEqual(job['strategy']['matrix']['browser'], ['chromium', 'firefox', 'webkit'])
            self.assertEqual(job['strategy']['matrix']['shard'], [1, 2, 3, 4])
            self.assertEqual(job['timeout-minutes'], 75)
            steps = job['steps']
            build = next(s for s in steps if s['name'] == 'Build production login browser fixture')
            gate = next(s for s in steps if s['name'] == 'Run production login browser gate')
            primary = next(s for s in steps if s['name'] == 'Run Playwright browser gate')
            self.assertEqual(build['if'], 'matrix.shard == 1')
            self.assertEqual(gate['if'], build['if'])
            self.assertEqual(build['env'], {'FRONTEND_BUILD_OUT_DIR': 'dist/production-login',
                                            'VITE_STAGING_DEV_LOGIN': 'false'})
            self.assertIn('--project=${{ matrix.browser }}', gate['run'])
            self.assertIn('--config=playwright.https-production-login.config.ts', gate['run'])
            self.assertNotIn('continue-on-error', gate)
            self.assertLess(steps.index(primary), steps.index(gate))
            upload = next(s for s in steps if s['name'] == 'Upload Playwright artifacts')
            for path in ('frontend/playwright-report-production-login', 'frontend/test-results-production-login'):
                self.assertIn(path, upload['with']['path'].splitlines())

    def test_manual_diagnostic_has_one_job_no_push_pr_no_release_acceptance(self):
        value = workflow('browser-ci-diagnostic.yml')
        events = value.get('on', value.get(True))
        self.assertEqual(set(events), {'workflow_dispatch', 'workflow_call'})
        self.assertTrue(events['workflow_call']['inputs']['git_ref']['required'])
        self.assertEqual(events['workflow_call']['inputs']['browser']['type'], 'string')
        inputs = events['workflow_dispatch']['inputs']
        self.assertEqual(inputs['scenario']['options'], ['mandatory', 'offers', 'both', 'chat-menu', 'accounts-and-menu'])
        self.assertTrue(inputs['git_ref']['required'])
        self.assertEqual(set(value['jobs']), {'browser-diagnostic'})
        job = value['jobs']['browser-diagnostic']
        self.assertNotIn('needs', job)
        self.assertNotIn('strategy', job)
        self.assertEqual(job['permissions'], {'contents': 'read', 'actions': 'read'})
        self.assertIn("github.repository == 'mohsenbarari/trading_bot'", job['if'])
        steps = job['steps']
        selected = next(s for s in steps if s.get('id') == 'browser')
        self.assertNotIn('continue-on-error', selected)
        self.assertEqual(selected['run'].count('--retries=0 --workers=1'), 5)
        self.assertIn('mandatory-channel.spec.ts', selected['run'])
        self.assertIn('market-offers.spec.ts', selected['run'])
        self.assertNotIn('--shard', selected['run'])
        self.assertNotIn('GH_TOKEN', job['env'])
        token_steps = [s for s in steps if 'GH_TOKEN' in s.get('env', {})]
        self.assertEqual([s['id'] for s in token_steps], ['bootstrap'])
        self.assertEqual(next(s for s in steps if s['name'] == 'Upload diagnostic evidence')['if'], 'always()')
        install = next(s for s in steps if s['name'] == 'Install Playwright browser')
        self.assertEqual(install['run'], 'npx playwright install --with-deps "$DIAGNOSTIC_BROWSER"')

    def test_normal_browser_jobs_require_fixture_and_closed_projection_regressions(self):
        for name in ('merge-gate.yml', 'pre-release-gate.yml'):
            jobs = workflow(name)['jobs']
            self.assertIn('observability-gate', jobs['frontend-browser-matrix']['needs'])
            step = next(s for s in jobs['observability-gate']['steps']
                        if s['name'] == 'Run focused observability gate')
            self.assertIn('tests.test_browser_ci_fixture_contract tests.test_extract_browser_ci_backend_exceptions', step['run'])
            self.assertIn('tests.test_browser_ci_https_contract', step['run'])
            self.assertNotIn('continue-on-error', step)

    def test_cookie_observation_is_owned_selected_job_only_and_preserved_as_diagnostic_evidence(self):
        job = workflow('browser-ci-diagnostic.yml')['jobs']['browser-diagnostic']
        steps = job['steps']
        probe = next(s for s in steps if s.get('id') == 'cookie')
        self.assertEqual(probe['env'], {'DIAGNOSTIC_BROWSER': '${{ inputs.browser }}'})
        self.assertEqual(probe['timeout-minutes'], 2)
        self.assertNotIn('continue-on-error', probe)
        self.assertIn('node --test ../scripts/probe_browser_ci_secure_cookie.test.mjs', probe['run'])
        self.assertIn('node ../scripts/probe_browser_ci_secure_cookie.mjs "$DIAGNOSTIC_BROWSER"', probe['run'])
        self.assertIn('timeout --signal=TERM --kill-after=2s 75s', probe['run'])
        self.assertLess(steps.index(probe), next(i for i, s in enumerate(steps) if s.get('id') == 'bootstrap'))
        upload = next(s for s in steps if s['name'] == 'Upload diagnostic evidence')
        self.assertEqual(upload['if'], 'always()')
        self.assertIn('tmp/browser-ci-secure-cookie.json', upload['with']['path'].splitlines())
        for name in ('merge-gate.yml', 'pre-release-gate.yml'):
            normal = workflow(name)['jobs']['frontend-browser-matrix']
            self.assertFalse(any('probe_browser_ci_secure_cookie' in s.get('run', '') for s in normal['steps']))

    def test_chat_menu_geometry_is_explicit_selected_only_before_unchanged_real_delete(self):
        job = workflow('browser-ci-diagnostic.yml')['jobs']['browser-diagnostic']
        selected = next(s for s in job['steps'] if s.get('id') == 'browser')['run']
        case = selected.split('chat-menu)', 1)[1].split(';;', 1)[0]
        self.assertIn('CI_CHAT_MENU_GEOMETRY=1 npx playwright test e2e/direct-chat.spec.ts', case)
        self.assertIn('--retries=0 --workers=1', case)
        self.assertIn("--grep 'direct chat supports reacting to an incoming message and deleting an own message'", case)
        self.assertNotIn('--shard', case)
        guard = next(s for s in job['steps'] if s['name'] == 'Validate chat-menu geometry projection')
        self.assertEqual(guard['if'], "inputs.scenario == 'chat-menu' || inputs.scenario == 'accounts-and-menu'")
        self.assertNotIn('continue-on-error', guard)
        source = (ROOT / 'frontend/e2e/direct-chat.spec.ts').read_text()
        self.assertIn("if (process.env.CI_CHAT_MENU_GEOMETRY === '1') await attachChatMenuGeometry(page, testInfo)", source)
        case = source.split("test('direct chat supports reacting", 1)[1]
        opened = case.index('await deleteBubble.click()')
        observation = case.index('await attachChatMenuGeometry(page, testInfo)')
        click = case.index("await page.locator('.context-menu [role=\"menuitem\"]').filter({ hasText: 'حذف' }).click()")
        self.assertLess(opened, observation)
        self.assertLess(observation, click)
        self.assertIn('return messages.some((message) => message.id === deleteMessageId)', case)
        self.assertIn('.toBeFalsy()', case)
        self.assertNotIn('force:', case)
        self.assertNotIn('scrollIntoView', case)
        registered = workflow('pre-release-gate.yml')
        inputs = registered.get('on', registered.get(True))['workflow_dispatch']['inputs']
        self.assertEqual(inputs['browser_diagnostic_scenario']['options'], ['mandatory', 'offers', 'both', 'chat-menu', 'accounts-and-menu'])
        self.assertEqual(inputs['browser_diagnostic_scenario']['default'], 'both')
        for name in ('merge-gate.yml', 'pre-release-gate.yml'):
            job = workflow(name)['jobs']['frontend-browser-matrix']
            self.assertFalse(any('CI_CHAT_MENU_GEOMETRY' in str(s) for s in job['steps']))

    def test_accounts_and_menu_runs_exact_corrected_cases_and_keeps_real_geometry_action(self):
        steps = workflow('browser-ci-diagnostic.yml')['jobs']['browser-diagnostic']['steps']
        selected = next(step for step in steps if step.get('id') == 'browser')['run']
        case = selected.split('accounts-and-menu)', 1)[1].split(';;', 1)[0]
        for filename in ('account-status.spec.ts', 'accountant-owner-flow.spec.ts', 'direct-chat.spec.ts'):
            self.assertIn('e2e/' + filename, case)
        for name in ('admin UI deactivation drives inactive UX, global-lock login revocation, and reactivation recovery',
                     'owner can create pending accountant, observe activation, view it, and delete its active account',
                     'direct chat supports reacting to an incoming message and deleting an own message'):
            self.assertIn(name, case)
        self.assertIn('CI_CHAT_MENU_GEOMETRY=1', case)
        self.assertIn('--config=playwright.https-diagnostic.config.ts', case)
        self.assertIn('--retries=0 --workers=1', case)
        self.assertNotIn('--shard', case)
        self.assertEqual(case.count('npx playwright test'), 1)

    def test_actual_telegram_policy_allows_queue_production_but_never_provider_execution(self):
        source = ast.parse((ROOT / 'core/telegram_delivery_runtime_policy.py').read_bytes())
        # Keep every policy definition unchanged; inject only its configuration
        # and server dependencies, without importing any ambient settings.
        source.body = [n for n in source.body if not isinstance(n, ast.ImportFrom)
                       or n.module not in {'core.config', 'core.server_routing'}]
        module = ModuleType('_owned_browser_telegram_policy')
        module.settings = SimpleNamespace(
            trading_bot_service='api', telegram_delivery_producer_mode='queue-v1',
            telegram_delivery_execution_owner='producer-only', telegram_provider_test_authority=False,
            telegram_delivery_queue_worker_enabled=False, telegram_delivery_queue_cutover_ready=False,
        )
        module.SERVER_FOREIGN = 'foreign'
        module.current_server = lambda: 'iran'
        with patch.dict(sys.modules, {module.__name__: module}):
            exec(compile(source, 'public_telegram_runtime_policy', 'exec'), module.__dict__)
        self.assertEqual(module.configured_telegram_delivery_producer_mode().value, 'queue-v1')
        runtime = module.configured_telegram_delivery_runtime()
        self.assertEqual(runtime.mode.value, 'producer-only')
        self.assertFalse(runtime.legacy_workers_enabled)
        self.assertFalse(runtime.queue_worker_enabled)
        module.assert_queue_v1_non_bot_is_producer_only()
        with self.assertRaises(module.TelegramProviderAuthorityError):
            module.assert_telegram_provider_execution_authority()
        for name in ('telegram_delivery_queue_worker_enabled', 'telegram_delivery_queue_cutover_ready'):
            setattr(module.settings, name, True)
            with self.assertRaises(module.TelegramDeliveryRuntimeConfigurationError):
                module.configured_telegram_delivery_runtime()
            setattr(module.settings, name, False)
        module.settings.telegram_delivery_execution_owner = 'legacy'
        with self.assertRaises(module.TelegramDeliveryRuntimeConfigurationError):
            module.assert_queue_v1_non_bot_is_producer_only()
        module.settings.telegram_delivery_producer_mode = 'legacy'
        with self.assertRaises(module.TelegramProviderAuthorityError):
            module.assert_telegram_provider_execution_authority()

    def test_all_three_browser_fixtures_bind_nonexecuting_queue_role_and_attest_outbox_schema(self):
        compose = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
        for name in ('merge-gate.yml', 'pre-release-gate.yml', 'browser-ci-diagnostic.yml'):
            jobs = workflow(name)['jobs']
            job = jobs['browser-diagnostic' if name == 'browser-ci-diagnostic.yml' else 'frontend-browser-matrix']
            write = next(s for s in job['steps'] if s['name'] == 'Write CI environment file')['run']
            for field, expected in (
                ('TELEGRAM_DELIVERY_PRODUCER_MODE', 'queue-v1'),
                ('TELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER', 'queue-v1'),
                ('TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER', 'producer-only')):
                self.assertIn(field + '=' + expected + '\n', write)
            surface = next(s for s in job['steps'] if s['name'] == 'Verify browser CI backend surface')['run']
            self.assertIn('assert_queue_v1_non_bot_is_producer_only()', surface)
            self.assertIn('assert_telegram_provider_execution_authority()', surface)
            self.assertIn('except TelegramProviderAuthorityError:', surface)
            self.assertIn('has_table("offer_publication_states")', surface)
            self.assertIn('runtime.legacy_workers_enabled or runtime.queue_worker_enabled', surface)
            self.assertIn('settings.telegram_provider_test_authority', surface)
        for service in ('app', 'sync_worker', 'migration'):
            environment = compose['services'][service]['environment']
            self.assertEqual(environment['TELEGRAM_DELIVERY_EXECUTION_OWNER'], '${TELEGRAM_NON_BOT_DELIVERY_EXECUTION_OWNER:-legacy}')
            self.assertEqual(environment['TELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED'], 'false')
            self.assertEqual(environment['TELEGRAM_DELIVERY_QUEUE_CUTOVER_READY'], 'false')
            self.assertEqual(environment['BOT_TOKEN'], '${TELEGRAM_NON_BOT_BOT_TOKEN:-}')

    def test_mandatory_registration_fixture_requests_otp_after_visible_context_ready_step(self):
        source = (ROOT / 'frontend/e2e/mandatory-channel.spec.ts').read_text()
        helper = source.split('async function createInvitation(', 1)[1].split('async function loginWithSeededSession', 1)[0]
        self.assertNotIn('SETEX', helper)
        case = source.split("test('freshly activated web user", 1)[1]
        self.assertLess(case.index('await expect(requestOtpButton).toBeVisible()'), case.index('SETEX'))
        self.assertLess(case.index('SETEX'), case.index('await requestOtpButton.click()'))
        self.assertLess(case.index('expect(requested.ok()).toBeTruthy()'), case.index('autocomplete="one-time-code"'))
        self.assertIn("name: 'دریافت کد تأیید', exact: true", case)
        self.assertIn("name: 'تأیید و ادامه', exact: true", case)
        self.assertIn('requested.request().postDataJSON()).toEqual({})', case)
        self.assertIn('verified.request().postDataJSON()).toEqual({ code: invitation.otpCode })', case)
        self.assertIn('expect(page.url()).not.toContain(invitation.token)', case)
        self.assertIn('page.getByText(invitation.mobileNumber)).toHaveCount(0)', case)

    def test_registered_dispatch_defaults_preserve_every_existing_normal_and_security_guard(self):
        value = workflow('pre-release-gate.yml')
        inputs = value.get('on', value.get(True))['workflow_dispatch']['inputs']
        self.assertEqual(inputs['browser_diagnostic_only'], {
            'description': 'Run only selected synthetic browser diagnostics; never release acceptance',
            'required': False, 'default': False, 'type': 'boolean'})
        previous = {name: '!inputs.market_security_payload' for name in
                    ('repository-governance', 'observability-gate', 'market-postgres-gate', 'frontend-browser-matrix')}
        previous['security-consumer-payload'] = "github.event_name == 'workflow_dispatch' && inputs.security_payload && !inputs.market_security_payload && github.ref == 'refs/heads/main' && (inputs.git_ref == '' || inputs.git_ref == 'main' || inputs.git_ref == github.sha)"
        previous['market-security-payload'] = "github.event_name == 'workflow_dispatch' && inputs.market_security_payload && github.ref == 'refs/heads/main' && (inputs.git_ref == '' || inputs.git_ref == 'main' || inputs.git_ref == github.sha)"
        for name, condition in previous.items():
            self.assertEqual(value['jobs'][name]['if'], '!inputs.browser_diagnostic_only && (' + condition + ')')
        self.assertEqual(set(value['jobs']), {*previous, 'selected-browser-diagnostic'})
        selected = value['jobs']['selected-browser-diagnostic']
        self.assertEqual(selected['if'], "github.event_name == 'workflow_dispatch' && inputs.browser_diagnostic_only")
        self.assertEqual(selected['uses'], './.github/workflows/browser-ci-diagnostic.yml')
        self.assertEqual(selected['permissions'], {'contents': 'read', 'actions': 'read'})
        self.assertEqual(selected['with'], {'git_ref': '${{ inputs.git_ref || github.sha }}',
                                          'scenario': '${{ inputs.browser_diagnostic_scenario }}',
                                          'browser': '${{ inputs.browser_diagnostic_browser }}'})
        self.assertNotIn('secrets', selected)

    def binding(self, directory, requested, head, *, scenario='both', browser='chromium'):
        step = next(s for s in workflow('browser-ci-diagnostic.yml')['jobs']['browser-diagnostic']['steps']
                    if s.get('id') == 'source')
        environment = {'REQUESTED_REF': requested, 'REQUESTED_SCENARIO': scenario,
                       'REQUESTED_BROWSER': browser, 'GITHUB_SHA': 'b' * 40,
                       'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}
        self.binding_git_calls = 0
        def git(argv, **kwargs):
            self.binding_git_calls += 1
            self.assertEqual(argv, ['git', 'rev-parse', 'HEAD'])
            self.assertEqual(kwargs['timeout'], 10)
            return SimpleNamespace(stdout=head + '\n')
        with chdir(directory), patch.dict(os.environ, environment, clear=True), patch.object(subprocess, 'run', git):
            exec(compile(inline_python(step), 'owned_source_binding_fixture', 'exec'), {})

    def test_actual_manual_source_receipt_binds_both_source_and_workflow_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            self.binding(directory, 'a' * 40, 'a' * 40)
            path = Path(directory) / 'tmp/browser-ci-diagnostic.json'
            value = json.loads(path.read_text())
            self.assertEqual(value['source_commit'], 'a' * 40)
            self.assertEqual(value['workflow_commit'], 'b' * 40)
            self.assertTrue(value['diagnostic_only'])
            self.assertFalse(value['full_matrix_accepted'])
            self.assertFalse(value['security_accepted'])
            self.assertEqual((value['scenario'], value['browser']), ('both', 'chromium'))

    def test_manual_branch_ref_or_mismatched_head_refuses_before_fixture_preparation(self):
        for requested, head in (('main', 'a' * 40), ('a' * 40, 'c' * 40)):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(SystemExit):
                    self.binding(directory, requested, head)
                self.assertFalse((Path(directory) / 'tmp').exists())

    def test_reusable_string_inputs_are_validated_before_git_dependency_or_browser_commands(self):
        for scenario, browser in (('both; PRIVATE_COMMAND', 'chromium'), ('both', 'chromium; PRIVATE_COMMAND'),
                                  ('', 'webkit'), ('offers', 'unknown')):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(SystemExit):
                    self.binding(directory, 'a' * 40, 'a' * 40, scenario=scenario, browser=browser)
                self.assertEqual(self.binding_git_calls, 0)
                self.assertFalse((Path(directory) / 'tmp').exists())

    def test_manual_final_record_preserves_failure_and_never_becomes_global_acceptance(self):
        job = workflow('browser-ci-diagnostic.yml')['jobs']['browser-diagnostic']
        step = next(s for s in job['steps'] if s['name'] == 'Record diagnostic outcome')
        for browser, cookie in (('success', 'success'), ('failure', 'success'), ('success', 'failure')):
            with tempfile.TemporaryDirectory() as directory:
                self.binding(directory, 'a' * 40, 'a' * 40)
                environment = {key.upper() + '_OUTCOME': 'success' for key in ('source', 'cookie', 'cookie_https', 'bootstrap', 'surface', 'tls_prepare', 'tls', 'browser', 'cleanup', 'tls_cleanup')}
                environment['BROWSER_OUTCOME'] = browser
                environment['COOKIE_OUTCOME'] = cookie
                with chdir(directory), patch.dict(os.environ, environment, clear=True):
                    exec(compile(inline_python(step), 'owned_diagnostic_outcome_fixture', 'exec'), {})
                value = json.loads((Path(directory) / 'tmp/browser-ci-diagnostic.json').read_text())
                self.assertEqual(value['diagnostic_complete'], browser == cookie == 'success')
                self.assertEqual(value['step_outcomes']['browser'], browser)
                self.assertEqual(value['step_outcomes']['cookie'], cookie)
                self.assertFalse(value['full_matrix_accepted'])
                self.assertFalse(value['security_accepted'])


if __name__ == '__main__':
    unittest.main()
