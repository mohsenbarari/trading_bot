"""No host compilation: real inert source generation and named CI command mocks."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
import ctypes

FENCE_TARGETS = ('subprocess.Popen', 'subprocess.run', 'subprocess.call', 'subprocess.check_call', 'subprocess.check_output', 'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'ctypes.CDLL', 'os.system', 'os.popen', 'os.fork', 'os.posix_spawn', 'os.posix_spawnp', 'os.kill', 'os.killpg', 'resource.setrlimit')
FENCE_ERROR = 'ambient_execution_denied'
def fenced(stack):
    for target in FENCE_TARGETS:
        stack.enter_context(mock.patch(target, side_effect=AssertionError(FENCE_ERROR)))
    return stack

with ExitStack() as _import_fence:
    fenced(_import_fence)
    SPEC = importlib.util.spec_from_file_location('subdir_contract', Path(__file__).resolve().parents[1]
            / 'deploy/maintenance/util_linux_build/restricted_subdir_contract.py')
    M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)

# Ordinary source structure only. No kernel/mount operation or executable payload.
CONTEXT = '''int mnt_context_is_restricted(struct libmnt_context *cxt)
{ return cxt->restricted; }
int mnt_context_target_fd_required(struct libmnt_context *cxt)
{ return mnt_context_is_restricted(cxt); }
'''
PRE = '''static int hook_mount_pre(struct libmnt_context *cxt,
 const struct libmnt_hookset *hs, void *data __attribute__((__unused__)))
{
 struct hookset_data *hsd = mnt_context_get_hookset_data(cxt, hs);
 if (!hsd) return 0;
 if (mnt_context_target_fd_required(cxt)) {
  DBG(HOOK, ul_debugobj(hs, "ordinary } string /* not comment */"));
  return -ENOTSUP;
 }
 hsd->org_target = strdup(mnt_fs_get_target(cxt->fs));
 if (!hsd->org_target) return -ENOMEM;
 tmptgt_unshare(hsd);
 mnt_fs_set_target(cxt->fs, MNT_PATH_TMPTGT);
 return mnt_context_append_hook(cxt, hs, MNT_STAGE_MOUNT_POST, NULL, hook_mount_post);
}
'''


def archive(entries, prefix):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:xz') as stream:
        directory = tarfile.TarInfo(prefix); directory.type = tarfile.DIRTYPE; stream.addfile(directory)
        for name, raw in entries.items():
            entry = tarfile.TarInfo(prefix + '/' + name); entry.size = len(raw)
            stream.addfile(entry, io.BytesIO(raw))
    return output.getvalue()


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        fenced(self.stack)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.functions = {'mnt_context_is_restricted': M.function(CONTEXT, 'mnt_context_is_restricted'),
            'mnt_context_target_fd_required': M.function(CONTEXT, 'mnt_context_target_fd_required'),
            'hook_mount_pre': M.function(PRE, 'hook_mount_pre')}

    def inputs(self, *, protected=False):
        buffers = {M.ARCHIVE: archive({'libmount/src/context.c': CONTEXT.encode(),
            'libmount/src/hook_subdir.c': PRE.encode()}, 'util-linux-2.41.6')}
        packaging = {'patches/series': ('\n'.join(M.SERIES) + '\n').encode()}
        for name in M.SERIES[:9]:
            target = 'libmount/src/context.c' if protected else 'ordinary/public-source.c'
            packaging['patches/' + name] = ('--- a/' + target + '\n+++ b/' + target + '\n').encode()
        buffers[M.PACKAGING] = archive(packaging, 'debian')
        for name, paths in M.FOLLOWUPS.items():
            buffers[name] = b''.join(('--- a/' + path + '\n+++ b/' + path + '\n').encode() for path in sorted(paths))
        pins = {name: (len(raw), sha256(raw).hexdigest()) for name, raw in buffers.items()}
        package = b'ordinary public package fixture, never executed'
        receipt = {'schema': 'trixie_util_linux_source_candidate/1', 'status': 'candidate_built_not_accepted',
            'source_regressions': {'CVE-2026-78409_vendor_binary_reconciliation': 'OPEN'},
            'security_accepted': False, 'raw_scanner_gate_waived': False, 'production_installed': False,
            'services_changed': False, 'inputs_sha256': {
                (name[:8] + '.patch' if name in M.FOLLOWUPS else name): digest for name, (_, digest) in pins.items()},
            'all_family_packages': [{'name': 'libmount1', 'path': M.PACKAGE, 'version': '2.41.6-0+deb13u0+gold1',
                'bytes': len(package), 'sha256': sha256(package).hexdigest()}]}
        raw = json.dumps(receipt, sort_keys=True).encode()
        for name, value in (('INPUTS', pins), ('RECEIPT_SHA', sha256(raw).hexdigest()),
                            ('PACKAGE_SIZE', len(package)), ('PACKAGE_SHA', sha256(package).hexdigest())):
            self.stack.enter_context(mock.patch.object(M, name, value))
        return buffers, raw, package

    def test_real_archive_path_and_patch_lineage_prepares_original_functions(self):
        buffers, receipt, package = self.inputs()
        functions, evidence = M.prepare(buffers, receipt, package)
        self.assertEqual(functions, self.functions)
        self.assertEqual(len(evidence['active_patch_paths']), 12)
        self.assertEqual(evidence['libmount_package']['sha256'], sha256(package).hexdigest())

    def test_input_pin_drift_and_missing_input_refuse_before_source_parsing(self):
        buffers, receipt, package = self.inputs()
        changed = dict(buffers); changed[M.ARCHIVE] += b'X'
        with self.assertRaisesRegex(RuntimeError, 'input_pin'): M.prepare(changed, receipt, package)
        buffers.pop(M.ARCHIVE)
        with self.assertRaisesRegex(RuntimeError, 'input_inventory'): M.prepare(buffers, receipt, package)

    def test_actual_active_debian_patch_touching_protected_file_refuses(self):
        buffers, receipt, package = self.inputs(protected=True)
        with self.assertRaisesRegex(RuntimeError, 'packaging_protected_path'): M.prepare(buffers, receipt, package)

    def test_followup_exact_paths_not_merely_count_are_required(self):
        buffers, receipt, package = self.inputs()
        name = next(iter(M.FOLLOWUPS)); buffers[name] = b'--- a/ordinary.c\n+++ b/ordinary.c\n'
        M.INPUTS[name] = (len(buffers[name]), sha256(buffers[name]).hexdigest())
        receipt_value = json.loads(receipt); receipt_value['inputs_sha256'][name[:8] + '.patch'] = M.INPUTS[name][1]
        receipt = json.dumps(receipt_value).encode()
        with mock.patch.object(M, 'RECEIPT_SHA', sha256(receipt).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, 'followup_protected_path'): M.prepare(buffers, receipt, package)

    def test_receipt_input_mapping_and_exact_package_identity_required(self):
        buffers, receipt, package = self.inputs()
        for field in ('inputs_sha256', 'all_family_packages'):
            value = json.loads(receipt)
            if field == 'inputs_sha256': value[field][M.ARCHIVE] = '0' * 64
            else: value[field][0]['version'] = 'other'
            raw = json.dumps(value).encode()
            with self.subTest(field=field), mock.patch.object(M, 'RECEIPT_SHA', sha256(raw).hexdigest()):
                with self.assertRaisesRegex(RuntimeError, 'receipt_.*pin'): M.prepare(buffers, raw, package)

    def test_historical_open_receipt_cannot_be_replaced_with_new_success(self):
        buffers, receipt, package = self.inputs()
        value = json.loads(receipt); value['source_regressions']['CVE-2026-78409_vendor_binary_reconciliation'] = 'passed'
        raw = json.dumps(value).encode()
        with mock.patch.object(M, 'RECEIPT_SHA', sha256(raw).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, 'historical_receipt'): M.prepare(buffers, raw, package)
        with self.assertRaisesRegex(RuntimeError, 'package_receipt_pin'): M.prepare(buffers, receipt, package + b'X')

    def test_extractor_balances_comments_strings_and_refuses_duplicates(self):
        self.assertEqual(M.function(PRE, 'hook_mount_pre'), PRE.strip())
        with self.assertRaisesRegex(RuntimeError, 'function_identity'):
            M.function(PRE + PRE, 'hook_mount_pre')
        with self.assertRaisesRegex(RuntimeError, 'function_unterminated'):
            M.function('int ordinary(void) { /* } */', 'ordinary')

    def test_mutation_removes_only_real_refusal_block(self):
        original = M.source_code(self.functions)
        mutated = M.source_code(self.functions, mutated=True)
        self.assertIn(self.functions['mnt_context_target_fd_required'], original)
        self.assertIn(self.functions['hook_mount_pre'], original)
        self.assertNotIn('return -ENOTSUP;', mutated)
        self.assertIn('hook_mount_pre(&c, &h, NULL) != -ENOTSUP || effects != 0', mutated)
        self.assertIn('return 10;', mutated)
        self.assertIn('effects != 4', original)
        with self.assertRaisesRegex(RuntimeError, 'guard_identity'): M.remove_guard(M.remove_guard(PRE))

    def test_source_archive_missing_member_or_duplicate_is_refused(self):
        raw = archive({'ordinary.c': b'int ordinary;'}, 'source')
        with self.assertRaisesRegex(RuntimeError, 'source_members_missing'):
            M.members(raw, ['source/missing.c'], prefix='source', maximum=1024)
        with self.assertRaisesRegex(RuntimeError, 'source_inventory'):
            M.members(raw, ['source/ordinary.c'], prefix='source', maximum=1)

    def test_compile_actual_caller_requires_compile_success_and_exact_mutation_exit(self):
        calls = []
        def named_command(argv, output, name, deadline, expected):
            calls.append((argv, name, expected))
            if '-o' in argv:
                Path(argv[argv.index('-o') + 1]).write_bytes(b'ordinary inert compiler-output fixture')
            return {'exit_code': expected, 'log_bytes': 0, 'log_sha256': sha256(b'').hexdigest()}
        with mock.patch.object(M, 'command', side_effect=named_command):
            records = M.compile_contract(self.functions, self.root, 100)
        self.assertEqual(len(calls), 8); self.assertEqual(len(records), 4)
        for argv, name, expected in calls:
            if name.endswith('-compile'):
                self.assertEqual(argv[0], '/usr/bin/cc'); self.assertIn('-Werror', argv)
                self.assertEqual('-DUSE_LIBMOUNT_MOUNTFD_SUPPORT=1' in argv, name.startswith('mountfd-enabled'))
                self.assertEqual(expected, 0)
            else:
                self.assertEqual(expected, 10 if 'missing-guard' in name else 0)
                self.assertEqual(len(argv), 1)

    def test_compile_failure_cannot_count_as_negative_control_success(self):
        with mock.patch.object(M, 'command', side_effect=RuntimeError('contract_command_failed')) as command:
            with self.assertRaisesRegex(RuntimeError, 'contract_command_failed'):
                M.compile_contract(self.functions, self.root, 100)
        self.assertEqual(command.call_count, 1)

    def test_no_local_compile_even_if_output_requested(self):
        with mock.patch.object(M, 'compile_contract', side_effect=AssertionError('no_host_compile')), \
                mock.patch.object(M.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'github_ci_only'):
                M.run(self.root, self.root/'build-receipt.json', self.root/M.PACKAGE,
                      self.root/'new-output', 'a'*40, 'b'*64)
        self.assertFalse((self.root/'new-output').exists())

    def test_real_offline_flow_emits_separate_proof_and_preserves_old_receipt(self):
        buffers, receipt, package = self.inputs()
        inputs = self.root/'inputs'; inputs.mkdir()
        for name, raw in buffers.items(): (inputs/name).write_bytes(raw)
        receipt_path, package_path = self.root/'build-receipt.json', self.root/M.PACKAGE
        receipt_path.write_bytes(receipt); package_path.write_bytes(package)
        output = self.root/'new-proof'
        with mock.patch.object(M, 'admit_ci') as admission, \
                mock.patch.object(M, 'compile_contract', return_value={'named_benign_ci_result': 'passed'}) as compiler:
            result = M.run(inputs, receipt_path, package_path, output, 'a'*40, 'b'*64)
        admission.assert_called_once(); compiler.assert_called_once()
        self.assertEqual(json.loads((output/'proof.json').read_bytes()), result)
        self.assertEqual(receipt_path.read_bytes(), receipt)
        for key in ('historical_receipts_modified', 'vendor_database_reconciliation_completed',
                    'scanner_findings_modified', 'production_installed', 'security_accepted',
                    'privileged_or_kernel_mount_operations_executed'):
            self.assertIs(result[key], False)

    def test_ci_compile_failure_never_writes_pass_receipt(self):
        buffers, receipt, package = self.inputs()
        inputs = self.root/'inputs'; inputs.mkdir()
        for name, raw in buffers.items(): (inputs/name).write_bytes(raw)
        receipt_path, package_path = self.root/'build-receipt.json', self.root/M.PACKAGE
        receipt_path.write_bytes(receipt); package_path.write_bytes(package)
        output = self.root/'new-proof'
        with mock.patch.object(M, 'admit_ci'), mock.patch.object(M, 'compile_contract',
                side_effect=RuntimeError('contract_command_failed')):
            with self.assertRaisesRegex(RuntimeError, 'contract_command_failed'):
                M.run(inputs, receipt_path, package_path, output, 'a'*40, 'b'*64)
        self.assertFalse((output/'proof.json').exists())

    def test_input_drift_precedes_output_creation_and_compilation(self):
        buffers, receipt, package = self.inputs()
        inputs = self.root/'inputs'; inputs.mkdir()
        for name, raw in buffers.items(): (inputs/name).write_bytes(raw)
        (inputs/M.ARCHIVE).write_bytes(b'changed public source')
        receipt_path, package_path = self.root/'build-receipt.json', self.root/M.PACKAGE
        receipt_path.write_bytes(receipt); package_path.write_bytes(package)
        output = self.root/'new-proof'
        with mock.patch.object(M, 'admit_ci'), mock.patch.object(M, 'compile_contract',
                side_effect=AssertionError('must_not_compile')):
            with self.assertRaisesRegex(RuntimeError, 'input_pin'):
                M.run(inputs, receipt_path, package_path, output, 'a'*40, 'b'*64)
        self.assertFalse(output.exists())

    def test_read_rejects_growth_and_path_aba(self):
        path = self.root/'public'; path.write_bytes(b'ordinary source')
        real = M.os.read; changed = False
        def reader(fd, size):
            nonlocal changed
            raw = real(fd, size)
            if raw and not changed:
                changed = True
                replacement = self.root/'replacement'; replacement.write_bytes(b'ordinary source'); replacement.replace(path)
            return raw
        with mock.patch.object(M.os, 'read', side_effect=reader):
            with self.assertRaisesRegex(RuntimeError, 'input_changed'): M.read(path, 100)

    def test_command_timeout_kills_only_owned_process_group(self):
        child = mock.Mock(pid=12345, poll=mock.Mock(return_value=0))
        child.wait.side_effect = [subprocess.TimeoutExpired('named-owned-fixture', 1), 0]
        with mock.patch.object(M.subprocess, 'Popen', return_value=child), \
                mock.patch.object(M.os, 'killpg') as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                M.command(['/named-owned-fixture'], self.root, 'timeout', M.time.monotonic()+1, 0)
        kill.assert_called_once_with(child.pid, M.signal.SIGKILL)

    def test_wrong_command_exit_cannot_emit_a_pass_record(self):
        child = mock.Mock(pid=12345, poll=mock.Mock(return_value=0), wait=mock.Mock(return_value=0))
        with mock.patch.object(M.subprocess, 'Popen', return_value=child), mock.patch.object(M.os, 'killpg'):
            with self.assertRaisesRegex(RuntimeError, 'contract_command_failed'):
                M.command(['/named-owned-fixture'], self.root, 'wrong-exit', M.time.monotonic()+1, 10)



if __name__ == '__main__': unittest.main()
