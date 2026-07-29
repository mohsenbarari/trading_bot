"""Shared lightweight errors for prepared-clone operations.

The convergence gate must normalize a prepared-clone liveness failure without
importing the prepared-clone implementation and its runtime dependencies.
"""

from __future__ import annotations


class PreparedCloneInventoryError(RuntimeError):
    """A redacted, fail-closed prepared-clone collection error."""
