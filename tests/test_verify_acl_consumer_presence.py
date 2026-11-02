"""Hermetic, inert ELF metadata and real temporary-file inventory checks."""
from contextlib import ExitStack
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest import mock


FENCE_TARGETS = ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output', 'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL', 'ctypes.PyDLL', 'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp', 'os.execv', 'os.execve', 'os.execvp', 'os.execvpe', 'os.kill', 'os.killpg')
FENCE_ERROR = 'ambient_execution_denied'
def fenced(stack):
    for target in FENCE_TARGETS:
        stack.enter_context(mock.patch(target, side_effect=AssertionError(FENCE_ERROR)))
    return stack

with ExitStack() as _import_fence:
    fenced(_import_fence)
    SPEC = importlib.util.spec_from_file_location('acl_consumer_presence',
            Path(__file__).resolve().parents[1] / 'scripts/verify_acl_consumer_presence.py')
    M = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(M)
IMAGE = 'sha256:0e4f132a9696569af9c138204fea381d3a7b02e43a7f845dc613eba107ef377c'


def fixture(*, kind=2, endian='<', symbols=('acl_set_file',), needed=('libacl.so.1',), defined=()):
    """An inert non-executable ELF metadata file; it is never loaded or run."""
    hfmt = endian + ('HHIIIIIHHHHHH' if kind == 1 else 'HHIQQQIHHHHHH')
    pfmt = endian + ('IIIIIIII' if kind == 1 else 'IIQQQQQQ')
    sfmt = endian + ('IIIIIIIIII' if kind == 1 else 'IIQQQQIIQQ')
    symfmt = endian + ('IIIBBH' if kind == 1 else 'IBBHQQ')
    dfmt = endian + ('iI' if kind == 1 else 'qQ')
    hs, ps, ss, ys, ds = (16 + struct.calcsize(hfmt), struct.calcsize(pfmt),
                         struct.calcsize(sfmt), struct.calcsize(symfmt), struct.calcsize(dfmt))
    strings = bytearray(b'\0')
    offsets = {}
    for name in (*needed, *symbols, *defined):
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode() + b'\0')
    dynoff = hs + ps
    stroff = dynoff + (len(needed) + 5) * ds
    symoff = stroff + len(strings)
    symraw = bytearray(ys)
    for name in (*symbols, *defined):
        shndx = 1 if name in defined else 0
        fields = (offsets[name], 0, 0, 0x12, 0, shndx) if kind == 1 else (offsets[name], 0x12, 0, shndx, 0, 0)
        symraw.extend(struct.pack(symfmt, *fields))
    shoff = symoff + len(symraw)
    base = 0x1000
    ident = b'\x7fELF' + bytes((kind, 1 if endian == '<' else 2, 1)) + bytes(9)
    header = struct.pack(hfmt, 3, 3 if kind == 1 else 62, 1, 0, hs, shoff, 0,
                         hs, ps, 1, ss, 4, 0)
    dynsize = stroff - dynoff
    ph = ((2, dynoff, base + dynoff, base + dynoff, dynsize, dynsize, 4, 4)
          if kind == 1 else (2, 4, dynoff, base + dynoff, base + dynoff, dynsize, dynsize, 8))
    dynamic = [(1, offsets[name]) for name in needed]
    dynamic += [(5, base + stroff), (10, len(strings)), (6, base + symoff), (11, ys), (0, 0)]
    sections = [(0,) * 10,
                (0, 3, 2, base + stroff, stroff, len(strings), 0, 0, 1, 0),
                (0, 11, 2, base + symoff, symoff, len(symraw), 1, 1, 8, ys),
                (0, 6, 3, base + dynoff, dynoff, dynsize, 1, 0, 8, ds)]
    return (ident + header + struct.pack(pfmt, *ph)
            + b''.join(struct.pack(dfmt, *d) for d in dynamic) + strings + symraw
            + b''.join(struct.pack(sfmt, *s) for s in sections))


class ElfTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        fenced(self.stack)

    def test_both_classes_and_orders_report_real_undefined_entries(self):
        for kind in (1, 2):
            for endian in ('<', '>'):
                with self.subTest(kind=kind, endian=endian):
                    record = M.elf_metadata(fixture(kind=kind, endian=endian,
                        symbols=('acl_set_file', 'acl_get_file_at', 'acl_get_fd'),
                        defined=('acl_delete_def_file',)))
                    self.assertEqual(record['needed'], ['libacl.so.1'])
                    self.assertEqual(record['undefined_legacy_acl'], ['acl_set_file'])
                    self.assertEqual(record['undefined_at_acl'], ['acl_get_file_at'])
                    self.assertEqual(record['undefined_fd_acl'], ['acl_get_fd'])
                    self.assertEqual(record['class'], kind * 32)

    def test_defined_export_is_not_a_consumer(self):
        record = M.elf_metadata(fixture(symbols=(), needed=(), defined=tuple(M.LEGACY)))
        self.assertFalse(record['undefined_legacy_acl'])

    def test_needed_without_acl_symbol_still_recorded_for_indirect_review(self):
        record = M.elf_metadata(fixture(symbols=(), needed=('libacl.so.1', 'libc.so.6')))
        self.assertEqual(record['needed'], ['libacl.so.1', 'libc.so.6'])
        self.assertFalse(record['undefined_legacy_acl'])

    def test_truncated_section_inventory_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'elf_section_bound'):
            M.elf_metadata(fixture()[:-1])

    def test_sectionless_dynamic_file_refused_not_absent(self):
        raw = bytearray(fixture())
        struct.pack_into('<H', raw, 60, 0)
        with self.assertRaisesRegex(RuntimeError, 'elf_section_inventory'):
            M.elf_metadata(bytes(raw))

    def test_dynamic_segment_must_match_section(self):
        raw = bytearray(fixture())
        struct.pack_into('<Q', raw, 64 + 8, 121)
        with self.assertRaisesRegex(RuntimeError, 'elf_dynamic_section_required'):
            M.elf_metadata(bytes(raw))

    def test_dynamic_string_and_symbol_addresses_must_match_sections(self):
        for entry in (1, 3):
            raw = bytearray(fixture())
            struct.pack_into('<Q', raw, 120 + entry * 16 + 8, 0x9999)
            with self.subTest(entry=entry), self.assertRaisesRegex(RuntimeError, 'elf_dynamic_.*binding'):
                M.elf_metadata(bytes(raw))

    def test_bad_symbol_name_index_refused(self):
        raw = bytearray(fixture())
        shoff = struct.unpack_from('<Q', raw, 40)[0]
        symoff = struct.unpack_from('<Q', raw, shoff + 2 * 64 + 24)[0]
        struct.pack_into('<I', raw, symoff + 24, 0xffffffff)
        with self.assertRaisesRegex(RuntimeError, 'elf_string_bound'):
            M.elf_metadata(bytes(raw))


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        fenced(self.stack)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in M.ROOTS:
            (self.root / name.lstrip('/')).mkdir()
        self.binary = self.root / 'usr/tool'
        self.binary.write_bytes(fixture())
        (self.root / 'app/ordinary.py').write_text('print("not executed")\n')

    def test_actual_tree_reads_metadata_and_safe_aliases_only(self):
        (self.root / 'usr/tool-link').symlink_to('tool')
        (self.root / 'app/tool-chain').symlink_to('/usr/tool-link')
        (self.root / 'opt/outside').symlink_to('/etc/private-file')
        (self.root / 'app/dir-link').symlink_to('/usr', target_is_directory=True)
        record = M.probe(IMAGE, 'a' * 40, 'b' * 64, root=self.root)
        rows = record['inventory']['elf_files']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['symlink_aliases'], ['/app/tool-chain', '/usr/tool-link'])
        self.assertEqual(rows[0]['sha256'], M.sha256(fixture()).hexdigest())
        self.assertEqual(record['inventory']['regular_files_examined'], 2)
        for key in ('security_accepted', 'scanner_findings_modified', 'inspected_code_executed',
                    'dlopen_or_ctypes_reachability_analyzed', 'acl_flags_or_callsite_safety_analyzed'):
            self.assertIs(record[key], False)

    def test_alias_cycles_are_not_followed(self):
        (self.root / 'app/a').symlink_to('b')
        (self.root / 'app/b').symlink_to('a')
        self.assertEqual(M.inventory(self.root)['elf_files'][0]['symlink_aliases'], [])

    def test_root_directory_symlink_refused(self):
        (self.root / 'opt').rmdir()
        (self.root / 'opt').symlink_to('/usr', target_is_directory=True)
        with self.assertRaises(OSError):
            M.inventory(self.root)

    def test_short_or_growing_elf_cannot_pass_stable_fd_check(self):
        original = M.os.read
        for grow in (False, True):
            self.binary.write_bytes(fixture())
            changed = False
            def read(fd, size):
                nonlocal changed
                value = original(fd, size)
                if not changed and value == b'\x7fELF':
                    changed = True
                    with self.binary.open('ab' if grow else 'wb') as output:
                        output.write(b'X' if grow else b'')
                return value
            with self.subTest(grow=grow), mock.patch.object(M.os, 'read', side_effect=read):
                with self.assertRaisesRegex(RuntimeError, 'inventory_changed'):
                    M.inventory(self.root)

    def test_path_replaced_after_open_refused_even_equal_bytes(self):
        original = M.os.read
        replaced = False
        def read(fd, size):
            nonlocal replaced
            value = original(fd, size)
            if not replaced and value == b'\x7fELF':
                replaced = True
                other = self.binary.with_name('replacement')
                other.write_bytes(fixture())
                other.replace(self.binary)
            return value
        with mock.patch.object(M.os, 'read', side_effect=read):
            with self.assertRaisesRegex(RuntimeError, 'inventory_changed'):
                M.inventory(self.root)

    def test_advertised_bytes_bound_precedes_large_file_read(self):
        with mock.patch.object(M, 'MAX_BYTES', len(fixture()) - 1):
            with self.assertRaisesRegex(RuntimeError, 'inventory_byte_bound'):
                M.inventory(self.root)

    def test_count_and_deadline_bounds(self):
        with mock.patch.object(M, 'MAX_ENTRIES', 0):
            with self.assertRaisesRegex(RuntimeError, 'inventory_count_bound'):
                M.inventory(self.root)
        with self.assertRaisesRegex(RuntimeError, 'inventory_timeout'):
            M.inventory(self.root, deadline=0)

    def test_elf_size_limit_precedes_full_read(self):
        with mock.patch.object(M, 'MAX_ELF', len(fixture()) - 1):
            with self.assertRaisesRegex(RuntimeError, 'elf_file_bound'):
                M.inventory(self.root)

    def test_report_accumulation_bound_refuses_instead_of_truncating(self):
        with mock.patch.object(M, 'MAX_REPORT', 32769):
            with self.assertRaisesRegex(RuntimeError, 'report_bound'):
                M.inventory(self.root)

    def test_parser_refusal_is_not_silently_skipped(self):
        self.binary.write_bytes(b'\x7fELF' + bytes(20))
        with self.assertRaisesRegex(RuntimeError, 'elf_encoding'):
            M.inventory(self.root)

    def test_malformed_identity_refused_before_inventory(self):
        with mock.patch.object(M, 'inventory', side_effect=AssertionError('must_not_read')):
            for image,commit,verifier in ((True,'a'*40,'b'*64),('sha256:bad','a'*40,'b'*64),
                    (IMAGE,True,'b'*64),(IMAGE,'a'*40,None)):
                with self.subTest(image=image,commit=commit,verifier=verifier):
                    with self.assertRaisesRegex(RuntimeError, 'identity_invalid'):
                        M.probe(image,commit,verifier)

    def test_observation_preserves_new_controller_bound_immutable_image(self):
        image = 'sha256:' + 'c' * 64
        result = M.probe(image, 'a' * 40, 'b' * 64, root=self.root)
        self.assertEqual(result['image_id'], image)
        self.assertIs(result['security_accepted'], False)

    def test_cli_outputs_complete_json_with_false_acceptance(self):
        output = io.BytesIO()
        class Stdout:
            buffer = output
        expected = M.probe(IMAGE, 'a' * 40, 'b' * 64, root=self.root)
        with mock.patch.object(M.sys, 'argv', ['probe', '--image-id', IMAGE,
                '--source-commit', 'a' * 40, '--verifier-sha256', 'b' * 64]), \
                mock.patch.object(M.sys, 'stdout', Stdout()), mock.patch.object(M, 'probe', return_value=expected):
            M.main()
        self.assertEqual(json.loads(output.getvalue()), expected)



if __name__ == '__main__':
    unittest.main()
