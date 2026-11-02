"""The public Debian build gets its normal mask; controller logs stay private."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'ncurses_umask_fixture', ROOT / 'deploy/maintenance/ncurses_build/build.py')
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


class NcursesPackagingUmaskTests(unittest.TestCase):
    def test_only_exact_vendor_packaging_child_uses_normal_public_mask(self):
        previous = os.umask(0o077)
        self.addCleanup(os.umask, previous)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, (executable, expected) in enumerate((
                ('/usr/bin/dpkg-buildpackage', 0o022),
                ('/usr/bin/dpkg-source', -1),
                ('/untrusted/dpkg-buildpackage', -1),
            )):
                with self.subTest(executable=executable):
                    child = SimpleNamespace(
                        stdout=SimpleNamespace(close=Mock()), pid=99999999,
                        poll=lambda: 0, wait=Mock(return_value=0))
                    selector = MagicMock()
                    selector.__enter__.return_value = selector
                    selector.get_map.return_value = {}
                    log = root / (str(index) + '.log')
                    with patch.object(B.subprocess, 'Popen', return_value=child) as spawn, \
                            patch.object(B.selectors, 'DefaultSelector', return_value=selector), \
                            patch.object(B.os, 'killpg', side_effect=AssertionError('host_signal_forbidden')):
                        B.command([executable, '--never-executed'], root, log)
                    self.assertEqual(spawn.call_args.kwargs['umask'], expected)
                    self.assertTrue(spawn.call_args.kwargs['start_new_session'])
                    self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)
                    observed = os.umask(0o077)
                    self.assertEqual(observed, 0o077)
                    child.stdout.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
