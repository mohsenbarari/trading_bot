"""Source-only checks: no Docker/host operations in this test module."""
from pathlib import Path
import unittest
import yaml

PLAY = yaml.safe_load((Path(__file__).resolve().parents[1] / 'deploy/maintenance/security_trial_image_cleanup.yml').read_text())[0]
TASKS = PLAY['tasks']


class TrialImageCleanupContracts(unittest.TestCase):
    def test_fixed_lane_daemon_and_immutable_identity(self):
        self.assertEqual(PLAY['hosts'], 'boot_mount_bot')
        self.assertEqual(PLAY['environment'], {'DOCKER_HOST': 'unix:///var/run/docker.sock'})
        self.assertEqual(PLAY['vars']['trial_image'], 'sha256:61e83a73ebd37b8582140a4c0cc9abcd8895a8b0b4f5289dc1fd07d2bae78f34')
        self.assertIn("groups['all'] == ['boot_mount_bot']", TASKS[0]['ansible.builtin.assert']['that'])

    def test_default_boundary_before_only_nonforced_exact_image_removal(self):
        self.assertFalse(PLAY['vars']['security_trial_image_cleanup_apply'])
        boundary = next(i for i, task in enumerate(TASKS) if task.get('ansible.builtin.meta') == 'end_play')
        self.assertEqual(boundary, 6)
        self.assertEqual(TASKS[boundary]['when'], 'not (security_trial_image_cleanup_apply | bool) or ansible_check_mode')
        self.assertEqual(TASKS[boundary + 1]['ansible.builtin.command']['argv'], ['/usr/bin/docker', '--host=unix:///var/run/docker.sock', 'image', 'rm', '{{ trial_image }}'])
        self.assertEqual(TASKS[boundary + 1]['timeout'], 60)
        for task in TASKS[:boundary]:
            self.assertTrue(task.get('changed_when') is False or 'ansible.builtin.assert' in task)

    def test_only_public_metadata_before_boundary_and_full_postidentity_check(self):
        self.assertIn('15', str(TASKS[3]['ansible.builtin.assert']['that']))
        self.assertIn('trial_image', TASKS[4]['failed_when'])
        self.assertIn('RepoTags', str(TASKS[4]['ansible.builtin.command']['argv']))
        self.assertEqual(TASKS[2]['ansible.builtin.command'], TASKS[-2]['ansible.builtin.command'])
        self.assertIn('trial_runtime_before.stdout_lines | sort', TASKS[-2]['failed_when'])
        self.assertEqual(TASKS[5]['failed_when'], 'trial_image in trial_references_before.stdout')
        self.assertIn('trial_references_before.results', TASKS[-1]['loop'])
        self.assertEqual(TASKS[-1]['failed_when'], 'trial_reference_after.stdout != item.stdout')
        for task in TASKS:
            argv = task.get('ansible.builtin.command', {}).get('argv', [])
            if argv and argv[0] == '/usr/bin/docker':
                self.assertEqual(argv[1], '--host=unix:///var/run/docker.sock')
        raw = yaml.safe_dump([task.get('ansible.builtin.command', {}) for task in TASKS])
        for forbidden in ('--force', 'prune', 'volume', 'docker stop', 'reboot'):
            self.assertNotIn(forbidden, raw)
        for task in TASKS:
            argv = task.get('ansible.builtin.command', {}).get('argv', [])
            if '-c' in argv:
                compile(argv[argv.index('-c') + 1], '<native-admission>', 'exec')
