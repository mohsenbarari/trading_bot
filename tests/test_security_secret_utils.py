import unittest

from core.security import constant_time_secret_equals


class SecuritySecretUtilsTests(unittest.TestCase):
    def test_constant_time_secret_equals_accepts_exact_match(self):
        self.assertTrue(constant_time_secret_equals("secret-value", "secret-value"))

    def test_constant_time_secret_equals_rejects_missing_or_different_values(self):
        self.assertFalse(constant_time_secret_equals(None, "secret-value"))
        self.assertFalse(constant_time_secret_equals("", "secret-value"))
        self.assertFalse(constant_time_secret_equals("secret-value", None))
        self.assertFalse(constant_time_secret_equals("secret-value", "other-value"))


if __name__ == "__main__":
    unittest.main()
