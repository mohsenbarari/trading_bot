"""Receiver scheduler regression tests; all data and credentials are synthetic."""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import ssl
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from core.market_intelligence.coin_rate_engine import COIN_RATE_ENGINE_VERSION
from core.market_intelligence.private_market_transport import (
    HEALTH_PATH, SNAPSHOT_CURRENT_PATH, SNAPSHOT_PATH, client_tls_context,
    encode_document, signed_headers,
)
from core.market_intelligence.private_pipeline_contracts import (
    ESTIMATOR_RATE_GRID_V1, EstimatorSnapshotV2, content_hash, estimator_snapshot_id,
)

from core.market_intelligence import estimator_snapshot_receiver_service as service


def wait_until(predicate, seconds=4.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('bounded test condition not reached')


def snapshot(version):
    document = {
        'contract': 'estimator_snapshot/2.0', 'snapshot_version': version,
        'generated_at_utc': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'input_snapshot_hash': content_hash([]), 'model_version': COIN_RATE_ENGINE_VERSION,
        'feed_mode': 'PRIVATE_SHADOW', 'status': 'SAFE_NO_DATA',
        'rates': [dict(instrument=instrument, settlement=settlement, status='NO_DATA',
                       value=None, unit='PROJECT_THOUSAND_TOMAN', lower_bound=None,
                       upper_bound=None, confidence='NONE', method='NO_DATA',
                       reason_code='NO_TEST_INPUT', underlying_source=None,
                       underlying_age_seconds=None, anchor_age_seconds=None,
                       market_regime='UNKNOWN')
                  for instrument, settlement in ESTIMATOR_RATE_GRID_V1],
        'health': [], 'inputs': [], 'reason_codes': ['NO_TEST_INPUT'],
    }
    # Include every defaulted field before binding the immutable identity.
    document['snapshot_id'] = estimator_snapshot_id(document)
    return EstimatorSnapshotV2.model_validate(document).model_dump(mode='json')


def make_pki(root):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'receiver-test-only-ca')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .sign(ca_key, hashes.SHA256()))
    (root/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for role, usage in [('server', ExtendedKeyUsageOID.SERVER_AUTH), ('client', ExtendedKeyUsageOID.CLIENT_AUTH)]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'receiver-test-only-'+role)]))
                .issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        (root/(role+'.pem')).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root/(role+'.key')).write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (root/(role+'.key')).chmod(0o600)


class RunningService:
    def __init__(self, root, *, fake=False, unblock=lambda: None):
        self.root = root
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.results = []
        self.errors = []
        self.closed = threading.Event()
        self.stack = ExitStack()
        self.fake = fake
        self.unblock = unblock
        self.server = None
        self.thread = None
        self.existing_threads = set(threading.enumerate())

    def __enter__(self):
        environment = {
            'APP_ENV_FILE': 'config/unit-test.env.example',
            'MARKET_PIPELINE_ALLOWED_PEER_IP': '127.0.0.1',
            'MARKET_PIPELINE_LISTEN_HOST': '127.0.0.1', 'MARKET_PIPELINE_LISTEN_PORT': '0',
            'MARKET_PIPELINE_SNAPSHOT_ROOT': str(self.root/'snapshots'),
            'MARKET_PIPELINE_CALIBRATION_ROOT': str(self.root/'calibration'),
        }
        original_server = service._Server
        owner = self
        if self.fake:
            class FakeServer:
                timeout = None
                def handle_request(self):
                    owner.stop.wait(.025)
                def server_close(self):
                    owner.closed.set()
            def factory(*args, **kwargs):
                self.server = FakeServer()
                self.ready.set()
                return self.server
            self.stack.enter_context(patch.object(service, 'read_key', return_value=b't'*32))
            self.stack.enter_context(patch.object(service, 'server_tls_context', return_value=object()))
        else:
            make_pki(self.root)
            (self.root/'synthetic-hmac').write_bytes(b't'*32)
            environment.update({
                'MARKET_HMAC_ACTIVE_PATH': str(self.root/'synthetic-hmac'),
                'MARKET_HMAC_PREVIOUS_PATH': str(self.root/'synthetic-hmac'),
                'MARKET_TRANSPORT_CA_PATH': str(self.root/'ca.pem'),
                'MARKET_TRANSPORT_CERT_PATH': str(self.root/'server.pem'),
                'MARKET_TRANSPORT_KEY_PATH': str(self.root/'server.key'),
            })
            self.context = client_tls_context(ca=self.root/'ca.pem', cert=self.root/'client.pem', key=self.root/'client.key')
            def factory(*args, **kwargs):
                self.server = original_server(*args, **kwargs)
                self.ready.set()
                return self.server
        self.stack.enter_context(patch.dict(os.environ, environment, clear=True))
        self.stack.enter_context(patch.object(service, '_Server', side_effect=factory))
        def run():
            try:
                self.results.append(service.run_estimator_snapshot_receiver_service(
                    role='estimator-snapshot-receiver', mode='live', release_sha='a'*40,
                    state_directory=self.root/'state', stop=self.stop))
            except BaseException as exc:
                self.errors.append(exc)
        self.thread = threading.Thread(target=run, name='receiver-test-main', daemon=False)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.unblock()
        self.thread.join(6)
        try:
            if self.thread.is_alive():
                raise AssertionError('service failed to join after test unblocked all IO')
            # Clients request Connection: close; require every fixture-created
            # request worker as well as both service owners to really retire.
            wait_until(lambda: all(t in self.existing_threads for t in threading.enumerate()), seconds=2)
            leaked = [t.name for t in threading.enumerate() if t not in self.existing_threads]
            if leaked:
                raise AssertionError('owned service threads leaked: '+repr(leaked))
        finally:
            self.stack.close()

    def request(self, path=HEALTH_PATH, *, document=None, bad_hmac=False, context=None):
        assert self.ready.wait(4), 'listener not constructed'
        connection = http.client.HTTPSConnection('127.0.0.1', self.server.server_address[1],
            context=context or self.context, timeout=3.0)
        try:
            if document is None:
                connection.request('GET', path, headers={'Connection': 'close'})
            else:
                encoded = encode_document(document)
                headers = signed_headers(key_id='active-v1', key=b't'*32, body=encoded.body,
                                         content_encoding=encoded.content_encoding, path=path)
                headers['Connection'] = 'close'
                if bad_hmac:
                    headers['X-Market-Signature'] = '0'*64
                connection.request('POST', path, body=encoded.body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()


class ReceiverAcceptHotfixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='receiver-hotfix-synthetic-')
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_real_mtls_hmac_publication_and_heartbeat_continue_while_compaction_blocks(self):
        entered, release = threading.Event(), threading.Event()
        original = service.compact_snapshot_receiver
        def blocked(connection, **kwargs):
            entered.set()
            if not release.wait(12):
                raise RuntimeError('test compaction release deadline')
            return original(connection, **kwargs)
        with patch.object(service, 'compact_snapshot_receiver', side_effect=blocked):
            try:
                with RunningService(self.root, unblock=release.set) as running:
                    self.assertTrue(entered.wait(4))
                    first_start = time.monotonic()
                    first = snapshot(1)
                    self.assertEqual(running.request(SNAPSHOT_PATH, document=first)[0], 200)
                    self.assertLess(time.monotonic()-first_start, 3.0)
                    wait_until(lambda: (self.root/'state/health.json').exists())
                    previous = json.loads((self.root/'state/health.json').read_text())['updated_at_utc']
                    # A real second producer tick at five seconds, while the same
                    # maintenance call remains blocked longer than sender timeout.
                    self.assertFalse(release.wait(5.0))
                    self.assertEqual(running.request(SNAPSHOT_PATH, document=snapshot(2))[0], 200)
                    status, current = running.request(SNAPSHOT_CURRENT_PATH)
                    self.assertEqual((status, current['snapshot_version']), (200, 2))
                    self.assertLess(current['age_seconds'], 3.0)
                    status, health = running.request()
                    self.assertEqual((status, health['snapshot_readiness']), (200, 'READY'))
                    heartbeat = json.loads((self.root/'state/health.json').read_text())
                    self.assertGreater(heartbeat['updated_at_utc'], previous)
                    self.assertEqual(running.request(SNAPSHOT_PATH, document=snapshot(3), bad_hmac=True)[0], 401)
                    running.server.allowed_peer_ip = '127.0.0.2'
                    self.assertEqual(running.request(SNAPSHOT_PATH, document=snapshot(3))[0], 403)
                    running.server.allowed_peer_ip = '127.0.0.1'
                    anonymous = ssl.create_default_context(cafile=str(self.root/'ca.pem'))
                    with self.assertRaises((ssl.SSLError, OSError, http.client.HTTPException)):
                        running.request(context=anonymous)
                    running.stop.set()
                    running.thread.join(.35)
                    self.assertTrue(running.thread.is_alive(), 'blocked maintenance must actually join')
                    self.assertEqual(running.results, [])
                    release.set()
                self.assertEqual(running.errors, [])
                self.assertEqual(running.results, [0])
            finally:
                release.set()

    def test_slow_health_observation_does_not_block_real_publication_or_retimestamp_itself(self):
        entered, release = threading.Event(), threading.Event()
        captured = []
        original = service.snapshot_receiver_metrics
        def blocked(connection, **kwargs):
            if threading.current_thread().name == 'receiver-test-main' and not captured:
                captured.append(kwargs['now_utc'])
                entered.set()
                if not release.wait(8):
                    raise RuntimeError('test health release deadline')
            return original(connection, **kwargs)
        with patch.object(service, 'snapshot_receiver_metrics', side_effect=blocked):
            try:
                with RunningService(self.root, unblock=release.set) as running:
                    self.assertTrue(entered.wait(4))
                    self.assertEqual(running.request(SNAPSHOT_PATH, document=snapshot(1))[0], 200)
                    self.assertEqual(running.request(SNAPSHOT_CURRENT_PATH)[1]['snapshot_version'], 1)
                    self.assertFalse((self.root/'state/health.json').exists())
                    running.stop.set()
                    release.set()
                health = json.loads((self.root/'state/health.json').read_text())
                stamp = datetime.fromisoformat(health['updated_at_utc'].replace('Z', '+00:00'))
                self.assertEqual(stamp, captured[0])
                self.assertEqual(running.errors, [])
            finally:
                release.set()

    def test_background_maintenance_failure_propagates_and_all_owners_join(self):
        failure = OSError('synthetic-maintenance-failure')
        with patch.object(service, 'compact_snapshot_receiver', side_effect=failure):
            with RunningService(self.root, fake=True) as running:
                running.thread.join(4)
                self.assertFalse(running.thread.is_alive())
            self.assertEqual(running.results, [])
            self.assertIsInstance(running.errors[0], service.EstimatorSnapshotReceiverServiceError)
            self.assertIs(running.errors[0].__cause__, failure)
            self.assertTrue(running.closed.is_set())

    def test_accept_failure_propagates_and_all_owners_join(self):
        failure = OSError('synthetic-accept-failure')
        with RunningService(self.root, fake=True) as running:
            self.assertTrue(running.ready.wait(4))
            running.server.handle_request = lambda: (_ for _ in ()).throw(failure)
            running.thread.join(4)
            self.assertFalse(running.thread.is_alive())
        self.assertEqual(running.results, [])
        self.assertIs(running.errors[0].__cause__, failure)
        self.assertTrue(running.closed.is_set())

    def test_main_health_failure_propagates_and_all_owners_join(self):
        failure = OSError('synthetic-health-failure')
        with patch.object(service, 'snapshot_receiver_metrics', side_effect=failure):
            with RunningService(self.root, fake=True) as running:
                running.thread.join(4)
                self.assertFalse(running.thread.is_alive())
        self.assertEqual(running.results, [])
        self.assertEqual(running.errors, [failure])
        self.assertTrue(running.closed.is_set())

    def test_partial_thread_start_failure_joins_acceptor_and_closes_listener(self):
        original_start = threading.Thread.start
        failure = RuntimeError('synthetic-thread-start-failure')
        def start(thread):
            if thread.name == 'snapshot-receiver-maintenance':
                raise failure
            return original_start(thread)
        with patch.object(threading.Thread, 'start', start):
            with RunningService(self.root, fake=True) as running:
                running.thread.join(4)
                self.assertFalse(running.thread.is_alive())
        self.assertEqual(running.errors, [failure])
        self.assertEqual(running.results, [])
        self.assertTrue(running.closed.is_set())

    def test_sqlite_connections_are_closed_by_their_creating_thread(self):
        owners, closed = [], []
        lock = threading.Lock()
        original = service.connect_snapshot_receiver
        class Connection:
            def __init__(self, real):
                self.real = real
                self.owner = threading.get_ident()
                with lock:
                    owners.append((id(self), self.owner))
            def __getattr__(self, name):
                if threading.get_ident() != self.owner:
                    raise AssertionError('SQLite connection crossed thread ownership')
                return getattr(self.real, name)
            def close(self):
                assert self.owner == threading.get_ident()
                self.real.close()
                with lock:
                    closed.append(id(self))
        with patch.object(service, 'connect_snapshot_receiver', side_effect=lambda path: Connection(original(path))):
            with RunningService(self.root, fake=True) as running:
                wait_until(lambda: (self.root/'state/health.json').exists())
        self.assertEqual(running.errors, [])
        self.assertEqual(len(closed), len(owners))
        self.assertEqual(sorted(closed), sorted(key for key, _owner in owners))
        self.assertGreaterEqual(len({owner for _key, owner in owners}), 2)


if __name__ == '__main__':
    unittest.main()
