import unittest
from unittest.mock import patch

import httpx

from core import sms
from scripts.smsir_verify_probe import build_verify_payload, endpoint_for_style


def smsir_response(status_code=200, payload=None):
    return httpx.Response(
        status_code,
        json=payload if payload is not None else {"status": 1, "message": "موفق", "data": {}},
        request=httpx.Request("POST", "https://api.sms.ir/v1/send/bulk"),
    )


class CoreSmsTests(unittest.TestCase):
    def configured_smsir(self):
        return (
            patch.object(sms.settings, "smsir_api_key", "api-key"),
            patch.object(sms.settings, "smsir_line_number", 30002108016422),
            patch.object(sms.settings, "smsir_base_url", "https://api.sms.ir"),
            patch.object(sms.settings, "smsir_timeout_seconds", 10.0),
        )

    def test_smsir_headers_requires_configured_api_key(self):
        with patch.object(sms.settings, "smsir_api_key", None):
            with self.assertRaises(RuntimeError):
                sms._smsir_headers()

    def test_send_sms_posts_bulk_payload_and_returns_true(self):
        response = smsir_response(
            payload={
                "status": 1,
                "message": "موفق",
                "data": {"packId": "pack-id", "messageIds": [86522023], "cost": 1.0},
            }
        )
        patches = self.configured_smsir()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "core.sms.httpx.post", return_value=response
        ) as post_mock:
            self.assertTrue(sms.send_sms("۰۹۱۲۰۰۰۰۰۰۰", "hello"))

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0], "https://api.sms.ir/v1/send/bulk")
        self.assertEqual(post_mock.call_args.kwargs["headers"]["X-API-KEY"], "api-key")
        self.assertEqual(
            post_mock.call_args.kwargs["json"],
            {
                "lineNumber": 30002108016422,
                "messageText": "hello",
                "mobiles": ["09120000000"],
            },
        )

    def test_send_sms_returns_false_when_line_number_is_missing(self):
        with patch.object(sms.settings, "smsir_line_number", None), patch(
            "core.sms.httpx.post"
        ) as post_mock:
            self.assertFalse(sms.send_sms("09120000000", "hello"))

        post_mock.assert_not_called()

    def test_send_sms_returns_false_when_provider_rejects_message_id(self):
        response = smsir_response(
            payload={
                "status": 1,
                "message": "موفق",
                "data": {"packId": "pack-id", "messageIds": [0], "cost": 0.0},
            }
        )
        patches = self.configured_smsir()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "core.sms.httpx.post", return_value=response
        ):
            self.assertFalse(sms.send_sms("09120000000", "hello"))

    def test_send_sms_returns_false_when_provider_omits_message_ids(self):
        response = smsir_response(payload={"status": 1, "message": "موفق", "data": {}})
        patches = self.configured_smsir()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "core.sms.httpx.post", return_value=response
        ):
            self.assertFalse(sms.send_sms("09120000000", "hello"))

    def test_send_sms_returns_false_on_http_error(self):
        response = smsir_response(status_code=401, payload={"status": 0, "message": "unauthorized"})
        patches = self.configured_smsir()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "core.sms.httpx.post", return_value=response
        ):
            self.assertFalse(sms.send_sms("09120000000", "hello"))

    def test_send_sms_returns_false_on_request_exception(self):
        patches = self.configured_smsir()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "core.sms.httpx.post", side_effect=httpx.ConnectError("down")
        ):
            self.assertFalse(sms.send_sms("09120000000", "hello"))

    def test_send_otp_sms_uses_verify_template_when_configured(self):
        response = smsir_response(
            payload={"status": 1, "message": "موفق", "data": {"messageId": 89545112, "cost": 1.0}}
        )
        patches = self.configured_smsir()
        with patches[0], patches[1], patches[2], patches[3], patch.object(
            sms.settings, "smsir_otp_template_id", "123456"
        ), patch.object(sms.settings, "smsir_otp_template_parameter", "Code"), patch(
            "core.sms.httpx.post", return_value=response
        ) as post_mock:
            self.assertTrue(sms.send_otp_sms("+989120000000", "12345"))

        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.args[0], "https://api.sms.ir/v1/send/verify")
        self.assertEqual(
            post_mock.call_args.kwargs["json"],
            {
                "mobile": "09120000000",
                "templateId": 123456,
                "parameters": [{"name": "Code", "value": "12345"}],
            },
        )

    def test_send_otp_sms_falls_back_to_plain_sms_without_template(self):
        with patch.object(sms.settings, "smsir_otp_template_id", None), patch(
            "core.sms.send_sms", return_value=True
        ) as send_sms_mock:
            self.assertTrue(sms.send_otp_sms("09120000000", "12345"))

        args = send_sms_mock.call_args.args
        self.assertEqual(args[0], "09120000000")
        self.assertEqual(
            args[1],
            "کد تایید شما: 12345\nاین کد تا ۲ دقیقه معتبر است.\ncoin.gold-trade.ir/",
        )

    def test_send_otp_sms_returns_false_for_invalid_template_id(self):
        with patch.object(sms.settings, "smsir_otp_template_id", "abc"), patch(
            "core.sms.httpx.post"
        ) as post_mock:
            self.assertFalse(sms.send_otp_sms("09120000000", "12345"))

        post_mock.assert_not_called()

    def test_smsir_verify_probe_builds_package_style_payload(self):
        payload = build_verify_payload(
            mobile="۰۹۳۷۰۰۰۰۰۰۰",
            code="12345",
            template_id=232000,
            parameter_name="CODE",
            payload_style="package",
        )

        self.assertEqual(endpoint_for_style("package"), "v1/send/verify/")
        self.assertEqual(
            payload,
            {
                "Mobile": "09370000000",
                "TemplateId": 232000,
                "Parameters": [{"name": "CODE", "value": "12345"}],
            },
        )

    def test_smsir_verify_probe_builds_rest_style_payload(self):
        payload = build_verify_payload(
            mobile="+989370000000",
            code="12345",
            template_id=232000,
            parameter_name="CODE",
            payload_style="rest",
        )

        self.assertEqual(endpoint_for_style("rest"), "v1/send/verify")
        self.assertEqual(
            payload,
            {
                "mobile": "09370000000",
                "templateId": 232000,
                "parameters": [{"name": "CODE", "value": "12345"}],
            },
        )

    def test_send_invitation_sms_uses_template_parameters(self):
        with patch.object(sms.settings, "smsir_invitation_template_id", "657938"), patch.object(
            sms.settings, "smsir_invitation_template_parameter", "NAME"
        ), patch("core.sms._send_template_sms", return_value=True) as template_mock:
            self.assertTrue(
                sms.send_invitation_sms(
                    "09120000000",
                    "demo",
                    "https://t.me/demo",
                    "https://app.example/register",
                )
            )

        template_mock.assert_called_once_with(
            "09120000000",
            template_id=657938,
            parameters=[{"name": "NAME", "value": "demo"}],
        )

    def test_send_accountant_invitation_sms_uses_template_parameters(self):
        with patch.object(sms.settings, "smsir_accountant_invitation_template_id", "162103"), patch.object(
            sms.settings, "smsir_invitation_template_parameter", "NAME"
        ), patch("core.sms._send_template_sms", return_value=True) as template_mock:
            self.assertTrue(
                sms.send_accountant_invitation_sms(
                    "09120000000",
                    "accountant demo",
                    "https://app.example/accountant-register",
                )
            )

        template_mock.assert_called_once_with(
            "09120000000",
            template_id=162103,
            parameters=[{"name": "NAME", "value": "accountant demo"}],
        )

    def test_send_customer_invitation_sms_uses_template_parameters(self):
        with patch.object(sms.settings, "smsir_customer_invitation_template_id", "903643"), patch.object(
            sms.settings, "smsir_invitation_template_parameter", "NAME"
        ), patch("core.sms._send_template_sms", return_value=True) as template_mock:
            self.assertTrue(
                sms.send_customer_invitation_sms(
                    "09120000000",
                    "customer alias",
                    "https://app.example/customer-register",
                )
            )

        template_mock.assert_called_once_with(
            "09120000000",
            template_id=903643,
            parameters=[{"name": "NAME", "value": "customer alias"}],
        )


if __name__ == "__main__":
    unittest.main()
