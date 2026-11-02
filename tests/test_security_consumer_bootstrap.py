"""Execute the exact YAML bootstrap against ordinary private source fixtures."""
import ast
import builtins
from contextlib import ExitStack
from hashlib import sha256
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

REPO=Path(__file__).resolve().parents[1]
YAML=REPO/'deploy/maintenance/security_consumers.yml'


def bootstrap():
    tasks=yaml.safe_load(YAML.read_bytes())[0]['tasks']
    task=next(v for v in tasks if v['name']=='Invoke one bounded worker with public identity arguments only')
    argv=task['ansible.builtin.command']['argv']
    return argv,argv[argv.index('-c')+1]


class EntrypointBufferTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','socket.create_connection','socket.getaddrinfo',
                     'urllib.request.urlopen','ctypes.CDLL','resource.setrlimit','signal.signal','signal.alarm'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.consumer-entrypoint-',dir=REPO)))
        self.root.chmod(0o700);self.path=self.root/'security_consumer_site.py';self.marker=self.root/'effect'

    def save(self,raw):self.path.write_bytes(raw);self.path.chmod(0o600);return sha256(raw).hexdigest()

    def execute(self,pin,*args):
        _,code=bootstrap()
        with patch.object(sys,'argv',['-c',str(self.path),pin,*args]):exec(compile(code,'<actual-yaml-bootstrap>','exec'),{})

    def test_exact_same_buffer_preserves_argv_file_and_main_with_fd_held(self):
        raw=b'import sys\n_test_worker_observe(__file__,__name__,sys.argv)\n'
        pin=self.save(raw);opened=[];closed=[];observed=[];original_open=os.open;original_close=os.close
        def open_file(path,*a,**kw):
            fd=original_open(path,*a,**kw)
            if path==self.path:opened.append(fd)
            return fd
        def close(fd):closed.append(fd);return original_close(fd)
        def observe(file,name,argv):
            self.assertEqual(file,str(self.path));self.assertEqual(name,'__main__')
            self.assertEqual(argv,[str(self.path),'target','--site','web','--service','app'])
            self.assertEqual(len(opened),1);self.assertNotIn(opened[0],closed)
            self.assertEqual(os.pread(opened[0],len(raw)+1,0),raw);observed.append(True)
        with patch.object(os,'open',side_effect=open_file),patch.object(os,'close',side_effect=close), \
             patch.dict(builtins.__dict__,{'_test_worker_observe':observe}):
            self.execute(pin,'target','--site','web','--service','app')
        self.assertEqual(observed,[True]);self.assertEqual(closed,[opened[0]])

    def test_changed_top_level_bytes_never_execute_after_earlier_stat(self):
        original=b'pass\n';pin=self.save(original)
        changed=('from pathlib import Path\nPath('+repr(str(self.marker))+').write_text("executed")\n').encode()
        self.path.write_bytes(changed)
        with self.assertRaisesRegex(AssertionError,'site_entrypoint_digest'):self.execute(pin,'check')
        self.assertFalse(self.marker.exists())

    def test_replacement_between_pread_and_compile_never_executes(self):
        raw=b'_test_worker_observe()\n';pin=self.save(raw);called=[];original=os.pread;changed=False
        def pread(fd,n,offset):
            nonlocal changed
            result=original(fd,n,offset)
            if not changed:
                changed=True;replacement=self.root/'replacement';replacement.write_bytes(b'pass\n');replacement.chmod(0o600)
                os.replace(replacement,self.path)
            return result
        with patch.object(os,'pread',side_effect=pread),patch.dict(builtins.__dict__,{'_test_worker_observe':lambda:called.append(True)}):
            with self.assertRaisesRegex(AssertionError,'site_entrypoint_changed'):self.execute(pin,'check')
        self.assertEqual(called,[])

    def test_unsafe_mode_link_or_advertised_bound_refuses_before_body(self):
        raw=b'_test_worker_observe()\n';pin=self.save(raw);called=[]
        with patch.dict(builtins.__dict__,{'_test_worker_observe':lambda:called.append(True)}):
            self.path.chmod(0o644)
            with self.assertRaises(AssertionError):self.execute(pin)
            self.path.chmod(0o600);alias=self.root/'alias';os.link(self.path,alias)
            with self.assertRaises(AssertionError):self.execute(pin)
            alias.unlink();self.path.write_bytes(b' '*131073)
            with self.assertRaises(AssertionError):self.execute(pin)
        self.assertEqual(called,[])

    def test_final_metadata_check_runs_even_on_worker_success_exit(self):
        raw=b'_test_worker_change()\nraise SystemExit(0)\n';pin=self.save(raw)
        def change():self.path.write_bytes(b'pass\n')
        with patch.dict(builtins.__dict__,{'_test_worker_change':change}):
            with self.assertRaisesRegex(AssertionError,'site_entrypoint_changed'):self.execute(pin)

    def test_hash_argument_comes_from_the_unique_sealed_site_asset(self):
        argv,code=bootstrap();self.assertEqual(argv[:4],['/usr/bin/python3','-I','-B','-c'])
        self.assertIn('selectattr("name", "equalto", "security_consumer_site.py")',argv[6])
        tree=ast.parse(code)
        self.assertTrue(any(isinstance(v,ast.Call) and isinstance(v.func,ast.Name) and v.func.id=='compile'
                            and isinstance(v.args[0],ast.Name) and v.args[0].id=='raw' for v in ast.walk(tree)))


if __name__=='__main__':unittest.main()
