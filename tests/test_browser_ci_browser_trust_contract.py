"""CI-only trust wiring; no browser, NSS, application configuration or host calls."""
from contextlib import chdir
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCOPED = {
    'HOME': '${{ env.BROWSER_CI_BROWSER_HOME }}',
    'XDG_DATA_HOME': '${{ env.BROWSER_CI_BROWSER_HOME }}/.local/share',
    'XDG_CONFIG_HOME': '${{ env.BROWSER_CI_BROWSER_HOME }}/.config',
    'TMPDIR': '${{ env.BROWSER_CI_BROWSER_HOME }}/tmp',
    'PLAYWRIGHT_BROWSERS_PATH': '${{ env.BROWSER_CI_PLAYWRIGHT_BROWSERS_PATH }}',
    'PLAYWRIGHT_FIREFOX_POLICIES_JSON': '${{ env.BROWSER_CI_FIREFOX_POLICIES_JSON }}',
}


class BrowserTrustContractTests(unittest.TestCase):
    def jobs(self):
        for filename in ('merge-gate.yml', 'pre-release-gate.yml', 'browser-ci-diagnostic.yml'):
            workflow = yaml.safe_load((ROOT / '.github/workflows' / filename).read_text())
            selected = filename == 'browser-ci-diagnostic.yml'
            yield filename, workflow['jobs']['browser-diagnostic' if selected else 'frontend-browser-matrix'], selected

    def test_all_browser_launches_use_owned_trust_home_and_original_browser_cache_only_after_install(self):
        for filename, job, selected in self.jobs():
            with self.subTest(filename=filename):
                steps = job['steps']
                trust = next(step for step in steps if step.get('id') == 'browser_trust')
                install = next(step for step in steps if step['name'].startswith('Install Playwright browser'))
                tls = next(step for step in steps if step['name'] in ('Trust synthetic CI CA', 'Trust selected synthetic CI CA'))
                self.assertLess(steps.index(install), steps.index(trust))
                self.assertLess(steps.index(tls), steps.index(trust))
                self.assertEqual(trust['timeout-minutes'], 2)
                self.assertEqual(trust['run'].splitlines(), [
                    'node --test scripts/probe_browser_ci_browser_trust.test.mjs',
                    'sudo apt-get install --no-install-recommends -y libnss3-tools >/dev/null',
                    'node frontend/scripts/browser-ci-trust.mjs prepare',
                ])
                names = ('Observe secure cookie over trusted selected HTTPS', 'Run selected diagnostic scenarios') if selected else (
                    'Run Playwright browser gate', 'Run production login browser gate')
                launches = [step for step in steps if step['name'] in names]
                self.assertEqual(len(launches), 2)
                for step in launches:
                    self.assertLess(steps.index(trust), steps.index(step))
                    for key, value in SCOPED.items():
                        self.assertEqual(step['env'][key], value)
                    self.assertNotIn('continue-on-error', step)
                self.assertNotIn('HOME', job.get('env', {}))
                for step in steps:
                    if step not in launches:
                        self.assertNotIn('HOME', step.get('env', {}))

    def test_cleanup_is_always_owned_and_precedes_diagnostic_receipt_without_exporting_trust_material(self):
        for filename, job, selected in self.jobs():
            with self.subTest(filename=filename):
                steps = job['steps']
                cleanup = next(step for step in steps if step.get('id') == 'browser_trust_cleanup')
                self.assertEqual(cleanup['if'], "always() && env.BROWSER_CI_TRUST_ROOT != ''")
                self.assertEqual(cleanup['run'], 'node frontend/scripts/browser-ci-trust.mjs cleanup')
                self.assertEqual(cleanup['timeout-minutes'], 2)
                self.assertNotIn('continue-on-error', cleanup)
                for step in steps:
                    if 'upload-artifact@' in step.get('uses', ''):
                        paths = step['with']['path']
                        self.assertNotIn('RUNNER_TEMP', paths)
                        self.assertNotIn('BROWSER_CI_TRUST', paths)
                        self.assertNotIn('BROWSER_CI_BROWSER_HOME', paths)
                if selected:
                    receipt = next(step for step in steps if step['name'] == 'Record diagnostic outcome')
                    self.assertLess(steps.index(cleanup), steps.index(receipt))

    def test_selected_raw_http_observation_and_full_quality_policy_remain_separate(self):
        for filename, job, selected in self.jobs():
            with self.subTest(filename=filename):
                if selected:
                    steps = job['steps']
                    raw = next(step for step in steps if step.get('id') == 'cookie')
                    trust = next(step for step in steps if step.get('id') == 'browser_trust')
                    self.assertLess(steps.index(raw), steps.index(trust))
                    self.assertNotIn('HOME', raw.get('env', {}))
                    self.assertIn('> ../tmp/browser-ci-secure-cookie.json', raw['run'])
                    https = next(step for step in steps if step.get('id') == 'cookie_https')
                    self.assertIn('> ../tmp/browser-ci-secure-cookie-https.json', https['run'])
                    self.assertIn("value['accepted'] is not True", https['run'])
                    self.assertIn("value['retransmitted'] is not True", https['run'])
                else:
                    self.assertEqual(job['timeout-minutes'], 75)
                    self.assertFalse(job['strategy']['fail-fast'])
                    self.assertEqual(job['strategy']['matrix'], {
                        'browser': ['chromium', 'firefox', 'webkit'], 'shard': [1, 2, 3, 4]})
                    gate = next(step for step in job['steps'] if step['name'] == 'Run Playwright browser gate')
                    for flag in ('--retries', '--timeout', '--workers', '--grep'):
                        self.assertNotIn(flag, gate['run'])
                    preflight = (ROOT / '.github/workflows' / filename).read_text()
                    self.assertIn('tests.test_browser_ci_browser_trust_contract', preflight)
                self.assertNotIn('ignoreHTTPSErrors: true', str(job))
                self.assertNotIn('NODE_TLS_REJECT_UNAUTHORIZED', str(job))

    def test_a_trust_or_cleanup_failure_cannot_produce_a_complete_selected_receipt(self):
        selected = next(job for _, job, is_selected in self.jobs() if is_selected)
        receipt = next(step for step in selected['steps'] if step['name'] == 'Record diagnostic outcome')
        code = receipt['run'].split("<<'PYCODE'\n", 1)[1].rsplit('\nPYCODE', 1)[0]
        keys = ('source', 'cookie', 'cookie_https', 'bootstrap', 'surface', 'tls_prepare', 'tls',
                'browser', 'cleanup', 'tls_cleanup', 'browser_trust', 'browser_trust_cleanup')
        for failure in (None, 'browser_trust', 'browser_trust_cleanup'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary, chdir(temporary):
                environment = {key.upper() + '_OUTCOME': 'failure' if key == failure else 'success' for key in keys}
                with patch.dict(os.environ, environment, clear=True):
                    exec(compile(code, 'owned_diagnostic_receipt', 'exec'), {})
                value = json.loads(Path('tmp/browser-ci-diagnostic.json').read_text())
                self.assertEqual(value['diagnostic_complete'], failure is None)
                self.assertFalse(value['full_matrix_accepted'])
                self.assertFalse(value['security_accepted'])
                if failure:
                    self.assertEqual(value['step_outcomes'][failure], 'failure')


if __name__ == '__main__':
    unittest.main()
