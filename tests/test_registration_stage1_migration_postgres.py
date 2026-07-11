import os
import re
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from core.registration_identity import normalize_account_name, normalize_mobile_number


MIGRATION_DATABASE_PATTERN = re.compile(r"^stage1_migration_[a-z0-9_]+$")


def _migration_database_urls() -> tuple[str, str] | None:
    raw = str(os.getenv("STAGE1_MIGRATION_TEST_DATABASE_URL", "")).strip()
    if not raw:
        return None
    url = make_url(raw)
    database_name = str(url.database or "").lower()
    if not MIGRATION_DATABASE_PATTERN.fullmatch(database_name):
        raise RuntimeError(
            "Stage 1 migration tests require a stage1_migration_* scratch database"
        )
    sync_url = url.set(drivername="postgresql+psycopg2").render_as_string(
        hide_password=False
    )
    async_url = url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )
    return sync_url, async_url


MIGRATION_DATABASE_URLS = _migration_database_urls()


def _run_alembic(sync_url: str, *args: str) -> None:
    result = _run_alembic_result(sync_url, *args)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


def _run_alembic_result(sync_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["SYNC_DATABASE_URL"] = sync_url
    env["DATABASE_URL"] = sync_url
    env["TRADING_BOT_MIGRATION_MODE"] = "scratch"
    env["TRADING_BOT_EXPECTED_CHECKOUT"] = os.getcwd()
    return subprocess.run(
        [sys.executable, "scripts/run_guarded_scratch_alembic.py", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class Stage1MigrationDatabaseSafetyTests(unittest.TestCase):
    def test_rejects_non_scratch_database_name(self):
        previous = os.environ.get("STAGE1_MIGRATION_TEST_DATABASE_URL")
        os.environ["STAGE1_MIGRATION_TEST_DATABASE_URL"] = (
            "postgresql://user:pass@db/trading_bot_db"
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "stage1_migration_\\*"):
                _migration_database_urls()
        finally:
            if previous is None:
                os.environ.pop("STAGE1_MIGRATION_TEST_DATABASE_URL", None)
            else:
                os.environ["STAGE1_MIGRATION_TEST_DATABASE_URL"] = previous


@unittest.skipUnless(
    MIGRATION_DATABASE_URLS,
    "set STAGE1_MIGRATION_TEST_DATABASE_URL for the real migration matrix",
)
class Stage1MigrationPostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_requires_relation_and_temporal_completion_evidence(self):
        sync_url, async_url = MIGRATION_DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "f7c8d9e0a1b2")
        engine = create_async_engine(async_url, pool_pre_ping=True)
        label = uuid4().hex[:12]
        created_at = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        expires_at = created_at + timedelta(hours=2)

        cases = {
            "standard_ambiguous": {
                "prefix": "INV",
                "relation": None,
                "relation_status": None,
                "activation": None,
                "should_complete": False,
            },
            "accountant_valid": {
                "prefix": "ACCT",
                "relation": "accountant",
                "relation_status": "active",
                "activation": created_at + timedelta(minutes=30),
                "should_complete": True,
            },
            "customer_revoked": {
                "prefix": "CUST",
                "relation": "customer",
                "relation_status": "revoked",
                "activation": created_at + timedelta(minutes=30),
                "should_complete": False,
            },
            "customer_late": {
                "prefix": "CUST",
                "relation": "customer",
                "relation_status": "active",
                "activation": expires_at + timedelta(seconds=1),
                "should_complete": False,
            },
            "customer_valid": {
                "prefix": "CUST",
                "relation": "customer",
                "relation_status": "expired",
                "activation": created_at + timedelta(minutes=45),
                "should_complete": True,
            },
        }
        seeded: dict[str, tuple[int, int]] = {}

        try:
            async with engine.begin() as connection:
                owner_id = (
                    await connection.execute(
                        text(
                            """
                            INSERT INTO users (
                                account_name, mobile_number, full_name, address, role,
                                has_bot_access, is_deleted, must_change_password, home_server
                            ) VALUES (
                                :account, :mobile, 'Migration Owner', 'Migration owner address',
                                'SUPER_ADMIN', true, false, false, 'iran'
                            ) RETURNING id
                            """
                        ),
                        {
                            "account": f"migration_owner_{label}",
                            "mobile": f"090{int(label[:8], 16) % 100000000:08d}",
                        },
                    )
                ).scalar_one()

                for index, (case_name, case) in enumerate(cases.items(), start=1):
                    account = f"migration_{case_name}_{label}"
                    mobile = f"091{(int(label[:8], 16) + index) % 100000000:08d}"
                    user_id = (
                        await connection.execute(
                            text(
                                """
                                INSERT INTO users (
                                    account_name, mobile_number, full_name, address, role,
                                    has_bot_access, is_deleted, must_change_password, home_server,
                                    created_at
                                ) VALUES (
                                    :account, :mobile, :full_name, 'Migration target address',
                                    'STANDARD', true, false, false, 'iran', :created_at
                                ) RETURNING id
                                """
                            ),
                            {
                                "account": account,
                                "mobile": mobile,
                                "full_name": case_name,
                                "created_at": created_at,
                            },
                        )
                    ).scalar_one()
                    token = f"{case['prefix']}-migration-{case_name}-{label}"
                    invitation_id = (
                        await connection.execute(
                            text(
                                """
                                INSERT INTO invitations (
                                    account_name, mobile_number, token, is_used, role,
                                    created_by_id, expires_at, short_code, created_at
                                ) VALUES (
                                    :account, :mobile, :token, true, 'STANDARD',
                                    :owner_id, :expires_at, :short_code, :created_at
                                ) RETURNING id
                                """
                            ),
                            {
                                "account": account,
                                "mobile": mobile,
                                "token": token,
                                "owner_id": owner_id,
                                "expires_at": expires_at.replace(tzinfo=None),
                                "short_code": f"{index}{label[:7]}",
                                "created_at": created_at,
                            },
                        )
                    ).scalar_one()
                    seeded[case_name] = (invitation_id, user_id)

                    if case["relation"] == "accountant":
                        await connection.execute(
                            text(
                                """
                                INSERT INTO accountant_relations (
                                    owner_user_id, accountant_user_id, created_by_user_id,
                                    invitation_token, global_account_name, relation_display_name,
                                    mobile_number, status, expires_at, activated_at, created_at
                                ) VALUES (
                                    :owner_id, :user_id, :owner_id, :token, :account,
                                    :display_name, :mobile, :status, :relation_expires,
                                    :activated_at, :created_at
                                )
                                """
                            ),
                            {
                                "owner_id": owner_id,
                                "user_id": user_id,
                                "token": token,
                                "account": account,
                                "display_name": f"accountant-{label}",
                                "mobile": mobile,
                                "status": case["relation_status"],
                                "relation_expires": expires_at + timedelta(days=1),
                                "activated_at": case["activation"],
                                "created_at": created_at,
                            },
                        )
                    elif case["relation"] == "customer":
                        await connection.execute(
                            text(
                                """
                                INSERT INTO customer_relations (
                                    owner_user_id, customer_user_id, created_by_user_id,
                                    invitation_token, management_name, customer_tier, status,
                                    expires_at, activated_at, created_at
                                ) VALUES (
                                    :owner_id, :user_id, :owner_id, :token, :management_name,
                                    'tier1', :status, :relation_expires, :activated_at, :created_at
                                )
                                """
                            ),
                            {
                                "owner_id": owner_id,
                                "user_id": user_id,
                                "token": token,
                                "management_name": f"{case_name}-{label}",
                                "status": case["relation_status"],
                                "relation_expires": expires_at + timedelta(days=1),
                                "activated_at": case["activation"],
                                "created_at": created_at,
                            },
                        )

            await engine.dispose()
            _run_alembic(sync_url, "upgrade", "a8d9e0f1b2c3")
            engine = create_async_engine(async_url, pool_pre_ping=True)
            async with engine.connect() as connection:
                for case_name, (invitation_id, expected_user_id) in seeded.items():
                    row = (
                        await connection.execute(
                            text(
                                """
                                SELECT registered_user_id, completed_at, completed_via::text
                                FROM invitations WHERE id = :invitation_id
                                """
                            ),
                            {"invitation_id": invitation_id},
                        )
                    ).one()
                    with self.subTest(case=case_name):
                        if cases[case_name]["should_complete"]:
                            self.assertEqual(row.registered_user_id, expected_user_id)
                            self.assertIsNotNone(row.completed_at)
                            self.assertEqual(row.completed_via, "web")
                        else:
                            self.assertIsNone(row.registered_user_id)
                            self.assertIsNone(row.completed_at)
                            self.assertIsNone(row.completed_via)
        finally:
            await engine.dispose()
            _run_alembic(sync_url, "downgrade", "f7c8d9e0a1b2")

    async def test_canonical_user_collision_aborts_without_partial_schema(self):
        sync_url, async_url = MIGRATION_DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "f7c8d9e0a1b2")
        engine = create_async_engine(async_url, pool_pre_ping=True)
        label = uuid4().hex[:10]
        accounts = [
            f"\tCanonical_{label}_۱۲۳\u00a0",
            f"canonical_{label}_123",
            f"mobile_a_{label}",
            f"mobile_b_{label}",
        ]
        canonical_mobile = f"091{(int(label[:8], 16) + 77) % 100000000:08d}"
        persian_mobile = canonical_mobile.translate(
            str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
        )
        mobiles = [
            f"090{int(label[:7], 16) % 100000000:08d}",
            f"092{int(label[:7], 16) % 100000000:08d}",
            f"\u2007{persian_mobile}\u202f",
            canonical_mobile,
        ]
        try:
            async with engine.begin() as connection:
                for index, (account, mobile) in enumerate(zip(accounts, mobiles, strict=True)):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO users (
                                account_name, mobile_number, full_name, address, role,
                                has_bot_access, is_deleted, must_change_password, home_server
                            ) VALUES (
                                :account, :mobile, :full_name, 'Canonical migration address',
                                'STANDARD', true, false, false, 'iran'
                            )
                            """
                        ),
                        {
                            "account": account,
                            "mobile": mobile,
                            "full_name": f"canonical collision {index}",
                        },
                    )

            result = _run_alembic_result(sync_url, "upgrade", "a8d9e0f1b2c3")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "canonical User identity audit found 2 collision groups",
                f"{result.stdout}\n{result.stderr}",
            )
            async with engine.connect() as connection:
                revision = (
                    await connection.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
                self.assertEqual(revision, "f7c8d9e0a1b2")
                generated_column_count = int(
                    (
                        await connection.execute(
                            text(
                                """
                                SELECT COUNT(*)
                                FROM information_schema.columns
                                WHERE table_schema = current_schema()
                                  AND table_name = 'users'
                                  AND column_name IN (
                                      'normalized_account_name',
                                      'normalized_mobile_number'
                                  )
                                """
                            )
                        )
                    ).scalar_one()
                )
                self.assertEqual(generated_column_count, 0)
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM users WHERE account_name = ANY(:accounts)"),
                    {"accounts": accounts},
                )
            await engine.dispose()

    async def test_generated_identity_matches_versioned_python_contract(self):
        sync_url, async_url = MIGRATION_DATABASE_URLS
        _run_alembic(sync_url, "upgrade", "f7c8d9e0a1b2")
        engine = create_async_engine(async_url, pool_pre_ping=True)
        label = uuid4().hex[:8]
        cases = (
            (f"\tProject_{label}_۱۲۳\u00a0", f"\u20070912{int(label, 16) % 10_000_000:07d}\u202f"),
            (f"\nÄCCOUNT_{label}\r", f"\u30000913{int(label, 16) % 10_000_000:07d}\u1680"),
            (f"\u205fPlain_{label}\u3000", f"\u00850914{int(label, 16) % 10_000_000:07d}\u00a0"),
        )
        try:
            async with engine.begin() as connection:
                for index, (account, mobile) in enumerate(cases):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO users (
                                account_name, mobile_number, full_name, address, role,
                                has_bot_access, is_deleted, must_change_password, home_server
                            ) VALUES (
                                :account, :mobile, :full_name, 'Canonical differential address',
                                'STANDARD', true, false, false, 'iran'
                            )
                            """
                        ),
                        {
                            "account": account,
                            "mobile": mobile,
                            "full_name": f"canonical differential {index}",
                        },
                    )

            await engine.dispose()
            _run_alembic(sync_url, "upgrade", "a8d9e0f1b2c3")
            engine = create_async_engine(async_url, pool_pre_ping=True)
            async with engine.connect() as connection:
                rows = (
                    await connection.execute(
                        text(
                            """
                            SELECT account_name, mobile_number,
                                   normalized_account_name, normalized_mobile_number
                            FROM users
                            WHERE account_name = ANY(:accounts)
                            ORDER BY account_name
                            """
                        ),
                        {"accounts": [account for account, _mobile in cases]},
                    )
                ).all()
            self.assertEqual(len(rows), len(cases))
            for row in rows:
                with self.subTest(account=repr(row.account_name)):
                    self.assertEqual(
                        row.normalized_account_name,
                        normalize_account_name(row.account_name),
                    )
                    self.assertEqual(
                        row.normalized_mobile_number,
                        normalize_mobile_number(row.mobile_number),
                    )
        finally:
            await engine.dispose()
            _run_alembic(sync_url, "downgrade", "f7c8d9e0a1b2")


if __name__ == "__main__":
    unittest.main()
