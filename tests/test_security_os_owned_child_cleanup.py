"""Original genuine inert-child cleanup regressions plus new ordinary bindings."""
from contextlib import redirect_stdout
from hashlib import sha256
from pathlib import Path
from types import ModuleType,SimpleNamespace
from unittest.mock import patch
import ast,io,json,os,signal,subprocess,sys,tempfile,time,unittest
HERE=Path(__file__).resolve().parents[1]
SCRIPTS=HERE/'scripts'
def load(name):
 p=SCRIPTS/name;m=ModuleType(name);m.__file__=str(p);exec(compile(p.read_bytes(),str(p),'exec'),m.__dict__);return m
B=load('compose_security_os_candidate.py');A=load('prepare_app_security_os_cache.py');Q=load('prepare_sqlite_wal_os_cache.py')
class OwnedCleanupTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='.owned-ba938-', dir=HERE)
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def command(self, code, seconds=2):
        return B.command([sys.executable, '-I', '-S', '-B', '-c', code], seconds=seconds, maximum=128)

    def test_real_successful_and_nonzero_reaped_command_never_signalled(self):
        with patch.object(B.os, 'killpg', side_effect=AssertionError('postreap signal denied')):
            self.assertEqual(self.command("print('PUBLIC')"), b'PUBLIC\n')
            with self.assertRaisesRegex(RuntimeError, 'command_failed'):
                self.command("print('PUBLIC');raise SystemExit(7)")

    def wrapped_spawn(self, kind, *, stream=False):
        real = subprocess.Popen
        children = []

        class Wrapped:

            def __init__(self, child):
                self.child = child
                self.first = True

            def __getattr__(self, name):
                return getattr(self.child, name)

            def wait(self, *args, **kw):
                if self.first:
                    self.first = False
                    if kind == 'interrupted':
                        self.child.wait(*args, **kw)
                        raise RuntimeError('owned_after_reap')
                    if kind == 'timeout':
                        return self.child.wait(timeout=0.02)
                return self.child.wait(*args, **kw)

        def spawn(argv, *args, **kw):
            if stream:
                self.assertEqual(argv[:4], ['gh', 'api', '--method', 'GET'])
                self.assertIn('/actions/artifacts/123/zip', argv[-1])
                self.assertEqual(kw['env']['GH_TOKEN'], 'PUBLIC-OWNED-FIXTURE')
                code = 'import time;time.sleep(2)' if kind == 'timeout' else "import sys;sys.stdout.buffer.write(b'PUBLIC-ARTIFACT');" + ('raise SystemExit(7)' if kind == 'nonzero' else '')
                argv = [sys.executable, '-I', '-S', '-B', '-c', code]
            child = real(argv, *args, **kw)
            children.append(child)
            return Wrapped(child) if kind in ('interrupted', 'timeout') else child
        return (spawn, children)

    def test_interrupted_real_reap_command_never_signalled(self):
        spawn, children = self.wrapped_spawn('interrupted')
        with patch.object(B.subprocess, 'Popen', spawn), patch.object(B.os, 'killpg', side_effect=AssertionError('reaped group denied')), self.assertRaisesRegex(RuntimeError, 'owned_after_reap'):
            self.command("print('PUBLIC')")
        self.assertEqual(children[0].returncode, 0)

    def test_actual_live_timeout_still_proves_kernel_ownership_then_kills_and_reaps(self):
        spawn, children = self.wrapped_spawn('normal')
        real_waitid = os.waitid
        real_killpg = os.killpg
        signals = []

        def killpg(pid, sig):
            self.assertEqual(pid, children[0].pid)
            self.assertIsNone(children[0].returncode)
            real_waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            signals.append((pid, sig))
            return real_killpg(pid, sig)
        with patch.object(B.subprocess, 'Popen', spawn), patch.object(B.os, 'killpg', killpg), self.assertRaisesRegex(RuntimeError, 'command_timeout'):
            self.command('import time;time.sleep(2)', seconds=0.02)
        self.assertEqual(signals, [(children[0].pid, signal.SIGKILL)])
        self.assertIsNotNone(children[0].returncode)

    def fetch(self, kind):
        spawn, children = self.wrapped_spawn(kind, stream=True)
        raw = b'PUBLIC-ARTIFACT'
        target = self.root / ('artifact-' + kind)
        with patch.object(B.subprocess, 'Popen', spawn), patch.object(B.os, 'environ', {'GH_TOKEN': 'PUBLIC-OWNED-FIXTURE'}):
            B.fetch_artifact(123, target, len(raw), sha256(raw).hexdigest())
        return (target, children)

    def test_real_owned_artifact_child_success_nonzero_and_interrupted_reap_no_signal(self):
        with patch.object(B.os, 'killpg', side_effect=AssertionError('reaped group denied')):
            target, children = self.fetch('normal')
            self.assertEqual(target.read_bytes(), b'PUBLIC-ARTIFACT')
            self.assertEqual(children[0].returncode, 0)
            with self.assertRaisesRegex(RuntimeError, 'artifact_download_failed'):
                self.fetch('nonzero')
            with self.assertRaisesRegex(RuntimeError, 'owned_after_reap'):
                self.fetch('interrupted')

    def test_real_owned_artifact_timeout_kills_and_reaps_only_current_child(self):
        spawn, children = self.wrapped_spawn('timeout', stream=True)
        signals = []
        real = os.killpg

        def killpg(pid, sig):
            self.assertEqual(pid, children[0].pid)
            self.assertIsNone(children[0].returncode)
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            signals.append(pid)
            return real(pid, sig)
        with patch.object(B.subprocess, 'Popen', spawn), patch.object(B.os, 'killpg', killpg), patch.object(B.os, 'environ', {'GH_TOKEN': 'PUBLIC-OWNED-FIXTURE'}), self.assertRaises(subprocess.TimeoutExpired):
            B.fetch_artifact(123, self.root / 'timeout-artifact', 16, sha256(b'PUBLIC-ARTIFACT').hexdigest())
        self.assertEqual(signals, [children[0].pid])
        self.assertIsNotNone(children[0].returncode)

class BindingTests(unittest.TestCase):
 def test_new_transport_source_is_exact_reviewed_minimal_copy(self):
  raw=(SCRIPTS/'compose_security_os_candidate.py').read_bytes()
  self.assertEqual((len(raw),sha256(raw).hexdigest()),(54258,'a7ba6515937e7ae8193be4d550547651fe5b313399ec3a1f7634efe513e151c1'))
 def test_app_lock_only_transport_source_changes_not_artifact_or_package_evidence(self):
  raw=(SCRIPTS/'app_security_os_packages.lock.json').read_bytes();lock=json.loads(raw)
  self.assertEqual(sha256(raw).hexdigest(),A.LOCK_SHA)
  self.assertEqual(lock['source'],{'path':'compose_security_os_candidate.py','bytes':54258,'sha256':'a7ba6515937e7ae8193be4d550547651fe5b313399ec3a1f7634efe513e151c1'})
  lock['source'].update(bytes=54267,sha256='10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44')
  self.assertEqual(sha256((json.dumps(lock,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest(),'86d3fc5098a24320dfd7aad67a1df7d671e2bf1bdec2e5c0a66a308eb638f9f9')
 def test_sqlite_fetcher_truthfully_points_to_the_new_source(self):
  self.assertEqual(Q.FETCHER,('compose_security_os_candidate.py',54258,'a7ba6515937e7ae8193be4d550547651fe5b313399ec3a1f7634efe513e151c1'))
  self.assertEqual(Q.LOCK_SHA,'26b14975e21b51587bd39b9749c658ac37acdb861407745639e9913f6fed03eb')
if __name__=='__main__':unittest.main()
