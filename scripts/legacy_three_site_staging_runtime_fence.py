"""Fail-closed retirement fence for superseded three-site staging runtimes.

The retired staging campaign accepted execution inputs (including inventory,
approval policy, and artifact paths) from its caller. Those inputs cannot be
treated as a trust root for host, Docker, or Object Storage operations. Keep
pure parsers and planners available for historical evidence inspection, but
make every runtime entry point fail before it can inspect caller-provided
authority or cause an external effect.
"""

from __future__ import annotations

from typing import Final


RETIREMENT_REASON: Final = (
    "legacy three-site staging runtime is retired; it cannot consume "
    "caller-controlled approval, policy, inventory, or artifact paths for "
    "host, Docker, network, or storage operations"
)


class LegacyThreeSiteStagingRuntimeRetiredError(RuntimeError):
    """Raised before any retired staging runtime can perform I/O."""


def assert_retired(*, component: str, operation: str) -> None:
    """Unconditionally block a legacy staging runtime boundary.

    This must be the first statement of every non-pure entry point. It is
    intentionally not configurable: an environment flag would recreate the
    caller-controlled execution path that this fence removes.
    """

    raise LegacyThreeSiteStagingRuntimeRetiredError(
        f"{RETIREMENT_REASON}: {component} ({operation})"
    )


def blocked_payload(*, component: str) -> dict[str, str]:
    """Stable, non-secret CLI result for a blocked legacy executable."""

    return {
        "status": "blocked_legacy_three_site_staging_runtime_retired",
        "component": component,
        "error": RETIREMENT_REASON,
        "error_class": LegacyThreeSiteStagingRuntimeRetiredError.__name__,
    }
