"""Hermetic finite durable-launch admission; never execute its host commands."""
import ast
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jinja2 import Environment, StrictUndefined
import yaml

REPO = Path(__file__).resolve().parents[1]
PLAY = yaml.safe_load((REPO / 'deploy/maintenance/web_engine_launch.yml').read_text())[0]
TASKS = PLAY['tasks']
_ARGV = TASKS[1]['ansible.builtin.command']['argv']
CODE = _ARGV[_ARGV.index('-c') + 1]


class DurableWebEngineLaunchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for target in ('socket.socket.connect', 'socket.create_connection', 'socket.getaddrinfo', 'subprocess.Popen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('no host or network')))
        self.process = self.stack.enter_context(patch('subprocess.run', return_value=SimpleNamespace(stdout=b'not-found\n')))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.web-launch-fixture-', dir=REPO)))
        self.root = self.temp / 'workspace'; self.root.mkdir(mode=0o700)
        self.control = self.temp / 'control'; self.control.mkdir(mode=0o700)
        tree = ast.parse(CODE)
        tree.body = [node for node in tree.body if not isinstance(node, ast.Expr)]
        self.code = {}; exec(compile(tree, '<finite-launch-checker>', 'exec'), self.code)
        self.code.update(ROOT=self.root, CONTROL=self.control, PENDING=self.control / 'pending.json', native=lambda: None)
        files = []
        for name in self.code['SOURCES']:
            path = self.root / 'source' / name; path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            data = (REPO / name).read_bytes() if name.endswith('web_engine_controller.py') else b'bounded public fixture\n'
            path.write_bytes(data); path.chmod(0o600)
            files.append({'path': str(path), 'sha256': sha256(data).hexdigest(), 'size': len(data)})
        self.plan = {'operation_id': 'security-web-engine-20261023-05', 'nonce': 'b4bd11449f9843cd930b3f1dfddac5c5',
                     'deadline_utc': '2026-10-24T06:00:00Z', 'source_root': str(self.root / 'source'), 'files': files}

    def verify(self, plan=None):
        raw = json.dumps(self.plan if plan is None else plan).encode()
        path = self.root / 'plan.json'; path.write_bytes(raw); path.chmod(0o600)
        self.code['verify'](sha256(raw).hexdigest())

    def test_real_fixture_thirteen_source_digest_admission_invokes_only_readonly_unit_query(self):
        self.verify()
        self.assertEqual(len(self.code['SOURCES']), 13)
        self.assertEqual(self.process.call_args.args[0], ['/usr/bin/systemctl', 'show',
            'security-web-engine-05-b4bd11449f9843cd930b3f1dfddac5c5.service', '-p', 'LoadState', '--value'])
        self.assertEqual(self.process.call_args.kwargs['timeout'], 10)

    def test_changed_plan_sha_or_any_sealed_source_refuses_before_unit_query(self):
        self.verify(); self.process.reset_mock()
        with self.assertRaises(AssertionError): self.code['verify']('0' * 64)
        self.process.assert_not_called()
        for name in self.code['SOURCES']:
            path = self.root / 'source' / name; saved = path.read_bytes(); path.write_bytes(saved + b'!')
            with self.subTest(source=name), self.assertRaises(AssertionError): self.verify()
            path.write_bytes(saved); self.process.assert_not_called()

    def test_wrong_operation_nonce_window_source_or_duplicate_binding_refuses(self):
        for key, wrong in (('operation_id', 'security-web-engine-20261023-04'), ('nonce', '0' * 32),
                           ('deadline_utc', '2026-10-24T06:00:01Z'), ('source_root', '/wrong')):
            plan = deepcopy(self.plan); plan[key] = wrong
            with self.subTest(field=key), self.assertRaises(AssertionError): self.verify(plan)
        plan = deepcopy(self.plan); plan['files'].append(plan['files'][0])
        with self.assertRaises(AssertionError): self.verify(plan)
        self.process.assert_not_called()

    def test_resigned_changed_controller_cannot_pass_independent_c96_pin(self):
        plan = deepcopy(self.plan)
        entry = next(item for item in plan['files'] if item['path'].endswith('web_engine_controller.py'))
        data = b'changed controller\n'; Path(entry['path']).write_bytes(data)
        entry.update(sha256=sha256(data).hexdigest(), size=len(data))
        with self.assertRaises(AssertionError): self.verify(plan)
        self.process.assert_not_called()

    def test_existing_log_reservation_source_lock_journal_or_unit_refuses(self):
        for path in (self.root / 'controller05.log', self.control / 'private-primary-controller.lock',
                     self.control / 'security-web-engine-20261023-05.source.lock',
                     self.control / 'security-web-engine-20261023-05.journal.json', self.control / 'pending.json'):
            path.write_bytes(b'preserve existing')
            with self.subTest(existing=path.name), self.assertRaises(AssertionError): self.verify()
            path.unlink(); self.process.assert_not_called()
        self.process.return_value.stdout = b'loaded\n'
        with self.assertRaises(AssertionError): self.verify()

    def test_unsafe_source_symlink_hardlink_or_public_mode_refuses(self):
        path = self.root / 'source' / self.code['SOURCES'][0]
        path.chmod(0o644)
        with self.assertRaises(AssertionError): self.verify()
        path.chmod(0o600); alias = path.with_name('owned-hardlink'); os.link(path, alias)
        with self.assertRaises(AssertionError): self.verify()
        alias.unlink(); path.rename(alias); path.symlink_to(alias)
        with self.assertRaises(AssertionError): self.verify()
        self.process.assert_not_called()

    def test_default_boundary_precedes_only_mutation_and_preflight_has_no_apply(self):
        self.assertEqual(PLAY['vars'], {'web_engine_launch_action': 'check'})
        boundary = next(i for i, task in enumerate(TASKS) if task.get('ansible.builtin.meta') == 'end_play')
        self.assertEqual(boundary, 3)
        self.assertNotIn('--apply', TASKS[2]['ansible.builtin.command']['argv'])
        env = Environment(undefined=StrictUndefined)
        condition = env.compile_expression(TASKS[boundary]['when'])
        for action, check_mode, expected in (('check', False, True), ('start', True, True), ('start', False, False)):
            self.assertEqual(condition(web_engine_launch_action=action, ansible_check_mode=check_mode), expected)
        self.assertEqual(len(TASKS), 5)

    def test_durable_command_exact_controller_and_namespace_without_archive_size_cap(self):
        argv = TASKS[-1]['ansible.builtin.command']['argv']
        for item in ('--property=Type=exec', '--property=Restart=no', '--property=RuntimeMaxSec=5400',
                     '--property=KillMode=control-group', '--property=UMask=0077', '--property=LimitFSIZE=infinity'):
            self.assertIn(item, argv)
        self.assertFalse(any(any(name in item for name in ('ProtectSystem', 'PrivateTmp', 'PrivateNetwork', 'ReadWritePaths')) for item in argv))
        self.assertNotIn('--wait', argv); self.assertEqual(argv[-1], '--apply')
        self.assertEqual(argv[argv.index('/usr/bin/env') + 1], '-i')
        self.assertIn('/root/.local/security-web-engine-20261023/source/deploy/maintenance/web_engine_controller.py', argv)
        self.assertEqual(TASKS[-1]['timeout'], 30)

    def test_start_only_not_before_0430_enforces_ninety_minute_outer_window(self):
        class Clock(datetime):
            current = datetime(2026, 10, 24, 4, 29, 59, tzinfo=timezone.utc)
            @classmethod
            def now(cls, tz=None): return cls.current
        self.code['datetime'] = Clock
        self.code['admit_start']('check')
        with self.assertRaises(AssertionError): self.code['admit_start']('start')
        for value in ((4, 30, 0), (4, 55, 0)):
            Clock.current = datetime(2026, 10, 24, *value, tzinfo=timezone.utc)
            self.code['admit_start']('start')
        env = Environment(undefined=StrictUndefined)
        for action, mode, expected in (('check', False, 'check'), ('start', True, 'check'), ('start', False, 'start')):
            self.assertEqual(env.from_string(_ARGV[-1]).render(web_engine_launch_action=action, ansible_check_mode=mode), expected)


if __name__ == '__main__': unittest.main()
