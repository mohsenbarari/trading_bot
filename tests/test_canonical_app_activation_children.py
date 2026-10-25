"""Ordinary hosted-user owned children; no Root privileges or operational clients."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import importlib.util
import json
import os
import resource
import signal
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
REAL_POPEN,REAL_KILL,REAL_KILLPG = subprocess.Popen,os.kill,os.killpg


def module(suffix):
    path=ROOT/'deploy/maintenance'/('canonical_app_activation_'+suffix+'.py')
    spec=importlib.util.spec_from_file_location('_hosted_activation_'+suffix,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


C,S,K=module('contract'),module('site'),module('coordinator')
S.C=C;K.C=C;K.S=S


class ActivationOwnedChildrenTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close);self.children=[]
        for name in ('subprocess.run','subprocess.Popen','subprocess.call','subprocess.check_call','subprocess.check_output',
                'os.system','os.popen','os.fork','os.forkpty','os.posix_spawn','os.posix_spawnp',
                'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))
        for name,real in (('kill',REAL_KILL),('killpg',REAL_KILLPG)):
            def owned(target,number,real=real):
                self.assertIn(target,[child.pid for child in self.children]);self.assertEqual(number,signal.SIGKILL)
                return real(target,number)
            self.stack.enter_context(patch.object(os,name,side_effect=owned))

    def test_actual_owned_python_command_bounds_timeout_reap_and_parent_limits(self):
        before=resource.getrlimit(resource.RLIMIT_FSIZE)
        scripts=("print('PUBLIC')","import os;os.write(1,b'P'*4096)",'import time;time.sleep(5)')
        def owned(argv,**kwargs):
            self.assertEqual(argv[:4],[sys.executable,'-I','-B','-c']);self.assertEqual(len(argv),5)
            self.assertIn(argv[4],scripts)
            child=REAL_POPEN(argv,**kwargs);self.children.append(child);return child
        with patch.object(S.subprocess,'Popen',side_effect=owned):
            self.assertEqual(S.command([sys.executable,'-I','-B','-c',scripts[0]],time.monotonic()+3,maximum=64),b'PUBLIC\n')
            with self.assertRaisesRegex(RuntimeError,'command_unproven'):
                S.command([sys.executable,'-I','-B','-c',scripts[1]],time.monotonic()+3,maximum=64)
            with self.assertRaisesRegex(S.Unknown,'command_timeout'):
                S.command([sys.executable,'-I','-B','-c',scripts[2]],time.monotonic()+.08,maximum=64,mutation=True)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)
        self.assertEqual(len(self.children),3)
        for child in self.children:
            self.assertIsNotNone(child.returncode)
            with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)

    def test_owned_first_ready_refusal_and_timeout_reap_child_and_close_pipes(self):
        control={'nonce':'a'*32,'source_files':{'canonical_app_activation_coordinator.py':{'sha256':'b'*64}}}
        for mode in ('refusal','timeout'):
            refusal=json.dumps(K.frame(control,0,'refused_or_unknown'))
            script=("import time;print("+repr(refusal)+",flush=True);time.sleep(30)") if mode=='refusal' else 'import time;time.sleep(30)'
            def owned(argv,**kwargs):
                self.assertEqual(argv[:len(K.SSH)],K.SSH)
                child=REAL_POPEN([sys.executable,'-I','-B','-c',script],**kwargs);self.children.append(child);return child
            with self.subTest(mode=mode),patch.object(K.subprocess,'Popen',side_effect=owned):
                with self.assertRaises(S.Unknown):K.Peer(control,time.monotonic()+(.08 if mode=='timeout' else 2))
            child=self.children[-1]
            self.assertIsNotNone(child.returncode);self.assertTrue(child.stdin.closed);self.assertTrue(child.stdout.closed)
            with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)
        self.assertEqual(len(self.children),2)


if __name__=='__main__':unittest.main()
