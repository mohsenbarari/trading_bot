"""Retired legacy database namespace.

The historical connection factory is intentionally not re-exported here.
Application code must use ``core.db``; importing this namespace must never
make an alternate engine or session factory available.
"""

__all__: tuple[str, ...] = ()
