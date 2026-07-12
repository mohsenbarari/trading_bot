#!/usr/bin/env python3
"""Fail closed when changed backend/frontend executable paths are not covered."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from coverage import Coverage
from coverage.parser import PythonParser


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUSIONS = REPO_ROOT / "config/stage9_coverage_exclusions.json"
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def changed_lines_from_diff(diff_text: str) -> dict[str, set[int]]:
    changed: dict[str, set[int]] = defaultdict(set)
    current_path: str | None = None
    new_line = 0
    for raw_line in diff_text.splitlines():
        if raw_line.startswith("+++ b/"):
            current_path = raw_line[6:]
            continue
        match = HUNK_RE.match(raw_line)
        if match:
            new_line = int(match.group(1))
            continue
        if current_path is None or raw_line.startswith(("--- ", "+++ ", "@@")):
            continue
        if raw_line.startswith("+"):
            changed[current_path].add(new_line)
            new_line += 1
        elif raw_line.startswith("-"):
            continue
        else:
            new_line += 1
    return dict(changed)


def git_diff(base_ref: str, head_ref: str, pathspec: str) -> str:
    branch_point = subprocess.check_output(
        ["git", "merge-base", base_ref, head_ref],
        cwd=REPO_ROOT,
        text=True,
    ).strip()
    result = subprocess.run(
        ["git", "diff", "--unified=0", "--no-ext-diff", branch_point, head_ref, "--", pathspec],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def _normalized_coverage_path(raw_path: str) -> str:
    path = raw_path.replace("\\", "/")
    root = str(REPO_ROOT).replace("\\", "/")
    if path.startswith(root + "/"):
        return path[len(root) + 1 :]
    marker = path.find("/frontend/src/")
    if marker >= 0:
        return path[marker + 1 :]
    return path.lstrip("./")


def _location_lines(location: object) -> set[int]:
    if not isinstance(location, dict):
        return set()
    start = location.get("start")
    end = location.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return set()
    try:
        first = int(start["line"])
        last = int(end["line"])
    except (KeyError, TypeError, ValueError):
        return set()
    return set(range(first, last + 1))


def _python_executable_lines(path: Path) -> set[int]:
    coverage_config = Coverage(config_file=str(REPO_ROOT / ".coveragerc"))
    exclude = "|".join(
        f"(?:{pattern})" for pattern in coverage_config.get_exclude_list("exclude")
    )
    parser = PythonParser(filename=str(path), exclude=exclude)
    parser.parse_source()
    return set(parser.statements)


def _validated_exclusions(payload: dict[str, object]) -> dict[str, dict[int, str]]:
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported_coverage_exclusion_schema")
    result: dict[str, dict[int, str]] = {}
    for entry in payload.get("exclusions", []):
        if not isinstance(entry, dict):
            raise ValueError("invalid_coverage_exclusion")
        path = str(entry.get("path", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        lines = entry.get("lines")
        if not path or not reason or not isinstance(lines, list) or not lines:
            raise ValueError("incomplete_coverage_exclusion")
        normalized = {int(line): reason for line in lines}
        if any(line <= 0 for line in normalized):
            raise ValueError(f"invalid_coverage_exclusion_line:{path}")
        if path in result:
            raise ValueError(f"duplicate_coverage_exclusion_path:{path}")
        result[path] = normalized
    return result


def check_frontend(
    changed: dict[str, set[int]],
    coverage_payload: dict[str, object],
    exclusions: dict[str, dict[int, str]] | None = None,
) -> dict[str, object]:
    files = {
        _normalized_coverage_path(path): data
        for path, data in coverage_payload.items()
        if isinstance(data, dict)
    }
    report: dict[str, object] = {"kind": "frontend-v8", "files": {}, "failures": []}
    failures: list[str] = report["failures"]  # type: ignore[assignment]
    exclusions = exclusions or {}

    for path, lines in sorted(changed.items()):
        if not path.startswith("frontend/src/"):
            continue
        if ".test." in path or ".spec." in path or "/__tests__/" in path:
            continue
        data = files.get(path)
        if data is None:
            failures.append(f"coverage_file_missing:{path}")
            continue
        file_report = {metric: {"total": 0, "covered": 0, "uncovered": []} for metric in ("lines", "statements", "branches", "functions")}
        file_report["excluded"] = []
        mapped_lines: set[int] = set()

        statements = data.get("statementMap", {})
        statement_counts = data.get("s", {})
        line_counts: dict[int, int] = defaultdict(int)
        if isinstance(statements, dict) and isinstance(statement_counts, dict):
            for key, location in statements.items():
                location_lines = _location_lines(location)
                if not lines.intersection(location_lines):
                    continue
                mapped_lines.update(lines.intersection(location_lines))
                count = int(statement_counts.get(key, 0) or 0)
                item = file_report["statements"]
                item["total"] += 1
                if count > 0:
                    item["covered"] += 1
                else:
                    item["uncovered"].append(str(key))
                for mapped_line in lines.intersection(location_lines):
                    line_counts[mapped_line] = max(line_counts[mapped_line], count)

        for line in sorted(lines.intersection(line_counts)):
            item = file_report["lines"]
            item["total"] += 1
            if line_counts[line] > 0:
                item["covered"] += 1
            else:
                item["uncovered"].append(line)

        branches = data.get("branchMap", {})
        branch_counts = data.get("b", {})
        if isinstance(branches, dict) and isinstance(branch_counts, dict):
            for key, branch in branches.items():
                if not isinstance(branch, dict):
                    continue
                locations = branch.get("locations", [])
                counts = branch_counts.get(key, [])
                if not isinstance(locations, list) or not isinstance(counts, list):
                    continue
                for index, location in enumerate(locations):
                    if not lines.intersection(_location_lines(location)):
                        continue
                    mapped_lines.update(lines.intersection(_location_lines(location)))
                    count = int(counts[index] if index < len(counts) else 0)
                    item = file_report["branches"]
                    item["total"] += 1
                    if count > 0:
                        item["covered"] += 1
                    else:
                        item["uncovered"].append(f"{key}:{index}")

        functions = data.get("fnMap", {})
        function_counts = data.get("f", {})
        if isinstance(functions, dict) and isinstance(function_counts, dict):
            for key, function in functions.items():
                if not isinstance(function, dict):
                    continue
                location = function.get("loc") or function.get("decl")
                if not lines.intersection(_location_lines(location)):
                    continue
                count = int(function_counts.get(key, 0) or 0)
                item = file_report["functions"]
                item["total"] += 1
                if count > 0:
                    item["covered"] += 1
                else:
                    item["uncovered"].append(str(key))

        unmapped = lines - mapped_lines
        allowed = exclusions.get(path, {})
        unexplained = sorted(line for line in unmapped if line not in allowed)
        stale_exclusions = sorted(line for line in allowed if line not in unmapped)
        file_report["excluded"] = [
            {"line": line, "reason": allowed[line]} for line in sorted(unmapped & set(allowed))
        ]
        if unexplained:
            failures.append(f"coverage_map_missing:{path}:{','.join(map(str, unexplained))}")
        if stale_exclusions:
            failures.append(f"stale_coverage_exclusion:{path}:{','.join(map(str, stale_exclusions))}")
        for metric, item in file_report.items():
            if metric == "excluded":
                continue
            if item["uncovered"]:
                failures.append(f"uncovered_{metric}:{path}:{','.join(map(str, item['uncovered']))}")
        report["files"][path] = file_report  # type: ignore[index]
    report["passed"] = not failures
    return report


def check_backend(
    changed: dict[str, set[int]],
    xml_path: Path,
    exclusions: dict[str, dict[int, str]] | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {"kind": "backend-coverage-py", "files": {}, "failures": []}
    failures: list[str] = report["failures"]  # type: ignore[assignment]
    exclusions = exclusions or {}
    coverage_files: dict[str, ET.Element] = {}
    root = ET.parse(xml_path).getroot()
    for class_node in root.findall(".//class"):
        filename = str(class_node.attrib.get("filename", "")).lstrip("./")
        coverage_files[filename] = class_node

    backend_prefixes = ("api/", "bot/", "core/", "models/", "scripts/")
    for path, lines in sorted(changed.items()):
        if not path.endswith(".py") or not (
            path.startswith(backend_prefixes)
            or path in {"main.py", "manage.py", "run_bot.py", "schemas.py"}
        ):
            continue
        source_path = REPO_ROOT / path
        executable_changed = lines.intersection(_python_executable_lines(source_path))
        allowed = exclusions.get(path, {})
        non_executable_changed = lines - executable_changed
        stale_exclusions = sorted(line for line in allowed if line not in non_executable_changed)
        if stale_exclusions:
            failures.append(f"stale_coverage_exclusion:{path}:{','.join(map(str, stale_exclusions))}")
        class_node = coverage_files.get(path)
        if class_node is None:
            if executable_changed:
                failures.append(f"coverage_file_missing:{path}")
            continue
        changed_nodes = [
            node
            for node in class_node.findall("./lines/line")
            if int(node.attrib.get("number", "0")) in executable_changed
        ]
        mapped_lines = {int(node.attrib["number"]) for node in changed_nodes}
        unmapped_lines = sorted(executable_changed - mapped_lines)
        uncovered_lines: list[int] = []
        uncovered_branches: list[int] = []
        for node in changed_nodes:
            line = int(node.attrib["number"])
            if int(node.attrib.get("hits", "0")) <= 0:
                uncovered_lines.append(line)
            if node.attrib.get("branch") == "true":
                condition = node.attrib.get("condition-coverage", "")
                if not condition.startswith("100%"):
                    uncovered_branches.append(line)
        if uncovered_lines:
            failures.append(f"uncovered_lines:{path}:{','.join(map(str, uncovered_lines))}")
        if uncovered_branches:
            failures.append(f"uncovered_branches:{path}:{','.join(map(str, uncovered_branches))}")
        if unmapped_lines:
            failures.append(f"coverage_map_missing:{path}:{','.join(map(str, unmapped_lines))}")
        report["files"][path] = {  # type: ignore[index]
            "changed_executable_lines": len(changed_nodes),
            "expected_executable_lines": len(executable_changed),
            "unmapped_lines": unmapped_lines,
            "excluded": [
                {"line": line, "reason": allowed[line]}
                for line in sorted(non_executable_changed & set(allowed))
            ],
            "uncovered_lines": uncovered_lines,
            "uncovered_branches": uncovered_branches,
        }
    report["passed"] = not failures
    return report


def _load_diff(args: argparse.Namespace, pathspec: str) -> dict[str, set[int]]:
    if args.diff_file:
        return changed_lines_from_diff(Path(args.diff_file).read_text(encoding="utf-8"))
    return changed_lines_from_diff(git_diff(args.base_ref, args.head_ref, pathspec))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("backend", "frontend"))
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--diff-file")
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--exclusions", default=str(DEFAULT_EXCLUSIONS))
    args = parser.parse_args(argv)

    exclusions = _validated_exclusions(
        json.loads(Path(args.exclusions).read_text(encoding="utf-8"))
    )
    if args.kind == "frontend":
        changed = _load_diff(args, "frontend/src")
        payload = json.loads(Path(args.coverage).read_text(encoding="utf-8"))
        report = check_frontend(changed, payload, exclusions)
    else:
        changed = _load_diff(args, ".")
        report = check_backend(changed, Path(args.coverage), exclusions)
    report.update({"base_ref": args.base_ref, "head_ref": args.head_ref})
    report["commit"] = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
    ).strip()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not report["passed"]:
        print("\n".join(report["failures"]), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
