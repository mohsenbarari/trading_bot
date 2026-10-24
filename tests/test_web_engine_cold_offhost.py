"""Cipher-only temporary fixtures; every ambient command/network call is denied."""
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cold_engine_offhost_fixture', ROOT/'deploy/maintenance/web_engine_cold_offhost.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class ColdEngineOffhostTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'os.killpg',
                     'socket.socket', 'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('ambient_host_network_forbidden')))
        self.folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.cold-engine-offhost-', dir=ROOT)))
        self.root = self.folder/'backups'; self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.stack.enter_context(patch.object(M, 'ROOT', self.root))
        self.stack.enter_context(patch.object(M, 'DEVICE', self.root.stat().st_dev))
        self.stack.enter_context(patch.object(M, 'OWNER', os.geteuid()))
        self.available = 32*1024**3
        self.stack.enter_context(patch.object(M.os, 'fstatvfs', side_effect=lambda fd: SimpleNamespace(f_bavail=self.available, f_frsize=1, f_flag=0)))
        self.mount = {'filesystems': [{'target': M.MOUNT_TARGET, 'source': M.MOUNT_SOURCE, 'fstype': 'ext4', 'uuid': M.UUID}]}
        self.calls = []
        self.stack.enter_context(patch.object(M.subprocess, 'run', side_effect=self.findmnt))
        self.operation, self.nonce, self.manifest = '20261024-01', 'a'*32, 'b'*64
        self.data = b'opaque encrypted public fixture bytes'
        self.deadline = (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat().replace('+00:00', 'Z')

    def findmnt(self, arguments, **kwargs):
        self.assertEqual(arguments, ['/usr/bin/findmnt', '--json', '--target', str(self.root), '--output', 'TARGET,SOURCE,FSTYPE,UUID'])
        self.assertEqual(kwargs['timeout'], 5)
        self.assertEqual(kwargs['env'], {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        self.calls.append(arguments)
        return SimpleNamespace(returncode=0, stdout=json.dumps(self.mount).encode())

    def args(self, **changes):
        result = dict(operation=self.operation, nonce=self.nonce, manifest_sha256=self.manifest,
                      cipher_size=len(self.data), cipher_sha256=sha256(self.data).hexdigest(), deadline_utc=self.deadline)
        result.update(changes)
        return result

    def directory(self):
        return self.root/('security-web-engine-cold-'+self.operation+'-'+self.nonce)

    def receive(self, **changes):
        return M.transfer(**self.args(**changes), source=io.BytesIO(self.data))

    def verify(self, **changes):
        return M.transfer(**self.args(**changes), verify=True)

    def capacity(self):
        return M.capacity(self.operation, self.nonce, self.manifest, self.deadline)

    def test_capacity_reports_measured_budget_without_creating_any_file(self):
        self.available = M.RESERVE+M.CONTROL_ALLOWANCE+12345
        result = self.capacity()
        self.assertEqual(result['free_bytes'], self.available)
        self.assertEqual(result['max_cipher_bytes'], 12345)
        self.assertEqual(result['reserve_bytes'], 8*1024**3)
        self.assertFalse(result['files_created'])
        self.assertEqual(list(self.root.iterdir()), [])
        self.available = 4*1024**3
        self.assertEqual(self.capacity()['max_cipher_bytes'], 0)

    def test_receive_and_separate_verify_bind_cipher_metadata_and_fsync(self):
        synced = []; fsync = os.fsync
        def sync(fd):
            synced.append(stat.S_IFMT(os.fstat(fd).st_mode)); return fsync(fd)
        with patch.object(M.os, 'fsync', side_effect=sync):
            received = self.receive()
        verified = self.verify()
        self.assertEqual(received['status'], 'received'); self.assertEqual(verified['status'], 'verified')
        self.assertEqual({key: value for key, value in received.items() if key != 'status'},
                         {key: value for key, value in verified.items() if key != 'status'})
        self.assertEqual((self.directory()/M.CIPHER).read_bytes(), self.data)
        self.assertEqual({path.name for path in self.directory().iterdir()}, {M.CIPHER, M.RECEIPT})
        self.assertEqual(stat.S_IMODE(self.directory().stat().st_mode), 0o700)
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in self.directory().iterdir()))
        self.assertGreaterEqual(synced.count(stat.S_IFDIR), 3)
        self.assertGreaterEqual(synced.count(stat.S_IFREG), 2)
        self.assertTrue(verified['fsync_completed'])
        self.assertFalse(verified['plaintext_materialized_offhost'])
        self.assertFalse(verified['private_key_contents_read'])
        self.assertFalse(verified['production_mutated'])
        self.assertNotIn(self.data.decode(), json.dumps(verified))

    def test_capacity_and_receive_refuse_existing_partial_directory_without_adoption(self):
        self.directory().mkdir(mode=0o755); self.directory().chmod(0o755)
        (self.directory()/'partial').write_bytes(b'original')
        for operation in (self.capacity, self.receive):
            with self.assertRaisesRegex(M.Refusal, 'destination_exists'): operation()
        self.assertEqual((self.directory()/'partial').read_bytes(), b'original')
        self.assertEqual(stat.S_IMODE(self.directory().stat().st_mode), 0o755)

    def test_existing_symlink_is_never_followed_or_adopted(self):
        self.directory().symlink_to(self.folder)
        with self.assertRaisesRegex(M.Refusal, 'destination_exists'): self.receive()
        self.assertTrue(self.directory().is_symlink())

    def test_low_capacity_refuses_before_namespace_creation(self):
        self.available = M.RESERVE+M.CONTROL_ALLOWANCE+len(self.data)-1
        with self.assertRaisesRegex(M.Refusal, 'capacity_insufficient'): self.receive()
        self.assertFalse(self.directory().exists())

    def test_readonly_mount_never_claims_writable_capacity(self):
        with patch.object(M.os, 'fstatvfs', return_value=SimpleNamespace(
                f_bavail=self.available, f_frsize=1, f_flag=os.ST_RDONLY)):
            for operation in (self.capacity, self.receive):
                with self.assertRaisesRegex(M.Refusal, 'mount_readonly'): operation()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_wrong_uuid_source_filesystem_target_or_device_refuses_before_writes(self):
        original = json.loads(json.dumps(self.mount))
        for field, value in (('uuid', 'foreign'), ('source', '/dev/unreviewed'), ('fstype', 'overlay'), ('target', '/unreviewed')):
            self.mount = json.loads(json.dumps(original)); self.mount['filesystems'][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(M.Refusal, 'mount_identity_changed'): self.receive()
            self.assertFalse(self.directory().exists())
        self.mount = original
        with patch.object(M, 'DEVICE', M.DEVICE+1):
            with self.assertRaisesRegex(M.Refusal, 'root_identity_changed'): self.receive()
        self.assertFalse(self.directory().exists())

    def test_root_mode_and_symlink_refuse_before_mount_probe(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(M.Refusal, 'root_identity_changed'): self.capacity()
        self.assertEqual(self.calls, [])
        self.root.chmod(0o700)
        link = self.folder/'linked'; link.symlink_to(self.root)
        with patch.object(M, 'ROOT', link):
            with self.assertRaisesRegex(M.Refusal, 'root_identity_changed'): self.capacity()

    def test_mount_change_after_cipher_write_prevents_receipt_publication(self):
        findmnt = self.findmnt
        def changed(arguments, **kwargs):
            if self.calls:
                self.mount['filesystems'][0]['uuid'] = 'changed-after-write'
            return findmnt(arguments, **kwargs)
        with patch.object(M.subprocess, 'run', side_effect=changed):
            with self.assertRaisesRegex(M.Refusal, 'mount_identity_changed'): self.receive()
        self.assertTrue((self.directory()/M.CIPHER).exists())
        self.assertFalse((self.directory()/M.RECEIPT).exists())

    def test_excess_short_and_wrong_digest_streams_leave_unadoptable_evidence(self):
        for label, data, expected_size, expected_sha in (
            ('excess', b'abcd', 3, sha256(b'abc').hexdigest()),
            ('short', b'ab', 3, sha256(b'abc').hexdigest()),
            ('digest', b'abc', 3, '0'*64)):
            nonce = (label[0].encode().hex()*32)[:32]
            requests = []
            class Source(io.BytesIO):
                def read(self, size):
                    requests.append(size); return super().read(size)
            with self.subTest(label=label), self.assertRaises(M.Refusal):
                M.transfer(**self.args(nonce=nonce, cipher_size=expected_size, cipher_sha256=expected_sha), source=Source(data))
            directory = self.root/('security-web-engine-cold-'+self.operation+'-'+nonce)
            self.assertTrue(directory.exists()); self.assertFalse((directory/M.RECEIPT).exists())
            self.assertLessEqual((directory/M.CIPHER).stat().st_size, expected_size)
            self.assertTrue(all(size <= expected_size+1 for size in requests))
            with self.assertRaisesRegex(M.Refusal, 'destination_exists'):
                M.transfer(**self.args(nonce=nonce), source=io.BytesIO(self.data))

    def test_operation_directory_aba_during_stdin_read_refuses_before_writing(self):
        outer = self
        class Source(io.BytesIO):
            def read(self, size):
                old = outer.root/'displaced'
                outer.directory().rename(old)
                outer.directory().mkdir(mode=0o700)
                outer.directory().rename(outer.root/'foreign')
                old.rename(outer.directory())
                return super().read(size)
        with self.assertRaisesRegex(M.Refusal, '(root|operation)_identity_changed'):
            M.transfer(**self.args(), source=Source(self.data))
        self.assertEqual((self.directory()/M.CIPHER).stat().st_size, 0)
        self.assertFalse((self.directory()/M.RECEIPT).exists())

    def test_root_directory_aba_is_detected_by_retained_root_fd(self):
        outer = self
        class Source(io.BytesIO):
            def read(self, size):
                original = outer.folder/'original-root'
                outer.root.rename(original)
                outer.root.mkdir(mode=0o700)
                outer.root.rename(outer.folder/'foreign-root')
                original.rename(outer.root)
                return super().read(size)
        with self.assertRaisesRegex(M.Refusal, 'root_identity_changed'):
            M.transfer(**self.args(), source=Source(self.data))
        self.assertFalse((self.directory()/M.RECEIPT).exists())

    def test_cipher_aba_during_verify_refuses_even_with_original_bytes_and_mtime(self):
        self.receive()
        path = self.directory()/M.CIPHER; original, info = path.read_bytes(), path.stat()
        pread = os.pread; changed = []
        def read(fd, size, offset):
            raw = pread(fd, size, offset)
            if not changed and os.fstat(fd).st_ino == info.st_ino:
                path.write_bytes(b'x'*len(original)); path.write_bytes(original)
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns)); changed.append(True)
            return raw
        with patch.object(M.os, 'pread', side_effect=read):
            with self.assertRaisesRegex(M.Refusal, 'file_identity_changed'): self.verify()
        self.assertEqual(changed, [True])

    def test_receipt_aba_during_cipher_verification_refuses(self):
        self.receive()
        path = self.directory()/M.RECEIPT; original, info = path.read_bytes(), path.stat()
        cipher_inode = (self.directory()/M.CIPHER).stat().st_ino
        pread = os.pread; changed = []
        def read(fd, size, offset):
            raw = pread(fd, size, offset)
            if not changed and os.fstat(fd).st_ino == cipher_inode:
                path.write_bytes(original+b' '); path.write_bytes(original)
                os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns)); changed.append(True)
            return raw
        with patch.object(M.os, 'pread', side_effect=read):
            with self.assertRaisesRegex(M.Refusal, 'file_identity_changed'): self.verify()
        self.assertEqual(changed, [True])

    def test_verify_cannot_reinterpret_receipt_under_an_extended_deadline(self):
        self.receive()
        future = (datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat().replace('+00:00', 'Z')
        with self.assertRaisesRegex(M.Refusal, 'receipt_changed'): self.verify(deadline_utc=future)

    def test_dirty_receipt_flags_identity_extra_fields_and_duplicate_keys_refuse(self):
        self.receive()
        path = self.directory()/M.RECEIPT; original = json.loads(path.read_bytes())
        variants = [{**original, 'fsync_completed': 1}, {**original, 'plaintext_materialized_offhost': 0},
                    {**original, 'manifest_sha256': '0'*64}, {**original, 'extra': True},
                    {**original, 'ciphertext_binding': {**original['ciphertext_binding'], 'nlink': True}}]
        for record in variants:
            path.write_text(json.dumps(record))
            with self.subTest(record=record), self.assertRaisesRegex(M.Refusal, 'receipt_changed'): self.verify()
        path.write_bytes(b'{"schema":"first","schema":"second"}')
        with self.assertRaisesRegex(M.Refusal, 'receipt_invalid'): self.verify()

    def test_changed_cipher_receipt_or_extra_file_refuses_verification(self):
        self.receive()
        path = self.directory()/M.CIPHER; original = path.read_bytes()
        path.write_bytes(b'x'*len(original))
        with self.assertRaisesRegex(M.Refusal, 'cipher_integrity_failed'): self.verify()
        path.write_bytes(original)
        (self.directory()/'extra').write_bytes(b'public')
        with self.assertRaisesRegex(M.Refusal, 'directory_inventory_invalid'): self.verify()

    def test_short_or_oversized_receipt_refuses_without_body_access(self):
        self.receive(); path = self.directory()/M.RECEIPT
        os.truncate(path, M.CONTROL_MAXIMUM+1)
        with patch.object(M.os, 'pread', side_effect=AssertionError('oversized_receipt_read')):
            with self.assertRaisesRegex(M.Refusal, 'receipt_invalid'): self.verify()

    def test_root_descriptors_close_on_mount_refusal(self):
        opened, closed = [], []; real_open, real_close = os.open, os.close
        def open_file(*args, **kwargs):
            fd = real_open(*args, **kwargs); opened.append(fd); return fd
        def close_file(fd):
            closed.append(fd); return real_close(fd)
        self.mount['filesystems'][0]['uuid'] = 'wrong'
        with patch.object(M.os, 'open', side_effect=open_file), patch.object(M.os, 'close', side_effect=close_file):
            with self.assertRaisesRegex(M.Refusal, 'mount_identity_changed'): self.capacity()
        self.assertEqual(closed, list(reversed(opened)))

    def test_operation_nonce_manifest_size_and_finite_deadline_are_closed_inputs(self):
        for changes in ({'operation': '../foreign'}, {'nonce': 'a'*31}, {'manifest_sha256': True},
                        {'cipher_size': True}, {'cipher_size': -1}, {'cipher_sha256': 'bad'},
                        {'deadline_utc': '2020-01-01T00:00:00Z'},
                        {'deadline_utc': (datetime.now(timezone.utc)+timedelta(hours=3)).isoformat().replace('+00:00', 'Z')}):
            with self.subTest(changes=changes), self.assertRaises(M.Refusal):
                self.receive(**changes)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_cli_returns_only_metadata_and_uses_no_transport_or_private_key(self):
        args = ['receive', '--operation', self.operation, '--nonce', self.nonce,
                '--manifest-sha256', self.manifest, '--deadline-utc', self.deadline,
                '--cipher-size', str(len(self.data)), '--cipher-sha256', sha256(self.data).hexdigest()]
        output = io.StringIO()
        with patch.object(M.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(self.data))), \
                patch.object(M.sys, 'stdout', output), patch.object(M.signal, 'signal'), patch.object(M.signal, 'alarm'):
            self.assertEqual(M.main(args), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'received')
        self.assertNotIn(self.data.decode(), output.getvalue())
        self.assertTrue(self.calls)

    def test_cli_io_error_is_redacted_and_never_prints_cipher_or_private_path(self):
        args = ['capacity', '--operation', self.operation, '--nonce', self.nonce,
                '--manifest-sha256', self.manifest, '--deadline-utc', self.deadline]
        error = io.StringIO()
        with patch.object(M, 'capacity', side_effect=OSError('/private/key-material')), \
                patch.object(M.sys, 'stderr', error), patch.object(M.signal, 'signal'), patch.object(M.signal, 'alarm'):
            self.assertEqual(M.main(args), 1)
        value = json.loads(error.getvalue())
        self.assertEqual(value['code'], 'offhost_backup_refused')
        self.assertNotIn('/private', error.getvalue())
        self.assertFalse(value['secrets_disclosed'])


    def test_final_destination_and_caps_are_explicit(self):
        self.assertEqual(M.UUID,'ce366399-3430-441e-8a6f-6f5621f0c7a2')
        self.assertEqual(M.MOUNT_SOURCE,'/dev/sda1')
        self.assertEqual(M.MOUNT_TARGET,'/')
        self.assertEqual(M.RESERVE,8*1024**3)
        self.assertEqual(M.MAXIMUM,10*1024**3)
        self.available=64*1024**3
        self.assertEqual(self.capacity()['max_cipher_bytes'],M.MAXIMUM)
        with self.assertRaisesRegex(M.Refusal,'size_invalid'):
            self.receive(cipher_size=M.MAXIMUM+1)

    def guard(self):
        return {'schema':'web_engine_cold_ssh_guard/1','operation_id':self.operation,
                'nonce':self.nonce,'expires_utc':self.deadline,
                'source_ip':'65.109.220.59','destination_root':str(self.root),
                'lock_identity':{'device':self.root.stat().st_dev,'inode':1,'uid':os.geteuid(),
                                 'gid':os.getegid(),'mode':stat.S_IFREG|0o600}}

    def guard_command(self, mode='capacity'):
        command='engine-cold '+mode+' --operation '+self.operation+' --nonce '+self.nonce
        command+=' --manifest-sha256 '+self.manifest+' --deadline-utc '+self.deadline
        if mode != 'capacity':
            command+=' --cipher-size '+str(len(self.data))+' --cipher-sha256 '+sha256(self.data).hexdigest()
        return command

    def test_guard_accepts_only_one_owner_and_three_exact_command_shapes(self):
        for mode in ('capacity','receive','verify'):
            command=self.guard_command(mode)
            self.assertEqual(M.guarded_argv(self.guard(),command,'65.109.220.59 44200 65.109.214.203 22'),
                             command.split()[1:])

    def test_guard_denies_wrong_peer_owner_extra_flags_and_shell_commands(self):
        command=self.guard_command()
        for value in (command+' ; id',command+' --operation other',command.replace('engine-cold','/bin/sh',1),
                      command.replace(self.nonce,'c'*32),command.replace(self.operation,'foreign',1),
                      command+'\n',command+' --cipher-size 1'):
            with self.assertRaises(M.Refusal):
                M.guarded_argv(self.guard(),value,'65.109.220.59 44200 65.109.214.203 22')
        with self.assertRaisesRegex(M.Refusal,'peer_invalid'):
            M.guarded_argv(self.guard(),command,'127.0.0.1 44200 65.109.214.203 22')

    def test_guard_refuses_expired_or_extended_deadline(self):
        command=self.guard_command()
        control=self.guard()
        control['expires_utc']='2026-01-01T00:00:00Z'
        with self.assertRaises(M.Refusal):
            M.guarded_argv(control,command,'65.109.220.59 44200 65.109.214.203 22')
        control=self.guard()
        extended=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat().replace('+00:00','Z')
        with self.assertRaisesRegex(M.Refusal,'deadline_invalid'):
            M.guarded_argv(control,command.replace(self.deadline,extended),'65.109.220.59 44200 65.109.214.203 22')

    def test_guard_lock_is_read_only_and_same_inode_while_main_runs(self):
        path=self.root/'guard.json'
        lock=path.with_suffix('.lock');lock.write_text('pending\n');lock.chmod(0o600)
        control=self.guard();control['lock_identity']=M.directory_binding(lock.stat())
        raw=(json.dumps(control,sort_keys=True)+'\n').encode()
        path.write_bytes(raw);path.chmod(0o600)
        lock.write_text(sha256(raw).hexdigest()+'\n')
        # Fixture /tmp parent is intentionally writable; authorize only this owned fixture parent.
        original_lstat=M.Path.lstat
        def observed(p,*args,**kwargs):
            info=original_lstat(p,*args,**kwargs)
            if p in path.parents and p != self.root:
                values=list(info);values[0]=(info.st_mode & ~0o022);values[4]=os.geteuid()
                return os.stat_result(values)
            return info
        with patch.object(M.Path,'lstat',observed),patch.dict(os.environ,{
             'SSH_ORIGINAL_COMMAND':self.guard_command(),'SSH_CONNECTION':'65.109.220.59 44200 65.109.214.203 22'}):
            with patch.object(M,'main',return_value=0) as called:
                self.assertEqual(M.entrypoint(['--ssh-guard',str(path)]),0)
                called.assert_called_once()
            self.assertEqual(lock.read_text(),sha256(raw).hexdigest()+'\n')
            with lock.open('rb') as held:
                M.fcntl.flock(held, M.fcntl.LOCK_EX | M.fcntl.LOCK_NB)
                error=io.StringIO()
                with patch.object(M,'main',side_effect=AssertionError('concurrent_main_forbidden')),patch.object(M.sys,'stderr',error):
                    self.assertEqual(M.entrypoint(['--ssh-guard',str(path)]),1)
                self.assertEqual(json.loads(error.getvalue())['code'],'offhost_guard_busy')
            with patch.object(M,'main',return_value=0):
                self.assertEqual(M.entrypoint(['--ssh-guard',str(path)]),0)
            for victim in (lock,path):
                def replace_during_main(_argv):
                    original=victim.read_bytes()
                    victim.rename(victim.with_name(victim.name+'.old'))
                    victim.write_bytes(original);victim.chmod(0o600)
                    return 0
                error=io.StringIO()
                with patch.object(M,'main',side_effect=replace_during_main),patch.object(M.sys,'stderr',error):
                    self.assertEqual(M.entrypoint(['--ssh-guard',str(path)]),1)
                self.assertEqual(json.loads(error.getvalue())['code'],'offhost_guard_file_changed')
                with patch.object(M,'main',side_effect=AssertionError('replaced_lock_adoption_forbidden')),patch.object(M.sys,'stderr',io.StringIO()):
                    self.assertEqual(M.entrypoint(['--ssh-guard',str(path)]),1)
                # Restore the reviewed inode only for the next independent fixture case.
                victim.unlink();victim.with_name(victim.name+'.old').rename(victim)


if __name__ == '__main__':
    unittest.main()
