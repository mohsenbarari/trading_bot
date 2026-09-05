"""Disposable root-recovery classification tests."""
import json
import fcntl
import os
import tempfile
import unittest
from pathlib import Path

from scripts.two_site_agent_recovery import reconcile
from scripts.two_site_agent_transport import NativeTransport, TransportError, digest
from tests.test_two_site_agent_executor import GitFixture


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fx = GitFixture()
        self.addCleanup(self.fx.cleanup)
        self.temp = tempfile.TemporaryDirectory(prefix='two-site-recovery-')
        self.addCleanup(self.temp.cleanup)
        parent = Path(self.temp.name) / 'state'
        self.state = parent / 'controller'
        self.state.mkdir(parents=True, mode=0o700)
        self.manifest = parent / 'manifest.json'
        self.manifest.write_text(json.dumps({'root': str(self.fx.root)}))
        self.request_id = '44444444-4444-4444-8444-444444444444'
        self.requests = self.fx.root / '.local/two-site-refactor/gateway/requests'
        self.requests.mkdir(parents=True)

    def write_interrupted(self, operation='status'):
        request = {
            'id': self.request_id, 'operation': operation,
            'actor_id': '11111111-1111-4111-8111-111111111111',
            'assignment': 'WA-P1-00B-TEST-001', 'epoch': 1,
            'integration_head': self.fx.integration_head,
        }
        (self.requests / (self.request_id + '.json')).write_text(json.dumps(request))
        running = {
            'actor': request['actor_id'], 'step': 1, 'request_id': self.request_id,
            'request_sha256': digest(request), 'plan_sha256': 'a' * 64, 'issued_at': 1.0,
        }
        (self.state / (self.request_id + '.running.json')).write_text(json.dumps(running))

    def test_read_only_interruption_is_reconciled_without_authority(self):
        self.write_interrupted('status')
        audit = reconcile(self.request_id, apply=False, manifest_path=self.manifest)
        self.assertEqual('RETRY_SAFE', audit['status'])
        result = reconcile(self.request_id, apply=True, manifest_path=self.manifest)
        self.assertEqual('RECONCILED', result['status'])
        self.assertNotIn('authority_sha256', result)
        transport = NativeTransport(self.fx.root, self.state, (self.fx.root,), None, None)
        transport.admission_check()

    def test_ambiguous_mutation_remains_blocked(self):
        self.write_interrupted('integrate')
        result = reconcile(self.request_id, apply=True, manifest_path=self.manifest)
        self.assertEqual('BLOCKED', result['status'])
        self.assertFalse((self.state / (self.request_id + '.reconciliation.json')).exists())

    def test_recovery_cannot_race_a_live_transport_lock_holder(self):
        self.write_interrupted('status')
        lock = os.open(self.state / 'transport.lock', os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(TransportError, 'still running'):
                reconcile(self.request_id, apply=True, manifest_path=self.manifest)
        finally:
            os.close(lock)
        self.assertFalse((self.state / (self.request_id + '.reconciliation.json')).exists())


if __name__ == '__main__':
    unittest.main()
