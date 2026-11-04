"""Offline capsule custody and isolated import canaries; no model or services."""
from pathlib import Path
import json
import os
import tempfile
import unittest

from scripts import two_site_maintenance_lkg as lkg


class TestPinnedControllerCapsule(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="qoder-lkg-offline-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name); self.root = base / "live"; self.root.mkdir()
        self.store = base / "host-private"; self.head = "1" * 40
        self.capsule = self.store / "capsules" / self.head
        code = ("import sys,json\nfrom pathlib import Path\n"
            "sys.path.insert(0,str(Path(__file__).resolve().parents[1]))\n"
            "from scripts.two_site_dummy import VALUE\n"
            "root=Path(sys.argv[sys.argv.index('--root')+1])\n"
            "(root/'public-import-proof.json').write_text(json.dumps({'value':VALUE,'isolated':sys.flags.isolated}))\n")
        files = {"scripts/two_site_qoder_maintenance.py": code.encode(),
                 "scripts/two_site_qoder_host.py": code.encode(),
                 "scripts/two_site_maintenance_lkg.py": b"# fixture entry\n",
                 "scripts/two_site_dummy.py": b"VALUE='pinned-capsule'\n",
                 "scripts/__init__.py": b"# fixture package\n", lkg.POLICY: b"{}\n"}
        for name in lkg.CRITICAL:
            files.setdefault(name, b"# fixed offline critical canary\n")
        files.update({name: b"# fixed offline golden canary\n" for name in lkg.GOLDENS})
        rows = {}
        for name, data in files.items():
            path = self.capsule / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data); path.chmod(0o600)
            rows[name] = {"bytes": len(data), "sha256": lkg.sha(data)}
        manifest = {"schema": "QODER_LKG_CONTROLLER_V1", "head": self.head, "checkpoint_sha256": "2" * 64,
            "files": rows, "critical_paths": lkg.critical_paths(rows), "golden_tests": list(lkg.GOLDENS)}
        self.encoded = lkg.canonical(manifest); self.sha = lkg.sha(self.encoded)
        (self.capsule / "manifest.json").write_bytes(self.encoded)
        (self.capsule / "manifest.json").chmod(0o600)
        (self.store / "current.json").write_bytes(lkg.canonical({"schema": "QODER_LKG_POINTER_V1",
            "head": self.head, "manifest_sha256": self.sha}))
        (self.store / "current.json").chmod(0o600)

    def test_exact_public_capsule_qualifies(self):
        value = lkg.verify_capsule(self.capsule, self.sha)
        self.assertEqual(value["head"], self.head)
        self.assertEqual(lkg.current_capsule(self.store)[0], self.capsule)

    def test_genuine_child_isolated_from_broken_live_import(self):
        scripts = self.root / "scripts"; scripts.mkdir()
        (scripts / "__init__.py").write_text("raise RuntimeError('broken LIVE package must never import')\n")
        (scripts / "two_site_dummy.py").write_text("raise RuntimeError('broken LIVE engine')\n")
        self.assertEqual(lkg.launch(self.root, self.store), 0)
        proof = json.loads((self.root / "public-import-proof.json").read_text())
        self.assertEqual(proof, {"value": "pinned-capsule", "isolated": 1})
        self.assertEqual(lkg.verify_capsule(self.capsule, self.sha)["head"], self.head)

    def test_changed_or_extra_code_and_wrong_pointer_rejected(self):
        path = self.capsule / "scripts/two_site_dummy.py"
        original = path.read_bytes(); path.write_bytes(b"VALUE='altered'\n")
        with self.assertRaisesRegex(lkg.LKGError, "LKG_FILE_SHA"):
            lkg.verify_capsule(self.capsule, self.sha)
        path.write_bytes(original); extra = self.capsule / "scripts/two_site_extra.py"; extra.write_text("# no\n")
        with self.assertRaisesRegex(lkg.LKGError, "LKG_EXTRA_FILE"):
            lkg.verify_capsule(self.capsule, self.sha)
        extra.unlink()
        with self.assertRaisesRegex(lkg.LKGError, "LKG_MANIFEST_SHA"):
            lkg.verify_capsule(self.capsule, "f" * 64)

    def test_capsule_link_and_model_visible_store_are_not_authority(self):
        alias = self.store / "alias"; alias.symlink_to(self.capsule, target_is_directory=True)
        with self.assertRaisesRegex(lkg.LKGError, "LKG_CAPSULE_ALIAS"):
            lkg.verify_capsule(alias, self.sha)
        with self.assertRaisesRegex(lkg.LKGError, "LKG_ROOT"):
            lkg.launch(self.root, self.root / "model-visible-store")
        self.assertIsNone(lkg.executing_trusted_capsule())
        self.assertFalse(lkg.permits_critical_path("scripts/two_site_assignment_scope.py"))

    def test_pointer_and_manifest_reject_duplicate_nonfinite_and_wrong_types(self):
        pointer = self.store / "current.json"
        original = pointer.read_bytes()
        duplicate = b'{"schema":"QODER_LKG_POINTER_V1","head":"' + self.head.encode() + b'","head":"' + self.head.encode() + b'","manifest_sha256":"' + self.sha.encode() + b'"}\n'
        pointer.write_bytes(duplicate)
        with self.assertRaisesRegex(lkg.LKGError, "LKG_DUPLICATE_KEY"):
            lkg.current_capsule(self.store)
        pointer.write_bytes(b'{"head":NaN}\n')
        with self.assertRaisesRegex(lkg.LKGError, "LKG_NONFINITE"):
            lkg.current_capsule(self.store)
        value = json.loads(original); value["head"] = int("1" * 40)
        pointer.write_bytes(lkg.canonical(value))
        with self.assertRaisesRegex(lkg.LKGError, "LKG_POINTER"):
            lkg.current_capsule(self.store)
        pointer.write_bytes(original)
        manifest = json.loads(self.encoded); manifest["files"] = True
        bad = lkg.canonical(manifest); (self.capsule / "manifest.json").write_bytes(bad)
        with self.assertRaisesRegex(lkg.LKGError, "LKG_MANIFEST"):
            lkg.verify_capsule(self.capsule, lkg.sha(bad))


class TestBorrowedMonitorDescriptor(unittest.TestCase):
    def test_borrowed_real_lock_remains_held_and_wrong_inode_is_rejected(self):
        import fcntl
        with tempfile.TemporaryDirectory(prefix="lkg-monitor-fd-") as temp:
            root = Path(temp); path = root / ".local/two-site-refactor/monitor/monitor.lock"
            path.parent.mkdir(parents=True); path.touch(mode=0o600)
            descriptor = os.open(path, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with lkg.inherited_monitor(root, descriptor):
                    pass
                # A distinct OFD still cannot acquire it: the borrowed helper
                # did not unlock the parent monitor while releasing AP.
                other = os.open(path, os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(other)
                path.unlink(); path.touch(mode=0o600)
                with self.assertRaisesRegex(lkg.LKGError, "LKG_MONITOR_CUSTODY"):
                    with lkg.inherited_monitor(root, descriptor):
                        self.fail("wrong inode admitted")
            finally:
                os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
