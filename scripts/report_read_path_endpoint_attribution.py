#!/usr/bin/env python3
"""Build endpoint-family attribution reports from production load artifacts."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tmp" / "production-read-path-attribution"
DEFAULT_ARTIFACTS = (
    REPO_ROOT / "tmp/production-benchmark/20260613T111706Z/load-pool-matrix",
    REPO_ROOT / "tmp/production-benchmark/20260613T160851Z/load-pool-matrix",
)
ENDPOINT_METRIC_PREFIX = "stage_l_endpoint_"
ENDPOINT_DURATION_SUFFIX = "_duration"
ENDPOINT_FAILED_SUFFIX = "_failed"


@dataclass(frozen=True)
class EndpointAttribution:
    endpoint: str
    request_count: float
    successful_requests: float
    failed_requests: float
    failure_rate: float
    avg_ms: float | None
    p50_ms: float | None
    p90_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    max_ms: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "request_count": self.request_count,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "failure_rate": self.failure_rate,
            "avg_ms": self.avg_ms,
            "p50_ms": self.p50_ms,
            "p90_ms": self.p90_ms,
            "p95_ms": self.p95_ms,
            "p99_ms": self.p99_ms,
            "max_ms": self.max_ms,
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Extract endpoint-family p50/p95/p99 attribution from Stage L/RPL "
            "production load artifacts."
        )
    )
    parser.add_argument(
        "artifacts",
        nargs="*",
        type=Path,
        default=list(DEFAULT_ARTIFACTS),
        help="Artifact directories. Supports load-pool-matrix or load-realistic directories.",
    )
    parser.add_argument("--json", action="store_true", help="Emit the JSON report to stdout.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=20, help="Maximum endpoint rows per run.")
    return parser.parse_args(argv)


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def endpoint_rows_from_k6_summary(k6_summary_path: Path, *, limit: int) -> list[EndpointAttribution]:
    metrics = load_json(k6_summary_path).get("metrics") or {}
    rows: list[EndpointAttribution] = []
    for metric_name, duration in metrics.items():
        if not metric_name.startswith(ENDPOINT_METRIC_PREFIX) or not metric_name.endswith(ENDPOINT_DURATION_SUFFIX):
            continue
        endpoint = metric_name[len(ENDPOINT_METRIC_PREFIX):-len(ENDPOINT_DURATION_SUFFIX)]
        failed = metrics.get(f"{ENDPOINT_METRIC_PREFIX}{endpoint}{ENDPOINT_FAILED_SUFFIX}") or {}
        failed_requests = as_float(failed.get("passes"))
        successful_requests = as_float(failed.get("fails"))
        rows.append(
            EndpointAttribution(
                endpoint=endpoint,
                request_count=successful_requests + failed_requests,
                successful_requests=successful_requests,
                failed_requests=failed_requests,
                failure_rate=as_float(failed.get("value")),
                avg_ms=maybe_float(duration.get("avg")),
                p50_ms=maybe_float(duration.get("med") if duration.get("med") is not None else duration.get("p(50)")),
                p90_ms=maybe_float(duration.get("p(90)")),
                p95_ms=maybe_float(duration.get("p(95)")),
                p99_ms=maybe_float(duration.get("p(99)")),
                max_ms=maybe_float(duration.get("max")),
            )
        )
    rows.sort(
        key=lambda item: (
            item.p95_ms or 0,
            item.p99_ms or 0,
            item.avg_ms or 0,
        ),
        reverse=True,
    )
    return rows[:limit]


def find_k6_summary_for_candidate(candidate_summary: dict[str, Any]) -> Path | None:
    artifact_dir = candidate_summary.get("artifact_dir")
    if not artifact_dir:
        return None
    path = (REPO_ROOT / artifact_dir) if not Path(str(artifact_dir)).is_absolute() else Path(str(artifact_dir))
    k6_summary = path / "k6-summary.json"
    return k6_summary if k6_summary.exists() else None


def endpoint_rows_from_candidate_summary(candidate_summary: dict[str, Any], *, limit: int) -> list[EndpointAttribution]:
    k6_summary_path = find_k6_summary_for_candidate(candidate_summary)
    if k6_summary_path is not None:
        return endpoint_rows_from_k6_summary(k6_summary_path, limit=limit)

    rows: list[EndpointAttribution] = []
    for item in candidate_summary.get("endpoint_breakdown") or []:
        failed_requests = as_float(item.get("failed_requests"))
        successful_requests = as_float(item.get("successful_requests"))
        rows.append(
            EndpointAttribution(
                endpoint=str(item.get("endpoint") or ""),
                request_count=successful_requests + failed_requests,
                successful_requests=successful_requests,
                failed_requests=failed_requests,
                failure_rate=as_float(item.get("failure_rate")),
                avg_ms=maybe_float(item.get("avg_ms")),
                p50_ms=maybe_float(item.get("p50_ms")),
                p90_ms=maybe_float(item.get("p90_ms")),
                p95_ms=maybe_float(item.get("p95_ms")),
                p99_ms=maybe_float(item.get("p99_ms")),
                max_ms=maybe_float(item.get("max_ms")),
            )
        )
    rows.sort(
        key=lambda item: (
            item.p95_ms or 0,
            item.p99_ms or 0,
            item.avg_ms or 0,
        ),
        reverse=True,
    )
    return rows[:limit]


def summarize_k6(candidate_summary: dict[str, Any]) -> dict[str, Any]:
    k6 = candidate_summary.get("k6") or {}
    return {
        "http_reqs": k6.get("http_reqs"),
        "rps": k6.get("rps"),
        "failure_rate": k6.get("failure_rate"),
        "failed_requests": k6.get("failed_requests"),
        "check_rate": k6.get("check_rate"),
        "dropped_iterations": k6.get("dropped_iterations"),
        "dropped_iteration_ratio": k6.get("dropped_iteration_ratio"),
        "p95_ms": k6.get("p95_ms"),
        "p99_ms": k6.get("p99_ms"),
        "avg_ms": k6.get("avg_ms"),
    }


def build_run_entry(
    *,
    artifact: Path,
    label: str,
    candidate: str,
    candidate_summary: dict[str, Any],
    limit: int,
) -> dict[str, Any]:
    sampler = candidate_summary.get("sampler") or {}
    return {
        "label": label,
        "artifact": display_path(artifact),
        "candidate": candidate,
        "status": candidate_summary.get("status"),
        "load_artifact": candidate_summary.get("artifact_dir"),
        "results_path": candidate_summary.get("results_path"),
        "k6": summarize_k6(candidate_summary),
        "sampler": {
            "max_postgres_connections": sampler.get("max_postgres_connections"),
            "max_nginx_5xx_delta_from_first_sample": sampler.get("max_nginx_5xx_delta_from_first_sample"),
            "max_sync_backlog": sampler.get("max_sync_backlog"),
        },
        "endpoints": [row.as_dict() for row in endpoint_rows_from_candidate_summary(candidate_summary, limit=limit)],
    }


def load_artifact_runs(artifact: Path, *, index: int, limit: int) -> list[dict[str, Any]]:
    artifact = artifact.resolve()
    if artifact.is_dir() and (artifact / "results.json").exists():
        results_path = artifact / "results.json"
    elif artifact.is_file():
        results_path = artifact
        artifact = artifact.parent
    else:
        raise FileNotFoundError(f"artifact results.json not found: {artifact}")

    payload = load_json(results_path)
    label = artifact.name
    runs: list[dict[str, Any]] = []
    if isinstance(payload.get("candidates"), list):
        for candidate_item in payload["candidates"]:
            candidate_summary = candidate_item.get("summary") or {}
            runs.append(
                build_run_entry(
                    artifact=artifact,
                    label=label,
                    candidate=str(candidate_item.get("candidate") or candidate_item.get("candidate_stamp") or "candidate"),
                    candidate_summary=candidate_summary,
                    limit=limit,
                )
            )
        return runs

    k6_path = artifact / "k6-summary.json"
    if not k6_path.exists():
        raise FileNotFoundError(f"k6-summary.json not found for non-matrix artifact: {artifact}")
    synthetic_summary = {
        "status": payload.get("status"),
        "artifact_dir": display_path(artifact),
        "results_path": display_path(results_path),
        "k6": {},
        "sampler": {},
    }
    runs.append(
        {
            "label": label or f"artifact_{index + 1}",
            "artifact": display_path(artifact),
            "candidate": "single-run",
            "status": payload.get("status"),
            "load_artifact": display_path(artifact),
            "results_path": display_path(results_path),
            "k6": synthetic_summary["k6"],
            "sampler": synthetic_summary["sampler"],
            "endpoints": [row.as_dict() for row in endpoint_rows_from_k6_summary(k6_path, limit=limit)],
        }
    )
    return runs


def compare_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(runs) < 2:
        return []
    baseline = runs[0]
    baseline_rows = {row["endpoint"]: row for row in baseline.get("endpoints") or []}
    comparisons: list[dict[str, Any]] = []
    for run in runs[1:]:
        for row in run.get("endpoints") or []:
            endpoint = row["endpoint"]
            base = baseline_rows.get(endpoint)
            if not base:
                continue
            comparisons.append(
                {
                    "endpoint": endpoint,
                    "baseline_label": baseline["label"],
                    "candidate_label": run["label"],
                    "baseline_p95_ms": base.get("p95_ms"),
                    "candidate_p95_ms": row.get("p95_ms"),
                    "p95_delta_ms": (
                        None
                        if base.get("p95_ms") is None or row.get("p95_ms") is None
                        else row["p95_ms"] - base["p95_ms"]
                    ),
                    "baseline_p99_ms": base.get("p99_ms"),
                    "candidate_p99_ms": row.get("p99_ms"),
                    "p99_delta_ms": (
                        None
                        if base.get("p99_ms") is None or row.get("p99_ms") is None
                        else row["p99_ms"] - base["p99_ms"]
                    ),
                    "baseline_request_count": base.get("request_count"),
                    "candidate_request_count": row.get("request_count"),
                    "candidate_failure_rate": row.get("failure_rate"),
                }
            )
    comparisons.sort(
        key=lambda item: (
            item["candidate_p95_ms"] or 0,
            item["candidate_p99_ms"] or 0,
        ),
        reverse=True,
    )
    return comparisons


def build_report(artifacts: list[Path], *, limit: int) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts):
        runs.extend(load_artifact_runs(artifact, index=index, limit=limit))
    return {
        "generated_at": utc_iso(),
        "run_count": len(runs),
        "runs": runs,
        "comparisons": compare_runs(runs),
    }


def format_ms(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}ms"


def format_count(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.0f}"


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Production Read Path Endpoint Attribution",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Run count: `{report['run_count']}`",
        "",
    ]
    for run in report["runs"]:
        k6 = run.get("k6") or {}
        lines.extend(
            [
                f"## {run['label']} - {run['candidate']}",
                "",
                f"- Artifact: `{run['artifact']}`",
                f"- Load artifact: `{run.get('load_artifact')}`",
                f"- Status: `{run.get('status')}`",
                f"- RPS: `{k6.get('rps', 'n/a')}`",
                f"- Overall p95/p99: `{format_ms(k6.get('p95_ms'))}` / `{format_ms(k6.get('p99_ms'))}`",
                "",
                "| Endpoint | Requests | Failure | P50 | P95 | P99 | Avg | Max |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in run.get("endpoints") or []:
            lines.append(
                f"| `{row['endpoint']}` | `{format_count(row.get('request_count'))}` | "
                f"`{float(row.get('failure_rate') or 0) * 100:.3f}%` | "
                f"`{format_ms(row.get('p50_ms'))}` | `{format_ms(row.get('p95_ms'))}` | "
                f"`{format_ms(row.get('p99_ms'))}` | `{format_ms(row.get('avg_ms'))}` | "
                f"`{format_ms(row.get('max_ms'))}` |"
            )
        lines.append("")

    if report.get("comparisons"):
        lines.extend(
            [
                "## Comparison Against First Run",
                "",
                "| Endpoint | Baseline p95 | Candidate p95 | Delta p95 | Baseline p99 | Candidate p99 | Delta p99 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for item in report["comparisons"]:
            lines.append(
                f"| `{item['endpoint']}` | `{format_ms(item.get('baseline_p95_ms'))}` | "
                f"`{format_ms(item.get('candidate_p95_ms'))}` | `{format_ms(item.get('p95_delta_ms'))}` | "
                f"`{format_ms(item.get('baseline_p99_ms'))}` | `{format_ms(item.get('candidate_p99_ms'))}` | "
                f"`{format_ms(item.get('p99_delta_ms'))}` |"
            )
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report([Path(item) for item in args.artifacts], limit=args.limit)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    json_path = args.output_dir / f"read-path-endpoint-attribution-{stamp}.json"
    md_path = args.output_dir / f"read-path-endpoint-attribution-{stamp}.md"
    write_json(json_path, report)
    write_markdown(md_path, report)
    if args.json:
        print(json.dumps({**report, "json_path": display_path(json_path), "markdown_path": display_path(md_path)}, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Wrote {display_path(json_path)}")
        print(f"Wrote {display_path(md_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
