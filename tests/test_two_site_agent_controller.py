"""Native source/queue routing; these fixtures do not activate the real Stage."""
import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from scripts.two_site_agent_controller import NativeController, hook_response, source_path
from scripts.two_site_agent_gateway import ActorBinding, ActorRole, GitSnapshot
from scripts.two_site_agent_host import ProtectedGatewayHost
from scripts.two_site_agent_transport import MODEL, NativeTransport, TransportError, digest
from tests.test_two_site_agent_gateway import (
    ASSIGNMENT, CANDIDATE, COORDINATOR, INTEGRATION_HEAD, VERIFIER, WORKER, GatewayFixture,
)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GatewayFixture()
        self.addCleanup(self.fixture.cleanup)
        f = self.fixture
        state = f.container / 'private-controller'
        state.mkdir(mode=0o700)
        self.role = ActorRole.WORKER
        self.context = f.context()
        self.fence = json.loads((f.root / '.local/two-site-refactor/orchestrator-fence.json').read_text())
        self.builder = SimpleNamespace(
            root=f.root, state=state, manifest={'workspaces': [str(f.root), str(f.stage)], 'coordinator_id': COORDINATOR},
            validate_installation=lambda: None,
            active_assignment=lambda: (self.fence, {}, f.assignment),
            actor=lambda event, assignment, fence: ActorBinding(event['conversationId'], self.role, ASSIGNMENT),
            build=lambda event, request: self.context,
            observe=lambda path: next(s for s in self.context.git_snapshots if s.worktree == path),
        )
        self.calls = []
        self.transport = NativeTransport(f.root, state, (f.root, f.stage),
                                         lambda e, r: self.controller.policy(e, r),
                                         lambda p, r: self.execute(p, r))
        self.controller = NativeController(self.builder, self.transport)
        self.event = {'conversationId': WORKER, 'stepIdx': 12, 'modelName': MODEL,
                      'workspacePaths': self.builder.manifest['workspaces'],
                      'toolCall': {'name': 'write_to_file', 'args': {
                          'TargetFile': str(f.test_path), 'CodeContent': 'def test_changed():\n    pass\n',
                          'Overwrite': True, 'IsArtifact': False, 'Description': 'assigned change'}}}

    def execute(self, plan, request):
        self.calls.append(plan)
        return {'status': 'SUCCESS'}

    def write(self):
        decision = self.controller.pre(self.event)
        Path(self.event['toolCall']['args']['TargetFile']).write_text(self.event['toolCall']['args']['CodeContent'])
        self.controller.post(self.event)
        return decision

    def test_exact_source_write_releases_only_after_verified_postcondition(self):
        self.assertEqual('allow', self.write()['decision'])
        self.assertFalse(self.controller.reservation.exists())
        receipt = self.builder.state / 'source-receipts' / (WORKER + '-12.json')
        self.assertEqual('SUCCESS', json.loads(receipt.read_text())['status'])

    def test_pending_source_write_blocks_commands_and_another_write(self):
        self.controller.pre(self.event)
        with self.assertRaises(TransportError):
            self.controller.policy(self.event, self.fixture.request('test', selectors=[str(self.fixture.test_path.relative_to(self.fixture.stage))]))
        with self.assertRaises(TransportError):
            self.controller.pre({**self.event, 'stepIdx': 13})
        self.assertTrue(self.controller.reservation.exists())

    def test_incomplete_or_unexpected_source_write_stays_reserved(self):
        self.controller.pre(self.event)
        self.fixture.test_path.write_text('partial write')
        with self.assertRaises(TransportError):
            self.controller.post(self.event)
        self.assertTrue(self.controller.reservation.exists())
        self.assertFalse(list((self.builder.state / 'source-receipts').glob('*.json')))

    def test_other_hook_cannot_release_source_reservation_before_native_write(self):
        self.controller.pre(self.event)
        read_event = copy.deepcopy(self.event)
        read_event['stepIdx'] = 13
        read_event['toolCall'] = {
            'name': 'view_file', 'args': {'AbsolutePath': str(self.fixture.test_path)},
        }
        self.assertEqual('allow', self.controller.pre(read_event)['decision'])
        self.assertTrue(self.controller.reservation.exists())
        self.fixture.test_path.write_text(self.event['toolCall']['args']['CodeContent'])
        self.controller.post(self.event)
        self.assertFalse(self.controller.reservation.exists())
        receipt = self.builder.state / 'source-receipts' / (WORKER + '-12.json')
        self.assertEqual('SUCCESS', json.loads(receipt.read_text())['status'])

    def test_other_file_or_integration_head_change_blocks_source_completion(self):
        self.controller.pre(self.event)
        self.fixture.test_path.write_text(self.event['toolCall']['args']['CodeContent'])
        self.fixture.changed_doc.write_text('unexpected second mutation')
        with self.assertRaises(TransportError):
            self.controller.post(self.event)
        self.fixture.changed_doc.write_text('evidence\n')
        self.context = replace(self.context, git_snapshots=tuple(
            replace(s, head_sha='e' * 40) if s.worktree == self.fixture.root else s
            for s in self.context.git_snapshots))
        with self.assertRaises(TransportError):
            self.controller.post(self.event)

    def test_verifier_coordinator_or_other_paths_cannot_write(self):
        for role, actor in ((ActorRole.VERIFIER, VERIFIER), (ActorRole.COORDINATOR, COORDINATOR)):
            self.role = role
            with self.assertRaises(TransportError):
                self.controller.pre({**self.event, 'conversationId': actor})
        self.role = ActorRole.WORKER
        for path in (self.fixture.root / 'tests/test_x.py', self.fixture.stage / '.env',
                     self.fixture.stage / '.agents/hooks.json', self.fixture.stage / 'scripts/two_site_agent_controller.py'):
            event = copy.deepcopy(self.event)
            event['toolCall']['args']['TargetFile'] = str(path)
            with self.assertRaises(TransportError):
                self.controller.pre(event)
        self.assertFalse(self.controller.reservation.exists())

    def test_native_identity_or_unknown_tool_cannot_be_a_permission_bypass(self):
        for name in ('run_command', 'execute_code', 'ssh', 'manage_subagents'):
            event = {**self.event, 'toolCall': {'name': name, 'args': {}}}
            self.assertEqual('deny', hook_response(self.controller, 'pre', event)['decision'])
        event = {**self.event, 'modelName': 'other'}
        self.assertEqual('deny', hook_response(self.controller, 'pre', event)['decision'])

    def test_explicit_reads_allow_source_but_not_credentials_runtime_or_root_grep(self):
        def read(path):
            return {**self.event, 'toolCall': {'name': 'view_file', 'args': {'AbsolutePath': str(path)}}}
        self.assertEqual('allow', self.controller.pre(read(self.fixture.test_path))['decision'])
        self.assertEqual('allow', self.controller.pre(read(self.fixture.root / '.local/two-site-refactor/worktrees.yaml'))['decision'])
        for relative in ('.env', '.local/secrets.json', 'data/tokens.json', 'docs/memory/archive/old.md'):
            path = self.fixture.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture private content')
            with self.assertRaises(TransportError):
                self.controller.pre(read(path))
        with self.assertRaises(TransportError):
            self.controller.pre({**self.event, 'toolCall': {'name': 'grep_search', 'args': {'SearchPath': str(self.fixture.root)}}})

    def test_source_symlink_or_traversal_is_rejected(self):
        link = self.fixture.stage / 'tests/test_symlink.py'
        link.symlink_to(self.fixture.test_path)
        with self.assertRaises(TransportError):
            source_path(self.fixture.stage, str(link))
        with self.assertRaises(TransportError):
            source_path(self.fixture.stage, str(self.fixture.stage) + '/../outside.py', must_exist=False)

    def test_native_typed_queue_executes_once_and_never_grants_shell(self):
        request = self.fixture.request('status')
        event = copy.deepcopy(self.event)
        event['toolCall']['args'] = {'TargetFile': str(self.transport.requests / (request['id'] + '.json')),
                                    'CodeContent': json.dumps(request), 'Overwrite': False}
        self.assertEqual('allow', self.controller.pre(event)['decision'])
        Path(event['toolCall']['args']['TargetFile']).write_text(json.dumps(request))
        self.controller.post(event)
        self.assertEqual(1, len(self.calls))
        with self.assertRaises(TransportError):
            self.controller.pre(event)

    def test_verifier_native_request_delivery_works_but_source_write_is_denied(self):
        self.role = ActorRole.VERIFIER
        self.fixture.set_stage_registry(
            head_sha=CANDIDATE, dirty_uncommitted_candidate=False,
            status='COMPLETE_CANDIDATE',
        )
        clean = GitSnapshot(
            self.fixture.stage, 'refactor/stage/P1-00b-gateway', CANDIDATE,
            parent_sha=self.fixture.assignment['base_sha'],
        )
        self.context = self.fixture.context(caller=VERIFIER, stage_snapshot=clean)
        selectors = ['tests/test_p1_00b_characterization_gateway.py::test_gateway']
        request = self.fixture.request('test', actor=VERIFIER, selectors=selectors)

        authority_host = ProtectedGatewayHost.__new__(ProtectedGatewayHost)

        def execute_with_authority(plan, supplied_request):
            self.calls.append(plan)
            outcome = {
                'status': 'SUCCESS',
                'pre_git': [{}, {
                    'head_sha': CANDIDATE,
                    'worktree_patch_sha256': None,
                }],
            }
            record = authority_host._authority_record(
                plan, supplied_request, outcome, self.fixture.assignment, self.fence,
            )
            return {
                **outcome,
                'authority_record': record,
                'authority_sha256': digest(record),
            }

        self.transport.execute = execute_with_authority
        event = copy.deepcopy(self.event)
        event['conversationId'] = VERIFIER
        event['toolCall']['args'] = {
            'TargetFile': str(self.transport.requests / (request['id'] + '.json')),
            'CodeContent': json.dumps(request), 'Overwrite': False,
        }
        self.assertEqual('allow', self.controller.pre(event)['decision'])
        Path(event['toolCall']['args']['TargetFile']).write_text(json.dumps(request))
        self.controller.post(event)
        self.assertEqual(1, len(self.calls))
        protected = json.loads(
            (self.builder.state / (request['id'] + '.receipt.json')).read_text()
        )
        record = protected['outcome']['authority_record']
        self.assertEqual('SUCCESS', protected['outcome']['status'])
        self.assertEqual('TEST', record['record_type'])
        self.assertEqual('VERIFIER', record['role'])
        self.assertEqual('PASS', record['decision'])
        self.assertEqual(selectors, record['selectors'])
        self.assertEqual(digest(record), protected['outcome']['authority_sha256'])
        self.assertEqual(
            protected,
            json.loads((self.transport.results / (request['id'] + '.json')).read_text()),
        )

        source_event = copy.deepcopy(event)
        source_event['stepIdx'] += 1
        source_event['toolCall']['args'] = {
            'TargetFile': str(self.fixture.test_path),
            'CodeContent': 'def verifier_edit():\n    pass\n',
            'Overwrite': True,
        }
        with self.assertRaisesRegex(TransportError, 'only the assigned Worker'):
            self.controller.pre(source_event)
        self.assertFalse(self.controller.reservation.exists())

    def test_replacement_expansion_is_rejected_before_reservation(self):
        self.fixture.test_path.write_text('a' * 100_000)
        event = copy.deepcopy(self.event)
        event['toolCall'] = {'name': 'replace_file_content', 'args': {
            'TargetFile': str(self.fixture.test_path), 'TargetContent': 'a',
            'ReplacementContent': 'b' * 1000, 'AllowMultiple': True,
        }}
        with self.assertRaises(TransportError):
            self.controller.pre(event)
        self.assertFalse(self.controller.reservation.exists())

    def test_child_handoff_only_to_exact_parent(self):
        event = copy.deepcopy(self.event)
        event['toolCall'] = {'name': 'send_message', 'args': {'Recipient': COORDINATOR, 'Message': 'candidate ready'}}
        self.assertEqual('allow', self.controller.pre(event)['decision'])
        event['toolCall']['args']['Recipient'] = VERIFIER
        with self.assertRaises(TransportError):
            self.controller.pre(event)


if __name__ == '__main__':
    unittest.main()
