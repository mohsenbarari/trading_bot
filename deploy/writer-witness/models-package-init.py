"""Minimal model package for the isolated Writer Witness release.

The product ``models`` package eagerly imports the complete application schema.
Witness release assembly deliberately replaces it with this closed initializer
so Telegram, WebApp, and business-model dependencies cannot enter the control
plane package transitively.
"""

__all__: tuple[str, ...] = ()
