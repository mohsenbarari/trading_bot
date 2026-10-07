"""Retained call frame helper enforcing custody-first lifecycle semantics."""

from __future__ import annotations

from typing import Any, Callable
import tests.test_two_site_work_cost_witness_helpers as h

__all__ = ["RetainedCallFrame", "_ERR_OWNERSHIP"]

_ERR_OWNERSHIP = "ownership violation"


def _inspect_graph(
    node: Any,
    forbidden_ids: set[int] | None = None,
    reject_duplicates: bool = False,
) -> tuple[Any, set[int]]:
    visited = 0
    ids: set[int] = set()

    def _walk(curr: Any, depth: int) -> Any:
        nonlocal visited
        visited += 1
        if visited > 2048 or depth > 16:
            raise ValueError(h._ERR_BOUND)
        t = type(curr)
        if t is dict:
            nid = id(curr)
            if forbidden_ids is not None and nid in forbidden_ids:
                raise AssertionError(_ERR_OWNERSHIP)
            if reject_duplicates and nid in ids:
                raise AssertionError(_ERR_OWNERSHIP)
            ids.add(nid)
            items = []
            for k, v in curr.items():
                items.append((_walk(k, depth + 1), _walk(v, depth + 1)))
            return ("DICT", nid, tuple(items))
        if t is list:
            nid = id(curr)
            if forbidden_ids is not None and nid in forbidden_ids:
                raise AssertionError(_ERR_OWNERSHIP)
            if reject_duplicates and nid in ids:
                raise AssertionError(_ERR_OWNERSHIP)
            ids.add(nid)
            return ("LIST", nid, tuple(_walk(x, depth + 1) for x in curr))
        if t is tuple:
            return ("TUPLE", tuple(_walk(x, depth + 1) for x in curr))
        return None

    fp = _walk(node, 0)
    return fp, ids


class RetainedCallFrame:
    """Retains caller oracles and arguments, guarding custody and ownership."""

    def __init__(self, fixed_objects: list, preflight: Callable[[], Any]) -> None:
        if type(fixed_objects) is not list:
            raise ValueError("fixed_objects must be a list")
        if len(fixed_objects) > 64:
            raise ValueError(h._ERR_BOUND)
        if not callable(preflight):
            raise ValueError("preflight must be callable")

        self._fixed_objects = fixed_objects
        self._orig_fixed_list = fixed_objects
        self._orig_fixed_items = list(fixed_objects)

        self._fixed_snapshots: list[tuple[str, Any]] = []
        self._fixed_fingerprints: list[Any] = []
        self._fixed_mutable_ids: set[int] = set()

        for obj in self._orig_fixed_items:
            snap = h.capture_custody(obj)
            fp, ids = _inspect_graph(obj)
            self._fixed_snapshots.append(snap)
            self._fixed_fingerprints.append(fp)
            self._fixed_mutable_ids.update(ids)

        self._all_known_mutable_ids: set[int] = set(self._fixed_mutable_ids)
        self._returns: list[Any] = []
        self._return_snapshots: list[tuple[str, Any]] = []
        self._return_fingerprints: list[Any] = []

        try:
            preflight()
        finally:
            self.assert_all_custody()

    def assert_all_custody(self) -> None:
        """Assert all fixed objects and prior returns retain custody and identity."""
        if self._fixed_objects is not self._orig_fixed_list:
            raise AssertionError(h._ERR_CUSTODY)
        if len(self._fixed_objects) != len(self._orig_fixed_items):
            raise AssertionError(h._ERR_CUSTODY)
        for act, orig in zip(self._fixed_objects, self._orig_fixed_items):
            if act is not orig:
                raise AssertionError(h._ERR_CUSTODY)

        for obj, snap, orig_fp in zip(
            self._orig_fixed_items,
            self._fixed_snapshots,
            self._fixed_fingerprints,
        ):
            h.assert_custody_unchanged(obj, snap)
            fp, _ = _inspect_graph(obj)
            if fp != orig_fp:
                raise AssertionError(h._ERR_CUSTODY)

        for ret_obj, ret_snap, ret_fp in zip(
            self._returns,
            self._return_snapshots,
            self._return_fingerprints,
        ):
            h.assert_custody_unchanged(ret_obj, ret_snap)
            fp, _ = _inspect_graph(ret_obj)
            if fp != ret_fp:
                raise AssertionError(h._ERR_CUSTODY)

    def call(self, owner: Any, method: str, args: tuple, expected: Any) -> Any:
        """Invoke owner method under custody-first and ownership invariants."""
        self.assert_all_custody()

        if type(method) is not str:
            raise ValueError("method must be a str")
        if type(args) is not tuple:
            raise ValueError("args must be a tuple")
        if len(self._returns) >= 64:
            raise ValueError(h._ERR_BOUND)

        _, arg_ids = _inspect_graph(args)
        if not arg_ids.issubset(self._fixed_mutable_ids):
            raise ValueError("mutable args not in fixed objects")
        _, exp_ids = _inspect_graph(expected)
        if not exp_ids.issubset(self._fixed_mutable_ids):
            raise ValueError("mutable expected oracle not in fixed objects")

        try:
            fn = getattr(owner, method)
            actual_return = fn(*args)
        finally:
            self.assert_all_custody()

        h.assert_exact_typed(actual_return, expected)

        ret_fp, ret_ids = _inspect_graph(
            actual_return,
            forbidden_ids=self._all_known_mutable_ids,
            reject_duplicates=True,
        )

        self._returns.append(actual_return)
        self._return_snapshots.append(h.capture_custody(actual_return))
        self._return_fingerprints.append(ret_fp)
        self._all_known_mutable_ids.update(ret_ids)
        return actual_return
