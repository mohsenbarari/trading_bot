"""Characterization tests for real api.routers.auth.issue_telegram_link_token handler."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Install fail-closed network fence before importing the router to prevent live network/Redis calls.
install_p1_00_network_fence()

from fastapi import HTTPException, status  # noqa: E402

from api.routers.auth import (  # noqa: E402
    TelegramLinkTokenResponse,
    issue_telegram_link_token,
    settings as auth_settings,
)
from core.services.bot_access_policy import (  # noqa: E402
    BOT_ACCESS_REASON_INACTIVE,
    BotAccessDecision,
    bot_access_denial_message,
)
from core.services.telegram_link_token_service import (  # noqa: E402
    TELEGRAM_LINK_TOKEN_TTL_SECONDS,
    TelegramLinkTokenError,
    TelegramLinkTokenIssueResult,
    build_telegram_deep_link,
    build_telegram_start_parameter,
    hash_telegram_link_token,
)
from models.telegram_link_token import TelegramLinkToken, TelegramLinkTokenStatus  # noqa: E402
from models.user import User, UserRole  # noqa: E402


class DatabaseStandIn:
    """Strict in-memory database stand-in with AsyncMock transaction methods."""

    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()


class UserStandIn:
    """Strict in-memory user stand-in with authoritative UserRole."""

    def __init__(
        self,
        *,
        id: int = 42,
        telegram_id: int | None = None,
        role: UserRole = UserRole.STANDARD,
        account_name: str = "characterization_user",
        mobile_number: str = "09121234567",
    ) -> None:
        self.id = id
        self.telegram_id = telegram_id
        self.role = role
        self.account_name = account_name
        self.mobile_number = mobile_number


class TestIssueTelegramLinkTokenCharacterization(unittest.IsolatedAsyncioTestCase):
    """Characterize prerequisite rejection, already-linked, and success paths."""

    async def test_prerequisite_bot_username_normalizes_to_empty_raises_503(self) -> None:
        """Assert empty/blank/at-only bot_username raises HTTP 503 without invoking services or transactions."""
        blank_username_samples = [None, "", "   ", "@", "  @  ", "@@@"]
        for candidate in blank_username_samples:
            with self.subTest(bot_username=candidate):
                db = DatabaseStandIn()
                user = UserStandIn(id=42, telegram_id=None, role=UserRole.STANDARD)

                with (
                    patch.object(auth_settings, "bot_username", candidate),
                    patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock) as mock_evaluate,
                    patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock) as mock_create,
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        await issue_telegram_link_token(current_user=user, db=db)

                    self.assertEqual(ctx.exception.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
                    self.assertEqual(ctx.exception.detail, "نام کاربری ربات تنظیم نشده است")
                    mock_evaluate.assert_not_awaited()
                    mock_create.assert_not_awaited()
                    db.commit.assert_not_awaited()
                    db.rollback.assert_not_awaited()

    async def test_prerequisite_bot_access_denied_inactive_raises_403(self) -> None:
        """Assert inactive bot-access denial raises HTTP 403 with exact detail and no token creation or transaction."""
        db = DatabaseStandIn()
        user = UserStandIn(id=42, telegram_id=None, role=UserRole.STANDARD)
        denied_decision = BotAccessDecision(allowed=False, reason=BOT_ACCESS_REASON_INACTIVE)

        with (
            patch.object(auth_settings, "bot_username", "@official_bot"),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock, return_value=denied_decision) as mock_evaluate,
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock) as mock_create,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await issue_telegram_link_token(current_user=user, db=db)

            self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
            expected_detail = bot_access_denial_message(BOT_ACCESS_REASON_INACTIVE)
            self.assertEqual(ctx.exception.detail, expected_detail)
            mock_evaluate.assert_awaited_once_with(db, user)
            mock_create.assert_not_awaited()
            db.commit.assert_not_awaited()
            db.rollback.assert_not_awaited()

    async def test_prerequisite_ordering_empty_username_precedes_bot_access_and_link(self) -> None:
        """Assert unconfigured bot_username fails 503 before bot access check even if user has telegram_id and denial."""
        db = DatabaseStandIn()
        user = UserStandIn(id=42, telegram_id=987654321, role=UserRole.STANDARD)

        with (
            patch.object(auth_settings, "bot_username", "   "),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock) as mock_evaluate,
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock) as mock_create,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await issue_telegram_link_token(current_user=user, db=db)

            self.assertEqual(ctx.exception.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
            self.assertEqual(ctx.exception.detail, "نام کاربری ربات تنظیم نشده است")
            mock_evaluate.assert_not_awaited()
            mock_create.assert_not_awaited()
            db.commit.assert_not_awaited()
            db.rollback.assert_not_awaited()

    async def test_prerequisite_ordering_bot_access_denial_precedes_already_linked_check(self) -> None:
        """Assert bot access rejection occurs before already-linked response when user has telegram_id."""
        db = DatabaseStandIn()
        user = UserStandIn(id=42, telegram_id=987654321, role=UserRole.STANDARD)
        denied_decision = BotAccessDecision(allowed=False, reason=BOT_ACCESS_REASON_INACTIVE)

        with (
            patch.object(auth_settings, "bot_username", "test_bot"),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock, return_value=denied_decision) as mock_evaluate,
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock) as mock_create,
        ):
            with self.assertRaises(HTTPException) as ctx:
                await issue_telegram_link_token(current_user=user, db=db)

            self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(ctx.exception.detail, bot_access_denial_message(BOT_ACCESS_REASON_INACTIVE))
            mock_evaluate.assert_awaited_once_with(db, user)
            mock_create.assert_not_awaited()
            db.commit.assert_not_awaited()
            db.rollback.assert_not_awaited()

    async def test_already_linked_user_characterization(self) -> None:
        """Assert already-linked user returns complete TelegramLinkTokenResponse without creating token or mutating db."""
        db = DatabaseStandIn()
        user = UserStandIn(id=42, telegram_id=777888999, role=UserRole.STANDARD)
        allowed_decision = BotAccessDecision(allowed=True)

        with (
            patch.object(auth_settings, "bot_username", "  @my_linked_bot  "),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock, return_value=allowed_decision) as mock_evaluate,
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock) as mock_create,
        ):
            response = await issue_telegram_link_token(current_user=user, db=db)

            mock_evaluate.assert_awaited_once_with(db, user)
            mock_create.assert_not_awaited()
            db.commit.assert_not_awaited()
            db.rollback.assert_not_awaited()

            self.assertIsInstance(response, TelegramLinkTokenResponse)
            self.assertIs(response.telegram_linked, True)
            self.assertIs(response.can_connect_telegram, False)
            self.assertEqual(response.bot_username, "my_linked_bot")
            self.assertEqual(response.detail, "این حساب قبلاً به تلگرام متصل شده است")
            self.assertIsNone(response.telegram_url)
            self.assertIsNone(response.start_parameter)
            self.assertIsNone(response.expires_at)
            self.assertIsNone(response.expires_in)

    async def test_eligible_unlinked_user_successful_issuance(self) -> None:
        """Assert unlinked eligible user creates token, commits db, and populates all response fields exactly."""
        db = DatabaseStandIn()
        user = UserStandIn(id=42, telegram_id=None, role=UserRole.STANDARD)
        allowed_decision = BotAccessDecision(allowed=True)

        fixed_expires_at = datetime(2026, 9, 4, 18, 30, 0, tzinfo=timezone.utc)
        sample_token = "char_token_value_abc123_xyz789"
        sample_hash = hash_telegram_link_token(sample_token)

        record = TelegramLinkToken(
            id=123,
            user_id=42,
            token_hash=sample_hash,
            status=TelegramLinkTokenStatus.PENDING,
            issued_by_server="iran",
            expires_at=fixed_expires_at,
        )
        issue_result = TelegramLinkTokenIssueResult(
            token=sample_token,
            token_hash=sample_hash,
            record=record,
        )

        with (
            patch.object(auth_settings, "bot_username", " @market_exchange_bot "),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock, return_value=allowed_decision) as mock_evaluate,
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock, return_value=issue_result) as mock_create,
        ):
            response = await issue_telegram_link_token(current_user=user, db=db)

            mock_evaluate.assert_awaited_once_with(db, user)
            mock_create.assert_awaited_once_with(
                db,
                user,
                ttl_seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS,
            )
            db.commit.assert_awaited_once()
            db.rollback.assert_not_awaited()

            self.assertIsInstance(response, TelegramLinkTokenResponse)
            self.assertIs(response.telegram_linked, False)
            self.assertIs(response.can_connect_telegram, True)
            self.assertEqual(response.bot_username, "market_exchange_bot")

            expected_start = build_telegram_start_parameter(sample_token)
            self.assertEqual(expected_start, f"link_{sample_token}")
            self.assertEqual(response.start_parameter, expected_start)

            expected_url = build_telegram_deep_link("market_exchange_bot", sample_token)
            self.assertEqual(expected_url, f"https://t.me/market_exchange_bot?start={expected_start}")
            self.assertEqual(response.telegram_url, expected_url)

            self.assertEqual(response.expires_at, fixed_expires_at)
            self.assertEqual(response.expires_in, TELEGRAM_LINK_TOKEN_TTL_SECONDS)
            self.assertEqual(response.expires_in, 600)
            self.assertIsNone(response.detail)

    async def test_token_creation_service_error_rolls_back_and_raises_403(self) -> None:
        """Assert TelegramLinkTokenError during creation rolls back transaction and re-raises as HTTP 403."""
        db = DatabaseStandIn()
        user = UserStandIn(id=42, telegram_id=None, role=UserRole.STANDARD)
        allowed_decision = BotAccessDecision(allowed=True)

        with (
            patch.object(auth_settings, "bot_username", "test_bot"),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock, return_value=allowed_decision),
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock, side_effect=TelegramLinkTokenError(BOT_ACCESS_REASON_INACTIVE)),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await issue_telegram_link_token(current_user=user, db=db)

            self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(ctx.exception.detail, bot_access_denial_message(BOT_ACCESS_REASON_INACTIVE))
            db.rollback.assert_awaited_once()
            db.commit.assert_not_awaited()

    async def test_token_creation_generic_error_rolls_back_and_raises_500(self) -> None:
        """Assert generic exception during creation rolls back transaction and raises HTTP 500."""
        db = DatabaseStandIn()
        user = UserStandIn(id=42, telegram_id=None, role=UserRole.STANDARD)
        allowed_decision = BotAccessDecision(allowed=True)

        with (
            patch.object(auth_settings, "bot_username", "test_bot"),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock, return_value=allowed_decision),
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock, side_effect=RuntimeError("unexpected database fault")),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await issue_telegram_link_token(current_user=user, db=db)

            self.assertEqual(ctx.exception.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertEqual(ctx.exception.detail, "خطا در ساخت لینک اتصال تلگرام")
            db.rollback.assert_awaited_once()
            db.commit.assert_not_awaited()

    async def test_real_user_model_instance_compatibility(self) -> None:
        """Assert in-memory User SQLAlchemy model instance works identically with handler."""
        db = DatabaseStandIn()
        real_user = User(
            id=99,
            telegram_id=None,
            role=UserRole.STANDARD,
            account_name="real_user_stand_in",
            mobile_number="09129999999",
        )
        allowed_decision = BotAccessDecision(allowed=True)
        fixed_expires_at = datetime(2026, 9, 4, 20, 0, 0, tzinfo=timezone.utc)
        sample_token = "token_real_user_123"
        sample_hash = hash_telegram_link_token(sample_token)

        record = TelegramLinkToken(
            id=200,
            user_id=99,
            token_hash=sample_hash,
            status=TelegramLinkTokenStatus.PENDING,
            issued_by_server="iran",
            expires_at=fixed_expires_at,
        )
        issue_result = TelegramLinkTokenIssueResult(
            token=sample_token,
            token_hash=sample_hash,
            record=record,
        )

        with (
            patch.object(auth_settings, "bot_username", "@real_user_bot"),
            patch("api.routers.auth.evaluate_bot_access", new_callable=AsyncMock, return_value=allowed_decision) as mock_evaluate,
            patch("api.routers.auth.create_telegram_link_token", new_callable=AsyncMock, return_value=issue_result) as mock_create,
        ):
            response = await issue_telegram_link_token(current_user=real_user, db=db)

            mock_evaluate.assert_awaited_once_with(db, real_user)
            mock_create.assert_awaited_once_with(
                db,
                real_user,
                ttl_seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS,
            )
            db.commit.assert_awaited_once()
            db.rollback.assert_not_awaited()

            self.assertIsInstance(response, TelegramLinkTokenResponse)
            self.assertIs(response.telegram_linked, False)
            self.assertIs(response.can_connect_telegram, True)
            self.assertEqual(response.bot_username, "real_user_bot")
            self.assertEqual(response.expires_at, fixed_expires_at)
            self.assertEqual(response.expires_in, TELEGRAM_LINK_TOKEN_TTL_SECONDS)
            self.assertIsNone(response.detail)


if __name__ == "__main__":
    unittest.main()
