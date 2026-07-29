from __future__ import annotations

import base64
import csv
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/build_production_shadow_controller_runtime_closure.py"
SPEC = importlib.util.spec_from_file_location(
    "scripts.build_production_shadow_controller_runtime_closure",
    MODULE_PATH,
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
VERIFY = MODULE.VERIFY
BOOTSTRAP_PATH = ROOT / "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py"
BOOTSTRAP_SPEC = importlib.util.spec_from_file_location(
    "production_shadow_convergence_source_set_runtime_bootstrap_for_builder_tests",
    BOOTSTRAP_PATH,
)
assert BOOTSTRAP_SPEC is not None and BOOTSTRAP_SPEC.loader is not None
BOOTSTRAP = importlib.util.module_from_spec(BOOTSTRAP_SPEC)
sys.modules[BOOTSTRAP_SPEC.name] = BOOTSTRAP
BOOTSTRAP_SPEC.loader.exec_module(BOOTSTRAP)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def wheel_bytes(
    *,
    distribution: str,
    version: str,
    requires: tuple[str, ...],
    members: dict[str, bytes],
) -> bytes:
    dist_info = f"{distribution}-{version}.dist-info"
    payloads = dict(members)
    metadata = (
        f"Metadata-Version: 2.1\nName: {distribution}\nVersion: {version}\n"
        + "".join(f"Requires-Dist: {value}\n" for value in requires)
        + "\n"
    ).encode("utf-8")
    payloads[f"{dist_info}/METADATA"] = metadata
    record_path = f"{dist_info}/RECORD"
    rows: list[list[str]] = []
    for path, payload in sorted(payloads.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).decode().rstrip("=")
        rows.append([path, f"sha256={digest}", str(len(payload))])
    rows.append([record_path, "", ""])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    payloads[record_path] = output.getvalue().encode("utf-8")
    binary = io.BytesIO()
    with zipfile.ZipFile(binary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, payload in sorted(payloads.items()):
            archive.writestr(path, payload)
    return binary.getvalue()


def mutate_wheel_member(payload: bytes, member: str) -> bytes:
    source = io.BytesIO(payload)
    target = io.BytesIO()
    with zipfile.ZipFile(source) as read_archive, zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED
    ) as write_archive:
        for info in read_archive.infolist():
            value = read_archive.read(info)
            if info.filename == member:
                value += b"tampered"
            write_archive.writestr(info.filename, value)
    return target.getvalue()


class BuilderFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="controller-runtime-builder-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release = self.root / "release"
        self.wheelhouse = self.root / "trusted-wheel-input"
        self.plan_root = self.root / "held-plans"
        self.output = self.root / "output"
        self.receipt_path = self.root / "trusted-wheel-input-receipt.json"
        self.release.mkdir(mode=0o700)
        self.wheelhouse.mkdir(mode=0o700)
        self.plan_root.mkdir(mode=0o700)
        self.uid = os.getuid()
        self.campaign_id = "7fb08095-7a9e-4a92-9fa9-3f9a301b2944"
        self.wheels = self._make_wheels()
        self.contract = tuple(
            {
                "name": name,
                "version": version,
                "wheel": filename,
                "sha256": sha256(payload),
            }
            for name, version, filename, payload in self.wheels
        )
        self.origins = {
            "_cffi_backend": "_cffi_backend.cpython-312-x86_64-linux-gnu.so",
            "cffi": "cffi/__init__.py",
            "cryptography": "cryptography/__init__.py",
            "cryptography.hazmat.bindings._rust": "cryptography/hazmat/bindings/_rust.abi3.so",
            "pycparser": "pycparser/__init__.py",
        }
        self._write_wheels()
        self._write_release_files()
        self._initialize_release_git()

    def _make_wheels(self) -> tuple[tuple[str, str, str, bytes], ...]:
        return (
            (
                "cffi",
                "2.1.0",
                "cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
                wheel_bytes(
                    distribution="cffi",
                    version="2.1.0",
                    requires=("pycparser",),
                    members={
                        "_cffi_backend.cpython-312-x86_64-linux-gnu.so": b"synthetic cffi extension",
                        "cffi/__init__.py": b"# synthetic cffi\n",
                    },
                ),
            ),
            (
                "cryptography",
                "41.0.7",
                "cryptography-41.0.7-cp37-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
                wheel_bytes(
                    distribution="cryptography",
                    version="41.0.7",
                    requires=("cffi (>=1.12)",),
                    members={
                        "cryptography/__init__.py": b"# synthetic cryptography\n",
                        "cryptography/hazmat/bindings/_rust.abi3.so": b"synthetic rust extension",
                    },
                ),
            ),
            (
                "pycparser",
                "3.0",
                "pycparser-3.0-py3-none-any.whl",
                wheel_bytes(
                    distribution="pycparser",
                    version="3.0",
                    requires=(),
                    members={"pycparser/__init__.py": b"# synthetic pycparser\n"},
                ),
            ),
        )

    def _write(self, root: Path, relative: str, value: bytes) -> Path:
        path = root / relative
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        cursor = path.parent
        while cursor != root.parent:
            cursor.chmod(0o700)
            if cursor == root:
                break
            cursor = cursor.parent
        path.write_bytes(value)
        path.chmod(0o600)
        return path

    def _write_wheels(self) -> None:
        for _name, _version, filename, payload in self.wheels:
            self._write(self.wheelhouse, filename, payload)

    def _policy(self) -> dict[str, object]:
        return {
            "schema": MODULE.POLICY_SCHEMA,
            "namespace": VERIFY.RUNTIME_NAMESPACE,
            "python": {
                "implementation": "cpython",
                "major": 3,
                "minor": 12,
                "architecture": "x86_64",
            },
            "packages": list(self.contract),
            "site_packages": {
                "path": VERIFY.SITE_PACKAGES_DIRECTORY,
                "import_origins": self.origins,
            },
            "wheel_input": {
                "schema": MODULE.EXTERNAL_INPUT_SCHEMA,
                "status": "external-independent-held-plan-required",
                "held_root_only_plan_required": True,
                "caller_supplied_digest_allowed": False,
                "writer_witness_assets_used": False,
            },
        }

    def _write_release_files(self) -> None:
        policy = VERIFY.canonical_json_bytes(self._policy())
        self._write(self.release, MODULE.POLICY_RELATIVE, policy)
        requirements = b"cffi==2.1.0\ncryptography==41.0.7\npycparser==3.0\n"
        self._write(self.release, MODULE.REQUIREMENTS_RELATIVE, requirements)
        wheel_manifest = b"".join(
            f"{record['sha256']}  {record['wheel']}\n".encode("ascii")
            for record in self.contract
        )
        self._write(self.release, MODULE.WHEELHOUSE_RELATIVE, wheel_manifest)
        for relative in VERIFY.CONTROL_SOURCE_PATHS:
            source = b"# synthetic control source\n" if relative.endswith(".py") else b"#!/bin/sh\nexit 0\n"
            self._write(self.release, relative, source)
        self._write(self.release, "scripts/__init__.py", b"# explicit source-graph package\n")
        self.policy_sha = sha256(policy)
        self.wheelhouse_sha = sha256(wheel_manifest)

    def _initialize_release_git(self) -> None:
        def git(*args: str) -> None:
            completed = subprocess.run(
                ["/usr/bin/git", "-C", str(self.release), *args],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

        git("init", "--quiet")
        git("config", "user.email", "test@example.invalid")
        git("config", "user.name", "Synthetic Test")
        git("add", ".")
        git("commit", "--quiet", "-m", "synthetic release")
        self.release_sha = subprocess.check_output(
            ["/usr/bin/git", "-C", str(self.release), "rev-parse", "HEAD"], text=True
        ).strip()
        self.release_tree_sha = subprocess.check_output(
            ["/usr/bin/git", "-C", str(self.release), "rev-parse", "HEAD^{tree}"], text=True
        ).strip()
        git("checkout", "--quiet", "--detach")

    def input_receipt(self) -> tuple[Path, str]:
        provenance = {
            record["wheel"]: MODULE._validate_wheel(payload, contract=record)
            for record, (_name, _version, _filename, payload) in zip(self.contract, self.wheels)
        }
        document: dict[str, object] = {
            "schema": MODULE.WHEEL_INPUT_RECEIPT_SCHEMA,
            "status": MODULE.WHEEL_INPUT_RECEIPT_STATUS,
            "release": {"commit_sha": self.release_sha, "tree_sha": self.release_tree_sha},
            "source_policy_sha256": self.policy_sha,
            "controller_wheelhouse_sha256": self.wheelhouse_sha,
            "wheels": [
                {
                    "wheel": record["wheel"],
                    "archive_sha256": record["sha256"],
                    "record_sha256": provenance[record["wheel"]].record_sha256,
                    "members_sha256": provenance[record["wheel"]].members_sha256,
                }
                for record in self.contract
            ],
        }
        document["input_receipt_sha256"] = MODULE._sha256(document)
        raw = VERIFY.canonical_json_bytes(document)
        self.receipt_path.write_bytes(raw)
        self.receipt_path.chmod(0o600)
        return self.receipt_path, sha256(raw)

    def write_held_plan(self, receipt_sha256: str) -> Path:
        directory = self.plan_root / self.campaign_id
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        path = directory / VERIFY.HELD_RUNTIME_PLAN_FILENAME

        def write_document(blobs: dict[str, str]) -> None:
            document = {
                "schema": VERIFY.HELD_RUNTIME_PLAN_SCHEMA,
                "campaign_id": self.campaign_id,
                "release": {"commit_sha": self.release_sha, "tree_sha": self.release_tree_sha},
                "source_policy_sha256": self.policy_sha,
                "controller_wheelhouse_sha256": self.wheelhouse_sha,
                "wheel_input_receipt_sha256": receipt_sha256,
                "closure_scope": VERIFY.PRE_RUNTIME_CLOSURE_SCOPE,
                "bootstrap_path": BOOTSTRAP.BOOTSTRAP_SOURCE,
                "required_blobs": blobs,
            }
            path.write_bytes(VERIFY.canonical_json_bytes(document))
            path.chmod(0o600)

        write_document(
            {
                relative: sha256((self.release / relative).read_bytes())
                for relative in BOOTSTRAP.STATIC_REQUIRED_BLOBS
            }
        )
        release_descriptor = os.open(
            self.release,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        plan_descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            release_identity = BOOTSTRAP.capture_held_directory(
                release_descriptor,
                label="synthetic builder release",
                expected_uid=self.uid,
            )
            plan = BOOTSTRAP.read_held_runtime_plan_fd(plan_descriptor, expected_uid=self.uid)
            tracked = BOOTSTRAP._verify_exact_git_state(release_identity, plan)  # noqa: SLF001
            graph = BOOTSTRAP.discover_reachable_controller_sources(
                release_identity,
                tracked_blobs=tracked,
                expected_uid=self.uid,
            )
        finally:
            os.close(plan_descriptor)
            os.close(release_descriptor)
        write_document(
            {
                relative: sha256((self.release / relative).read_bytes())
                for relative in graph.paths
            }
        )
        return path

    def held_capability(self):
        path = self.plan_root / self.campaign_id / VERIFY.HELD_RUNTIME_PLAN_FILENAME
        release_descriptor = os.open(
            self.release,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        plan_descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        bootstrap_descriptor = os.open(
            self.release / BOOTSTRAP.BOOTSTRAP_SOURCE,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            inputs = BOOTSTRAP.verify_held_bootstrap_inputs(
                release_descriptor=release_descriptor,
                plan_descriptor=plan_descriptor,
                bootstrap_descriptor=bootstrap_descriptor,
                expected_uid=self.uid,
            )
            capability = BOOTSTRAP.activate_verified_held_bootstrap(inputs, expected_uid=self.uid)
            BOOTSTRAP.register_held_bootstrap_types(VERIFY)
        finally:
            os.close(bootstrap_descriptor)
            os.close(plan_descriptor)
            os.close(release_descriptor)
        self.addCleanup(capability.close)
        return capability

    def patch_contract(self):
        return mock.patch.multiple(
            VERIFY,
            REQUIRED_PACKAGES=self.contract,
            REQUIRED_IMPORT_ORIGINS=self.origins,
        )

    def prepare(self):
        receipt, digest = self.input_receipt()
        self.write_held_plan(digest)
        with self.patch_contract():
            return MODULE.prepare_runtime_closure(
                release_root=self.release,
                campaign_id=self.campaign_id,
                wheelhouse=self.wheelhouse,
                wheel_input_receipt=receipt,
                trusted_plan_root=self.plan_root,
                expected_uid=self.uid,
            )


class RuntimeClosureBuilderTests(BuilderFixture):
    def test_direct_absolute_path_cli_fails_closed_without_package_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="controller-runtime-builder-direct-") as temporary:
            completed = subprocess.run(
                [sys.executable, str(MODULE_PATH)],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 69)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(
            completed.stderr,
            "blocked: controller runtime builder requires a trusted scripts package context\n",
        )
        self.assertNotIn("ModuleNotFoundError", completed.stderr)

    def test_build_cli_requires_root_and_has_no_uid_override(self) -> None:
        with mock.patch.object(MODULE.os, "geteuid", return_value=1000), mock.patch.object(
            MODULE.os,
            "getegid",
            return_value=1000,
        ), mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = MODULE.main(
                [
                    "--release-root",
                    "/",
                    "--campaign-id",
                    self.campaign_id,
                    "--wheelhouse",
                    "/",
                    "--wheel-input-receipt",
                    "/",
                ]
            )
        self.assertEqual(result, 1)
        self.assertIn("build CLI requires root:root", stderr.getvalue())

    def test_build_cli_is_explicitly_unavailable_pending_bootstrap(self) -> None:
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = MODULE.main(
                [
                    "--release-root",
                    "/",
                    "--campaign-id",
                    self.campaign_id,
                    "--wheelhouse",
                    "/",
                    "--wheel-input-receipt",
                    "/",
                    "--apply",
                ]
            )
        self.assertEqual(result, 1)
        self.assertIn("unavailable pending held-FD exact-release bootstrap", stderr.getvalue())

    def test_held_preparation_reproves_without_path_reopen_or_output(self) -> None:
        self.write_held_plan("f" * 64)
        capability = self.held_capability()
        with (
            mock.patch.object(MODULE.Path, "resolve", side_effect=AssertionError("path resolution is forbidden")),
            mock.patch.object(MODULE, "_read_absolute_regular", side_effect=AssertionError("path receipt read")),
            mock.patch.object(MODULE, "_read_wheels", side_effect=AssertionError("path wheel read")),
            mock.patch.object(MODULE, "_verify_exact_release", side_effect=AssertionError("path Git verification")),
            mock.patch.object(MODULE, "_write_private", side_effect=AssertionError("runtime output write")),
            mock.patch.object(VERIFY, "_open_root", side_effect=AssertionError("release path reopen")),
            mock.patch.object(VERIFY, "attest_runtime_closure", side_effect=AssertionError("path attestation")),
        ):
            prepared = MODULE.prepare_held_runtime_closure(
                held_bootstrap_capability=capability,
            )

        self.assertIsInstance(prepared, MODULE.HeldPreparedRuntimeClosure)
        self.assertEqual(prepared.campaign_id, self.campaign_id)
        self.assertEqual(prepared.release_sha, self.release_sha)
        self.assertEqual(prepared.release_tree_sha, self.release_tree_sha)
        self.assertEqual(prepared.held_plan_sha256, capability.binding.held_plan_sha256)
        self.assertEqual(
            prepared.materialization_state,
            "blocked-pending-descriptor-native-runtime-attestation",
        )
        self.assertEqual(set(prepared.project_sources), VERIFY.CONTROL_SOURCE_PATHS)
        self.assertFalse(hasattr(prepared, "release_root"))
        self.assertFalse(hasattr(prepared, "descriptor"))
        self.assertFalse(hasattr(prepared, "wheels"))
        self.assertFalse(hasattr(prepared, "required_confirmation"))
        self.assertFalse(self.output.exists())

    def test_held_preparation_is_non_materializable_before_any_output_or_lease(self) -> None:
        self.write_held_plan("f" * 64)
        capability = self.held_capability()
        prepared = MODULE.prepare_held_runtime_closure(
            held_bootstrap_capability=capability,
        )

        with mock.patch.object(
            MODULE,
            "_claim_materialization_lease",
            side_effect=AssertionError("materialization lease must not be consumed"),
        ), self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "non-materializable pending descriptor-native runtime attestation",
        ):
            MODULE.build_runtime_closure(
                prepared,
                destination=self.output,
                confirm="anything",
                expected_uid=self.uid,
                held_bootstrap_capability=capability,
            )
        self.assertFalse(self.output.exists())

    def test_builds_exact_closure_from_independently_digest_bound_input(self) -> None:
        prepared = self.prepare()
        capability = self.held_capability()
        with self.patch_contract():
            result = MODULE.build_runtime_closure(
                prepared,
                destination=self.output,
                confirm=prepared.required_confirmation,
                expected_uid=self.uid,
                held_bootstrap_capability=capability,
            )
        self.assertEqual(result["site_file_count"], 11)
        self.assertEqual(
            set(path.name for path in self.output.iterdir()),
            {
                VERIFY.RUNTIME_MANIFEST_FILENAME,
                VERIFY.WHEEL_RECEIPT_FILENAME,
                VERIFY.SITE_PACKAGES_DIRECTORY,
            },
        )
        manifest = json.loads((self.output / VERIFY.RUNTIME_MANIFEST_FILENAME).read_text())
        receipt = json.loads((self.output / VERIFY.WHEEL_RECEIPT_FILENAME).read_text())
        self.assertEqual(manifest["wheel_input_receipt_sha256"], prepared.wheel_input_receipt_sha256)
        self.assertEqual(manifest["closure_scope"], VERIFY.PRE_RUNTIME_CLOSURE_SCOPE)
        self.assertFalse(set(manifest["control_sources"]) & VERIFY.POST_RUNTIME_UNAVAILABLE_SOURCES)
        self.assertEqual(receipt["wheel_input_receipt_sha256"], prepared.wheel_input_receipt_sha256)
        self.assertTrue(all(row["source_member"] == row["path"] for row in receipt["installed_files"]))

    def test_build_api_is_unavailable_without_held_fd_bootstrap(self) -> None:
        prepared = self.prepare()
        with self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "requires a held-FD bootstrap capability",
        ):
            MODULE.build_runtime_closure(
                prepared,
                destination=self.output,
                confirm=prepared.required_confirmation,
                expected_uid=self.uid,
            )
        self.assertFalse(self.output.exists())

    def test_builder_rejects_unregistered_duck_capability_before_output(self) -> None:
        prepared = self.prepare()
        registered = self.held_capability()
        registered.close()

        class DuckCapability:
            def consume_for(self, **_expected: object) -> object:
                raise AssertionError("unregistered capability must not be consumed")

        with self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "capability type differs",
        ):
            MODULE.build_runtime_closure(
                prepared,
                destination=self.output,
                confirm=prepared.required_confirmation,
                expected_uid=self.uid,
                held_bootstrap_capability=DuckCapability(),
            )
        self.assertFalse(self.output.exists())

    def test_private_materializer_and_prepared_binding_fail_closed_before_output(self) -> None:
        prepared = self.prepare()
        with self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "requires a held-FD bootstrap capability",
        ):
            MODULE._materialize(  # noqa: SLF001
                prepared,
                destination=self.output,
                expected_uid=self.uid,
            )
        capability = self.held_capability()
        tampered = replace(prepared, source_policy_sha256="f" * 64)
        with self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "prepared closure differs from held-FD plan",
        ):
            MODULE.build_runtime_closure(
                tampered,
                destination=self.output,
                confirm=tampered.required_confirmation,
                expected_uid=self.uid,
                held_bootstrap_capability=capability,
            )
        source_capability = self.held_capability()
        source_tampered = replace(prepared, project_sources={})
        with self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "project sources differ from held-FD plan",
        ):
            MODULE.build_runtime_closure(
                source_tampered,
                destination=self.output,
                confirm=source_tampered.required_confirmation,
                expected_uid=self.uid,
                held_bootstrap_capability=source_capability,
            )
        self.assertFalse(self.output.exists())

    def test_self_selected_receipt_and_digest_are_rejected_without_held_plan_change(self) -> None:
        receipt, held_digest = self.input_receipt()
        self.write_held_plan(held_digest)
        document = json.loads(receipt.read_text())
        document["wheels"][0]["record_sha256"] = "f" * 64
        document["input_receipt_sha256"] = MODULE._sha256(
            {key: value for key, value in document.items() if key != "input_receipt_sha256"}
        )
        self_selected_raw = VERIFY.canonical_json_bytes(document)
        receipt.write_bytes(self_selected_raw)
        receipt.chmod(0o600)
        with self.patch_contract(), self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "external trusted wheel input receipt digest differs",
        ):
            MODULE.prepare_runtime_closure(
                release_root=self.release,
                campaign_id=self.campaign_id,
                wheelhouse=self.wheelhouse,
                wheel_input_receipt=receipt,
                trusted_plan_root=self.plan_root,
                expected_uid=self.uid,
            )

    def test_held_plan_with_wrong_receipt_digest_is_rejected(self) -> None:
        receipt, _digest = self.input_receipt()
        self.write_held_plan("f" * 64)
        with self.patch_contract(), self.assertRaisesRegex(
            MODULE.RuntimeClosureBuildError,
            "external trusted wheel input receipt digest differs",
        ):
            MODULE.prepare_runtime_closure(
                release_root=self.release,
                campaign_id=self.campaign_id,
                wheelhouse=self.wheelhouse,
                wheel_input_receipt=receipt,
                trusted_plan_root=self.plan_root,
                expected_uid=self.uid,
            )

    def test_policy_package_mismatch_is_rejected_before_wheel_read(self) -> None:
        policy_path = self.release / MODULE.POLICY_RELATIVE
        policy = self._policy()
        policy["packages"] = []
        policy_path.write_bytes(VERIFY.canonical_json_bytes(policy))
        policy_path.chmod(0o600)
        for arguments in (("add", MODULE.POLICY_RELATIVE), ("commit", "--quiet", "-m", "invalid policy"), ("checkout", "--quiet", "--detach")):
            completed = subprocess.run(
                ["/usr/bin/git", "-C", str(self.release), *arguments],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        self.release_sha = subprocess.check_output(
            ["/usr/bin/git", "-C", str(self.release), "rev-parse", "HEAD"], text=True
        ).strip()
        self.release_tree_sha = subprocess.check_output(
            ["/usr/bin/git", "-C", str(self.release), "rev-parse", "HEAD^{tree}"], text=True
        ).strip()
        with self.patch_contract(), self.assertRaisesRegex(MODULE.RuntimeClosureBuildError, "policy package closure differs"):
            self.prepare()

    def test_wheel_record_tamper_is_rejected_even_if_archive_digest_is_rebound(self) -> None:
        name, version, filename, payload = self.wheels[0]
        tampered = mutate_wheel_member(payload, "cffi/__init__.py")
        contract = list(self.contract)
        contract[0] = {**contract[0], "sha256": sha256(tampered)}
        with mock.patch.object(VERIFY, "REQUIRED_PACKAGES", tuple(contract)):
            with self.assertRaisesRegex(MODULE.RuntimeClosureBuildError, "archive is invalid|RECORD"):
                MODULE._validate_wheel(tampered, contract=contract[0])

    def test_wheel_cannot_claim_another_package_top_level(self) -> None:
        payload = wheel_bytes(
            distribution="cffi",
            version="2.1.0",
            requires=("pycparser",),
            members={
                "_cffi_backend.cpython-312-x86_64-linux-gnu.so": b"synthetic cffi extension",
                "cffi/__init__.py": b"# synthetic cffi\n",
                "cryptography/__init__.py": b"# forbidden foreign ownership\n",
            },
        )
        contract = {**self.contract[0], "sha256": sha256(payload)}
        with self.assertRaisesRegex(MODULE.RuntimeClosureBuildError, "unsupported content"):
            MODULE._validate_wheel(payload, contract=contract)

    def test_no_replace_publish_preserves_destination_created_during_race(self) -> None:
        prepared = self.prepare()
        capability = self.held_capability()
        original_publish = MODULE._rename_no_replace
        sentinel = b"racing destination remains untouched"

        def collide_then_publish(*args):
            source_parent, _source_name, destination_parent, destination_name = args
            self.assertEqual(source_parent, destination_parent)
            os.mkdir(destination_name, 0o700, dir_fd=destination_parent)
            marker = self.output / "sentinel"
            marker.write_bytes(sentinel)
            marker.chmod(0o600)
            return original_publish(*args)

        with self.patch_contract(), mock.patch.object(
            MODULE,
            "_rename_no_replace",
            side_effect=collide_then_publish,
        ), self.assertRaisesRegex(MODULE.RuntimeClosureBuildError, "destination already exists"):
            MODULE.build_runtime_closure(
                prepared,
                destination=self.output,
                confirm=prepared.required_confirmation,
                expected_uid=self.uid,
                held_bootstrap_capability=capability,
            )
        self.assertEqual((self.output / "sentinel").read_bytes(), sentinel)
        self.assertFalse(any(path.name.startswith(".controller-runtime-") for path in self.root.iterdir()))

    def test_trusted_input_record_and_member_digests_are_verified_against_wheels(self) -> None:
        for field in ("record_sha256", "members_sha256"):
            with self.subTest(field=field):
                receipt, _digest = self.input_receipt()
                document = json.loads(receipt.read_text())
                document["wheels"][0][field] = "f" * 64
                document["input_receipt_sha256"] = MODULE._sha256(
                    {key: value for key, value in document.items() if key != "input_receipt_sha256"}
                )
                raw = VERIFY.canonical_json_bytes(document)
                receipt.write_bytes(raw)
                receipt.chmod(0o600)
                self.write_held_plan(sha256(raw))
                with self.patch_contract(), self.assertRaisesRegex(
                    MODULE.RuntimeClosureBuildError,
                    "input receipt provenance differs",
                ):
                    MODULE.prepare_runtime_closure(
                        release_root=self.release,
                        campaign_id=self.campaign_id,
                        wheelhouse=self.wheelhouse,
                        wheel_input_receipt=receipt,
                        trusted_plan_root=self.plan_root,
                        expected_uid=self.uid,
                    )


class CommittedControllerRuntimePolicyTests(unittest.TestCase):
    def test_committed_policy_and_lock_are_exact_controller_contracts(self) -> None:
        MODULE._parse_policy((ROOT / MODULE.POLICY_RELATIVE).read_bytes())
        MODULE._parse_requirements((ROOT / MODULE.REQUIREMENTS_RELATIVE).read_bytes())
        MODULE._parse_wheelhouse_manifest((ROOT / MODULE.WHEELHOUSE_RELATIVE).read_bytes())

    def test_runtime_uses_the_v3_pre_runtime_boundary_without_a_boolean_bypass(self) -> None:
        self.assertFalse(hasattr(VERIFY, "HELD_FD_BOOTSTRAP_IMPLEMENTED"))
        self.assertTrue((ROOT / "scripts/production_shadow_convergence_source_set_runtime_bootstrap.py").is_file())
        self.assertEqual(VERIFY.HELD_RUNTIME_PLAN_SCHEMA, "production-shadow-controller-runtime-held-plan-v3")


if __name__ == "__main__":
    unittest.main()
