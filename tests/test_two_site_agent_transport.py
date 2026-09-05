"""Native actor binding, one-use delivery and protected/public receipt tests."""
import copy
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.two_site_agent_sandbox import operation_deadline
from scripts.two_site_agent_transport import NativeTransport, TransportError, MODEL, unique_json

ACTOR = '11111111-1111-4111-8111-111111111111'
OTHER = '22222222-2222-4222-8222-222222222222'
REQUEST = '33333333-3333-4333-8333-333333333333'


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.state = base / 'source', base / 'protected'
        self.root.mkdir()
        self.state.mkdir(mode=0o700)
        self.time = 1000.0
        self.calls = []
        self.plan = {'operation': 'probe', 'fixed_argv': ['/usr/bin/pwd']}
        self.transport = NativeTransport(self.root, self.state, (self.root,),
                                         lambda event, request: self.plan,
                                         self.execute, clock=lambda: self.time)
        self.request = {'id': REQUEST, 'actor_id': ACTOR, 'operation': 'probe'}
        self.event = {'conversationId': ACTOR, 'stepIdx': 8, 'modelName': MODEL,
                      'workspacePaths': [str(self.root)], 'toolCall': {'name': 'write_to_file', 'args': {
                          'TargetFile': str(self.transport.requests / (REQUEST + '.json')),
                          'CodeContent': json.dumps(self.request), 'Overwrite': False,
                          'Description': 'typed request', 'toolAction': 'create', 'toolSummary': 'probe'}}}

    def execute(self, plan, request):
        self.calls.append((copy.deepcopy(plan), copy.deepcopy(request)))
        return {'status': 'SUCCESS', 'returncode': 0}

    def deliver(self):
        decision = self.transport.pre(self.event)
        Path(self.event['toolCall']['args']['TargetFile']).write_text(self.event['toolCall']['args']['CodeContent'])
        return decision, self.transport.post(self.event)

    def test_success_native_identity_one_use_and_protected_receipt(self):
        decision, _ = self.deliver()
        self.assertEqual('allow', decision['decision'])
        self.assertNotIn('permissionOverrides', decision)
        self.assertEqual(1, len(self.calls))
        protected = (self.state / (REQUEST + '.receipt.json')).read_bytes()
        public = self.transport.results / (REQUEST + '.json')
        self.assertEqual(protected, public.read_bytes())
        public.write_text('{"forged":true}')
        self.assertEqual(protected, (self.state / (REQUEST + '.receipt.json')).read_bytes())
        with self.assertRaises(TransportError):
            self.transport.pre(self.event)
        with self.assertRaises(TransportError):
            self.transport.post(self.event)
        self.assertEqual(1, len(self.calls))

    def test_spoofed_actor_model_workspace_or_path_never_issue_permit(self):
        for field, value in [('conversationId', OTHER), ('modelName', 'other'),
                             ('workspacePaths', ['/']), ('stepIdx', True)]:
            event = copy.deepcopy(self.event)
            event[field] = value
            with self.subTest(field=field), self.assertRaises(TransportError):
                self.transport.pre(event)
        event = copy.deepcopy(self.event)
        event['toolCall']['args']['TargetFile'] = str(self.transport.requests / '../outside.json')
        with self.assertRaises(TransportError):
            self.transport.pre(event)
        self.assertFalse(list(self.state.glob('*.permit.json')))
        self.assertFalse(self.calls)

    def test_cli_optional_false_artifact_and_bounded_descriptions(self):
        for artifact in (True, 'false', 0):
            event = copy.deepcopy(self.event)
            event['toolCall']['args']['IsArtifact'] = artifact
            with self.assertRaises(TransportError):
                self.transport.pre(event)
        self.event['toolCall']['args']['IsArtifact'] = False
        self.deliver()

    def test_duplicate_json_keys_unknown_args_and_oversized_requests(self):
        for body in ('{"id":"a","id":"b"}', '[1]', '{"a":NaN}', ' ' * 65537):
            event = copy.deepcopy(self.event)
            event['toolCall']['args']['CodeContent'] = body
            with self.assertRaises(TransportError):
                self.transport.pre(event)
        self.event['toolCall']['args']['UnexpectedExecutionOption'] = True
        with self.assertRaises(TransportError):
            self.transport.pre(self.event)

    def test_write_failure_post_identity_drift_or_expiry_never_execute(self):
        self.transport.pre(self.event)
        Path(self.event['toolCall']['args']['TargetFile']).write_text(json.dumps(self.request))
        for changes in ({'error': 'write failed'}, {'stepIdx': 9}, {'conversationId': OTHER}):
            event = {**copy.deepcopy(self.event), **changes}
            with self.assertRaises(TransportError):
                self.transport.post(event)
        self.time += 121
        with self.assertRaises(TransportError):
            self.transport.post(self.event)
        self.assertFalse(self.calls)

    def test_content_and_authority_drift_never_execute(self):
        self.transport.pre(self.event)
        path = Path(self.event['toolCall']['args']['TargetFile'])
        path.write_text(json.dumps({**self.request, 'operation': 'different'}))
        with self.assertRaises(TransportError):
            self.transport.post(self.event)
        path.write_text(json.dumps(self.request))
        self.plan = {'operation': 'changed'}
        with self.assertRaises(TransportError):
            self.transport.post(self.event)
        self.assertFalse(self.calls)

    def test_symlinks_cannot_redirect_request_or_result(self):
        destination = self.root / 'unrelated'
        destination.write_text('preserve me')
        path = Path(self.event['toolCall']['args']['TargetFile'])
        path.symlink_to(destination)
        with self.assertRaises(TransportError):
            self.transport.pre(self.event)
        path.unlink()
        self.transport.pre(self.event)
        path.symlink_to(destination)
        with self.assertRaises(TransportError):
            self.transport.post(self.event)
        self.assertEqual('preserve me', destination.read_text())
        self.assertFalse(self.calls)

    def test_interrupted_executor_never_retries_or_claims_success(self):
        def fail(plan, request):
            self.calls.append('effect may have happened')
            raise RuntimeError('private diagnostic never leaked')
        self.transport.execute = fail
        self.deliver()
        receipt = json.loads((self.state / (REQUEST + '.receipt.json')).read_text())
        self.assertEqual('INDETERMINATE', receipt['outcome']['status'])
        self.assertNotIn('private diagnostic', json.dumps(receipt))
        with self.assertRaises(TransportError):
            self.transport.post(self.event)
        replacement = '55555555-5555-4555-8555-555555555555'
        fresh = copy.deepcopy(self.event)
        fresh['stepIdx'] += 1
        fresh['toolCall']['args']['TargetFile'] = str(self.transport.requests / (replacement + '.json'))
        fresh['toolCall']['args']['CodeContent'] = json.dumps({**self.request, 'id': replacement})
        with self.assertRaisesRegex(TransportError, 'indeterminate'):
            self.transport.pre(fresh)
        self.assertEqual(1, len(self.calls))

    def test_end_to_end_deadline_records_indeterminate_without_authority(self):
        def slow(_plan, _request):
            time.sleep(0.25)
            return {'status': 'SUCCESS', 'authority_sha256': 'a' * 64}

        self.transport.execute = slow
        self.transport.pre(self.event)
        Path(self.event['toolCall']['args']['TargetFile']).write_text(
            self.event['toolCall']['args']['CodeContent']
        )
        with operation_deadline(0.05):
            self.transport.post(self.event)
        receipt = json.loads((self.state / (REQUEST + '.receipt.json')).read_text())
        self.assertEqual('INDETERMINATE', receipt['outcome']['status'])
        self.assertEqual('EndToEndDeadlineExceeded', receipt['outcome']['error_class'])
        self.assertNotIn('authority_sha256', receipt['outcome'])
        self.assertEqual(
            receipt,
            json.loads((self.transport.results / (REQUEST + '.json')).read_text()),
        )

    def test_running_without_receipt_freezes_other_operations(self):
        previous = '55555555-5555-4555-8555-555555555555'
        (self.state / (previous + '.running.json')).write_text('{}')
        with self.assertRaisesRegex(TransportError, 'interrupted'):
            self.transport.pre(self.event)
        self.assertFalse(self.calls)

    def test_exact_root_reconciliation_unfreezes_without_creating_authority(self):
        def fail(plan, request):
            raise RuntimeError('interrupted')
        self.transport.execute = fail
        self.deliver()
        running = self.state / (REQUEST + '.running.json')
        receipt = self.state / (REQUEST + '.receipt.json')
        reconciliation = {
            'schema_version': '1.0', 'request_id': REQUEST, 'operation': 'probe',
            'decision': 'RETRY_SAFE_NO_AUTHORITY',
            'running_sha256': hashlib.sha256(running.read_bytes()).hexdigest(),
            'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest(),
            'reconciled_at': 1001.0, 'authority': 'PROTECTED_ROOT_RECOVERY',
        }
        (self.state / (REQUEST + '.reconciliation.json')).write_text(json.dumps(reconciliation))
        replacement = '55555555-5555-4555-8555-555555555555'
        fresh = copy.deepcopy(self.event)
        fresh['stepIdx'] += 1
        fresh['toolCall']['args']['TargetFile'] = str(self.transport.requests / (replacement + '.json'))
        fresh['toolCall']['args']['CodeContent'] = json.dumps({**self.request, 'id': replacement})
        self.assertEqual('allow', self.transport.pre(fresh)['decision'])
        self.assertNotIn('authority_sha256', reconciliation)

    def test_reconciliation_is_bound_to_exact_running_and_receipt_bytes(self):
        previous = '55555555-5555-4555-8555-555555555555'
        running = self.state / (previous + '.running.json')
        running.write_text('{}')
        record = {
            'schema_version': '1.0', 'request_id': previous, 'operation': 'status',
            'decision': 'RETRY_SAFE_NO_AUTHORITY',
            'running_sha256': '0' * 64, 'receipt_sha256': None,
            'reconciled_at': 1000.0, 'authority': 'PROTECTED_ROOT_RECOVERY',
        }
        (self.state / (previous + '.reconciliation.json')).write_text(json.dumps(record))
        with self.assertRaisesRegex(TransportError, 'exact protected reconciliation'):
            self.transport.pre(self.event)

    def test_closed_assignment_receipts_compact_to_digest_index(self):
        self.request['assignment'] = 'WA-P1-00-OLD-001'
        self.event['toolCall']['args']['CodeContent'] = json.dumps(self.request)
        self.deliver()
        assignment = self.root / '.local/two-site-refactor/assignments/WA-P1-00-OLD-001.md'
        assignment.parent.mkdir(parents=True, exist_ok=True)
        assignment.write_text('```yaml\nstage_id: P1-00\n```\n')
        durable = self.root / 'docs/refactor/two-server/reviews/P1-00-WA-P1-00-OLD-001.json'
        durable.parent.mkdir(parents=True, exist_ok=True)
        durable.write_text('{"accepted":true}\n')
        order = self.root / 'docs/refactor/two-server/execution-order.yaml'
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text('stages:\n  P1-00: {state: COMPLETE, section: 1}\n')
        public = self.transport.results / (REQUEST + '.json')
        self.transport.compact_completed('WA-P1-01-ACTIVE-001')
        self.assertFalse((self.state / (REQUEST + '.receipt.json')).exists())
        self.assertFalse((self.state / (REQUEST + '.running.json')).exists())
        self.assertTrue(public.exists())
        archive = json.loads((self.state / 'archive/WA-P1-00-OLD-001.json').read_text())
        self.assertEqual(REQUEST, archive['entries'][0]['request_id'])
        self.assertRegex(archive['entries'][0]['protected_receipt_sha256'], r'^[0-9a-f]{64}$')

    def test_aggregate_protected_state_budget_rejects_admission(self):
        with patch('scripts.two_site_agent_transport.MAX_STATE_FILES', 2):
            (self.state / 'one.json').write_text('{}')
            with self.assertRaisesRegex(TransportError, 'file budget'):
                self.transport.pre(self.event)
        with patch('scripts.two_site_agent_transport.MAX_STATE_BYTES', 100):
            with self.assertRaisesRegex(TransportError, 'byte budget'):
                self.transport.pre(self.event)

    def test_protected_state_cannot_be_in_workspace_or_world_accessible(self):
        with self.assertRaises(TransportError):
            NativeTransport(self.root, self.root, (self.root,), None, None)
        self.state.chmod(0o755)
        with self.assertRaises(TransportError):
            NativeTransport(self.root, self.state, (self.root,), None, None)


if __name__ == '__main__':
    unittest.main()
