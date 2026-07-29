from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from scripts import (
    build_production_shadow_freeze_snapshot_phase_request as MODULE,
)
from scripts import (
    orchestrate_production_shadow_freeze_snapshot_phases as BRIDGE,
)
from tests import (
    test_orchestrate_production_shadow_freeze_snapshot_phases as FIXTURE,
)


NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
PLAN_SHA256 = "7" * 64


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def ensure_private_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)
    path.chmod(0o700)


def private_json(path: Path, value: object) -> MODULE.Reference:
    ensure_private_directory(path.parent)
    payload = canonical(value) + b"\n"
    path.write_bytes(payload)
    path.chmod(0o600)
    return MODULE.Reference(
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


class RequestBuilderFixture:
    def __init__(self, root: Path):
        self.root = root
        self.root.chmod(0o700)
        self.control = root / "control"
        self.evidence = self.control / "evidence"
        self.operation_secret = root / "frozen" / FIXTURE.OPERATION_ID
        self.controller_root = self.operation_secret / "controller"
        self.collection_root = (
            self.controller_root / "source-snapshots" / "frozen-final"
        )
        self.nginx_secret = self.operation_secret / "nginx-coordinator"
        self.current_receipts = (
            self.controller_root
            / "current-frozen-verification"
            / "receipts"
        )
        self.snapshot_root = root / "snapshots"
        for directory in (
            self.control,
            self.evidence,
            self.controller_root,
            self.collection_root,
            self.nginx_secret,
            self.current_receipts,
            self.snapshot_root,
        ):
            ensure_private_directory(directory)

        self.approval = private_json(
            self.control / "approval.json",
            {"approval": "bound"},
        )
        self.policy = private_json(
            self.control / "human-approval-policy.json",
            {"policy": "public"},
        )
        self.manifest = FIXTURE.manifest(self.control)
        self.manifest["deployment"]["controller_evidence_root"] = os.fspath(
            self.evidence
        )
        self.manifest["deployment"]["controller_journal_path"] = os.fspath(
            self.control / "journal.json"
        )
        self.manifest["artifacts"]["cutover_approval_sha256"] = (
            self.approval.sha256
        )
        self.manifest["artifacts"]["human_approval_policy_sha256"] = (
            self.policy.sha256
        )
        self.manifest_reference = private_json(
            self.control / "manifest.json",
            self.manifest,
        )

        self.prior: dict[str, MODULE.Reference] = {}
        for phase in MODULE._prior_names():
            evidence = {
                "phase": phase,
                "campaign_id": self.manifest["campaign_id"],
                "operation_id": self.manifest["operation_id"],
                "release_sha": self.manifest["release_sha"],
                "legacy_release_sha": self.manifest[
                    "legacy_release_sha"
                ],
                "manifest_sha256": self.manifest_reference.sha256,
                "plan_sha256": PLAN_SHA256,
                "approval_sha256": self.approval.sha256,
                "status": "passed",
                "business_write_observed": False,
            }
            payload = canonical(evidence) + b"\n"
            digest = hashlib.sha256(payload).hexdigest()
            path = MODULE._canonical_prior_path(
                self.manifest,
                phase=phase,
                digest=digest,
            )
            self.prior[phase] = private_json(path, evidence)

        self.roles: dict[str, dict[str, MODULE.Reference]] = {}
        for index, role in enumerate(BRIDGE.ROLE_ORDER, 1):
            self.roles[role] = {
                "binding": private_json(
                    self._role_paths(role)["binding"],
                    {"role": role, "kind": "binding"},
                ),
                "freeze_evidence": private_json(
                    self._role_paths(role)["freeze_evidence"],
                    {"role": role, "kind": "freeze-evidence"},
                ),
                "snapshot_manifest": private_json(
                    self._role_paths(role)["manifest"],
                    {"role": role, "kind": "snapshot-manifest"},
                ),
            }

        self.nginx = private_json(
            self._state_receipt_placeholder(),
            {"receipt": "fresh-nginx"},
        )
        canonical_nginx = self._canonical_paths(
            state_receipt_sha256=self.nginx.sha256,
        )["state_receipt"]
        if self.nginx.path != canonical_nginx:
            canonical_nginx.parent.mkdir(
                mode=0o700,
                parents=True,
                exist_ok=True,
            )
            canonical_nginx.parent.chmod(0o700)
            self.nginx.path.rename(canonical_nginx)
            self.nginx = replace(self.nginx, path=canonical_nginx)

        current_document = {"receipt": "current-frozen"}
        current_payload = canonical(current_document) + b"\n"
        current_digest = hashlib.sha256(current_payload).hexdigest()
        self.current = private_json(
            self.current_receipts / f"{current_digest}.json",
            current_document,
        )

        frozen_document = self._frozen_result_document()
        frozen_payload = canonical(frozen_document) + b"\n"
        frozen_digest = hashlib.sha256(frozen_payload).hexdigest()
        frozen_path = (
            self.controller_root
            / MODULE.COORDINATOR_RESULT_DIRECTORY
            / (
                f"{MODULE.COORDINATOR_RESULT_PREFIX}."
                f"{frozen_digest}.json"
            )
        )
        self.frozen = private_json(frozen_path, frozen_document)
        self.references = MODULE.RequestReferences(
            manifest=self.manifest_reference,
            approval=self.approval,
            approval_policy=self.policy,
            prior_phase_evidence=self.prior,
            frozen_snapshot_result=self.frozen,
            current_frozen_verification_receipt=self.current,
            nginx_readback_receipt=self.nginx,
            roles=self.roles,
        )

    def _role_paths(self, role: str) -> dict[str, Path]:
        role_secret = self.operation_secret / role.replace("_", "-")
        return {
            "binding": role_secret / "source-snapshot-binding.json",
            "freeze_evidence": (
                self.operation_secret
                / "legacy-writer-freeze"
                / role
                / "freeze-evidence.json"
            ),
            "manifest": (
                self.snapshot_root
                / FIXTURE.OPERATION_ID
                / role
                / "frozen-final"
                / BRIDGE.SOURCE.MANIFEST_FILE
            ),
            "collection": self.collection_root / role,
        }

    def _state_receipt_placeholder(self) -> Path:
        return self.nginx_secret / "receipts" / "placeholder.json"

    def _canonical_paths(
        self,
        _operation_id: str | None = None,
        _release_sha: str | None = None,
        *,
        state_receipt_sha256: str | None = None,
        lease_claim_sha256: str | None = None,
    ) -> dict:
        roles = {
            role: self._role_paths(role) for role in BRIDGE.ROLE_ORDER
        }
        result = {
            "controller_root": self.controller_root,
            "collection_root": self.collection_root,
            "outcome": self.controller_root
            / BRIDGE.FROZEN.OUTCOME_FILENAME,
            "journal": self.controller_root
            / BRIDGE.FROZEN.JOURNAL_FILENAME,
            "roles": roles,
        }
        if state_receipt_sha256 is not None:
            result["state_receipt"] = (
                self.nginx_secret
                / "receipts"
                / f"legacy-frozen-{state_receipt_sha256}.json"
            )
        if lease_claim_sha256 is not None:
            result["lease_claim"] = (
                self.nginx_secret
                / "live-leases"
                / "claims"
                / f"{lease_claim_sha256}.json"
            )
        return result

    def _current_paths(
        self,
        _operation_id: str,
        _release_sha: str,
    ) -> dict:
        return {"verification_receipts": self.current_receipts}

    def _frozen_result_document(self) -> dict:
        paths = self._canonical_paths(
            state_receipt_sha256=self.nginx.sha256,
        )
        lease = "7" * 64
        return {
            "schema": BRIDGE.FROZEN.RESULT_SCHEMA,
            "status": "complete",
            "operation_id": self.manifest["operation_id"],
            "release_sha": self.manifest["release_sha"],
            "release_tree_sha": self.manifest["release_tree_sha"],
            "nginx_aggregate_sha256": "5" * 64,
            "state_receipt_sha256": self.nginx.sha256,
            "lease_claim_sha256": lease,
            "outcome_sha256": "8" * 64,
            "consumption_sha256": "9" * 64,
            "roles": {
                role: {
                    "host": BRIDGE.FROZEN.ROLE_HOSTS[role],
                    "transport": BRIDGE.FROZEN.ROLE_TRANSPORTS[role],
                    "binding_sha256": self.roles[role][
                        "binding"
                    ].sha256,
                    "freeze_evidence_sha256": self.roles[role][
                        "freeze_evidence"
                    ].sha256,
                    "lease_claim_sha256": lease,
                    "manifest_binding_sha256": self.roles[role][
                        "binding"
                    ].sha256,
                    "files": {
                        name: {
                            "sha256": (
                                self.roles[role][
                                    "snapshot_manifest"
                                ].sha256
                                if name == BRIDGE.SOURCE.MANIFEST_FILE
                                else str(index) * 64
                            ),
                            "bytes": (
                                self.roles[role][
                                    "snapshot_manifest"
                                ].path.stat().st_size
                                if name == BRIDGE.SOURCE.MANIFEST_FILE
                                else 100 + index
                            ),
                        }
                        for index, name in enumerate(
                            BRIDGE.FROZEN.SNAPSHOT_FILENAMES,
                            1,
                        )
                    },
                }
                for role in BRIDGE.ROLE_ORDER
            },
            "collection_root": os.fspath(paths["collection_root"]),
            "outcome_path": os.fspath(paths["outcome"]),
            "journal_path": os.fspath(paths["journal"]),
            "journal_state_sha256": "a" * 64,
            "public_phase": BRIDGE.PHASES[0],
            "public_phase_handoff_sha256": "b" * 64,
            "public_phase_start_journal_state_sha256": "c" * 64,
            "public_phase_start_journal_event_tail_sha256": "d" * 64,
            "public_phase_start_journal_event_count": 6,
            "live_lease_outcome": "handoff-shadow-readonly",
            "legacy_writers_frozen": True,
            "automatic_restore_performed": False,
            "pull_policy": "never",
            "build_performed": False,
            "object_storage_used": False,
            "wa_contacted": False,
        }

    def _read_prior(self, path: Path) -> tuple[dict, str]:
        record = BRIDGE._read_private_json(  # noqa: SLF001
            path,
            label="fixture prior evidence",
        )
        return record.document, record.sha256

    def _validate_sources(
        self,
        context: BRIDGE.BridgeContext,
        *,
        now: datetime,
    ) -> BRIDGE.ValidatedSources:
        self.last_validation_time = now
        document = context.request.document
        records = {
            "frozen_snapshot_result": BRIDGE._record_from_reference(  # noqa: SLF001
                document["frozen_snapshot_result"],
                label="fixture frozen result",
            ),
            "current_frozen_verification_receipt": BRIDGE._record_from_reference(  # noqa: SLF001
                document["current_frozen_verification_receipt"],
                label="fixture current receipt",
            ),
            "nginx_readback_receipt": BRIDGE._record_from_reference(  # noqa: SLF001
                document["nginx_readback_receipt"],
                label="fixture Nginx receipt",
            ),
        }
        for role in BRIDGE.ROLE_ORDER:
            for kind in BRIDGE.ROLE_SOURCE_FIELDS:
                records[f"{role}_{kind}"] = (
                    BRIDGE._record_from_reference(  # noqa: SLF001
                        document["roles"][role][kind],
                        label=f"fixture {role} {kind}",
                    )
                )
        closure = hashlib.sha256(
            canonical(
                [
                    {
                        "label": label,
                        "path": os.fspath(record.path),
                        "sha256": record.sha256,
                        "bytes": record.identity.size,
                    }
                    for label, record in sorted(records.items())
                ]
            )
        ).hexdigest()
        return BRIDGE.ValidatedSources(
            records=records,
            bindings={},
            freeze_results={},
            freeze_evidence={},
            snapshots={},
            frozen_result={},
            current_verification_receipt={},
            nginx_receipt={},
            source_closure_sha256=closure,
        )

    @contextmanager
    def patches(self):
        with (
            mock.patch.object(
                MODULE.CONTROLLER,
                "read_root_only_manifest",
                return_value=(
                    self.manifest,
                    self.manifest_reference.sha256,
                ),
            ),
            mock.patch.object(
                MODULE.CONTROLLER,
                "render_plan",
                return_value={"plan_sha256": PLAN_SHA256},
            ),
            mock.patch.object(
                MODULE.VERIFY,
                "read_root_only_evidence",
                side_effect=self._read_prior,
            ),
            mock.patch.object(
                BRIDGE.FROZEN,
                "canonical_paths",
                side_effect=self._canonical_paths,
            ),
            mock.patch.object(
                MODULE.CURRENT,
                "_paths",
                side_effect=self._current_paths,
            ),
            mock.patch.object(
                BRIDGE,
                "_validate_sources",
                side_effect=self._validate_sources,
            ) as validate_sources,
            mock.patch.object(
                MODULE,
                "_fresh_observation",
                return_value=NOW,
            ),
        ):
            yield validate_sources

    def arguments(self) -> list[str]:
        result: list[str] = []

        def add(name: str, reference: MODULE.Reference) -> None:
            result.extend(
                [
                    f"--{name}",
                    os.fspath(reference.path),
                    f"--{name}-sha256",
                    reference.sha256,
                ]
            )

        add("manifest", self.references.manifest)
        add("approval", self.references.approval)
        add("approval-policy", self.references.approval_policy)
        add(
            "frozen-snapshot-result",
            self.references.frozen_snapshot_result,
        )
        add(
            "current-frozen-verification-receipt",
            self.references.current_frozen_verification_receipt,
        )
        add(
            "nginx-readback-receipt",
            self.references.nginx_readback_receipt,
        )
        for phase in MODULE._prior_names():
            add(
                "prior-" + phase.replace("_", "-"),
                self.references.prior_phase_evidence[phase],
            )
        for role in BRIDGE.ROLE_ORDER:
            for kind in BRIDGE.ROLE_SOURCE_FIELDS:
                add(
                    role.replace("_", "-")
                    + "-"
                    + kind.replace("_", "-"),
                    self.references.roles[role][kind],
                )
        return result


class FreezeSnapshotRequestBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.fixture = RequestBuilderFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_main(self, arguments: list[str]) -> tuple[int, dict]:
        stream = io.BytesIO()
        stdout = mock.Mock()
        stdout.buffer = stream
        with (
            self.fixture.patches(),
            mock.patch.object(MODULE.sys, "stdout", stdout),
        ):
            status = MODULE.main(arguments)
        return status, json.loads(stream.getvalue().decode("ascii"))

    def test_plan_is_deterministic_read_only_and_claim_free(self):
        with self.fixture.patches() as validate_sources:
            first = MODULE.execute(
                self.fixture.references,
                now=NOW,
            )
            second = MODULE.execute(
                self.fixture.references,
                now=NOW,
            )
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "planned")
        self.assertEqual(first["reference_count"], 17)
        self.assertFalse(first["caller_claim_values_accepted"])
        self.assertFalse(first["output_mutated"])
        self.assertFalse(first["network_io"])
        self.assertFalse(Path(first["output"]).exists())
        self.assertEqual(validate_sources.call_count, 2)
        self.assertEqual(self.fixture.last_validation_time, NOW)

    def test_apply_rejects_caller_supplied_observation_time(self):
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotRequestBuildError,
            "caller-supplied observation time",
        ):
            MODULE.execute(
                self.fixture.references,
                apply=True,
                now=NOW,
            )

    def test_apply_is_create_only_and_target_self_validated(self):
        with self.fixture.patches() as validate_sources:
            plan = MODULE.execute(
                self.fixture.references,
                now=NOW,
            )
            with mock.patch.object(
                BRIDGE,
                "_load_request",
                wraps=BRIDGE._load_request,
            ) as load_request:
                result = MODULE.execute(
                    self.fixture.references,
                    apply=True,
                    confirm=plan["required_confirmation"],
                )
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["publication"], "created")
        self.assertTrue(result["target_load_request_verified"])
        self.assertTrue(result["target_validate_sources_verified"])
        self.assertEqual(load_request.call_count, 1)
        self.assertEqual(validate_sources.call_count, 4)
        path = Path(result["output"])
        metadata = path.stat(follow_symlinks=False)
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_nlink, 1)
        request = BRIDGE._read_private_json(  # noqa: SLF001
            path,
            label="published request test readback",
        ).document
        self.assertEqual(set(request), BRIDGE.REQUEST_FIELDS)
        self.assertEqual(
            request["constraints"],
            BRIDGE.EXPECTED_CONSTRAINTS,
        )
        self.assertFalse(
            any("count" in key or "claim" in key for key in request)
        )

        with self.fixture.patches():
            retried = MODULE.execute(
                self.fixture.references,
                apply=True,
                confirm=plan["required_confirmation"],
            )
        self.assertEqual(retried["status"], "already-published")
        self.assertEqual(retried["publication"], "reused")
        self.assertFalse(retried["output_mutated"])

    def test_wrong_confirmation_and_plan_confirmation_do_not_publish(self):
        with self.fixture.patches():
            plan = MODULE.execute(
                self.fixture.references,
                now=NOW,
            )
            with self.assertRaises(MODULE.FreezeSnapshotRequestBuildError):
                MODULE.execute(
                    self.fixture.references,
                    apply=True,
                    confirm="wrong",
                )
            with self.assertRaises(MODULE.FreezeSnapshotRequestBuildError):
                MODULE.execute(
                    self.fixture.references,
                    confirm=plan["required_confirmation"],
                    now=NOW,
                )
        self.assertFalse(Path(plan["output"]).exists())

    def test_expired_source_at_publication_recheck_never_writes_request(self):
        with self.fixture.patches():
            plan = MODULE.execute(
                self.fixture.references,
                now=NOW,
            )
        with self.fixture.patches() as validate_sources:
            calls = 0

            def source_check(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                nonlocal calls
                calls += 1
                if calls == 1:
                    return self.fixture._validate_sources(*args, **kwargs)
                raise BRIDGE.FreezeSnapshotPhaseBridgeError(
                    "current frozen verification is outside phase age"
                )

            validate_sources.side_effect = source_check
            with self.assertRaisesRegex(
                MODULE.FreezeSnapshotRequestBuildError,
                "immediately before publication",
            ):
                MODULE.execute(
                    self.fixture.references,
                    apply=True,
                    confirm=plan["required_confirmation"],
                )
        self.assertFalse(Path(plan["output"]).exists())

    def test_path_substitution_and_hard_link_fail_closed(self):
        substituted = self.root / "substituted-binding.json"
        shutil.copyfile(
            self.fixture.references.roles["bot_fi"]["binding"].path,
            substituted,
        )
        substituted.chmod(0o600)
        substituted_reference = MODULE.Reference(
            substituted,
            self.fixture.references.roles["bot_fi"][
                "binding"
            ].sha256,
        )
        roles = {
            role: dict(row)
            for role, row in self.fixture.references.roles.items()
        }
        roles["bot_fi"]["binding"] = substituted_reference
        hostile = replace(self.fixture.references, roles=roles)
        with (
            self.fixture.patches(),
            self.assertRaisesRegex(
                MODULE.FreezeSnapshotRequestBuildError,
                "not canonical",
            ),
        ):
            MODULE.prepare_request(hostile, now=NOW)

        original = self.fixture.references.roles["bot_fi"]["binding"].path
        hard_link = original.with_name("hard-linked-binding.json")
        os.link(original, hard_link)
        hard_link_reference = MODULE.Reference(
            hard_link,
            self.fixture.references.roles["bot_fi"][
                "binding"
            ].sha256,
        )
        roles["bot_fi"]["binding"] = hard_link_reference
        hostile = replace(self.fixture.references, roles=roles)
        with (
            self.fixture.patches(),
            self.assertRaisesRegex(
                MODULE.FreezeSnapshotRequestBuildError,
                "unsafe or differs",
            ),
        ):
            MODULE.prepare_request(hostile, now=NOW)

    def test_missing_prior_or_digest_mismatch_fails_before_publication(self):
        prior = dict(self.fixture.references.prior_phase_evidence)
        prior.pop(next(iter(prior)))
        missing = replace(
            self.fixture.references,
            prior_phase_evidence=prior,
        )
        with self.assertRaisesRegex(
            MODULE.FreezeSnapshotRequestBuildError,
            "closure is not exact",
        ):
            MODULE.prepare_request(missing, now=NOW)

        wrong = replace(
            self.fixture.references,
            approval=replace(
                self.fixture.references.approval,
                sha256="f" * 64,
            ),
        )
        with (
            self.fixture.patches(),
            self.assertRaisesRegex(
                MODULE.FreezeSnapshotRequestBuildError,
                "unsafe or differs",
            ),
        ):
            MODULE.prepare_request(wrong, now=NOW)

    def test_existing_conflict_is_never_replaced(self):
        with self.fixture.patches():
            plan = MODULE.execute(
                self.fixture.references,
                now=NOW,
            )
        output = Path(plan["output"])
        ensure_private_directory(output.parent)
        output.write_bytes(b"{}\n")
        output.chmod(0o600)
        with (
            self.fixture.patches(),
            self.assertRaises(MODULE.FreezeSnapshotRequestBuildError),
        ):
            MODULE.execute(
                self.fixture.references,
                apply=True,
                confirm=plan["required_confirmation"],
            )
        self.assertEqual(output.read_bytes(), b"{}\n")

    def test_cli_generic_failure_redacts_internal_error(self):
        with mock.patch.object(
            MODULE,
            "execute",
            side_effect=RuntimeError("secret internal detail"),
        ):
            stream = io.BytesIO()
            stdout = mock.Mock()
            stdout.buffer = stream
            with mock.patch.object(MODULE.sys, "stdout", stdout):
                status = MODULE.main(self.fixture.arguments())
        result = json.loads(stream.getvalue().decode("ascii"))
        self.assertEqual(status, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("secret", json.dumps(result))
        self.assertFalse(result["network_io"])
        self.assertFalse(result["journal_mutated"])

    def test_cli_apply_uses_digest_bound_confirmation(self):
        status, plan = self.run_main(self.fixture.arguments())
        self.assertEqual(status, 0)
        status, result = self.run_main(
            self.fixture.arguments()
            + [
                "--apply",
                "--confirm",
                plan["required_confirmation"],
            ]
        )
        self.assertEqual(status, 0)
        self.assertEqual(result["request_sha256"], plan["request_sha256"])
        self.assertEqual(result["status"], "published")


if __name__ == "__main__":
    unittest.main()
