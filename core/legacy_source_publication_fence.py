"""Hard fail-closed fence for superseded source Object-delta publication APIs.

The first source-publication contracts predate the required transaction-scoped
locked source snapshot and a fresh live Writer Witness authority.  Their
value constructors and isolated mechanics remain useful for contract tests,
but they must never be wired into a runtime publisher.  This tiny, pure
module is the single explicit boundary used by every former public legacy
authorization or persistence entrypoint.

It intentionally has no opt-out flag, environment switch, or compatibility
mode.  Re-enabling publication is a separate future change that must provide
a root-only coordinator holding the locked snapshot transaction and a live
Writer Witness authority through reservation, sealing, receipt, attestation,
and ledger binding.
"""

from __future__ import annotations

from typing import NoReturn


class LegacyObjectDeltaSourcePublicationDisabledError(RuntimeError):
    """A retired legacy source-publication entrypoint was invoked."""


def reject_legacy_object_delta_source_publication_runtime(*, entrypoint: str) -> NoReturn:
    """Unconditionally reject a former public runtime publication operation."""

    if not isinstance(entrypoint, str) or not entrypoint:
        entrypoint = "unknown"
    raise LegacyObjectDeltaSourcePublicationDisabledError(
        f"legacy Object-delta source publication entrypoint {entrypoint!r} is hard-disabled: "
        "it lacks the required locked source snapshot and fresh live Writer Witness "
        "authority; use no runtime replacement until the root-only coordinator exists"
    )


__all__ = (
    "LegacyObjectDeltaSourcePublicationDisabledError",
    "reject_legacy_object_delta_source_publication_runtime",
)
