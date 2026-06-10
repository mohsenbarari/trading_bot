#!/usr/bin/env python3
"""Render the operator-facing metrics collection contract for each service surface."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any


OBSERVABILITY_HEADER = "X-Observability-Api-Key"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render metrics target/collection metadata for production operators.")
    parser.add_argument("--server-mode", default=os.getenv("TRADING_BOT_SERVER_MODE", "foreign"), help="Server mode label.")
    parser.add_argument("--api-url", default=os.getenv("TRADING_BOT_API_METRICS_URL", "http://127.0.0.1:8000/metrics"))
    parser.add_argument("--compose-file", default=os.getenv("TRADING_BOT_COMPOSE_FILE", "docker-compose.yml"))
    parser.add_argument("--output", choices=["json"], default="json")
    return parser.parse_args()


def build_manifest(*, server_mode: str, api_url: str, compose_file: str) -> dict[str, Any]:
    api_key = os.getenv("OBSERVABILITY_API_KEY", "").strip()
    header_value = "[set in env]" if api_key else "[missing]"
    return {
        "generated_for": server_mode or "unknown",
        "metrics_backend_policy": {
            "default_backend": os.getenv("TRADING_BOT_METRICS_BACKEND", "memory").strip().lower() or "memory",
            "authoritative_cross_service_source": "loki_logs_until_explicit_multi_surface_scrape_is_deployed",
        },
        "surfaces": [
            {
                "service": "api",
                "collection_mode": "http",
                "target_url": api_url,
                "headers": {OBSERVABILITY_HEADER: header_value},
                "authoritative_scope": "single_api_process_only_when_backend=memory",
                "notes": [
                    "Do not treat a single API scrape as a full-system aggregate under the memory backend.",
                    "Use per-worker scraping or a future explicit aggregator if SLO math depends on API metrics.",
                ],
            },
            {
                "service": "bot",
                "collection_mode": "logs_only",
                "target_url": None,
                "headers": {},
                "authoritative_scope": "no_direct_metrics_scrape_surface_under_memory_backend",
                "notes": [
                    "Bot metrics remain process-local today.",
                    "Use Loki/log-based alerts for production until an explicit exporter or sidecar is deployed.",
                ],
            },
            {
                "service": "sync_worker",
                "collection_mode": "logs_only",
                "target_url": None,
                "headers": {},
                "authoritative_scope": "no_direct_metrics_scrape_surface_under_memory_backend",
                "notes": [
                    "Sync-worker metrics remain process-local today.",
                    "Use sync health sampling plus Loki alerts until an explicit exporter or sidecar is deployed.",
                ],
            },
        ],
        "operator_hints": {
            "compose_file": compose_file,
            "api_metrics_header_name": OBSERVABILITY_HEADER,
        },
    }


def main() -> int:
    args = _parse_args()
    manifest = build_manifest(server_mode=args.server_mode, api_url=args.api_url, compose_file=args.compose_file)
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
