import json
import hashlib
import pathlib
import tempfile
import unittest
import unittest.mock
import os
import signal
import subprocess
import sys
import time
from contextlib import redirect_stdout
from io import StringIO

from scripts.report_production_recoverability import evaluate_backup, parse_json_output as parse_report_json_output
from scripts import run_production_backup
from scripts.run_production_backup import HostTarget, build_backup_shell, parse_json_from_stdout


class ProductionRecoverabilityToolsTests(unittest.TestCase):
    def test_backup_shell_includes_core_artifacts_and_restore_smoke(self):
        shell = build_backup_shell(
            HostTarget(role="iran", project_dir="/srv/trading-bot/current", compose_file="docker-compose.iran.yml", remote=True),
            stamp="20260611T150000Z",
            backup_dir="/srv/trading-bot/backups",
            include_uploads=True,
            include_audit=True,
            include_redis=True,
            restore_smoke=True,
        )

        self.assertIn("pg_dump", shell)
        self.assertIn("trading_bot_redis", shell)
        self.assertIn("/app/uploads", shell)
        self.assertIn("/app/audit_trail", shell)
        self.assertIn("trading_bot_restore_drill_iran_20260611T150000Z", shell)
        self.assertIn("postgres:15-alpine", shell)
        self.assertIn("OWNER TO", shell)
        self.assertIn("CREATE ROLE", shell)
        self.assertIn("umask 077", shell)
        self.assertIn(".production-backup.lock", shell)
        self.assertIn("set -o noclobber", shell)
        self.assertIn("trading-bot.production-backup-run", shell)
        self.assertIn("database_identity_sha256", shell)
        self.assertIn("os.O_EXCL", shell)
        self.assertIn('[ "$backup_dir_canonical" = "$backup_dir" ]', shell)
        self.assertIn('docker_cleanup_bounded rm -fv "$restore_name"', shell)
        self.assertIn("timeout --signal=TERM --kill-after=5s 30s docker", shell)
        self.assertIn('docker volume create --label "trading-bot.production-backup-run=$run_id"', shell)
        self.assertIn('docker_cleanup_bounded volume rm "$owned_volume"', shell)
        self.assertIn("restore_cleanup_container_absent", shell)
        self.assertIn("restore_cleanup_named_volume_absent", shell)
        self.assertIn("restore_cleanup_owned_volumes_absent", shell)
        self.assertIn("restore_cleanup_proof_sha256", shell)
        self.assertIn('if ! cleanup_restore_resources; then', shell)
        self.assertIn('and cleanup_status == "passed"', shell)
        self.assertIn('and bool(re.fullmatch(r"[0-9a-f]{64}", cleanup_proof_sha256))', shell)

    def test_restore_smoke_cannot_pass_before_cleanup_proof(self):
        shell = build_backup_shell(
            HostTarget(
                role="foreign",
                project_dir=str(run_production_backup.REPO_ROOT),
                compose_file="docker-compose.yml",
                remote=False,
            ),
            stamp="20260611T150001Z",
            backup_dir="/srv/trading-bot/backups",
            restore_smoke=True,
        )

        cleanup_call = shell.index("if cleanup_restore_resources; then")
        passed_assignment = shell.index("restore_status=passed", cleanup_call)
        manifest_generation = shell.index('manifest_file="$run_dir/', passed_assignment)
        self.assertLess(cleanup_call, passed_assignment)
        self.assertLess(passed_assignment, manifest_generation)
        self.assertIn('"commands_bounded": True', shell)
        self.assertIn('"container_absent": container_absent == "true"', shell)
        self.assertIn('"named_volume_absent": named_volume_absent == "true"', shell)
        self.assertIn('"owned_volumes_absent": owned_volumes_absent == "true"', shell)
        self.assertIn('intended restore volume residue remains', shell)

    def test_restore_container_and_run_directory_names_are_randomized(self):
        target = HostTarget(
            role="iran",
            project_dir="/srv/trading-bot/current",
            compose_file="docker-compose.iran.yml",
            remote=True,
        )
        first = build_backup_shell(
            target,
            stamp="20260611T150000Z",
            backup_dir="/srv/trading-bot/backups",
        )
        second = build_backup_shell(
            target,
            stamp="20260611T150000Z",
            backup_dir="/srv/trading-bot/backups",
        )
        self.assertNotEqual(first, second)

    def test_backup_rejects_any_unapproved_artifact_root(self):
        with self.assertRaisesRegex(ValueError, "approved root"):
            build_backup_shell(
                HostTarget(
                    role="iran",
                    project_dir="/srv/trading-bot/current",
                    compose_file="docker-compose.iran.yml",
                    remote=True,
                ),
                stamp="20260611T150000Z",
                backup_dir="/etc",
            )

    def test_pull_rejects_symlinked_approved_root_without_chmodding_target(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            target = root / "target"
            target.mkdir(mode=0o700)
            approved = root / "production-pull"
            approved.symlink_to(target, target_is_directory=True)
            before_mode = target.stat().st_mode & 0o777
            with unittest.mock.patch.object(
                run_production_backup, "DEFAULT_IRAN_PULL_DIR", approved
            ):
                with self.assertRaisesRegex(RuntimeError, "secure production path"):
                    run_production_backup.pull_iran_files({}, {"files": []}, approved)
            self.assertEqual(target.stat().st_mode & 0o777, before_mode)

    def test_parse_json_from_stdout_uses_last_json_object(self):
        payload = parse_json_from_stdout("noise\n{\"status\":\"old\"}\nmore\n{\"status\":\"ok\",\"x\":1}\n")
        self.assertEqual(payload, {"status": "ok", "x": 1})

    def test_pull_iran_files_uses_accept_new_host_key_policy(self):
        seen_args = []

        def fake_run(args, timeout):
            seen_args.append(args)
            pathlib.Path(args[-1]).write_bytes(b"iran-db-backup")

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        payload = {
            "files": [
                {
                    "path": "/srv/trading-bot/backups/iran-db-test.sql.gz",
                    "bytes": len(b"iran-db-backup"),
                    "sha256": hashlib.sha256(b"iran-db-backup").hexdigest(),
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            secure_root = pathlib.Path(tmp_dir)
            approved_pull = secure_root / "production-pull"
            with unittest.mock.patch.object(
                run_production_backup, "run_command", side_effect=fake_run
            ), unittest.mock.patch.object(
                run_production_backup,
                "DEFAULT_IRAN_PULL_DIR",
                approved_pull,
            ):
                identity = secure_root / "production-key"
                identity.write_text("test-key-material\n", encoding="utf-8")
                identity.chmod(0o600)
                pulled = run_production_backup.pull_iran_files(
                    {
                        "IRAN_HOST": "65.109.220.59",
                        "IRAN_SSH_PORT": "37067",
                        "IRAN_SSH_USER": "root",
                        "IRAN_SSH_AUTH_METHOD": "key",
                        "IRAN_SSH_PRIVATE_KEY_PATH": str(identity),
                    },
                    payload,
                    approved_pull,
                )

        self.assertEqual(pulled[0]["remote_path"], "/srv/trading-bot/backups/iran-db-test.sql.gz")
        self.assertIn("StrictHostKeyChecking=accept-new", seen_args[0])
        self.assertNotIn("StrictHostKeyChecking=no", seen_args[0])
        self.assertIn("BatchMode=yes", seen_args[0])
        self.assertIn("PasswordAuthentication=no", seen_args[0])
        self.assertIn("IdentitiesOnly=yes", seen_args[0])
        self.assertIn(str(identity), seen_args[0])

    def test_backup_main_uses_manifest_authoritative_target_not_shell_pollution(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            manifest = root / "online.env"
            identity = root / "production-key"
            identity.write_text("test-key\n", encoding="utf-8")
            identity.chmod(0o600)
            values = {
                "LOCAL_PROJECT_DIR": str(run_production_backup.REPO_ROOT),
                "FOREIGN_PUBLIC_DOMAIN": run_production_backup.PRODUCTION_FOREIGN_DOMAIN,
                "IRAN_HOST": "manifest-production-host.invalid",
                "IRAN_SSH_USER": "manifest-user",
                "IRAN_SSH_PORT": "22022",
                "IRAN_SSH_AUTH_METHOD": "key",
                "IRAN_SSH_PRIVATE_KEY_PATH": str(identity),
                "IRAN_PROJECT_DIR": run_production_backup.PRODUCTION_IRAN_PROJECT_DIR,
                "IRAN_APP_DOMAIN": run_production_backup.PRODUCTION_IRAN_DOMAIN,
                "IRAN_PUBLIC_DOMAIN": run_production_backup.PRODUCTION_IRAN_DOMAIN,
            }
            manifest.write_text(
                "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
                encoding="utf-8",
            )
            manifest.chmod(0o600)
            observed = {}

            def fake_backup(target, settings, manifest_values, **_kwargs):
                observed.update(settings)
                self.assertEqual(manifest_values["IRAN_HOST"], values["IRAN_HOST"])
                return {
                    "status": "ok",
                    "role": target.role,
                    "manifest_path": "/secure/backup.json",
                }

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "IRAN_HOST": "polluted-shell.invalid",
                    "IRAN_SSH_USER": "polluted-user",
                    "IRAN_SSH_PORT": "1",
                    "IRAN_PROJECT_DIR": "/srv/staging",
                },
                clear=False,
            ), unittest.mock.patch.object(
                run_production_backup, "backup_role", side_effect=fake_backup
            ), redirect_stdout(StringIO()):
                result = run_production_backup.main(
                    ["--manifest", str(manifest), "--role", "foreign", "--json"]
                )
            self.assertEqual(result, 0)
            self.assertEqual(observed["IRAN_HOST"], values["IRAN_HOST"])
            self.assertEqual(observed["IRAN_SSH_USER"], values["IRAN_SSH_USER"])
            self.assertEqual(observed["IRAN_SSH_PORT"], values["IRAN_SSH_PORT"])
            self.assertEqual(observed["IRAN_PROJECT_DIR"], values["IRAN_PROJECT_DIR"])

    def test_backup_command_timeout_terminates_descendant_process_group(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pid_path = pathlib.Path(tmp_dir) / "child.pid"
            code = (
                "import pathlib,subprocess,time,sys; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); "
                "time.sleep(60)"
            )
            result = run_production_backup.run_command(
                ["python3", "-c", code, str(pid_path)], timeout=0.2
            )
            self.assertEqual(result.returncode, 124)
            child_pid = int(pid_path.read_text(encoding="utf-8"))
            for _ in range(20):
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("backup timeout left a descendant process running")

    def test_backup_cleanup_kills_pipe_holder_after_group_leader_exits(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            child_pid_path = root / "pipe-holder.pid"
            child_ready_path = root / "pipe-holder.ready"
            child_code = (
                "import os,pathlib,signal,sys,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "pathlib.Path(sys.argv[2]).write_text('ready'); "
                "time.sleep(60)"
            )
            leader_code = (
                "import pathlib,subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]]); "
                "ready=pathlib.Path(sys.argv[3]); "
                "deadline=time.monotonic()+5; "
                "\nwhile not ready.exists() and time.monotonic()<deadline: time.sleep(0.01)"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    leader_code,
                    child_code,
                    str(child_pid_path),
                    str(child_ready_path),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            group_id = process.pid
            try:
                ready_deadline = time.monotonic() + 5.0
                while time.monotonic() < ready_deadline:
                    if child_ready_path.exists() and process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertTrue(child_ready_path.exists(), "pipe holder did not become ready")
                self.assertIsNotNone(process.poll(), "group leader must have exited")
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.communicate(timeout=0.2)
                run_production_backup._stop_process_group(
                    process,
                    process_group_id=group_id,
                    grace_seconds=0.1,
                    kill_seconds=1.0,
                )
                child_pid = int(child_pid_path.read_text(encoding="utf-8"))
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    stat_path = pathlib.Path(f"/proc/{child_pid}/stat")
                    if stat_path.exists() and stat_path.read_text(encoding="utf-8").split()[2] == "Z":
                        break
                    time.sleep(0.05)
                else:
                    self.fail("leader-exited pipe holder survived bounded group cleanup")
            finally:
                if run_production_backup._process_group_has_live_members(group_id):
                    run_production_backup._stop_process_group(
                        process,
                        process_group_id=group_id,
                        grace_seconds=0.1,
                        kill_seconds=1.0,
                    )

    def test_backup_timeout_kills_term_ignoring_descendant_with_closed_pipes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            child_pid_path = pathlib.Path(tmp_dir) / "closed-pipe-child.pid"
            escaped_marker = pathlib.Path(tmp_dir) / "closed-pipe-child.escaped"
            child_code = "\n".join(
                (
                    "import os, pathlib, signal, sys, time",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                    "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')",
                    "time.sleep(0.75)",
                    "pathlib.Path(sys.argv[2]).write_text('escaped')",
                    "time.sleep(60)",
                )
            )
            leader_code = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2],sys.argv[3]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                "time.sleep(60)"
            )
            original_cleanup = run_production_backup._stop_process_group

            def fast_cleanup(process, *, process_group_id=None):
                return original_cleanup(
                    process,
                    process_group_id=process_group_id,
                    grace_seconds=0.1,
                    kill_seconds=1.0,
                )

            child_pid = None
            try:
                original_communicate = subprocess.Popen.communicate
                first_communicate = True

                def communicate_after_child_ready(process, *args, **kwargs):
                    nonlocal first_communicate
                    if first_communicate:
                        first_communicate = False
                        ready_deadline = time.monotonic() + 5.0
                        while (
                            not child_pid_path.exists()
                            and time.monotonic() < ready_deadline
                        ):
                            time.sleep(0.01)
                        self.assertTrue(
                            child_pid_path.exists(),
                            "term-ignoring descendant did not become ready",
                        )
                        raise subprocess.TimeoutExpired(
                            process.args, kwargs.get("timeout", args[0] if args else 5.0)
                        )
                    return original_communicate(process, *args, **kwargs)

                with unittest.mock.patch.object(
                    run_production_backup,
                    "_stop_process_group",
                    side_effect=fast_cleanup,
                ), unittest.mock.patch.object(
                    subprocess.Popen,
                    "communicate",
                    new=communicate_after_child_ready,
                ):
                    result = run_production_backup.run_command(
                        [
                            sys.executable,
                            "-c",
                            leader_code,
                            child_code,
                            str(child_pid_path),
                            str(escaped_marker),
                        ],
                        timeout=5.0,
                    )
                self.assertEqual(result.returncode, 124)
                child_pid, child_group = map(
                    int, child_pid_path.read_text(encoding="utf-8").split(":")
                )
                self.assertFalse(
                    run_production_backup._process_group_has_live_members(child_group)
                )
                time.sleep(0.85)
                self.assertFalse(escaped_marker.exists())
            finally:
                if child_pid is None and child_pid_path.exists():
                    child_pid = int(
                        child_pid_path.read_text(encoding="utf-8").split(":", 1)[0]
                    )
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_backup_normal_return_fails_closed_on_detached_descendant(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = pathlib.Path(tmp_dir)
            child_identity = root / "normal-return-child.pid"
            escaped_marker = root / "normal-return-child.escaped"
            child_code = "\n".join(
                (
                    "import os, pathlib, signal, sys, time",
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
                    "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()}:{os.getpgrp()}')",
                    "time.sleep(0.75)",
                    "pathlib.Path(sys.argv[2]).write_text('escaped')",
                )
            )
            leader_code = "\n".join(
                (
                    "import pathlib, subprocess, sys, time",
                    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
                    "ready = pathlib.Path(sys.argv[2])",
                    "deadline = time.monotonic() + 5",
                    "while not ready.exists() and time.monotonic() < deadline: time.sleep(0.01)",
                    "raise SystemExit(0 if ready.exists() else 91)",
                )
            )
            child_pid = None
            try:
                result = run_production_backup.run_command(
                    [
                        sys.executable,
                        "-c",
                        leader_code,
                        child_code,
                        str(child_identity),
                        str(escaped_marker),
                    ],
                    timeout=3,
                )
                self.assertEqual(result.returncode, 125)
                child_pid, child_group = map(
                    int, child_identity.read_text(encoding="utf-8").split(":")
                )
                self.assertFalse(
                    run_production_backup._process_group_has_live_members(child_group)
                )
                time.sleep(0.85)
                self.assertFalse(escaped_marker.exists())
            finally:
                if child_pid is None and child_identity.exists():
                    child_pid = int(
                        child_identity.read_text(encoding="utf-8").split(":", 1)[0]
                    )
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_backup_cleanup_never_uses_unbounded_communicate(self):
        process = unittest.mock.Mock()
        process.pid = 4242
        process.stdout = unittest.mock.Mock()
        process.stderr = unittest.mock.Mock()
        process.communicate.side_effect = (
            subprocess.TimeoutExpired(["backup"], 0.01),
            subprocess.TimeoutExpired(["backup"], 0.01),
        )
        with unittest.mock.patch.object(
            run_production_backup.os, "killpg"
        ) as killpg, self.assertRaisesRegex(RuntimeError, "bounded cleanup"):
            run_production_backup._stop_process_group(
                process,
                process_group_id=4242,
                grace_seconds=0.01,
                kill_seconds=0.01,
            )
        signal_calls = [
            call
            for call in killpg.call_args_list
            if call.args[1] in {signal.SIGTERM, signal.SIGKILL}
        ]
        self.assertEqual(
            signal_calls,
            [
                unittest.mock.call(4242, signal.SIGTERM),
                unittest.mock.call(4242, signal.SIGKILL),
            ],
        )
        self.assertTrue(any(call.args[1] == 0 for call in killpg.call_args_list))
        self.assertEqual(process.communicate.call_count, 2)
        self.assertTrue(
            all("timeout" in call.kwargs for call in process.communicate.call_args_list)
        )
        process.stdout.close.assert_called_once_with()
        process.stderr.close.assert_called_once_with()

    def test_backup_subprocess_drops_compose_and_target_shell_pollution(self):
        process = unittest.mock.Mock()
        process.pid = 99_999_991
        process.communicate.return_value = ("", "")
        process.returncode = 0
        with unittest.mock.patch.dict(
            os.environ,
            {
                "COMPOSE_PROJECT_NAME": "polluted-staging",
                "IRAN_HOST": "polluted.invalid",
                "IRAN_SSH_PORT": "1",
            },
            clear=False,
        ), unittest.mock.patch.object(
            run_production_backup.subprocess, "Popen", return_value=process
        ) as popen:
            result = run_production_backup.run_command(["true"], timeout=1)
        self.assertEqual(result.returncode, 0)
        child_env = popen.call_args.kwargs["env"]
        self.assertNotIn("COMPOSE_PROJECT_NAME", child_env)
        self.assertNotIn("IRAN_HOST", child_env)
        self.assertNotIn("IRAN_SSH_PORT", child_env)

    def test_report_json_parser_accepts_pretty_json(self):
        payload = parse_report_json_output(json.dumps({"status": "ok", "items": [1, 2]}, indent=2))
        self.assertEqual(payload["status"], "ok")

    def test_evaluate_backup_requires_all_artifacts_and_restore_when_requested(self):
        payload = {
            "status": "ok",
            "results": [
                {
                    "role": "iran",
                    "files": [
                        {"kind": "db", "bytes": 100, "sha256": "a"},
                        {"kind": "redis", "bytes": 100, "sha256": "b"},
                        {"kind": "uploads", "bytes": 100, "sha256": "c"},
                        {"kind": "audit", "bytes": 100, "sha256": "d"},
                    ],
                    "restore_smoke": {"status": "passed", "table_count": 20},
                }
            ],
        }

        failures, warnings = evaluate_backup(payload, require_restore_smoke=True)

        self.assertEqual(failures, [])
        self.assertEqual(warnings, [])

    def test_evaluate_backup_warns_when_restore_smoke_is_skipped(self):
        payload = {
            "status": "ok",
            "results": [
                {
                    "role": "iran",
                    "files": [
                        {"kind": "db", "bytes": 100, "sha256": "a"},
                        {"kind": "redis", "bytes": 100, "sha256": "b"},
                        {"kind": "uploads", "bytes": 100, "sha256": "c"},
                        {"kind": "audit", "bytes": 100, "sha256": "d"},
                    ],
                    "restore_smoke": {"status": "skipped"},
                }
            ],
        }

        failures, warnings = evaluate_backup(payload, require_restore_smoke=False)

        self.assertEqual(failures, [])
        self.assertEqual(warnings, ["iran DB restore smoke was skipped"])


if __name__ == "__main__":
    unittest.main()
