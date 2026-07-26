"""Strict local-network Gemma second opinion for normalized offer parsing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen


GEMMA_PARSER_VERSION = "GEMMA_LOCAL_PARSER_SHADOW_20260726"
MAX_INPUT_CHARS = 500
MAX_HTTP_BODY_BYTES = 16 * 1024
MAX_CONTENT_CHARS = 4096
MAX_CANONICAL_COMMODITIES = 64
MAX_QUANTITY = 1_000_000
MAX_PROJECT_PRICE = 1_000_000_000_000
ALLOWED_ENDPOINT_HOSTS = frozenset(
    {"coin_intelligence_gemma_server", "127.0.0.1", "localhost"}
)
EXPECTED_KEYS = frozenset(
    {
        "side",
        "settlement",
        "quantity",
        "price",
        "commodity",
        "confidence",
        "abstain",
        "reason_code",
    }
)


@dataclass(frozen=True, slots=True)
class GemmaParserCandidate:
    side: str | None
    settlement: str | None
    quantity: int | None
    price: int | None
    commodity: str | None
    confidence: float
    abstain: bool
    reason_code: str

    def to_dict(self) -> dict:
        return asdict(self)


def validate_local_gemma_endpoint(endpoint: str) -> str:
    parsed = urlparse(str(endpoint).strip())
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ALLOWED_ENDPOINT_HOSTS
        or parsed.port != 18123
        or parsed.path != "/v1/chat/completions"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("gemma endpoint must be the fixed local service")
    return parsed.geturl()


def _extract_json(output: str) -> dict:
    if len(output) > MAX_CONTENT_CHARS:
        raise ValueError("gemma output exceeds bound")
    stripped = output.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        raise ValueError("gemma output is not exactly one JSON object")
    payload = json.loads(stripped)
    if not isinstance(payload, dict) or set(payload) != EXPECTED_KEYS:
        raise ValueError("gemma output schema mismatch")
    return payload


def _validated(
    payload: dict,
    *,
    canonical_commodities: Sequence[str],
) -> GemmaParserCandidate:
    abstain = payload["abstain"]
    if not isinstance(abstain, bool):
        raise ValueError("gemma abstain must be boolean")
    side = payload["side"]
    settlement = payload["settlement"]
    commodity = payload["commodity"]
    if side not in {None, "BUY", "SELL"}:
        raise ValueError("gemma side invalid")
    if settlement not in {None, "CASH", "TOMORROW"}:
        raise ValueError("gemma settlement invalid")
    if commodity is not None and commodity not in set(canonical_commodities):
        raise ValueError("gemma commodity is not canonical")
    quantity = payload["quantity"]
    price = payload["price"]
    if quantity is not None and (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or quantity <= 0
        or quantity > MAX_QUANTITY
    ):
        raise ValueError("gemma quantity invalid")
    if price is not None and (
        isinstance(price, bool)
        or not isinstance(price, int)
        or price <= 0
        or price > MAX_PROJECT_PRICE
    ):
        raise ValueError("gemma price invalid")
    raw_confidence = payload["confidence"]
    if isinstance(raw_confidence, bool) or not isinstance(
        raw_confidence,
        (int, float),
    ):
        raise ValueError("gemma confidence invalid")
    confidence = float(raw_confidence)
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("gemma confidence invalid")
    if not isinstance(payload["reason_code"], str):
        raise ValueError("gemma reason invalid")
    reason = payload["reason_code"].strip().upper()
    if (
        not reason
        or len(reason) > 96
        or re.fullmatch(r"[A-Z0-9_:-]+", reason) is None
    ):
        raise ValueError("gemma reason invalid")
    if not abstain and None in {
        side,
        settlement,
        quantity,
        price,
        commodity,
    }:
        raise ValueError("non-abstaining gemma result is incomplete")
    return GemmaParserCandidate(
        side=side,
        settlement=settlement,
        quantity=quantity,
        price=price,
        commodity=commodity,
        confidence=confidence,
        abstain=abstain,
        reason_code=reason,
    )


def _response_format(canonical_commodities: Sequence[str]) -> dict:
    """Constrain decoding itself instead of repairing free-form output."""

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "normalized_offer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "side": {"enum": ["BUY", "SELL", None]},
                    "settlement": {
                        "enum": ["CASH", "TOMORROW", None],
                    },
                    "quantity": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": MAX_QUANTITY,
                    },
                    "price": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": MAX_PROJECT_PRICE,
                    },
                    "commodity": {
                        "enum": [*canonical_commodities, None],
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                    },
                    "abstain": {"type": "boolean"},
                    "reason_code": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 96,
                    },
                },
                "required": sorted(EXPECTED_KEYS),
                "additionalProperties": False,
            },
        },
    }


def infer_gemma_parser_candidate(
    text: str,
    *,
    endpoint: str,
    canonical_commodities: Sequence[str],
    timeout_seconds: float,
    opener=urlopen,
) -> GemmaParserCandidate:
    """Call only the warm local Docker service and accept strict JSON."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("gemma input is empty")
    if len(text) > MAX_INPUT_CHARS:
        raise ValueError("gemma input exceeds bound")
    endpoint = validate_local_gemma_endpoint(endpoint)
    allowed = tuple(dict.fromkeys(str(item) for item in canonical_commodities))
    if (
        not allowed
        or len(allowed) > MAX_CANONICAL_COMMODITIES
        or any(not item.strip() or len(item) > 80 for item in allowed)
    ):
        raise ValueError("gemma canonical commodity catalog invalid")
    system = (
        "Return exactly one JSON object and no prose. Parse a Persian Iranian "
        "coin offer. Abbreviations: خ means BUY, ف means SELL, ن or نقدی "
        "means CASH, and فردا or فردایی means TOMORROW. Never calculate or "
        "guess missing numeric digits. If no coin name appears in the text, "
        "commodity must be null and abstain must be true; do not apply the "
        "product's separate default-Imam rule. Still extract every other "
        "explicit field. Use null and abstain=true when uncertain. Keys "
        "exactly: side "
        "(BUY|SELL|null), settlement (CASH|TOMORROW|null), quantity "
        "(integer|null), price (integer|null, project thousand-toman unit), "
        "commodity (one exact canonical value or null), confidence (0..1), "
        "abstain (boolean), reason_code (short ASCII code). Canonical values: "
        + json.dumps(allowed, ensure_ascii=False)
    )
    request_body = json.dumps(
        {
            "model": "gemma-4-E4B-it-q4_0",
            "temperature": 0,
            "max_tokens": 120,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": _response_format(allowed),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=request_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(request, timeout=float(timeout_seconds)) as response:
        raw_body = response.read(MAX_HTTP_BODY_BYTES + 1)
    if len(raw_body) > MAX_HTTP_BODY_BYTES:
        raise ValueError("gemma HTTP body exceeds bound")
    body = json.loads(raw_body)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("gemma response envelope invalid") from exc
    if not isinstance(content, str):
        raise ValueError("gemma response content invalid")
    return _validated(
        _extract_json(content),
        canonical_commodities=allowed,
    )
