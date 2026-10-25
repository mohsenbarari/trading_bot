"""Two-OS-process proof that split runtime has exactly one queue owner."""
from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, call, patch

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core.telegram_central_poller_owner import (
    TelegramCentralPollerLeaseLostError,
    acquire_telegram_central_poller_owner,
    telegram_central_poller_owner_monitor_loop,
)
from core.telegram_delivery_queue_owner import (
    acquire_telegram_delivery_queue_owner,
)
from tests.test_telegram_delivery_queue_postgres import _run_alembic


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DB_NAME = "telegram_queue_stage3_split_runtime_test"
POSTGRES_CONTAINER = "telegram-split-runtime-pg-scratch"
REDIS_CONTAINER = "telegram-split-runtime-redis-scratch"
POSTGRES_IMAGE = os.environ.get("TELEGRAM_SPLIT_SCRATCH_POSTGRES_IMAGE", "postgres:16-alpine")
REDIS_IMAGE = os.environ.get("TELEGRAM_SPLIT_SCRATCH_REDIS_IMAGE", "redis:7-alpine")


def _probe_env(url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment["TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL"] = url
    environment["APP_ENV_FILE"] = str(REPO_ROOT / "config/unit-test.env.example")
    environment["PYTHONUNBUFFERED"] = "1"
    environment.pop("TELEGRAM_PROVIDER_TEST_AUTHORITY", None)
    return environment


def _run_script(module: str, *args: str, url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=str(REPO_ROOT),
        env=_probe_env(url),
    )


def _harness(*args: str, url: str) -> subprocess.CompletedProcess:
    return _run_script("scripts.run_telegram_split_runtime_harness", *args, url=url)


def _probe(*args: str, url: str) -> subprocess.CompletedProcess:
    return _run_script("scripts.probe_telegram_split_runtime", *args, url=url)


@dataclass
class _HeldChild:
    process: subprocess.Popen
    ready_file: Path
    release_file: Path
    stdout_path: Path
    stderr_path: Path

    def wait_ready(self, timeout: float = 40) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise AssertionError(
                    f"child exited {self.process.returncode} before ready: "
                    f"{self.stderr_path.read_text()} {self.stdout_path.read_text()}"
                )
            if self.ready_file.exists() and self.ready_file.stat().st_size > 0:
                return self.stdout_path.read_text()
            time.sleep(0.05)
        raise AssertionError(
            f"child produced no ready file: {self.stderr_path.read_text()} "
            f"{self.stdout_path.read_text()}"
        )

    def stdout_lines(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.stdout_path.read_text().splitlines()
            if line.strip()
        ]

    def release(self) -> None:
        self.release_file.write_text("release\n", encoding="utf-8")
        self.process.wait(timeout=20)


def _start_script(
    module: str,
    *args: str,
    url: str,
    workdir: Path,
) -> _HeldChild:
    ready_file = workdir / "ready"
    release_file = workdir / "release"
    stdout_path = workdir / "stdout.jsonl"
    stderr_path = workdir / "stderr.log"
    command = [
        sys.executable,
        "-m",
        module,
        *args,
        "--ready-file",
        str(ready_file),
        "--release-file",
        str(release_file),
    ]
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            cwd=str(REPO_ROOT),
            env=_probe_env(url),
        )
    return _HeldChild(
        process=process,
        ready_file=ready_file,
        release_file=release_file,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _scratch_postgres_ready(url: str, deadline: float) -> bool:
    """Authenticate and query the migration's TCP target within its one budget."""
    import psycopg2
    from psycopg2 import extensions

    connection = None
    try:
        connection = psycopg2.connect(url, async_=True)

        def wait() -> None:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                state = connection.poll()
                if state == extensions.POLL_OK:
                    return
                if state == extensions.POLL_READ:
                    ready = select.select([connection.fileno()], [], [], remaining)[0]
                elif state == extensions.POLL_WRITE:
                    ready = select.select([], [connection.fileno()], [], remaining)[1]
                else:
                    raise RuntimeError("scratch postgres polling state")
                if not ready:
                    raise TimeoutError

        wait()
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT 1")
            wait()
            return cursor.fetchone() == (1,) and time.monotonic() < deadline
        finally:
            cursor.close()
    except (psycopg2.Error, OSError, TimeoutError):
        return False
    finally:
        if connection is not None:
            connection.close()


def _start_scratch() -> tuple[str, str]:
    postgres_port = os.environ.get("TELEGRAM_SPLIT_SCRATCH_PG_PORT", "55433")
    redis_port = os.environ.get("TELEGRAM_SPLIT_SCRATCH_REDIS_PORT", "56379")
    subprocess.run(
        ["docker", "rm", "-fv", POSTGRES_CONTAINER],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["docker", "rm", "-fv", REDIS_CONTAINER],
        check=False,
        capture_output=True,
    )
    pg = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            POSTGRES_CONTAINER,
            "-e",
            "POSTGRES_USER=split_runtime",
            "-e",
            "POSTGRES_PASSWORD=split_runtime",
            "-e",
            f"POSTGRES_DB={SCRATCH_DB_NAME}",
            "-p",
            f"127.0.0.1:{postgres_port}:5432",
            POSTGRES_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if pg.returncode != 0:
        raise unittest.SkipTest(f"could not start scratch postgres: {pg.stderr.strip()}")
    redis = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            REDIS_CONTAINER,
            "-p",
            f"127.0.0.1:{redis_port}:6379",
            REDIS_IMAGE,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if redis.returncode != 0:
        subprocess.run(
            ["docker", "rm", "-fv", POSTGRES_CONTAINER],
            check=False,
            capture_output=True,
        )
        raise unittest.SkipTest(f"could not start scratch redis: {redis.stderr.strip()}")
    url = (
        f"postgresql://split_runtime:split_runtime@127.0.0.1:{postgres_port}/{SCRATCH_DB_NAME}"
    )
    redis_url = f"redis://127.0.0.1:{redis_port}/15"
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if _scratch_postgres_ready(url, deadline):
            return url, redis_url
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1, remaining))
    _stop_scratch()
    raise RuntimeError("scratch postgres TCP query did not become ready")


def _stop_scratch() -> None:
    subprocess.run(
        ["docker", "rm", "-fv", POSTGRES_CONTAINER],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["docker", "rm", "-fv", REDIS_CONTAINER],
        check=False,
        capture_output=True,
    )


class TelegramSplitRuntimeScratchCleanupTests(unittest.TestCase):
    @patch("tests.test_telegram_split_runtime_dual_process.subprocess.run")
    def test_stop_scratch_removes_anonymous_image_volumes(self, run_mock):
        _stop_scratch()

        self.assertEqual(
            run_mock.call_args_list,
            [
                call(
                    ["docker", "rm", "-fv", POSTGRES_CONTAINER],
                    check=False,
                    capture_output=True,
                ),
                call(
                    ["docker", "rm", "-fv", REDIS_CONTAINER],
                    check=False,
                    capture_output=True,
                ),
            ],
        )


class TelegramSplitRuntimeScratchReadinessTests(unittest.TestCase):
    def test_start_waits_for_exact_tcp_target_even_when_socket_tools_succeed(self):
        module = sys.modules[__name__]
        environment = {
            "TELEGRAM_SPLIT_SCRATCH_PG_PORT": "55434",
            "TELEGRAM_SPLIT_SCRATCH_REDIS_PORT": "56380",
        }
        with patch.dict(os.environ, environment), patch.object(
            module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ) as run, patch.object(
            module, "_scratch_postgres_ready", side_effect=[False, True]
        ) as ready, patch.object(module.time, "monotonic", return_value=100), patch.object(
            module.time, "sleep"
        ) as sleep:
            url, redis_url = _start_scratch()
        expected = f"postgresql://split_runtime:split_runtime@127.0.0.1:55434/{SCRATCH_DB_NAME}"
        self.assertEqual(url, expected)
        self.assertEqual(redis_url, "redis://127.0.0.1:56380/15")
        self.assertEqual(ready.call_args_list, [call(expected, 145), call(expected, 145)])
        sleep.assert_called_once_with(1)
        self.assertFalse(any("pg_isready" in args.args[0] for args in run.call_args_list))

    def test_tcp_timeout_cleans_scratch_and_fails_without_advancing_or_skipping(self):
        module = sys.modules[__name__]
        with patch.object(
            module.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)
        ), patch.object(module, "_scratch_postgres_ready", return_value=False) as ready, patch.object(
            module.time, "monotonic", side_effect=[0, 0, 44.5, 45]
        ), patch.object(module.time, "sleep") as sleep, patch.object(module, "_stop_scratch") as stop:
            with self.assertRaisesRegex(RuntimeError, "^scratch postgres TCP query did not become ready$"):
                _start_scratch()
        ready.assert_called_once()
        sleep.assert_called_once_with(0.5)
        stop.assert_called_once_with()

    def test_driver_authenticates_and_queries_exact_target_before_readiness(self):
        import psycopg2
        from psycopg2 import extensions

        module = sys.modules[__name__]
        connection = Mock()
        connection.poll.side_effect = [extensions.POLL_WRITE, extensions.POLL_OK, extensions.POLL_READ, extensions.POLL_OK]
        connection.fileno.return_value = 7
        connection.cursor.return_value.fetchone.return_value = (1,)
        url = f"postgresql://split_runtime:split_runtime@127.0.0.1:55433/{SCRATCH_DB_NAME}"
        with patch.object(psycopg2, "connect", return_value=connection) as connect, patch.object(
            module.time, "monotonic", return_value=0
        ), patch.object(module.select, "select", side_effect=[([], [7], []), ([7], [], [])]) as poll:
            self.assertTrue(_scratch_postgres_ready(url, 45))
        connect.assert_called_once_with(url, async_=True)
        self.assertEqual(poll.call_args_list, [call([], [7], [], 45), call([7], [], [], 45)])
        connection.cursor.return_value.execute.assert_called_once_with("SELECT 1")
        connection.cursor.return_value.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_connected_socket_cannot_advance_until_query_completes(self):
        import psycopg2
        from psycopg2 import extensions

        module = sys.modules[__name__]
        connection = Mock()
        connection.poll.side_effect = [extensions.POLL_OK, extensions.POLL_READ]
        connection.fileno.return_value = 7
        with patch.object(psycopg2, "connect", return_value=connection), patch.object(
            module.time, "monotonic", return_value=0
        ), patch.object(module.select, "select", return_value=([], [], [])):
            self.assertFalse(_scratch_postgres_ready("postgresql://public-fixture", 45))
        connection.cursor.return_value.execute.assert_called_once_with("SELECT 1")
        connection.cursor.return_value.fetchone.assert_not_called()
        connection.cursor.return_value.close.assert_called_once_with()
        connection.close.assert_called_once_with()


@unittest.skipUnless(_docker_available(), "docker is required for isolated scratch dual-process tests")
class TelegramSplitRuntimeDualProcessTests(unittest.TestCase):
    def setUp(self):
        reset = _harness("--role", "executor", "--action", "reset", url=self.database_url)
        self.assertEqual(reset.returncode, 0, reset.stderr)

    @classmethod
    def setUpClass(cls):
        explicit = str(os.getenv("TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL") or "").strip()
        cls._owns_scratch = False
        if explicit:
            target = make_url(explicit)
            if not str(target.database or "").startswith("telegram_queue_stage3_"):
                raise RuntimeError("dual-process tests require an isolated telegram_queue_stage3_* database")
            cls.database_url = explicit
            cls.redis_url = os.getenv("TELEGRAM_SPLIT_SCRATCH_REDIS_URL", "redis://127.0.0.1:6379/15")
        else:
            cls.database_url, cls.redis_url = _start_scratch()
            cls._owns_scratch = True
        sync_url = (
            make_url(cls.database_url)
            .set(drivername="postgresql+psycopg2")
            .render_as_string(hide_password=False)
        )
        _run_alembic(sync_url, "upgrade", "head")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "_owns_scratch", False):
            _stop_scratch()

    def test_primary_and_executor_have_exactly_one_queue_owner(self):
        with tempfile.TemporaryDirectory(prefix="split-owner-") as raw_dir:
            workdir = Path(raw_dir)
            executor = _start_script(
                "scripts.probe_telegram_split_runtime",
                "--role",
                "executor",
                "--split-enabled",
                "--acquire-queue",
                "--hold",
                url=self.database_url,
                workdir=workdir,
            )
            try:
                first_line = executor.wait_ready()
                first = json.loads(first_line.splitlines()[0])
                self.assertTrue(first["acquired"])
                self.assertTrue(first["owns_queue_executor"])
                self.assertEqual(
                    first["queue_execution_identities"],
                    ["primary", "channel_editor", *[f"publisher_{index}" for index in range(1, 6)]],
                )
                self.assertEqual(
                    first["polling_identities"],
                    [f"publisher_{index}" for index in range(1, 6)],
                )

                primary = _probe(
                    "--role",
                    "primary",
                    "--split-enabled",
                    url=self.database_url,
                )
                self.assertEqual(primary.returncode, 0, primary.stderr)
                primary_payload = json.loads(primary.stdout)
                self.assertFalse(primary_payload["owns_queue_executor"])
                self.assertFalse(primary_payload["owns_otp_worker"])
                self.assertTrue(primary_payload["owns_local_ack"])
                self.assertEqual(primary_payload["polling_identities"], ["primary"])
                self.assertEqual(primary_payload["queue_execution_identities"], [])

                second = _probe(
                    "--role",
                    "executor",
                    "--split-enabled",
                    "--acquire-queue",
                    url=self.database_url,
                )
                self.assertEqual(second.returncode, 3, second.stderr)
                second_payload = json.loads(second.stdout)
                self.assertFalse(second_payload["acquired"])
                self.assertEqual(
                    second_payload["error"],
                    "telegram_delivery_queue_process_owner_already_active",
                )

                primary_must_not = _probe(
                    "--role",
                    "primary",
                    "--split-enabled",
                    "--acquire-queue",
                    url=self.database_url,
                )
                self.assertEqual(primary_must_not.returncode, 3, primary_must_not.stderr)
                self.assertEqual(
                    json.loads(primary_must_not.stdout)["error"],
                    "primary_must_not_acquire_queue_owner",
                )
            finally:
                if executor.process.poll() is None:
                    executor.release()
                elif executor.process.returncode not in (0, None):
                    self.fail(executor.stderr_path.read_text())

        rollback = _probe(
            "--role",
            "all",
            "--acquire-queue",
            url=self.database_url,
        )
        self.assertEqual(rollback.returncode, 0, rollback.stderr)
        rollback_payload = json.loads(rollback.stdout)
        self.assertTrue(rollback_payload["acquired"])
        self.assertTrue(rollback_payload["owns_queue_executor"])
        self.assertEqual(
            rollback_payload["queue_execution_identities"],
            ["primary", "channel_editor", *[f"publisher_{index}" for index in range(1, 6)]],
        )

    def test_handoff_wakeup_claim_and_fake_send_are_single_shot(self):
        enqueue = _harness(
            "--role",
            "primary",
            "--action",
            "enqueue",
            "--source-key",
            f"split-handoff-{uuid.uuid4().hex[:8]}",
            url=self.database_url,
        )
        self.assertEqual(enqueue.returncode, 0, enqueue.stderr)
        ack = _harness(
            "--role",
            "primary",
            "--action",
            "primary-ack",
            url=self.database_url,
        )
        self.assertEqual(ack.returncode, 0, ack.stderr + ack.stdout)
        ack_payload = json.loads(ack.stdout)
        self.assertTrue(ack_payload["acked"])
        self.assertEqual(ack_payload["provider_calls"], 0)

        consume_proc = _harness(
            "--role",
            "executor",
            "--action",
            "executor-consume",
            url=self.database_url,
        )
        self.assertEqual(consume_proc.returncode, 0, consume_proc.stderr)
        lines = [json.loads(line) for line in consume_proc.stdout.splitlines() if line.strip()]
        ready = lines[0]
        consume = lines[-1]
        self.assertTrue(ready["acquired"])
        self.assertEqual(ready["provider_calls"], 0)
        self.assertFalse(ready["polling_started"])
        self.assertEqual(ready["owner_count"], 1)
        self.assertTrue(consume["claimed"])
        self.assertTrue(consume["fake_sent"])
        self.assertEqual(consume["provider_calls"], 1)
        self.assertEqual(consume["publisher"], "publisher_1")

        replay = _harness(
            "--role",
            "executor",
            "--action",
            "executor-consume",
            url=self.database_url,
        )
        self.assertEqual(replay.returncode, 0, replay.stderr)
        replay_payload = json.loads(replay.stdout.splitlines()[-1])
        self.assertFalse(replay_payload["claimed"])
        self.assertEqual(replay_payload["provider_calls"], 0)

    def test_central_ping_is_not_blocked_by_slow_channel_send(self):
        enqueue = _harness(
            "--role",
            "primary",
            "--action",
            "enqueue",
            "--source-key",
            f"split-backlog-{uuid.uuid4().hex[:8]}",
            url=self.database_url,
        )
        self.assertEqual(enqueue.returncode, 0, enqueue.stderr)
        ack = _harness(
            "--role",
            "primary",
            "--action",
            "primary-ack",
            url=self.database_url,
        )
        self.assertEqual(ack.returncode, 0, ack.stderr + ack.stdout)
        with tempfile.TemporaryDirectory(prefix="split-ping-") as raw_dir:
            workdir = Path(raw_dir)
            executor = _start_script(
                "scripts.run_telegram_split_runtime_harness",
                "--role",
                "executor",
                "--action",
                "executor-consume",
                "--delay-send",
                "1.2",
                url=self.database_url,
                workdir=workdir,
            )
            try:
                executor.wait_ready()
                ping = _harness(
                    "--role",
                    "primary",
                    "--action",
                    "central-ping",
                    url=self.database_url,
                )
                self.assertEqual(ping.returncode, 0, ping.stderr)
                ping_payload = json.loads(ping.stdout)
                self.assertLess(ping_payload["central_interaction_seconds"], 0.4)
                executor.process.wait(timeout=20)
                consume = executor.stdout_lines()[-1]
                self.assertTrue(consume["claimed"])
                self.assertTrue(consume["fake_sent"])
            finally:
                if executor.process.poll() is None:
                    executor.release()
                    if executor.process.poll() is None:
                        executor.process.kill()
                        executor.process.wait(timeout=5)

    def test_primary_exit_leaves_executor_owner(self):
        with tempfile.TemporaryDirectory(prefix="split-hold-") as raw_dir:
            holder = _start_script(
                "scripts.run_telegram_split_runtime_harness",
                "--role",
                "executor",
                "--action",
                "hold-owner",
                url=self.database_url,
                workdir=Path(raw_dir),
            )
            try:
                ready = json.loads(holder.wait_ready().splitlines()[0])
                self.assertTrue(ready["acquired"])
                primary = _probe("--role", "primary", "--split-enabled", url=self.database_url)
                self.assertEqual(primary.returncode, 0, primary.stderr)
                owners = _harness(
                    "--role",
                    "executor",
                    "--action",
                    "count-owners",
                    url=self.database_url,
                )
                self.assertEqual(json.loads(owners.stdout)["owner_count"], 1)
            finally:
                if holder.process.poll() is None:
                    holder.release()

    def test_killed_executor_releases_owner_for_successor(self):
        with tempfile.TemporaryDirectory(prefix="split-kill-") as raw_dir:
            holder = _start_script(
                "scripts.run_telegram_split_runtime_harness",
                "--role",
                "executor",
                "--action",
                "hold-owner",
                url=self.database_url,
                workdir=Path(raw_dir),
            )
            try:
                ready = json.loads(holder.wait_ready().splitlines()[0])
                self.assertTrue(ready["acquired"])
                holder.process.kill()
                holder.process.wait(timeout=10)
            finally:
                if holder.process.poll() is None:
                    holder.process.kill()
                    holder.process.wait(timeout=5)

        deadline = time.time() + 8
        successor_payload = None
        while time.time() < deadline:
            successor = _probe(
                "--role",
                "executor",
                "--split-enabled",
                "--acquire-queue",
                url=self.database_url,
            )
            successor_payload = json.loads(successor.stdout)
            if successor.returncode == 0 and successor_payload.get("acquired"):
                break
            time.sleep(0.2)
        self.assertTrue(successor_payload and successor_payload.get("acquired"))
        self.assertEqual(
            successor_payload["queue_execution_identities"],
            ["primary", "channel_editor", *[f"publisher_{index}" for index in range(1, 6)]],
        )

    def test_sticky_edit_keeps_the_original_publisher(self):
        result = _harness(
            "--role",
            "executor",
            "--action",
            "sticky-edit",
            "--source-key",
            f"split-sticky-{uuid.uuid4().hex[:8]}",
            "--publisher",
            "publisher_3",
            url=self.database_url,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["sticky"])
        self.assertEqual(payload["first_publisher"], payload["edit_publisher"])
        self.assertEqual(payload["first_publisher"], "publisher_3")

    def test_two_primaries_cannot_share_central_poller(self):
        with tempfile.TemporaryDirectory(prefix="split-central-") as raw_dir:
            workdir = Path(raw_dir)
            first = _start_script(
                "scripts.probe_telegram_split_runtime",
                "--role",
                "primary",
                "--split-enabled",
                "--acquire-central",
                "--hold",
                url=self.database_url,
                workdir=workdir,
            )
            try:
                ready = json.loads(first.wait_ready().splitlines()[0])
                self.assertTrue(ready["acquired_central"])
                second = _probe(
                    "--role",
                    "primary",
                    "--split-enabled",
                    "--acquire-central",
                    url=self.database_url,
                )
                self.assertEqual(second.returncode, 3, second.stderr)
                second_payload = json.loads(second.stdout)
                self.assertFalse(second_payload["acquired_central"])
                self.assertEqual(
                    second_payload["error"],
                    "telegram_central_poller_already_active",
                )
                executor = _probe(
                    "--role",
                    "executor",
                    "--split-enabled",
                    "--acquire-central",
                    url=self.database_url,
                )
                self.assertEqual(executor.returncode, 3, executor.stderr)
                self.assertEqual(
                    json.loads(executor.stdout)["error"],
                    "executor_must_not_acquire_central_poller",
                )
            finally:
                if first.process.poll() is None:
                    first.release()

        successor = _probe(
            "--role",
            "primary",
            "--split-enabled",
            "--acquire-central",
            url=self.database_url,
        )
        self.assertEqual(successor.returncode, 0, successor.stderr)
        self.assertTrue(json.loads(successor.stdout)["acquired_central"])

    def test_all_holds_both_locks_and_central_loss_keeps_queue(self):
        with tempfile.TemporaryDirectory(prefix="split-all-locks-") as raw_dir:
            holder = _start_script(
                "scripts.probe_telegram_split_runtime",
                "--role",
                "all",
                "--acquire-queue",
                "--acquire-central",
                "--hold",
                url=self.database_url,
                workdir=Path(raw_dir),
            )
            try:
                ready = json.loads(holder.wait_ready().splitlines()[0])
                self.assertTrue(ready["acquired"])
                self.assertTrue(ready["acquired_central"])
                second_queue = _probe(
                    "--role",
                    "executor",
                    "--split-enabled",
                    "--acquire-queue",
                    url=self.database_url,
                )
                self.assertEqual(second_queue.returncode, 3, second_queue.stderr)
                second_central = _probe(
                    "--role",
                    "primary",
                    "--split-enabled",
                    "--acquire-central",
                    url=self.database_url,
                )
                self.assertEqual(second_central.returncode, 3, second_central.stderr)
            finally:
                if holder.process.poll() is None:
                    holder.release()

        import asyncio

        async def lost_central_keeps_queue():
            engine = create_async_engine(
                make_url(self.database_url)
                .set(drivername="postgresql+asyncpg")
                .render_as_string(hide_password=False),
                pool_pre_ping=True,
            )
            try:
                queue_lease = await acquire_telegram_delivery_queue_owner(engine)
                central_lease = await acquire_telegram_central_poller_owner(engine)
                import psycopg2

                admin = psycopg2.connect(self.database_url)
                admin.autocommit = True
                try:
                    with admin.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_terminate_backend(%s)",
                            (central_lease.backend_pid,),
                        )
                finally:
                    admin.close()
                with self.assertRaises(TelegramCentralPollerLeaseLostError):
                    await asyncio.wait_for(
                        telegram_central_poller_owner_monitor_loop(
                            central_lease, interval_seconds=0.05
                        ),
                        timeout=3,
                    )
                await queue_lease.assert_held()
                await queue_lease.close()
            finally:
                await engine.dispose()

        asyncio.run(lost_central_keeps_queue())


if __name__ == "__main__":
    unittest.main()
