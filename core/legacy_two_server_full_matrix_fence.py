"""Fail closed before superseded two-site Full Matrix entrypoints run.

Historical Full Matrix and rollout tools were designed around direct Finland /
Iran control, root Compose services, and the removed HTTP sync plane.  They
cannot validate or operate the physical PostgreSQL, private Object Storage
pull, and Witness-controlled single-writer topology.  Keep them as forensic
source only; their executable boundaries must not emit plans that could be
mistaken for an approved three-site campaign.
"""

from __future__ import annotations

from typing import Final, NoReturn


LEGACY_TWO_SERVER_FULL_MATRIX_RETIREMENT_REASON: Final = (
    "legacy two-server Full Matrix and rollout execution is retired; private, "
    "versioned Object Storage pull and Witness-mediated single-writer control "
    "are required"
)


class LegacyTwoServerFullMatrixRetiredError(RuntimeError):
    """Raised before a superseded two-site matrix can perform an external action."""


def assert_legacy_two_server_full_matrix_retired(
    *, component: str, operation: str
) -> NoReturn:
    """Unconditionally reject an executable historical two-site boundary."""

    raise LegacyTwoServerFullMatrixRetiredError(
        f"{LEGACY_TWO_SERVER_FULL_MATRIX_RETIREMENT_REASON}: {component} ({operation})"
    )


def blocked_legacy_two_server_full_matrix_payload(*, component: str) -> dict[str, str]:
    """Return the stable, non-secret CLI output for a rejected entrypoint."""

    return {
        "status": "blocked_legacy_two_server_full_matrix_retired",
        "component": component,
        "error": LEGACY_TWO_SERVER_FULL_MATRIX_RETIREMENT_REASON,
        "error_class": LegacyTwoServerFullMatrixRetiredError.__name__,
    }
