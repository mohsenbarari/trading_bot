"""Public boot-selection and real temporary metadata only; no SSH/reboot."""
from contextlib import ExitStack
from copy import deepcopy
import ast
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import yaml

ROOT=Path(__file__).resolve().parents[1]
PLAY=yaml.safe_load((ROOT/'deploy/maintenance/final_kernel_reboot.yml').read_bytes())[0]
INVENTORY=yaml.safe_load((ROOT/'deploy/maintenance/final_kernel_inventory.yml').read_bytes())
TARGETS=('subprocess.run','subprocess.Popen','os.system','socket.socket','socket.create_connection',
         'socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL','resource.setrlimit','signal.signal','signal.alarm')
with ExitStack()as fence:
    for name in TARGETS:fence.enter_context(patch(name,side_effect=AssertionError('ambient_forbidden')))
    M=types.ModuleType('_final_kernel_fixture')
    exec(compile(PLAY['vars']['final_kernel_probe'],'<exact-final-kernel-probe>','exec'),M.__dict__)


class FinalKernelTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in TARGETS:self.stack.enter_context(patch(name,side_effect=AssertionError('ambient_forbidden')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='public-final-backup-')))
        self.root.chmod(0o700)

    def metadata(self):return M.backup_metadata(self.root,owner=os.geteuid())

    def test_real_metadata_comparison_reads_no_ciphertext_or_other_file_contents(self):
        folder=self.root/'public';folder.mkdir(mode=0o700)
        artifact=folder/'ciphertext';artifact.write_bytes(b'ordinary ciphertext fixture');artifact.chmod(0o600)
        with patch.object(Path,'read_bytes',side_effect=AssertionError('contents_forbidden')),patch.object(os,'read',side_effect=AssertionError('contents_forbidden')),patch.object(os,'pread',side_effect=AssertionError('contents_forbidden')):
            first=self.metadata();self.assertEqual(first,self.metadata())
        self.assertEqual(first['entries'],3);self.assertFalse(first['file_contents_read'])
        artifact.write_bytes(b'changed public fixture');self.assertNotEqual(first,self.metadata())

    def test_redirected_or_hardlinked_ciphertext_metadata_is_refused(self):
        file=self.root/'ordinary';file.write_bytes(b'public');file.chmod(0o600)
        alias=self.root/'alias';os.link(file,alias)
        with self.assertRaisesRegex(RuntimeError,'backup_entry_metadata'):self.metadata()
        alias.unlink();alias.symlink_to(file)
        with self.assertRaisesRegex(RuntimeError,'backup_entry_metadata'):self.metadata()

    def test_only_default_first_target_entry_and_no_next_entry_are_accepted(self):
        good=b'''set default="${next_entry}"\nset default="0"\nmenuentry 'Ubuntu' {\nlinux /boot/vmlinuz-7.0.0-38-generic root=ordinary\ninitrd /boot/initrd.img-7.0.0-38-generic\n}\nsubmenu 'Advanced options' {\n menuentry 'old' {\n linux /boot/vmlinuz-7.0.0-29-generic\n }\n}\n'''
        M.boot_selection(good,'recordfail=1\nsaved_entry=unused-old-entry\n')
        for raw,environment in ((good,'next_entry=old\n'),(good.replace(b'set default="0"',b'set default="1"'),''),
                                (good.replace(b'linux /boot/vmlinuz-7.0.0-38',b'linux /boot/vmlinuz-7.0.0-29'),'')):
            with self.assertRaisesRegex(RuntimeError,'target_boot_selection_unknown'):M.boot_selection(raw,environment)

    def test_playbook_is_exact_host_idempotent_and_has_only_one_reboot_mutation(self):
        host=INVENTORY['all']['hosts'];self.assertEqual(set(host),{'final_kernel'})
        self.assertEqual(host['final_kernel']['ansible_host'],'65.109.214.203')
        self.assertEqual(host['final_kernel']['ansible_ssh_private_key_file'],'/root/.ssh/id_ed25519_finland_primary')
        for text in ('StrictHostKeyChecking=yes','ForwardAgent=no','UpdateHostKeys=no','ControlMaster=no'):
            self.assertIn(text,host['final_kernel']['ansible_ssh_common_args'])
        steps=PLAY['tasks'];reboots=[step for step in steps if 'ansible.builtin.reboot'in step]
        self.assertEqual(len(reboots),1);self.assertEqual(reboots[0]['ansible.builtin.reboot']['reboot_command'],'/usr/sbin/reboot')
        self.assertIn('reboot_required',reboots[0]['when'][1]);self.assertIn("!= '7.0.0-38-generic'",reboots[0]['when'][1])
        for step in steps:
            self.assertFalse({'ansible.builtin.apt','ansible.builtin.copy','ansible.builtin.file','ansible.builtin.shell','ansible.builtin.service'}&set(step))
        self.assertIn('boot_id !=',steps[-1]['ansible.builtin.assert']['that'][-1])
        self.assertIn('backup_metadata ==',steps[-1]['ansible.builtin.assert']['that'][2])
        for step in steps:
            argv=step.get('ansible.builtin.command',{}).get('argv',[])
            if '-c'in argv and not argv[-1].startswith('{{'):ast.parse(argv[-1])
        probes=[step['ansible.builtin.command']['argv'][-1]for step in steps if step.get('register')in ('final_kernel_before','final_kernel_after')]
        self.assertEqual(probes,[PLAY['vars']['final_kernel_probe']]*2)
        self.assertTrue(all('{{ final_kernel_probe }}'not in value for value in probes))


if __name__=='__main__':unittest.main()
