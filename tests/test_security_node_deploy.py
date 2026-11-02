"""Hermetic Node deployment checks: dummy tools, tiny authenticated archives.

No release entrypoint, registry access, host package installation or global
runtime writes. The frontend guard's tests own the version-floor semantics;
these checks ensure deployment calls that policy instead of outdated floors.
"""

from hashlib import sha256
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "scripts/production_deploy_online.sh"
TOP = "node-v24.22.1-linux-x64"
PUBLIC_SHA = "6c172a9ab6718f7b801fb5a7bd486af2ee5e32758086a04b84fcce2aa77095e6"


def function_source(path: Path, name: str) -> str:
    return name + "() {" + path.read_text().split(name + "() {", 1)[1].split("\n}", 1)[0] + "\n}\n"


class SecurityNodeDeployTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="security-node-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tools = self.root / "fake-tools"
        self.tools.mkdir()
        self.log = self.root / "tool-calls.log"
        self.make_node(self.tools, "20.19.5", policy_exit=1)
        self.make_tool("uname", "printf '%s\\n' x86_64\n")
        for command in ("apt-get", "systemctl", "curl"):
            self.make_tool(command, f"printf '%s\\n' forbidden-{command} >> \"$NODE_TEST_LOG\"\nexit 99\n")

    def make_tool(self, name: str, body: str, directory: Path | None = None):
        destination = (directory or self.tools) / name
        destination.write_text("#!/bin/sh\n" + body)
        destination.chmod(0o755)
        return destination

    def make_node(self, directory: Path, version: str, *, policy_exit: int, probe_exit: int = 0):
        directory.mkdir(parents=True, exist_ok=True)
        self.make_tool("node", f'''if [ "$1" = -p ]; then
    case "$2" in
        *process.platform*) printf '%s\\n' '{version}|linux|x64' ;;
        *) printf '%s\\n' '{version}' ;;
    esac
    exit {probe_exit}
else
    printf 'policy:%s\\n' "$1" >> "$NODE_TEST_LOG"
    exit {policy_exit}
fi
''', directory)
        self.make_tool("npm", "printf '%s\\n' 11.6.0\n", directory)

    def shell(self, body: str, *arguments: str, source: str | None = None):
        program = (source or 'source "$1"\n') + "shift\n"
        return subprocess.run(
            ["bash", "-c", program + body, "node-security-test", str(RELEASE), *arguments],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
            env={"PATH": f"{self.tools}:/usr/bin:/bin", "NODE_TEST_LOG": str(self.log), "LANG": "C.UTF-8", "TZ": "UTC"},
        )

    def bundle(self, version="24.22.1") -> Path:
        bundle = self.root / ".local/tools" / TOP
        self.make_node(bundle / "bin", version, policy_exit=0)
        (bundle / ".verified-archive-sha256").write_text(PUBLIC_SHA + "\n")
        return bundle

    def archive(self, entries):
        archive = self.root / "fixture.tar.xz"
        with tarfile.open(archive, "w:xz") as output:
            for name, kind, data in entries:
                info = tarfile.TarInfo(name)
                info.mode = 0o755
                info.type = kind
                if kind == tarfile.SYMTYPE:
                    info.linkname = data
                    output.addfile(info)
                else:
                    info.size = len(data)
                    output.addfile(info, io.BytesIO(data))
        target = self.root / "extracted"
        target.mkdir(mode=0o700)
        return archive, sha256(archive.read_bytes()).hexdigest(), target

    def unpack(self, archive: Path, digest: str, target: Path):
        return self.shell('unpack_local_node_archive "$1" "$2" "$3" "$4"', str(archive), digest, TOP, str(target))

    def test_preflight_rejects_previously_accepted_unsafe_nodes_through_shared_policy(self):
        for version in ("20.19.5", "22.12.0", "22.23.3", "24.22.0", "26.12.0", "27.0.0"):
            with self.subTest(version=version):
                self.make_node(self.tools, version, policy_exit=1)
                self.assertNotEqual(self.shell("local_node_version_ok").returncode, 0)
        self.assertIn(str(ROOT / "frontend/scripts/check-node-security-baseline.mjs"), self.log.read_text())

    def test_existing_secure_runtime_is_preserved_without_install_or_global_writes(self):
        self.make_node(self.tools, "24.22.1", policy_exit=0)
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime; command -v node', str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.tools / "node"))
        self.assertFalse((self.root / ".local").exists())
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_verified_cache_is_selected_only_for_this_process_and_child_shells(self):
        bundle = self.bundle()
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime; command -v node; bash -c "command -v node"', str(self.root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(bundle / "bin/node")] * 2)
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_cache_version_mismatch_is_rejected_without_overwriting_existing_bundle(self):
        bundle = self.bundle("24.22.0")
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not overwritten", result.stderr)
        self.assertTrue((bundle / "bin/node").exists())
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_cache_probe_failure_cannot_pass_with_valid_looking_stdout(self):
        bundle = self.bundle()
        self.make_node(bundle / "bin", "24.22.1", policy_exit=0, probe_exit=7)
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not overwritten", result.stderr)
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_lock_redirection_failure_cannot_reuse_an_inherited_descriptor(self):
        self.bundle()
        (self.root / ".local/tools/.node-install.lock").mkdir()
        protected = self.root / "inherited-descriptor.txt"
        protected.write_text("preserve inherited descriptor")
        result = self.shell('exec 9>>"$2"; LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root), str(protected))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid Node installer lock", result.stderr)
        self.assertEqual(protected.read_text(), "preserve inherited descriptor")
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_lock_hardlink_is_rejected_without_truncating_its_other_name(self):
        self.bundle()
        protected = self.root / "unrelated-file.txt"
        protected.write_text("preserve unrelated hardlinked fixture")
        protected.chmod(0o600)
        os.link(protected, self.root / ".local/tools/.node-install.lock")
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid Node installer lock identity", result.stderr)
        self.assertEqual(protected.read_text(), "preserve unrelated hardlinked fixture")
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_failed_exec_redirection_stops_even_when_descriptor_nine_was_inherited(self):
        self.bundle()
        protected = self.root / "inherited-descriptor.txt"
        protected.write_text("preserve inherited descriptor")
        result = self.shell('exec 9>>"$2"; exec() { return 1; }; LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root), str(protected))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot open the Node installer lock", result.stderr)
        self.assertEqual(protected.read_text(), "preserve inherited descriptor")
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_fifo_lock_is_rejected_before_blocking_open_or_network(self):
        self.bundle()
        os.mkfifo(self.root / ".local/tools/.node-install.lock", mode=0o600)
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid Node installer lock", result.stderr)
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_fifo_archive_is_rejected_by_nonblocking_type_check(self):
        archive = self.root / "archive-fifo"
        os.mkfifo(archive, mode=0o600)
        target = self.root / "extracted"
        target.mkdir(mode=0o700)
        result = self.unpack(archive, PUBLIC_SHA, target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("size/type refused", result.stderr)
        self.assertEqual(list(target.iterdir()), [])

    def test_unaudited_override_and_architecture_fail_before_network(self):
        result = self.shell('LOCAL_PROJECT_DIR="$1"; DEPLOY_NODE_VERSION=22.12.0; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.make_tool("uname", "printf '%s\\n' mips64\n")
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("forbidden-", self.log.read_text())

    def test_failed_download_cannot_continue_and_staging_is_cleaned(self):
        result = self.shell('LOCAL_PROJECT_DIR="$1"; unpack_local_node_archive() { echo UNSAFE-CONTINUATION; }; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("UNSAFE-CONTINUATION", result.stdout)
        self.assertIn("forbidden-curl", self.log.read_text())
        tools = self.root / ".local/tools"
        self.assertFalse((tools / TOP).exists())
        self.assertEqual(sorted(path.name for path in tools.iterdir()), [".node-install.lock"])

    def test_download_digest_mismatch_never_promotes_archive(self):
        self.make_tool("curl", '''while [ "$#" -gt 0 ]; do
    if [ "$1" = --output ]; then shift; printf bad-archive > "$1"; fi
    shift
done
''')
        result = self.shell('LOCAL_PROJECT_DIR="$1"; install_local_node_runtime', str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertFalse((self.root / ".local/tools" / TOP).exists())
        self.assertEqual(sorted(path.name for path in (self.root / ".local/tools").iterdir()), [".node-install.lock"])

    def test_hash_is_checked_before_tar_processing_or_extraction(self):
        archive, _, target = self.archive([(TOP + "/bin/node", tarfile.REGTYPE, b"node fixture")])
        result = self.unpack(archive, "0" * 64, target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SHA-256 mismatch", result.stderr)
        self.assertEqual(list(target.iterdir()), [])

    def test_tiny_authenticated_bundle_allows_internal_npm_symlink(self):
        archive, digest, target = self.archive([
            (TOP + "/bin/node", tarfile.REGTYPE, b"node fixture"),
            (TOP + "/lib/node_modules/npm/bin/npm-cli.js", tarfile.REGTYPE, b"npm fixture"),
            (TOP + "/bin/npm", tarfile.SYMTYPE, "../lib/node_modules/npm/bin/npm-cli.js"),
        ])
        result = self.unpack(archive, digest, target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / TOP / "bin/node").read_bytes(), b"node fixture")
        self.assertEqual((target / TOP / "bin/npm").read_bytes(), b"npm fixture")

    def test_unsafe_archive_entries_and_wrong_root_are_rejected(self):
        cases = [
            ("/outside", tarfile.REGTYPE, b"bad"),
            (TOP + "/../../outside", tarfile.REGTYPE, b"bad"),
            (TOP + "/bin/npm", tarfile.SYMTYPE, "/outside"),
            (TOP + "/bin/npm", tarfile.SYMTYPE, "../../outside"),
            (TOP + "/bin/node", tarfile.LNKTYPE, b""),
            ("node-v24.22.0-linux-x64/bin/node", tarfile.REGTYPE, b"bad"),
        ]
        for entry in cases:
            with self.subTest(entry=entry[0]):
                archive, digest, target = self.archive([entry])
                result = self.unpack(archive, digest, target)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(list(target.iterdir()), [])
                target.rmdir()

    def test_sparse_oversize_archive_is_refused_without_reading_contents(self):
        archive = self.root / "oversize.tar.xz"
        with archive.open("wb") as output:
            output.truncate(80 * 1024 * 1024 + 1)
        target = self.root / "extracted"
        target.mkdir(mode=0o700)
        result = self.unpack(archive, "0" * 64, target)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("size/type refused", result.stderr)
        self.assertEqual(list(target.iterdir()), [])

    def test_legacy_build_keeps_scoped_path_before_signatures_and_child_npm(self):
        self.make_node(self.tools, "24.22.1", policy_exit=0)
        self.make_tool("npm", 'command -v node >> "$NODE_TEST_LOG"\n')
        signature = function_source(ROOT / "deploy.sh", "frontend_build_signature")
        build = function_source(ROOT / "deploy.sh", "build_frontend")
        self.assertIn("check-node-security-baseline.mjs", signature)
        self.assertNotIn("bash -lc", build)
        result = self.shell('''PROJECT_DIR="$1"
FRONTEND_DIR="$1/frontend"
DIST_DIR="$1/mini_app_dist"
DEPLOY_STATE_DIR="$1/state"
FRONTEND_SIGNATURE_FILE="$1/state/signature"
DEPLOY_FORCE_REBUILD=1
mkdir -p "$FRONTEND_DIR" "$DIST_DIR"
print_header() { :; }
frontend_build_signature() { printf fixture-signature; }
run_with_local_resource_guard() { shift; "$@"; }
build_frontend
''', str(self.root), source=build)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [str(self.tools / "node")] * 2)

    def test_bootstrap_exact_digest_bindings_and_no_host_package_mutation(self):
        installer = function_source(RELEASE, "install_local_node_runtime")
        self.assertIn(PUBLIC_SHA, installer)
        self.assertIn("dbabf7a1cbf947e6059ffad2c27b80041db97dea1e553b62afdee7f13fca336e", installer)
        for required in ("--disable", "--proto '=https'", "--max-time 180", "--max-filesize 83886080", 'export PATH="$bundle/bin:$PATH"'):
            self.assertIn(required, installer)
        for forbidden in ("apt-get", "systemctl", "/usr/local/bin", "ln -s"):
            self.assertNotIn(forbidden, installer)
        for path in (RELEASE, ROOT / "deploy.sh"):
            self.assertIn("scripts/check-node-security-baseline.mjs", function_source(path, "frontend_build_signature"))


if __name__ == "__main__":
    unittest.main()
