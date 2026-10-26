"""Source and pure public synthetic requests; no App configuration or browser."""
import ast
from pathlib import Path
from types import SimpleNamespace
from contextlib import chdir
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]


class BrowserHttpsContractTests(unittest.TestCase):
    def auth_function(self, name, namespace):
        source = ast.parse((ROOT / 'api/routers/auth.py').read_bytes())
        node = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'unchanged_public_auth_guard', 'exec'), namespace)
        return namespace[name]

    def test_real_guard_accepts_preserved_https_origin_with_http_scope_and_trusted_proxy_scheme(self):
        class Refused(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
        guard = self.auth_function('_assert_registration_context_same_origin', {
            'Request': object, 'HTTPException': Refused, 'urlsplit': urlsplit,
        })
        def request(**headers):
            return SimpleNamespace(url=SimpleNamespace(scheme='http'), headers={
                'origin': 'https://127.0.0.1:4173', 'host': '127.0.0.1:4173', **headers,
            })
        guard(request(**{'x-forwarded-proto': 'https'}))
        for value in (request(), request(**{'x-forwarded-proto': 'untrusted'}),
                      request(**{'x-forwarded-proto': 'https', 'host': 'foreign.invalid'}),
                      request(**{'x-forwarded-proto': 'https', 'origin': 'https://foreign.invalid'}),
                      request(**{'x-forwarded-host': '127.0.0.1:4173', 'host': '127.0.0.1:8000', 'x-forwarded-proto': 'https'}),
                      request(**{'x-forwarded-proto': 'https', 'sec-fetch-site': 'cross-site'})):
            with self.assertRaises(Refused) as outcome:
                guard(value)
            self.assertEqual(outcome.exception.status_code, 403)

    def test_real_staging_cookie_guard_retains_secure_host_cookie(self):
        for environment in ('staging', 'production'):
            namespace = {'settings': SimpleNamespace(environment=environment)}
            secure = self.auth_function('_registration_context_secure_cookie', namespace)
            self.assertTrue(secure())
            namespace.update(REGISTRATION_CONTEXT_COOKIE_PRODUCTION='__Host-web_registration',
                             REGISTRATION_CONTEXT_COOKIE_DEVELOPMENT='web_registration')
            name = self.auth_function('_registration_context_cookie_name', namespace)
            self.assertEqual(name(), '__Host-web_registration')

    def test_actual_cors_projection_uses_selected_https_frontend_without_wildcard(self):
        namespace = {}
        exec(compile((ROOT / 'core/deployment_surface.py').read_bytes(), 'public_deployment_surface', 'exec'), namespace)
        allowed = namespace['allowed_cors_origins'](SimpleNamespace(
            environment='staging', frontend_url='https://127.0.0.1:4173'))
        self.assertIn('https://127.0.0.1:4173', allowed)
        self.assertNotIn('*', allowed)
        self.assertNotIn('https://foreign.invalid', allowed)

    def test_selected_config_keeps_certificate_validation_and_normal_config_unchanged(self):
        ordinary = (ROOT / 'frontend/playwright.config.ts').read_text()
        selected = (ROOT / 'frontend/playwright.https-diagnostic.config.ts').read_text()
        self.assertIn("const ciBaseUrl = 'http://127.0.0.1:4173'", ordinary)
        self.assertIn("process.env.CI !== 'true' || process.env.BROWSER_CI_PREVIEW_TLS !== '1'", selected)
        self.assertIn("baseURL: 'https://127.0.0.1:4173'", selected)
        self.assertIn("url: 'https://127.0.0.1:4173'", selected)
        self.assertEqual(selected.count('ignoreHTTPSErrors: false'), 2)
        self.assertIn('reuseExistingServer: false', selected)
        self.assertIn('export default defineConfig({\n  ...ordinary,', selected)
        self.assertNotIn('defineConfig(ordinary,', selected)
        self.assertNotIn('ignoreHTTPSErrors: true', selected)
        for filename in ('mandatory-channel.spec.ts', 'market-offers.spec.ts', 'direct-chat.spec.ts'):
            self.assertNotIn('ignoreHTTPSErrors', (ROOT / 'frontend/e2e' / filename).read_text())

    def test_vite_tls_is_ci_only_and_preserves_preview_host_with_a_tls_listener_derived_scheme(self):
        source = (ROOT / 'frontend/vite.config.ts').read_text()
        helper = (ROOT / 'frontend/scripts/browser-ci-https.mjs').read_text()
        self.assertIn('const browserCiHttps = previewHttps(process.env)', source)
        self.assertIn('https: browserCiHttps', source)
        self.assertIn('...(browserCiHttps ? { configure: trustedHttpsProxy } : {})', source)
        self.assertIn('changeOrigin: true', source.split('preview:', 1)[0])
        self.assertIn('changeOrigin: false', source.split('preview:', 1)[1])
        self.assertIn("env.BROWSER_CI_PREVIEW_TLS !== '1'", helper)
        self.assertIn("env.CI !== 'true'", helper)
        self.assertIn("incoming.socket?.encrypted !== true", helper)
        self.assertIn("request.removeHeader('x-forwarded-proto')", helper)
        self.assertIn("request.removeHeader('x-forwarded-host')", helper)
        self.assertIn("request.setHeader('x-forwarded-proto', 'https')", helper)
        self.assertNotIn('NODE_TLS_REJECT_UNAUTHORIZED', helper)
        self.assertNotIn('FORWARDED_ALLOW_IPS', helper)

    def test_selected_job_prepares_and_trusts_ca_after_build_without_weakening_validation(self):
        value = yaml.safe_load((ROOT / '.github/workflows/browser-ci-diagnostic.yml').read_text())
        job = value['jobs']['browser-diagnostic']
        steps = job['steps']
        prepare = next(step for step in steps if step.get('id') == 'tls_prepare')
        trust = next(step for step in steps if step.get('id') == 'tls')
        cleanup = next(step for step in steps if step.get('id') == 'tls_cleanup')
        selected = next(step for step in steps if step.get('id') == 'browser')
        build = next(step for step in steps if step['name'] == 'Build frontend')
        self.assertLess(steps.index(build), steps.index(prepare))
        self.assertLess(steps.index(prepare), steps.index(trust))
        self.assertLess(steps.index(trust), steps.index(selected))
        self.assertIn('node --test scripts/probe_browser_ci_https.test.mjs', prepare['run'])
        self.assertFalse((ROOT / 'frontend/scripts/browser-ci-https.test.mjs').exists())
        self.assertIn('node frontend/scripts/browser-ci-https.mjs prepare', prepare['run'])
        self.assertIn('[[ ! -e "$browser_ci_ca_anchor" && ! -L "$browser_ci_ca_anchor" ]]', trust['run'])
        self.assertIn('sudo install -m 0644 -- "$BROWSER_CI_TLS_CA" "$browser_ci_ca_anchor"', trust['run'])
        self.assertIn('sudo update-ca-certificates', trust['run'])
        self.assertEqual(selected['run'].count('--config=playwright.https-diagnostic.config.ts'), 6)
        self.assertEqual(selected['run'].count('--retries=0 --workers=1'), 6)
        self.assertEqual(cleanup['if'], "always() && env.BROWSER_CI_TLS_CA_ANCHOR != ''")
        self.assertEqual(cleanup['run'].count('== "$BROWSER_CI_TLS_CA_IDENTITY"'), 2)
        self.assertIn('sudo cmp -s -- "$BROWSER_CI_TLS_CA" "$BROWSER_CI_TLS_CA_ANCHOR"', cleanup['run'])
        self.assertIn('sudo rm -- "$BROWSER_CI_TLS_CA_ANCHOR"', cleanup['run'])
        self.assertNotIn('rm -r', cleanup['run'])
        for step in (prepare, trust, selected, cleanup):
            self.assertNotIn('continue-on-error', step)
        environment = next(step for step in steps if step['name'] == 'Write CI environment file')['run']
        self.assertIn('ENVIRONMENT=staging\n', environment)
        self.assertIn('FRONTEND_URL=https://127.0.0.1:4173\n', environment)
        surface = next(step for step in steps if step.get('id') == 'surface')['run']
        self.assertIn("Origin: https://127.0.0.1:4173", surface)
        self.assertNotIn('NODE_TLS_REJECT_UNAUTHORIZED', str(job))
        self.assertNotIn('FORWARDED_ALLOW_IPS', str(job))
        artifact = next(step for step in steps if step['name'] == 'Upload diagnostic evidence')['with']['path']
        self.assertNotIn('RUNNER_TEMP', artifact)
        self.assertNotIn('BROWSER_CI_TLS', artifact)

    def test_full_browser_matrices_reuse_strict_tls_after_both_builds_and_preserve_every_gate(self):
        selected = yaml.safe_load((ROOT / '.github/workflows/browser-ci-diagnostic.yml').read_text())['jobs']['browser-diagnostic']
        selected_step = lambda name: next(step for step in selected['steps'] if step['name'] == name)
        for name in ('merge-gate.yml', 'pre-release-gate.yml'):
            normal = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())['jobs']['frontend-browser-matrix']
            steps = normal['steps']
            step = lambda name: next(value for value in steps if value['name'] == name)
            prepare = step('Prepare CI HTTPS fixture')
            trust = step('Trust synthetic CI CA')
            cleanup = step('Remove only the owned synthetic CI CA anchor')
            gate = step('Run Playwright browser gate')
            production = step('Run production login browser gate')
            for build in ('Build frontend', 'Build production login browser fixture'):
                self.assertLess(steps.index(step(build)), steps.index(prepare))
            self.assertLess(steps.index(prepare), steps.index(trust))
            self.assertLess(steps.index(trust), steps.index(gate))
            self.assertLess(steps.index(gate), steps.index(production))
            self.assertEqual(prepare['run'], selected_step('Prepare selected CI HTTPS fixture')['run'])
            self.assertEqual(trust['run'], selected_step('Trust selected synthetic CI CA')['run']
                             .replace('browser-ci-selected-', 'browser-ci-matrix-'))
            self.assertEqual(cleanup['run'], selected_step('Remove only the owned synthetic CI CA anchor')['run']
                             .replace('browser-ci-selected-', 'browser-ci-matrix-'))
            self.assertEqual(cleanup['if'], "always() && env.BROWSER_CI_TLS_CA_ANCHOR != ''")
            self.assertLess(steps.index(step('Tear down backend stack')), steps.index(cleanup))
            for value in (prepare, trust, gate, production, cleanup):
                self.assertNotIn('continue-on-error', value)
            for command in ('npm run test:e2e --', 'npm run test:e2e:firefox --', 'npm run test:e2e:webkit --'):
                self.assertIn(command + ' --config=playwright.https-diagnostic.config.ts --shard=${{ matrix.shard }}/4', gate['run'])
            for option in ('--grep', '--retries', '--workers', '--timeout'):
                self.assertNotIn(option, gate['run'])
            self.assertIn('--config=playwright.https-production-login.config.ts', production['run'])
            self.assertEqual(production['if'], 'matrix.shard == 1')
            self.assertEqual(normal['strategy'], {'fail-fast': False, 'matrix': {
                'browser': ['chromium', 'firefox', 'webkit'], 'shard': [1, 2, 3, 4]}})
            self.assertEqual(normal['timeout-minutes'], 75)
            self.assertEqual(normal['permissions'], {'contents': 'read', 'actions': 'read'})
            environment = step('Write CI environment file')['run']
            self.assertIn('ENVIRONMENT=staging\n', environment)
            self.assertIn('FRONTEND_URL=https://127.0.0.1:4173\n', environment)
            surface = step('Verify browser CI backend surface')['run']
            self.assertIn("Origin: https://127.0.0.1:4173", surface)
            self.assertIn('allow-origin: https://127.0.0.1:4173', surface)
            self.assertNotIn('NODE_TLS_REJECT_UNAUTHORIZED', str(normal))
            self.assertNotIn('FORWARDED_ALLOW_IPS', str(normal))
            artifact = step('Upload Playwright artifacts')['with']['path']
            self.assertNotIn('RUNNER_TEMP', artifact)
            self.assertNotIn('BROWSER_CI_TLS', artifact)

    def test_https_cookie_evidence_is_separate_bounded_and_requires_true_typed_values(self):
        job = yaml.safe_load((ROOT / '.github/workflows/browser-ci-diagnostic.yml').read_text())['jobs']['browser-diagnostic']
        steps = job['steps']
        probe = next(step for step in steps if step.get('id') == 'cookie_https')
        self.assertLess(next(i for i, step in enumerate(steps) if step.get('id') == 'tls'), steps.index(probe))
        self.assertIn('node ../scripts/probe_browser_ci_secure_cookie.mjs "$DIAGNOSTIC_BROWSER" https', probe['run'])
        self.assertNotIn('continue-on-error', probe)
        source = probe['run'].split("<<'PYCODE'\n", 1)[1].rsplit('\nPYCODE', 1)[0]
        good = {'browser': 'webkit', 'accepted': True, 'retransmitted': True}
        values = [good, {**good, 'accepted': 1}, {**good, 'retransmitted': False},
                  {**good, 'browser': 'foreign'}, {**good, 'private': 'PRIVATE_SENTINEL'},
                  {'browser': 'webkit'}, None, b'x' * 4097, b'PRIVATE_SENTINEL']
        for index, value in enumerate(values):
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / 'frontend').mkdir(); (base / 'tmp').mkdir()
                raw = value if isinstance(value, bytes) else json.dumps(value).encode()
                (base / 'tmp/browser-ci-secure-cookie-https.json').write_bytes(raw)
                with chdir(base / 'frontend'), patch.object(sys, 'argv', ['owned_public_verifier', 'webkit']):
                    if index == 0:
                        exec(compile(source, 'owned_https_cookie_verifier', 'exec'), {})
                    else:
                        with self.assertRaises(SystemExit) as closed:
                            exec(compile(source, 'owned_https_cookie_verifier', 'exec'), {})
                        self.assertIn(str(closed.exception), {'https_cookie_report_bound', 'https_cookie_report_invalid',
                                                            'https_cookie_not_accepted_and_retransmitted'})
                        self.assertNotIn('PRIVATE', str(closed.exception))
        upload = next(step for step in steps if step['name'] == 'Upload diagnostic evidence')['with']['path']
        self.assertIn('tmp/browser-ci-secure-cookie.json', upload.splitlines())
        self.assertIn('tmp/browser-ci-secure-cookie-https.json', upload.splitlines())


if __name__ == '__main__':
    unittest.main()
