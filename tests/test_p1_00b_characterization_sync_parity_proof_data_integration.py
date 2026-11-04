"""Owner-authorized manual integration; author iran_sms_precheck/Codex."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import unittest
from unittest import mock

from tests import p1_00b_membership_history as history
from tests import test_p1_00b_characterization_sync_parity_observability_proof_data_witness as proof

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = "docs/refactor/two-server/section-1/inventory/recent-characterization-sync-parity-proof-data-integration.json"
LEDGER = "docs/refactor/two-server/section-1/00-stage-ledger.md"
KEY = "sync_parity_observability_downstream_failure_registration_path"
WITNESS = "tests/test_p1_00b_characterization_sync_parity_observability_proof_data_witness.py"
WITNESS_SHA = "de22a42de323891fb030c657e3b1ef0f4d8396c69329f020795c628035822246"
RECEIPT_BYTES = 7811
RECEIPT_SHA = "6f673765a3b7dcdfbd2668abbaa244adb15d8e0e9c29065bdb6d3e49a669eafd"
MANIFEST_SHA = "044dc7afa5b6491b1ab87b4eb9ab29dcde5ad1677400037c2c39e2f600db0c68"
PREFIX_BYTES = 116148
PREFIX_SHA = "80b1fa5e71bbf440a2ce09f3236f935cbf0882ce2ecd1cc80679a6765912dbdb"
APPEND_BYTES = 3311
APPEND_SHA = "70f22f67246276076d0276b6eddc6af0ba36d60c0cf3e362a3109a11a2e87242"
ANCHOR = '<a id="sync-parity-owner-manual-canonical-integration"></a>'


def _need(value, message):
    if not value:
        raise AssertionError(message)


def _types(value, depth=0):
    _need(depth <= 64, "JSON nesting")
    if type(value) is dict:
        _need(all(type(key) is str for key in value), "JSON key type")
        for item in value.values():
            _types(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _types(item, depth + 1)
    elif type(value) is float:
        _need(math.isfinite(value), "nonfinite JSON")
    else:
        _need(type(value) in (str, int, bool, type(None)), "non-JSON type")


def _canonical(value):
    _types(value)
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def _pairs(items):
    value = {}
    for key, item in items:
        _need(key not in value, "duplicate JSON key")
        value[key] = item
    return value


def _decode(raw):
    value = json.loads(raw, object_pairs_hook=_pairs)
    _types(value)
    return value


def load_sync_parity_integration_controls():
    packet, manifest = proof.load_sync_parity_proof_data_controls()
    raw = (ROOT / RECEIPT).read_bytes()
    receipt = _decode(raw)
    _need(raw == _canonical(receipt), "physical receipt canonical bytes")
    return packet, receipt, manifest, (ROOT / LEDGER).read_text(encoding="utf-8")


def assert_sync_parity_integration(packet, receipt, live_manifest, ledger_text):
    raw = _canonical(receipt)
    _need(len(raw) == RECEIPT_BYTES and hashlib.sha256(raw).hexdigest() == RECEIPT_SHA,
          "supplied receipt bytes/SHA")
    _types(live_manifest)
    history.read_pin(WITNESS, WITNESS_SHA, 9649, root=ROOT)
    extra = {RECEIPT: receipt}
    historical = history.historical_manifest(live_manifest, extra_documents=extra)
    # The accepted two-argument API sees398, not a physical B receipt reread.
    proof.assert_sync_parity_proof_data(packet, historical)
    surface, baseline, before = history.assert_live_membership(historical)
    _need(tuple(map(len, (surface, baseline, before))) == (465, 135, 398), "historical sets")
    ids = [seed for row in receipt["records"] for seed in row["seed_ids"]]
    _need(len(ids) == len(set(ids)) == 2 and set(ids) <= surface and not set(ids) & before,
          "exact two new known seeds")
    _need(ids == [row["seed_id"] for row in packet["records"]], "DATA seed order")
    projected = copy.deepcopy(historical)
    projected["evidence_sources"][KEY] = RECEIPT
    projected["counts"] = dict(zip(history.COUNT_KEYS, (135, 265, 400, 65)))
    projected_raw = _canonical(projected)
    _need(len(projected_raw) == 5482 and hashlib.sha256(projected_raw).hexdigest() == MANIFEST_SHA,
          "captured B manifest")
    _, _, after = history.assert_live_membership(projected, extra_documents=extra)
    _need(after - before == set(ids) and not before - after and len(after) == 400, "actual B delta")
    _need(list(live_manifest["evidence_sources"].items())[:34]
          == list(projected["evidence_sources"].items()), "accepted B prefix")
    actual_surface, actual_baseline, actual = history.assert_live_membership(live_manifest, extra_documents=extra)
    _need(actual_surface == surface and actual_baseline == baseline and after <= actual,
          "full dynamic membership")
    _need(type(ledger_text) is str, "ledger text type")
    body = ledger_text.encode("utf-8")
    _need(len(body) >= PREFIX_BYTES + APPEND_BYTES, "ledger segment truncated")
    _need(hashlib.sha256(body[:PREFIX_BYTES]).hexdigest() == PREFIX_SHA, "ledger prefix changed")
    segment = body[PREFIX_BYTES:PREFIX_BYTES + APPEND_BYTES]
    _need(hashlib.sha256(segment).hexdigest() == APPEND_SHA and ledger_text.count(ANCHOR) == 1,
          "own ledger segment/anchor changed")


class SyncParityProofDataIntegrationTests(unittest.TestCase):
    def test_controls_register_exact_two_seed_contribution_and_eof_append(self):
        controls = load_sync_parity_integration_controls()
        snapshot = copy.deepcopy(controls)
        assert_sync_parity_integration(*controls)
        self.assertEqual(controls, snapshot)
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            _decode('{"x":1,"x":2}')
        packet, receipt, manifest, ledger = controls
        # Pure later401 DATA canary; never a future acceptance claim.
        surface, _, evidenced = history.assert_live_membership(manifest)
        seed = sorted(surface - evidenced)[0]
        future = copy.deepcopy(manifest)
        path = "docs/refactor/two-server/section-1/inventory/recent-characterization-growth-canary.json"
        future["evidence_sources"]["growth_canary_path"] = path
        future["counts"] = dict(zip(history.COUNT_KEYS,
            (135, len(evidenced) + 1 - 135, len(evidenced) + 1, len(surface - evidenced) - 1)))
        doc = {path: {"records": [{"seed_ids": [seed]}]}}
        original_historical, original_live = history.historical_manifest, history.assert_live_membership
        def historical(value, **kwargs):
            return original_historical(value, extra_documents={**doc, **kwargs.get("extra_documents", {})})
        def live(value, **kwargs):
            return original_live(value, extra_documents={**doc, **kwargs.get("extra_documents", {})})
        with mock.patch.object(history, "historical_manifest", side_effect=historical), \
             mock.patch.object(history, "assert_live_membership", side_effect=live):
            assert_sync_parity_integration(packet, receipt, future, ledger + "\n### Later append\n")
        self.assertEqual(controls, snapshot)

    def test_supplied_receipt_mutations_are_rejected_without_mutation(self):
        packet, receipt, manifest, ledger = load_sync_parity_integration_controls()
        original = copy.deepcopy((packet, receipt, manifest, ledger))
        cases = []
        def changed(label, mutate):
            value = copy.deepcopy(receipt)
            mutate(value)
            cases.append((label, value))
        changed("shape", lambda value: value.pop("accounting"))
        cases.append(("order", dict(reversed(list(receipt.items())))))
        changed("type", lambda value: value.update(schema_version=True))
        changed("tuple", lambda value: value.update(records=tuple(value["records"])))
        changed("reference", lambda value: value["proof_data_reference"].update(sha256="0" * 64))
        changed("manual", lambda value: value["manual_exception"].update(failed_attempt_accepted=True))
        changed("seed", lambda value: value["records"][0].update(seed_ids=["UNKNOWN"]))
        changed("selector", lambda value: value["records"][0].update(test_selectors=["wrong.py::wrong"]))
        changed("gate", lambda value: value.update(closures_authorized=True))
        with mock.patch.object(Path, "read_bytes", side_effect=RuntimeError("control reread")):
            for label, invalid in cases:
                with self.subTest(label=label):
                    before = repr(invalid)
                    with self.assertRaises(AssertionError):
                        assert_sync_parity_integration(packet, invalid, manifest, ledger)
                    self.assertEqual(repr(invalid), before)
        with mock.patch.object(history, "read_pin", side_effect=AssertionError("physical witness mismatch")):
            with self.assertRaisesRegex(AssertionError, "physical witness mismatch"):
                assert_sync_parity_integration(packet, receipt, manifest, ledger)
        self.assertEqual((packet, receipt, manifest, ledger), original)

    def test_invalid_live_membership_is_rejected(self):
        packet, receipt, manifest, ledger = load_sync_parity_integration_controls()
        for label in ("count", "prefix", "removed"):
            invalid = copy.deepcopy(manifest)
            if label == "count":
                invalid["counts"]["evidenced_seed_count"] += 1
            elif label == "prefix":
                invalid["evidence_sources"][next(iter(invalid["evidence_sources"]))] += ".wrong"
            else:
                invalid["evidence_sources"].pop(KEY)
            before = copy.deepcopy(invalid)
            with self.subTest(label=label), self.assertRaises(AssertionError):
                assert_sync_parity_integration(packet, receipt, invalid, ledger)
            self.assertEqual(invalid, before)
        invalid = copy.deepcopy(manifest)
        path = "docs/refactor/two-server/section-1/inventory/recent-characterization-unknown-canary.json"
        invalid["evidence_sources"]["unknown_canary_path"] = path
        original = history.historical_manifest
        def unknown(value, **kwargs):
            extra = {**kwargs.get("extra_documents", {}), path: {"records": [{"seed_ids": ["UNKNOWN"]}]}}
            return original(value, extra_documents=extra)
        with mock.patch.object(history, "historical_manifest", side_effect=unknown):
            with self.assertRaises(AssertionError):
                assert_sync_parity_integration(packet, receipt, invalid, ledger)

    def test_ledger_prefix_and_append_mutations_are_rejected(self):
        packet, receipt, manifest, ledger = load_sync_parity_integration_controls()
        body = ledger.encode("utf-8")
        cases = [b"X" + body[1:], body[:PREFIX_BYTES],
                 body[:PREFIX_BYTES] + b"X" + body[PREFIX_BYTES + 1:], body + ANCHOR.encode()]
        for index, invalid in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(AssertionError):
                assert_sync_parity_integration(packet, receipt, manifest, invalid.decode("utf-8"))
