"""Structured context and noise controls for background jobs."""
from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from core.metrics import record_job_run
from core.request_context import get_request_context, replace_request_context, set_request_context


@contextmanager
def job_context(job_name: str, *, iteration: int | None = None, **extra: Any) -> Iterator[str]:
    previous = get_request_context()
    run_id = str(uuid.uuid4())
    start_time = time.perf_counter()
    result = "success"
    set_request_context(
        log_class="job",
        job_name=job_name,
        run_id=run_id,
        iteration=iteration,
        **extra,
    )
    try:
        yield run_id
    except Exception:
        result = "failure"
        raise
    finally:
        record_job_run(job_name=job_name, result=result, duration_ms=duration_ms_since(start_time))
        replace_request_context(previous)


class RepeatedErrorLogger:
    """Log the first repeated error and then every Nth repetition."""

    def __init__(self, *, every: int = 10) -> None:
        self.every = max(every, 1)
        self._last_key: str | None = None
        self._repeat_count = 0

    def log(self, logger: Any, message: str, exc: Exception, **extra: Any) -> None:
        key = f"{type(exc).__name__}:{exc}"
        if key == self._last_key:
            self._repeat_count += 1
        else:
            self._last_key = key
            self._repeat_count = 1

        if self._repeat_count == 1 or self._repeat_count % self.every == 0:
            record_job_run(
                job_name=str(extra.get("job_name") or get_request_context().get("job_name") or "unknown"),
                result="failure",
                duration_ms=0,
            )
            logger.error(
                message,
                exc,
                extra={
                    "event": "job.error",
                    "repeat_count": self._repeat_count,
                    "suppressed_repeats": max(self._repeat_count - 1, 0),
                    **extra,
                },
            )


def duration_ms_since(start_time: float) -> float:
    return round((time.perf_counter() - start_time) * 1000, 2)
