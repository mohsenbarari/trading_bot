#!/usr/bin/env python3
"""Probe production static/media delivery through the public Nginx surface."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deploy_config import resolve_deploy_settings


DEFAULT_TIMEOUT_SECONDS = 15
STATIC_DELIVERY_HEADER = "x-static-delivery"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report public Nginx static delivery behavior.")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--dist-dir", default="mini_app_dist")
    parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default for benchmark use.")
    parser.add_argument("--no-fail", action="store_true", help="Exit 0 even when probes fail.")
    return parser.parse_args(argv)


def normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def choose_representative_asset(dist_dir: Path) -> str:
    assets_dir = dist_dir / "assets"
    for suffix in (".js", ".css", ".woff2"):
        matches = sorted(path for path in assets_dir.glob(f"*{suffix}") if path.is_file())
        if matches:
            return f"assets/{matches[0].name}"
    raise FileNotFoundError(f"No representative frontend asset found under {assets_dir}")


def header_value(headers: dict[str, str], name: str) -> str:
    lowered = name.lower()
    return headers.get(lowered, "")


def fetch_url(url: str, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "trading-bot-static-probe/1.0"}, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = int(response.status)
            headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = int(exc.code)
        headers = {key.lower(): value for key, value in exc.headers.items()}
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "url": url,
        "status": status,
        "headers": headers,
        "body_bytes": len(body),
        "elapsed_ms": elapsed_ms,
    }


def evaluate_probe(
    *,
    name: str,
    response: dict[str, Any],
    expected_status: int,
    required_headers: dict[str, tuple[str, ...]] | None = None,
    require_body: bool = False,
) -> dict[str, Any]:
    headers = response.get("headers", {})
    header_checks: list[dict[str, Any]] = []
    for header_name, tokens in (required_headers or {}).items():
        value = header_value(headers, header_name)
        ok = all(token.lower() in value.lower() for token in tokens)
        header_checks.append(
            {
                "header": header_name,
                "value": value,
                "required_tokens": list(tokens),
                "ok": ok,
            }
        )
    status_ok = int(response.get("status", 0)) == expected_status
    body_ok = not require_body or int(response.get("body_bytes", 0)) > 0
    ok = status_ok and body_ok and all(item["ok"] for item in header_checks)
    return {
        "name": name,
        "ok": ok,
        "expected_status": expected_status,
        "status": response.get("status"),
        "elapsed_ms": response.get("elapsed_ms"),
        "body_bytes": response.get("body_bytes"),
        "headers": {
            "cache-control": header_value(headers, "cache-control"),
            STATIC_DELIVERY_HEADER: header_value(headers, STATIC_DELIVERY_HEADER),
            "service-worker-allowed": header_value(headers, "service-worker-allowed"),
            "content-type": header_value(headers, "content-type"),
        },
        "header_checks": header_checks,
    }


def collect_report(*, base_url: str, dist_dir: Path) -> dict[str, Any]:
    asset_path = choose_representative_asset(dist_dir)
    probes = [
        evaluate_probe(
            name="immutable_asset",
            response=fetch_url(f"{base_url}/{asset_path}"),
            expected_status=200,
            required_headers={
                "cache-control": ("public", "max-age=31536000", "immutable"),
                STATIC_DELIVERY_HEADER: ("nginx",),
            },
            require_body=True,
        ),
        evaluate_probe(
            name="service_worker_no_cache",
            response=fetch_url(f"{base_url}/sw.js"),
            expected_status=200,
            required_headers={
                "cache-control": ("no-cache",),
                "service-worker-allowed": ("/",),
                STATIC_DELIVERY_HEADER: ("nginx",),
            },
            require_body=True,
        ),
        evaluate_probe(
            name="manifest_no_cache",
            response=fetch_url(f"{base_url}/manifest.webmanifest"),
            expected_status=200,
            required_headers={
                "cache-control": ("no-cache",),
                STATIC_DELIVERY_HEADER: ("nginx",),
            },
            require_body=True,
        ),
        evaluate_probe(
            name="missing_asset_404",
            response=fetch_url(f"{base_url}/assets/__p4_missing_asset__.css"),
            expected_status=404,
        ),
        evaluate_probe(
            name="raw_uploads_not_public",
            response=fetch_url(f"{base_url}/uploads/__p4_static_probe__"),
            expected_status=404,
        ),
        evaluate_probe(
            name="protected_chat_media_requires_token",
            response=fetch_url(f"{base_url}/api/chat/files/__p4_static_probe__"),
            expected_status=401,
        ),
    ]
    return {
        "ok": all(item["ok"] for item in probes),
        "base_url": base_url,
        "representative_asset": asset_path,
        "probes": probes,
    }


def format_human_report(report: dict[str, Any]) -> str:
    lines = [
        "Static/media delivery:",
        f"- base_url: {report['base_url']}",
        f"- representative_asset: {report['representative_asset']}",
        f"- ok: {report['ok']}",
    ]
    for probe in report["probes"]:
        status = "ok" if probe["ok"] else "failed"
        lines.append(
            f"- {probe['name']}: status={probe['status']} elapsed_ms={probe['elapsed_ms']} [{status}]"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = resolve_deploy_settings(manifest_path=args.manifest)
    base_url = normalize_base_url(args.base_url or settings.get("IRAN_FRONTEND_URL") or settings["IRAN_SERVER_URL"])
    report = collect_report(base_url=base_url, dist_dir=Path(args.dist_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_human_report(report))
    if report["ok"] or args.no_fail:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
