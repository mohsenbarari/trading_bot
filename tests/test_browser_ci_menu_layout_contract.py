"""Public CI workflow contracts; no browser, backend or configuration import."""
import json
from pathlib import Path
import shlex
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
STEP = 'Validate chat-menu layout with candidate dependencies'


class BrowserMenuLayoutContractTests(unittest.TestCase):
    def test_selected_menu_jobs_run_both_real_unit_files_after_npm_ci_before_any_backend_or_e2e(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/browser-ci-diagnostic.yml').read_text())
        steps = workflow['jobs']['browser-diagnostic']['steps']
        unit = next(step for step in steps if step['name'] == STEP)
        self.assertEqual(unit['if'], "inputs.scenario == 'chat-menu' || inputs.scenario == 'accounts-and-menu'")
        self.assertEqual(unit['working-directory'], 'frontend')
        self.assertEqual(unit['timeout-minutes'], 2)
        self.assertNotIn('continue-on-error', unit)
        self.assertEqual(shlex.split(unit['run']), ['npm', 'run', 'test:unit:run', '--',
            'src/utils/messageContextMenuModel.test.ts', 'src/components/chat/ChatContextMenu.test.ts', '--maxWorkers=1'])
        install = next(step for step in steps if step['name'] == 'Install frontend dependencies')
        self.assertEqual(install['run'], 'npm ci')
        self.assertLess(steps.index(install), steps.index(unit))
        for name in ('Bootstrap backend stack', 'Run selected diagnostic scenarios'):
            self.assertLess(steps.index(unit), next(i for i, step in enumerate(steps) if step['name'] == name))

    def test_candidate_lock_and_normal_unit_command_are_used_without_module_substitution(self):
        package = json.loads((ROOT / 'frontend/package.json').read_bytes())
        lock = json.loads((ROOT / 'frontend/package-lock.json').read_bytes())
        self.assertEqual(package['scripts']['test:unit:run'], 'node scripts/check-node-security-baseline.mjs && vitest run')
        self.assertEqual(int(lock['packages']['node_modules/vitest']['version'].split('.')[0]), 4)

    def test_normal_browser_matrices_and_selected_real_actions_are_preserved(self):
        for name in ('merge-gate.yml', 'pre-release-gate.yml'):
            job = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())['jobs']['frontend-browser-matrix']
            self.assertNotIn(STEP, [step['name'] for step in job['steps']])
            self.assertEqual(job['strategy']['matrix'], {'browser': ['chromium', 'firefox', 'webkit'], 'shard': [1, 2, 3, 4]})
            self.assertEqual(job['timeout-minutes'], 75)
        source = (ROOT / 'frontend/e2e/direct-chat.spec.ts').read_text()
        self.assertIn("await page.locator('.context-menu [role=\"menuitem\"]').filter({ hasText: 'حذف' }).click()", source)
        self.assertIn('return messages.some((message) => message.id === deleteMessageId)', source)


if __name__ == '__main__':
    unittest.main()
