"""Accepted membership-history DATA and live accounting checks; never execute blobs."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat

import tests.test_p1_00b_characterization_notification_mark_all_accounting as accounting

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "tests/fixtures/p1_00b_membership_history"
INDEX_SHA256 = "7d7b49bc00c3fe09c86f89c096a43fccd76bdb30755621c2a6c1cb2189da4948"
ACCEPTED_SOURCE = "3ac62a6396f92d5c4d330b76843ca88cfdcb4ae0"
MANIFEST = accounting.MANIFEST
COUNT_KEYS = ("baseline_evidenced_seed_count", "reconciled_new_seed_count",
              "evidenced_seed_count", "remaining_seed_count")
SET_SHA256 = (
    "f3fed11dff487348bc998306b4eb8c17657e00d81c794bb09a1b3cbdc3f28d91",
    "6a8018ea4a8ee0366cff417bd8f008658c95d9ee9f9d0bc759beafb029161648",
    "a7e5c29f5075d59c357a369d8a403ff74d5a357debbdbd7a4590c15a1d017ff7",
)


def _need(value, reason):
    if not value:
        raise AssertionError(reason)


def _same(actual, expected):
    if type(actual) is not type(expected):
        return False
    if type(expected) is dict:
        return list(actual) == list(expected) and all(_same(actual[k], v) for k, v in expected.items())
    if type(expected) in (list, tuple):
        return len(actual) == len(expected) and all(_same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def _raw(path):
    path = Path(path)
    _need(path.is_absolute() and path.resolve(strict=True) == path, "historical data path must be canonical")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and not before.st_mode & 0o022
              and 0 < before.st_size <= 2_000_000, "historical data custody mismatch")
        parts = []; total = 0
        while block := os.read(descriptor, 65536):
            total += len(block)
            _need(total <= 2_000_000, "historical data exceeds bound")
            parts.append(block)
        after = os.fstat(descriptor); final = path.lstat()
        keys = ("st_dev", "st_ino", "st_uid", "st_nlink", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
        _need(total == before.st_size and all(getattr(before, k) == getattr(after, k) == getattr(final, k) for k in keys),
              "historical data changed during read")
        return b"".join(parts)
    finally:
        os.close(descriptor)


def _index():
    raw = _raw(ROOT / FIXTURE / "index.json")
    _need(hashlib.sha256(raw).hexdigest() == INDEX_SHA256, "historical data index SHA mismatch")
    value = json.loads(raw)
    _need(value["schema_version"] == 1 and value["accepted_source_commit"] == ACCEPTED_SOURCE,
          "historical accepted Source mismatch")
    return value["blobs"]


def historical_blob(path, expected_sha256, expected_bytes):
    """Return verified old accepted file bytes as DATA, never load them as code."""
    pins = _index()
    _need(type(path) is str and path in pins, "path is not in accepted historical data")
    pin = pins[path]
    _need(type(expected_bytes) is int and expected_bytes == pin["bytes"]
          and type(expected_sha256) is str and expected_sha256 == pin["sha256"],
          "requested historical pin is not the accepted original pin")
    raw = _raw(ROOT / FIXTURE / "blobs" / (pin["sha256"] + ".txt"))
    _need(len(raw) == expected_bytes and hashlib.sha256(raw).hexdigest() == expected_sha256,
          "historical data bytes/SHA mismatch")
    return raw


def read_pin(path, sha, size, *, root=None):
    """Changed historical owner pins use accepted data; other files stay physical."""
    pins = _index()
    if path in pins and path.startswith("tests/"):
        return historical_blob(path, sha, size)
    _need(type(path) is str and not Path(path).is_absolute() and ".." not in Path(path).parts,
          "physical pin path is invalid")
    raw = _raw(Path(root) / path if root is not None else accounting.ROOT / path)
    _need(type(size) is int and len(raw) == size and hashlib.sha256(raw).hexdigest() == sha,
          "physical pin bytes/SHA mismatch")
    return raw


def _historical():
    pin = _index()[MANIFEST]
    return json.loads(historical_blob(MANIFEST, pin["sha256"], pin["bytes"]))


def _digest(values):
    return hashlib.sha256(json.dumps(sorted(values), ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _validate_live(manifest, extra_documents, *, earlier_prefix):
    old = _historical()
    _need(type(manifest) is dict and list(manifest) == list(old), "current manifest root keys/order mismatch")
    for key, value in old.items():
        if key not in {"counts", "evidence_sources"}:
            _need(_same(manifest[key], value), "current immutable manifest root mismatch: " + key)
    sources = manifest["evidence_sources"]
    _need(type(sources) is dict and all(type(k) is str and k and type(v) is str and v for k, v in sources.items()),
          "current evidence source entries invalid")
    prefix = list(old["evidence_sources"].items()); current = list(sources.items())
    _need(_same(current[:len(prefix)], prefix[:len(current)]), "historical evidence prefix/path/order changed")
    if not earlier_prefix:
        _need(len(current) >= len(prefix), "current historical evidence mapping removed")
    counts = manifest["counts"]
    _need(type(counts) is dict and list(counts) == list(COUNT_KEYS)
          and all(type(v) is int for v in counts.values()), "current counts must be exact ordered integers")
    reader = accounting.NotificationMarkAllAccountingTests("runTest")
    receipt = reader._json(accounting.RECEIPT)
    historical_sets = reader._derive(old, receipt)
    _need(tuple(map(len, historical_sets)) == (465, 135, 398)
          and tuple(_digest(s) for s in historical_sets) == SET_SHA256,
          "physical historical membership changed")
    surface, baseline, evidenced = reader._derive(manifest, receipt, extra_documents=extra_documents)
    _need(surface == historical_sets[0] and baseline == historical_sets[1]
          and historical_sets[2] <= evidenced <= surface, "current membership removes history or adds unknown seeds")
    expected = dict(zip(COUNT_KEYS, (len(baseline), len(evidenced - baseline), len(evidenced), len(surface - evidenced))))
    _need(_same(counts, expected), "current declared counts mismatch actual physical membership")
    return old, (surface, baseline, evidenced)


def assert_live_membership(manifest, *, extra_documents=None):
    """Validate the actual full live manifest independently of the historical view."""
    return _validate_live(manifest, extra_documents, earlier_prefix=False)[1]


def historical_manifest(manifest, *, extra_documents=None):
    """Validate supplied live state, then project only its accepted historical epoch.

    Earlier exact prefixes support existing nested pre-owner assertions. New
    mappings remain validated by live accounting but cannot rewrite old398 pins.
    """
    old, _ = _validate_live(manifest, extra_documents, earlier_prefix=True)
    old["evidence_sources"] = dict(list(manifest["evidence_sources"].items())[:len(old["evidence_sources"])])
    return copy.deepcopy(old)
