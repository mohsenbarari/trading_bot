"""Exact owned QA fixtures only; no host, network, secret read or real Docker."""
from contextlib import ExitStack, nullcontext
from datetime import datetime, timezone, timedelta
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('_web_qa_retirement_test', ROOT / 'deploy/maintenance/web_qa_retirement.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def plan_fixture():
    rows = [{'id': ident, 'name': '/' + M.PROJECT + '-' + name + '-1', 'project': M.PROJECT, 'running': True,
             'image': 'public-image', 'mounts': [], 'restart_policy': {'Name': 'unless-stopped', 'MaximumRetryCount': 0}}
            for ident, name in M.IDS.items()]
    rows += [{'id': ident, 'name': name, 'project': 'production', 'running': True}
             for ident, name in M.KNOWN_MARKET_FLAPS.items()]
    rows += [{'id': 'production-' + str(i), 'name': '/production-' + str(i), 'running': i < 14} for i in range(15)]
    stores = [ident for ident, name in M.IDS.items() if name in {'webapp_fi_db', 'webapp_fi_redis'}]
    return {'containers': rows, 'datastore_ids': stores + ['production-' + str(i) for i in range(6)], 'deadline_utc': '2026-10-24T07:00:00Z'}


class QaRetirement(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for target in ('subprocess.run', 'subprocess.Popen', 'os.system', 'socket.socket.connect',
                       'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(target, side_effect=AssertionError('hermetic only')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.qa-retirement-test-', dir=ROOT)))

    def test_exact_ten_ids_names_and_eight_app_two_store_scope(self):
        plan = plan_fixture()
        selected, stores = M.exact_scope(plan)
        self.assertEqual(len(selected), 10)
        self.assertEqual(len(stores), 2)
        self.assertTrue(all(row['id'] in M.IDS for row in selected))
        plan['containers'][0]['name'] = '/foreign-production'
        with self.assertRaisesRegex(RuntimeError, 'approved_qa_identity_changed'):
            M.exact_scope(plan)

    def test_known_health_incident_is_exact_id_and_name_bound(self):
        plan = plan_fixture()
        next(row for row in plan['containers'] if row['id'] in M.KNOWN_MARKET_FLAPS)['name'] = '/foreign'
        with self.assertRaisesRegex(RuntimeError, 'known_market_incident_identity_changed'):
            M.exact_scope(plan)

    def test_stop_policy_and_order_touch_only_approved_eight_then_two(self):
        selected, stores = M.exact_scope(plan_fixture())
        events = []
        C = SimpleNamespace(B=SimpleNamespace(command=lambda a: events.append(a) or 'false'),
            bounded=lambda a, **k: events.append(a))
        M.retire(C, selected, stores)
        updates = [a for a in events if a[1] == 'update']
        self.assertEqual({a[-1] for a in updates}, set(M.IDS))
        self.assertTrue(all(a[2] == '--restart=no' for a in updates))
        stops = [a for a in events if a[1] == 'stop']
        self.assertEqual(len(stops[0][4:]), 8)
        self.assertEqual(set(stops[1][4:]), stores)
        self.assertTrue(all(not set(a) & {'rm', 'prune', 'rmi', 'volume', 'kill'} for a in events))

    def test_stable_production_records_only_known_market_degradation(self):
        stable = {'production': ('public-start', 0)}
        known = list(M.KNOWN_MARKET_FLAPS)
        with patch.object(M, 'production', return_value=stable), patch.object(M, 'inventory', return_value=known), \
             patch.object(M.time, 'sleep') as pause:
            self.assertEqual(M.stable_production(object(), {}), known)
        self.assertEqual([call.args for call in pause.call_args_list], [(10,), (10,), (10,)])
        with patch.object(M, 'production', return_value=stable), patch.object(M, 'inventory', return_value=['foreign-new-unhealthy']), \
             patch.object(M.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'new_production_health_degradation'):
            M.stable_production(object(), {})

    def test_restart_during_retained_stability_refuses(self):
        with patch.object(M, 'production', side_effect=[{'app': ('first', 0)}, {'app': ('second', 1)}]), \
             patch.object(M.time, 'sleep'), self.assertRaisesRegex(RuntimeError, 'retained_application_restarted'):
            M.stable_production(object(), {})

    def test_all_bind_file_metadata_including_tls_is_preserved_without_reading_secrets(self):
        volumes = []
        for index in range(4):
            folder = self.root / ('volume-' + str(index)); folder.mkdir()
            volumes.append({'Type': 'volume', 'Name': 'v' + str(index), 'Source': str(folder), 'Destination': '/data/' + str(index)})
        secret = self.root / 'server.key'; secret.write_bytes(b'synthetic never read'); secret.chmod(0o600)
        actual_mounts = volumes + [{'Type': 'bind', 'Source': str(secret), 'Destination': '/run/tls/server.key'}]
        selected = [{'id': 'selected', 'image': 'public-image', 'mounts': [
            {key: value for key, value in row.items() if key != 'Name'} for row in actual_mounts]}]
        def command(a):
            if a[1] == 'image':
                return 'public-image'
            if a[1] == 'inspect':
                return json.dumps(actual_mounts)
            name = a[-1]
            return json.dumps({'name': name, 'driver': 'local', 'mountpoint': str(self.root / ('volume-' + name[1:])), 'labels': {}})
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        C = SimpleNamespace(B=SimpleNamespace(command=command, identity=identity),
            P=SimpleNamespace(canonical_mounts=lambda rows: [{key: value for key, value in row.items() if key != 'Name'} for row in rows]))
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('secret contents forbidden')):
            value = M.preserved(C, selected)
        self.assertEqual(len(value['volumes']), 4)
        self.assertIn(str(secret), value['secret_metadata'])

    def test_new_scope_clock_is_explicit_and_does_not_extend_old_plan(self):
        fixed = datetime(2026, 10, 24, 7, 2, tzinfo=timezone.utc)
        C = SimpleNamespace(datetime=SimpleNamespace(fromisoformat=datetime.fromisoformat, now=lambda tz: fixed), timezone=timezone)
        old_plan = plan_fixture()
        self.assertEqual(M.scope_remaining(C, 300).isoformat(), '2026-10-24T07:10:00+00:00')
        self.assertEqual(old_plan['deadline_utc'], '2026-10-24T07:00:00Z')
        fixed = datetime(2026, 10, 24, 7, 10, tzinfo=timezone.utc)
        with self.assertRaisesRegex(RuntimeError, 'independent_qa_scope_window_exhausted'):
            M.scope_remaining(C, 0)

    def test_private_dbus_reference_persists_without_starting_inactive_units(self):
        events = []
        manager = SimpleNamespace(RefUnit=lambda name, **k: events.append(('ref', name)),
            UnrefUnit=lambda name, **k: events.append(('unref', name)), GetUnit=lambda *a, **k: '/fixture')
        props = SimpleNamespace(Get=lambda *a, **k: 'loaded')
        bus = SimpleNamespace(get_object=lambda *a: object(), close=lambda: events.append(('close',)))
        factory = Mock(return_value=bus)
        dbus = SimpleNamespace(SystemBus=factory, Interface=lambda obj, kind: manager if kind.endswith('Manager') else props)
        with patch.dict(sys.modules, {'dbus': dbus}):
            with M.pinned(SimpleNamespace(UNITS={'inactive.service'}, ENGINES=['docker.service'])):
                self.assertEqual(len(events), 2)
        factory.assert_called_once_with(private=True)
        self.assertEqual(events, [('ref', 'docker.service'), ('ref', 'inactive.service'),
                                  ('unref', 'inactive.service'), ('unref', 'docker.service'), ('close',)])

    def test_active_hot_backup_or_leftover_restore_volume_blocks_retirement(self):
        C = SimpleNamespace(B=SimpleNamespace(command=Mock(return_value='LoadState=loaded\nActiveState=active\nMainPID=123')),
                            scope_empty=Mock(return_value=True))
        with self.assertRaisesRegex(RuntimeError, 'hot_backup_still_active'):
            M.hot_finished(C, SimpleNamespace())
        C.B.command = Mock(side_effect=['LoadState=loaded\nActiveState=failed\nMainPID=0', 'leftover-labelled-volume'])
        with self.assertRaisesRegex(RuntimeError, 'owned_restore_volume_cleanup_unverified'):
            M.hot_finished(C, SimpleNamespace())

    def fixture(self):
        marker, source, reservation = (self.root / name for name in ('marker', 'source', 'reservation'))
        for path in (marker, source, reservation):
            path.write_bytes(b'{"original":"untouched"}\n'); path.chmod(0o600)
        for suffix in ('.journal.json', '.owned-restore.json', '.owned-intent.json'):
            path = self.root / ('original' + suffix)
            path.write_bytes(b'{"failed_original":"preserve_raw"}\n'); path.chmod(0o600)
        events = []
        def write_json(path, value, **k):
            path.write_text(json.dumps(value)); path.chmod(0o600)
            events.append(('write', value['phase'], value.get('fences_release_status')))
        def release(rows):
            for path, dev, ino in reversed(rows):
                self.assertEqual((path.stat().st_dev, path.stat().st_ino), (dev, ino))
                events.append(('unlink', path.name)); path.unlink()
        plan = plan_fixture()
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        C = SimpleNamespace(ROOT=self.root, P=SimpleNamespace(MARKER=marker, RESERVATION=reservation, document=lambda *a: plan),
            B=SimpleNamespace(read=lambda p, **k: p.read_bytes(), identity=identity, command=Mock(return_value='')),
            paths=lambda p: (self.root / 'original.journal.json', source), remaining_contract=Mock(),
            datetime=datetime, timezone=timezone, timedelta=timedelta, write_json=write_json, release_owned_fences=release)
        H = SimpleNamespace(OP='original', NONCE='original-nonce', PLAN_SHA='public-digest', OLD_ROOT=self.root, owner_proof=Mock())
        H.owner_proof = self.stack.enter_context(patch.object(M, 'scope_owner_proof'))
        self.stack.enter_context(patch.object(M, 'pinned', return_value=nullcontext()))
        self.stack.enter_context(patch.object(M, 'scope_remaining', return_value=datetime.now(timezone.utc) + timedelta(minutes=10)))
        for target in ('hot_finished', 'no_backup_exec', 'production'):
            self.stack.enter_context(patch.object(M, target))
        self.stack.enter_context(patch.object(M, 'inventory', return_value=list(M.KNOWN_MARKET_FLAPS)))
        self.stack.enter_context(patch.object(M, 'preserved', return_value={'preserved': True}))
        self.stack.enter_context(patch.object(M, 'stable_production', return_value=list(M.KNOWN_MARKET_FLAPS)))
        return C, H, events, (marker, source, reservation)

    def test_default_holds_three_real_flocks_and_never_mutates_anything(self):
        C, H, events, files = self.fixture()
        original = [p.read_bytes() for p in files]
        def proof(*a):
            for path in files:
                fd = os.open(path, os.O_RDONLY)
                try:
                    with self.assertRaises(BlockingIOError):
                        M.fcntl.flock(fd, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
                finally:
                    os.close(fd)
        H.owner_proof.side_effect = proof
        with patch.object(M, 'retire') as stop:
            self.assertEqual(M.execute(C, H, 'check'), {'status': 'exact_qa_retirement_checked', 'runtime_changed': False})
        stop.assert_not_called()
        self.assertFalse(events)
        self.assertEqual(original, [p.read_bytes() for p in files])

    def test_superseding_audit_precedes_source_then_reservation_release_without_old_journal_edits(self):
        C, H, events, files = self.fixture()
        old = (self.root / 'original.journal.json').read_bytes()
        with patch.object(M, 'retire'):
            result = M.execute(C, H, 'retire')
        self.assertEqual(result['status'], 'aborted_after_authorized_qa_retirement')
        self.assertFalse(result['security_transition_completed'])
        self.assertFalse(result['all_prior_runtime_healthy'])
        self.assertEqual((self.root / 'original.journal.json').read_bytes(), old)
        self.assertTrue(files[0].exists())
        self.assertEqual([e for e in events if e[0] == 'unlink'], [('unlink', 'source'), ('unlink', 'reservation')])
        before_release = events.index(('write', 'aborted_after_authorized_qa_retirement', 'pending'))
        self.assertLess(before_release, events.index(('unlink', 'source')))
        audit = json.loads((self.root / 'original.qa-retirement.json').read_bytes())
        self.assertEqual(audit['fences_release_status'], 'released')
        self.assertEqual(len(audit['retired_ids']), 10)
        self.assertIn('cleanup_review_due_utc', audit)

    def test_refused_retirement_never_releases_fences_or_claims_terminal_success(self):
        C, H, events, files = self.fixture()
        with patch.object(M, 'retire', side_effect=RuntimeError('qa_stop_not_complete')), self.assertRaisesRegex(RuntimeError, 'qa_stop_not_complete'):
            M.execute(C, H, 'retire')
        self.assertTrue(all(p.exists() for p in files))
        self.assertFalse(any(e[0] == 'unlink' for e in events))
        audit = json.loads((self.root / 'original.qa-retirement.json').read_bytes())
        self.assertEqual(audit['phase'], 'retiring_qa')


if __name__ == '__main__':
    unittest.main()
