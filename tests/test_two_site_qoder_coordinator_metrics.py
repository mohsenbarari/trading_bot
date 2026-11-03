"""Qoder observations must never be reported as historical Codex savings."""
import json
from scripts import two_site_cycle_metrics as metrics


def stream(tmp_path, terminal):
    path = tmp_path / "stream.jsonl"
    path.write_text(json.dumps(terminal) + "\n")
    return metrics.usage_from_stream(tmp_path, path)


def test_zero_token_placeholders_with_credits_are_unknown(tmp_path):
    result = stream(tmp_path, {"type": "result", "subtype": "success", "is_error": False,
        "usage": {"input_tokens": 0, "output_tokens": 0}, "total_credits": 0.125})
    assert result["usage_state"] == "UNKNOWN"
    assert result["input_tokens"] is None
    assert result["coordinator_usage"]["token_usage_state"] == "UNKNOWN"
    assert result["coordinator_usage"]["credits"] == 0.125


def test_positive_observed_tokens_are_labeled_provider_reported(tmp_path):
    result = stream(tmp_path, {"type": "result", "subtype": "success", "is_error": False,
        "usage": {"input_tokens": 120, "output_tokens": 30}, "total_credits": 0.25})
    assert result["coordinator_usage"]["token_usage_state"] == "PROVIDER_REPORTED"
    assert result["coordinator_usage"]["input_tokens"] == 120
    assert result["coordinator_usage"]["output_tokens"] == 30
    assert result["input_tokens"] is None


def test_qoder_cannot_enter_codex_token_aggregate():
    assert metrics._codex_observed_usage({"coordinator_provider": "qoder",
        "usage_state": "OBSERVED_COMPLETED_TURNS", "input_tokens": 10,
        "cached_input_tokens": 0, "uncached_input_tokens": 10, "output_tokens": 2}) is None


def test_finite_reviewer_continuation_names_are_recognized():
    assert metrics._is_gemini_reviewer_run_id("unit-v0r1")
    assert metrics._is_gemini_reviewer_run_id("unit-v2r2-retry-2")
    assert not metrics._is_gemini_reviewer_run_id("unit-v2r3")
