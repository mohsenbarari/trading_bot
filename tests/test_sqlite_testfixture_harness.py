"""Actual small GNU make/patch producer and real receipt consumer; no compiler or SQL."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch
import ast, base64, json, tempfile, unittest

if __package__:
    from . import test_prepare_sqlite_wal_ci_context as F
else:
    import test_prepare_sqlite_wal_ci_context as F

R, A = F.R, F.A
ROOT = F.MAIN
REAL_POPEN = R.subprocess.Popen

class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='owned-harness-', dir=ROOT / 'tests')))
        self.src = self.root / 'src'
        self.src.mkdir()
        fixture = F.fixture_harness()
        self.before = R.testfixture_decode(fixture['generated_before'], 131072)
        for name in ('Makefile', 'Makefile.in'):
            (self.src / name).write_bytes(self.before)
            (self.src / name).chmod(0o600)
        self.stack.enter_context(patch.object(R, 'WORK', self.root))
        for name in ('subprocess.Popen', 'subprocess.run', 'socket.socket', 'urllib.request.urlopen', 'os.system', 'os.popen'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no ambient operation')))

    def actual(self):
        original = R.bounded_command
        calls = []
        def allowed(argv, **kwargs):
            self.assertIn(argv[0], ('/usr/bin/make', '/usr/bin/patch'))
            if argv[0] == '/usr/bin/make':
                self.assertIn('--dry-run', argv)
                self.assertIn('-rR', argv)
                self.assertEqual(kwargs['cwd'], self.root / 'testfixture-source-list-probes')
            calls.append(list(argv))
            return original(argv, **kwargs)
        with patch.object(R.subprocess, 'Popen', REAL_POPEN), patch.object(R, 'bounded_command', side_effect=allowed):
            receipt = R.prepare_vendor_testfixture(self.src)
        self.assertEqual(len(calls), 5)
        self.assertEqual(sum(row[0] == '/usr/bin/make' for row in calls), 4)
        return receipt

    def reject(self, receipt):
        rows, proofs = F.fixtures()
        rows['provenance.json']['vendor_tests']['testfixture_source_deduplication'] = receipt
        with self.assertRaises(RuntimeError):
            A.verify_artifacts(R, rows, proofs)

    def test_real_make_both_modes_and_patch_preserve_exact_other_bytes(self):
        receipt = self.actual()
        after = (self.src / 'Makefile').read_bytes()
        self.assertEqual(after, self.before.replace(R.TESTFIXTURE_ASSIGNMENT_BEFORE, R.TESTFIXTURE_ASSIGNMENT_AFTER))
        self.assertEqual((self.src / 'Makefile.in').read_bytes(), self.before)
        self.assertEqual(R.testfixture_harness_contract(receipt), receipt)
        for mode in (0, 1):
            pair = receipt['gnu_make'][str(mode)]
            old = R.testfixture_words(R.testfixture_decode(pair['before']['stdout'], 65536))
            new = R.testfixture_words(R.testfixture_decode(pair['after']['stdout'], 65536))
            self.assertEqual(len(old) - len(new), 1 if mode == 0 else 0)
            self.assertEqual(new.count(b'/OWNED_SOURCE/ext/fts3/fts3_term.c'), 1)

    def test_actual_producer_receipt_reaches_real_artifact_consumer(self):
        receipt = self.actual()
        rows, proofs = F.fixtures()
        vendor = rows['provenance.json']['vendor_tests']
        vendor['testfixture_source_deduplication'] = receipt
        vendor['same_configured_source']['Makefile'] = {key: receipt['generated_after'][key] for key in ('bytes', 'sha256')}
        verdict = A.verify_artifacts(R, rows, proofs)
        self.assertTrue(verdict['native_abi_checked'])
        self.assertFalse(verdict['security_accepted'])

    def test_missing_duplicate_or_already_modified_assignment_refuses(self):
        for changed in (self.before.replace(R.TESTFIXTURE_ASSIGNMENT_BEFORE, b''),
                        self.before + R.TESTFIXTURE_ASSIGNMENT_BEFORE,
                        self.before.replace(R.TESTFIXTURE_ASSIGNMENT_BEFORE, R.TESTFIXTURE_ASSIGNMENT_AFTER)):
            with self.subTest(case=sha256(changed).hexdigest()):
                (self.src / 'Makefile').write_bytes(changed)
                with self.assertRaisesRegex(RuntimeError, 'testfixture_exact_one_line'):
                    R.prepare_vendor_testfixture(self.src)

    def test_authenticated_stanzas_cannot_change_source_or_flags(self):
        changed = self.before.replace(b'$(TOP)/ext/fts3/fts3_term.c', b'$(TOP)/ext/fts3/UNIT_OTHER.c', 1)
        (self.src / 'Makefile').write_bytes(changed)
        with self.assertRaisesRegex(RuntimeError, 'testfixture_vendor_stanzas'):
            R.prepare_vendor_testfixture(self.src)

    def test_patch_cannot_change_any_unrelated_generated_byte(self):
        original = R.command
        def extra(argv, **kwargs):
            original(argv, **kwargs)
            with (self.src / 'Makefile').open('ab') as stream:
                stream.write(b'\nCFLAGS += UNIT_UNADMITTED\n')
        with patch.object(R.subprocess, 'Popen', REAL_POPEN), patch.object(R, 'command', side_effect=extra):
            with self.assertRaisesRegex(RuntimeError, 'testfixture_exact_one_line'):
                R.prepare_vendor_testfixture(self.src)

    def test_consumer_requires_bodies_scope_patch_and_current_generated_binding(self):
        original = F.fixture_harness()
        changes = []
        for key in ('generated_before', 'generated_after', 'gnu_make', 'local_patch'):
            row = deepcopy(original)
            del row[key]
            changes.append(row)
        for key, value in (('official_or_production_fix', True), ('test_selection_changed', True), ('phase', 'BEFORE_PRODUCTION_BUILD')):
            row = deepcopy(original)
            row[key] = value
            changes.append(row)
        for row in changes:
            with self.subTest(keys=sorted(row)):
                self.reject(row)
        rows, proofs = F.fixtures()
        rows['provenance.json']['vendor_tests']['same_configured_source']['Makefile']['sha256'] = '0' * 64
        with self.assertRaisesRegex(RuntimeError, 'testfixture_generated_source_changed'):
            A.verify_artifacts(R, rows, proofs)

    def test_consumer_verifies_exact_body_delta_and_raw_probe_output(self):
        for kind in ('other-byte', 'word-order', 'probe-code', 'probe-program', 'missing-mode'):
            row = F.fixture_harness()
            if kind == 'other-byte':
                body = R.testfixture_decode(row['generated_after'], 131072) + b'\nUNIT_UNRELATED=1\n'
                row['generated_after'] = R.testfixture_blob(body)
            elif kind == 'word-order':
                raw = R.testfixture_decode(row['gnu_make']['0']['before']['stdout'], 65536)
                prefix, tail = raw.split(b'=', 1)
                words, ending = tail.split(b'\n', 1)
                row['gnu_make']['0']['before']['stdout'] = R.testfixture_blob(prefix + b'=' + b' '.join(reversed(words.split())) + b'\n' + ending)
            elif kind == 'probe-code':
                row['gnu_make']['0']['after']['actual_returncode'] = False
            elif kind == 'probe-program':
                row['gnu_make']['1']['before']['program']['sha256'] = '0' * 64
            else:
                del row['gnu_make']['1']
            with self.subTest(kind=kind):
                self.reject(row)

    def test_test_selection_filter_scope_is_explicit_and_mandatory(self):
        for value in (None, 'all_source_lists_unchanged', False):
            rows, proofs = F.fixtures()
            rows['provenance.json']['vendor_tests']['no_new_filters_or_exclusions_scope'] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                A.verify_artifacts(R, rows, proofs)

    def test_build_call_order_keeps_production_build_before_local_test_change(self):
        tree = ast.parse((ROOT / 'deploy/security/sqlite-wal-backport/sourcebuild.py').read_bytes())
        body = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'build')
        calls = [node for node in ast.walk(body) if isinstance(node, ast.Call)]
        dedup = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'prepare_vendor_testfixture')
        vendor = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'vendor_tests')
        build = next(node for node in calls if isinstance(node.func, ast.Name) and node.func.id == 'command' and node.args and isinstance(node.args[0], ast.List) and any(isinstance(value, ast.Constant) and value.value == '/usr/bin/dpkg-buildpackage' for value in node.args[0].elts))
        self.assertLess(build.lineno, dedup.lineno)
        self.assertLess(dedup.lineno, vendor.lineno)
        self.assertEqual(R.VENDOR_COMMANDS['vendor-standard.log'], ['/usr/bin/make', 'test'])
        self.assertEqual(R.VENDOR_COMMANDS['vendor-fts5-regression.log'], ['./testfixture', 'ext/fts5/test/fts5prefix2.test'])
        self.assertEqual(R.VENDOR_HELPERS['Makefile.in'], (53067, '28c0cd79217d77c89c0ca3ccd39ed3937285f006384f36737de26a1db4bdc7b7'))

if __name__ == '__main__':
    unittest.main()
