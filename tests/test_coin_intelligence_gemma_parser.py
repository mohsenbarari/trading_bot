from __future__ import annotations

import json
import unittest

from core.market_intelligence.gemma_parser import (
    MAX_PROJECT_PRICE,
    MAX_QUANTITY,
    _validated,
    infer_gemma_parser_candidate,
)


class GemmaParserCandidateTests(unittest.TestCase):
    def test_strict_normalized_candidate_is_accepted(self) -> None:
        payload = {
            "side": "BUY",
            "settlement": "CASH",
            "quantity": 10,
            "price": 183500,
            "commodity": "امام",
            "confidence": 0.91,
            "abstain": False,
            "reason_code": "EXPLICIT_FIELDS",
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps(
                                        payload,
                                        ensure_ascii=False,
                                    )
                                }
                            }
                        ]
                    },
                    ensure_ascii=False,
                ).encode()

        def opener(request, **_kwargs):
            request_body = json.loads(request.data.decode("utf-8"))
            self.assertIn(
                "خ ن 10تا 183500",
                request.data.decode("utf-8"),
            )
            self.assertEqual(
                request_body["chat_template_kwargs"],
                {"enable_thinking": False},
            )
            schema = request_body["response_format"]["json_schema"][
                "schema"
            ]
            self.assertFalse(schema["additionalProperties"])
            self.assertIn(
                "امام",
                schema["properties"]["commodity"]["enum"],
            )
            return Response()

        result = infer_gemma_parser_candidate(
            "خ ن 10تا 183500",
            endpoint=(
                "http://coin_intelligence_gemma_server:18123/"
                "v1/chat/completions"
            ),
            canonical_commodities=("امام", "بهار"),
            timeout_seconds=2,
            opener=opener,
        )

        self.assertEqual(result.commodity, "امام")
        self.assertFalse(result.abstain)

    def test_noncanonical_commodity_fails_closed(self) -> None:
        payload = {
            "side": "BUY",
            "settlement": "CASH",
            "quantity": 10,
            "price": 183500,
            "commodity": "ساختگی",
            "confidence": 0.91,
            "abstain": False,
            "reason_code": "GUESS",
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                content = json.dumps(payload, ensure_ascii=False)
                return json.dumps(
                    {"choices": [{"message": {"content": content}}]},
                    ensure_ascii=False,
                ).encode()

        with self.assertRaises(ValueError):
            infer_gemma_parser_candidate(
                "خ ن 10تا 183500",
                endpoint=(
                    "http://coin_intelligence_gemma_server:18123/"
                    "v1/chat/completions"
                ),
                canonical_commodities=("امام",),
                timeout_seconds=2,
                opener=lambda *_args, **_kwargs: Response(),
            )

    def test_external_endpoint_is_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            infer_gemma_parser_candidate(
                "خ ن 10تا 183500",
                endpoint="https://example.com/v1/chat/completions",
                canonical_commodities=("امام",),
                timeout_seconds=2,
            )

    def test_reasoning_without_content_fails_closed(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "",
                                    "reasoning_content": "synthetic thought",
                                }
                            }
                        ]
                    }
                ).encode()

        with self.assertRaises(ValueError):
            infer_gemma_parser_candidate(
                "خ ن 10تا 183500",
                endpoint=(
                    "http://coin_intelligence_gemma_server:18123/"
                    "v1/chat/completions"
                ),
                canonical_commodities=("امام",),
                timeout_seconds=2,
                opener=lambda *_args, **_kwargs: Response(),
            )

    def test_schema_values_are_independently_type_and_size_checked(
        self,
    ) -> None:
        baseline = {
            "side": "BUY",
            "settlement": "CASH",
            "quantity": 10,
            "price": 183500,
            "commodity": "امام",
            "confidence": 0.9,
            "abstain": False,
            "reason_code": "OK",
        }
        invalid_values = (
            ("confidence", True),
            ("confidence", "0.9"),
            ("reason_code", "دلیل"),
            ("reason_code", 7),
            ("quantity", MAX_QUANTITY + 1),
            ("price", MAX_PROJECT_PRICE + 1),
        )
        for field, value in invalid_values:
            with self.subTest(field=field, value=value):
                payload = dict(baseline)
                payload[field] = value
                with self.assertRaises(ValueError):
                    _validated(
                        payload,
                        canonical_commodities=("امام",),
                    )


if __name__ == "__main__":
    unittest.main()
