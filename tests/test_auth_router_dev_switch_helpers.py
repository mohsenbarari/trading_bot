import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from jose import jwt

from api.routers import auth
from models.customer_relation import CustomerTier


def _make_request(host: str = "1.2.3.4", headers: dict | None = None):
    return SimpleNamespace(
        client=SimpleNamespace(host=host),
        headers=headers or {},
    )


class AuthRouterDevSwitchHelperTests(unittest.TestCase):
    def test_extract_request_real_ip_prefers_forwarded_header(self):
        request = _make_request(host="10.0.0.1", headers={"x-forwarded-for": "8.8.8.8, 10.0.0.1"})

        self.assertEqual(auth._extract_request_real_ip(request), "8.8.8.8")
        self.assertEqual(auth._extract_request_real_ip(_make_request(host="10.0.0.2")), "10.0.0.2")

    def test_is_local_dev_request_recognizes_private_ranges(self):
        self.assertTrue(auth._is_local_dev_request(_make_request(host="127.0.0.1")))
        self.assertTrue(auth._is_local_dev_request(_make_request(host="192.168.1.10")))
        self.assertTrue(auth._is_local_dev_request(_make_request(host="172.16.0.9")))
        self.assertTrue(auth._is_local_dev_request(_make_request(host="10.1.2.3")))
        self.assertFalse(auth._is_local_dev_request(_make_request(host="8.8.8.8")))

    def test_has_test_switch_claim_parses_valid_tokens_and_rejects_invalid_ones(self):
        token = jwt.encode(
            {auth.TEST_ACCOUNT_SWITCH_CLAIM: True},
            auth.settings.jwt_secret_key,
            algorithm=auth.settings.jwt_algorithm,
        )

        self.assertTrue(auth._has_test_switch_claim(token))
        self.assertFalse(auth._has_test_switch_claim(None))
        self.assertFalse(auth._has_test_switch_claim("bad-token"))

    def test_serialize_test_switch_user_option_preserves_customer_flags(self):
        user = SimpleNamespace(
            id=7,
            full_name="Dev User",
            account_name="dev_user",
            mobile_number="09120000000",
            role=auth.UserRole.SUPER_ADMIN,
        )

        option = auth._serialize_test_switch_user_option(
            user,
            is_accountant=True,
            is_customer=True,
            customer_tier=CustomerTier.TIER_2,
        )

        self.assertEqual(option.id, 7)
        self.assertEqual(option.account_name, "dev_user")
        self.assertTrue(option.is_accountant)
        self.assertTrue(option.is_customer)
        self.assertEqual(option.customer_tier, CustomerTier.TIER_2)

    def test_assert_test_switch_access_allows_dev_key_local_ip_super_admin_and_switch_claim(self):
        public_request = _make_request(host="8.8.8.8")
        local_request = _make_request(host="127.0.0.1")
        switch_token = jwt.encode(
            {auth.TEST_ACCOUNT_SWITCH_CLAIM: True},
            auth.settings.jwt_secret_key,
            algorithm=auth.settings.jwt_algorithm,
        )

        auth._assert_test_switch_access(public_request, current_user=None, token=None, dev_key=auth.settings.dev_api_key)
        auth._assert_test_switch_access(local_request, current_user=None, token=None, dev_key=None)
        auth._assert_test_switch_access(
            public_request,
            current_user=SimpleNamespace(role=auth.UserRole.SUPER_ADMIN),
            token=None,
            dev_key=None,
        )
        auth._assert_test_switch_access(public_request, current_user=None, token=switch_token, dev_key=None)

    def test_assert_test_switch_access_rejects_unauthorized_public_requests(self):
        with self.assertRaises(HTTPException) as exc_info:
            auth._assert_test_switch_access(
                _make_request(host="8.8.8.8"),
                current_user=SimpleNamespace(role=auth.UserRole.STANDARD),
                token=None,
                dev_key=None,
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "سوییچ موقت حساب فقط در محیط توسعه یا نشست‌های سوییچ‌شده مجاز است",
        )


if __name__ == "__main__":
    unittest.main()