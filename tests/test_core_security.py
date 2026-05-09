from datetime import timedelta
import unittest

from jose import jwt

from core import security


class CoreSecurityTests(unittest.TestCase):
    def test_create_access_token_includes_expected_claims(self):
        token = security.create_access_token(subject=42, session_id='session-1', server_id='foreign')

        payload = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])

        self.assertEqual(payload['sub'], '42')
        self.assertEqual(payload['sid'], 'session-1')
        self.assertEqual(payload['srv'], 'foreign')
        self.assertEqual(payload['type'], 'access')
        self.assertIn('exp', payload)

    def test_create_access_token_supports_data_and_custom_expiry(self):
        token = security.create_access_token(
            data={'scope': 'admin'},
            expires_delta=timedelta(minutes=5),
        )

        payload = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])

        self.assertEqual(payload['scope'], 'admin')
        self.assertEqual(payload['type'], 'access')

    def test_create_refresh_token_includes_refresh_type(self):
        token = security.create_refresh_token(subject='demo-user', data={'kind': 'refresh-test'})

        payload = jwt.decode(token, security.SECRET_KEY, algorithms=[security.ALGORITHM])

        self.assertEqual(payload['sub'], 'demo-user')
        self.assertEqual(payload['kind'], 'refresh-test')
        self.assertEqual(payload['type'], 'refresh')

    def test_password_hash_and_verify_support_truncation_and_invalid_inputs(self):
        long_password = 'x' * 100
        hashed = security.get_password_hash(long_password)

        self.assertTrue(hashed)
        self.assertTrue(security.verify_password(long_password, hashed))
        self.assertFalse(security.verify_password('', hashed))
        self.assertFalse(security.verify_password(long_password, 'not-a-valid-hash'))
        self.assertEqual(security.get_password_hash(''), '')


if __name__ == '__main__':
    unittest.main()