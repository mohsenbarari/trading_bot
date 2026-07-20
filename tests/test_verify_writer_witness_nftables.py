from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import subprocess
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/verify_writer_witness_nftables.py"
SPEC = importlib.util.spec_from_file_location("verify_writer_witness_nftables", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
nft = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = nft
SPEC.loader.exec_module(nft)


def valid_payload() -> dict[str, object]:
    return {
        "nftables": [
            {
                "metainfo": {
                    "version": "1.0.9",
                    "release_name": "Old Doc Yak #3",
                    "json_schema_version": 1,
                }
            },
            {"table": {"family": "ip", "name": "filter", "handle": 1}},
            {
                "chain": {
                    "family": "ip",
                    "table": "filter",
                    "name": "INPUT",
                    "handle": 2,
                    "type": "filter",
                    "hook": "input",
                    "prio": 0,
                    "policy": "drop",
                }
            },
            {
                "rule": {
                    "family": "ip",
                    "table": "filter",
                    "chain": "INPUT",
                    "handle": 3,
                    "expr": [
                        {"counter": {"packets": 12, "bytes": 3456}},
                        {
                            "match": {
                                "op": "==",
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": 443,
                            }
                        },
                        {"accept": None},
                    ],
                    "comment": "Writer Witness HTTPS from FI",
                }
            },
            {"table": {"family": "ip6", "name": "filter", "handle": 4}},
            {
                "chain": {
                    "family": "ip6",
                    "table": "filter",
                    "name": "INPUT",
                    "handle": 5,
                    "type": "filter",
                    "hook": "input",
                    "prio": 0,
                    "policy": "drop",
                }
            },
            {
                "rule": {
                    "family": "ip6",
                    "table": "filter",
                    "chain": "INPUT",
                    "handle": 6,
                    "expr": [
                        {"counter": {"packets": 0, "bytes": 0}},
                        {"drop": None},
                    ],
                    "comment": "IPv6 denied",
                }
            },
        ]
    }


def encode(payload: object) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")


class SuccessfulAttestationTests(unittest.TestCase):
    def test_cli_rejects_unisolated_python_before_argument_parsing(self) -> None:
        completed = subprocess.run(
            ["/usr/bin/python3.12", str(MODULE_PATH)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("requires isolated clean Python startup", completed.stderr)

    def test_exact_policy_passes_and_emits_deterministic_counts(self) -> None:
        value = encode(valid_payload())
        snapshot = nft.inspect_ruleset(value)
        result = nft.attest_ruleset(value, snapshot.policy_sha256)
        self.assertEqual(
            result,
            {
                "chain_count": 2,
                "counter_count": 2,
                "expression_count": 5,
                "nftables_policy_attested": "yes",
                "policy_sha256": snapshot.policy_sha256,
                "rule_count": 2,
                "table_count": 2,
            },
        )
        self.assertRegex(snapshot.policy_sha256, r"^[0-9a-f]{64}$")

    def test_handle_counter_and_metainfo_changes_are_runtime_stable(self) -> None:
        baseline = valid_payload()
        changed = copy.deepcopy(baseline)
        changed_entries = changed["nftables"]
        assert isinstance(changed_entries, list)
        metainfo = changed_entries[0]["metainfo"]
        assert isinstance(metainfo, dict)
        metainfo.update({"version": "9.9.9", "host": "runtime-host"})
        for offset, entry in enumerate(changed_entries[1:], start=100):
            body = next(iter(entry.values()))
            assert isinstance(body, dict)
            body["handle"] = offset
        first_rule = changed_entries[3]["rule"]
        second_rule = changed_entries[6]["rule"]
        assert isinstance(first_rule, dict) and isinstance(second_rule, dict)
        first_rule["expr"][0]["counter"] = {"packets": 999, "bytes": 123456789}
        second_rule["expr"][0]["counter"] = {"packets": 8, "bytes": 4096}
        self.assertEqual(
            nft.inspect_ruleset(encode(baseline)).policy_sha256,
            nft.inspect_ruleset(encode(changed)).policy_sha256,
        )

    def test_cli_outputs_one_canonical_json_document(self) -> None:
        value = encode(valid_payload())
        expected = nft.inspect_ruleset(value).policy_sha256
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(nft, "_require_isolated_startup"), redirect_stdout(stdout), redirect_stderr(stderr):
            status = nft.main(
                ["--expected-policy-sha256", expected], stdin=io.BytesIO(value)
            )
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        raw = stdout.getvalue()
        self.assertEqual(raw.count("\n"), 1)
        self.assertEqual(
            raw, json.dumps(json.loads(raw), separators=(",", ":"), sort_keys=True) + "\n"
        )

    def test_emit_binding_produces_a_reviewable_non_self_approving_pin(self) -> None:
        value = encode(valid_payload())
        stdout = io.StringIO()
        with (
            mock.patch.object(nft, "_require_isolated_startup"),
            redirect_stdout(stdout),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                nft.main(["--emit-policy-binding"], stdin=io.BytesIO(value)),
                0,
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schema_version"], "writer_witness_nftables_policy_v1")
        self.assertEqual(payload["policy_sha256"], nft.inspect_ruleset(value).policy_sha256)


class SemanticDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = valid_payload()
        self.expected = nft.inspect_ruleset(encode(self.baseline)).policy_sha256

    def assert_digest_rejected(self, changed: object) -> None:
        with self.assertRaisesRegex(nft.NftablesAttestationError, "does not match expected"):
            nft.attest_ruleset(encode(changed), self.expected)

    def test_added_unmanaged_table_is_rejected_before_digest_comparison(self) -> None:
        changed = copy.deepcopy(self.baseline)
        changed["nftables"].append({"table": {"family": "inet", "name": "native"}})
        with self.assertRaisesRegex(nft.NftablesAttestationError, "only declared tables"):
            nft.attest_ruleset(encode(changed), self.expected)

    def test_added_rule_is_rejected(self) -> None:
        changed = copy.deepcopy(self.baseline)
        changed["nftables"].insert(
            4,
            {
                "rule": {
                    "family": "ip",
                    "table": "filter",
                    "chain": "INPUT",
                    "handle": 1000,
                    "expr": [{"accept": None}],
                }
            },
        )
        self.assert_digest_rejected(changed)

    def test_added_accept_path_is_rejected(self) -> None:
        changed = copy.deepcopy(self.baseline)
        changed["nftables"][6]["rule"]["expr"].insert(1, {"accept": None})
        self.assert_digest_rejected(changed)

    def test_comment_and_chain_policy_changes_are_rejected(self) -> None:
        for mutation in ("comment", "policy"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(self.baseline)
                if mutation == "comment":
                    changed["nftables"][3]["rule"]["comment"] = "broader traffic"
                else:
                    changed["nftables"][2]["chain"]["policy"] = "accept"
                self.assert_digest_rejected(changed)

    def test_rule_order_and_expression_order_are_bound(self) -> None:
        changed = copy.deepcopy(self.baseline)
        expressions = changed["nftables"][3]["rule"]["expr"]
        expressions[1], expressions[2] = expressions[2], expressions[1]
        self.assert_digest_rejected(changed)


class MalformedInputTests(unittest.TestCase):
    def assert_rejected(self, payload: object, message: str) -> None:
        with self.assertRaisesRegex(nft.NftablesAttestationError, message):
            nft.inspect_ruleset(encode(payload))

    def test_duplicate_json_key_is_rejected(self) -> None:
        value = (
            b'{"nftables":[{"metainfo":{}},{"table":{"family":"ip",'
            b'"family":"ip6","name":"filter"}}]}'
        )
        with self.assertRaisesRegex(nft.NftablesAttestationError, "duplicate JSON object key"):
            nft.inspect_ruleset(value)

    def test_bad_root_unknown_object_and_multi_key_entry_are_rejected(self) -> None:
        cases = (
            ({"nftables": [], "extra": True}, "root must contain exactly"),
            ({"nftables": [{"metainfo": {}}, {"set": {}}]}, "unexpected object form"),
            (
                {"nftables": [{"metainfo": {}}, {"table": {}, "chain": {}}]},
                "single-key nftables object",
            ),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                self.assert_rejected(payload, message)

    def test_missing_or_misplaced_metainfo_is_rejected(self) -> None:
        payload = valid_payload()
        payload["nftables"].pop(0)
        self.assert_rejected(payload, "metainfo must appear exactly once")
        payload = valid_payload()
        payload["nftables"][0], payload["nftables"][1] = (
            payload["nftables"][1],
            payload["nftables"][0],
        )
        self.assert_rejected(payload, "metainfo must appear exactly once")

    def test_no_rules_is_rejected(self) -> None:
        payload = valid_payload()
        payload["nftables"] = [
            entry for entry in payload["nftables"] if "rule" not in entry
        ]
        self.assert_rejected(payload, "contains no rules|must contain rules")

    def test_rule_referencing_missing_chain_is_rejected(self) -> None:
        payload = valid_payload()
        payload["nftables"][3]["rule"]["chain"] = "MISSING"
        self.assert_rejected(payload, "undeclared nftables chain")

    def test_unexpected_fields_and_malformed_counter_are_rejected(self) -> None:
        payload = valid_payload()
        payload["nftables"][3]["rule"]["verdict"] = "accept"
        self.assert_rejected(payload, "unexpected fields")
        payload = valid_payload()
        payload["nftables"][3]["rule"]["expr"][0]["counter"] = {
            "packets": -1,
            "bytes": 0,
        }
        self.assert_rejected(payload, "non-negative integer")
        payload = valid_payload()
        payload["nftables"][3]["rule"]["expr"][0]["counter"] = {"packets": 1}
        self.assert_rejected(payload, "both packets and bytes")

    def test_invalid_expected_digest_is_rejected(self) -> None:
        with self.assertRaisesRegex(nft.NftablesAttestationError, "64 lowercase hex"):
            nft.attest_ruleset(encode(valid_payload()), "A" * 64)

    def test_oversized_stdin_is_rejected_without_parsing(self) -> None:
        with self.assertRaisesRegex(nft.NftablesAttestationError, "safe size limit"):
            nft.read_bounded_input(io.BytesIO(b"x" * 33), maximum_bytes=32)

    def test_invalid_utf8_nonfinite_float_and_deep_json_are_rejected(self) -> None:
        with self.assertRaisesRegex(nft.NftablesAttestationError, "valid UTF-8"):
            nft.inspect_ruleset(b"\xff")
        with self.assertRaisesRegex(nft.NftablesAttestationError, "non-finite"):
            nft.inspect_ruleset(b'{"nftables":NaN}')
        payload = valid_payload()
        nested: object = None
        for _ in range(nft.MAXIMUM_JSON_DEPTH + 2):
            nested = [nested]
        payload["nftables"][0]["metainfo"]["nested"] = nested
        self.assert_rejected(payload, "nesting depth")


if __name__ == "__main__":
    unittest.main()
