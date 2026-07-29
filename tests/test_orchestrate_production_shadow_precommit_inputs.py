from __future__ import annotations

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import orchestrate_production_shadow_precommit_inputs as MODULE
from scripts import production_shadow_cutover_controller as CUTOVER
from scripts import production_shadow_precommit_worker as WORKER
from tests.test_install_production_shadow_precommit_inputs import (
    InstallFixture,
    canonical,
    secure_file,
)
from tests.test_production_shadow_cutover_controller import (
    manifest_payload,
    write_controller_manifest,
)


def completed(
    payload: object,
    *,
    returncode: int = 0,
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    stdout = (
        payload
        if isinstance(payload, bytes)
        else MODULE._canonical_json(payload) + b"\n"  # noqa: SLF001
    )
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def input_file(path: Path, key: str, filename: str) -> MODULE.InputFile:
    payload = path.read_bytes()
    metadata = path.stat(follow_symlinks=False)
    return MODULE.InputFile(
        key=key,
        filename=filename,
        path=path,
        sha256=hashlib.sha256(payload).hexdigest(),
        bytes=len(payload),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        uid=metadata.st_uid,
        gid=metadata.st_gid,
        links=metadata.st_nlink,
        mtime_ns=metadata.st_mtime_ns,
        ctime_ns=metadata.st_ctime_ns,
    )


def fake_closure(root: Path) -> MODULE.ControllerClosure:
    controller = manifest_payload()
    roles: dict[str, MODULE.RoleClosure] = {}
    filenames = {
        "precommit_manifest": MODULE.ROLE_FILENAMES[
            "precommit_manifest"
        ],
        "source_snapshot_manifest": MODULE.ROLE_FILENAMES[
            "source_snapshot_manifest"
        ],
        "database": MODULE.ROLE_FILENAMES["database"],
        "uploads": MODULE.ROLE_FILENAMES["uploads"],
        "audit": MODULE.ROLE_FILENAMES["audit"],
    }
    for role in MODULE.ROLE_ORDER:
        role_root = root / role
        role_root.mkdir(parents=True, mode=0o700)
        role_filenames = {
            **filenames,
            "role_material": MODULE.ROLE_FILENAMES["role_material"][role],
        }
        inputs: dict[str, MODULE.InputFile] = {}
        for key, filename in role_filenames.items():
            path = role_root / filename
            secure_file(path, f"{role}:{key}\n".encode("ascii"))
            inputs[key] = input_file(path, key, filename)
        roles[role] = MODULE.RoleClosure(
            role=role,
            manifest=SimpleNamespace(),
            inputs=inputs,
            installed_bindings={
                kind: {"sha256": "f" * 64, "bytes": 1}
                for kind in (
                    "role_compose",
                    "runtime_environment",
                    "ca_certificate",
                )
            },
        )
    manifest_sha256 = hashlib.sha256(
        MODULE._canonical_json(controller)  # noqa: SLF001
    ).hexdigest()
    public = {
        "controller_manifest_sha256": manifest_sha256,
        "roles": {
            role: roles[role].public_inventory()
            for role in MODULE.ROLE_ORDER
        },
    }
    return MODULE.ControllerClosure(
        manifest=controller,
        manifest_sha256=manifest_sha256,
        roles=roles,
        closure_sha256=hashlib.sha256(
            MODULE._canonical_json(public)  # noqa: SLF001
        ).hexdigest(),
    )


def artifact_attestation(role: str) -> dict[str, object]:
    return {
        "schema": MODULE.ATTESTATION_SCHEMA,
        "status": "verified",
        "role": role,
        "operation_id": "22222222-2222-4222-8222-222222222222",
        "release_sha": "a" * 40,
        "controller_manifest_sha256": "manifest",
        "host_agent_contract_sha256": CUTOVER.HOST_AGENT_CONTRACT_SHA256,
        "artifacts": {},
        "docker_invoked": False,
        "service_mutated": False,
        "current_mutated": False,
        "source_mutated": False,
        "object_storage_mutated": False,
    }


def host_input_request(role: str) -> tuple[dict[str, object], dict[str, bytes]]:
    payloads = {
        "precommit_manifest": b"precommit\n",
        "role_material": b"role material\n",
        "source_snapshot_manifest": b"source manifest",
        "database": b"database\n",
        "uploads": b"uploads\n",
        "audit": b"audit\n",
    }
    filenames = {
        "precommit_manifest": MODULE.ROLE_FILENAMES[
            "precommit_manifest"
        ],
        "role_material": MODULE.ROLE_FILENAMES["role_material"][role],
        "source_snapshot_manifest": MODULE.ROLE_FILENAMES[
            "source_snapshot_manifest"
        ],
        "database": MODULE.ROLE_FILENAMES["database"],
        "uploads": MODULE.ROLE_FILENAMES["uploads"],
        "audit": MODULE.ROLE_FILENAMES["audit"],
    }
    request = {
        "role": role,
        "operation_id": "22222222-2222-4222-8222-222222222222",
        "inputs": {
            key: {
                "filename": filenames[key],
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
            for key, payload in payloads.items()
        },
    }
    return request, payloads


class ControllerInputClosureTests(unittest.TestCase):
    def _bound_fixture(
        self,
        root: Path,
        role: str,
    ) -> tuple[InstallFixture, dict[str, object], str, Path]:
        fixture = InstallFixture(root, role)
        controller = manifest_payload()
        controller["release_tree_sha"] = fixture.precommit_document[
            "release_tree_sha"
        ]
        controller["legacy_release_sha"] = fixture.source_document[
            "legacy_release_sha"
        ]
        artifacts = controller["artifacts"]
        artifacts["release_bundle_sha256"] = hashlib.sha256(
            fixture.bundle
        ).hexdigest()
        artifacts["release_bundle_bytes"] = len(fixture.bundle)
        artifacts["shadow_compose_sha256"] = fixture.precommit_document[
            "canonical_compose_sha256"
        ]
        artifacts["cutover_approval_sha256"] = "7" * 64
        artifacts["role_materials"][role] = {
            "sha256": hashlib.sha256(fixture.role_archive).hexdigest(),
            "bytes": len(fixture.role_archive),
            "transport": controller["topology"][role]["transport"],
            "format": "production-shadow-role-material-tar",
        }
        artifacts["image_artifacts"] = copy.deepcopy(fixture.image_rows)
        artifacts["role_runtime_image_ids"][role] = dict(
            fixture.runtime_ids
        )
        validated = CUTOVER.validate_manifest(controller)
        controller_path = root / "controller.json"
        write_controller_manifest(controller_path, validated)
        controller_sha256 = hashlib.sha256(
            controller_path.read_bytes()
        ).hexdigest()
        fixture.precommit_document["controller_manifest_sha256"] = (
            controller_sha256
        )
        fixture.precommit_document["approval_sha256"] = artifacts[
            "cutover_approval_sha256"
        ]
        fixture.write_precommit()
        fixture.source_document["controller_manifest_sha256"] = (
            controller_sha256
        )
        fixture.source_document["approval_sha256"] = artifacts[
            "cutover_approval_sha256"
        ]
        secure_file(
            fixture.source_manifest_path,
            canonical(fixture.source_document),
        )
        return fixture, validated, controller_sha256, controller_path

    def test_real_installer_loaders_accept_exact_bound_role_closure(self) -> None:
        for role in MODULE.ROLE_ORDER:
            with self.subTest(role=role), tempfile.TemporaryDirectory() as raw:
                fixture, controller, digest, _ = self._bound_fixture(
                    Path(raw),
                    role,
                )
                try:
                    closure = MODULE._load_role_closure(  # noqa: SLF001
                        controller=controller,
                        controller_sha256=digest,
                        role=role,
                        precommit_manifest=fixture.precommit_path,
                        role_material=fixture.role_material_path,
                        source_snapshot_manifest=(
                            fixture.source_manifest_path
                        ),
                    )
                    self.assertEqual(
                        set(closure.inputs),
                        {
                            "precommit_manifest",
                            "role_material",
                            "source_snapshot_manifest",
                            "database",
                            "uploads",
                            "audit",
                        },
                    )
                    self.assertEqual(
                        closure.manifest.controller_manifest_sha256,
                        digest,
                    )
                finally:
                    fixture.close()

    def test_controller_and_source_cross_binding_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture, controller, digest, _ = self._bound_fixture(
                Path(raw),
                "bot_fi",
            )
            try:
                fixture.source_document["legacy_release_sha"] = "d" * 40
                secure_file(
                    fixture.source_manifest_path,
                    canonical(fixture.source_document),
                )
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "legacy release differs",
                ):
                    MODULE._load_role_closure(  # noqa: SLF001
                        controller=controller,
                        controller_sha256=digest,
                        role="bot_fi",
                        precommit_manifest=fixture.precommit_path,
                        role_material=fixture.role_material_path,
                        source_snapshot_manifest=(
                            fixture.source_manifest_path
                        ),
                    )

                fixture.source_document["legacy_release_sha"] = controller[
                    "legacy_release_sha"
                ]
                secure_file(
                    fixture.source_manifest_path,
                    canonical(fixture.source_document),
                )
                fixture.precommit_document["approval_sha256"] = "f" * 64
                fixture.write_precommit()
                fixture.source_document["approval_sha256"] = "f" * 64
                secure_file(
                    fixture.source_manifest_path,
                    canonical(fixture.source_document),
                )
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "differs from the controller",
                ):
                    MODULE._load_role_closure(  # noqa: SLF001
                        controller=controller,
                        controller_sha256=digest,
                        role="bot_fi",
                        precommit_manifest=fixture.precommit_path,
                        role_material=fixture.role_material_path,
                        source_snapshot_manifest=(
                            fixture.source_manifest_path
                        ),
                    )
            finally:
                fixture.close()

    def test_controller_manifest_requires_canonical_root_only_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            fixture, _controller, digest, controller_path = (
                self._bound_fixture(root, "bot_fi")
            )
            try:
                observed, observed_digest = (
                    MODULE._read_controller_manifest(  # noqa: SLF001
                        controller_path
                    )
                )
                self.assertEqual(observed_digest, digest)
                secure_file(
                    controller_path,
                    MODULE._canonical_json(observed) + b"\n",  # noqa: SLF001
                )
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "unavailable or invalid",
                ):
                    MODULE._read_controller_manifest(  # noqa: SLF001
                        controller_path
                    )
                controller_path.unlink()
                controller_path.symlink_to(fixture.precommit_path)
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "unavailable or invalid",
                ):
                    MODULE._read_controller_manifest(  # noqa: SLF001
                        controller_path
                    )
            finally:
                fixture.close()


class ControllerExecutionTests(unittest.TestCase):
    def test_default_runner_keeps_controller_stdin_live(self):
        code = (
            "import json,select\n"
            "readable=bool(select.select([0],[],[],0)[0])\n"
            "print(json.dumps({'stdin_eof':readable},"
            "sort_keys=True,separators=(',',':')))\n"
        )
        completed = MODULE._default_runner(
            [sys.executable, "-I", "-B", "-c", code],
            5,
            {"PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")
        self.assertEqual(completed.stdout, b'{"stdin_eof":false}\n')

    def test_default_runner_preserves_subsecond_wait_budget(self):
        process = mock.Mock()
        process.pid = 42
        process.stdin = mock.Mock()
        process.stdout.fileno.return_value = 10
        process.stderr.fileno.return_value = 11
        process.wait.return_value = 0
        selector = mock.Mock()
        selector.get_map.return_value = {}
        with (
            mock.patch.object(
                MODULE.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                MODULE.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(MODULE.os, "set_blocking"),
            mock.patch.object(MODULE.time, "monotonic", side_effect=(10, 10.75)),
            mock.patch.object(MODULE, "_terminate_process_group"),
        ):
            completed = MODULE._default_runner(
                ["/usr/bin/true"],
                1,
                {"PATH": "/usr/bin:/bin"},
            )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(process.wait.call_args, mock.call(timeout=0.25))

    def test_default_runner_kills_forked_descendant_after_parent_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "controller-descendant-survived"
            descendant_pid = Path(directory) / "controller-descendant-pid"
            code = (
                "import os,signal,time\n"
                "if os.fork() == 0:\n"
                " os.setsid()\n"
                " if os.fork() != 0: time.sleep(60);os._exit(0)\n"
                " os.close(1);os.close(2)\n"
                " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f" open({str(descendant_pid)!r},'w').write(str(os.getpid()))\n"
                " time.sleep(0.5)\n"
                f" open({str(sentinel)!r},'wb').write(b'survived')\n"
                " os._exit(0)\n"
                f"while not os.path.exists({str(descendant_pid)!r}):"
                " time.sleep(0.005)\n"
                "print('{}',flush=True)\n"
            )
            with mock.patch.object(
                MODULE,
                "PROCESS_GROUP_TERM_SECONDS",
                0.1,
            ):
                completed = MODULE._default_runner(
                    [sys.executable, "-I", "-B", "-c", code],
                    5,
                    {"PATH": "/usr/bin:/bin"},
                )
            self.assertEqual(completed.stdout, b"{}\n")
            time.sleep(0.6)
            self.assertFalse(sentinel.exists())
            self.assertFalse(
                Path(
                    f"/proc/{descendant_pid.read_text(encoding='ascii')}"
                ).exists()
            )

    def test_default_runner_bounds_output_while_process_runs(self):
        with (
            mock.patch.object(MODULE, "MAX_JSON_BYTES", 1024),
            self.assertRaises(MODULE.BoundedRunnerError),
        ):
            MODULE._default_runner(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    "import os;os.write(1,b'x'*4096)",
                ],
                5,
                {"PATH": "/usr/bin:/bin"},
            )

    def test_default_plan_and_wrong_confirmation_are_zero_runner_zero_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            closure = fake_closure(root)
            secure_root = root / "must-not-exist"
            runner = mock.Mock()
            arguments = {
                "controller_manifest": root / "controller",
                "bot_precommit_manifest": root / "bot-precommit",
                "webapp_precommit_manifest": root / "web-precommit",
                "bot_role_material": root / "bot-role",
                "webapp_role_material": root / "web-role",
                "bot_source_snapshot_manifest": root / "bot-source",
                "webapp_source_snapshot_manifest": root / "web-source",
            }
            with (
                mock.patch.object(
                    MODULE,
                    "load_controller_closure",
                    return_value=closure,
                ),
                mock.patch.object(MODULE, "SECURE_ROOT", secure_root),
            ):
                plan = MODULE.orchestrate(**arguments, runner=runner)
                self.assertEqual(plan["status"], "planned")
                self.assertFalse(plan["runner_invoked"])
                self.assertFalse(plan["network_io"])
                self.assertFalse(secure_root.exists())
                runner.assert_not_called()

                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "requires --confirm",
                ):
                    MODULE.orchestrate(
                        **arguments,
                        apply=True,
                        confirm="wrong",
                        runner=runner,
                    )
                self.assertFalse(secure_root.exists())
                runner.assert_not_called()

    def test_controller_source_metadata_is_immutable_during_apply(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            closure = fake_closure(Path(raw))
            item = closure.roles["bot_fi"].inputs["database"]
            MODULE._rehash_input(item)  # noqa: SLF001
            os.utime(item.path, ns=(item.mtime_ns + 1, item.mtime_ns + 1))
            with self.assertRaisesRegex(
                MODULE.PrecommitInputOrchestrationError,
                "controller source changed",
            ):
                MODULE._rehash_input(item)  # noqa: SLF001

    def test_pinned_local_and_webapp_ssh_scp_argv_have_no_other_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            closure = fake_closure(Path(raw))
            known_hosts = Path("/root/.ssh/known_hosts.production")
            identity = Path("/root/.ssh/id_ed25519")
            local = MODULE._host_argv(  # noqa: SLF001
                closure,
                "bot_fi",
                action="prepare",
                known_hosts=known_hosts,
                identity_file=identity,
            )
            remote = MODULE._host_argv(  # noqa: SLF001
                closure,
                "webapp_fi",
                action="install",
                known_hosts=known_hosts,
                identity_file=identity,
            )
            item = next(
                iter(closure.roles["webapp_fi"].inputs.values())
            )
            scp = MODULE._scp_argv(  # noqa: SLF001
                closure,
                item,
                Path(
                    "/root/secure-envs/trading-bot/"
                    "three-site-production-shadow/"
                    "22222222-2222-4222-8222-222222222222/"
                    "webapp-fi/precommit-inputs/incoming/.x.transfer"
                ),
                known_hosts=known_hosts,
                identity_file=identity,
            )
            self.assertEqual(local[:3], [MODULE.PYTHON3, "-I", "-B"])
            self.assertEqual(remote[0], MODULE.SSH)
            self.assertIn("-p", remote)
            self.assertEqual(remote[remote.index("-p") + 1], "37067")
            self.assertIn("root@65.109.220.59", remote)
            self.assertIn("StrictHostKeyChecking=yes", remote)
            self.assertIn("ClearAllForwardings=yes", remote)
            self.assertEqual(scp[0], MODULE.SCP)
            self.assertEqual(scp[scp.index("-P") + 1], "37067")
            self.assertNotIn("--", scp)
            flattened = "\n".join((*local, *remote, *scp))
            for forbidden in (
                "95.38.164.29",
                "37.152.191.11",
                "185.206.95.94",
                "s3",
                "object-storage",
            ):
                self.assertNotIn(forbidden, flattened.lower())
            for token in remote:
                self.assertRegex(token, MODULE.SAFE_TOKEN_RE)

    def test_install_result_requires_exact_nonsecret_readback_paths_and_hashes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure_root = root / "secure"
            closure = fake_closure(root)
            role = "bot_fi"
            role_closure = closure.roles[role]
            operation_id = closure.manifest["operation_id"]
            with (
                mock.patch.object(MODULE, "SECURE_ROOT", secure_root),
                mock.patch.multiple(
                    WORKER,
                    PROJECT_ROOT_PREFIX=root / "project",
                    DATA_ROOT_PREFIX=root / "data",
                    SECRET_ROOT_PREFIX=root / "secret",
                ),
            ):
                paths = WORKER.operation_paths(
                    operation_id,
                    closure.manifest["release_sha"],
                    role,
                )
                incoming = MODULE._incoming_directory(  # noqa: SLF001
                    operation_id,
                    role,
                )
                contract_payload = MODULE._canonical_json(  # noqa: SLF001
                    CUTOVER.host_agent_contract_document()
                )
                bindings = {
                    "precommit_manifest": (
                        paths.manifest,
                        role_closure.inputs["precommit_manifest"].sha256,
                        role_closure.inputs["precommit_manifest"].bytes,
                    ),
                    "role_material": (
                        paths.artifacts["role-material"],
                        role_closure.inputs["role_material"].sha256,
                        role_closure.inputs["role_material"].bytes,
                    ),
                    "role_compose": (
                        paths.compose,
                        role_closure.installed_bindings[
                            "role_compose"
                        ]["sha256"],
                        role_closure.installed_bindings[
                            "role_compose"
                        ]["bytes"],
                    ),
                    "runtime_environment": (
                        paths.environment,
                        role_closure.installed_bindings[
                            "runtime_environment"
                        ]["sha256"],
                        role_closure.installed_bindings[
                            "runtime_environment"
                        ]["bytes"],
                    ),
                    "ca_certificate": (
                        paths.secret_root / "tls" / "ca.crt",
                        role_closure.installed_bindings[
                            "ca_certificate"
                        ]["sha256"],
                        role_closure.installed_bindings[
                            "ca_certificate"
                        ]["bytes"],
                    ),
                    "source_snapshot_manifest": (
                        incoming
                        / role_closure.inputs[
                            "source_snapshot_manifest"
                        ].filename,
                        role_closure.inputs[
                            "source_snapshot_manifest"
                        ].sha256,
                        role_closure.inputs[
                            "source_snapshot_manifest"
                        ].bytes,
                    ),
                    "database": (
                        paths.artifacts["database-backup"],
                        role_closure.inputs["database"].sha256,
                        role_closure.inputs["database"].bytes,
                    ),
                    "uploads": (
                        paths.artifacts["uploads-archive"],
                        role_closure.inputs["uploads"].sha256,
                        role_closure.inputs["uploads"].bytes,
                    ),
                    "audit": (
                        paths.artifacts["audit-archive"],
                        role_closure.inputs["audit"].sha256,
                        role_closure.inputs["audit"].bytes,
                    ),
                    "host_agent_contract": (
                        secure_root
                        / operation_id
                        / "host-agent-contract.json",
                        CUTOVER.HOST_AGENT_CONTRACT_SHA256,
                        len(contract_payload),
                    ),
                }
                attestation = {
                    "schema": MODULE.ATTESTATION_SCHEMA,
                    "status": "verified",
                    "role": role,
                    "operation_id": operation_id,
                    "release_sha": closure.manifest["release_sha"],
                    "controller_manifest_sha256": (
                        closure.manifest_sha256
                    ),
                    "host_agent_contract_sha256": (
                        CUTOVER.HOST_AGENT_CONTRACT_SHA256
                    ),
                    "artifacts": {
                        kind: {
                            "path": str(path),
                            "sha256": digest,
                            "bytes": size,
                            "mode": "0600",
                            "device": 1,
                            "inode": index,
                            "links": 1,
                        }
                        for index, (
                            kind,
                            (path, digest, size),
                        ) in enumerate(bindings.items(), start=1)
                    },
                    "docker_invoked": False,
                    "service_mutated": False,
                    "current_mutated": False,
                    "source_mutated": False,
                    "object_storage_mutated": False,
                }
                result = {
                    "schema": MODULE.HOST_RESULT_SCHEMA,
                    "status": "installed",
                    "action": "install",
                    "role": role,
                    "operation_id": operation_id,
                    "release_sha": closure.manifest["release_sha"],
                    "release_tree_sha": closure.manifest[
                        "release_tree_sha"
                    ],
                    "controller_manifest_sha256": (
                        closure.manifest_sha256
                    ),
                    "host_agent_contract": str(
                        secure_root
                        / operation_id
                        / "host-agent-contract.json"
                    ),
                    "host_agent_contract_sha256": (
                        CUTOVER.HOST_AGENT_CONTRACT_SHA256
                    ),
                    "contract_publication": "reused",
                    "expected_host": closure.manifest["topology"][role][
                        "host"
                    ],
                    "observed_host": closure.manifest["topology"][role][
                        "host"
                    ],
                    "network_io": False,
                    "docker_invoked": False,
                    "service_mutated": False,
                    "current_mutated": False,
                    "source_mutated": False,
                    "object_storage_mutated": False,
                    "attestation": attestation,
                }
                MODULE._validate_host_result(  # noqa: SLF001
                    result,
                    closure,
                    role,
                    action="install",
                )
                result["attestation"]["artifacts"]["database"][
                    "sha256"
                ] = "0" * 64
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "database readback differs",
                ):
                    MODULE._validate_host_result(  # noqa: SLF001
                        result,
                        closure,
                        role,
                        action="install",
                    )

    def test_journal_resumes_after_web_failure_and_rechecks_completed_bot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            closure = fake_closure(root)
            secure_root = root / "secure"
            bot_attestation = artifact_attestation("bot_fi")
            web_attestation = artifact_attestation("webapp_fi")
            first_calls: list[tuple[str, str]] = []

            def first_invoke(
                _runner: object,
                _closure: object,
                role: str,
                *,
                action: str,
                **_kwargs: object,
            ) -> dict[str, object]:
                first_calls.append((role, action))
                if role == "webapp_fi":
                    raise MODULE.PrecommitInputOrchestrationError(
                        "simulated remote interruption"
                    )
                if action == "prepare":
                    return {"needed_files": []}
                return {"attestation": bot_attestation}

            with (
                mock.patch.object(MODULE, "SECURE_ROOT", secure_root),
                mock.patch.object(
                    MODULE,
                    "_invoke_host",
                    side_effect=first_invoke,
                ),
                mock.patch.object(MODULE, "_transfer_needed"),
                self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "simulated remote interruption",
                ),
            ):
                MODULE._apply(  # noqa: SLF001
                    closure,
                    runner=mock.Mock(),
                    known_hosts=Path("/root/.ssh/known_hosts"),
                    identity_file=Path("/root/.ssh/id_ed25519"),
                )
            self.assertEqual(
                first_calls,
                [
                    ("bot_fi", "prepare"),
                    ("bot_fi", "install"),
                    ("webapp_fi", "prepare"),
                ],
            )

            second_calls: list[tuple[str, str]] = []

            def second_invoke(
                _runner: object,
                _closure: object,
                role: str,
                *,
                action: str,
                **_kwargs: object,
            ) -> dict[str, object]:
                second_calls.append((role, action))
                if action == "prepare":
                    return {"needed_files": []}
                return {
                    "attestation": (
                        bot_attestation
                        if role == "bot_fi"
                        else web_attestation
                    )
                }

            with (
                mock.patch.object(MODULE, "SECURE_ROOT", secure_root),
                mock.patch.object(
                    MODULE,
                    "_invoke_host",
                    side_effect=second_invoke,
                ),
                mock.patch.object(MODULE, "_transfer_needed"),
            ):
                result = MODULE._apply(  # noqa: SLF001
                    closure,
                    runner=mock.Mock(),
                    known_hosts=Path("/root/.ssh/known_hosts"),
                    identity_file=Path("/root/.ssh/id_ed25519"),
                )
            self.assertEqual(
                second_calls,
                [
                    ("bot_fi", "install"),
                    ("webapp_fi", "prepare"),
                    ("webapp_fi", "install"),
                ],
            )
            self.assertEqual(result["status"], "completed")
            evidence = Path(result["evidence_path"])
            self.assertEqual(
                stat.S_IMODE(evidence.stat().st_mode),
                0o600,
            )
            journal_path = (
                secure_root
                / closure.manifest["operation_id"]
                / "controller"
                / "precommit-input-orchestrator"
                / "journal.json"
            )
            journal = json.loads(journal_path.read_text(encoding="ascii"))
            self.assertEqual(journal["status"], "completed")
            self.assertEqual(
                journal["completed_roles"],
                list(MODULE.ROLE_ORDER),
            )
            identities = {
                path: (path.stat().st_ino, path.stat().st_mtime_ns)
                for path in (journal_path, evidence)
            }
            with (
                mock.patch.object(MODULE, "SECURE_ROOT", secure_root),
                mock.patch.object(
                    MODULE,
                    "_invoke_host",
                    side_effect=second_invoke,
                ),
                mock.patch.object(MODULE, "_transfer_needed"),
            ):
                repeated = MODULE._apply(  # noqa: SLF001
                    closure,
                    runner=mock.Mock(),
                    known_hosts=Path("/root/.ssh/known_hosts"),
                    identity_file=Path("/root/.ssh/id_ed25519"),
                )
            self.assertEqual(
                repeated["evidence_publication"],
                "reused",
            )
            self.assertEqual(
                identities,
                {
                    path: (path.stat().st_ino, path.stat().st_mtime_ns)
                    for path in identities
                },
            )

    def test_completed_readback_tamper_blocks_without_republishing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            closure = fake_closure(root)
            secure_root = root / "secure"
            attestations = {
                role: artifact_attestation(role)
                for role in MODULE.ROLE_ORDER
            }

            def invoke(
                _runner: object,
                _closure: object,
                role: str,
                *,
                action: str,
                **_kwargs: object,
            ) -> dict[str, object]:
                if action == "prepare":
                    return {"needed_files": []}
                return {"attestation": attestations[role]}

            with (
                mock.patch.object(MODULE, "SECURE_ROOT", secure_root),
                mock.patch.object(
                    MODULE,
                    "_invoke_host",
                    side_effect=invoke,
                ),
                mock.patch.object(MODULE, "_transfer_needed"),
            ):
                MODULE._apply(  # noqa: SLF001
                    closure,
                    runner=mock.Mock(),
                    known_hosts=Path("/root/.ssh/known_hosts"),
                    identity_file=Path("/root/.ssh/id_ed25519"),
                )
            attestations["bot_fi"] = {
                **attestations["bot_fi"],
                "release_sha": "f" * 40,
            }
            with (
                mock.patch.object(MODULE, "SECURE_ROOT", secure_root),
                mock.patch.object(
                    MODULE,
                    "_invoke_host",
                    side_effect=invoke,
                ),
                mock.patch.object(MODULE, "_transfer_needed"),
                self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "readback differs",
                ),
            ):
                MODULE._apply(  # noqa: SLF001
                    closure,
                    runner=mock.Mock(),
                    known_hosts=Path("/root/.ssh/known_hosts"),
                    identity_file=Path("/root/.ssh/id_ed25519"),
                )


class HostInputStateTests(unittest.TestCase):
    def test_contract_is_exact_operation_scoped_create_only_and_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            secure_root = Path(raw) / "secure"
            operation_id = "22222222-2222-4222-8222-222222222222"
            with mock.patch.object(MODULE, "SECURE_ROOT", secure_root):
                path, first = MODULE._publish_contract(  # noqa: SLF001
                    operation_id
                )
                inode = path.stat().st_ino
                repeated_path, second = MODULE._publish_contract(  # noqa: SLF001
                    operation_id
                )
                self.assertEqual(first, "created")
                self.assertEqual(second, "reused")
                self.assertEqual(path, repeated_path)
                self.assertEqual(path.stat().st_ino, inode)
                self.assertEqual(
                    path,
                    secure_root
                    / operation_id
                    / "host-agent-contract.json",
                )
                self.assertEqual(
                    path.read_bytes(),
                    MODULE._canonical_json(  # noqa: SLF001
                        CUTOVER.host_agent_contract_document()
                    ),
                )
                self.assertFalse(path.read_bytes().endswith(b"\n"))
                self.assertEqual(
                    stat.S_IMODE(path.stat().st_mode),
                    0o600,
                )
                secure_file(path, b"conflict")
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "conflicts",
                ):
                    MODULE._publish_contract(operation_id)  # noqa: SLF001
                path.unlink()
                target = secure_root / operation_id / "elsewhere"
                secure_file(target, b"target")
                path.symlink_to(target)
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "unsafe",
                ):
                    MODULE._publish_contract(operation_id)  # noqa: SLF001

    def test_partial_transfer_promotes_create_only_and_recovers_hardlink_crash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            secure_root = Path(raw) / "secure"
            request, payloads = host_input_request("bot_fi")
            with mock.patch.object(MODULE, "SECURE_ROOT", secure_root):
                directory, needed, ready = MODULE._prepare_incoming(  # noqa: SLF001
                    request
                )
                self.assertEqual(
                    needed,
                    sorted(
                        row["filename"]
                        for row in request["inputs"].values()
                    ),
                )
                self.assertEqual(ready, [])
                for key, row in request["inputs"].items():
                    secure_file(
                        directory / f".{row['filename']}.transfer",
                        payloads[key],
                    )
                _, needed, ready = MODULE._prepare_incoming(  # noqa: SLF001
                    request
                )
                self.assertEqual(needed, [])
                self.assertEqual(len(ready), 6)
                promoted = MODULE._promote_incoming(  # noqa: SLF001
                    request,
                    directory,
                )
                for key, path in promoted.items():
                    self.assertEqual(path.read_bytes(), payloads[key])
                    self.assertEqual(path.stat().st_nlink, 1)
                    self.assertFalse(
                        (directory / f".{path.name}.transfer").exists()
                    )

                row = request["inputs"]["database"]
                final_path = directory / row["filename"]
                final_path.unlink()
                partial = directory / f".{row['filename']}.transfer"
                secure_file(partial, payloads["database"])
                os.link(partial, final_path)
                self.assertEqual(final_path.stat().st_nlink, 2)
                _, needed, ready = MODULE._prepare_incoming(  # noqa: SLF001
                    request
                )
                self.assertEqual(needed, [])
                self.assertIn(row["filename"], ready)
                self.assertFalse(partial.exists())
                self.assertEqual(final_path.stat().st_nlink, 1)

    def test_partial_symlink_and_final_tamper_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            secure_root = Path(raw) / "secure"
            request, payloads = host_input_request("webapp_fi")
            with mock.patch.object(MODULE, "SECURE_ROOT", secure_root):
                directory, _, _ = MODULE._prepare_incoming(  # noqa: SLF001
                    request
                )
                row = request["inputs"]["database"]
                outside = Path(raw) / "outside"
                secure_file(outside, payloads["database"])
                partial = directory / f".{row['filename']}.transfer"
                partial.symlink_to(outside)
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "residue is unsafe",
                ):
                    MODULE._prepare_incoming(request)  # noqa: SLF001
                partial.unlink()
                final_path = directory / row["filename"]
                secure_file(final_path, b"tampered")
                with self.assertRaisesRegex(
                    MODULE.PrecommitInputOrchestrationError,
                    "differs",
                ):
                    MODULE._prepare_incoming(request)  # noqa: SLF001

    def test_safe_mismatched_partial_is_removed_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            secure_root = Path(raw) / "secure"
            request, _ = host_input_request("bot_fi")
            with mock.patch.object(MODULE, "SECURE_ROOT", secure_root):
                directory, _, _ = MODULE._prepare_incoming(  # noqa: SLF001
                    request
                )
                row = request["inputs"]["uploads"]
                partial = directory / f".{row['filename']}.transfer"
                secure_file(partial, b"truncated")
                _, needed, _ = MODULE._prepare_incoming(  # noqa: SLF001
                    request
                )
                self.assertIn(row["filename"], needed)
                self.assertFalse(partial.exists())


class ReleaseAndInstallerTests(unittest.TestCase):
    def test_release_verification_binds_clean_tree_and_host_agent_hash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            operation_id = "22222222-2222-4222-8222-222222222222"
            release_sha = "a" * 40
            tree_sha = "b" * 40
            shadow_root = root / "shadow" / operation_id
            release_root = shadow_root / "releases" / release_sha
            scripts = release_root / "scripts"
            scripts.mkdir(parents=True, mode=0o700)
            host_agent = scripts / "production_shadow_host_agent.py"
            installer = (
                scripts
                / "install_production_shadow_precommit_inputs.py"
            )
            orchestrator = (
                scripts
                / "orchestrate_production_shadow_precommit_inputs.py"
            )
            secure_file(host_agent, b"host agent\n", mode=0o644)
            secure_file(installer, b"installer\n", mode=0o644)
            secure_file(orchestrator, b"orchestrator\n", mode=0o644)
            request = {
                "release_sha": release_sha,
                "release_tree_sha": tree_sha,
                "controller_manifest": {
                    "deployment": {"shadow_root": str(shadow_root)},
                    "artifacts": {
                        "host_agent_sha256": hashlib.sha256(
                            host_agent.read_bytes()
                        ).hexdigest(),
                        "host_agent_contract_sha256": (
                            CUTOVER.HOST_AGENT_CONTRACT_SHA256
                        ),
                    },
                },
            }

            git_calls: list[
                tuple[list[str], dict[str, str]]
            ] = []

            def runner(
                argv: object,
                _timeout: int,
                _env: dict[str, str],
            ) -> subprocess.CompletedProcess[bytes]:
                arguments = list(argv)
                git_calls.append((arguments, _env))
                if "ls-tree" in arguments:
                    relative = arguments[-1]
                    payload = (release_root / relative).read_bytes()
                    header = f"blob {len(payload)}\0".encode("ascii")
                    blob = hashlib.sha1(header + payload).hexdigest()
                    return completed(
                        f"100644 blob {blob}\t{relative}".encode()
                    )
                if "hash-object" in arguments:
                    payload = Path(arguments[-1]).read_bytes()
                    header = f"blob {len(payload)}\0".encode("ascii")
                    return completed(
                        hashlib.sha1(header + payload).hexdigest().encode()
                    )
                if "--abbrev-ref" in arguments:
                    return completed(b"HEAD")
                if arguments[-1] == "HEAD":
                    return completed(release_sha.encode())
                if arguments[-1] == "HEAD^{tree}":
                    return completed(tree_sha.encode())
                if arguments[-1] == "--show-toplevel":
                    return completed(str(release_root).encode())
                return completed(b"")

            observed = MODULE._verify_release(  # noqa: SLF001
                request,
                runner=runner,
                current_script=orchestrator,
            )
            self.assertEqual(observed, release_root)
            self.assertGreaterEqual(len(git_calls), 6)
            for argv, environment in git_calls:
                self.assertEqual(argv[0], MODULE.GIT)
                self.assertIn("--no-optional-locks", argv)
                self.assertIn("core.fsmonitor=false", argv)
                self.assertIn("core.untrackedCache=false", argv)
                self.assertIn("core.hooksPath=/dev/null", argv)
                self.assertIn("core.fileMode=true", argv)
                self.assertEqual(
                    environment["GIT_CONFIG_GLOBAL"],
                    "/dev/null",
                )
                self.assertEqual(
                    environment["GIT_NO_REPLACE_OBJECTS"],
                    "1",
                )
            git_calls.clear()
            request["controller_manifest"]["artifacts"][
                "host_agent_sha256"
            ] = "f" * 64
            with self.assertRaisesRegex(
                MODULE.PrecommitInputOrchestrationError,
                "host-agent artifact differs",
            ):
                MODULE._verify_release(  # noqa: SLF001
                    request,
                    runner=runner,
                    current_script=orchestrator,
                )

    def test_installer_invocation_is_release_bound_isolated_and_non_runtime(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            operation_id = "22222222-2222-4222-8222-222222222222"
            release_sha = "a" * 40
            role = "bot_fi"
            inputs = {}
            for key in (
                "precommit_manifest",
                "role_material",
                "source_snapshot_manifest",
            ):
                path = root / key
                secure_file(path, key.encode())
                inputs[key] = path
            manifest = SimpleNamespace(
                operation_id=operation_id,
                release_sha=release_sha,
                role=role,
                canonical_sha256="f" * 64,
            )
            request = {
                "operation_id": operation_id,
                "release_sha": release_sha,
                "role": role,
                "controller_manifest": {"artifacts": {}},
                "controller_manifest_sha256": "e" * 64,
            }
            calls: list[list[str]] = []

            def runner(
                argv: object,
                _timeout: int,
                _env: object,
            ) -> subprocess.CompletedProcess[bytes]:
                arguments = list(argv)
                calls.append(arguments)
                return completed(
                    {
                        "schema": WORKER.MANIFEST_SCHEMA,
                        "status": "installed",
                        "operation_id": operation_id,
                        "role": role,
                        "release_sha": release_sha,
                        "manifest_sha256": manifest.canonical_sha256,
                        "network_io": False,
                        "docker_invoked": False,
                        "service_mutated": False,
                        "current_mutated": False,
                        "source_mutated": False,
                    }
                )

            role_members = {
                "role-compose.yml": b"compose",
                "runtime.env.role": b"environment",
                "ca.crt": b"certificate",
            }
            with (
                mock.patch.object(
                    MODULE.INSTALLER,
                    "_load_precommit_manifest_source",
                    return_value=({}, b"", manifest, SimpleNamespace()),
                ),
                mock.patch.object(
                    MODULE.INSTALLER,
                    "_load_role_material",
                    return_value=(
                        b"",
                        SimpleNamespace(),
                        role_members,
                    ),
                ),
                mock.patch.object(
                    MODULE.INSTALLER,
                    "_load_source_snapshot",
                    side_effect=AssertionError(
                        "large source validation must stay in bounded installer"
                    ),
                ) as source_loader,
                mock.patch.object(
                    MODULE,
                    "_validate_role_controller_binding",
                ),
                mock.patch.object(
                    MODULE,
                    "_hash_regular_release_file",
                    return_value="1" * 64,
                ),
            ):
                loaded, members = MODULE._run_installer(  # noqa: SLF001
                    request,
                    root / "release",
                    inputs,
                    runner=runner,
                )
            self.assertIs(loaded, manifest)
            self.assertEqual(members, role_members)
            source_loader.assert_not_called()
            self.assertEqual(
                calls[0][:4],
                [
                    MODULE.PYTHON3,
                    "-I",
                    "-B",
                    str(
                        root
                        / "release"
                        / MODULE.INSTALLER_RELATIVE
                    ),
                ],
            )
            self.assertNotIn(MODULE.SSH, calls[0])
            self.assertNotIn(MODULE.SCP, calls[0])
            self.assertFalse(
                any("docker" in token.lower() for token in calls[0])
            )


class CliAndRedactionTests(unittest.TestCase):
    def test_host_request_rejects_path_and_role_expansion(self) -> None:
        controller = manifest_payload()
        inputs = {}
        request, payloads = host_input_request("bot_fi")
        for key, row in request["inputs"].items():
            inputs[key] = dict(row)
        document = {
            "schema": MODULE.HOST_REQUEST_SCHEMA,
            "action": "prepare",
            "role": "bot_fi",
            "operation_id": controller["operation_id"],
            "release_sha": controller["release_sha"],
            "release_tree_sha": controller["release_tree_sha"],
            "controller_manifest": controller,
            "controller_manifest_sha256": hashlib.sha256(
                MODULE._canonical_json(controller)  # noqa: SLF001
            ).hexdigest(),
            "inputs": inputs,
        }
        self.assertEqual(
            MODULE._validate_host_request(document)["role"],  # noqa: SLF001
            "bot_fi",
        )
        document["inputs"]["database"]["filename"] = "../database.dump"
        with self.assertRaisesRegex(
            MODULE.PrecommitInputOrchestrationError,
            "input is invalid",
        ):
            MODULE._validate_host_request(document)  # noqa: SLF001
        self.assertEqual(len(payloads), 6)

    def test_main_redacts_unexpected_secret_and_direct_help_smoke(self) -> None:
        args = SimpleNamespace(
            host_request_b64="safe",
        )
        output = io.StringIO()
        with (
            mock.patch.object(MODULE, "parse_args", return_value=args),
            mock.patch.object(
                MODULE,
                "host_execute",
                side_effect=RuntimeError("DO-NOT-LEAK-secret-value"),
            ),
            redirect_stdout(output),
        ):
            status = MODULE.main([])
        self.assertEqual(status, 2)
        self.assertNotIn("DO-NOT-LEAK", output.getvalue())
        redacted = json.loads(output.getvalue())
        self.assertEqual(redacted["status"], "blocked")
        self.assertFalse(redacted["docker_invoked"])

        script = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "orchestrate_production_shadow_precommit_inputs.py"
        )
        smoke = subprocess.run(
            [sys.executable, "-I", "-B", str(script), "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/root",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
            },
        )
        self.assertEqual(smoke.returncode, 0, smoke.stderr.decode())
        self.assertIn(b"--controller-manifest", smoke.stdout)


if __name__ == "__main__":
    unittest.main()
