"""Source and pure public synthetic requests; no App configuration or browser."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
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

    def test_only_selected_job_prepares_and_trusts_ca_after_build_without_weakening_validation(self):
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
        self.assertEqual(selected['run'].count('--config=playwright.https-diagnostic.config.ts'), 4)
        self.assertEqual(selected['run'].count('--retries=0 --workers=1'), 4)
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
        for name in ('merge-gate.yml', 'pre-release-gate.yml'):
            normal = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())['jobs']['frontend-browser-matrix']
            self.assertNotIn('BROWSER_CI_PREVIEW_TLS', str(normal))
            environment = next(step for step in normal['steps'] if step['name'] == 'Write CI environment file')['run']
            self.assertIn('FRONTEND_URL=http://127.0.0.1:4173\n', environment)


if __name__ == '__main__':
    unittest.main()
