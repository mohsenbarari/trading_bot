import unittest
from unittest.mock import patch

from core import sms


class CoreSmsTests(unittest.TestCase):
    def setUp(self):
        sms._sms_client = None

    def tearDown(self):
        sms._sms_client = None

    def test_get_client_requires_configured_api_key(self):
        with patch.object(sms.settings, 'smsir_api_key', None):
            with self.assertRaises(RuntimeError):
                sms._get_client()

    def test_get_client_caches_smsir_instance(self):
        with patch.object(sms.settings, 'smsir_api_key', 'key'), patch.object(
            sms.settings, 'smsir_line_number', 3000
        ), patch('core.sms.SmsIr', return_value=object()) as sms_ir:
            first = sms._get_client()
            second = sms._get_client()

        self.assertIs(first, second)
        sms_ir.assert_called_once_with(api_key='key', linenumber=3000)

    def test_send_sms_returns_true_in_outage_mode(self):
        self.assertTrue(sms.send_sms('09120000000', 'hello'))

    def test_send_otp_sms_formats_message_and_delegates(self):
        with patch('core.sms.send_sms', return_value=True) as send_sms_mock:
            self.assertTrue(sms.send_otp_sms('09120000000', '12345'))

        args = send_sms_mock.call_args.args
        self.assertEqual(args[0], '09120000000')
        self.assertIn('12345', args[1])

    def test_send_invitation_sms_formats_message_and_delegates(self):
        with patch('core.sms.send_sms', return_value=True) as send_sms_mock:
            self.assertTrue(
                sms.send_invitation_sms(
                    '09120000000',
                    'demo',
                    'https://t.me/demo',
                    'https://app.example/register',
                )
            )

        message = send_sms_mock.call_args.args[1]
        self.assertIn('demo', message)
        self.assertIn('https://t.me/demo', message)
        self.assertIn('https://app.example/register', message)


if __name__ == '__main__':
    unittest.main()