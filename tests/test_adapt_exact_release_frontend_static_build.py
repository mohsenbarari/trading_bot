"""Focused tests for the controller-only exact-build static manifest adapter.

The fixtures model an already-prepared local exact-release candidate.  They
never invoke npm, unshare, SSH, Object Storage, Docker, or a service.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ADAPTER = _load(
    "adapt_exact_release_frontend_static_build_test",
    ROOT / "scripts" / "adapt_exact_release_frontend_static_build.py",
)
BINDING = _load(
    "exact_release_static_adapter_campaign_binding_test",
    ROOT / "scripts" / "webapp_fi_source_campaign_binding.py",
)

CAMPAIGN = "exact-static-adapter-20260730"
RELEASE = "a" * 40
TREE = "b" * 40
REVISION = "f2c7d8e9a0b1"
CONTROL_COMMIT = "c" * 40
CONTROL_TREE = "d" * 40


def _canonical(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii") + b"\n"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_private(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


@unittest.skipUnless(os.geteuid() == 0, "adapter enforces root-only controller inputs")
class ExactReleaseStaticAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="exact-static-adapter-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root.chmod(0o700)
        self.candidate_parent = self.root / "candidates"
        self.candidate_parent.mkdir(mode=0o700)
        self.candidate = self.candidate_parent / "one"
        self.candidate.mkdir(mode=0o700)
        self.output = self.candidate / ADAPTER.EXACT_BUILD_OUTPUT_DIRECTORY_NAME
        self.output.mkdir(mode=0o700)
        self._write_output("index.html", b"<!doctype html><title>fixture</title>\n")
        self._write_output("assets/app.js", b"console.log('fixture');\n")
        self.binding = self._write_binding()
        self.receipt = self._write_receipt()
        self.destination_parent = self.root / "manifests"
        self.destination_parent.mkdir(mode=0o700)
        self.destination = self.destination_parent / "expected-static-assets.json"

    def _write_output(self, relative: str, payload: bytes) -> None:
        path = self.output / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for parent in (path.parent,):
            parent.chmod(0o700)
        path.write_bytes(payload)
        path.chmod(0o600)

    def _files(self) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        for path in sorted(self.output.rglob("*")):
            if path.is_file():
                payload = path.read_bytes()
                values.append(
                    {
                        "path": path.relative_to(self.output).as_posix(),
                        "sha256": _sha(payload),
                        "bytes": len(payload),
                    }
                )
        return values

    def _write_binding(self) -> Path:
        value = BINDING.build_campaign_binding(
            campaign_id=CAMPAIGN,
            application_release_sha=RELEASE,
            application_release_tree=TREE,
            expected_alembic_revision=REVISION,
            control_commit=CONTROL_COMMIT,
            control_tree=CONTROL_TREE,
        )
        path = self.root / "campaigns" / CAMPAIGN / "webapp-fi-source" / "campaign-binding.json"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in (self.root / "campaigns", path.parent.parent, path.parent):
            directory.chmod(0o700)
        return _write_private(path, BINDING.canonical_json_bytes(value) + b"\n")

    def _write_receipt(self) -> Path:
        files = self._files()
        output = {
            "files_sha256": _sha(ADAPTER.canonical_json_bytes(files)),
            "file_count": len(files),
            "bytes": sum(item["bytes"] for item in files),
            "files": files,
        }
        unsigned: dict[str, object] = {
            "schema": ADAPTER.EXACT_BUILD_SCHEMA,
            "status": "prepared",
            "release_sha": RELEASE,
            "release_tree": TREE,
            "source": {"tree_file_count": 3, "repository_path_sha256": "1" * 64},
            "toolchain": {
                "fixed_policy_sha256": "2" * 64,
                "git": {"path_sha256": "3" * 64, "sha256": "4" * 64, "version": "2.43.0"},
                "node": {"path_sha256": "5" * 64, "sha256": "6" * 64, "version": "20.19.5"},
                "npm": {
                    "path_sha256": "7" * 64,
                    "sha256": "8" * 64,
                    "version": "11.12.1",
                    "runtime_path_sha256": "9" * 64,
                    "runtime_tree_sha256": "a" * 64,
                },
                "sandbox": {
                    "python": {"path_sha256": "b" * 64, "sha256": "c" * 64, "version": "3.12.3"},
                    "unshare": {"path_sha256": "d" * 64, "sha256": "e" * 64, "version": "2.40.2"},
                    "setpriv": {"path_sha256": "f" * 64, "sha256": "0" * 64, "version": "2.40.2"},
                    "mount": {"path_sha256": "1" * 64, "sha256": "2" * 64, "version": "2.40.2"},
                    "policy_sha256": "3" * 64,
                },
            },
            "lock": {
                "package_json_sha256": "4" * 64,
                "package_json_bytes": 12,
                "package_lock_sha256": "5" * 64,
                "package_lock_bytes": 18,
            },
            "offline_dependency_input": {
                "archive_sha256": "6" * 64,
                "archive_bytes": 1024,
                "files_sha256": "7" * 64,
                "file_count": 1,
                "bytes": 512,
            },
            "runtime_closure": {
                "manifest_sha256": "8" * 64,
                "setpriv_sha256": "9" * 64,
                "sh_sha256": "a" * 64,
                "env_sha256": "b" * 64,
            },
            "build_environment_sha256": "c" * 64,
            "sandbox_preflight": {"mount_network_pid_namespace": "passed", "privilege_drop": "passed"},
            "network_action": False,
            "object_storage_action": False,
            "ssh_action": False,
            "docker_action": False,
            "service_changed": False,
            "current_changed": False,
            "receipt_authority": {
                "unsigned": True,
                "provenance": "local-preparation-only-not-transport-provenance",
                "integration_status": "blocked-pending-external-controller-signature",
            },
            "transport_authority": {
                "local_receipt_only": True,
                "external_controller_signature_required": True,
                "transport_or_install_authorized": False,
            },
            "release_archive": {"sha256": "d" * 64, "bytes": 1024},
            "materialized_source": {
                "files_sha256": "e" * 64,
                "file_count": 3,
                "package_json_sha256": "f" * 64,
                "package_lock_sha256": "0" * 64,
            },
            "build": {
                "environment_sha256": "c" * 64,
                "lifecycle_scripts_enabled": False,
                "mount_namespace_required": True,
                "network_namespace_required": True,
                "pid_namespace_required": True,
                "privilege_drop_required": True,
                "rlimit_nproc": 64,
                "rlimit_as_bytes": 1024,
                "rlimit_cpu_seconds": 60,
                "rlimit_fsize_bytes": 1024 * 1024,
            },
            "output": output,
        }
        value = {**unsigned, "receipt_sha256": _sha(ADAPTER.canonical_json_bytes(unsigned))}
        return _write_private(self.candidate / ADAPTER.EXACT_BUILD_RECEIPT_NAME, _canonical(value))

    def _adapt(self, *, apply: bool) -> dict[str, object]:
        return ADAPTER.derive_expected_static_assets_manifest(
            exact_build_candidate=self.candidate,
            exact_build_receipt=self.receipt,
            campaign_binding_path=self.binding,
            destination=self.destination,
            apply=apply,
        )

    def test_plan_and_create_only_manifest_bind_exact_build_to_campaign(self) -> None:
        plan = self._adapt(apply=False)
        result = self._adapt(apply=True)
        manifest_payload = self.destination.read_bytes()
        manifest = json.loads(manifest_payload.decode("ascii"))

        self.assertEqual("planned", plan["status"])
        self.assertFalse(plan["network_action"])
        self.assertFalse(plan["object_storage_action"])
        self.assertEqual("prepared", result["status"])
        self.assertEqual(CAMPAIGN, manifest["campaign_id"])
        self.assertEqual(RELEASE, manifest["application"]["release_sha"])
        self.assertEqual(TREE, manifest["application"]["release_tree"])
        self.assertEqual(REVISION, manifest["application"]["expected_alembic_revision"])
        self.assertEqual(CONTROL_COMMIT, manifest["tooling"]["control_commit"])
        self.assertEqual(["assets/app.js", "index.html"], [item["path"] for item in manifest["files"]])
        self.assertEqual(manifest_payload, ADAPTER.canonical_json_bytes(manifest) + b"\n")
        self.assertEqual(0o600, self.destination.stat().st_mode & 0o777)
        self.assertNotIn(b"://", manifest_payload)
        self.assertNotIn(b"presigned", manifest_payload.lower())

    def test_output_drift_blocks_before_manifest_creation(self) -> None:
        self._write_output("index.html", b"changed after exact receipt\n")
        with self.assertRaisesRegex(ADAPTER.ExactReleaseStaticAdapterError, "differs from its receipt"):
            self._adapt(apply=True)
        self.assertFalse(self.destination.exists())

    def test_campaign_binding_mismatch_blocks_before_manifest_creation(self) -> None:
        wrong = BINDING.build_campaign_binding(
            campaign_id=CAMPAIGN,
            application_release_sha="e" * 40,
            application_release_tree=TREE,
            expected_alembic_revision=REVISION,
            control_commit=CONTROL_COMMIT,
            control_tree=CONTROL_TREE,
        )
        _write_private(self.binding, BINDING.canonical_json_bytes(wrong) + b"\n")
        with self.assertRaisesRegex(ADAPTER.ExactReleaseStaticAdapterError, "not bound to the canonical campaign"):
            self._adapt(apply=True)
        self.assertFalse(self.destination.exists())

    def test_nonlocal_transport_authority_blocks_before_manifest_creation(self) -> None:
        value = json.loads(self.receipt.read_text(encoding="ascii"))
        value["transport_authority"]["transport_or_install_authorized"] = True
        unsigned = {key: item for key, item in value.items() if key != "receipt_sha256"}
        value["receipt_sha256"] = _sha(ADAPTER.canonical_json_bytes(unsigned))
        _write_private(self.receipt, _canonical(value))
        with self.assertRaisesRegex(ADAPTER.ExactReleaseStaticAdapterError, "transport authority is invalid"):
            self._adapt(apply=True)
        self.assertFalse(self.destination.exists())

    def test_manifest_destination_is_create_only(self) -> None:
        self._adapt(apply=True)
        with self.assertRaisesRegex(ADAPTER.ExactReleaseStaticAdapterError, "must be a new child"):
            self._adapt(apply=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
