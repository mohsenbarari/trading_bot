"""Tests for two_site_protected_main_approval_record wire parser."""

import copy
import hashlib
import json
import os
import re
import sys
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_SOURCE_PATH = os.path.join(_REPO_ROOT, "scripts", "two_site_protected_main_approval_record.py")
_TEST_PATH = os.path.abspath(__file__)

_src_stat = os.stat(_SOURCE_PATH)
if _src_stat.st_size > 8000:
    raise AssertionError(f"Source size {_src_stat.st_size} exceeds 8000 bytes")

_tst_stat = os.stat(_TEST_PATH)
if _tst_stat.st_size > 16000:
    raise AssertionError(f"Test size {_tst_stat.st_size} exceeds 16000 bytes")

if _src_stat.st_size + _tst_stat.st_size > 24000:
    raise AssertionError("Combined size exceeds 24000 bytes")

with open(_SOURCE_PATH, "rb") as _f:
    _src_bytes = _f.read(8001)
if len(_src_bytes) > 8000:
    raise AssertionError("Source exceeds 8000 bytes on bounded read")

with open(_TEST_PATH, "rb") as _f:
    _tst_bytes = _f.read(16001)
if len(_tst_bytes) > 16000:
    raise AssertionError("Test exceeds 16000 bytes on bounded read")

import scripts.two_site_protected_main_approval_record as target_mod
from scripts.two_site_protected_main_approval_record import (
    MAX_PROTECTED_MAIN_APPROVAL_BYTES,
    ProtectedMainApprovalRecordError,
    parse_protected_main_approval_record,
)


class StrSubclass(str):
    pass


class BytesSubclass(bytes):
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


def _make_raw(data):
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


FROZEN_RAW = _make_raw(FROZEN_DECISION)
FROZEN_RAW_SHA256 = hashlib.sha256(FROZEN_RAW).hexdigest().lower()
FROZEN_GOLDEN = {
    "decision": copy.deepcopy(FROZEN_DECISION),
    "raw_sha256": FROZEN_RAW_SHA256,
}


class TestTwoSiteProtectedMainApprovalRecord(unittest.TestCase):
    def _assert_negative_row(self, bad_raw, bad_kwargs, expected_msg):
        bad_raw_snap = copy.deepcopy(bad_raw)
        bad_kwargs_snap = copy.deepcopy(bad_kwargs)
        good_raw_snap = copy.deepcopy(FROZEN_RAW)
        good_kwargs_snap = copy.deepcopy(FROZEN_KWARGS)
        golden_snap = copy.deepcopy(FROZEN_GOLDEN)

        good_before = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_before, FROZEN_GOLDEN)
        self.assertEqual(FROZEN_RAW, good_raw_snap)
        self.assertEqual(FROZEN_KWARGS, good_kwargs_snap)
        self.assertEqual(FROZEN_GOLDEN, golden_snap)

        with self.assertRaises(ProtectedMainApprovalRecordError) as ctx:
            parse_protected_main_approval_record(bad_raw, **bad_kwargs)
        self.assertEqual(str(ctx.exception), expected_msg)

        self.assertEqual(bad_raw, bad_raw_snap)
        self.assertEqual(bad_kwargs, bad_kwargs_snap)
        self.assertEqual(FROZEN_RAW, good_raw_snap)
        self.assertEqual(FROZEN_KWARGS, good_kwargs_snap)
        self.assertEqual(FROZEN_GOLDEN, golden_snap)

        good_after = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_after, FROZEN_GOLDEN)
        self.assertEqual(bad_raw, bad_raw_snap)
        self.assertEqual(bad_kwargs, bad_kwargs_snap)
        self.assertEqual(FROZEN_RAW, good_raw_snap)
        self.assertEqual(FROZEN_KWARGS, good_kwargs_snap)
        self.assertEqual(FROZEN_GOLDEN, golden_snap)

    def test_r1_valid_record_detachment_and_custody(self):
        res = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(res, FROZEN_GOLDEN)
        res["decision"]["decision_id"] = "MUTATED_ID"
        res["decision"]["prior_main_sha"] = "f" * 40
        res_next = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(res_next, FROZEN_GOLDEN)
        self.assertEqual(FROZEN_DECISION["decision_id"], "OWNER-SYNTHETIC-1")

        rec_128 = copy.deepcopy(FROZEN_DECISION)
        rec_128["decision_id"] = "A" + "z" * 127
        raw_128 = _make_raw(rec_128)
        golden_128 = {"decision": copy.deepcopy(rec_128), "raw_sha256": hashlib.sha256(raw_128).hexdigest().lower()}
        self.assertEqual(parse_protected_main_approval_record(raw_128, **FROZEN_KWARGS), golden_128)

        rec_equal = copy.deepcopy(FROZEN_DECISION)
        rec_equal["approved_main_sha"] = "a" * 40
        kw_equal = copy.deepcopy(FROZEN_KWARGS)
        kw_equal["approved_main_sha"] = "a" * 40
        raw_equal = _make_raw(rec_equal)
        golden_equal = {"decision": copy.deepcopy(rec_equal), "raw_sha256": hashlib.sha256(raw_equal).hexdigest().lower()}
        self.assertEqual(parse_protected_main_approval_record(raw_equal, **kw_equal), golden_equal)

    def test_r2_binding_dimensions_matrix(self):
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
                ch.encode("ascii") * length,
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
                self._assert_negative_row(FROZEN_RAW, bad_kw, "invalid owner approval binding")

    def test_r2_binding_predecode_spy_when_raw_invalid(self):
        loads_count = 0
        orig_loads = json.loads

        def spy_loads(*args, **kwargs):
            nonlocal loads_count
            loads_count += 1
            return orig_loads(*args, **kwargs)

        bad_kw = copy.deepcopy(FROZEN_KWARGS)
        bad_kw["prior_main_sha"] = None
        good_before = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_before, FROZEN_GOLDEN)

        try:
            json.loads = spy_loads
            with self.assertRaises(ProtectedMainApprovalRecordError) as ctx:
                parse_protected_main_approval_record(b"INVALID_RAW_JSON", **bad_kw)
            self.assertEqual(str(ctx.exception), "invalid owner approval binding")
            self.assertEqual(loads_count, 0)
        finally:
            json.loads = orig_loads

        good_after = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_after, FROZEN_GOLDEN)

    def test_r3_field_matrix_and_positive_str_subclass_projection(self):
        for key in FROZEN_DECISION:
            for bad_val in (None, True, 42, ["item"], {"nested": "value"}):
                bad_rec = copy.deepcopy(FROZEN_DECISION)
                bad_rec[key] = bad_val
                self._assert_negative_row(_make_raw(bad_rec), FROZEN_KWARGS, "invalid owner approval record")
            bad_rec_del = copy.deepcopy(FROZEN_DECISION)
            del bad_rec_del[key]
            self._assert_negative_row(_make_raw(bad_rec_del), FROZEN_KWARGS, "invalid owner approval record")

        bad_rec_extra = copy.deepcopy(FROZEN_DECISION)
        bad_rec_extra["extra_key"] = "extra"
        self._assert_negative_row(_make_raw(bad_rec_extra), FROZEN_KWARGS, "invalid owner approval record")

        for k, v in [("schema_version", "2.0"), ("kind", "WRONG"), ("reference", "refs/heads/other")]:
            bad_rec = copy.deepcopy(FROZEN_DECISION)
            bad_rec[k] = v
            self._assert_negative_row(_make_raw(bad_rec), FROZEN_KWARGS, "invalid owner approval record")

        for k, mismatch in [
            ("prior_main_sha", "e" * 40),
            ("approved_main_sha", "e" * 40),
            ("prior_checkpoint_sha256", "e" * 64),
            ("prior_head", "e" * 40),
        ]:
            bad_rec = copy.deepcopy(FROZEN_DECISION)
            bad_rec[k] = mismatch
            self._assert_negative_row(_make_raw(bad_rec), FROZEN_KWARGS, "invalid owner approval record")

        for k, length in [
            ("prior_main_sha", 40),
            ("approved_main_sha", 40),
            ("prior_checkpoint_sha256", 64),
            ("prior_head", 40),
        ]:
            for bad_hash in ("A" + "a" * (length - 1), "g" * length, "a" * (length - 1)):
                bad_rec = copy.deepcopy(FROZEN_DECISION)
                bad_rec[k] = bad_hash
                self._assert_negative_row(_make_raw(bad_rec), FROZEN_KWARGS, "invalid owner approval record")

        for bad_id in ("", "_lead_punct", "slash/sub", "ctrl\x00id", "nonascii\u200cid", "A" + "b" * 128):
            bad_rec = copy.deepcopy(FROZEN_DECISION)
            bad_rec["decision_id"] = bad_id
            self._assert_negative_row(_make_raw(bad_rec), FROZEN_KWARGS, "invalid owner approval record")

        subclass_raw = _make_raw({k: StrSubclass(v) for k, v in FROZEN_DECISION.items()})
        self.assertEqual(parse_protected_main_approval_record(subclass_raw, **FROZEN_KWARGS), FROZEN_GOLDEN)

    def test_r4_raw_payload_and_canonical_failures(self):
        for bad_raw in ["not_bytes", bytearray(FROZEN_RAW), BytesSubclass(FROZEN_RAW), None, True, [1, 2]]:
            self._assert_negative_row(bad_raw, FROZEN_KWARGS, "invalid owner approval record")

        raw_dup = FROZEN_RAW.replace(b'"decision_id":', b'"decision_id":"OWNER-SYNTHETIC-1","decision_id":', 1)
        raw_nan = FROZEN_RAW.replace(b'"OWNER-SYNTHETIC-1"', b"NaN")
        raw_inf = FROZEN_RAW.replace(b'"OWNER-SYNTHETIC-1"', b"Infinity")
        raw_rev = (
            json.dumps(dict(reversed(list(sorted(FROZEN_DECISION.items())))), separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        self.assertNotEqual(raw_rev, FROZEN_RAW)

        raw_spaced = (
            json.dumps(FROZEN_DECISION, sort_keys=True, separators=(", ", ": "), ensure_ascii=False) + "\n"
        ).encode("utf-8")
        self.assertNotEqual(raw_spaced, FROZEN_RAW)

        for bad_raw in [
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
        ]:
            self._assert_negative_row(bad_raw, FROZEN_KWARGS, "invalid owner approval record")

    def test_r5_boundary_4096_and_4097(self):
        raw_4096 = b"{" + b"a" * 4094 + b" "
        raw_4097 = b"{" + b"a" * 4095 + b" "
        self.assertEqual(len(raw_4096), 4096)
        self.assertEqual(len(raw_4097), 4097)
        self.assertEqual(MAX_PROTECTED_MAIN_APPROVAL_BYTES, 4096)

        good_before = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_before, FROZEN_GOLDEN)

        loads_count = 0
        orig_loads = json.loads

        def counting_loads(*args, **kwargs):
            nonlocal loads_count
            loads_count += 1
            return orig_loads(*args, **kwargs)

        try:
            json.loads = counting_loads
            with self.assertRaises(ProtectedMainApprovalRecordError) as ctx_4096:
                parse_protected_main_approval_record(raw_4096, **FROZEN_KWARGS)
            self.assertEqual(str(ctx_4096.exception), "invalid owner approval record")
            self.assertGreaterEqual(loads_count, 1)

            loads_count = 0
            with self.assertRaises(ProtectedMainApprovalRecordError) as ctx_4097:
                parse_protected_main_approval_record(raw_4097, **FROZEN_KWARGS)
            self.assertEqual(str(ctx_4097.exception), "invalid owner approval record")
            self.assertEqual(loads_count, 0)
        finally:
            json.loads = orig_loads

        good_after = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_after, FROZEN_GOLDEN)

    def test_r6_decision_id_predicate_mutation_and_restoration(self):
        rec_slash = copy.deepcopy(FROZEN_DECISION)
        rec_slash["decision_id"] = "OWNER/SLASH-ID"
        raw_slash = _make_raw(rec_slash)
        raw_slash_sha256 = hashlib.sha256(raw_slash).hexdigest().lower()
        golden_slash = {"decision": copy.deepcopy(rec_slash), "raw_sha256": raw_slash_sha256}

        raw_slash_snap = copy.deepcopy(raw_slash)
        kw_snap = copy.deepcopy(FROZEN_KWARGS)
        good_raw_snap = copy.deepcopy(FROZEN_RAW)
        golden_slash_snap = copy.deepcopy(golden_slash)
        frozen_golden_snap = copy.deepcopy(FROZEN_GOLDEN)

        good_before = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_before, FROZEN_GOLDEN)

        with self.assertRaises(ProtectedMainApprovalRecordError) as ctx_before:
            parse_protected_main_approval_record(raw_slash, **FROZEN_KWARGS)
        self.assertEqual(str(ctx_before.exception), "invalid owner approval record")

        orig_predicate = target_mod._is_valid_decision_id
        try:
            def broadened_predicate(val: str) -> bool:
                if type(val) is not str:
                    return False
                return bool(re.fullmatch(r"^[A-Za-z0-9/._-]+$", val))

            target_mod._is_valid_decision_id = broadened_predicate

            res_broadened = parse_protected_main_approval_record(raw_slash, **FROZEN_KWARGS)
            self.assertEqual(res_broadened, golden_slash)
            self.assertEqual(res_broadened["raw_sha256"], raw_slash_sha256)
        finally:
            target_mod._is_valid_decision_id = orig_predicate

        self.assertIs(target_mod._is_valid_decision_id, orig_predicate)

        with self.assertRaises(ProtectedMainApprovalRecordError) as ctx_after:
            parse_protected_main_approval_record(raw_slash, **FROZEN_KWARGS)
        self.assertEqual(str(ctx_after.exception), "invalid owner approval record")

        good_after = parse_protected_main_approval_record(FROZEN_RAW, **FROZEN_KWARGS)
        self.assertEqual(good_after, FROZEN_GOLDEN)

        self.assertEqual(raw_slash, raw_slash_snap)
        self.assertEqual(FROZEN_KWARGS, kw_snap)
        self.assertEqual(FROZEN_RAW, good_raw_snap)
        self.assertEqual(golden_slash, golden_slash_snap)
        self.assertEqual(FROZEN_GOLDEN, frozen_golden_snap)


if __name__ == "__main__":
    unittest.main()
