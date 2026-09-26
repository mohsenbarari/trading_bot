"""Hermetic unit tests for Gemini quota stream evidence classifier."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest

try:
    from scripts.two_site_quota_evidence import (
        classify_gemini_quota_stream,
        provider_for_model,
    )
except ImportError:
    from two_site_quota_evidence import (
        classify_gemini_quota_stream,
        provider_for_model,
    )

try:
    from scripts.two_site_worker_model_routing import OPUS_MODEL
except ImportError:
    from two_site_worker_model_routing import OPUS_MODEL


def _build_stream(cid: str, model: str, error: str, usage: dict | None = None) -> str:
    if usage is None:
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    evt1 = {"event": "init", "conversation_id": cid, "init": {"model": model}}
    evt2 = {
        "event": "result",
        "result": {
            "conversation_id": cid,
            "status": "ERROR",
            "error": error,
            "usage": usage,
        },
    }
    return f"{json.dumps(evt1)}\n{json.dumps(evt2)}\n"


class TestTwoSiteQuotaEvidence(unittest.TestCase):
    def setUp(self) -> None:
        self.now_utc = datetime(2026, 9, 24, 18, 0, 0, tzinfo=timezone.utc)
        self.cid = "conv-test-001"
        self.model = "gemini-1.5-pro"

    def test_nested_envelope_valid_known_reset_and_timezone_normalization(self) -> None:
        error = "RESOURCE_EXHAUSTED: Quota exceeded for model. Reset at 2026-09-24T21:00:00+02:00"
        raw = _build_stream(self.cid, self.model, error)
        res = classify_gemini_quota_stream(raw, self.model, self.now_utc)
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "QUOTA_RESET_KNOWN")
        self.assertEqual(res["provider"], "gemini")
        self.assertEqual(res["model"], self.model)
        self.assertEqual(res["conversation_id"], self.cid)
        self.assertEqual(res["reset_at_utc"], "2026-09-24T19:00:00Z")

    def test_concrete_false_positive_counterexample_unrelated_timestamp_and_ambiguous_reset(self) -> None:
        error = (
            "RESOURCE_EXHAUSTED: Quota exceeded for model. "
            "request_time=2026-09-24T18:00:00Z but reset at 8:09 AM"
        )
        raw = _build_stream(self.cid, self.model, error)
        res = classify_gemini_quota_stream(raw, self.model, self.now_utc)
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "QUOTA_RESET_UNKNOWN")
        self.assertEqual(res["provider"], "gemini")
        self.assertEqual(res["model"], self.model)
        self.assertEqual(res["conversation_id"], self.cid)
        self.assertIsNone(res["reset_at_utc"])
        self.assertNotIn("raw", res)
        self.assertNotIn("error", res)

    def test_explicit_reset_phrases_accepted(self) -> None:
        phrases = [
            "RESOURCE_EXHAUSTED: Quota exceeded. reset at 2026-09-24T19:00:00Z",
            "RESOURCE_EXHAUSTED: Quota capacity reached. retry-after: 2026-09-24T19:00:00Z",
            "RESOURCE_EXHAUSTED: Capacity limit reached. reset_time=2026-09-24T19:00:00Z",
            "RESOURCE_EXHAUSTED: Quota exhausted. Please retry after 2026-09-24T19:00:00Z",
        ]
        for phr in phrases:
            with self.subTest(phrase=phr):
                raw = _build_stream(self.cid, self.model, phr)
                res = classify_gemini_quota_stream(raw, self.model, self.now_utc)
                self.assertIsNotNone(res)
                self.assertEqual(res["kind"], "QUOTA_RESET_KNOWN")
                self.assertEqual(res["reset_at_utc"], "2026-09-24T19:00:00Z")

    def test_unrelated_timestamp_not_treated_as_reset(self) -> None:
        error = "RESOURCE_EXHAUSTED: Quota exceeded. log_time=2026-09-24T20:00:00Z without reset details"
        raw = _build_stream(self.cid, self.model, error)
        res = classify_gemini_quota_stream(raw, self.model, self.now_utc)
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "QUOTA_RESET_UNKNOWN")
        self.assertIsNone(res["reset_at_utc"])

    def test_wrong_flat_form_impostors_rejected(self) -> None:
        flat_init = json.dumps({"event": "init", "conversation_id": self.cid, "model": self.model})
        good_res = json.dumps({
            "event": "result",
            "result": {
                "conversation_id": self.cid,
                "status": "ERROR",
                "error": "RESOURCE_EXHAUSTED: Quota exceeded",
                "usage": {"tokens": 0},
            },
        })
        self.assertIsNone(classify_gemini_quota_stream(f"{flat_init}\n{good_res}", self.model, self.now_utc))

        good_init = json.dumps({"event": "init", "conversation_id": self.cid, "init": {"model": self.model}})
        flat_res = json.dumps({
            "event": "result",
            "conversation_id": self.cid,
            "status": "ERROR",
            "error": "RESOURCE_EXHAUSTED: Quota exceeded",
            "usage": {"tokens": 0},
        })
        self.assertIsNone(classify_gemini_quota_stream(f"{good_init}\n{flat_res}", self.model, self.now_utc))

    def test_usage_matrix_zero_empty_nonzero(self) -> None:
        err = "RESOURCE_EXHAUSTED: Quota exceeded. reset at 2026-09-24T19:00:00Z"
        raw_ok = _build_stream(self.cid, self.model, err, usage={"input_tokens": 0, "output_tokens": 0})
        self.assertIsNotNone(classify_gemini_quota_stream(raw_ok, self.model, self.now_utc))

        raw_empty = _build_stream(self.cid, self.model, err, usage={})
        self.assertIsNone(classify_gemini_quota_stream(raw_empty, self.model, self.now_utc))

        raw_nonzero = _build_stream(self.cid, self.model, err, usage={"prompt_tokens": 1})
        self.assertIsNone(classify_gemini_quota_stream(raw_nonzero, self.model, self.now_utc))

        raw_nonint = _build_stream(self.cid, self.model, err, usage={"prompt_tokens": "0"})  # type: ignore[dict-item]
        self.assertIsNone(classify_gemini_quota_stream(raw_nonint, self.model, self.now_utc))

    def test_model_and_cid_validation_and_mismatch(self) -> None:
        err = "RESOURCE_EXHAUSTED: Quota exceeded"
        raw = _build_stream(self.cid, self.model, err)
        self.assertIsNone(classify_gemini_quota_stream(raw, "other-model", self.now_utc))

        evt1 = {"event": "init", "conversation_id": "cid-a", "init": {"model": self.model}}
        evt2 = {
            "event": "result",
            "result": {
                "conversation_id": "cid-b",
                "status": "ERROR",
                "error": err,
                "usage": {"tokens": 0},
            },
        }
        self.assertIsNone(classify_gemini_quota_stream(f"{json.dumps(evt1)}\n{json.dumps(evt2)}", self.model, self.now_utc))

        raw_non_ascii = _build_stream("cid-\u200c", self.model, err)
        self.assertIsNone(classify_gemini_quota_stream(raw_non_ascii, self.model, self.now_utc))

    def test_duplicate_keys_and_extraneous_events(self) -> None:
        err = "RESOURCE_EXHAUSTED: Quota exceeded"
        dup_key_line = '{"event": "init", "conversation_id": "c1", "conversation_id": "c1", "init": {"model": "gemini-1.5-pro"}}'
        res_line = json.dumps({
            "event": "result",
            "result": {"conversation_id": "c1", "status": "ERROR", "error": err, "usage": {"tokens": 0}},
        })
        self.assertIsNone(classify_gemini_quota_stream(f"{dup_key_line}\n{res_line}", "gemini-1.5-pro", self.now_utc))

        step_line = json.dumps({"event": "step", "index": 1})
        init_line = json.dumps({"event": "init", "conversation_id": "c1", "init": {"model": "gemini-1.5-pro"}})
        raw_step = f"{init_line}\n{step_line}\n{res_line}"
        self.assertIsNone(classify_gemini_quota_stream(raw_step, "gemini-1.5-pro", self.now_utc))

    def test_partial_response_and_tools_rejected(self) -> None:
        evt1 = {"event": "init", "conversation_id": self.cid, "init": {"model": self.model}}
        evt2 = {
            "event": "result",
            "result": {
                "conversation_id": self.cid,
                "status": "ERROR",
                "error": "RESOURCE_EXHAUSTED: Quota exceeded",
                "usage": {"tokens": 0},
                "response": "partial content",
            },
        }
        self.assertIsNone(classify_gemini_quota_stream(f"{json.dumps(evt1)}\n{json.dumps(evt2)}", self.model, self.now_utc))

    def test_machine_marker_boundaries(self) -> None:
        raw_429 = _build_stream(self.cid, self.model, "HTTP 429: Too Many Requests; quota exceeded")
        self.assertIsNone(classify_gemini_quota_stream(raw_429, self.model, self.now_utc))

        raw_quota = _build_stream(self.cid, self.model, "Quota exceeded for this user account")
        self.assertIsNone(classify_gemini_quota_stream(raw_quota, self.model, self.now_utc))

        raw_unrelated = _build_stream(self.cid, self.model, "RESOURCE_EXHAUSTED: Out of memory buffer")
        self.assertIsNone(classify_gemini_quota_stream(raw_unrelated, self.model, self.now_utc))

        raw_prefixed_exhausted = _build_stream(self.cid, self.model, "NOT_RESOURCE_EXHAUSTED: Quota exceeded")
        self.assertIsNone(classify_gemini_quota_stream(raw_prefixed_exhausted, self.model, self.now_utc))

    def test_malformed_surrogate_raw_returns_none(self) -> None:
        surrogate_raw = "{\"event\": \"init\"}\n\ud800\n"
        self.assertIsNone(classify_gemini_quota_stream(surrogate_raw, self.model, self.now_utc))

    def test_clock_boundaries_and_naive_datetime(self) -> None:
        naive_now = datetime(2026, 9, 24, 18, 0, 0)
        raw = _build_stream(self.cid, self.model, "RESOURCE_EXHAUSTED: Quota exceeded")
        with self.assertRaises(ValueError):
            classify_gemini_quota_stream(raw, self.model, naive_now)

        past_err = "RESOURCE_EXHAUSTED: Quota exceeded. reset at 2026-09-24T17:59:59Z"
        res_past = classify_gemini_quota_stream(_build_stream(self.cid, self.model, past_err), self.model, self.now_utc)
        self.assertIsNotNone(res_past)
        self.assertEqual(res_past["kind"], "QUOTA_RESET_UNKNOWN")
        self.assertIsNone(res_past["reset_at_utc"])

        equal_ts = self.now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        equal_err = f"RESOURCE_EXHAUSTED: Quota exceeded. reset at {equal_ts}"
        res_equal = classify_gemini_quota_stream(_build_stream(self.cid, self.model, equal_err), self.model, self.now_utc)
        self.assertIsNotNone(res_equal)
        self.assertEqual(res_equal["kind"], "QUOTA_RESET_UNKNOWN")
        self.assertIsNone(res_equal["reset_at_utc"])

        future_9d = self.now_utc + timedelta(days=8, seconds=1)
        future_ts = future_9d.strftime("%Y-%m-%dT%H:%M:%SZ")
        far_err = f"RESOURCE_EXHAUSTED: Quota exceeded. reset at {future_ts}"
        res_far = classify_gemini_quota_stream(_build_stream(self.cid, self.model, far_err), self.model, self.now_utc)
        self.assertIsNotNone(res_far)
        self.assertEqual(res_far["kind"], "QUOTA_RESET_UNKNOWN")
        self.assertIsNone(res_far["reset_at_utc"])

        valid_7d = self.now_utc + timedelta(days=7)
        valid_ts = valid_7d.strftime("%Y-%m-%dT%H:%M:%SZ")
        valid_err = f"RESOURCE_EXHAUSTED: Quota exceeded. reset at {valid_ts}"
        res_valid = classify_gemini_quota_stream(_build_stream(self.cid, self.model, valid_err), self.model, self.now_utc)
        self.assertIsNotNone(res_valid)
        self.assertEqual(res_valid["kind"], "QUOTA_RESET_KNOWN")
        self.assertEqual(res_valid["reset_at_utc"], valid_ts)

    def test_provider_for_model_attribution(self) -> None:
        self.assertEqual(provider_for_model(OPUS_MODEL), "claude")
        self.assertEqual(provider_for_model("gemini-1.5-pro"), "gemini")
        self.assertEqual(provider_for_model("gemini-2.5-pro"), "gemini")
        self.assertEqual(provider_for_model("gemini-experimental"), "gemini")

        self.assertIsNone(provider_for_model("claude-opus-4-6"))
        self.assertIsNone(provider_for_model("claude-3-5-sonnet"))
        self.assertIsNone(provider_for_model("claude"))
        self.assertIsNone(provider_for_model("gemini"))
        self.assertIsNone(provider_for_model("gemini-"))
        self.assertIsNone(provider_for_model("gemini-\n"))
        self.assertIsNone(provider_for_model("gemini-" + "a" * 129))
        self.assertIsNone(provider_for_model("gpt-4o"))
        self.assertIsNone(provider_for_model(""))
        self.assertIsNone(provider_for_model(123))  # type: ignore[arg-type]
        self.assertIsNone(provider_for_model(None))  # type: ignore[arg-type]

    def test_opus_known_reset_classification(self) -> None:
        reset_ts = "2026-09-24T19:00:00Z"
        error = f"RESOURCE_EXHAUSTED: Quota exceeded for Opus model. Reset at {reset_ts}"
        raw = _build_stream(self.cid, OPUS_MODEL, error)
        res = classify_gemini_quota_stream(raw, OPUS_MODEL, self.now_utc)
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "QUOTA_RESET_KNOWN")
        self.assertEqual(res["provider"], "claude")
        self.assertEqual(res["model"], OPUS_MODEL)
        self.assertEqual(res["conversation_id"], self.cid)
        self.assertEqual(res["reset_at_utc"], reset_ts)

    def test_opus_unknown_reset_classification(self) -> None:
        error = "RESOURCE_EXHAUSTED: Quota exceeded for Opus model with no reset details"
        raw = _build_stream(self.cid, OPUS_MODEL, error)
        res = classify_gemini_quota_stream(raw, OPUS_MODEL, self.now_utc)
        self.assertIsNotNone(res)
        self.assertEqual(res["kind"], "QUOTA_RESET_UNKNOWN")
        self.assertEqual(res["provider"], "claude")
        self.assertEqual(res["model"], OPUS_MODEL)
        self.assertEqual(res["conversation_id"], self.cid)
        self.assertIsNone(res["reset_at_utc"])

    def test_opus_model_mismatch_and_unsupported_rejected(self) -> None:
        error = "RESOURCE_EXHAUSTED: Quota exceeded. reset at 2026-09-24T19:00:00Z"
        raw_opus = _build_stream(self.cid, OPUS_MODEL, error)
        self.assertIsNone(classify_gemini_quota_stream(raw_opus, "gemini-1.5-pro", self.now_utc))

        raw_gemini = _build_stream(self.cid, "gemini-1.5-pro", error)
        self.assertIsNone(classify_gemini_quota_stream(raw_gemini, OPUS_MODEL, self.now_utc))

        self.assertIsNone(classify_gemini_quota_stream(raw_opus, "unknown-model", self.now_utc))

    def test_opus_nonzero_usage_rejected(self) -> None:
        error = "RESOURCE_EXHAUSTED: Quota exceeded. reset at 2026-09-24T19:00:00Z"
        raw_nonzero = _build_stream(self.cid, OPUS_MODEL, error, usage={"prompt_tokens": 10})
        self.assertIsNone(classify_gemini_quota_stream(raw_nonzero, OPUS_MODEL, self.now_utc))

    def test_bare_gemini_model_rejected(self) -> None:
        error = "RESOURCE_EXHAUSTED: Quota exceeded. reset at 2026-09-24T19:00:00Z"
        raw = _build_stream(self.cid, "gemini-", error)
        self.assertIsNone(classify_gemini_quota_stream(raw, "gemini-", self.now_utc))
        self.assertIsNone(classify_gemini_quota_stream(raw, "gemini-1.5-pro", self.now_utc))

    def test_direct_script_import_subprocess(self) -> None:
        import os
        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        scripts_dir = repo_root / "scripts"
        cmd = [
            sys.executable,
            "-c",
            "import two_site_quota_evidence; "
            "assert two_site_quota_evidence.provider_for_model(two_site_quota_evidence.OPUS_MODEL) == 'claude'; "
            "assert two_site_quota_evidence.provider_for_model('gemini-1.5-pro') == 'gemini'",
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(scripts_dir)
        proc = subprocess.run(cmd, cwd=str(scripts_dir), env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Direct import failed:\nstdout={proc.stdout}\nstderr={proc.stderr}")


if __name__ == "__main__":
    unittest.main()
