"""Manual reader-compatible registration; author iran_sms_precheck/Codex."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
from pathlib import Path
import unittest
from unittest import mock

from tests import p1_00b_membership_history as history

ROOT = Path(__file__).resolve().parents[1]
PACKET = "docs/refactor/two-server/section-1/inventory/recent-characterization-sync-health-first-only-registration.json"
MODULE = "tests/test_p1_00b_characterization_sync_health_summary_execute_failure.py"
MODULE_SHA = "6f955361d65efaf3ba04a357a38108f4d49e6a9f2d4ed225556ccf371738d10d"
CLASS = "SyncHealthSummaryExecuteFailureCharacterizationTests"
METHOD = "test_first_summary_execute_exception_propagates_before_any_downstream_health_work"
SEED = "API-SYNC-GET-SYNC-GET-SYNC-HEALTH-L4890"
KEY = "sync_health_first_summary_execute_failure_registration_path"
PRIOR_KEY = "sync_parity_observability_downstream_failure_registration_path"
PRIOR_PACKET = "docs/refactor/two-server/section-1/inventory/recent-characterization-sync-parity-proof-data-integration.json"
LEDGER = "docs/refactor/two-server/section-1/00-stage-ledger.md"
PACKET_BYTES = 8703
PACKET_SHA = "603611e62557093b70e10c0c76717e796b10cb3c79c0ccf8b468f9e714c0529e"
BEFORE_SHA = "044dc7afa5b6491b1ab87b4eb9ab29dcde5ad1677400037c2c39e2f600db0c68"
AFTER_SHA = "ea23866be378404f12c3ae4475babf284aa834b3a1319cdf7e7bbdc8ee9ba54e"
LEDGER_SHA = "d9c33986c1559b00eded81378bd79d321902f9fd3626bfa54ad463f213c103e0"


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


def load_sync_health_first_only_registration_controls():
    raw = (ROOT / PACKET).read_bytes()
    packet = _decode(raw)
    _need(raw == _canonical(packet), "physical packet canonical bytes")
    manifest = _decode((ROOT / history.MANIFEST).read_bytes())
    return packet, manifest, (ROOT / LEDGER).read_text(encoding="utf-8")


def assert_sync_health_first_only_registration(packet, live_manifest, ledger_text):
    raw = _canonical(packet)
    _need(len(raw) == PACKET_BYTES and hashlib.sha256(raw).hexdigest() == PACKET_SHA,
          "supplied packet bytes/SHA")
    _types(live_manifest)
    _need(packet["status"] == packet["receipt_type"] == "PARTIAL_CANONICAL_INTEGRATION"
          and packet["source"] == {
              "accepted_source_commit": "ed55b9167b16f074bb39fdca65c68dc018224a23",
              "accepted_parent": "75fe8987cbf278b9c484381c99c6501e71a49533"},
          "standard canonical envelope")
    source = history.read_pin(MODULE, MODULE_SHA, 17219, root=ROOT)
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == CLASS]
    _need(len(classes) == 1 and classes[0].lineno == 38, "accepted selector class")
    methods = [node for node in classes[0].body if isinstance(node, ast.AsyncFunctionDef)
               and node.name == METHOD]
    _need(len(methods) == 1 and methods[0].lineno == 41, "accepted async selector")
    extra = {PACKET: packet}
    historical = history.historical_manifest(live_manifest, extra_documents=extra)
    before = copy.deepcopy(historical)
    before["evidence_sources"][PRIOR_KEY] = PRIOR_PACKET
    before["counts"] = dict(zip(history.COUNT_KEYS, (135, 265, 400, 65)))
    body = _canonical(before)
    _need(len(body) == 5482 and hashlib.sha256(body).hexdigest() == BEFORE_SHA, "before capture")
    surface, baseline, old = history.assert_live_membership(before, extra_documents=extra)
    _need(tuple(map(len, (surface, baseline, old))) == (465, 135, 400)
          and SEED in surface - old, "physical before membership")
    after = copy.deepcopy(before)
    after["evidence_sources"][KEY] = PACKET
    after["counts"] = dict(zip(history.COUNT_KEYS, (135, 266, 401, 64)))
    body = _canonical(after)
    _need(len(body) == 5662 and hashlib.sha256(body).hexdigest() == AFTER_SHA, "after capture")
    _, _, new = history.assert_live_membership(after, extra_documents=extra)
    _need(new - old == {SEED} and not old - new and len(new) == 401, "exact one seed delta")
    _need(list(live_manifest["evidence_sources"].items())[:35]
          == list(after["evidence_sources"].items()), "accepted mapping prefix")
    actual_surface, actual_baseline, actual = history.assert_live_membership(
        live_manifest, extra_documents=extra)
    _need(actual_surface == surface and actual_baseline == baseline and new <= actual,
          "full supplied live membership")
    _need(type(ledger_text) is str, "ledger text type")
    ledger = ledger_text.encode("utf-8")
    _need(len(ledger) >= 119459 and hashlib.sha256(ledger[:119459]).hexdigest() == LEDGER_SHA,
          "ledger immutable prefix")


class SyncHealthFirstOnlyRegistrationTests(unittest.TestCase):
    def test_controls_register_exact_one_seed_contribution_and_untouched_canonical_prefixes(self):
        controls = load_sync_health_first_only_registration_controls()
        before = copy.deepcopy(controls)
        assert_sync_health_first_only_registration(*controls)
        self.assertEqual(controls, before)
        with self.assertRaisesRegex(AssertionError, "duplicate"):
            _decode('{"a":1,"a":2}')
        packet, manifest, ledger = controls
        surface, _, evidenced = history.assert_live_membership(manifest, extra_documents={PACKET: packet})
        path = "docs/refactor/two-server/section-1/inventory/recent-characterization-growth-canary.json"
        future = copy.deepcopy(manifest)
        future["evidence_sources"]["growth_canary_path"] = path
        future["counts"] = dict(zip(history.COUNT_KEYS,
            (135, len(evidenced) + 1 - 135, len(evidenced) + 1, len(surface - evidenced) - 1)))
        doc = {path: {"records": [{"seed_ids": [sorted(surface - evidenced)[0]]}]}}
        original_history, original_live = history.historical_manifest, history.assert_live_membership
        def historical(value, **kwargs):
            return original_history(value, extra_documents={**doc, **kwargs.get("extra_documents", {})})
        def live(value, **kwargs):
            return original_live(value, extra_documents={**doc, **kwargs.get("extra_documents", {})})
        with mock.patch.object(history, "historical_manifest", side_effect=historical), \
             mock.patch.object(history, "assert_live_membership", side_effect=live):
            assert_sync_health_first_only_registration(packet, future, ledger + "\n### Later append\n")
        self.assertEqual(controls, before)

    def test_supplied_packet_mutations_are_rejected_without_mutation(self):
        packet, manifest, ledger = load_sync_health_first_only_registration_controls()
        original = copy.deepcopy((packet, manifest, ledger))
        cases = []
        def changed(label, mutate):
            value = copy.deepcopy(packet)
            mutate(value)
            cases.append((label, value))
        changed("type", lambda value: value.update(schema_version=True))
        cases.append(("order", dict(reversed(list(packet.items())))))
        changed("tuple", lambda value: value.update(records=tuple(value["records"])))
        changed("envelope", lambda value: value.update(status="REGISTERED_ON_EXACT_ACCEPTANCE"))
        changed("selector", lambda value: value["records"][0].update(test_selectors=["wrong.py::wrong"]))
        changed("unknown seed", lambda value: value["records"][0].update(seed_ids=["UNKNOWN"]))
        changed("false CID absence", lambda value: value["prior_failed_combined"].update(raw_init_conversation_id=None))
        changed("adoption", lambda value: value["manual_scope"].update(intercepted_task_accepted=True))
        changed("count", lambda value: value["manifest_registration"].update(raw_membership_after_on_exact_acceptance=402))
        changed("reconciliation", lambda value: value["reconciliation"].update(group_reconciliation_complete=True))
        changed("closure", lambda value: value.update(closures_authorized=True))
        with mock.patch.object(Path, "read_bytes", side_effect=RuntimeError("control reread")):
            for label, invalid in cases:
                with self.subTest(label=label):
                    before = repr(invalid)
                    with self.assertRaises(AssertionError):
                        assert_sync_health_first_only_registration(invalid, manifest, ledger)
                    self.assertEqual(repr(invalid), before)
        self.assertEqual((packet, manifest, ledger), original)

    def test_invalid_live_manifest_or_membership_is_rejected(self):
        packet, manifest, ledger = load_sync_health_first_only_registration_controls()
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
                assert_sync_health_first_only_registration(packet, invalid, ledger)
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
                assert_sync_health_first_only_registration(packet, invalid, ledger)

    def test_accepted_source_pin_and_ledger_prefix_mismatch_are_rejected(self):
        packet, manifest, ledger = load_sync_health_first_only_registration_controls()
        with mock.patch.object(history, "read_pin", side_effect=AssertionError("physical accepted pin")):
            with self.assertRaisesRegex(AssertionError, "physical accepted pin"):
                assert_sync_health_first_only_registration(packet, manifest, ledger)
        raw = ledger.encode("utf-8")
        for invalid in [b"X" + raw[1:], raw[:119458]]:
            with self.assertRaises(AssertionError):
                assert_sync_health_first_only_registration(packet, manifest, invalid.decode("utf-8"))
