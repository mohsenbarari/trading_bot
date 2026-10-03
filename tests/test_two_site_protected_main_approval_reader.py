"""Tests for two_site_protected_main_approval_reader bridge."""

import copy
import hashlib
import json
import os
import pathlib
import stat
import traceback
from typing import Any, Optional
from unittest.mock import patch
import pytest

_TEST_FILE = pathlib.Path(__file__).resolve()
_SRC_FILE = _TEST_FILE.parent.parent / "scripts" / "two_site_protected_main_approval_reader.py"

_SRC_STAT_SIZE = _SRC_FILE.stat().st_size
_TEST_STAT_SIZE = _TEST_FILE.stat().st_size
_TOTAL_STAT_SIZE = _SRC_STAT_SIZE + _TEST_STAT_SIZE

if _SRC_STAT_SIZE > 8000 or _TEST_STAT_SIZE > 24000 or _TOTAL_STAT_SIZE > 32000:
    raise AssertionError(
        f"Byte cap exceeded: src={_SRC_STAT_SIZE} (max 8000), "
        f"test={_TEST_STAT_SIZE} (max 24000), total={_TOTAL_STAT_SIZE} (max 32000)"
    )

with open(_SRC_FILE, "rb") as _f_src:
    _SRC_BYTES = _f_src.read(8001)
if len(_SRC_BYTES) > 8000 or len(_SRC_BYTES) != _SRC_STAT_SIZE:
    raise AssertionError("Invalid src bytes")

with open(_TEST_FILE, "rb") as _f_test:
    _TEST_BYTES = _f_test.read(24001)
if len(_TEST_BYTES) > 24000 or len(_TEST_BYTES) != _TEST_STAT_SIZE:
    raise AssertionError("Invalid test bytes")

import scripts.two_site_protected_main_approval_reader as target_mod
from scripts.two_site_protected_main_approval_reader import (
    MAX_PROTECTED_MAIN_APPROVAL_BYTES,
    ProtectedMainApprovalReaderError,
    read_protected_main_approval_locked,
)


class StrSubclass(str):
    pass


FROZEN_DECISION = {
    "approved_main_sha": "b" * 40,
    "decision_id": "OWNER-SYNTHETIC-1",
    "kind": "OWNER_APPROVED_PROTECTED_MAIN_CHANGE",
    "prior_checkpoint_sha256": "c" * 64,
    "prior_head": "d" * 40,
    "prior_main_sha": "a" * 40,
    "reference": "refs/heads/main",
    "schema_version": "1.0",
}

FROZEN_KWARGS = {
    "approved_main_sha": "b" * 40,
    "prior_checkpoint_sha256": "c" * 64,
    "prior_head": "d" * 40,
    "prior_main_sha": "a" * 40,
}


def _make_raw(data: dict[str, Any]) -> bytes:
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


FROZEN_RAW = _make_raw(FROZEN_DECISION)
FROZEN_RAW_SHA256 = hashlib.sha256(FROZEN_RAW).hexdigest().lower()
FROZEN_GOLDEN = {
    "decision": copy.deepcopy(FROZEN_DECISION),
    "raw_sha256": FROZEN_RAW_SHA256,
}


def _setup_tree(
    tmp_path: pathlib.Path,
    leaf_content: bytes = FROZEN_RAW,
    leaf_mode: int = 0o600,
) -> pathlib.Path:
    outer = tmp_path / "outer"
    outer.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(outer, 0o755)
    project = outer / "project"
    project.mkdir(mode=0o755, exist_ok=True)
    os.chmod(project, 0o755)
    local = project / ".local"
    local.mkdir(mode=0o700, exist_ok=True)
    os.chmod(local, 0o700)
    refactor = local / "two-site-refactor"
    refactor.mkdir(mode=0o755, exist_ok=True)
    os.chmod(refactor, 0o755)
    autopilot = refactor / "autopilot"
    autopilot.mkdir(mode=0o700, exist_ok=True)
    os.chmod(autopilot, 0o700)
    leaf = autopilot / "protected-main-approval.json"
    leaf.write_bytes(leaf_content)
    os.chmod(leaf, leaf_mode)
    return project


def _snapshot_tree(base: pathlib.Path) -> dict[str, tuple[str, int, int, int, Optional[str], Optional[bytes]]]:
    snap = {}
    for p in sorted(base.rglob("*")):
        st = os.stat(p, follow_symlinks=False)
        m = st.st_mode
        if stat.S_ISLNK(m):
            ftype = "lnk"
            target = os.readlink(p)
            content = None
        elif stat.S_ISDIR(m):
            ftype = "dir"
            target = None
            content = None
        elif stat.S_ISFIFO(m):
            ftype = "fifo"
            target = None
            content = None
        elif stat.S_ISREG(m):
            ftype = "reg"
            target = None
            with open(p, "rb") as f:
                content = f.read()
        else:
            ftype = "other"
            target = None
            content = None
        rel = str(p.relative_to(base))
        snap[rel] = (ftype, st.st_uid, stat.S_IMODE(m), st.st_nlink, target, content)
    return copy.deepcopy(snap)


def _run_bridge_fault(
    project: pathlib.Path,
    expected_msg: str,
    *,
    bad_kwargs: Optional[dict[str, Any]] = None,
    bad_root: Optional[Any] = None,
    fault_fn: Optional[Any] = None,
    restore_fn: Optional[Any] = None,
) -> None:
    outer = project.parent
    target_root = project if bad_root is None else bad_root
    target_kwargs = copy.deepcopy(FROZEN_KWARGS) if bad_kwargs is None else bad_kwargs

    good_before = read_protected_main_approval_locked(project, **FROZEN_KWARGS)
    assert good_before == FROZEN_GOLDEN

    before_snap = _snapshot_tree(outer)
    root_str_snap = str(project)
    good_kw_snap = copy.deepcopy(FROZEN_KWARGS)
    golden_snap = copy.deepcopy(FROZEN_GOLDEN)
    raw_snap = copy.deepcopy(FROZEN_RAW)
    bad_kw_snap = copy.deepcopy(target_kwargs)

    try:
        if fault_fn is not None:
            fault_fn()
        bad_tree_snap = _snapshot_tree(outer)

        with pytest.raises(ProtectedMainApprovalReaderError) as exc_info:
            read_protected_main_approval_locked(target_root, **target_kwargs)
        err = exc_info.value
        assert str(err) == expected_msg
        assert err.__cause__ is None
        assert err.__suppress_context__ is True

        assert _snapshot_tree(outer) == bad_tree_snap
        assert target_kwargs == bad_kw_snap
        assert str(project) == root_str_snap
        assert FROZEN_KWARGS == good_kw_snap
        assert FROZEN_GOLDEN == golden_snap
        assert FROZEN_RAW == raw_snap
    finally:
        if restore_fn is not None:
            restore_fn()

    assert _snapshot_tree(outer) == before_snap
    good_after = read_protected_main_approval_locked(project, **FROZEN_KWARGS)
    assert good_after == FROZEN_GOLDEN
    assert FROZEN_KWARGS == good_kw_snap
    assert FROZEN_GOLDEN == golden_snap
    assert FROZEN_RAW == raw_snap


def test_r1_positive_detachment_and_forwarding_spies(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"

    res = read_protected_main_approval_locked(project, **FROZEN_KWARGS)
    assert res == FROZEN_GOLDEN
    assert res["decision"] == FROZEN_DECISION
    assert res["raw_sha256"] == FROZEN_RAW_SHA256

    res["new_top_key"] = "mutation"
    res["decision"]["new_nested_key"] = "mutation"
    res["decision"]["decision_id"] = "MUTATED_ID"
    res["decision"]["prior_main_sha"] = "f" * 40
    del res["raw_sha256"]

    res_after = read_protected_main_approval_locked(project, **FROZEN_KWARGS)
    assert res_after == FROZEN_GOLDEN
    assert "new_top_key" not in res_after
    assert "new_nested_key" not in res_after["decision"]
    assert res_after["decision"]["decision_id"] == "OWNER-SYNTHETIC-1"
    assert FROZEN_DECISION["decision_id"] == "OWNER-SYNTHETIC-1"
    assert FROZEN_KWARGS["prior_main_sha"] == "a" * 40

    leaf_calls = []
    parser_calls = []
    orig_leaf = target_mod.read_protected_main_approval_leaf_locked
    orig_parser = target_mod.parse_protected_main_approval_record

    def spy_leaf(*args, **kwargs):
        leaf_calls.append((args, kwargs))
        return orig_leaf(*args, **kwargs)

    def spy_parser(*args, **kwargs):
        parser_calls.append((args, kwargs))
        return orig_parser(*args, **kwargs)

    with patch.object(target_mod, "read_protected_main_approval_leaf_locked", spy_leaf), \
         patch.object(target_mod, "parse_protected_main_approval_record", spy_parser):
        res_spy = target_mod.read_protected_main_approval_locked(project, **FROZEN_KWARGS)

    assert res_spy == FROZEN_GOLDEN
    assert len(leaf_calls) == 1
    assert leaf_calls[0] == ((project,), {})
    assert len(parser_calls) == 1
    p_args, p_kwargs = parser_calls[0]
    assert len(p_args) == 1
    assert p_args[0] == FROZEN_RAW
    assert p_kwargs == FROZEN_KWARGS

    rec_128 = copy.deepcopy(FROZEN_DECISION)
    rec_128["decision_id"] = "A" + "z" * 127
    raw_128 = _make_raw(rec_128)
    sha_128 = hashlib.sha256(raw_128).hexdigest().lower()
    golden_128 = {"decision": copy.deepcopy(rec_128), "raw_sha256": sha_128}

    leaf.write_bytes(raw_128)
    os.chmod(leaf, 0o600)
    res_128 = read_protected_main_approval_locked(project, **FROZEN_KWARGS)
    assert res_128 == golden_128
    leaf.write_bytes(FROZEN_RAW)
    os.chmod(leaf, 0o600)

    subclass_raw = _make_raw({k: StrSubclass(v) for k, v in FROZEN_DECISION.items()})
    leaf.write_bytes(subclass_raw)
    os.chmod(leaf, 0o600)
    res_subclass = read_protected_main_approval_locked(project, **FROZEN_KWARGS)
    assert res_subclass == FROZEN_GOLDEN
    leaf.write_bytes(FROZEN_RAW)
    os.chmod(leaf, 0o600)


def test_r2_binding_dimensions_matrix(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    specs = [
        ("prior_main_sha", 40, "a"),
        ("approved_main_sha", 40, "b"),
        ("prior_checkpoint_sha256", 64, "c"),
        ("prior_head", 40, "d"),
    ]
    for name, length, ch in specs:
        bads = [
            None,
            True,
            12345,
            (ch * length).encode("ascii"),
            [ch * length],
            {ch: length},
            StrSubclass(ch * length),
            ch * (length - 1),
            ch * (length + 1),
            "A" + ch * (length - 1),
            "g" * length,
        ]
        for bad_val in bads:
            bad_kw = copy.deepcopy(FROZEN_KWARGS)
            bad_kw[name] = bad_val
            _run_bridge_fault(
                project,
                "invalid owner approval binding",
                bad_kwargs=bad_kw,
            )

    leaf_spy_calls = 0
    parser_spy_calls = 0
    os_open_calls = 0
    os_stat_calls = 0
    os_read_calls = 0

    orig_leaf = target_mod.read_protected_main_approval_leaf_locked
    orig_parser = target_mod.parse_protected_main_approval_record
    orig_open = os.open
    orig_stat = os.stat
    orig_read = os.read

    def spy_no_leaf(*a, **kw):
        nonlocal leaf_spy_calls
        leaf_spy_calls += 1
        return orig_leaf(*a, **kw)

    def spy_no_parser(*a, **kw):
        nonlocal parser_spy_calls
        parser_spy_calls += 1
        return orig_parser(*a, **kw)

    def spy_no_open(*a, **kw):
        nonlocal os_open_calls
        os_open_calls += 1
        return orig_open(*a, **kw)

    def spy_no_stat(*a, **kw):
        nonlocal os_stat_calls
        os_stat_calls += 1
        return orig_stat(*a, **kw)

    def spy_no_read(*a, **kw):
        nonlocal os_read_calls
        os_read_calls += 1
        return orig_read(*a, **kw)

    bad_kw_root = copy.deepcopy(FROZEN_KWARGS)
    bad_kw_root["prior_main_sha"] = None
    bad_root_path = pathlib.Path("/nonexistent/bad/root/for/binding/test")

    with patch.object(target_mod, "read_protected_main_approval_leaf_locked", spy_no_leaf), \
         patch.object(target_mod, "parse_protected_main_approval_record", spy_no_parser), \
         patch("os.open", spy_no_open), \
         patch("os.stat", spy_no_stat), \
         patch("os.read", spy_no_read):
        with pytest.raises(ProtectedMainApprovalReaderError) as exc_info:
            target_mod.read_protected_main_approval_locked(bad_root_path, **bad_kw_root)
        assert str(exc_info.value) == "invalid owner approval binding"

    assert leaf_spy_calls == 0
    assert parser_spy_calls == 0
    assert os_open_calls == 0
    assert os_stat_calls == 0
    assert os_read_calls == 0


def test_r2_caller_hash_format_valid_mismatch_after_read_and_parser(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    specs = [
        ("prior_main_sha", "f" * 40),
        ("approved_main_sha", "f" * 40),
        ("prior_checkpoint_sha256", "f" * 64),
        ("prior_head", "f" * 40),
    ]
    for name, mismatch_hash in specs:
        bad_kw = copy.deepcopy(FROZEN_KWARGS)
        bad_kw[name] = mismatch_hash

        leaf_called = False
        parser_called = False
        orig_leaf = target_mod.read_protected_main_approval_leaf_locked
        orig_parser = target_mod.parse_protected_main_approval_record

        def spy_l(*a, **kw):
            nonlocal leaf_called
            leaf_called = True
            return orig_leaf(*a, **kw)

        def spy_p(*a, **kw):
            nonlocal parser_called
            parser_called = True
            return orig_parser(*a, **kw)

        with patch.object(target_mod, "read_protected_main_approval_leaf_locked", spy_l), \
             patch.object(target_mod, "parse_protected_main_approval_record", spy_p):
            _run_bridge_fault(
                project,
                "invalid owner approval file",
                bad_kwargs=bad_kw,
            )
        assert leaf_called
        assert parser_called


def test_r2_wire_record_payload_fields_matrix(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"

    for key in sorted(FROZEN_DECISION):
        for bad_val in (None, True, 42, ["item"], {"nested": "value"}):
            bad_rec = copy.deepcopy(FROZEN_DECISION)
            bad_rec[key] = bad_val
            bad_raw = _make_raw(bad_rec)

            def do_fault(raw=bad_raw):
                leaf.write_bytes(raw)
                os.chmod(leaf, 0o600)

            def undo_fault():
                leaf.write_bytes(FROZEN_RAW)
                os.chmod(leaf, 0o600)

            _run_bridge_fault(
                project,
                "invalid owner approval file",
                fault_fn=do_fault,
                restore_fn=undo_fault,
            )

        bad_rec_del = copy.deepcopy(FROZEN_DECISION)
        del bad_rec_del[key]
        bad_raw_del = _make_raw(bad_rec_del)

        def do_del(raw=bad_raw_del):
            leaf.write_bytes(raw)
            os.chmod(leaf, 0o600)

        def undo_del():
            leaf.write_bytes(FROZEN_RAW)
            os.chmod(leaf, 0o600)

        _run_bridge_fault(
            project,
            "invalid owner approval file",
            fault_fn=do_del,
            restore_fn=undo_del,
        )

    bad_rec_extra = copy.deepcopy(FROZEN_DECISION)
    bad_rec_extra["extra_key"] = "extra"
    bad_raw_extra = _make_raw(bad_rec_extra)

    def do_extra():
        leaf.write_bytes(bad_raw_extra)
        os.chmod(leaf, 0o600)

    def undo_extra():
        leaf.write_bytes(FROZEN_RAW)
        os.chmod(leaf, 0o600)

    _run_bridge_fault(
        project,
        "invalid owner approval file",
        fault_fn=do_extra,
        restore_fn=undo_extra,
    )

    for k, v in [("schema_version", "2.0"), ("kind", "WRONG"), ("reference", "refs/heads/other")]:
        bad_rec = copy.deepcopy(FROZEN_DECISION)
        bad_rec[k] = v
        bad_raw = _make_raw(bad_rec)

        def do_skr(raw=bad_raw):
            leaf.write_bytes(raw)
            os.chmod(leaf, 0o600)

        def undo_skr():
            leaf.write_bytes(FROZEN_RAW)
            os.chmod(leaf, 0o600)

        _run_bridge_fault(
            project,
            "invalid owner approval file",
            fault_fn=do_skr,
            restore_fn=undo_skr,
        )

    for k, mismatch in [
        ("prior_main_sha", "e" * 40),
        ("approved_main_sha", "e" * 40),
        ("prior_checkpoint_sha256", "e" * 64),
        ("prior_head", "e" * 40),
    ]:
        bad_rec = copy.deepcopy(FROZEN_DECISION)
        bad_rec[k] = mismatch
        bad_raw = _make_raw(bad_rec)

        def do_hm(raw=bad_raw):
            leaf.write_bytes(raw)
            os.chmod(leaf, 0o600)

        def undo_hm():
            leaf.write_bytes(FROZEN_RAW)
            os.chmod(leaf, 0o600)

        _run_bridge_fault(
            project,
            "invalid owner approval file",
            fault_fn=do_hm,
            restore_fn=undo_hm,
        )

    for bad_id in ("", "_lead_punct", "slash/sub", "ctrl\x00id", "nonascii\u200cid", "A" + "b" * 128):
        bad_rec = copy.deepcopy(FROZEN_DECISION)
        bad_rec["decision_id"] = bad_id
        bad_raw = _make_raw(bad_rec)

        def do_id(raw=bad_raw):
            leaf.write_bytes(raw)
            os.chmod(leaf, 0o600)

        def undo_id():
            leaf.write_bytes(FROZEN_RAW)
            os.chmod(leaf, 0o600)

        _run_bridge_fault(
            project,
            "invalid owner approval file",
            fault_fn=do_id,
            restore_fn=undo_id,
        )


def test_r2_raw_payload_and_canonical_failures(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"

    raw_dup = FROZEN_RAW.replace(b'"decision_id":', b'"decision_id":"OWNER-SYNTHETIC-1","decision_id":', 1)
    raw_nan = FROZEN_RAW.replace(b'"OWNER-SYNTHETIC-1"', b"NaN")
    raw_inf = FROZEN_RAW.replace(b'"OWNER-SYNTHETIC-1"', b"Infinity")

    raw_rev = (
        json.dumps(dict(reversed(list(sorted(FROZEN_DECISION.items())))), separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    assert raw_rev != FROZEN_RAW

    raw_spaced = (
        json.dumps(FROZEN_DECISION, sort_keys=True, separators=(", ", ": "), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    assert raw_spaced != FROZEN_RAW

    raw_bads = [
        b"",
        b"\xff\xfe\xfd",
        b'{"invalid json',
        b'["array_root"]\n',
        b'"scalar_root"\n',
        raw_dup,
        raw_nan,
        raw_inf,
        raw_rev,
        raw_spaced,
        FROZEN_RAW.rstrip(b"\n"),
        FROZEN_RAW + b"\n",
    ]
    for bad_raw in raw_bads:
        def do_raw(r=bad_raw):
            leaf.write_bytes(r)
            os.chmod(leaf, 0o600)

        def undo_raw():
            leaf.write_bytes(FROZEN_RAW)
            os.chmod(leaf, 0o600)

        _run_bridge_fault(
            project,
            "invalid owner approval file",
            fault_fn=do_raw,
            restore_fn=undo_raw,
        )


def test_r3_leaf_hygiene_prior_caller_context_and_base_exceptions(tmp_path: pathlib.Path):
    project = _setup_tree(tmp_path)
    leaf = project / ".local" / "two-site-refactor" / "autopilot" / "protected-main-approval.json"

    def do_bad_leaf_mode():
        os.chmod(leaf, 0o644)

    def undo_bad_leaf_mode():
        os.chmod(leaf, 0o600)

    _run_bridge_fault(
        project,
        "invalid owner approval file",
        fault_fn=do_bad_leaf_mode,
        restore_fn=undo_bad_leaf_mode,
    )

    def do_bad_parent_mode():
        os.chmod(project, 0o777)

    def undo_bad_parent_mode():
        os.chmod(project, 0o755)

    _run_bridge_fault(
        project,
        "invalid owner approval file",
        fault_fn=do_bad_parent_mode,
        restore_fn=undo_bad_parent_mode,
    )

    bad_root = project / "nonexistent_sub"
    _run_bridge_fault(
        project,
        "invalid owner approval file",
        bad_root=bad_root,
    )

    marker_meta = "UNIQUE_PRIOR_CALLER_METADATA_MARKER_998811"
    os.chmod(leaf, 0o644)
    try:
        raise ValueError(marker_meta)
    except ValueError:
        with pytest.raises(ProtectedMainApprovalReaderError) as exc_info:
            read_protected_main_approval_locked(project, **FROZEN_KWARGS)
        err = exc_info.value
        assert str(err) == "invalid owner approval file"
        assert err.__cause__ is None
        assert err.__suppress_context__ is True
        formatted = "".join(traceback.format_exception(err))
        assert marker_meta not in formatted
    finally:
        os.chmod(leaf, 0o600)

    marker_payload = "UNIQUE_PRIOR_CALLER_PAYLOAD_MARKER_998822"
    leaf.write_bytes(b"INVALID_JSON_PAYLOAD\n")
    os.chmod(leaf, 0o600)
    try:
        raise RuntimeError(marker_payload)
    except RuntimeError:
        with pytest.raises(ProtectedMainApprovalReaderError) as exc_info:
            read_protected_main_approval_locked(project, **FROZEN_KWARGS)
        err = exc_info.value
        assert str(err) == "invalid owner approval file"
        assert err.__cause__ is None
        assert err.__suppress_context__ is True
        formatted = "".join(traceback.format_exception(err))
        assert marker_payload not in formatted
    finally:
        leaf.write_bytes(FROZEN_RAW)
        os.chmod(leaf, 0o600)

    sentinel_ki = KeyboardInterrupt("sentinel leaf ki")
    with patch.object(target_mod, "read_protected_main_approval_leaf_locked", side_effect=sentinel_ki):
        with pytest.raises(KeyboardInterrupt) as exc_ki:
            read_protected_main_approval_locked(project, **FROZEN_KWARGS)
        assert exc_ki.value is sentinel_ki

    sentinel_se = SystemExit(77)
    with patch.object(target_mod, "parse_protected_main_approval_record", side_effect=sentinel_se):
        with pytest.raises(SystemExit) as exc_se:
            read_protected_main_approval_locked(project, **FROZEN_KWARGS)
        assert exc_se.value is sentinel_se
        assert exc_se.value.code == 77

    raw_4096 = b"{" + b"a" * 4094 + b" "
    assert len(raw_4096) == 4096
    assert MAX_PROTECTED_MAIN_APPROVAL_BYTES == 4096

    raw_4097 = b"{" + b"a" * 4095 + b" "
    assert len(raw_4097) == 4097

    parser_calls_count = 0
    orig_parser = target_mod.parse_protected_main_approval_record

    def counting_parser(*a, **kw):
        nonlocal parser_calls_count
        parser_calls_count += 1
        return orig_parser(*a, **kw)

    with patch.object(target_mod, "parse_protected_main_approval_record", counting_parser):
        parser_calls_count = 0
        leaf.write_bytes(raw_4096)
        os.chmod(leaf, 0o600)
        with pytest.raises(ProtectedMainApprovalReaderError) as ctx_4096:
            read_protected_main_approval_locked(project, **FROZEN_KWARGS)
        assert str(ctx_4096.value) == "invalid owner approval file"
        assert parser_calls_count == 1

        parser_calls_count = 0
        leaf.write_bytes(raw_4097)
        os.chmod(leaf, 0o600)
        with pytest.raises(ProtectedMainApprovalReaderError) as ctx_4097:
            read_protected_main_approval_locked(project, **FROZEN_KWARGS)
        assert str(ctx_4097.value) == "invalid owner approval file"
        assert parser_calls_count == 0

    leaf.write_bytes(FROZEN_RAW)
    os.chmod(leaf, 0o600)
    assert read_protected_main_approval_locked(project, **FROZEN_KWARGS) == FROZEN_GOLDEN
