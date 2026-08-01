"""Injected local-only tests for the sealed paired Arvan probe receipt runner.

The test factory method is patched in process.  These tests never open an
Arvan connection, read a credential file, import boto3, run a shell command,
or start Docker/SSH.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
from uuid import UUID

from core import physical_arvan_immutability_preflight as preflight
from core import physical_arvan_s3_immutability_probe_runner as runner
from core import physical_arvan_s3_separated_client_factory as factory
from core import physical_arvan_s3_separated_credential_loader as credentials
from core import physical_release_seal_admission as seal


NOW = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)
CAMPAIGN = "physical-arvan-probe-runner-20260731"
RELEASE = "a" * 40
TREE = "b" * 40
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-recovery"
WORKTREE = Path("/srv/trading-bot-three-site/probe-runner-seal-worktree")
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_immutability_probe_runner.py"
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _tree_listing() -> bytes:
    entries = (
        ("100644", "c" * 40, ".gitignore"),
        ("100644", "d" * 40, "README.md"),
        ("100755", "e" * 40, "scripts/release.sh"),
    )
    return b"".join(
        f"{mode} blob {object_id}\t{path}".encode("ascii") + b"\0"
        for mode, object_id, path in entries
    )


def _filesystem_object(
    path: Path,
    *,
    mode: int,
    regular_file: bool,
    directory: bool,
    executable: bool,
) -> seal.PhysicalReleaseSealFilesystemObject:
    return seal.PhysicalReleaseSealFilesystemObject(
        path=path,
        owner_uid=0,
        mode=mode,
        regular_file=regular_file,
        directory=directory,
        symlink=False,
        executable=executable,
        ancestors_root_controlled=True,
        device=1,
        inode={
            WORKTREE: 101,
            WORKTREE / ".git": 102,
            seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY: 103,
        }[path],
        ctime_ns=1_000_000_000,
        mtime_ns=1_000_000_000,
    )


class _SealFilesystemInspector:
    def __init__(self) -> None:
        self.calls = 0
        self.observed = seal.PhysicalReleaseSealWorktreeInspection(
            worktree=_filesystem_object(
                WORKTREE,
                mode=0o750,
                regular_file=False,
                directory=True,
                executable=True,
            ),
            git_metadata=_filesystem_object(
                WORKTREE / ".git",
                mode=0o700,
                regular_file=False,
                directory=True,
                executable=True,
            ),
            git_binary=_filesystem_object(
                seal.FIXED_PHYSICAL_RELEASE_SEAL_GIT_BINARY,
                mode=0o755,
                regular_file=True,
                directory=False,
                executable=True,
            ),
        )

    def inspect_worktree(self, *, worktree: Path):
        if worktree != WORKTREE:
            raise AssertionError("unexpected local worktree inspection")
        self.calls += 1
        return self.observed


class _SealGitRunner:
    def __init__(self) -> None:
        self.heads = [RELEASE, RELEASE]
        self.statuses = [b"", b""]
        self.calls: list[seal.PhysicalReleaseSealGitInvocation] = []

    def run(self, *, invocation: seal.PhysicalReleaseSealGitInvocation):
        self.calls.append(invocation)
        arguments = invocation.arguments[3:]
        if arguments == ("rev-parse", "--verify", "HEAD^{commit}"):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=(self.heads.pop(0) + "\n").encode("ascii"),
            )
        if arguments == (
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignored=matching",
        ):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=self.statuses.pop(0),
            )
        if arguments == ("rev-parse", "--verify", RELEASE + "^{tree}"):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=(TREE + "\n").encode("ascii"),
            )
        if arguments == ("ls-tree", "-r", "-z", "--full-tree", RELEASE):
            return seal.PhysicalReleaseSealGitCommandResult(
                exit_code=0,
                stdout_bytes=_tree_listing(),
            )
        raise AssertionError(f"unexpected Git inspection: {arguments!r}")


def _sealed_descriptor(
    *,
    sealed_at: datetime = NOW - timedelta(seconds=1),
    admission_now: datetime | None = None,
):
    images = tuple(
        seal.PhysicalReleaseSealImage(
            role=role,
            reference=(
                f"registry.example:5000/gold-trade/{role}@sha256:" + _sha("image:" + role)
            ),
        )
        for role in seal.REQUIRED_PHYSICAL_RELEASE_IMAGE_ROLES
    )
    with mock.patch.object(seal.os, "geteuid", return_value=0):
        return seal.admit_physical_release_seal(
            config=seal.PhysicalReleaseSealAdmissionConfig(
                worktree=WORKTREE,
                campaign_id=CAMPAIGN,
                expected_release_sha=RELEASE,
                images=images,
                seal_id=UUID("f5764788-4a4f-4985-a10f-d17a8d73f651"),
                sealed_at=sealed_at,
                enabled=True,
                maximum_freshness_seconds=180,
            ),
            filesystem_inspector=_SealFilesystemInspector(),
            git_runner=_SealGitRunner(),
            now=sealed_at if admission_now is None else admission_now,
        )


def _binding(**changes: object) -> preflight.PhysicalArvanImmutabilityPreflightBinding:
    values: dict[str, object] = {
        "campaign_id": CAMPAIGN,
        "release_sha": RELEASE,
        "source_site": "webapp_fi",
        "destination_site": "webapp_ir",
        "route_binding_sha256": "a" * 64,
        "endpoint": ENDPOINT,
        "region": REGION,
        "bucket": BUCKET,
        "minimum_retention_days": 90,
    }
    values.update(changes)
    return preflight.PhysicalArvanImmutabilityPreflightBinding(**values)


def _denied(
    *operations: str,
) -> tuple[preflight.PhysicalArvanDeniedOperationObservation, ...]:
    return tuple(
        preflight.PhysicalArvanDeniedOperationObservation(
            operation=operation,
            outcome=preflight.ARVAN_DISPOSABLE_DELETE_DENIED,
        )
        for operation in operations
    )


def _observation(
    *,
    binding: preflight.PhysicalArvanImmutabilityPreflightBinding | None = None,
    observed_at: datetime = NOW,
) -> preflight.PhysicalArvanImmutabilityPreflightObservation:
    supplied = _binding() if binding is None else binding
    restrictions = (
        preflight.PhysicalArvanCredentialRestrictionObservation(
            role="fi-publisher",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256="b" * 64,
            allowed_operations=(
                "GetBucketAcl",
                "GetBucketVersioning",
                "GetObjectLockConfiguration",
                "PutObject:create-only",
                "ListObjectVersions:exact-key",
                "GetObjectRetention:exact-version",
                "GetObject:exact-version",
                "HeadObject:exact-version",
            ),
            denied_operations=_denied(
                "DeleteObject", "DeleteObjectVersion", "PutObject:overwrite"
            ),
        ),
        preflight.PhysicalArvanCredentialRestrictionObservation(
            role="ir-receiver",
            credential_posture="scoped-credential-probed",
            credential_identity_sha256="c" * 64,
            allowed_operations=("GetObject:exact-version", "HeadObject:exact-version"),
            denied_operations=_denied(
                "DeleteObject",
                "DeleteObjectVersion",
                "ListBucket",
                "ListObjectVersions",
                "PutObject",
            ),
        ),
        preflight.PhysicalArvanCredentialRestrictionObservation(
            role="witness-controller",
            credential_posture="no-object-storage-credential-issued",
            credential_identity_sha256=None,
            allowed_operations=(),
            denied_operations=(),
        ),
    )
    probe = preflight.PhysicalArvanDisposableImmutabilityProbe(
        object_key=(
            f"physical-preflight/{supplied.campaign_id}/arvan-immutability/"
            "nonce-20260731.age"
        ),
        version_id="preflight-version-20260731",
        ciphertext_sha256="d" * 64,
        ciphertext_bytes=427,
        delete_version_outcome="access-denied",
        delete_marker_outcome="access-denied",
        exact_version_get_outcome="exact-version-get-succeeded",
        retrieved_version_id="preflight-version-20260731",
        retrieved_ciphertext_sha256="d" * 64,
        retrieved_ciphertext_bytes=427,
    )
    return preflight.build_physical_arvan_immutability_preflight_observation(
        binding=supplied,
        versioning_status="Enabled",
        acl_posture="private-canonical-owner-only-v1",
        retention_mode="provider-verified-immutable-retention-v1",
        retention_policy_evidence_sha256="e" * 64,
        retention_days=180,
        credential_restrictions=restrictions,
        disposable_probe=probe,
        observed_at=observed_at,
    )


@unittest.skipUnless(os.geteuid() == 0, "receipt runner is root-only")
class PhysicalArvanS3ImmutabilityProbeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="arvan-probe-runner-")
        self.receipt_root = Path(self.temporary.name).resolve()
        self.receipt_root.chmod(0o700)
        self.descriptor = _sealed_descriptor()
        self.binding = _binding()
        loader_config = credentials.RootOwnedArvanS3SeparatedCredentialLoaderConfig(
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
            enabled=True,
        )
        self.factory = factory.RootOwnedArvanS3SeparatedClientFactory(
            factory.RootOwnedArvanS3SeparatedClientFactoryConfig(
                credential_loader_config=loader_config,
                enabled=True,
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _config(self, **changes: object) -> runner.RootOwnedArvanS3ImmutabilityProbeRunnerConfig:
        values: dict[str, object] = {
            "campaign_id": CAMPAIGN,
            "sealed_release_descriptor": self.descriptor,
            "binding": self.binding,
            "paired_factory": self.factory,
            "enabled": True,
            "maximum_release_seal_freshness_seconds": 180,
            "maximum_observation_freshness_seconds": 300,
        }
        values.update(changes)
        return runner.RootOwnedArvanS3ImmutabilityProbeRunnerConfig(**values)

    def _owner(self, **changes: object) -> runner.RootOwnedArvanS3ImmutabilityProbeRunner:
        return runner.RootOwnedArvanS3ImmutabilityProbeRunner(self._config(**changes))

    def _run(
        self,
        owner: runner.RootOwnedArvanS3ImmutabilityProbeRunner,
        *,
        result: object | None = None,
    ) -> runner.PhysicalArvanS3ImmutabilityProbeReceipt:
        supplied = _observation() if result is None else result
        with mock.patch.object(
            runner,
            "FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT",
            self.receipt_root,
        ), mock.patch.object(
            factory.RootOwnedArvanS3SeparatedClientFactory,
            "collect_immutability_preflight",
            autospec=True,
            return_value=supplied,
        ):
            return owner.run(now=NOW)

    def test_success_is_redacted_canonical_root_only_and_non_authorizing(self) -> None:
        receipt = self._run(self._owner())
        leaves = list(self.receipt_root.iterdir())
        self.assertEqual(1, len(leaves))
        leaf = leaves[0]
        metadata = leaf.stat()
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(0, metadata.st_uid)
        self.assertEqual(0o600, stat.S_IMODE(metadata.st_mode))
        self.assertEqual(1, metadata.st_nlink)
        payload = leaf.read_bytes()
        self.assertEqual(receipt, runner.parse_physical_arvan_s3_immutability_probe_receipt(payload))
        self.assertTrue(payload.endswith(b"\n"))
        self.assertFalse(receipt.deployment_authorized)
        self.assertFalse(receipt.promotion_authorized)
        self.assertFalse(receipt.full_matrix_authorized)
        rendered = payload.decode("ascii")
        for forbidden in (
            ENDPOINT,
            REGION,
            BUCKET,
            "nonce-20260731.age",
            "preflight-version-20260731",
            "ciphertext",
            "secret",
            "client",
            "path",
        ):
            self.assertNotIn(forbidden, rendered.lower())
        expected_fields = {
            "schema",
            "status",
            "campaign_id",
            "release_sha",
            "source_site",
            "destination_site",
            "sealed_release_descriptor_sha256",
            "arvan_binding_sha256",
            "observation_evidence_sha256",
            "retention_mode",
            "retention_days",
            "observed_at",
            "fi_publisher_identity_sha256",
            "ir_receiver_identity_sha256",
            "direct_fi_to_ir_control",
            "deployment_authorized",
            "promotion_authorized",
            "full_matrix_authorized",
            "receipt_sha256",
        }
        self.assertEqual(expected_fields, set(json.loads(payload)))

    def test_default_off_invalid_seal_and_binding_all_stop_before_factory(self) -> None:
        cases = (
            self._owner(enabled=False),
            self._owner(
                binding=replace(self.binding, campaign_id="another-campaign-20260731")
            ),
            self._owner(
                sealed_release_descriptor=_sealed_descriptor(
                    sealed_at=NOW - timedelta(seconds=181)
                )
            ),
        )
        for owner in cases:
            with self.subTest(owner=owner._config.enabled):
                with mock.patch.object(
                    runner,
                    "FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT",
                    self.receipt_root,
                ), mock.patch.object(
                    factory.RootOwnedArvanS3SeparatedClientFactory,
                    "collect_immutability_preflight",
                    autospec=True,
                ) as collect:
                    with self.assertRaises(runner.PhysicalArvanS3ImmutabilityProbeRunnerError):
                        owner.run(now=NOW)
                collect.assert_not_called()
        self.assertEqual([], list(self.receipt_root.iterdir()))

    def test_existing_claim_and_unsafe_root_stop_before_factory(self) -> None:
        owner = self._owner()
        self._run(owner)
        with mock.patch.object(
            runner,
            "FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT",
            self.receipt_root,
        ), mock.patch.object(
            factory.RootOwnedArvanS3SeparatedClientFactory,
            "collect_immutability_preflight",
            autospec=True,
        ) as collect:
            with self.assertRaisesRegex(
                runner.PhysicalArvanS3ImmutabilityProbeRunnerError,
                "^ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ALREADY_EXISTS$",
            ):
                owner.run(now=NOW)
        collect.assert_not_called()

        unsafe = self.receipt_root / "unsafe-root"
        unsafe.mkdir(mode=0o755)
        unsafe.chmod(0o755)
        symlink = self.receipt_root / "symlink-root"
        os.symlink(unsafe, symlink)
        for root in (unsafe, symlink):
            with self.subTest(root=root.name), mock.patch.object(
                runner,
                "FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT",
                root,
            ), mock.patch.object(
                factory.RootOwnedArvanS3SeparatedClientFactory,
                "collect_immutability_preflight",
                autospec=True,
            ) as collect:
                with self.assertRaisesRegex(
                    runner.PhysicalArvanS3ImmutabilityProbeRunnerError,
                    "^ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_RECEIPT_ROOT_UNSAFE$",
                ):
                    owner.run(now=NOW)
            collect.assert_not_called()

    def test_factory_and_observation_failures_are_redacted_and_do_not_write(self) -> None:
        owner = self._owner()
        raw_error = RuntimeError("super-secret provider path s3://" + BUCKET)
        with mock.patch.object(
            runner,
            "FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT",
            self.receipt_root,
        ), mock.patch.object(
            factory.RootOwnedArvanS3SeparatedClientFactory,
            "collect_immutability_preflight",
            autospec=True,
            side_effect=raw_error,
        ):
            with self.assertRaisesRegex(
                runner.PhysicalArvanS3ImmutabilityProbeRunnerError,
                "^ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_FACTORY_FAILED$",
            ) as raised:
                owner.run(now=NOW)
        self.assertNotIn("super-secret", str(raised.exception))
        self.assertNotIn(BUCKET, str(raised.exception))
        self.assertEqual([], list(self.receipt_root.iterdir()))

        bad = replace(_observation(), evidence_sha256="f" * 64)
        with mock.patch.object(
            runner,
            "FIXED_ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_ROOT",
            self.receipt_root,
        ), mock.patch.object(
            factory.RootOwnedArvanS3SeparatedClientFactory,
            "collect_immutability_preflight",
            autospec=True,
            return_value=bad,
        ):
            with self.assertRaisesRegex(
                runner.PhysicalArvanS3ImmutabilityProbeRunnerError,
                "^ARVAN_S3_IMMUTABILITY_PROBE_RUNNER_OBSERVATION_INVALID$",
            ):
                owner.run(now=NOW)
        self.assertEqual([], list(self.receipt_root.iterdir()))

    def test_receipt_parser_rejects_tamper_noncanonical_and_duplicate_fields(self) -> None:
        receipt = self._run(self._owner())
        payload = next(self.receipt_root.iterdir()).read_bytes()
        parsed = json.loads(payload)
        parsed["promotion_authorized"] = True
        tampered = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
        for bad in (
            tampered,
            payload.rstrip(b"\n"),
            b'{"schema":"x","schema":"x"}\n',
        ):
            with self.subTest(bad=bad[:16]):
                with self.assertRaisesRegex(
                    runner.PhysicalArvanS3ImmutabilityProbeRunnerError,
                    "^ARVAN_S3_IMMUTABILITY_PROBE_RECEIPT_INVALID$",
                ):
                    runner.parse_physical_arvan_s3_immutability_probe_receipt(bad)
        self.assertEqual(receipt, runner.parse_physical_arvan_s3_immutability_probe_receipt(payload))

    def test_constructor_is_inert_and_module_has_no_direct_external_client_surface(self) -> None:
        with mock.patch.object(factory, "_load_boto_sdk") as sdk_loader, mock.patch.object(
            credentials,
            "_load_root_owned_separated_credential_facts",
        ) as credential_loader:
            owner = self._owner()
        self.assertEqual({"_config"}, set(vars(owner)))
        sdk_loader.assert_not_called()
        credential_loader.assert_not_called()
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertFalse({"boto3", "botocore", "socket", "subprocess", "requests"} & imported)
