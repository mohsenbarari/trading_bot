"""Pure response codec for two-site quota continuation broker responses."""

from __future__ import annotations

import json
import math
import time
from typing import Any

try:
    from scripts.two_site_provider_broker import (
        MAX_PIPELINE_OUTPUT_BYTES,
        BrokerError,
        validate_response,
    )
except ImportError:
    from two_site_provider_broker import (  # type: ignore
        MAX_PIPELINE_OUTPUT_BYTES,
        BrokerError,
        validate_response,
    )

try:
    from scripts.two_site_quota_resume_submission_intent import (
        compute_canonical_request_sha256,
    )
except ImportError:
    from two_site_quota_resume_submission_intent import (  # type: ignore
        compute_canonical_request_sha256,
    )


class ContinuationResponseError(BrokerError):
    """Raised when continuation broker response encoding or validation fails."""


__all__ = [
    "ContinuationResponseError",
    "build_continuation_broker_response",
]


def _validate_json_native(obj: Any) -> None:
    seen_container_ids: set[int] = set()
    stack: list[Any] = [obj]
    traversal_count = 0
    max_traversal_nodes = 100_000

    while stack:
        traversal_count += 1
        if traversal_count > max_traversal_nodes:
            raise ContinuationResponseError(
                "result container traversal exceeded maximum allowable items"
            )
        current = stack.pop()
        curr_type = type(current)

        if curr_type is dict:
            curr_id = id(current)
            if curr_id in seen_container_ids:
                raise ContinuationResponseError(
                    "cyclic or repeated container identity detected in result"
                )
            seen_container_ids.add(curr_id)

            for k, v in current.items():
                if type(k) is not str:
                    raise ContinuationResponseError(
                        f"result dict keys must be str, got {type(k).__name__}"
                    )
                if len(k) > MAX_PIPELINE_OUTPUT_BYTES:
                    raise ContinuationResponseError(
                        "result dict key exceeds output byte limit"
                    )
                if len(k.encode("utf-8")) > MAX_PIPELINE_OUTPUT_BYTES:
                    raise ContinuationResponseError(
                        "result dict key exceeds output byte limit"
                    )
                stack.append(v)

        elif curr_type is list:
            curr_id = id(current)
            if curr_id in seen_container_ids:
                raise ContinuationResponseError(
                    "cyclic or repeated container identity detected in result"
                )
            seen_container_ids.add(curr_id)

            for item in current:
                stack.append(item)

        elif curr_type is str:
            if len(current) > MAX_PIPELINE_OUTPUT_BYTES:
                raise ContinuationResponseError(
                    "result string exceeds output byte limit"
                )
            if len(current.encode("utf-8")) > MAX_PIPELINE_OUTPUT_BYTES:
                raise ContinuationResponseError(
                    "result string exceeds output byte limit"
                )

        elif curr_type in (bool, int):
            continue

        elif curr_type is float:
            try:
                finite = math.isfinite(current)
            except OverflowError as exc:
                raise ContinuationResponseError(
                    f"result float value caused overflow: {exc}"
                ) from exc
            if not finite:
                raise ContinuationResponseError(
                    "result float values must be finite"
                )

        elif current is None:
            continue

        else:
            raise ContinuationResponseError(
                f"result contains non-JSON-native type {curr_type.__name__}"
            )


def build_continuation_broker_response(
    request: dict[str, Any],
    result: dict[str, Any],
    *,
    now_epoch: float | int | None = None,
) -> dict[str, Any]:
    """Build and validate an exact nine-key Broker COMPLETED response for quota continuation."""
    if type(request) is not dict:
        raise ContinuationResponseError(
            f"request must be an exact dict, got {type(request).__name__}"
        )
    try:
        compute_canonical_request_sha256(request)
    except Exception as exc:
        raise ContinuationResponseError(
            f"continuation request validation failed: {exc}"
        ) from exc

    if type(result) is not dict:
        raise ContinuationResponseError(
            f"result must be an exact dict, got {type(result).__name__}"
        )

    _validate_json_native(result)

    inner_state = result.get("state")
    if type(inner_state) is not str or inner_state not in {
        "READY_FOR_CODEX",
        "TASK_BLOCKED",
    }:
        raise ContinuationResponseError(
            f"inner result state must be 'READY_FOR_CODEX' or 'TASK_BLOCKED', got {inner_state!r}"
        )

    if now_epoch is None:
        try:
            finished_at: Any = time.time()
        except Exception as exc:
            raise ContinuationResponseError(
                f"time.time() failed: {exc}"
            ) from exc
    else:
        finished_at = now_epoch

    if (
        type(finished_at) is bool
        or not isinstance(finished_at, (int, float))
    ):
        raise ContinuationResponseError(
            f"finished_at must be a finite numeric value, got {type(finished_at).__name__}"
        )

    try:
        if not math.isfinite(finished_at):
            raise ContinuationResponseError(
                "finished_at must be a finite numeric value"
            )
    except OverflowError as exc:
        raise ContinuationResponseError(
            f"finished_at numeric value caused overflow: {exc}"
        ) from exc

    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    chunks: list[str] = []
    total_bytes = 0
    try:
        for chunk in encoder.iterencode(result):
            chunk_bytes = chunk.encode("utf-8")
            total_bytes += len(chunk_bytes)
            if total_bytes > MAX_PIPELINE_OUTPUT_BYTES:
                raise ContinuationResponseError(
                    f"encoded result size ({total_bytes} bytes) exceeds limit "
                    f"MAX_PIPELINE_OUTPUT_BYTES ({MAX_PIPELINE_OUTPUT_BYTES})"
                )
            chunks.append(chunk)
    except ContinuationResponseError:
        raise
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        raise ContinuationResponseError(
            f"result cannot be serialized to canonical JSON: {exc}"
        ) from exc
    except Exception as exc:
        raise ContinuationResponseError(
            f"result cannot be serialized to canonical JSON: {exc}"
        ) from exc

    stdout_text = "".join(chunks)

    exit_code = 0 if inner_state == "READY_FOR_CODEX" else 1

    response: dict[str, Any] = {
        "schema_version": "1.0",
        "request_id": request["request_id"],
        "run_prefix": request["run_prefix"],
        "source_head": request["source_head"],
        "state": "COMPLETED",
        "exit_code": exit_code,
        "stdout": stdout_text,
        "stderr": "",
        "finished_at": finished_at,
    }

    try:
        return validate_response(request, response)
    except BrokerError as exc:
        raise ContinuationResponseError(
            f"broker response validation failed: {exc}"
        ) from exc
