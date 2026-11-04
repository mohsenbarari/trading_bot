"""Codex author iran_sms_precheck: witness of Root DATA, no Gemini authorship claim."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import unittest
from unittest import mock

from tests import p1_00b_membership_history as history

ROOT = Path(__file__).resolve().parents[1]
DATA = "docs/refactor/two-server/section-1/inventory/sync-parity-observability-proof-data.json"
DATA_BYTES = 13821
DATA_SHA256 = "36ce97b62b714e9cf6339a7bd06306ec6b18af495942ca7a72ddab2d8e283a71"


def _need(condition, message):
    if not condition:
        raise AssertionError(message)


def _json_types(value, depth=0):
    _need(depth <= 64, "JSON nesting exceeds bound")
    if type(value) is dict:
        _need(all(type(key) is str for key in value), "JSON keys must be strings")
        for item in value.values():
            _json_types(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _json_types(item, depth + 1)
    elif type(value) is float:
        _need(math.isfinite(value), "nonfinite JSON number")
    else:
        _need(type(value) in (str, int, bool, type(None)), "non-JSON supplied type")


def _canonical(value):
    _json_types(value)
    return (json.dumps(value, ensure_ascii=True, indent=2, allow_nan=False) + "\n").encode()


def _pairs(items):
    result = {}
    for key, value in items:
        _need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _decode(raw):
    value = json.loads(raw, object_pairs_hook=_pairs)
    _json_types(value)
    return value


def load_sync_parity_proof_data_controls():
    """Load the two tracked controls; private receipt paths stay inert DATA."""
    data_raw = (ROOT / DATA).read_bytes()
    packet = _decode(data_raw)
    _need(data_raw == _canonical(packet), "physical DATA is not exact canonical JSON")
    _need(len(data_raw) == DATA_BYTES and hashlib.sha256(data_raw).hexdigest() == DATA_SHA256,
          "physical DATA byte/SHA mismatch")
    return packet, _decode((ROOT / history.MANIFEST).read_bytes())


def assert_sync_parity_proof_data(packet, live_manifest):
    """Validate supplied controls, actual module pins, and independent live accounting."""
    raw = _canonical(packet)
    _need(len(raw) == DATA_BYTES and hashlib.sha256(raw).hexdigest() == DATA_SHA256,
          "supplied DATA byte/SHA mismatch")
    _json_types(live_manifest)
    accepted = packet["accepted_unit"]
    history.read_pin(accepted["module_path"], accepted["module_sha256"],
                     accepted["module_utf8_bytes"], root=ROOT)
    other = packet["accounting"]["other_unreconciled_accepted_units"][0]
    history.read_pin(other["path"], other["sha256"], other["utf8_bytes"], root=ROOT)
    capture = packet["at_capture"]
    historical = _canonical(history.historical_manifest(live_manifest))
    _need(len(historical) == capture["manifest_utf8_bytes"]
          and hashlib.sha256(historical).hexdigest() == capture["manifest_sha256"],
          "historical capture byte/SHA mismatch")
    history.assert_live_membership(live_manifest)


class SyncParityProofDataWitnessTests(unittest.TestCase):
    def test_controls_match_root_data_and_current_membership(self):
        packet, manifest = load_sync_parity_proof_data_controls()
        snapshot = copy.deepcopy((packet, manifest))
        assert_sync_parity_proof_data(packet, manifest)
        self.assertEqual((packet, manifest), snapshot)
        self.assertEqual(len(_canonical(packet)), DATA_BYTES)
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            _decode('{"same":1,"same":2}')

        # Pure supplied-document growth canary, never a future acceptance claim.
        future = copy.deepcopy(manifest)
        path = "docs/refactor/two-server/section-1/inventory/recent-characterization-proof-data-growth-canary.json"
        future["evidence_sources"]["proof_data_growth_canary_path"] = path
        surface, baseline, evidenced = history.assert_live_membership(manifest)
        new_ids = {seed for row in packet["records"] for seed in row["seed_ids"]}
        promoted = evidenced | new_ids
        future["counts"] = dict(zip(history.COUNT_KEYS, (
            len(baseline), len(promoted - baseline), len(promoted), len(surface - promoted))))
        extra = {path: {"records": [{"seed_ids": sorted(new_ids)}]}}
        original_historical, original_live = history.historical_manifest, history.assert_live_membership
        with mock.patch.object(history, "historical_manifest",
                               side_effect=lambda value: original_historical(value, extra_documents=extra)), \
             mock.patch.object(history, "assert_live_membership",
                               side_effect=lambda value: original_live(value, extra_documents=extra)):
            assert_sync_parity_proof_data(packet, future)
        self.assertGreaterEqual(len(promoted), 400)
        self.assertEqual((packet, manifest), snapshot)

    def test_supplied_packet_mutations_are_rejected_without_mutation(self):
        packet, manifest = load_sync_parity_proof_data_controls()
        originals = copy.deepcopy((packet, manifest))
        cases = []

        def changed(label, mutate):
            value = copy.deepcopy(packet)
            mutate(value)
            cases.append((label, value))

        changed("shape", lambda value: value.pop("origin_matrix_unresolved_cells"))
        cases.append(("root order", dict(reversed(list(packet.items())))))
        changed("integer type", lambda value: value.update(schema_version=True))
        changed("tuple", lambda value: value.update(records=tuple(value["records"])))
        changed("key type", lambda value: value.update({1: "invalid"}))
        changed("nonfinite", lambda value: value.update(origin_matrix_unresolved_cells=float("nan")))
        changed("seed", lambda value: value["records"][0].update(seed_ids=["UNKNOWN-SEED"]))
        changed("selector", lambda value: value["records"][0].update(test_selectors=["wrong.py::wrong"]))
        changed("contract", lambda value: value["records"][0].update(characterized_contract={}))
        changed("exclusion", lambda value: value["records"][0].update(exclusions={}))
        changed("accepted proof", lambda value: value["accepted_unit"].update(qoder_decision="REQUEST_CHANGES"))
        changed("capture", lambda value: value["at_capture"]["counts"].update(evidenced_seed_count=400))
        changed("future prerequisite", lambda value: value["canonical_integration"]["conditional_B_target"].update(requires="failed A accepted"))
        changed("gate", lambda value: value.update(closures_authorized=True))
        with mock.patch.object(Path, "read_bytes", side_effect=RuntimeError("supplied DATA reread")):
            for label, invalid in cases:
                with self.subTest(label=label):
                    before = repr(invalid)
                    with self.assertRaises(AssertionError):
                        assert_sync_parity_proof_data(invalid, manifest)
                    self.assertEqual(repr(invalid), before)
        self.assertEqual((packet, manifest), originals)

    def test_invalid_supplied_current_manifest_is_rejected(self):
        packet, manifest = load_sync_parity_proof_data_controls()
        originals = copy.deepcopy((packet, manifest))
        bad_count = copy.deepcopy(manifest)
        bad_count["counts"]["evidenced_seed_count"] += 1
        bad_prefix = copy.deepcopy(manifest)
        first_key = next(iter(bad_prefix["evidence_sources"]))
        bad_prefix["evidence_sources"][first_key] += ".wrong"
        removed = copy.deepcopy(manifest)
        removed["evidence_sources"].pop(next(iter(removed["evidence_sources"])))
        for label, invalid in [("count", bad_count), ("prefix", bad_prefix), ("removed", removed)]:
            with self.subTest(label=label):
                before = copy.deepcopy(invalid)
                with self.assertRaises(AssertionError):
                    assert_sync_parity_proof_data(packet, invalid)
                self.assertEqual(invalid, before)
        unknown = copy.deepcopy(manifest)
        path = "docs/refactor/two-server/section-1/inventory/recent-characterization-proof-data-unknown-canary.json"
        unknown["evidence_sources"]["proof_data_unknown_canary_path"] = path
        extra = {path: {"records": [{"seed_ids": ["UNKNOWN-SEED"]}]}}
        original = history.historical_manifest
        with mock.patch.object(history, "historical_manifest",
                               side_effect=lambda value: original(value, extra_documents=extra)):
            with self.assertRaises(AssertionError):
                assert_sync_parity_proof_data(packet, unknown)
        self.assertEqual((packet, manifest), originals)

    def test_physical_module_pin_mismatch_is_rejected(self):
        packet, manifest = load_sync_parity_proof_data_controls()
        original = history.read_pin
        targets = [packet["accepted_unit"]["module_path"],
                   packet["accounting"]["other_unreconciled_accepted_units"][0]["path"]]
        for target in targets:
            def reject(path, *args, **kwargs):
                if path == target:
                    raise AssertionError("physical module pin mismatch sentinel")
                return original(path, *args, **kwargs)

            with self.subTest(path=target), mock.patch.object(history, "read_pin", side_effect=reject):
                with self.assertRaisesRegex(AssertionError, "physical module pin mismatch sentinel"):
                    assert_sync_parity_proof_data(packet, manifest)
