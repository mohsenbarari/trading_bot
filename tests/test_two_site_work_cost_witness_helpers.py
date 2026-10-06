"""Reusable test-evidence foundation helpers for two-site work cost lineage.

Exports:
    capture_custody: Recursively captures exact builtin primitives/containers
        and retains foreign references as opaque identity nodes.
    assert_custody_unchanged: Rewalks an object tree and asserts identity/value
        preservation against a prior snapshot without foreign hooks.
    assert_exact_typed: Expected-driven strict-typed structural oracle.
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

__all__ = [
    "_ERR_BOUND",
    "_ERR_CUSTODY",
    "_ERR_TYPED",
    "ForeignClassAllHooks",
    "ForeignClassUnhashable",
    "ForeignDict",
    "ForeignDuration",
    "ForeignFinished",
    "ForeignList",
    "ForeignMetaAllHooks",
    "ForeignMetaUnhashable",
    "ForeignOpaqueLeaf",
    "ForeignStrKey",
    "armed_hooks",
    "assert_custody_unchanged",
    "assert_exact_typed",
    "capture_custody",
    "get_total_hook_calls",
    "reset_hook_counters",
]

_ERR_CUSTODY = "custody mismatch"
_ERR_TYPED = "type or value mismatch"
_ERR_BOUND = "custody bound exceeded"


def capture_custody(value: Any) -> tuple[str, Any]:
    visited = 0

    def _walk(node: Any, depth: int) -> tuple[str, Any]:
        nonlocal visited
        visited += 1
        if visited > 2048 or depth > 16:
            raise ValueError(_ERR_BOUND)
        t = type(node)
        if t is dict:
            items = []
            for k, v in node.items():
                items.append((_walk(k, depth + 1), _walk(v, depth + 1)))
            return ("DICT", tuple(items))
        if t is list:
            return ("LIST", tuple(_walk(elem, depth + 1) for elem in node))
        if t is str:
            return ("STR", node)
        if t is int:
            return ("INT", node)
        if t is float:
            return ("FLOAT", node)
        if t is bool:
            return ("BOOL", node)
        if node is None and t is type(None):
            return ("NONE", None)
        return ("FOREIGN", node)

    return _walk(value, 0)


def assert_custody_unchanged(value: Any, snapshot: tuple[str, Any]) -> None:
    visited = 0

    def _check(val: Any, snap: Any, depth: int) -> None:
        nonlocal visited
        visited += 1
        if visited > 2048 or depth > 16:
            raise ValueError(_ERR_BOUND)
        if type(snap) is not tuple or len(snap) != 2:
            raise AssertionError(_ERR_CUSTODY)
        tag, snap_data = snap
        t = type(val)
        if tag == "DICT":
            if t is not dict or len(val) != len(snap_data):
                raise AssertionError(_ERR_CUSTODY)
            for (act_k, act_v), (snap_k, snap_v) in zip(val.items(), snap_data):
                _check(act_k, snap_k, depth + 1)
                _check(act_v, snap_v, depth + 1)
        elif tag == "LIST":
            if t is not list or len(val) != len(snap_data):
                raise AssertionError(_ERR_CUSTODY)
            for act_elem, snap_elem in zip(val, snap_data):
                _check(act_elem, snap_elem, depth + 1)
        elif tag == "STR":
            if t is not str or val != snap_data:
                raise AssertionError(_ERR_CUSTODY)
        elif tag == "INT":
            if t is not int or val != snap_data:
                raise AssertionError(_ERR_CUSTODY)
        elif tag == "FLOAT":
            if t is not float:
                raise AssertionError(_ERR_CUSTODY)
            if math.isnan(snap_data):
                if not math.isnan(val):
                    raise AssertionError(_ERR_CUSTODY)
            elif val != snap_data:
                raise AssertionError(_ERR_CUSTODY)
        elif tag == "BOOL":
            if t is not bool or val != snap_data:
                raise AssertionError(_ERR_CUSTODY)
        elif tag == "NONE":
            if val is not None or t is not type(None):
                raise AssertionError(_ERR_CUSTODY)
        elif tag == "FOREIGN":
            if val is not snap_data:
                raise AssertionError(_ERR_CUSTODY)
        else:
            raise AssertionError(_ERR_CUSTODY)

    _check(value, snapshot, 0)


def assert_exact_typed(actual: Any, expected: Any, depth: int = 0) -> None:
    if depth > 16:
        raise ValueError(_ERR_BOUND)
    exp_t = type(expected)
    if (
        exp_t is not dict
        and exp_t is not list
        and exp_t is not str
        and exp_t is not int
        and exp_t is not float
        and exp_t is not bool
        and exp_t is not type(None)
    ):
        raise AssertionError(_ERR_TYPED)

    if type(actual) is not exp_t:
        raise AssertionError(_ERR_TYPED)

    if exp_t is dict:
        for k in actual.keys():
            if type(k) is not str:
                raise AssertionError(_ERR_TYPED)
        for k in expected.keys():
            if type(k) is not str:
                raise AssertionError(_ERR_TYPED)
        if len(actual) != len(expected):
            raise AssertionError(_ERR_TYPED)
        for k in expected:
            if k not in actual:
                raise AssertionError(_ERR_TYPED)
        for k, exp_val in expected.items():
            assert_exact_typed(actual[k], exp_val, depth + 1)
    elif exp_t is list:
        if len(actual) != len(expected):
            raise AssertionError(_ERR_TYPED)
        for act_elem, exp_elem in zip(actual, expected):
            assert_exact_typed(act_elem, exp_elem, depth + 1)
    elif exp_t is str or exp_t is int or exp_t is bool:
        if actual != expected:
            raise AssertionError(_ERR_TYPED)
    elif exp_t is float:
        if math.isnan(expected):
            if not math.isnan(actual):
                raise AssertionError(_ERR_TYPED)
        elif actual != expected:
            raise AssertionError(_ERR_TYPED)
    elif exp_t is type(None):
        if actual is not None:
            raise AssertionError(_ERR_TYPED)


_HOOKS_ARMED = False
_HOOK_COUNTS: dict[str, int] = {}


def _record_hook(name: str) -> None:
    _HOOK_COUNTS[name] = _HOOK_COUNTS.get(name, 0) + 1


def reset_hook_counters() -> None:
    _HOOK_COUNTS.clear()


def get_total_hook_calls() -> int:
    return sum(_HOOK_COUNTS.values())


@contextlib.contextmanager
def armed_hooks():
    global _HOOKS_ARMED
    reset_hook_counters()
    _HOOKS_ARMED = True
    try:
        yield
    finally:
        _HOOKS_ARMED = False


class ForeignMetaUnhashable(type):
    __hash__ = None

    def __eq__(cls, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("meta_unhashable_eq")
            raise RuntimeError("ForeignMetaUnhashable.__eq__ called")
        return super().__eq__(other)


class ForeignMetaAllHooks(type):
    def __eq__(cls, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("meta_all_eq")
            raise RuntimeError("ForeignMetaAllHooks.__eq__ called")
        return super().__eq__(other)

    def __hash__(cls) -> int:
        if _HOOKS_ARMED:
            _record_hook("meta_all_hash")
            raise RuntimeError("ForeignMetaAllHooks.__hash__ called")
        return super().__hash__()

    def __str__(cls) -> str:
        if _HOOKS_ARMED:
            _record_hook("meta_all_str")
            raise RuntimeError("ForeignMetaAllHooks.__str__ called")
        return super().__str__()

    def __repr__(cls) -> str:
        if _HOOKS_ARMED:
            _record_hook("meta_all_repr")
            raise RuntimeError("ForeignMetaAllHooks.__repr__ called")
        return super().__repr__()

    def __getattribute__(cls, name: str) -> Any:
        if _HOOKS_ARMED and not (name.startswith("__") and name.endswith("__")):
            _record_hook("meta_all_getattribute")
            raise RuntimeError(f"ForeignMetaAllHooks.__getattribute__ {name}")
        return super().__getattribute__(name)


class ForeignClassUnhashable(metaclass=ForeignMetaUnhashable):
    pass


class ForeignClassAllHooks(metaclass=ForeignMetaAllHooks):
    pass


class ForeignStrKey(str, metaclass=ForeignMetaUnhashable):
    __hash__ = str.__hash__

    def __eq__(self, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("foreign_str_eq")
            raise RuntimeError("ForeignStrKey.__eq__ called")
        return super().__eq__(other)


class ForeignDuration(float):
    def __eq__(self, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("duration_eq")
            raise RuntimeError("ForeignDuration.__eq__ called")
        return super().__eq__(other)

    def __hash__(self) -> int:
        if _HOOKS_ARMED:
            _record_hook("duration_hash")
            raise RuntimeError("ForeignDuration.__hash__ called")
        return super().__hash__()


class ForeignFinished(int):
    def __eq__(self, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("finished_eq")
            raise RuntimeError("ForeignFinished.__eq__ called")
        return super().__eq__(other)

    def __hash__(self) -> int:
        if _HOOKS_ARMED:
            _record_hook("finished_hash")
            raise RuntimeError("ForeignFinished.__hash__ called")
        return super().__hash__()


class ForeignDict(dict):
    def __eq__(self, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("dict_eq")
            raise RuntimeError("ForeignDict.__eq__ called")
        return super().__eq__(other)

    def __iter__(self) -> Any:
        if _HOOKS_ARMED:
            _record_hook("dict_iter")
            raise RuntimeError("ForeignDict.__iter__ called")
        return super().__iter__()

    def __len__(self) -> int:
        if _HOOKS_ARMED:
            _record_hook("dict_len")
            raise RuntimeError("ForeignDict.__len__ called")
        return super().__len__()


class ForeignList(list):
    def __eq__(self, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("list_eq")
            raise RuntimeError("ForeignList.__eq__ called")
        return super().__eq__(other)

    def __iter__(self) -> Any:
        if _HOOKS_ARMED:
            _record_hook("list_iter")
            raise RuntimeError("ForeignList.__iter__ called")
        return super().__iter__()

    def __len__(self) -> int:
        if _HOOKS_ARMED:
            _record_hook("list_len")
            raise RuntimeError("ForeignList.__len__ called")
        return super().__len__()


class ForeignOpaqueLeaf:
    def __eq__(self, other: Any) -> bool:
        if _HOOKS_ARMED:
            _record_hook("leaf_eq")
            raise RuntimeError("ForeignOpaqueLeaf.__eq__ called")
        return super().__eq__(other)

    def __hash__(self) -> int:
        if _HOOKS_ARMED:
            _record_hook("leaf_hash")
            raise RuntimeError("ForeignOpaqueLeaf.__hash__ called")
        return super().__hash__()

    def __str__(self) -> str:
        if _HOOKS_ARMED:
            _record_hook("leaf_str")
            raise RuntimeError("ForeignOpaqueLeaf.__str__ called")
        return super().__str__()

    def __repr__(self) -> str:
        if _HOOKS_ARMED:
            _record_hook("leaf_repr")
            raise RuntimeError("ForeignOpaqueLeaf.__repr__ called")
        return super().__repr__()

    def __getattribute__(self, name: str) -> Any:
        if _HOOKS_ARMED and not (name.startswith("__") and name.endswith("__")):
            _record_hook("leaf_getattribute")
            raise RuntimeError(f"ForeignOpaqueLeaf.__getattribute__ {name}")
        return super().__getattribute__(name)
