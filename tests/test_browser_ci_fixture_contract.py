"""Public source/configuration and owned files; no backend, browser or network."""
import ast
from contextlib import chdir
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
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
            self.assertIn('--config=playwright.production-login.config.ts', gate['run'])
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
        self.assertEqual(inputs['scenario']['options'], ['mandatory', 'offers', 'both'])
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
        self.assertEqual(selected['run'].count('--retries=0 --workers=1'), 3)
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
            self.assertNotIn('continue-on-error', step)

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
        for browser in ('success', 'failure'):
            with tempfile.TemporaryDirectory() as directory:
                self.binding(directory, 'a' * 40, 'a' * 40)
                environment = {key.upper() + '_OUTCOME': 'success' for key in ('source', 'bootstrap', 'surface', 'browser', 'cleanup')}
                environment['BROWSER_OUTCOME'] = browser
                with chdir(directory), patch.dict(os.environ, environment, clear=True):
                    exec(compile(inline_python(step), 'owned_diagnostic_outcome_fixture', 'exec'), {})
                value = json.loads((Path(directory) / 'tmp/browser-ci-diagnostic.json').read_text())
                self.assertEqual(value['diagnostic_complete'], browser == 'success')
                self.assertEqual(value['step_outcomes']['browser'], browser)
                self.assertFalse(value['full_matrix_accepted'])
                self.assertFalse(value['security_accepted'])


if __name__ == '__main__':
    unittest.main()
