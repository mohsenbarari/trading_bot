"""Hermetic execution of the finite scanner verifier, never real cache paths."""
import ast
from contextlib import redirect_stdout
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml


REPO = Path(__file__).resolve().parents[1]
PLAY = REPO / 'deploy/maintenance/scanner_cache_relocation.yml'


class ScannerRelocationTests(unittest.TestCase):
    def setUp(self):
        self.play = yaml.safe_load(PLAY.read_text())[0]
        self.code = self.play['vars']['scanner_verify']
        self.original = self.play['vars']['scanner_files']
        temporary = tempfile.TemporaryDirectory(prefix='scanner-relocation-fixture-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.source, self.destination = self.base / 'source', self.base / 'destination'
        self.entries = deepcopy(self.original)
        replacements = {
            self.play['vars']['scanner_source']: str(self.source),
            self.play['vars']['scanner_destination']: str(self.destination),
        }
        for index, entry in enumerate(self.entries):
            data = ('public fixture ' + str(index)).encode()
            replacements[entry['size']] = len(data)
            replacements[entry['sha256']] = sha256(data).hexdigest()
            entry.update(size=len(data), sha256=sha256(data).hexdigest())
            for root in (self.source, self.destination):
                path = root / entry['name']
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o600)

        class FixtureLiterals(ast.NodeTransformer):
            def visit_Constant(self, node):
                if isinstance(node.value, (str, int)) and node.value in replacements:
                    return ast.copy_location(ast.Constant(replacements[node.value]), node)
                return node

            def visit_Attribute(self, node):
                if node.attr == 'parents':
                    return ast.copy_location(ast.Call(ast.Name('_fixture_parents', ast.Load()), [node.value], []), node)
                return self.generic_visit(node)

            def visit_Compare(self, node):
                # Simulate production root-owned files with the test runner's
                # own UID; the original production root-only check is asserted
                # separately. No real host ownership is changed.
                if (isinstance(node.left, ast.Attribute) and node.left.attr == 'st_uid'
                        and len(node.ops) == 1 and isinstance(node.ops[0], ast.Eq)
                        and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value == 0):
                    node.ops = [ast.In()]
                    node.comparators = [ast.Tuple([ast.Constant(0), ast.Constant(os.geteuid())], ast.Load())]
                return self.generic_visit(node)

        self.program = compile(ast.fix_missing_locations(FixtureLiterals().visit(ast.parse(self.code))), str(PLAY), 'exec')

    def run_verifier(self, root=None, mode='source', entries=None):
        def parents(path):
            return tuple(p for p in path.parents if p == self.base or self.base in p.parents)
        arguments = ['scanner_verify', str(root or self.source), json.dumps(entries or self.entries), mode]
        with patch('sys.argv', arguments), redirect_stdout(io.StringIO()) as output:
            exec(self.program, {'_fixture_parents': parents})
        return json.loads(output.getvalue())

    def test_readonly_source_checks_all_exact_files_without_writing(self):
        with patch('os.unlink') as unlink, patch('os.fsync') as sync:
            self.assertEqual(self.run_verifier(), {'status': 'verified', 'files': 3, 'mode': 'source'})
            unlink.assert_not_called(); sync.assert_not_called()

    def test_full_digest_triples_are_hard_pinned_before_open(self):
        bad = deepcopy(self.entries); bad[0]['sha256'] = '0' * 64
        with patch('os.open') as opened, self.assertRaises(AssertionError):
            self.run_verifier(entries=bad)
        opened.assert_not_called()
        expected = [(v['name'], v['size'], v['sha256']) for v in self.original]
        self.assertEqual(len(expected), 3)
        for name, size, digest in expected:
            self.assertIn(repr(name), self.code)
            self.assertIn(str(size), self.code)
            self.assertIn(repr(digest), self.code)
        self.assertIn('before.st_uid==0', self.code)
        self.assertIn('st.st_uid==0', self.code)

    def test_vendor_public_modes_allowed_only_on_public_source(self):
        for index, entry in enumerate(self.entries):
            (self.source / entry['name']).chmod(0o755 if index == 0 else 0o644)
        self.assertEqual(self.run_verifier()['status'], 'verified')
        (self.destination / self.entries[0]['name']).chmod(0o755)
        with self.assertRaises(AssertionError):
            self.run_verifier(self.destination, 'fsync')
        (self.source / self.entries[0]['name']).chmod(0o777)
        with patch('os.unlink') as unlink, self.assertRaises(AssertionError):
            self.run_verifier(mode='cleanup')
        unlink.assert_not_called()

    def test_corrupt_last_file_prevents_every_source_unlink(self):
        (self.source / self.entries[-1]['name']).write_bytes(b'wrong')
        with patch('os.unlink') as unlink, self.assertRaises(AssertionError):
            self.run_verifier(mode='cleanup')
        unlink.assert_not_called()
        self.assertTrue(all((self.source / e['name']).exists() for e in self.entries))

    def test_cleanup_unlinks_only_three_files_and_keeps_reports_and_directories(self):
        report = self.source / 'findings.json'; report.write_text('keep provenance')
        self.assertEqual(self.run_verifier(mode='cleanup')['mode'], 'cleanup')
        self.assertTrue(report.exists())
        self.assertTrue((self.source / 'cache/db').is_dir())
        self.assertFalse(any((self.source / e['name']).exists() for e in self.entries))
        self.assertTrue(all((self.destination / e['name']).is_file() for e in self.entries))

    def test_destination_never_accepts_cleanup(self):
        with patch('os.unlink') as unlink, self.assertRaises(AssertionError):
            self.run_verifier(self.destination, 'cleanup')
        unlink.assert_not_called()

    def test_replacement_between_verification_and_unlink_is_refused(self):
        first = self.source / self.entries[0]['name']
        retained = self.source / 'retained-original'
        original_lstat = Path.lstat
        calls = 0

        def replaced(path, *args, **kwargs):
            nonlocal calls
            if path == first:
                calls += 1
                if calls == 2:  # Full verification passed; cleanup is about to unlink.
                    first.rename(retained)
                    first.mkdir(mode=0o700)
            return original_lstat(path, *args, **kwargs)

        with patch.object(Path, 'lstat', new=replaced), patch('os.unlink') as unlink:
            with self.assertRaises(AssertionError):
                self.run_verifier(mode='cleanup')
            unlink.assert_not_called()
        self.assertTrue(first.is_dir())
        self.assertTrue(retained.is_file())
        self.assertTrue(all((self.source / e['name']).is_file() for e in self.entries[1:]))

    def test_symlink_hardlink_and_directory_replacements_are_not_removed(self):
        first = self.source / self.entries[0]['name']
        original = first.read_bytes(); first.unlink()
        outside = self.base / 'outside'; outside.write_bytes(original); outside.chmod(0o600)
        for kind in ('symlink', 'hardlink', 'directory'):
            with self.subTest(kind=kind):
                if kind == 'symlink': first.symlink_to(outside)
                elif kind == 'hardlink': os.link(outside, first)
                else: first.mkdir(mode=0o700)
                with patch('os.unlink') as unlink, self.assertRaises(AssertionError):
                    self.run_verifier(mode='cleanup')
                unlink.assert_not_called()
                self.assertEqual(outside.read_bytes(), original)
                if first.is_dir(): first.rmdir()
                else: first.unlink()

    def test_fsync_is_actual_and_happens_for_three_files_and_four_directories(self):
        with patch('os.fsync', wraps=os.fsync) as sync:
            self.assertEqual(self.run_verifier(self.destination, 'fsync')['mode'], 'fsync')
        self.assertEqual(sync.call_count, 7)

    def test_default_and_check_stop_before_copy_and_delete_is_not_recursive(self):
        tasks = self.play['tasks']
        stop = next(i for i, t in enumerate(tasks) if t.get('ansible.builtin.meta') == 'end_play')
        copy = next(i for i, t in enumerate(tasks) if 'ansible.builtin.copy' in t)
        self.assertLess(stop, copy)
        self.assertIn('not (scanner_relocation_apply | bool) or ansible_check_mode', tasks[stop]['when'])
        self.assertFalse(self.play['vars']['scanner_relocation_apply'])
        self.assertIn('46*1024**3', tasks[stop + 1]['ansible.builtin.command']['argv'][-1])
        self.assertFalse(any(t.get('ansible.builtin.file', {}).get('state') == 'absent' for t in tasks))
        self.assertIn('os.unlink(path)', self.code)
        self.assertIn('ident(path.lstat())==identity', self.code)
        self.assertEqual(tasks[-1]['ansible.builtin.command']['argv'][-1], 'cleanup')
        self.assertEqual(tasks[-2]['ansible.builtin.command']['argv'][-1], 'fsync')


if __name__ == '__main__':
    unittest.main()
