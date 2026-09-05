"""Transactional installer tests using only disposable roots."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import install_two_site_agent_gateway as installer
from scripts.two_site_agent_context import PROTECTED_SOURCE


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='two-site-installer-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.git('init', '-q', '-b', installer.BRANCH)
        self.git('config', 'user.name', 'Installer Test')
        self.git('config', 'user.email', 'installer@test.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        canonical = Path(__file__).resolve().parents[1]
        for relative in PROTECTED_SOURCE:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(canonical / relative, target)
        guard = self.root / 'scripts/check_memory_secrets.py'
        guard.write_text('raise SystemExit(0)\n')
        (self.root / '.gitignore').write_text('.local/\n')
        self.git('add', '--all')
        self.git('commit', '-qm', 'reviewed gateway')
        self.accepted = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('branch', 'main')
        hook = self.root / '.git/hooks/pre-commit'
        hook.write_text(
            '#!/bin/sh\nset -eu\n\ngit diff --cached --check\n'
            'git show main:scripts/check_memory_secrets.py | python3 -\n'
        )
        hook.chmod(0o755)
        self.stage_root = self.base / 'worktrees'
        self.stage_root.mkdir()
        runtime = self.root / '.local/two-site-refactor/antigravity-runtime-sessions.json'
        runtime.parent.mkdir(parents=True)
        true = Path('/usr/bin/true').resolve()
        runtime.write_text(json.dumps({
            'execution_coordinator': '11111111-1111-4111-8111-111111111111',
            'cli_sha256': hashlib.sha256(true.read_bytes()).hexdigest(),
        }))
        self.receipt = self.base / 'governance.json'
        self.receipt.write_text(json.dumps({
            'schema_version': '1.0', 'reviewer_authority': 'CODEX_FINAL_REVIEWER',
            'receipt_type': 'EXECUTION_GOVERNANCE_AMENDMENT', 'amendment_id': 'INSTALL-TEST',
            'integration_base_sha': 'b' * 40, 'reviewed_head_sha': self.accepted,
            'reviewed_at_utc': '2026-09-05T00:00:00Z', 'verdict': 'APPROVE',
            'scope': {'changed_paths_within_scope': True, 'stage_state_unchanged': True,
                      'unauthorized_external_mutation': 'NONE'},
            'findings': [],
            'verification': [{'check': 'fixture', 'result': 'PASS', 'evidence_uri': 'fixture'}],
            'decision': {'accepted_commit': self.accepted, 'stage_completion_granted': False,
                         'residual_risk': 'fixture', 'conditions': []},
        }))
        self.paths = {
            'INSTALL_ROOT': self.base / 'libexec/gateway.d',
            'LAUNCHER': self.base / 'libexec/gateway',
            'RECOVERY_LAUNCHER': self.base / 'libexec/recover',
            'STATE_PARENT': self.base / 'state',
            'STATE_ROOT': self.base / 'state/controller',
            'MANIFEST': self.base / 'state/manifest.json',
            'JOURNAL': self.base / 'state/install-journal.json',
        }

    def git(self, *args):
        return subprocess.run(
            ['/usr/bin/git', '-C', str(self.root), *args], check=True,
            capture_output=True, text=True,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
                 'HOME': os.environ.get('HOME', '/root')},
        )

    def install(self):
        true = Path('/usr/bin/true').resolve()
        with patch.object(installer, 'isolated_runtime', return_value=True):
            return installer.install(
                self.root.resolve(), self.accepted,
                '11111111-1111-4111-8111-111111111111', self.receipt.resolve(),
                self.stage_root.resolve(), true, true,
            )

    def test_install_is_complete_and_recovery_launcher_is_present(self):
        with patch.multiple(installer, **self.paths):
            manifest = self.install()
        self.assertEqual(self.accepted, manifest['accepted_sha'])
        self.assertTrue(self.paths['LAUNCHER'].is_file())
        self.assertTrue(self.paths['RECOVERY_LAUNCHER'].is_file())
        self.assertTrue((self.paths['STATE_ROOT'] / 'governance-receipt.json').is_file())
        self.assertFalse(self.paths['JOURNAL'].exists())

    def test_installer_direct_cli_help_loads_from_outside_repository(self):
        poison = self.base / 'poison'
        scripts = poison / 'scripts'
        scripts.mkdir(parents=True)
        marker = self.base / 'poison-executed'
        source = Path(__file__).resolve().parents[1] / 'scripts/install_two_site_agent_gateway.py'
        script = scripts / source.name
        shutil.copyfile(source, script)
        payload = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('poison')\n"
        for relative in (
            'yaml.py', 'sitecustomize.py', 'scripts/yaml.py', 'scripts/sitecustomize.py',
            'scripts/two_site_agent_context.py', 'scripts/two_site_agent_sandbox.py',
        ):
            target = poison / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload)
        result = subprocess.run(
            [sys.executable, '-I', str(script), '--help'], cwd=poison,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('--accepted-sha', result.stdout)
        self.assertFalse(marker.exists())

    def test_installer_catalog_matches_runtime_without_importing_it_at_bootstrap(self):
        self.assertEqual(tuple(PROTECTED_SOURCE), tuple(installer.PROTECTED_SOURCE))
        source = (Path(__file__).resolve().parents[1]
                  / 'scripts/install_two_site_agent_gateway.py').read_text()
        tree = ast.parse(source)
        repository_imports = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and isinstance(node.module, str) and node.module.startswith('scripts')
        ]
        bootstrap_path_mutations = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == 'insert'
            and isinstance(node.func.value, ast.Attribute)
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == 'sys' and node.func.value.attr == 'path'
        ]
        self.assertEqual([], repository_imports)
        self.assertEqual([], bootstrap_path_mutations)

    def test_library_entry_and_nonaccepted_installer_bytes_fail_closed(self):
        true = Path('/usr/bin/true').resolve()
        arguments = (
            self.root.resolve(), self.accepted,
            '11111111-1111-4111-8111-111111111111', self.receipt.resolve(),
            self.stage_root.resolve(), true, true,
        )
        with self.assertRaisesRegex(installer.InstallError, 'isolated Python'):
            installer.install(*arguments)

        tampered = self.base / 'tampered-installer.py'
        tampered.write_bytes(Path(installer.__file__).read_bytes() + b'\n# tampered\n')
        with patch.object(installer, 'isolated_runtime', return_value=True), patch.object(
            installer, '__file__', str(tampered),
        ):
            with self.assertRaisesRegex(installer.InstallError, 'exact accepted Git blob'):
                installer.install(*arguments)

    def test_git_materialized_exact_installer_blob_passes_provenance(self):
        materialized = self.base / 'reviewed-installer.py'
        materialized.write_text(self.git(
            'show', self.accepted + ':scripts/install_two_site_agent_gateway.py').stdout)
        with patch.object(installer, '__file__', str(materialized)):
            installer.validate_installer_provenance(self.root.resolve(), self.accepted)

    def test_late_failure_rolls_back_and_journal_allows_exact_retry(self):
        original = installer.atomic_bytes

        def fail_launcher(path, content, mode):
            if path == self.paths['LAUNCHER']:
                raise OSError('injected late failure')
            return original(path, content, mode)

        with patch.multiple(installer, **self.paths):
            with patch.object(installer, 'atomic_bytes', side_effect=fail_launcher):
                with self.assertRaises(OSError):
                    self.install()
            self.assertFalse(self.paths['LAUNCHER'].exists())
            self.assertFalse(self.paths['RECOVERY_LAUNCHER'].exists())
            self.assertFalse(self.paths['MANIFEST'].exists())
            self.assertFalse((self.paths['INSTALL_ROOT'] / self.accepted).exists())
            self.assertTrue(self.paths['JOURNAL'].exists())
            manifest = self.install()
        self.assertEqual(self.accepted, manifest['accepted_sha'])
        self.assertTrue(self.paths['LAUNCHER'].exists())
        self.assertFalse(self.paths['JOURNAL'].exists())

    def test_post_launcher_crash_journal_is_cleared_only_after_full_byte_validation(self):
        with patch.multiple(installer, **self.paths):
            self.install()
            installer.atomic_bytes(self.paths['JOURNAL'], json.dumps({
                'schema_version': '1.0', 'accepted_sha': self.accepted,
                'root': str(self.root.resolve()),
            }, sort_keys=True, separators=(',', ':')).encode() + b'\n', 0o600)
            recovered = self.install()
        self.assertEqual(self.accepted, recovered['accepted_sha'])
        self.assertFalse(self.paths['JOURNAL'].exists())


if __name__ == '__main__':
    unittest.main()
