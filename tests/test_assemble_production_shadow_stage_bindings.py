from __future__ import annotations

from contextlib import redirect_stdout
import copy
import hashlib
from importlib.util import find_spec
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

from scripts import assemble_production_shadow_stage_bindings as MODULE


if find_spec("scripts.produce_production_shadow_prepare_material") is None:
    PREPARE_CONSUMER = None
else:
    from scripts import (
        produce_production_shadow_prepare_material as PREPARE_CONSUMER,
    )


OPERATION_ID = "123e4567-e89b-42d3-a456-426614174000"
OTHER_OPERATION_ID = "223e4567-e89b-42d3-a456-426614174000"
RELEASE_SHA = "a" * 40


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


class AssemblyFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.output = root / "stage-bindings.json"
        self.paths = {
            role: root / f"{role}-stage-summary.json"
            for role in MODULE.ALL_ROLES
        }
        image_characters = {
            "bot_fi": ("1", "2", "3", "4"),
            "webapp_fi": ("5", "6", "7", "8"),
            "webapp_ir": ("9", "a", "b", "c"),
        }
        self.documents = {}
        for index, role in enumerate(MODULE.ALL_ROLES):
            runtime_image_ids = (
                {
                    kind: "sha256:" + character * 64
                    for kind, character in zip(
                        MODULE.IMAGE_KINDS,
                        image_characters[role],
                    )
                }
                if role in MODULE.DOCKER_ROLES
                else {}
            )
            self.documents[role] = {
                "schema": MODULE.ROLE_SUMMARY_SCHEMA,
                "operation_id": OPERATION_ID,
                "release_sha": RELEASE_SHA,
                "role": role,
                "stage_operation_manifest_sha256": hashlib.sha256(
                    f"{role}:manifest".encode("ascii")
                ).hexdigest(),
                "stage_attestation_sha256": hashlib.sha256(
                    f"{role}:attestation".encode("ascii")
                ).hexdigest(),
                "runtime_image_ids": runtime_image_ids,
            }
        self.write_all()

    def write(self, role: str, payload: bytes | None = None) -> None:
        self.paths[role].write_bytes(
            canonical_json(self.documents[role])
            if payload is None
            else payload
        )
        self.paths[role].chmod(0o600)

    def write_all(self) -> None:
        for role in MODULE.ALL_ROLES:
            self.write(role)

    def preflight(self) -> MODULE.AssemblyPreflight:
        return MODULE.preflight_assembly(self.paths, self.output)

    def confirmation(self) -> str:
        return MODULE._confirmation(OPERATION_ID, RELEASE_SHA)

    def argv(
        self,
        *,
        apply: bool = False,
        confirm: str | None = None,
    ) -> list[str]:
        arguments = [
            "--bot-fi",
            str(self.paths["bot_fi"]),
            "--webapp-fi",
            str(self.paths["webapp_fi"]),
            "--webapp-ir",
            str(self.paths["webapp_ir"]),
            "--witness",
            str(self.paths["witness"]),
            "--output",
            str(self.output),
        ]
        if apply:
            arguments.append("--apply")
        if confirm is not None:
            arguments.extend(["--confirm", confirm])
        return arguments

    def invoke(
        self,
        *,
        apply: bool = False,
        confirm: str | None = None,
    ) -> tuple[int, dict, str]:
        captured = io.StringIO()
        with redirect_stdout(captured):
            status = MODULE.main(
                self.argv(apply=apply, confirm=confirm)
            )
        raw = captured.getvalue()
        return status, json.loads(raw), raw


@unittest.skipUnless(
    os.geteuid() == 0,
    "the controller-only contract requires uid 0",
)
class ProductionShadowStageBindingAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.fixture = AssemblyFixture(self.root / "fixture")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fresh_fixture(self, name: str) -> AssemblyFixture:
        return AssemblyFixture(self.root / name)

    def test_default_plan_is_deterministic_and_does_not_mutate_output(self):
        first_status, first, first_raw = self.fixture.invoke()
        second_status, second, second_raw = self.fixture.invoke()
        self.assertEqual(first_status, 0)
        self.assertEqual(second_status, 0)
        self.assertEqual(first, second)
        self.assertEqual(first_raw, second_raw)
        self.assertEqual(first["status"], "planned")
        self.assertEqual(first["operation_id"], OPERATION_ID)
        self.assertEqual(first["release_sha"], RELEASE_SHA)
        self.assertEqual(
            first["required_confirmation"],
            self.fixture.confirmation(),
        )
        self.assertEqual(
            {
                role: row["runtime_image_id_count"]
                for role, row in first["roles"].items()
            },
            {
                "bot_fi": 4,
                "webapp_fi": 4,
                "webapp_ir": 4,
                "witness": 0,
            },
        )
        self.assertFalse(self.fixture.output.exists())
        self.assertFalse(
            list(self.fixture.root.glob(
                ".production-shadow-stage-bindings-*.tmp"
            ))
        )
        self.assertNotIn(str(self.fixture.root), first_raw)
        self.assertNotIn("://", first_raw)
        self.assertNotIn("secret", first_raw.lower())

    def test_apply_requires_exact_confirmation_and_is_idempotent(self):
        status, blocked, _raw = self.fixture.invoke(
            apply=True,
            confirm="assemble-production-shadow-stage-bindings:wrong",
        )
        self.assertEqual(status, 1)
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(self.fixture.output.exists())

        status, created, _raw = self.fixture.invoke(
            apply=True,
            confirm=self.fixture.confirmation(),
        )
        self.assertEqual(status, 0)
        self.assertEqual(created["status"], "created")
        payload = self.fixture.output.read_bytes()
        document = json.loads(payload)
        self.assertEqual(payload, canonical_json(document))
        self.assertFalse(payload.endswith(b"\n"))
        metadata = self.fixture.output.stat(follow_symlinks=False)
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(metadata.st_uid, 0)
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_nlink, 1)
        identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mtime_ns,
        )

        status, reused, _raw = self.fixture.invoke(
            apply=True,
            confirm=self.fixture.confirmation(),
        )
        self.assertEqual(status, 0)
        self.assertEqual(reused["status"], "reused")
        after = self.fixture.output.stat(follow_symlinks=False)
        self.assertEqual(
            (after.st_dev, after.st_ino, after.st_mtime_ns),
            identity,
        )
        self.assertEqual(self.fixture.output.read_bytes(), payload)
        self.assertFalse(
            list(self.fixture.root.glob(
                ".production-shadow-stage-bindings-*.tmp"
            ))
        )

    def test_output_contract_has_exact_prepare_consumer_shape(self):
        preflight = self.fixture.preflight()
        document = preflight.document
        self.assertEqual(set(document), MODULE.OUTPUT_FIELDS)
        self.assertEqual(
            document["schema"],
            "production-shadow-image-stage-bindings-v1",
        )
        self.assertEqual(set(document["roles"]), set(MODULE.ALL_ROLES))
        for role, row in document["roles"].items():
            self.assertEqual(set(row), MODULE.OUTPUT_ROLE_FIELDS)
            self.assertNotIn("schema", row)
            self.assertNotIn("operation_id", row)
            self.assertNotIn("release_sha", row)
            self.assertNotIn("role", row)
            self.assertEqual(
                set(row["runtime_image_ids"]),
                (
                    set(MODULE.IMAGE_KINDS)
                    if role in MODULE.DOCKER_ROLES
                    else set()
                ),
            )

    def test_real_prepare_consumer_accepts_assembled_document(self):
        if PREPARE_CONSUMER is None:
            self.skipTest(
                "prepare consumer is newer than this isolated base"
            )
        preflight = self.fixture.preflight()
        self.assertEqual(
            MODULE.publish_assembly(preflight),
            "created",
        )
        document, raw = PREPARE_CONSUMER._read_json_secure(
            self.fixture.output,
            label="assembled production shadow stage bindings",
            required_uid=0,
        )
        self.assertEqual(raw, preflight.payload)
        accepted = PREPARE_CONSUMER._validate_stage_bindings(
            document,
            operation_id=OPERATION_ID,
            release_sha=RELEASE_SHA,
        )
        self.assertEqual(accepted, document["roles"])

    def test_swapped_role_summaries_are_rejected(self):
        bot = self.fixture.paths["bot_fi"].read_bytes()
        web = self.fixture.paths["webapp_fi"].read_bytes()
        self.fixture.write("bot_fi", web)
        self.fixture.write("webapp_fi", bot)
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "role differs",
        ):
            self.fixture.preflight()
        self.assertFalse(self.fixture.output.exists())

    def test_mixed_operation_or_release_is_rejected(self):
        cases = {
            "operation": ("operation_id", OTHER_OPERATION_ID),
            "release": ("release_sha", "b" * 40),
        }
        for name, (field, value) in cases.items():
            with self.subTest(name=name):
                fixture = self.fresh_fixture(f"mixed-{name}")
                fixture.documents["witness"][field] = value
                fixture.write("witness")
                with self.assertRaisesRegex(
                    MODULE.StageBindingAssemblyError,
                    "do not bind one operation and release",
                ):
                    fixture.preflight()
                self.assertFalse(fixture.output.exists())

    def test_malformed_identity_hash_and_runtime_image_ids_are_rejected(self):
        cases = {
            "uuid": (
                "bot_fi",
                lambda document: document.__setitem__(
                    "operation_id",
                    "123e4567-e89b-12d3-a456-426614174000",
                ),
                "UUIDv4",
            ),
            "release-zero": (
                "bot_fi",
                lambda document: document.__setitem__(
                    "release_sha",
                    "0" * 40,
                ),
                "release SHA",
            ),
            "manifest-zero": (
                "webapp_fi",
                lambda document: document.__setitem__(
                    "stage_operation_manifest_sha256",
                    "0" * 64,
                ),
                "nonzero SHA-256",
            ),
            "attestation-uppercase": (
                "webapp_ir",
                lambda document: document.__setitem__(
                    "stage_attestation_sha256",
                    "A" * 64,
                ),
                "nonzero SHA-256",
            ),
            "image-zero": (
                "bot_fi",
                lambda document: document["runtime_image_ids"].__setitem__(
                    "app",
                    "sha256:" + "0" * 64,
                ),
                "runtime image IDs",
            ),
            "image-duplicate": (
                "webapp_fi",
                lambda document: document["runtime_image_ids"].__setitem__(
                    "app",
                    document["runtime_image_ids"]["postgres"],
                ),
                "runtime image IDs",
            ),
            "image-key-missing": (
                "webapp_ir",
                lambda document: document["runtime_image_ids"].pop("nginx"),
                "runtime image IDs",
            ),
            "witness-image": (
                "witness",
                lambda document: document["runtime_image_ids"].__setitem__(
                    "app",
                    "sha256:" + "d" * 64,
                ),
                "runtime image IDs",
            ),
        }
        for name, (role, mutate, message) in cases.items():
            with self.subTest(name=name):
                fixture = self.fresh_fixture(f"malformed-{name}")
                mutate(fixture.documents[role])
                fixture.write(role)
                with self.assertRaisesRegex(
                    MODULE.StageBindingAssemblyError,
                    message,
                ):
                    fixture.preflight()
                self.assertFalse(fixture.output.exists())

    def test_input_json_schema_and_fields_are_exact(self):
        cases = {
            "schema": lambda document: document.__setitem__(
                "schema",
                "wrong-schema",
            ),
            "extra": lambda document: document.__setitem__(
                "unexpected",
                True,
            ),
            "missing": lambda document: document.pop(
                "stage_attestation_sha256"
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                fixture = self.fresh_fixture(f"shape-{name}")
                mutate(fixture.documents["bot_fi"])
                fixture.write("bot_fi")
                with self.assertRaises(MODULE.StageBindingAssemblyError):
                    fixture.preflight()

    def test_noncanonical_or_unsafe_input_files_fail_closed(self):
        newline = self.fresh_fixture("input-newline")
        newline.write(
            "bot_fi",
            canonical_json(newline.documents["bot_fi"]) + b"\n",
        )
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "not canonical JSON",
        ):
            newline.preflight()

        unsafe_mode = self.fresh_fixture("input-mode")
        unsafe_mode.paths["bot_fi"].chmod(0o640)
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "root-only 0600",
        ):
            unsafe_mode.preflight()

        hardlink = self.fresh_fixture("input-hardlink")
        alias = hardlink.root / "input-alias.json"
        os.link(hardlink.paths["bot_fi"], alias)
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "root-only 0600",
        ):
            hardlink.preflight()

        symlink = self.fresh_fixture("input-symlink")
        symlink.paths["bot_fi"].unlink()
        symlink.paths["bot_fi"].symlink_to(
            symlink.paths["webapp_fi"]
        )
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "unavailable or unsafe",
        ):
            symlink.preflight()

    def test_all_paths_must_be_absolute_canonical_and_distinct(self):
        role_paths = dict(self.fixture.paths)
        role_paths["bot_fi"] = Path("relative-summary.json")
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "absolute canonical path",
        ):
            MODULE.preflight_assembly(role_paths, self.fixture.output)

        role_paths = dict(self.fixture.paths)
        role_paths["webapp_fi"] = role_paths["bot_fi"]
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "paths must be distinct",
        ):
            MODULE.preflight_assembly(role_paths, self.fixture.output)

        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "paths must be distinct",
        ):
            MODULE.preflight_assembly(
                self.fixture.paths,
                self.fixture.paths["witness"],
            )

        noncanonical_output = (
            self.fixture.root / "nested" / ".." / "output.json"
        )
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "absolute canonical path",
        ):
            MODULE.preflight_assembly(
                self.fixture.paths,
                noncanonical_output,
            )

    def test_existing_output_conflict_and_tamper_fail_closed(self):
        conflict = self.fresh_fixture("output-conflict")
        conflict.output.write_bytes(b"{}")
        conflict.output.chmod(0o600)
        before = conflict.output.read_bytes()
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "output differs",
        ):
            conflict.preflight()
        self.assertEqual(conflict.output.read_bytes(), before)

        tamper = self.fresh_fixture("output-tamper")
        plan = tamper.preflight()
        self.assertEqual(MODULE.publish_assembly(plan), "created")
        tamper.output.write_bytes(plan.payload[:-1] + b"\n")
        tamper.output.chmod(0o600)
        poisoned = tamper.output.read_bytes()
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "output differs",
        ):
            tamper.preflight()
        self.assertEqual(tamper.output.read_bytes(), poisoned)

    def test_symlink_hardlink_and_unsafe_output_mode_fail_closed(self):
        symlink = self.fresh_fixture("output-symlink")
        symlink.output.symlink_to(symlink.paths["bot_fi"])
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "unavailable or unsafe",
        ):
            symlink.preflight()

        hardlink = self.fresh_fixture("output-hardlink")
        plan = hardlink.preflight()
        MODULE.publish_assembly(plan)
        os.link(hardlink.output, hardlink.root / "output-alias.json")
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "root-only 0600",
        ):
            hardlink.preflight()

        unsafe_mode = self.fresh_fixture("output-mode")
        plan = unsafe_mode.preflight()
        MODULE.publish_assembly(plan)
        unsafe_mode.output.chmod(0o640)
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "root-only 0600",
        ):
            unsafe_mode.preflight()

        unsafe_parent = self.fresh_fixture("output-parent-mode")
        unsafe_parent.root.chmod(0o755)
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "root-owned 0700",
        ):
            unsafe_parent.preflight()

    def test_unsafe_deterministic_temporary_fails_without_mutation(self):
        preflight = self.fixture.preflight()
        temporary = self.fixture.root / MODULE._temporary_name(
            self.fixture.output,
            preflight.payload,
        )
        temporary.write_bytes(b"tampered")
        temporary.chmod(0o600)
        before = temporary.read_bytes()
        with self.assertRaisesRegex(
            MODULE.StageBindingAssemblyError,
            "temporary differs",
        ):
            self.fixture.preflight()
        self.assertFalse(self.fixture.output.exists())
        self.assertEqual(temporary.read_bytes(), before)

    def test_exact_temporary_is_resumed_create_only(self):
        preflight = self.fixture.preflight()
        temporary = self.fixture.root / MODULE._temporary_name(
            self.fixture.output,
            preflight.payload,
        )
        temporary.write_bytes(preflight.payload)
        temporary.chmod(0o600)
        resumed = self.fixture.preflight()
        self.assertEqual(
            resumed.output_state,
            "recoverable-temporary",
        )
        self.assertEqual(MODULE.publish_assembly(resumed), "created")
        self.assertEqual(
            self.fixture.output.read_bytes(),
            preflight.payload,
        )
        self.assertFalse(temporary.exists())

    def test_invalid_last_input_is_preflighted_before_any_output_write(self):
        self.fixture.documents["witness"][
            "stage_attestation_sha256"
        ] = "0" * 64
        self.fixture.write("witness")
        status, result, _raw = self.fixture.invoke(
            apply=True,
            confirm=self.fixture.confirmation(),
        )
        self.assertEqual(status, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.fixture.output.exists())
        self.assertFalse(
            list(self.fixture.root.glob(
                ".production-shadow-stage-bindings-*.tmp"
            ))
        )

    def test_confirm_is_apply_only_and_nonroot_is_rejected(self):
        status, result, _raw = self.fixture.invoke(
            confirm=self.fixture.confirmation(),
        )
        self.assertEqual(status, 1)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(self.fixture.output.exists())

        with (
            mock.patch.object(MODULE.os, "geteuid", return_value=1000),
            self.assertRaisesRegex(
                MODULE.StageBindingAssemblyError,
                "controller root",
            ),
        ):
            self.fixture.preflight()


if __name__ == "__main__":
    unittest.main()
