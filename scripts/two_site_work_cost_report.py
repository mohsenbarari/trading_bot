"""Pure unit B reporting consumer for two-site work cost lineage ledger."""

from __future__ import annotations

import math
from typing import Any

from scripts.two_site_work_cost_rows import normalize_work_cost_row

__all__ = [
    "MAX_RECORDS",
    "WorkCostReportError",
    "summarize_work_lineage_ledger",
]

MAX_RECORDS: int = 2000


class WorkCostReportError(ValueError):
    """Raised when work lineage ledger records envelope is invalid."""


def _aggregate_costs(rows: list[dict[str, Any] | None]) -> dict[str, Any]:
    c_obs = 0
    c_unk = 0
    c_in = 0
    c_cin = 0
    c_uin = 0
    c_out = 0

    p_obs = 0
    p_unk = 0
    p_in = 0
    p_out = 0
    p_tot = 0
    full_prov_count = 0
    att_cov_obs = 0
    att_cov_unk = 0
    rep_att = 0
    obs_att = 0

    t_obs = 0
    t_unk = 0
    durations: list[float] = []

    for r in rows:
        if r is None:
            c_unk += 1
            p_unk += 1
            att_cov_unk += 1
            t_unk += 1
            continue

        c = r.get("codex_usage")
        if c is not None:
            c_obs += 1
            c_in += c["input_tokens"]
            c_cin += c["cached_input_tokens"]
            c_uin += c["uncached_input_tokens"]
            c_out += c["output_tokens"]
        else:
            c_unk += 1

        pu = r.get("provider_usage") or {}
        sub = pu.get("observed_subtotal")
        if sub is not None:
            p_obs += 1
            p_in += sub["input_tokens"]
            p_out += sub["output_tokens"]
            p_tot += sub["total_tokens"]
        else:
            p_unk += 1

        if pu.get("full_usage") is not None:
            full_prov_count += 1

        ap = pu.get("attempt_pair")
        if ap is not None:
            att_cov_obs += 1
            rep_att += ap[0]
            obs_att += ap[1]
        else:
            att_cov_unk += 1

        sec = r.get("reported_cycle_seconds")
        if sec is not None:
            t_obs += 1
            durations.append(float(sec))
        else:
            t_unk += 1

    codex_full = (
        {
            "input_tokens": c_in,
            "cached_input_tokens": c_cin,
            "uncached_input_tokens": c_uin,
            "output_tokens": c_out,
        }
        if len(rows) > 0 and c_unk == 0
        else None
    )

    prov_full = (
        {
            "input_tokens": p_in,
            "output_tokens": p_out,
            "total_tokens": p_tot,
        }
        if len(rows) > 0 and full_prov_count == len(rows)
        else None
    )

    durations.sort()
    time_sub = math.fsum(durations)
    time_full = time_sub if (len(rows) > 0 and t_unk == 0) else None

    return {
        "codex": {
            "observed_record_count": c_obs,
            "unknown_record_count": c_unk,
            "observed_subtotal": {
                "input_tokens": c_in,
                "cached_input_tokens": c_cin,
                "uncached_input_tokens": c_uin,
                "output_tokens": c_out,
            },
            "full_usage": codex_full,
        },
        "provider": {
            "observed_record_count": p_obs,
            "unknown_record_count": p_unk,
            "observed_subtotal": {
                "input_tokens": p_in,
                "output_tokens": p_out,
                "total_tokens": p_tot,
            },
            "full_usage": prov_full,
            "attempt_coverage_record_count": att_cov_obs,
            "unknown_attempt_coverage_record_count": att_cov_unk,
            "reported_attempt_count": rep_att,
            "observed_attempt_count": obs_att,
            "unknown_attempt_count": rep_att - obs_att,
        },
        "reported_cycle_seconds": {
            "observed_record_count": t_obs,
            "unknown_record_count": t_unk,
            "observed_subtotal": time_sub,
            "full_total": time_full,
        },
    }


def summarize_work_lineage_ledger(records: object) -> dict[str, Any]:
    """Summarize raw work lineage ledger records into pure offline metrics report."""
    if type(records) is not list or len(records) > MAX_RECORDS:
        raise WorkCostReportError("invalid work lineage ledger records")

    seen_runs: dict[str, list[dict[str, Any]]] = {}
    distinct_rows: list[dict[str, Any] | None] = []
    duplicate_record_count = 0

    for item in records:
        if type(item) is not dict or len(item) > 64:
            distinct_rows.append(None)
            continue
        malformed = False
        for k in item.keys():
            if type(k) is not str:
                malformed = True
                break
        if malformed:
            distinct_rows.append(None)
            continue

        norm = normalize_work_cost_row(item)
        if norm is None:
            distinct_rows.append(None)
            continue

        if type(norm["reported_cycle_seconds"]) is float and norm["reported_cycle_seconds"] == 0.0:
            norm["reported_cycle_seconds"] = 0.0
        dims = norm["dimensions"]
        if type(dims.get("finished_at")) is float and dims["finished_at"] == 0.0:
            dims["finished_at"] = 0.0

        rid = norm["run_id"]
        if rid is None:
            distinct_rows.append(norm)
        else:
            if rid not in seen_runs:
                seen_runs[rid] = [norm]
                distinct_rows.append(norm)
            else:
                is_dup = False
                for prev in seen_runs[rid]:
                    if norm == prev:
                        is_dup = True
                        break
                if is_dup:
                    duplicate_record_count += 1
                else:
                    seen_runs[rid].append(norm)
                    distinct_rows.append(norm)

    conflicting_run_ids = {rid for rid, variants in seen_runs.items() if len(variants) > 1}

    all_work_fps: set[str] = set()
    work_metadata: dict[str, set[tuple[str, str, str]]] = {}
    sem_to_works: dict[str, set[str]] = {}

    for row in distinct_rows:
        if row is None:
            continue
        work = row.get("work_lineage")
        sem = row.get("semantic_identity")
        w_fp = work["fingerprint"] if work is not None else None
        s_fp = sem["fingerprint"] if sem is not None else None

        if w_fp is not None:
            all_work_fps.add(w_fp)
            if sem is not None:
                meta = (sem["stage_id"], sem["scenario_key"], sem["gap_reference"])
                work_metadata.setdefault(w_fp, set()).add(meta)

        if s_fp is not None and w_fp is not None:
            sem_to_works.setdefault(s_fp, set()).add(w_fp)

    quarantined_work_fps: set[str] = set()

    for w_fp, metas in work_metadata.items():
        if len(metas) > 1:
            quarantined_work_fps.add(w_fp)

    for rid in conflicting_run_ids:
        for v in seen_runs[rid]:
            w = v.get("work_lineage")
            if w is not None:
                quarantined_work_fps.add(w["fingerprint"])

    for s_fp, w_fps in sem_to_works.items():
        if len(w_fps) > 1:
            for w in w_fps:
                quarantined_work_fps.add(w)

    adj: dict[str, set[str]] = {w: set() for w in all_work_fps}

    for rid in conflicting_run_ids:
        w_fps = {v["work_lineage"]["fingerprint"] for v in seen_runs[rid] if v.get("work_lineage") is not None}
        for w1 in w_fps:
            for w2 in w_fps:
                if w1 != w2:
                    adj[w1].add(w2)
                    adj[w2].add(w1)

    for s_fp, w_fps in sem_to_works.items():
        if len(w_fps) > 1:
            for w1 in w_fps:
                for w2 in w_fps:
                    if w1 != w2:
                        adj[w1].add(w2)
                        adj[w2].add(w1)

    queue = list(quarantined_work_fps)
    while queue:
        curr = queue.pop()
        for neighbor in adj.get(curr, ()):
            if neighbor not in quarantined_work_fps:
                quarantined_work_fps.add(neighbor)
                queue.append(neighbor)

    lineage_buckets: dict[str, list[dict[str, Any]]] = {}
    unattributed_rows: list[dict[str, Any] | None] = []
    conflict_record_count = 0
    malformed_record_count = 0

    for row in distinct_rows:
        if row is None:
            malformed_record_count += 1
            unattributed_rows.append(None)
            continue

        w = row.get("work_lineage")
        w_fp = w["fingerprint"] if w is not None else None
        is_quarantined = w_fp is not None and w_fp in quarantined_work_fps
        is_conflicting_run = row.get("run_id") is not None and row["run_id"] in conflicting_run_ids

        if row.get("group_eligible") is True and not is_quarantined:
            assert w_fp is not None
            lineage_buckets.setdefault(w_fp, []).append(row)
        else:
            unattributed_rows.append(row)
            if is_quarantined or is_conflicting_run:
                conflict_record_count += 1

    lineages: list[dict[str, Any]] = []
    observed_candidate_lineages = 0
    all_grouped_sem_fps: set[str] = set()

    for w_fp in sorted(lineage_buckets.keys()):
        rows = lineage_buckets[w_fp]
        sem_fps = {r["semantic_identity"]["fingerprint"] for r in rows if r.get("semantic_identity")}
        src_shas = {r["semantic_identity"]["source_sha256"] for r in rows if r.get("semantic_identity")}
        cand_count = sum(1 for r in rows if r.get("observed_candidate") is True)
        if cand_count > 0:
            observed_candidate_lineages += 1
        all_grouped_sem_fps.update(sem_fps)
        lineages.append({
            "work_fingerprint": w_fp,
            "semantic_attempt_count": len(sem_fps),
            "source_revision_count": len(src_shas),
            "record_count": len(rows),
            "observed_candidate_count": cand_count,
            "costs": _aggregate_costs(rows),
        })

    return {
        "schema_version": 1,
        "state": "OBSERVATIONAL_ONLY",
        "input_record_count": len(records),
        "distinct_record_count": len(distinct_rows),
        "duplicate_record_count": duplicate_record_count,
        "run_conflict_count": len(conflicting_run_ids),
        "identity_conflict_work_count": len(quarantined_work_fps),
        "logical_work_count": len(lineages),
        "semantic_attempt_count": len(all_grouped_sem_fps),
        "observed_acceptance_candidate_lineage_count": observed_candidate_lineages,
        "costs": _aggregate_costs(distinct_rows),
        "lineages": lineages,
        "unattributed": {
            "record_count": len(unattributed_rows),
            "conflict_record_count": conflict_record_count,
            "malformed_record_count": malformed_record_count,
            "allocation_rule": "UNATTRIBUTED_NOT_ALLOCATED",
            "costs": _aggregate_costs(unattributed_rows),
        },
        "accepted_behavior_count": None,
        "denominator_state": "UNVERIFIED",
        "token_per_accepted_behavior": None,
        "billing_savings": None,
        "value_state": "NOT_ESTABLISHED",
        "cohort_comparability": "NOT_ESTABLISHED",
    }
