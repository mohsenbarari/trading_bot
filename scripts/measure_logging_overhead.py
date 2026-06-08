#!/usr/bin/env python3
"""Measure local structured logging overhead without external dependencies."""

from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.logging_config import JsonLogFormatter, RequestContextFilter
from core.request_context import clear_request_context, set_request_context


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure JSON logging overhead.")
    parser.add_argument("--iterations", type=int, default=5000)
    parser.add_argument("--budget-us", type=float, default=1000.0, help="Per-event overhead budget in microseconds.")
    return parser.parse_args()


def _noop(iterations: int) -> float:
    started = time.perf_counter()
    for _ in range(iterations):
        pass
    return time.perf_counter() - started


def _structured_logging(iterations: int) -> tuple[float, int]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(RequestContextFilter())

    logger = logging.getLogger("observability.overhead")
    old_handlers = logger.handlers[:]
    old_level = logger.level
    old_propagate = logger.propagate
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    set_request_context(request_id="overhead-probe", actor_id=1, actor_role="operator", path="/metrics", method="GET")
    try:
        started = time.perf_counter()
        for index in range(iterations):
            logger.info(
                "Logging overhead probe",
                extra={
                    "event": "observability.overhead.probe",
                    "log_class": "application",
                    "iteration": index,
                    "status_code": 200,
                },
            )
        elapsed = time.perf_counter() - started
    finally:
        clear_request_context()
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate

    return elapsed, len(stream.getvalue())


def main() -> int:
    args = _parse_args()
    iterations = max(1, args.iterations)
    baseline = _noop(iterations)
    logging_elapsed, bytes_written = _structured_logging(iterations)
    overhead_us = max(logging_elapsed - baseline, 0) * 1_000_000 / iterations
    result = {
        "iterations": iterations,
        "baseline_seconds": round(baseline, 6),
        "logging_seconds": round(logging_elapsed, 6),
        "per_event_overhead_us": round(overhead_us, 2),
        "budget_us": args.budget_us,
        "bytes_written": bytes_written,
        "acceptable": overhead_us <= args.budget_us,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["acceptable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
