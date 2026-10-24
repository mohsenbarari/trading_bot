"""Source-only contract for exact unused04 retirement; no host commands."""
import ast
from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined
import yaml

REPO = Path(__file__).resolve().parents[1]
PLAY = yaml.safe_load((REPO / 'deploy/maintenance/web_engine_archive_unapplied.yml').read_text())[0]
TASK = PLAY['tasks'][1]
ARGV = TASK['ansible.builtin.command']['argv']
CODE = ARGV[ARGV.index('-c') + 1]
TREE = ast.parse(CODE)


class UnappliedWebPlanRetirementContracts(unittest.TestCase):
    def test_exact_original04_identity_and_separate_non_destructive_archive(self):
        values = {}
        for node in TREE.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                value = node.value.args[0] if isinstance(node.value, ast.Call) else node.value
                if isinstance(value, ast.Constant): values[node.targets[0].id] = value.value
        self.assertEqual(values['expected'], '5ad57c8c91d758df46a50c5cd661f2c35665e5561b671b669f12ba40f57fb373')
        self.assertEqual(values['root'], '/root/.local/security-web-engine-20261023')
        self.assertEqual(values['archive'], '/root/.local/security-web-engine-20261023-unapplied-04-5ad57c8c')
        checks = {}
        for node in ast.walk(TREE):
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Subscript) and isinstance(node.left.value, ast.Name) and node.left.value.id == 'plan' and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq):
                if isinstance(node.left.slice, ast.Constant) and isinstance(node.comparators[0], ast.Constant):
                    checks[node.left.slice.value] = node.comparators[0].value
        self.assertEqual(checks, {'operation_id': 'security-web-engine-20261023-04',
            'nonce': 'ff0dc99a002840a5b4b2e44312e09960', 'deadline_utc': '2026-10-24T05:20:00Z'})

    def test_original_full_guards_under_marker_lock_precede_exclusive_directory_rename(self):
        for literal in ('fcntl.LOCK_EX|fcntl.LOCK_NB', "controller.invariant_contract(plan,expected,owned=False)",
                        "controller.runtime_contract(plan,'baseline')", "hashlib.sha256(read(root/'plan.json',2097152)).hexdigest()==expected"):
            self.assertIn(literal, CODE)
            self.assertLess(CODE.index(literal), CODE.index('if rename('))
        self.assertIn('controller.B.identity(os.fstat(fd))==controller.B.identity(marker.lstat())', CODE)
        self.assertIn("os.open(marker,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)", CODE)
        self.assertIn("(root.lstat().st_dev,root.lstat().st_ino)==(info.st_dev,info.st_ino)", CODE)
        self.assertIn('rename(-100,os.fsencode(root),-100,os.fsencode(archive),1)', CODE)
        self.assertIn('os.fsync(parent)', CODE)

    def test_default_and_check_mode_only_verify_before_any_rename(self):
        env = Environment(undefined=StrictUndefined); env.filters['bool'] = bool
        for apply, check, expected in ((False, False, 'check'), (True, True, 'check'), (True, False, 'archive')):
            self.assertEqual(env.from_string(ARGV[-1]).render(web_engine_archive_apply=apply, ansible_check_mode=check), expected)
        self.assertEqual(env.from_string(ARGV[-1]).render(ansible_check_mode=False), 'check')
        self.assertIn("if sys.argv[1]=='archive':", CODE)
        self.assertIn('not os.path.lexists(archive)', CODE)
        self.assertEqual(TASK['timeout'], 180)

    def test_no_delete_reboot_service_or_secret_payload_operations(self):
        for forbidden in ('unlink(', 'rmtree(', 'os.replace(', 'reboot(', 'subprocess.', '_master_key(', 'validate_release_env('):
            self.assertNotIn(forbidden, CODE)
        self.assertIn('os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK', CODE)
        self.assertIn('identity(before)==identity(os.fstat(fd))==identity(path.lstat())', CODE)


if __name__ == '__main__': unittest.main()
