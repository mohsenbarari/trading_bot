#!/usr/bin/env python3
"""Run the local Telegram queue evidence matrix with fail-closed hygiene.

This runner is deliberately unable to target staging/production or call the
Telegram provider.  It turns ResourceWarning, unraisable exceptions, leaked
event-loop task failures, and file-descriptor growth into a failing result.
"""
from __future__ import annotations

import argparse
import atexit
import asyncio
import gc
from hashlib import sha256
from importlib import metadata
import io
import json
import os
import platform
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest
from urllib.parse import urlparse, urlunparse
import warnings


_SCRATCH_DATABASE_PATTERN = re.compile(
    r"^telegram_queue_stage3_[a-z0-9_]+_test$"
)
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_POSTGRES_SYSTEM_IDENTIFIER_PATTERN = re.compile(r"^[1-9][0-9]{15,19}$")
_SYNTHETIC_CHANNEL_ID = -1001234567890
# Physical PostgreSQL/Alembic/CLI tests legitimately block their isolated test
# task for several seconds while a subprocess runs. Ten seconds suppresses that
# known harness noise while still surfacing an event-loop stall large enough to
# invalidate timing-sensitive local evidence.
_SLOW_CALLBACK_THRESHOLD_SECONDS = 10.0


class LocalEvidenceConfigurationError(ValueError):
    """Raised before imports or test side effects on an unsafe target."""


class _ExclusiveScratchLock:
    """Own one session advisory lock for the complete evidence process."""

    def __init__(self, connection, *, lock_key: int, fingerprint: str) -> None:
        self._connection = connection
        self._lock_key = lock_key
        self.fingerprint = fingerprint
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            cursor = self._connection.cursor()
            try:
                cursor.execute("SELECT pg_advisory_unlock(%s)", (self._lock_key,))
            finally:
                cursor.close()
        finally:
            self._connection.close()


class _TeeTextStream(io.TextIOBase):
    """Mirror unittest output to the console and an owned evidence file."""

    def __init__(self, console: io.TextIOBase, evidence: io.TextIOBase) -> None:
        self._console = console
        self._evidence = evidence

    def write(self, value: str) -> int:
        self._console.write(value)
        self._evidence.write(value)
        return len(value)

    def flush(self) -> None:
        self._console.flush()
        self._evidence.flush()


class _FdTrackingTextTestResult(unittest.TextTestResult):
    """Record the first test boundary at which a descriptor remains open."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fd_growth_events: list[dict[str, object]] = []
        self.test_results: list[dict[str, object]] = []
        self._fd_before_test: dict[int, str] | None = None
        self._test_started_at: float | None = None
        self._recorded_test_ids: set[str] = set()

    def startTest(self, test) -> None:
        self._fd_before_test = _open_fd_snapshot()
        self._test_started_at = time.perf_counter()
        super().startTest(test)

    def _record_test(self, test, status: str) -> None:
        test_id = test.id()
        if test_id in self._recorded_test_ids:
            return
        self._recorded_test_ids.add(test_id)
        duration_ms = (
            None
            if self._test_started_at is None
            else round((time.perf_counter() - self._test_started_at) * 1000.0, 3)
        )
        self.test_results.append(
            {"test_id": test_id, "status": status, "duration_ms": duration_ms}
        )

    def addSuccess(self, test) -> None:
        self._record_test(test, "passed")
        super().addSuccess(test)

    def addFailure(self, test, err) -> None:
        self._record_test(test, "failed")
        super().addFailure(test, err)

    def addError(self, test, err) -> None:
        self._record_test(test, "error")
        super().addError(test, err)

    def addSkip(self, test, reason) -> None:
        self._record_test(test, "skipped")
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err) -> None:
        self._record_test(test, "expected_failure")
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test) -> None:
        self._record_test(test, "unexpected_success")
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err) -> None:
        if err is not None:
            self._record_test(
                test,
                "failed" if issubclass(err[0], test.failureException) else "error",
            )
        super().addSubTest(test, subtest, err)

    def stopTest(self, test) -> None:
        fd_after_test = _open_fd_snapshot()
        if self._fd_before_test is not None and fd_after_test is not None:
            opened = [
                {"fd": descriptor, "target": target}
                for descriptor, target in sorted(fd_after_test.items())
                if self._fd_before_test.get(descriptor) != target
                and not target.startswith("anon_inode:")
                and not target.startswith("pipe:")
                and target != "/dev/null"
            ]
            if opened:
                self.fd_growth_events.append(
                    {"test": test.id(), "opened": opened}
                )
        self._fd_before_test = None
        self._test_started_at = None
        super().stopTest(test)


def _flatten_test_ids(suite: unittest.TestSuite) -> list[str]:
    ids: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            ids.extend(_flatten_test_ids(item))
        else:
            ids.append(item.id())
    return ids


def _git_metadata(repo_root: Path) -> dict[str, object]:
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    try:
        return {
            "commit": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "dirty": bool(git("status", "--porcelain")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "branch": None, "dirty": None}


def _dependency_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for package in ("SQLAlchemy", "asyncpg", "psycopg2-binary", "redis", "pydantic", "aiogram"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return versions


def _validated_database_urls(raw_url: str) -> tuple[str, str]:
    parsed = urlparse(str(raw_url or "").strip())
    database_name = parsed.path.lstrip("/").lower()
    if (
        parsed.scheme not in {"postgresql", "postgres"}
        or parsed.hostname not in _LOCAL_HOSTS
        or _SCRATCH_DATABASE_PATTERN.fullmatch(database_name) is None
    ):
        raise LocalEvidenceConfigurationError(
            "local evidence requires a localhost telegram_queue_stage3_*_test database"
        )
    sync_url = urlunparse(parsed._replace(scheme="postgresql"))
    async_url = urlunparse(parsed._replace(scheme="postgresql+asyncpg"))
    return sync_url, async_url


def _validated_redis_url(raw_url: str) -> str:
    parsed = urlparse(str(raw_url or "").strip())
    if (
        parsed.scheme not in {"redis", "rediss"}
        or parsed.hostname not in _LOCAL_HOSTS
        or parsed.path in {"", "/", "/0"}
    ):
        raise LocalEvidenceConfigurationError(
            "local evidence requires a non-default localhost Redis database"
        )
    return str(raw_url).strip()


def _scratch_lock_identity(database_url: str, redis_url: str) -> tuple[int, str]:
    database = urlparse(database_url)
    redis = urlparse(redis_url)
    safe_identity = (
        "telegram-queue-local-evidence-v1|"
        f"postgres={database.hostname}:{database.port or 5432}/{database.path.lstrip('/')}|"
        f"redis={redis.hostname}:{redis.port or 6379}/{redis.path.lstrip('/')}"
    )
    digest = sha256(safe_identity.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True), digest.hex()[:16]


def _acquire_exclusive_scratch_lock(
    *,
    database_url: str,
    redis_url: str,
    expected_system_id: str,
) -> _ExclusiveScratchLock:
    import psycopg2

    expected = str(expected_system_id or "").strip()
    if _POSTGRES_SYSTEM_IDENTIFIER_PATTERN.fullmatch(expected) is None:
        raise LocalEvidenceConfigurationError(
            "expected scratch-cluster system identifier is required"
        )
    lock_key, fingerprint = _scratch_lock_identity(database_url, redis_url)
    connection = psycopg2.connect(database_url, connect_timeout=5)
    try:
        connection.autocommit = True
        cursor = connection.cursor()
        try:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s), system_identifier::text "
                "FROM pg_control_system()",
                (lock_key,),
            )
            acquired, observed_system_id = cursor.fetchone()
            acquired = bool(acquired)
        finally:
            cursor.close()
    except BaseException:
        connection.close()
        raise
    if not acquired:
        connection.close()
        raise LocalEvidenceConfigurationError(
            "local evidence scratch database is already owned by another runner"
        )
    if str(observed_system_id) != expected:
        connection.close()
        raise LocalEvidenceConfigurationError(
            "local evidence database is not on the expected scratch cluster"
        )
    return _ExclusiveScratchLock(
        connection,
        lock_key=lock_key,
        fingerprint=fingerprint,
    )


def _configure_synthetic_environment(
    *, database_url: str, redis_url: str, expected_system_id: str
) -> None:
    sync_url, async_url = _validated_database_urls(database_url)
    validated_redis = _validated_redis_url(redis_url)
    values = {
        "ENVIRONMENT": "synthetic-test",
        "SERVER_MODE": "foreign",
        "DATABASE_URL": async_url,
        "SYNC_DATABASE_URL": sync_url,
        "TELEGRAM_QUEUE_STAGE3_TEST_DATABASE_URL": sync_url,
        "REDIS_URL": validated_redis,
        "TELEGRAM_QUEUE_STAGE3_TEST_REDIS_URL": validated_redis,
        "POSTGRES_DB": "synthetic-test",
        "POSTGRES_USER": "synthetic-test",
        "POSTGRES_PASSWORD": "synthetic-test",
        "FRONTEND_URL": "http://localhost",
        "JWT_SECRET_KEY": "synthetic-local-evidence-only",
        "CHANNEL_ID": str(_SYNTHETIC_CHANNEL_ID),
        "TELEGRAM_DELIVERY_QUEUE_ENABLED": "false",
        # Provider credentials are overwritten, not inherited.  Queue tests
        # inject fake gateways and must never reach a real Bot API identity.
        "BOT_TOKEN": "",
        "TELEGRAM_DELIVERY_QUEUE_CHANNEL_EDITOR_BOT_TOKEN": "",
        "CHANNEL_EDITOR_BOT_TOKEN": "",
        "TRADING_BOT_EXPECTED_SCRATCH_CLUSTER_SYSTEM_ID": expected_system_id,
    }
    os.environ.update(values)


def _open_fd_snapshot() -> dict[int, str] | None:
    fd_root = Path("/proc/self/fd")
    if not fd_root.is_dir():
        return None
    snapshot: dict[int, str] = {}
    for entry in fd_root.iterdir():
        try:
            descriptor = int(entry.name)
            snapshot[descriptor] = os.readlink(entry)
        except (FileNotFoundError, OSError, ValueError):
            continue
    return snapshot


def _install_async_hygiene(loop_failures: list[str]) -> None:
    def loop_factory() -> asyncio.AbstractEventLoop:
        loop = asyncio.new_event_loop()
        loop.slow_callback_duration = _SLOW_CALLBACK_THRESHOLD_SECONDS

        def exception_handler(
            _loop: asyncio.AbstractEventLoop,
            context: dict[str, object],
        ) -> None:
            message = str(context.get("message") or "asyncio_unhandled_error")
            exception = context.get("exception")
            suffix = type(exception).__name__ if exception is not None else "none"
            loop_failures.append(f"{message}:{suffix}")

        loop.set_exception_handler(exception_handler)
        return loop

    def setup_runner(test_case: unittest.IsolatedAsyncioTestCase) -> None:
        if getattr(test_case, "_asyncioRunner", None) is not None:
            raise RuntimeError("asyncio runner is already initialized")
        test_case._asyncioRunner = asyncio.Runner(  # type: ignore[attr-defined]
            debug=True,
            loop_factory=loop_factory,
        )

    unittest.IsolatedAsyncioTestCase._setupAsyncioRunner = setup_runner  # type: ignore[method-assign]


def _write_report(path: str | None, report: dict[str, object]) -> None:
    if not path:
        return
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True, choices=("synthetic-test",))
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--redis-url", required=True)
    parser.add_argument(
        "--expected-postgres-system-id",
        default=os.getenv("TRADING_BOT_EXPECTED_SCRATCH_CLUSTER_SYSTEM_ID", ""),
    )
    parser.add_argument("--pattern", default="test_telegram*.py")
    parser.add_argument("--start-directory", default="tests")
    parser.add_argument("--report")
    parser.add_argument("--log")
    parser.add_argument("--verbosity", type=int, choices=(0, 1, 2), default=1)
    args = parser.parse_args(argv)

    _configure_synthetic_environment(
        database_url=args.database_url,
        redis_url=args.redis_url,
        expected_system_id=args.expected_postgres_system_id,
    )
    sync_database_url, _ = _validated_database_urls(args.database_url)
    scratch_lock = _acquire_exclusive_scratch_lock(
        database_url=sync_database_url,
        redis_url=_validated_redis_url(args.redis_url),
        expected_system_id=args.expected_postgres_system_id,
    )
    atexit.register(scratch_lock.release)
    # Nested CLI negative tests must not inherit this runner's arguments.
    sys.argv = [sys.argv[0]]
    warnings.simplefilter("error", ResourceWarning)
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    unraisable_failures: list[str] = []
    loop_failures: list[str] = []
    previous_unraisable_hook = sys.unraisablehook

    def unraisable_hook(event) -> None:
        unraisable_failures.append(
            f"{type(event.exc_value).__name__}:{event.err_msg or 'unraisable'}"
        )

    sys.unraisablehook = unraisable_hook
    _install_async_hygiene(loop_failures)
    suite = unittest.defaultTestLoader.discover(
        start_dir=str((repo_root / args.start_directory).resolve()),
        pattern=args.pattern,
    )
    discovered_test_ids = _flatten_test_ids(suite)
    test_inventory_sha256 = sha256(
        "\n".join(discovered_test_ids).encode("utf-8")
    ).hexdigest()
    # Imports and the first uvloop/event-loop initialization may create
    # process-lifetime selector descriptors. Warm them before the leak
    # baseline so only per-test growth is treated as a leak.
    warm_loop = asyncio.new_event_loop()
    try:
        warm_loop.run_until_complete(asyncio.sleep(0))
    finally:
        warm_loop.close()
    gc.collect()
    fd_before_snapshot = _open_fd_snapshot()
    evidence_log = None
    test_stream = None
    if args.log:
        log_target = Path(args.log).resolve()
        log_target.parent.mkdir(parents=True, exist_ok=True)
        evidence_log = log_target.open("w", encoding="utf-8")
        test_stream = _TeeTextStream(sys.stderr, evidence_log)
    captured_warnings: list[warnings.WarningMessage] = []
    try:
        with warnings.catch_warnings(record=True) as warning_records:
            warnings.simplefilter("always")
            warnings.filterwarnings("error", category=ResourceWarning)
            result = unittest.TextTestRunner(
                stream=test_stream,
                verbosity=args.verbosity,
                buffer=True,
                resultclass=_FdTrackingTextTestResult,
            ).run(suite)
            captured_warnings = list(warning_records)
    finally:
        if evidence_log is not None:
            evidence_log.close()

    try:
        # Keep the evidence hook installed through deterministic finalization.
        # Otherwise a late __del__ ResourceWarning can be printed after the
        # JSON verdict and escape the report entirely.
        gc.collect()
        fd_after_snapshot = _open_fd_snapshot()
    finally:
        sys.unraisablehook = previous_unraisable_hook
    fd_before = None if fd_before_snapshot is None else len(fd_before_snapshot)
    fd_after = None if fd_after_snapshot is None else len(fd_after_snapshot)
    fd_growth = (
        None
        if fd_before is None or fd_after is None
        else fd_after - fd_before
    )
    fd_opened = (
        []
        if fd_before_snapshot is None or fd_after_snapshot is None
        else [
            {"fd": descriptor, "target": target}
            for descriptor, target in sorted(fd_after_snapshot.items())
            if fd_before_snapshot.get(descriptor) != target
        ]
    )
    result_inventory_complete = bool(
        len(result.test_results) == result.testsRun == len(discovered_test_ids)
    )
    warning_categories: dict[str, int] = {}
    for warning in captured_warnings:
        category = warning.category.__name__
        warning_categories[category] = warning_categories.get(category, 0) + 1
    hygiene_ok = (
        not unraisable_failures
        and not loop_failures
        and (fd_growth is None or fd_growth <= 0)
        and result_inventory_complete
        and not result.skipped
    )
    report: dict[str, object] = {
        "schema_version": 2,
        "environment": "synthetic-test",
        "pattern": args.pattern,
        "tests_run": result.testsRun,
        "discovered_test_count": len(discovered_test_ids),
        "test_inventory_sha256": test_inventory_sha256,
        "result_inventory_complete": result_inventory_complete,
        "test_results": result.test_results,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "resource_warnings_are_errors": True,
        "captured_warning_count": len(captured_warnings),
        "captured_warning_categories": dict(sorted(warning_categories.items())),
        "slow_callback_threshold_seconds": _SLOW_CALLBACK_THRESHOLD_SECONDS,
        "unraisable_failures": unraisable_failures,
        "asyncio_loop_failures": loop_failures,
        "fd_before": fd_before,
        "fd_after": fd_after,
        "fd_growth": fd_growth,
        "fd_opened": fd_opened,
        "fd_growth_events": result.fd_growth_events,
        "provider_credentials_forced_empty": True,
        "exclusive_scratch_lock": True,
        "scratch_lock_fingerprint": scratch_lock.fingerprint,
        "scratch_cluster_system_id": args.expected_postgres_system_id,
        "git": _git_metadata(repo_root),
        "runtime": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "dependencies": _dependency_versions(),
        },
        "invocation": {
            "environment": args.environment,
            "pattern": args.pattern,
            "start_directory": args.start_directory,
            "verbosity": args.verbosity,
            "database_url_redacted": True,
            "redis_url_redacted": True,
            "command_redacted": [
                sys.executable,
                "scripts/run_telegram_queue_local_evidence.py",
                "--environment",
                args.environment,
                "--database-url",
                "[redacted-local-scratch-url]",
                "--redis-url",
                "[redacted-local-scratch-url]",
                "--expected-postgres-system-id",
                args.expected_postgres_system_id,
                "--pattern",
                args.pattern,
                "--start-directory",
                args.start_directory,
                "--verbosity",
                str(args.verbosity),
            ],
        },
        "success": bool(result.wasSuccessful() and hygiene_ok),
    }
    _write_report(args.report, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    exit_code = 0 if report["success"] else 1
    scratch_lock.release()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
