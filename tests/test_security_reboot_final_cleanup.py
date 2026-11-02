"""A self-revoking final credential must not be reused by module housekeeping."""
from pathlib import Path
import unittest
import yaml


class FinalCleanupTransport(unittest.TestCase):
    def test_final_revocation_uses_one_web_local_ssh_child_after_result_fetch(self):
        path = Path(__file__).resolve().parents[1] / 'deploy/maintenance/reboot_owned_external.yml'
        tasks = yaml.safe_load(path.read_text())[0]['tasks']
        cleanup = tasks[-1]
        self.assertIn('ansible.builtin.fetch', tasks[-2])
        self.assertEqual(cleanup['delegate_to'], 'localhost')
        argv = cleanup['ansible.builtin.command']['argv']
        self.assertEqual(argv[:3], ['/usr/bin/ssh', '-F', '/dev/null'])
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn('IdentitiesOnly=yes', argv)
        self.assertIn('ForwardAgent=no', argv)
        self.assertEqual(argv[-2], 'root@65.109.216.187')
        self.assertIn(' cleanup --apply RESUME-OWNED-KERNEL-20261023-02', argv[-1])
        self.assertTrue(cleanup['no_log'])
        self.assertEqual(len([t for t in tasks if t.get('delegate_to') == 'localhost']), 1)


if __name__ == '__main__':
    unittest.main()
