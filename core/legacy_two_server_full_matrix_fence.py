"""Fail-closed fence for superseded two-server Full Matrix executors.

The historical runners were built around direct Finland/Iran control and
two-server logical-sync assumptions. They cannot establish the physical
PostgreSQL + private Object Storage + Witness architecture now required for
the three-site topology. Keep their source only for forensic comparison;
neither public nor underscored runtime callables may emit a command plan or
contact a host, Docker, or a production endpoint.
"""

from __future__ import annotations

from typing import Final


RETIREMENT_REASON: Final = (
    "legacy two-server Full Matrix execution is retired; it cannot satisfy "
    "the physical PostgreSQL, private Object Storage pull, and witnessed "
    "single-writer architecture"
)


class LegacyTwoServerFullMatrixRetiredError(RuntimeError):
    """Raised before a retired runner can perform an external action."""


def assert_legacy_two_server_full_matrix_retired(*, component: str, operation: str) -> None:
    """Unconditionally reject every historical two-server runtime boundary."""

    raise LegacyTwoServerFullMatrixRetiredError(
        f"{RETIREMENT_REASON}: {component} ({operation})"
    )


def blocked_legacy_two_server_full_matrix_payload(*, component: str) -> dict[str, str]:
    """Return the stable, non-secret CLI result for a rejected invocation."""

    return {
        "status": "blocked_legacy_two_server_full_matrix_retired",
        "component": component,
        "error": RETIREMENT_REASON,
        "error_class": LegacyTwoServerFullMatrixRetiredError.__name__,
    }
