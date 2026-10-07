"""Pure normalization for two-site work cost lineage records."""

from __future__ import annotations

import math
import re
from typing import Any

from scripts import two_site_cycle_metrics as cycle_metrics

__all__ = [
    "GROUPING_PROVIDER_STATES",
    "MAX_NUMERIC",
    "normalize_work_cost_row",
]

MAX_NUMERIC: int = 1000000000000

_SAFE_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,80}")
_HEX_40_RE = re.compile(r"[a-f0-9]{40}")
_HEX_64_RE = re.compile(r"[a-f0-9]{64}")

GROUPING_PROVIDER_STATES = frozenset({
    "OBSERVED_CONTROLLER_ATTEMPTS",
    "OBSERVED_MATCHING_ATTEMPTS",
})

_VALID_PROVIDER_STATES = frozenset({
    "OBSERVED_CONTROLLER_ATTEMPTS",
    "OBSERVED_MATCHING_ATTEMPTS",
    "OK",
    "PARTIAL",
})


def _bound_number(v: object) -> float | None:
    t = type(v)
    if t is int:
        if not isinstance(v, bool) and 0 <= v <= MAX_NUMERIC:
            return float(v)
        return None
    if t is float:
        if 0.0 <= v <= MAX_NUMERIC and math.isfinite(v):
            return v
        return None
    return None


def _bound_finished(v: object) -> float | str | None:
    num = _bound_number(v)
    if num is not None:
        return num
    if type(v) is str and 1 <= len(v) <= 80:
        if all(32 <= ord(c) <= 126 for c in v):
            return v
    return None


def _safe_id(v: object) -> str | None:
    if type(v) is str and _SAFE_ID_RE.fullmatch(v):
        return v
    return None


def _hex_40(v: object) -> str | None:
    if type(v) is str and _HEX_40_RE.fullmatch(v):
        return v
    return None


def _hex_64(v: object) -> str | None:
    if type(v) is str and _HEX_64_RE.fullmatch(v):
        return v
    return None


def _validate_token_map(val: object) -> dict[str, int] | None:
    if type(val) is not dict or len(val) != 3:
        return None
    for k in val.keys():
        if type(k) is not str:
            return None
    if set(val.keys()) != {"input_tokens", "output_tokens", "total_tokens"}:
        return None
    it = val["input_tokens"]
    ot = val["output_tokens"]
    tt = val["total_tokens"]
    if type(it) is not int or isinstance(it, bool) or not (0 <= it <= MAX_NUMERIC):
        return None
    if type(ot) is not int or isinstance(ot, bool) or not (0 <= ot <= MAX_NUMERIC):
        return None
    if type(tt) is not int or isinstance(tt, bool) or not (0 <= tt <= MAX_NUMERIC):
        return None
    if it + ot != tt:
        return None
    return {"input_tokens": it, "output_tokens": ot, "total_tokens": tt}


def _validate_semantic(val: object) -> dict[str, str] | None:
    if type(val) is not dict or len(val) != 5:
        return None
    for k in val.keys():
        if type(k) is not str:
            return None
    if set(val.keys()) != {
        "stage_id", "scenario_key", "gap_reference", "source_sha256", "fingerprint",
    }:
        return None
    for v in val.values():
        if type(v) is not str:
            return None
    if len(val["stage_id"]) > 128 or len(val["scenario_key"]) > 128:
        return None
    if len(val["gap_reference"]) > 500:
        return None
    if len(val["source_sha256"]) != 64 or len(val["fingerprint"]) != 64:
        return None
    res = cycle_metrics.semantic_identity(val)
    if res is None:
        return None
    return dict(res)


def _validate_work(val: object) -> dict[str, Any] | None:
    if type(val) is not dict or len(val) != 2:
        return None
    for k in val.keys():
        if type(k) is not str:
            return None
    if set(val.keys()) != {"schema_version", "fingerprint"}:
        return None
    sv = val["schema_version"]
    if type(sv) is not int or isinstance(sv, bool) or sv != 1:
        return None
    fp = val["fingerprint"]
    if type(fp) is not str or len(fp) != 64 or not _HEX_64_RE.fullmatch(fp):
        return None
    res = cycle_metrics.work_lineage(val)
    if res is None:
        return None
    return {"schema_version": 1, "fingerprint": str(res["fingerprint"])}


def normalize_work_cost_row(value: object) -> dict[str, Any] | None:
    """Return sanitized canonical 12-key work cost row or None if structurally invalid."""
    if type(value) is not dict or len(value) > 64:
        return None
    for k in value.keys():
        if type(k) is not str:
            return None

    run_id = _safe_id(value.get("run_id"))
    model = _safe_id(value.get("model"))
    effort = _safe_id(value.get("effort"))
    state = _safe_id(value.get("state"))
    finished_at = _bound_finished(value.get("finished_at"))
    head_before = _hex_40(value.get("head_before"))
    head_after = _hex_40(value.get("head_after"))
    raw_worker = value.get("gemini_worker_run_id")
    raw_reviewer = value.get("gemini_reviewer_run_id")
    worker_id = _safe_id(raw_worker)
    reviewer_id = _safe_id(raw_reviewer)
    proposal_sha = _hex_64(value.get("proposal_sha256"))
    duration = _bound_number(value.get("duration_seconds"))

    top_sem = _validate_semantic(value.get("semantic_identity"))
    top_work = _validate_work(value.get("work_lineage"))

    codex_usage = None
    raw_usage_state = value.get("usage_state")
    if type(raw_usage_state) is str and raw_usage_state == "OBSERVED_COMPLETED_TURNS":
        c_in = value.get("input_tokens")
        c_cin = value.get("cached_input_tokens")
        c_uin = value.get("uncached_input_tokens")
        c_out = value.get("output_tokens")
        if (
            type(c_in) is int and not isinstance(c_in, bool) and 0 <= c_in <= MAX_NUMERIC
            and type(c_cin) is int and not isinstance(c_cin, bool) and 0 <= c_cin <= MAX_NUMERIC
            and type(c_uin) is int and not isinstance(c_uin, bool) and 0 <= c_uin <= MAX_NUMERIC
            and type(c_out) is int and not isinstance(c_out, bool) and 0 <= c_out <= MAX_NUMERIC
            and c_in == c_cin + c_uin
        ):
            codex_usage = {
                "input_tokens": c_in,
                "cached_input_tokens": c_cin,
                "uncached_input_tokens": c_uin,
                "output_tokens": c_out,
            }

    raw_pm = value.get("provider_metrics")
    pm_is_dict = type(raw_pm) is dict and len(raw_pm) <= 32
    if pm_is_dict:
        for k in raw_pm.keys():
            if type(k) is not str:
                pm_is_dict = False
                break

    provider_state = None
    nested_identities: dict[str, dict[str, Any]]
    observed_subtotal = None
    full_usage = None
    attempt_pair = None

    if not pm_is_dict:
        nested_identities = {
            "semantic": {"state": "UNAVAILABLE", "value": None},
            "work": {"state": "UNAVAILABLE", "value": None},
        }
    else:
        if "semantic_identity" not in raw_pm:
            nested_sem: dict[str, Any] = {"state": "ABSENT", "value": None}
        else:
            val_sem = _validate_semantic(raw_pm["semantic_identity"])
            if val_sem is not None:
                nested_sem = {"state": "VALID", "value": val_sem}
            else:
                nested_sem = {"state": "INVALID", "value": None}

        if "work_lineage" not in raw_pm:
            nested_work: dict[str, Any] = {"state": "ABSENT", "value": None}
        else:
            val_work = _validate_work(raw_pm["work_lineage"])
            if val_work is not None:
                nested_work = {"state": "VALID", "value": val_work}
            else:
                nested_work = {"state": "INVALID", "value": None}

        nested_identities = {
            "semantic": nested_sem,
            "work": nested_work,
        }

        raw_pstate = raw_pm.get("state")
        if type(raw_pstate) is str and raw_pstate in _VALID_PROVIDER_STATES:
            provider_state = raw_pstate

        if provider_state is not None:
            valid_usage = _validate_token_map(raw_pm.get("usage"))
            valid_sub = _validate_token_map(raw_pm.get("observed_usage_subtotal"))

            if provider_state == "PARTIAL":
                selected_sub = valid_sub
            else:
                selected_sub = valid_usage if valid_usage is not None else valid_sub

            if selected_sub is not None:
                observed_subtotal = dict(selected_sub)

            raw_att = raw_pm.get("attempts")
            raw_uatt = raw_pm.get("usage_observed_attempts")
            valid_counts = (
                type(raw_att) is int and not isinstance(raw_att, bool) and 0 <= raw_att <= 32
                and type(raw_uatt) is int and not isinstance(raw_uatt, bool) and 0 <= raw_uatt <= 32
                and raw_uatt <= raw_att
            )

            if valid_counts:
                if selected_sub is not None and selected_sub["total_tokens"] > 0 and raw_uatt == 0:
                    attempt_pair = None
                else:
                    attempt_pair = [raw_att, raw_uatt]

            if (
                attempt_pair is not None
                and attempt_pair[0] == attempt_pair[1]
                and valid_usage is not None
                and provider_state != "PARTIAL"
            ):
                full_usage = dict(valid_usage)

    sem_ok = (
        nested_identities["semantic"]["state"] == "ABSENT"
        or (
            nested_identities["semantic"]["state"] == "VALID"
            and nested_identities["semantic"]["value"] == top_sem
        )
    )
    work_ok = (
        nested_identities["work"]["state"] == "ABSENT"
        or (
            nested_identities["work"]["state"] == "VALID"
            and nested_identities["work"]["value"] == top_work
        )
    )
    group_eligible = (
        provider_state in GROUPING_PROVIDER_STATES
        and top_sem is not None
        and top_work is not None
        and sem_ok
        and work_ok
    )

    valid_heads = (
        head_before is not None
        and head_after is not None
        and head_before != head_after
    )
    valid_roles = (
        type(raw_worker) is str
        and type(raw_reviewer) is str
        and raw_worker != raw_reviewer
        and cycle_metrics._is_gemini_worker_run_id(raw_worker)
        and cycle_metrics._is_gemini_reviewer_run_id(raw_reviewer)
    )
    observed_candidate = (
        group_eligible
        and state in ("CONTINUE", "COMPLETE")
        and valid_heads
        and valid_roles
        and proposal_sha is not None
    )

    return {
        "schema_version": 1,
        "run_id": run_id,
        "dimensions": {
            "model": model,
            "effort": effort,
            "state": state,
            "finished_at": finished_at,
            "head_before": head_before,
            "head_after": head_after,
            "worker_run_id": worker_id,
            "reviewer_run_id": reviewer_id,
            "proposal_sha256": proposal_sha,
        },
        "semantic_identity": top_sem,
        "work_lineage": top_work,
        "provider_state": provider_state,
        "nested_identities": nested_identities,
        "group_eligible": group_eligible,
        "observed_candidate": observed_candidate,
        "codex_usage": codex_usage,
        "provider_usage": {
            "observed_subtotal": observed_subtotal,
            "full_usage": full_usage,
            "attempt_pair": attempt_pair,
        },
        "reported_cycle_seconds": duration,
    }
