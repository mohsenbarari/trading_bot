"""Evidence classification for Gemini quota error streams in two-site architecture."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re

_RFC3339_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\b"
)

_RESOURCE_EXHAUSTED_RE = re.compile(r"\bRESOURCE_EXHAUSTED\b")

_DIRECT_RESET_PREFIX_RE = re.compile(
    r"(?:"
    r"\b(?:resets?|retry[-_ ]?(?:after|at))(?:\s+(?:at|time|timestamp|is|on))?"
    r"|reset[-_](?:at|time|timestamp)"
    r"|retry[-_]after"
    r")[\s:=]*[\'\"`(\[]?$",
    re.IGNORECASE,
)

_RESET_TIME_CLAUSE_RE = re.compile(
    r"(?i)\b(?:resets?|retry[-_ ]?(?:after|at))(?:\s+(?:at|in|after|time|timestamp))?[\s:=]+([^\s,;]+)"
)


def _no_dup_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    res: dict[str, object] = {}
    for k, v in pairs:
        if k in res:
            raise ValueError(f"duplicate key: {k}")
        res[k] = v
    return res


def _is_bounded_ascii(val: object, max_len: int = 128) -> bool:
    if not isinstance(val, str) or not val or len(val) > max_len:
        return False
    return all(33 <= ord(c) <= 126 for c in val)


def _has_conflicting_reset_time(error_str: str, attached_ts: str) -> bool:
    for m in _RESET_TIME_CLAUSE_RE.finditer(error_str):
        token = m.group(1).strip("'\"`()[]")
        if any(c.isdigit() for c in token):
            if not token.startswith(attached_ts) and not attached_ts.startswith(token):
                return True
    return False


def classify_gemini_quota_stream(
    raw: str,
    expected_model: str,
    now_utc: datetime,
) -> dict | None:
    """Classifies a bounded Gemini quota CLI error stream.

    Pure classifier: returns evidence dictionary on valid streams, or None on invalid streams.
    Does not provide replay authority or retry execution.
    """
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None or now_utc.tzinfo.utcoffset(now_utc) is None:
        raise ValueError("now_utc must be an aware datetime")
    now_clean = now_utc.astimezone(timezone.utc)

    if not _is_bounded_ascii(expected_model):
        return None

    if not isinstance(raw, str):
        return None

    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError:
        return None

    if len(raw_bytes) > 128 * 1024:
        return None

    lines = raw.splitlines()
    if len(lines) > 256:
        return None

    for line in lines:
        try:
            line_bytes = line.encode("utf-8")
        except UnicodeEncodeError:
            return None
        if len(line_bytes) > 8 * 1024:
            return None

    events: list[dict] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped, object_pairs_hook=_no_dup_pairs)
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        events.append(parsed)

    if len(events) != 2:
        return None

    init_evt, res_evt = events[0], events[1]

    if init_evt.get("event") != "init" or res_evt.get("event") != "result":
        return None

    if set(init_evt.keys()) - {"event", "conversation_id", "init"}:
        return None
    cid = init_evt.get("conversation_id")
    if not _is_bounded_ascii(cid):
        return None

    init_payload = init_evt.get("init")
    if not isinstance(init_payload, dict):
        return None
    model = init_payload.get("model")
    if not _is_bounded_ascii(model) or model != expected_model:
        return None

    if set(res_evt.keys()) - {"event", "conversation_id", "result"}:
        return None
    if "conversation_id" in res_evt and res_evt["conversation_id"] != cid:
        return None

    res_payload = res_evt.get("result")
    if not isinstance(res_payload, dict):
        return None

    if res_payload.get("conversation_id") != cid:
        return None
    if res_payload.get("status") != "ERROR":
        return None

    for k in ("response", "structured_output", "denied_actions", "tool", "tools", "tool_calls"):
        if k in res_payload and res_payload[k]:
            return None
        if k in res_evt and res_evt[k]:
            return None

    usage = res_payload.get("usage")
    if not isinstance(usage, dict) or not usage or len(usage) > 64:
        return None
    for uk, uv in usage.items():
        if not isinstance(uk, str) or len(uk) > 64:
            return None
        if type(uv) is not int or uv != 0:
            return None

    error_str = res_payload.get("error")
    if not isinstance(error_str, str) or not error_str or len(error_str) > 2048:
        return None

    if not _RESOURCE_EXHAUSTED_RE.search(error_str):
        return None
    error_lower = error_str.lower()
    if "quota" not in error_lower and "capacity" not in error_lower:
        return None

    rfc3339_matches = list(_RFC3339_RE.finditer(error_str))
    attached_dts: list[str] = []
    for m in rfc3339_matches:
        prefix = error_str[: m.start()]
        if _DIRECT_RESET_PREFIX_RE.search(prefix):
            attached_dts.append(m.group(1))

    if len(attached_dts) != 1 or _has_conflicting_reset_time(error_str, attached_dts[0]):
        return {
            "kind": "QUOTA_RESET_UNKNOWN",
            "provider": "gemini",
            "model": model,
            "conversation_id": cid,
            "reset_at_utc": None,
        }

    raw_ts = attached_dts[0]
    try:
        if raw_ts.endswith("Z"):
            dt = datetime.fromisoformat(raw_ts[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(raw_ts)
    except Exception:
        dt = None

    if dt is None:
        return {
            "kind": "QUOTA_RESET_UNKNOWN",
            "provider": "gemini",
            "model": model,
            "conversation_id": cid,
            "reset_at_utc": None,
        }

    dt_utc = dt.astimezone(timezone.utc)
    if dt_utc <= now_clean or dt_utc > now_clean + timedelta(days=8):
        return {
            "kind": "QUOTA_RESET_UNKNOWN",
            "provider": "gemini",
            "model": model,
            "conversation_id": cid,
            "reset_at_utc": None,
        }

    if dt_utc.microsecond:
        formatted_utc = dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        formatted_utc = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    return {
        "kind": "QUOTA_RESET_KNOWN",
        "provider": "gemini",
        "model": model,
        "conversation_id": cid,
        "reset_at_utc": formatted_utc,
    }
