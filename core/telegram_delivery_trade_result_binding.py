"""Local ownership marker for one trade receipt handed to the main queue."""
from __future__ import annotations

from typing import Any


TRADE_RESULT_QUEUE_RECEIPT_WORKER_PREFIX = (
    "telegram-delivery-queue-v1:trade-result:"
)
TRADE_RESULT_QUEUE_RECONCILIATION_WORKER_PREFIX = (
    "telegram-delivery-queue-v1:trade-reconcile:"
)


def trade_result_queue_receipt_worker_id(job_id: int) -> str:
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise ValueError("telegram_trade_queue_job_id_invalid")
    marker = f"{TRADE_RESULT_QUEUE_RECEIPT_WORKER_PREFIX}{job_id}"
    if len(marker) > 128:
        raise ValueError("telegram_trade_queue_receipt_worker_id_too_long")
    return marker


def trade_result_queue_job_id_from_receipt(receipt: Any) -> int | None:
    worker_id = str(getattr(receipt, "worker_id", "") or "").strip()
    if not worker_id.startswith(TRADE_RESULT_QUEUE_RECEIPT_WORKER_PREFIX):
        return None
    raw_job_id = worker_id.removeprefix(
        TRADE_RESULT_QUEUE_RECEIPT_WORKER_PREFIX
    )
    if not raw_job_id.isascii() or not raw_job_id.isdigit():
        return None
    job_id = int(raw_job_id)
    return job_id if job_id > 0 else None


def trade_result_receipt_is_bound_to_job(receipt: Any, job_id: int) -> bool:
    return trade_result_queue_job_id_from_receipt(receipt) == job_id


def trade_result_queue_reconciliation_worker_id(job_id: int) -> str:
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
        raise ValueError("telegram_trade_queue_reconciliation_job_id_invalid")
    marker = f"{TRADE_RESULT_QUEUE_RECONCILIATION_WORKER_PREFIX}{job_id}"
    if len(marker) > 128:
        raise ValueError(
            "telegram_trade_queue_reconciliation_worker_id_too_long"
        )
    return marker


def trade_result_queue_reconciliation_job_id_from_receipt(
    receipt: Any,
) -> int | None:
    worker_id = str(getattr(receipt, "worker_id", "") or "").strip()
    if not worker_id.startswith(
        TRADE_RESULT_QUEUE_RECONCILIATION_WORKER_PREFIX
    ):
        return None
    raw_job_id = worker_id.removeprefix(
        TRADE_RESULT_QUEUE_RECONCILIATION_WORKER_PREFIX
    )
    if not raw_job_id.isascii() or not raw_job_id.isdigit():
        return None
    job_id = int(raw_job_id)
    return job_id if job_id > 0 else None
