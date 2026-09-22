from __future__ import annotations

from scripts.two_site_proposal_runner import ProposalError


def _collect_session_readiness_facts(
    dependency_context: object,
) -> list[dict[str, str | None]]:
    if dependency_context is None:
        return []
    if not isinstance(dependency_context, dict) or type(dependency_context) is not dict:
        raise ProposalError("invalid dependency context")

    session_context = dependency_context.get("session_context")
    if session_context is None:
        return []
    if not isinstance(session_context, dict) or type(session_context) is not dict:
        raise ProposalError("invalid session context")
    if not session_context.get("requested"):
        return []

    raw_facts: list[dict[str, str | None]] = []

    if not session_context.get("adapter_symbols"):
        raw_facts.append({"code": "SESSION_ADAPTER_MISSING", "path": None})

    if not session_context.get("root_symbols"):
        raw_facts.append({"code": "SESSION_ENTRYPOINT_MISSING", "path": None})

    omitted_counts = (
        session_context.get("omitted_required_symbol_count", 0),
        session_context.get("omitted_missing_symbol_count", 0),
        session_context.get("omitted_query_operation_count", 0),
        session_context.get("omitted_unresolved_symbol_count", 0),
    )
    if any(type(v) is not int or isinstance(v, bool) or v < 0 for v in omitted_counts):
        raw_facts.append({"code": "SESSION_CONTEXT_INVALID", "path": None})
    elif any(v > 0 for v in omitted_counts):
        raw_facts.append({"code": "SESSION_CONTEXT_LIMIT_EXCEEDED", "path": None})

    unresolved = session_context.get("unresolved_symbols", [])
    if not isinstance(unresolved, list) or type(unresolved) is not list:
        raise ProposalError("unresolved_symbols must be a list")

    missing = session_context.get("missing_symbols", [])
    if not isinstance(missing, list) or type(missing) is not list:
        raise ProposalError("missing_symbols must be a list")

    for item in unresolved:
        if isinstance(item, str):
            raw_facts.append({"code": "SESSION_CONTEXT_MISSING", "path": None})
    for item in missing:
        if isinstance(item, str):
            raw_facts.append({"code": "SESSION_CONTEXT_MISSING", "path": None})

    if not session_context.get("query_operations"):
        raw_facts.append({"code": "SESSION_QUERY_CONTRACT_MISSING", "path": None})

    seen: set[tuple[str, str | None]] = set()
    unique_facts: list[dict[str, str | None]] = []
    for fact in raw_facts:
        key = (fact["code"], fact["path"])
        if key not in seen:
            seen.add(key)
            unique_facts.append({"code": fact["code"], "path": fact["path"]})

    unique_facts.sort(
        key=lambda item: (
            item["code"],
            0 if item["path"] is None else 1,
            item["path"] or "",
        )
    )
    return unique_facts


_SESSION_ISSUE_DETAILS: dict[str, str] = {
    "SESSION_ADAPTER_MISSING": "session adapter is missing",
    "SESSION_ENTRYPOINT_MISSING": "session entrypoint is missing",
    "SESSION_CONTEXT_INVALID": "session context is invalid",
    "SESSION_CONTEXT_LIMIT_EXCEEDED": "session context limit exceeded",
    "SESSION_CONTEXT_MISSING": "session context is missing",
    "SESSION_QUERY_CONTRACT_MISSING": "session query contract is missing",
}


def _collect_session_readiness_issues(
    dependency_context: object,
) -> list[dict[str, str]]:
    raw_facts = _collect_session_readiness_facts(dependency_context)
    issues: list[dict[str, str]] = []
    for fact in raw_facts:
        code = fact.get("code") if isinstance(fact, dict) else None
        if not isinstance(code, str) or code not in _SESSION_ISSUE_DETAILS:
            raise ProposalError("unknown session readiness code")
        issues.append({"code": code, "detail": _SESSION_ISSUE_DETAILS[code]})
    return issues
