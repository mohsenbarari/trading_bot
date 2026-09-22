"""Tracked source bundle extraction with linear physical newline handling."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess

if __package__:
    import scripts.two_site_proposal_runner as runner
else:
    import two_site_proposal_runner as runner

ProposalError = runner.ProposalError

SOURCE_MAX_BYTES = 2000000
INVOCATION_MAX_BYTES = 8000000

_PHYSICAL_NEWLINE_RE = re.compile(r"\r\n|[\r\n]")


def _split_physical_lines(text: str) -> list[str]:
    """Split text into physical lines preserving exact physical newline delimiters.

    Matches physical newlines (CRLF, CR, LF) in a single linear pass without
    normalizing or splitting on Unicode line/paragraph breaks (U+2028, U+2029).
    """
    if not text:
        return []
    lines: list[str] = []
    start = 0
    for match in _PHYSICAL_NEWLINE_RE.finditer(text):
        end = match.end()
        lines.append(text[start:end])
        start = end
    if start < len(text):
        lines.append(text[start:])
    return lines


def extract_bundle(root: Path, requests: list[dict]) -> tuple[list[dict], dict]:
    """Extract top-level AST symbols and ranges from tracked project sources within 2M/8M limits."""
    if not isinstance(requests, list):
        raise ProposalError("requests must be a list")
    if len(requests) > 32:
        raise ProposalError("requests cannot exceed 32 items")

    validated: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for req in requests:
        if not isinstance(req, dict) or set(req.keys()) != {"path", "symbols"}:
            raise ProposalError("request must be a dict with exactly 'path' and 'symbols'")
        path_name = req["path"]
        if not isinstance(path_name, str):
            raise ProposalError("request path must be a string")
        if path_name in seen:
            raise ProposalError(f"duplicate excerpt source path: {path_name}")
        seen.add(path_name)

        symbols = req["symbols"]
        if (
            not isinstance(symbols, list)
            or not 1 <= len(symbols) <= 32
            or any(not isinstance(s, str) or not s.isidentifier() for s in symbols)
            or len(set(symbols)) != len(symbols)
        ):
            raise ProposalError("excerpt needs unique Python top-level symbol names")
        validated.append((path_name, symbols))

    records: list[dict] = []
    total_bytes = 0

    for path_name, symbols in validated:
        remaining = INVOCATION_MAX_BYTES - total_bytes
        if remaining <= 0:
            raise ProposalError("invocation context budget exceeded")

        read_limit = min(SOURCE_MAX_BYTES, remaining)

        target = runner.relative_file(root, path_name)
        if target.suffix != ".py":
            raise ProposalError("excerpt source must be a .py file")

        try:
            runner.git(root, "ls-files", "--error-unmatch", "--", path_name)
        except subprocess.CalledProcessError as exc:
            raise ProposalError(f"untracked excerpt source: {path_name}") from exc

        data = runner.bounded_file_bytes(root, target, read_limit, "excerpt source")
        raw_len = len(data)
        if raw_len > SOURCE_MAX_BYTES or total_bytes + raw_len > INVOCATION_MAX_BYTES:
            raise ProposalError("source byte budget exceeded")
        total_bytes += raw_len

        text = data.decode("utf-8")
        if runner.SECRET.search(text):
            raise ProposalError("credential-like excerpt source")

        tree = ast.parse(text)
        lines = _split_physical_lines(text)
        selected: list[dict] = []
        found: set[str] = set()

        for node in tree.body:
            names = (
                {node.name}
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                else set()
            )
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {
                    child.id
                    for target in targets
                    for child in ast.walk(target)
                    if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store)
                }
            wanted = names & set(symbols)
            if not wanted and not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            found.update(wanted)
            start = min([node.lineno] + [x.lineno for x in getattr(node, "decorator_list", [])])
            end = node.end_lineno
            selected.append({"start": start, "end": end, "text": "".join(lines[start - 1 : end])})

        if found != set(symbols):
            missing = sorted(set(symbols) - found)
            raise ProposalError("missing top-level excerpt symbols: " + ",".join(missing))

        records.append({
            "path": path_name,
            "sha256": runner.sha(data),
            "symbols": symbols,
            "ranges": selected,
        })

    report = {
        "source_count": len(records),
        "source_bytes": total_bytes,
        "max_source_bytes": SOURCE_MAX_BYTES,
        "max_total_bytes": INVOCATION_MAX_BYTES,
    }
    return records, report
