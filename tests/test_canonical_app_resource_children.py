"""Ordinary lane: two owned Python children; no UID0 or Docker requirement."""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
import json,os,resource,subprocess,sys,unittest

REAL_POPEN=subprocess.Popen
REAL_KILL=os.kill
WORKER=Path(__file__).resolve().parents[1]/'deploy/maintenance/canonical_app_candidate_images_v3.py'

class ResourceChildTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen','subprocess.run','os.system','os.popen','os.fork','os.kill','os.killpg',
                     'socket.socket','socket.create_connection','socket.getaddrinfo','urllib.request.urlopen','ctypes.CDLL'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient denied')))
    def child(self,code):
        fd=os.memfd_create('public-resource-child',os.MFD_CLOEXEC);self.addCleanup(os.close,fd)
        child=REAL_POPEN([sys.executable,'-I','-B','-c',code],stdin=subprocess.DEVNULL,stdout=fd,stderr=subprocess.DEVNULL,close_fds=True)
        try:rc=child.wait(timeout=8)
        finally:
            if child.poll() is None:
                REAL_KILL(child.pid,9);child.wait(timeout=3)
        self.assertLessEqual(os.fstat(fd).st_size,4096);return rc,os.pread(fd,4097,0)
    def test_actual_python_memory_limit_failure_emits_one_closed_worker_record(self):
        code=('import importlib.util,resource,sys\n'
            's=importlib.util.spec_from_file_location("a",'+repr(str(WORKER))+');a=importlib.util.module_from_spec(s);s.loader.exec_module(a)\n'
            'resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2));resource.setrlimit(resource.RLIMIT_CPU,(5,5))\n'
            'resource.setrlimit(resource.RLIMIT_FSIZE,(4097,4097));resource.setrlimit(resource.RLIMIT_CORE,(0,0))\n'
            'def fail(*args):\n x=bytearray(1024*1024**2)\n raise AssertionError("must hit hard memory limit")\n'
            'a.load_engine=fail\nraise SystemExit(a.main(["--site","web","--phase","snapshot","--snapshot-nonce","a"*32,"--source-sha256","b"*64]))\n')
        rc,raw=self.child(code);self.assertEqual(rc,1);self.assertEqual(len(raw.splitlines()),1)
        value=json.loads(raw);self.assertEqual(value['status'],'refused_or_unknown');self.assertFalse(value['retry_allowed']);self.assertFalse(value['security_accepted'])
        self.assertEqual(value['exception_class'],'other');self.assertNotIn(b'bytearray',raw);self.assertNotIn(b'must hit',raw)
    def test_owned_limit_child_is_reaped_and_parent_resource_limits_remain_exact(self):
        kinds=(resource.RLIMIT_AS,resource.RLIMIT_CPU,resource.RLIMIT_FSIZE)
        before={k:resource.getrlimit(k) for k in kinds}
        code=('import resource,json\nresource.setrlimit(resource.RLIMIT_AS,(512*1024**2,512*1024**2))\n'
            'resource.setrlimit(resource.RLIMIT_CPU,(5,5));resource.setrlimit(resource.RLIMIT_FSIZE,(4097,4097))\n'
            'print(json.dumps([resource.getrlimit(resource.RLIMIT_AS),resource.getrlimit(resource.RLIMIT_CPU)]))\n')
        rc,raw=self.child(code);self.assertEqual(rc,0);self.assertEqual(json.loads(raw),[[512*1024**2]*2,[5,5]])
        self.assertEqual(before,{k:resource.getrlimit(k) for k in kinds})

if __name__=='__main__':unittest.main()
