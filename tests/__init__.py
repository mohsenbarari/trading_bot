"""Canonical package marker for the repository test suite.

Full-suite discovery uses the repository root as its top-level directory and
many tests import shared helpers through ``tests.*``.  Keeping this marker
tracked makes those imports deterministic and prevents an unrelated
third-party ``tests`` package from shadowing them.
"""
