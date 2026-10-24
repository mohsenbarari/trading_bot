"""No host commands, network, DSNs or database imports in source-build tests."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / 'scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = load('_psycopg2_source_test', 'prepare_psycopg2_source.py')
V = load('_psycopg2_linkage_test', 'verify_psycopg2_runtime.py')


class PsycopgSourceSecurity(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        def denied(*args, **kwargs): raise AssertionError('unexpected host command or network')
        for name in ('subprocess.run', 'subprocess.Popen', 'subprocess.call', 'subprocess.check_call',
                     'subprocess.check_output', 'os.system', 'socket.socket.connect', 'socket.getaddrinfo',
                     'socket.create_connection', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=denied))
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.psycopg-source-', dir=REPO)))
        self.cache = self.temp / 'cache'; self.cache.mkdir(mode=0o700)
        self.requirements = self.temp / 'requirements.txt'
        self.requirements.write_bytes(b'# public fixture\npsycopg2-binary==2.9.13\nother==1.0\n')
        self.requirements.chmod(0o600)
        self.archive = b'synthetic official source archive'
        self.stack.enter_context(patch.object(P, 'SIZE', len(self.archive)))
        self.stack.enter_context(patch.object(P, 'SHA256', sha256(self.archive).hexdigest()))

    def source(self):
        path = self.cache / P.FILENAME
        path.write_bytes(self.archive); path.chmod(0o600)
        return path

    def test_existing_exact_archive_admitted_and_only_one_pin_removed(self):
        self.source()
        P.prepare(self.cache, self.requirements, download=True)
        self.assertEqual((self.cache / P.FILTERED).read_bytes(), b'# public fixture\nother==1.0\n')
        P.prepare(self.cache, self.requirements)

    def test_same_distribution_cached_wheel_is_refused_before_download(self):
        for filename in ('psycopg2_binary-2.9.13-cp311-cp311-manylinux.whl', 'psycopg2-2.9.13-cp311-cp311-linux.whl'):
            path = self.cache / filename; path.touch(mode=0o600)
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                P.prepare(self.cache, self.requirements, download=True)
            path.unlink()
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_default_check_cannot_fetch_or_write(self):
        with self.assertRaises(ValueError): P.prepare(self.cache, self.requirements)
        self.assertEqual(list(self.cache.iterdir()), [])
        self.source()
        with self.assertRaises(FileNotFoundError): P.prepare(self.cache, self.requirements)
        self.assertEqual(len(list(self.cache.iterdir())), 1)

    def test_missing_duplicate_or_unpatched_requirement_refuses(self):
        for raw in (b'other==1\n', b'psycopg2-binary==2.9.9\n', b'psycopg2-binary==2.9.13\n' * 2):
            self.requirements.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                P.prepare(self.cache, self.requirements, download=True)
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_archive_digest_size_symlink_hardlink_and_fifo_are_rejected(self):
        path = self.source(); path.write_bytes(b'corrupted')
        with self.assertRaises(ValueError): P.prepare(self.cache, self.requirements, download=True)
        path.unlink(); outside = self.temp / 'outside'; outside.write_bytes(self.archive); outside.chmod(0o600)
        path.symlink_to(outside)
        with self.assertRaises(ValueError): P.prepare(self.cache, self.requirements, download=True)
        path.unlink(); os.link(outside, path)
        with self.assertRaises(ValueError): P.prepare(self.cache, self.requirements, download=True)
        path.unlink(); os.mkfifo(path, mode=0o600)
        with self.assertRaises(ValueError): P.prepare(self.cache, self.requirements, download=True)

    def test_fetch_has_early_exact_byte_wall_and_process_bounds(self):
        calls = []
        def bounded(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[0], '/usr/bin/curl')
            self.assertEqual(argv[argv.index('--max-filesize') + 1], str(len(self.archive)))
            self.assertEqual(argv[argv.index('--max-time') + 1], '60')
            self.assertEqual(argv[-1], P.URL)
            self.assertNotIn('--location', argv)
            self.assertEqual(kwargs['timeout'], 65)
            self.assertTrue(callable(kwargs['preexec_fn']))
            kwargs['stdout'].write(self.archive)
        with patch.object(P.subprocess, 'run', side_effect=bounded): P.prepare(self.cache, self.requirements, download=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual((self.cache / P.FILENAME).read_bytes(), self.archive)

    def test_oversize_or_wrong_sha_never_becomes_an_admitted_cache_file(self):
        for payload in (self.archive + b'x', b'x' * len(self.archive)):
            with self.subTest(length=len(payload)):
                def bad(argv, **kwargs): kwargs['stdout'].write(payload)
                with patch.object(P.subprocess, 'run', side_effect=bad), self.assertRaises(ValueError):
                    P.prepare(self.cache, self.requirements, download=True)
                self.assertEqual(list(self.cache.iterdir()), [])

    def test_runtime_linkage_gate_accepts_only_system_dynamic_families(self):
        def maps(paths): return '\n'.join('1-2 r--p 00000000 00:00 0 ' + path for path in paths)
        system = ['/usr/lib/x86_64-linux-gnu/libpq.so.5.15', '/usr/lib/x86_64-linux-gnu/libssl.so.3',
                  '/usr/lib/x86_64-linux-gnu/libcrypto.so.3']
        with patch.object(Path, 'resolve', lambda path, **kwargs: path):
            self.assertEqual(V.system_libraries(maps(system)), ['libcrypto', 'libpq', 'libssl'])
            for bad in (system[:2], system + ['/opt/psycopg2_binary.libs/libpq-vendor.so.5'],
                        [system[0].replace('/usr/lib/', '/opt/'), *system[1:]],
                        [system[0] + ' (deleted)', *system[1:]], [system[0], system[1].replace('.3', '.1.1'), system[2]]):
                with self.subTest(bad=bad), self.assertRaises(ValueError): V.system_libraries(maps(bad))

    def test_exact_official_source_metadata_is_pinned(self):
        self.assertEqual(P.FILENAME, 'psycopg2_binary-2.9.13.tar.gz')
        self.assertEqual(P.URL, 'https://files.pythonhosted.org/packages/ed/76/7b4383014be0fcc6c1c0e24292845a14e1672cf17fca62ca0a2bd5f4563d/' + P.FILENAME)
        source = (REPO / 'scripts/prepare_psycopg2_source.py').read_text()
        self.assertIn('SIZE = 378112', source)
        self.assertIn('e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373', source)

    def test_actual_docker_copy_targets_admit_source_without_writable_ancestor_exemptions(self):
        for name in ('Dockerfile', 'Dockerfile.iran'):
            with self.subTest(name=name):
                recipe = (REPO / name).read_text()
                target = re.search(r'^COPY pip_packages/ (/[^\s]+)/$', recipe, re.MULTILINE)
                self.assertIsNotNone(target)
                # Model the actual base-image parent permissions, not a relaxed
                # fake stat: /tmp is 1777, while /opt is owned and non-writable.
                image = self.temp / ('image-' + name)
                image.mkdir(mode=0o700)
                (image / 'tmp').mkdir(mode=0o700); (image / 'tmp').chmod(0o1777)
                (image / 'opt').mkdir(mode=0o755)
                cache = image / target[1].lstrip('/')
                cache.mkdir(mode=0o700)
                requirements = image / 'requirements.txt'
                requirements.write_bytes(self.requirements.read_bytes()); requirements.chmod(0o600)
                archive = cache / P.FILENAME
                archive.write_bytes(self.archive); archive.chmod(0o600)
                P.prepare(cache, requirements, download=True)
                P.prepare(cache, requirements)
                self.assertEqual((cache / P.FILTERED).read_bytes(), b'# public fixture\nother==1.0\n')

    def test_world_writable_cache_ancestor_stays_refused_before_any_download(self):
        parent = self.temp / 'world-writable'
        parent.mkdir(mode=0o700); parent.chmod(0o1777)
        cache = parent / 'pip_packages'; cache.mkdir(mode=0o700)
        with self.assertRaises(ValueError):
            P.prepare(cache, self.requirements, download=True)
        self.assertEqual(list(cache.iterdir()), [])

    def test_both_app_builders_use_direct_source_fresh_wheel_dir_and_compiler_free_runtime(self):
        for name in ('Dockerfile', 'Dockerfile.iran'):
            source = (REPO / name).read_text()
            with self.subTest(name=name):
                self.assertIn('3.11.17-slim-bookworm@sha256:f06d01d4', source)
                builder, runtime = source.split('FROM patched-base AS runtime')
                self.assertIn('prepare_psycopg2_source.py check', builder)
                self.assertIn('--wheel-dir=/wheelhouse /opt/pip_packages/psycopg2_binary-2.9.13.tar.gz', builder)
                self.assertIn('--no-cache-dir --no-index --no-deps --no-build-isolation', builder)
                self.assertIn('libpq-dev=$(dpkg-query', builder)
                self.assertNotIn('build-essential', runtime)
                self.assertNotIn('libpq-dev', runtime)
                self.assertIn('verify_psycopg2_runtime.py', runtime)
                self.assertIn('pip check', runtime)
        app = (REPO / 'Dockerfile').read_text()
        self.assertIn('prepare_psycopg2_source.py prepare', app)
        self.assertIn('else \\\n            python -m pip wheel --no-cache-dir --wheel-dir=/wheelhouse', app)
        iran = (REPO / 'Dockerfile.iran').read_text()
        self.assertNotIn('prepare_psycopg2_source.py prepare', iran)

    def test_existing_cache_signatures_payload_and_filtered_requirements_stay_in_sync(self):
        controller = (REPO / 'scripts/production_deploy_online.sh').read_text()
        for name in ('prepare_pip_packages', 'expected_wheel_cache_signature'):
            body = controller.split(name + '() {', 1)[1].split('\n}', 1)[0]
            self.assertIn('md5sum "$LOCAL_PROJECT_DIR/scripts/prepare_psycopg2_source.py"', body)
        verifier = controller.split('verify_prepared_wheel_cache() {', 1)[1].split('\n}', 1)[0]
        self.assertIn('prepare_psycopg2_source.py" check', verifier)
        build = controller.split('build_release() {', 1)[1].split('\n}', 1)[0]
        self.assertIn('rsync -a --delete "$iran_pip_dir/" "$iran_context_dir/pip_packages/"', build)
        self.assertNotIn("--exclude '.*'", build)
        payload = controller.split('prepare_committed_iran_source_payload() {', 1)[1].split('\n}', 1)[0]
        self.assertIn('archive --format=tar "$RELEASE_SHA"', payload)
        self.assertNotIn("--exclude 'scripts'", payload)
        self.assertNotIn('.requirements-without-psycopg2', (REPO / '.dockerignore').read_text())

    def test_market_exact_source_hash_dynamic_system_libpq_and_signatures_are_bound(self):
        lock = (REPO / 'deploy/market-data/requirements.lock').read_text()
        self.assertIn('psycopg2-binary @ ' + P.URL, lock)
        self.assertIn('sha256:e324ecf60f952d21dd11413b8bbed0951bbd99579a06fd06f28bfc37737cd373', lock)
        source = (REPO / 'deploy/market-data/Dockerfile').read_text()
        self.assertIn('ca-certificates libpq5', source)
        self.assertIn('libpq-dev=$(dpkg-query', source)
        self.assertIn('--no-binary=pyaes,psycopg2-binary', source)
        self.assertIn('--no-build-isolation', source)
        self.assertIn('--package-root /opt/market-python', source)
        controller = (REPO / 'scripts/production_deploy_online.sh').read_text()
        for body in (controller.split('market_pipeline_image_input_signature() {')[1].split('\n}')[0],
                     controller.split('prepare_market_pipeline_control_payload() {')[1].split('\n}')[0]):
            self.assertIn('scripts/verify_psycopg2_runtime.py', body)
            self.assertIn('deploy/production/pip-bootstrap-requirements.txt', body)
        self.assertIn('pip==26.2.2', (REPO / 'deploy/production/pip-bootstrap-requirements.txt').read_text())


if __name__ == '__main__': unittest.main()
