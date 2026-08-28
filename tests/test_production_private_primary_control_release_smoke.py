"""Isolated --help/import smoke for PRIVATE_PRIMARY control-release tools."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT_ENV = REPO_ROOT / "config/unit-test.env.example"
CONTROL_RELEASE_TOOLS = (
    "scripts/audit_production_market_catchup.py",
    "scripts/backup_market_pipeline_archive.py",
    "scripts/build_production_private_primary_choreography_plan.py",
    "scripts/check_production_coin_inference_readiness.py",
    "scripts/crypt_market_pipeline_backup.py",
    "scripts/cutover_telegram_delivery_queue_production.py",
    "scripts/migrate_market_pipeline_archive.py",
    "scripts/observe_production_private_primary.py",
    "scripts/prepare_production_private_primary_manifest.py",
    "scripts/promote_production_private_primary_product.py",
    "scripts/quiesce_production_legacy_market_collectors.py",
    "scripts/reconcile_estimator_snapshot_publication_outbox.py",
    "scripts/rollout_market_pipeline_shadow.py",
    "scripts/run_fenced_production_deploy.py",
    "scripts/run_production_private_primary_choreography.py",
    "scripts/run_release_bound_product_readiness.py",
    "scripts/update_production_coin_inference_source.py",
    "scripts/upgrade_market_pipeline_bluegreen.py",
    "scripts/verify_production_private_primary_promotion.py",
)


class ControlReleaseHelpSmokeTests(unittest.TestCase):
    def test_help_and_import_survive_hostile_cwd_path_and_git_env(self) -> None:
        with tempfile.TemporaryDirectory(prefix="control-release-smoke-") as temporary:
            isolated = Path(temporary)
            isolated.chmod(0o700)
            hostile_bin = isolated / "bin"
            (isolated / "scripts").mkdir()
            (isolated / "scripts" / "__init__.py").write_text(
                "raise RuntimeError('hostile cwd scripts imported')\n",
                encoding="utf-8",
            )
            hostile_bin.mkdir()
            for name in ("python3", "python", "ssh", "git"):
                decoy = hostile_bin / name
                decoy.write_text(
                    "#!/bin/sh\necho hostile-control-release-decoy >&2\nexit 41\n",
                    encoding="utf-8",
                )
                decoy.chmod(0o700)
            environment = {
                "HOME": os.environ.get("HOME", "/root"),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": f"{hostile_bin}:/usr/bin:/bin",
                "PYTHONPATH": str(REPO_ROOT),
                "APP_ENV_FILE": str(UNIT_ENV),
                "GIT_DIR": str(isolated / "hostile.git"),
                "GIT_WORK_TREE": str(isolated / "hostile-tree"),
            }
            for relative in CONTROL_RELEASE_TOOLS:
                tool = REPO_ROOT / relative
                self.assertTrue(tool.is_file(), relative)
                completed = subprocess.run(
                    ["/usr/bin/python3", str(tool), "--help"],
                    cwd=str(isolated),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                )
                output = completed.stdout + completed.stderr
                self.assertIn(
                    completed.returncode,
                    {0, 2},
                    f"{relative} --help failed: {output[-400:]!r}",
                )
                self.assertNotIn(b"hostile-control-release-decoy", output)
                self.assertNotIn(b"hostile cwd scripts imported", output)
                compiled = subprocess.run(
                    ["/usr/bin/python3", "-m", "py_compile", str(tool)],
                    cwd=str(isolated),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                )
                compiled_output = compiled.stdout + compiled.stderr
                self.assertEqual(
                    compiled.returncode,
                    0,
                    f"{relative} compile failed: {compiled_output[-400:]!r}",
                )
                self.assertNotIn(b"hostile-control-release-decoy", compiled_output)
