#!/usr/bin/env python3
"""Run mandatory market proofs against disposable local PostgreSQL databases."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
from urllib.parse import urlparse

import psycopg2
from psycopg2 import sql
import redis
from sqlalchemy.engine import URL, make_url


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ADMIN_DATABASE = "postgres"
SKIPPED_PATTERN = re.compile(r"(?:skipped=|\bskipped\s+)([1-9]\d*)")
TEST_COUNT_PATTERN = re.compile(r"\bRan\s+(\d+)\s+tests?\b")
FULL_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

PROOF_SUITES = (
    ("tests.test_market_offer_admission_postgres", "MARKET_STAGE6_TEST_DATABASE_URL", "market_stage6"),
    ("tests.test_offer_quota_admission_postgres", "MARKET_STAGE7_TEST_DATABASE_URL", "market_stage7"),
    ("tests.test_trade_price_settlement_postgres", "MARKET_STAGE8_TEST_DATABASE_URL", "market_stage8"),
    ("tests.test_offer_publication_repair_postgres", "MARKET_STAGE9_TEST_DATABASE_URL", "market_stage9"),
    ("tests.test_offer_cancel_all_postgres", "MARKET_STAGE10_TEST_DATABASE_URL", "market_stage10"),
    ("tests.test_offer_expiry_receipts_postgres", "MARKET_STAGE11_TEST_DATABASE_URL", "market_stage11"),
    ("tests.test_active_offer_pagination_postgres", "MARKET_STAGE12_TEST_DATABASE_URL", "market_stage12"),
    ("tests.test_trade_history_pagination_postgres", "MARKET_STAGE13_TEST_DATABASE_URL", "market_stage13"),
    ("tests.test_offer_idempotency_fingerprint_postgres", "MARKET_STAGE15_TEST_DATABASE_URL", "market_stage15"),
)
TEST_MODULES = tuple(module for module, _variable, _prefix in PROOF_SUITES)


class MarketPostgresGateSafetyError(RuntimeError):
    pass


class MarketPostgresGateInterrupted(BaseException):
    def __init__(self, signum: int):
        super().__init__(f"signal_{signum}")
        self.signum = signum


def _validate_admin_url(admin_url: str) -> URL:
    try:
        parsed = make_url(admin_url)
    except Exception as exc:
        raise MarketPostgresGateSafetyError("admin URL parsing failed") from exc
    if parsed.get_backend_name() != "postgresql":
        raise MarketPostgresGateSafetyError("admin URL must use PostgreSQL")
    if parsed.query:
        raise MarketPostgresGateSafetyError("admin URL query parameters are forbidden")
    if str(parsed.host or "").lower() not in LOCAL_HOSTS:
        raise MarketPostgresGateSafetyError("admin URL must target localhost")
    if str(parsed.database or "").lower() != ADMIN_DATABASE:
        raise MarketPostgresGateSafetyError("admin URL must target the postgres control database")
    if not parsed.username:
        raise MarketPostgresGateSafetyError("admin URL must include a disposable test user")
    return parsed


def _validate_checkout(expected_sha: str) -> str:
    normalized = str(expected_sha or "").strip().lower()
    if FULL_SHA_PATTERN.fullmatch(normalized) is None:
        raise MarketPostgresGateSafetyError("a full expected checkout SHA is required")
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip().lower()
    worktree_status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if Path(root).resolve() != REPO_ROOT.resolve():
        raise MarketPostgresGateSafetyError("gate checkout does not match script checkout")
    if head != normalized:
        raise MarketPostgresGateSafetyError("gate checkout does not match expected SHA")
    if worktree_status:
        raise MarketPostgresGateSafetyError("gate checkout has uncommitted source changes")
    return head


def _database_names(run_id: str | None = None) -> dict[str, str]:
    token = str(run_id or f"{os.getpid()}_{secrets.token_hex(4)}").strip().lower()
    if not token or re.fullmatch(r"[a-z0-9_]+", token) is None:
        raise MarketPostgresGateSafetyError("scratch run id is invalid")
    return {
        variable: f"{prefix}_ci_{token}_test"
        for _module, variable, prefix in PROOF_SUITES
    }


def _database_url(admin_url: str, database: str, *, driver: str = "postgresql") -> str:
    parsed = _validate_admin_url(admin_url)
    return parsed.set(database=database, drivername=driver).render_as_string(
        hide_password=False
    )


def _connect(admin_url: str):
    parsed = _validate_admin_url(admin_url).set(drivername="postgresql")
    return psycopg2.connect(parsed.render_as_string(hide_password=False))


def _create_scratch_databases(
    admin_url: str,
    databases: dict[str, str],
    created: list[str],
    evidence: list[str],
) -> None:
    connection = _connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            for database in databases.values():
                cursor.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s",
                    (database,),
                )
                if cursor.fetchone() is not None:
                    raise MarketPostgresGateSafetyError(
                        f"refusing pre-existing scratch database: {database}"
                    )
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                created.append(database)
                evidence.append(f"scratch.created={database}")
    finally:
        connection.close()


def _drop_scratch_databases(
    admin_url: str,
    databases: list[str],
    evidence: list[str],
) -> None:
    if not databases:
        evidence.append("scratch.remaining=0")
        return
    connection = _connect(admin_url)
    errors: list[str] = []
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            for database in reversed(databases):
                try:
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (database,),
                    )
                    cursor.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database))
                    )
                    evidence.append(f"scratch.dropped={database}")
                except Exception as exc:
                    errors.append(f"{database}:{type(exc).__name__}")
            cursor.execute(
                "SELECT datname FROM pg_database WHERE datname = ANY(%s) ORDER BY datname",
                (databases,),
            )
            remaining = [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()
    evidence.append(f"scratch.remaining={len(remaining)}")
    if errors or remaining:
        detail = ",".join(errors + remaining)
        raise MarketPostgresGateSafetyError(f"scratch cleanup incomplete: {detail}")


def _validate_redis_url(redis_url: str) -> tuple[str, int]:
    parsed = urlparse(redis_url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise MarketPostgresGateSafetyError("Redis URL scheme is invalid")
    if str(parsed.hostname or "").lower() not in LOCAL_HOSTS:
        raise MarketPostgresGateSafetyError("Redis URL must target localhost")
    if parsed.params or parsed.query or parsed.fragment:
        raise MarketPostgresGateSafetyError("Redis URL parameters are forbidden")
    if parsed.path != "/14":
        raise MarketPostgresGateSafetyError("Redis URL must target scratch DB 14")
    client = redis.Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        if client.ping() is not True:
            raise MarketPostgresGateSafetyError("scratch Redis ping failed")
    except MarketPostgresGateSafetyError:
        raise
    except Exception as exc:
        raise MarketPostgresGateSafetyError("scratch Redis is unreachable") from exc
    finally:
        client.close()
    return str(parsed.hostname), int(parsed.port or 6379)


def _base_test_environment(
    admin_url: str,
    databases: dict[str, str],
    redis_url: str,
) -> tuple[dict[str, str], dict[str, str]]:
    parsed_admin = _validate_admin_url(admin_url)
    redis_host, redis_port = _validate_redis_url(redis_url)
    stage_urls = {
        variable: _database_url(admin_url, database)
        for variable, database in databases.items()
    }
    env = os.environ.copy()
    env.update(stage_urls)
    env.update(
        {
            "SERVER_MODE": "foreign",
            "ENVIRONMENT": "test",
            "REGISTRATION_SYNC_V2_ENABLED": "false",
            "BOT_USERNAME": "market_postgres_gate_bot",
            "FRONTEND_URL": "http://127.0.0.1:4173",
            "JWT_SECRET_KEY": "market_postgres_gate_secret",
            "REDIS_URL": redis_url,
            "REDIS_HOST": redis_host,
            "REDIS_PORT": str(redis_port),
            "POSTGRES_USER": str(parsed_admin.username),
            "POSTGRES_PASSWORD": str(parsed_admin.password or ""),
        }
    )
    return env, stage_urls


def _run_gate(
    admin_url: str,
    databases: dict[str, str],
    redis_url: str,
) -> tuple[int, str]:
    base_env, stage_urls = _base_test_environment(admin_url, databases, redis_url)
    output_parts: list[str] = []
    total_tests = 0
    for module, variable, _prefix in PROOF_SUITES:
        suite_url = make_url(stage_urls[variable])
        env = base_env.copy()
        env.update(
            {
                "DATABASE_URL": suite_url.set(drivername="postgresql+asyncpg").render_as_string(
                    hide_password=False
                ),
                "SYNC_DATABASE_URL": suite_url.set(
                    drivername="postgresql+psycopg2"
                ).render_as_string(hide_password=False),
                "POSTGRES_DB": str(suite_url.database),
            }
        )
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", module],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        output = result.stdout + result.stderr
        output_parts.append(f"proof.module={module}\n{output}")
        if result.returncode != 0:
            return int(result.returncode), "\n".join(output_parts)
        test_counts = [
            int(match.group(1)) for match in TEST_COUNT_PATTERN.finditer(output)
        ]
        if len(test_counts) != 1 or test_counts[0] <= 0:
            output_parts.append(f"proof.test_count_rejected={module}")
            return 2, "\n".join(output_parts)
        total_tests += test_counts[0]
        if SKIPPED_PATTERN.search(output):
            output_parts.append(f"proof.skip_rejected={module}")
            return 2, "\n".join(output_parts)
    output_parts.append(
        f"proof.summary=modules:{len(PROOF_SUITES)},tests:{total_tests},skipped:0"
    )
    return 0, "\n".join(output_parts)


def _write_log(log_path: Path, output: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8")
    print(output, end="" if output.endswith("\n") else "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--admin-url",
        default=os.getenv("MARKET_POSTGRES_GATE_ADMIN_URL", ""),
    )
    parser.add_argument(
        "--expected-sha",
        default=os.getenv("MARKET_POSTGRES_GATE_EXPECTED_SHA", ""),
    )
    parser.add_argument("--log-path", default="tmp/market-postgres-gate.log")
    args = parser.parse_args(argv)
    admin_url = str(args.admin_url).strip()
    if not admin_url:
        parser.error("--admin-url or MARKET_POSTGRES_GATE_ADMIN_URL is required")

    evidence: list[str] = []
    created: list[str] = []
    exit_code = 2
    databases = _database_names()
    old_handlers: dict[int, object] = {}

    def interrupt(signum, _frame):
        raise MarketPostgresGateInterrupted(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        old_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupt)

    try:
        _validate_admin_url(admin_url)
        head = _validate_checkout(str(args.expected_sha))
        redis_url = os.getenv(
            "MARKET_POSTGRES_GATE_REDIS_URL",
            "redis://127.0.0.1:6379/14",
        )
        _validate_redis_url(redis_url)
        evidence.extend(
            (
                f"gate.head={head}",
                f"gate.repo={REPO_ROOT}",
                f"gate.suites={len(PROOF_SUITES)}",
            )
        )
        _create_scratch_databases(admin_url, databases, created, evidence)
        exit_code, proof_output = _run_gate(admin_url, databases, redis_url)
        evidence.append(proof_output)
    except MarketPostgresGateInterrupted as exc:
        exit_code = 128 + int(exc.signum)
        evidence.append(f"gate.interrupted=signal_{exc.signum}")
    except KeyboardInterrupt:
        exit_code = 130
        evidence.append("gate.interrupted=keyboard")
    except Exception as exc:
        exit_code = 2
        evidence.append(f"gate.failure={type(exc).__name__}:{exc}")
    finally:
        try:
            _drop_scratch_databases(admin_url, created, evidence)
        except Exception as exc:
            exit_code = 2
            evidence.append(f"gate.cleanup_failure={type(exc).__name__}:{exc}")
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        _write_log(Path(args.log_path), "\n".join(evidence))
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
