"""Unhooked quota continuation route adapter."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from scripts.two_site_quota_continuation_admission import (
    validate_quota_continuation_request,
)
from scripts.two_site_quota_continuation_response import (
    build_continuation_broker_response,
)
from scripts.two_site_quota_resume_role_dispatch import (
    dispatch_quota_continuation_once,
)
from scripts.two_site_quota_resume_submission_intent import (
    read_quota_resume_submission_intent,
)


def _check_pre_dispatch_stop(root: Path) -> None:
    from scripts import two_site_provider_broker as broker

    stop_val = getattr(broker, "_STOP", None)
    if type(stop_val) is not bool or stop_val is not False:
        raise broker.BrokerError("stop requested before continuation dispatch")

    stop_path = root / ".local" / "two-site-refactor" / "autopilot" / "STOP"
    if os.path.lexists(stop_path):
        raise broker.BrokerError("stop requested before continuation dispatch")


def execute_intent_bound_continuation(
    root: Path,
    request: object,
) -> dict[str, Any]:
    """Execute intent-bound quota continuation workflow.

    1. Admits and detaches the QUOTA_CONTINUATION request.
    2. Validates immutable exact full-request submission intent binding before dispatch.
    3. Dispatches continuation exactly once and builds the Broker response.
    """
    admitted, _task_path, _scope = validate_quota_continuation_request(root, request)
    read_quota_resume_submission_intent(
        root,
        admitted["original_controller_run_id"],
        admitted,
    )
    _check_pre_dispatch_stop(root)
    result = dispatch_quota_continuation_once(root, admitted)
    return build_continuation_broker_response(admitted, result)
