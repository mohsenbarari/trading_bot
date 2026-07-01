import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import ProfileTradePdfCallback
from bot.utils.public_profile import (
    _load_active_accountants_for_owner,
    build_bot_public_profile_keyboard,
    build_bot_public_profile_text,
    load_bot_public_profile,
    BotPublicProfile,
)
from models.user import UserRole


class FakeScalarResult:
    def __init__(self, values=None):
        self.values = list(values or [])

    def scalars(self):
        values = list(self.values)
        return SimpleNamespace(all=lambda: values)


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, stmt):
        if self.execute_results:
            return self.execute_results.pop(0)
        return FakeScalarResult()


class BotPublicProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_active_accountants_uses_owner_registered_relation_name(self):
        relation = SimpleNamespace(accountant_user_id=7, relation_display_name="نام ثبت‌شده سرگروه")
        accountant_user = SimpleNamespace(
            id=7,
            is_deleted=False,
            account_name="نام عمومی حسابدار",
            full_name="Public Accountant",
            mobile_number="09120000007",
        )
        db = FakeDB(
            execute_results=[
                FakeScalarResult([relation]),
                FakeScalarResult([accountant_user]),
            ]
        )

        accountants = await _load_active_accountants_for_owner(db, 2)

        self.assertEqual(len(accountants), 1)
        self.assertEqual(accountants[0].display_name, "نام ثبت‌شده سرگروه")
        self.assertEqual(accountants[0].mobile_number, "09120000007")

    async def test_load_bot_public_profile_requires_logged_in_viewer(self):
        self.assertIsNone(await load_bot_public_profile(FakeDB(), viewer=None, target_user_id=5))

    async def test_unrelated_viewer_cannot_open_customer_profile(self):
        target = SimpleNamespace(id=5, is_deleted=False, account_name="customer")
        viewer = SimpleNamespace(id=2, is_deleted=False, role=UserRole.STANDARD)
        target_relation = SimpleNamespace(owner_user_id=10, management_name="نام مشتری")

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "bot.utils.public_profile.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=target_relation),
        ), patch(
            "bot.utils.public_profile.attach_customer_management_names",
            new=AsyncMock(),
        ) as attach_mock:
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=5)

        self.assertIsNone(profile)
        attach_mock.assert_not_awaited()

    async def test_customer_viewer_cannot_open_unrelated_profile(self):
        target = SimpleNamespace(id=7, is_deleted=False, account_name="unrelated")
        viewer = SimpleNamespace(id=5, is_deleted=False, role=UserRole.STANDARD)
        viewer_relation = SimpleNamespace(owner_user_id=10, management_name="نام مشتری")

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "bot.utils.public_profile.get_active_customer_relation_for_customer",
            new=AsyncMock(side_effect=[None, viewer_relation]),
        ), patch(
            "bot.utils.public_profile.build_allowed_customer_chat_targets",
            new=AsyncMock(return_value=[10, 11]),
        ), patch(
            "bot.utils.public_profile.attach_customer_management_names",
            new=AsyncMock(),
        ) as attach_mock:
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=7)

        self.assertIsNone(profile)
        attach_mock.assert_not_awaited()

    async def test_customer_owner_can_open_customer_profile_with_management_name(self):
        target = SimpleNamespace(id=5, is_deleted=False, account_name="customer_public")
        viewer = SimpleNamespace(id=10, is_deleted=False, role=UserRole.STANDARD)
        target_relation = SimpleNamespace(owner_user_id=10, management_name="نام ثبت‌شده مشتری")

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "bot.utils.public_profile.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=target_relation),
        ), patch(
            "bot.utils.public_profile.attach_customer_management_names",
            new=AsyncMock(),
        ):
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=5)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.display_name, "نام ثبت‌شده مشتری")
        self.assertEqual(profile.accountants, ())

    async def test_owner_accountant_can_open_customer_profile(self):
        target = SimpleNamespace(id=5, is_deleted=False, account_name="customer_public")
        viewer = SimpleNamespace(id=20, is_deleted=False, role=UserRole.STANDARD)
        target_relation = SimpleNamespace(owner_user_id=10, management_name="مشتری مجاز")
        viewer_accountant_relation = SimpleNamespace(owner_user_id=10)

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, viewer_accountant_relation]),
        ), patch(
            "bot.utils.public_profile.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=target_relation),
        ), patch(
            "bot.utils.public_profile.attach_customer_management_names",
            new=AsyncMock(),
        ):
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=5)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.display_name, "مشتری مجاز")

    async def test_superadmin_can_open_customer_profile(self):
        target = SimpleNamespace(id=5, is_deleted=False, account_name="customer_public")
        viewer = SimpleNamespace(id=99, is_deleted=False, role=UserRole.SUPER_ADMIN)
        target_relation = SimpleNamespace(owner_user_id=10, management_name="مشتری")

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.utils.public_profile.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=target_relation),
        ), patch(
            "bot.utils.public_profile.attach_customer_management_names",
            new=AsyncMock(),
        ):
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=5)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.display_name, "مشتری")

    async def test_accountant_profile_target_redirects_to_owner_profile(self):
        accountant_target = SimpleNamespace(id=20, is_deleted=False, account_name="accountant_public")
        owner = SimpleNamespace(id=10, is_deleted=False, account_name="owner_account")
        viewer = SimpleNamespace(id=2, is_deleted=False, role=UserRole.STANDARD)
        target_accountant_relation = SimpleNamespace(owner_user_id=10, owner_user=owner)

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=accountant_target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[target_accountant_relation, None]),
        ), patch(
            "bot.utils.public_profile.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.utils.public_profile.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.utils.public_profile._load_active_accountants_for_owner",
            new=AsyncMock(return_value=()),
        ):
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=20)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.target_user.id, 10)
        self.assertEqual(profile.display_name, "owner_account")

    async def test_deleted_target_and_deleted_accountant_user_are_excluded(self):
        deleted_target = SimpleNamespace(id=5, is_deleted=True, account_name="deleted")
        viewer = SimpleNamespace(id=2, is_deleted=False, role=UserRole.STANDARD)

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=deleted_target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(),
        ) as accountant_relation_mock:
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=5)

        self.assertIsNone(profile)
        accountant_relation_mock.assert_not_awaited()

        relation = SimpleNamespace(accountant_user_id=7, relation_display_name="حسابدار حذف‌شده")
        accountant_user = SimpleNamespace(
            id=7,
            is_deleted=True,
            account_name="deleted_accountant",
            mobile_number="09120000007",
        )
        db = FakeDB(
            execute_results=[
                FakeScalarResult([relation]),
                FakeScalarResult([accountant_user]),
            ]
        )

        self.assertEqual(await _load_active_accountants_for_owner(db, 2), ())

    async def test_load_bot_public_profile_allows_normal_user_and_keeps_accountant_names(self):
        target = SimpleNamespace(id=5, is_deleted=False, account_name="target", full_name="Target")
        viewer = SimpleNamespace(id=2, is_deleted=False, role=UserRole.STANDARD)
        accountant = SimpleNamespace(display_name="حسابدار ثبت‌شده", mobile_number="0912")

        with patch("bot.utils.public_profile._load_user", new=AsyncMock(return_value=target)), patch(
            "bot.utils.public_profile.get_active_accountant_relation_for_accountant",
            new=AsyncMock(side_effect=[None, None]),
        ), patch(
            "bot.utils.public_profile.get_active_customer_relation_for_customer",
            new=AsyncMock(return_value=None),
        ), patch(
            "bot.utils.public_profile.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.utils.public_profile._load_active_accountants_for_owner",
            new=AsyncMock(return_value=(accountant,)),
        ):
            profile = await load_bot_public_profile(FakeDB(), viewer=viewer, target_user_id=5)

        self.assertIsNotNone(profile)
        self.assertEqual(profile.display_name, "target")
        self.assertEqual(profile.accountants[0].display_name, "حسابدار ثبت‌شده")

    def test_profile_text_and_keyboard_are_minimal_and_use_three_month_pdf_callback(self):
        profile = BotPublicProfile(
            target_user=SimpleNamespace(id=5, mobile_number="0912", address="تهران"),
            display_name="target",
            accountants=(SimpleNamespace(display_name="حسابدار ثبت‌شده", mobile_number="0913"),),
        )

        text = build_bot_public_profile_text(profile)
        keyboard = build_bot_public_profile_keyboard(profile)

        self.assertIn("🔸 نام: target", text)
        self.assertIn("📞 شماره تماس: 0912", text)
        self.assertIn("📍 آدرس: تهران", text)
        self.assertIn("حسابدار ثبت‌شده - 0913", text)
        callback_data = keyboard.inline_keyboard[0][0].callback_data
        self.assertEqual(callback_data, ProfileTradePdfCallback(target_user_id=5).pack())


if __name__ == "__main__":
    unittest.main()
