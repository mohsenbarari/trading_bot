"""Pure bounded presentation formatter for canonical inventory evidence."""
from __future__ import annotations

import json

_ALLOWED_STATES = {
    "PROVEN_FOR_REQUESTED_SCENARIO",
    "PARTIAL_OR_SEED_ONLY",
    "UNKNOWN",
}


def valid_request(behavior_id: object, offset: object, limit: object) -> bool:
    """Validate request types and scalar bounds before invoking operations."""
    if behavior_id is not None:
        if type(behavior_id) is not str:
            return False
        if not (1 <= len(behavior_id) <= 32768):
            return False
    if type(offset) is not int or isinstance(offset, bool):
        return False
    if not (0 <= offset < 2**63):
        return False
    if type(limit) is not int or isinstance(limit, bool):
        return False
    if not (1 <= limit <= 128):
        return False
    return True


def error_report(reason: object) -> dict:
    """Render a bounded error report envelope with fixed reason constraints."""
    if type(reason) is not str:
        clean_reason = "unexpected error"
        truncated = False
    else:
        truncated = len(reason) > 512
        clean_reason = reason[:512]
    report: dict[str, object] = {
        "schema_version": 1,
        "display_only": True,
        "status": "INVENTORY_INCONSISTENT",
        "reason": clean_reason,
    }
    if truncated:
        report["reason_truncated"] = True
    return report


def format_inventory(
    raw: object,
    behavior_id: str | None = None,
    *,
    offset: int = 0,
    limit: int = 64,
) -> dict:
    """Format canonical inventory evidence into a bounded, lossless page."""
    if not valid_request(behavior_id, offset, limit):
        return error_report("invalid request parameters")

    if type(raw) is not dict:
        return error_report("raw envelope must be an exact dict")

    if "status" not in raw:
        return error_report("missing status in envelope")
    status = raw["status"]
    if type(status) is not str:
        return error_report("status must be an exact string")

    if status == "INVENTORY_INCONSISTENT":
        raw_reason = raw.get("reason", "inventory inconsistent")
        if type(raw_reason) is not str:
            raw_reason = "inventory inconsistent"
        rep = error_report(raw_reason)
        if raw.get("reason_truncated") is True:
            rep["reason_truncated"] = True
        return rep

    if status != "VALID":
        return error_report("unrecognized envelope status")

    raw_keys = set(raw.keys())
    if behavior_id is None:
        if raw_keys != {"status", "expected_seed_count", "seed_count", "coverage"}:
            return error_report("invalid envelope keys for full view")
    else:
        if raw_keys != {"status", "expected_seed_count", "seed_count", "coverage", "requested_coverage"}:
            return error_report("invalid envelope keys for targeted view")

    expected = raw["expected_seed_count"]
    seed_count = raw["seed_count"]
    if type(expected) is not int or isinstance(expected, bool) or not (0 <= expected < 2**63):
        return error_report("invalid expected_seed_count")
    if type(seed_count) is not int or isinstance(seed_count, bool) or not (0 <= seed_count < 2**63):
        return error_report("invalid seed_count")
    if expected != seed_count:
        return error_report("mismatched seed counts")

    coverage = raw["coverage"]
    if type(coverage) is not dict:
        return error_report("coverage must be an exact dict")

    for k, v in coverage.items():
        if type(k) is not str:
            return error_report("coverage behavior ID must be an exact string")
        if not (1 <= len(k) <= 32768):
            return error_report("coverage behavior ID length must be between 1 and 32768")
        if type(v) is not str:
            return error_report("coverage status must be an exact string")
        if v not in _ALLOWED_STATES:
            return error_report("coverage status not recognized")

    if behavior_id is None:
        if len(coverage) != seed_count:
            return error_report("full view coverage count does not match seed count")
    else:
        req_cov = raw["requested_coverage"]
        if type(req_cov) is not str:
            return error_report("requested_coverage must be an exact string")
        if req_cov == "NO_MATCHING_EVIDENCE":
            if len(coverage) != 0:
                return error_report("targeted coverage must be empty for NO_MATCHING_EVIDENCE")
        elif req_cov in _ALLOWED_STATES:
            if seed_count < 1:
                return error_report("seed count must be >= 1 for matching evidence")
            if len(coverage) != 1 or behavior_id not in coverage or coverage[behavior_id] != req_cov:
                return error_report("targeted coverage does not match requested coverage")
        else:
            return error_report("unrecognized requested_coverage value")

    sorted_ids = sorted(coverage.keys())
    total_entries = len(sorted_ids)

    if offset >= total_entries:
        report = {
            "schema_version": 1,
            "display_only": True,
            "status": "VALID",
            "expected_seed_count": expected,
            "seed_count": seed_count,
            "total_entries": total_entries,
            "offset": offset,
            "returned_entries": 0,
            "omitted_entries": total_entries,
            "next_offset": None,
            "coverage": {},
        }
        if behavior_id is not None:
            report["requested_coverage"] = raw["requested_coverage"]
        serialized = (json.dumps(report) + "\n").encode("utf-8")
        if len(serialized) > 32768:
            return error_report("serialized report exceeds byte budget")
        return report

    page_coverage: dict[str, str] = {}
    returned_count = 0
    max_candidates = min(limit, total_entries - offset)

    for i in range(max_candidates):
        next_id = sorted_ids[offset + i]
        cand_coverage = dict(page_coverage)
        cand_coverage[next_id] = coverage[next_id]
        cand_returned = returned_count + 1
        cand_omitted = total_entries - cand_returned
        cand_next = (offset + cand_returned) if (offset + cand_returned < total_entries) else None
        cand_report = {
            "schema_version": 1,
            "display_only": True,
            "status": "VALID",
            "expected_seed_count": expected,
            "seed_count": seed_count,
            "total_entries": total_entries,
            "offset": offset,
            "returned_entries": cand_returned,
            "omitted_entries": cand_omitted,
            "next_offset": cand_next,
            "coverage": cand_coverage,
        }
        if behavior_id is not None:
            cand_report["requested_coverage"] = raw["requested_coverage"]
        cand_serialized = (json.dumps(cand_report) + "\n").encode("utf-8")
        if len(cand_serialized) <= 32768:
            page_coverage = cand_coverage
            returned_count = cand_returned
        else:
            if returned_count == 0:
                return error_report("first pending entry exceeds byte budget")
            break

    final_omitted = total_entries - returned_count
    final_next = (offset + returned_count) if (offset + returned_count < total_entries) else None
    final_report = {
        "schema_version": 1,
        "display_only": True,
        "status": "VALID",
        "expected_seed_count": expected,
        "seed_count": seed_count,
        "total_entries": total_entries,
        "offset": offset,
        "returned_entries": returned_count,
        "omitted_entries": final_omitted,
        "next_offset": final_next,
        "coverage": page_coverage,
    }
    if behavior_id is not None:
        final_report["requested_coverage"] = raw["requested_coverage"]

    final_serialized = (json.dumps(final_report) + "\n").encode("utf-8")
    if len(final_serialized) > 32768:
        return error_report("serialized report exceeds byte budget")
    return final_report
