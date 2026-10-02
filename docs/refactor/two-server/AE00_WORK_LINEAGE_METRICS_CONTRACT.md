# AE-00 Work Lineage Metrics Contract (Producer & Cycle Observer Prerequisite)

## 1. Scope & Non-Authority Disclaimer
This contract defines the Producer implementation of `work_lineage` in `scripts/two_site_provider_metrics.py` and the cycle observer projection prerequisite in `scripts/two_site_cycle_metrics.py`.
- **Observational Only**: Work identity produced and observed by these modules is purely observational telemetry over provider attempts and cycle runs.
- **No Authority**: It does NOT constitute proof of accepted product work, product behavior, replay authority, startup authority, or billing/token savings.
- **Unimplemented Dimensions**: Full pure Consumer grouping (`summarize_work_lineage_ledger`), downstream grouping / UNKNOWN-not-zero denominator, semantic-attempt vs logical work cardinality, conflict reports, partial cost allocation, value reports, savings calculations, comparable accepted-behavior cohorts, and subsequent stages AE00-AE06 remain completely OPEN, REQUIRED, and UNIMPLEMENTED. No source grouping helper exists.
- **No Backfill or Activation**: No historical telemetry is backfilled, no replay authority is conferred, and no live consumer activation is performed.

## 2. Work Lineage Schema & Canonical Hashing Domain
When all matched attempts agree on a validated, full canonical semantic identity, `provider_metrics()` emits an optional observer field:
```json
{
  "work_lineage": {
    "schema_version": 1,
    "fingerprint": "<64-character lowercase hex SHA-256>"
  }
}
```

### Canonical Input Domain
The `fingerprint` is computed over the UTF-8 bytes of an exact compact JSON object with sorted keys, no whitespace or newlines (`separators=(",", ":")`, `ensure_ascii=False`, `allow_nan=False`):
```json
{
  "gap_reference": "<validated string>",
  "kind": "two-site-logical-work",
  "scenario_key": "<validated string>",
  "schema_version": 1,
  "stage_id": "<validated string>",
  "symbol_groups": [
    {
      "path": "<canonical sorted repo-relative path>",
      "symbols": ["<canonical sorted ascii identifier>", ...]
    }
  ]
}
```

### Excluded Volatile Dimensions
The following dimensions are deliberately excluded from `work_lineage`:
- `source_manifest` and `source_sha256`
- Attempt `fingerprint`
- `task_id` and `controller_run_id`
- Git commit hashes (`HEAD` / `source_head`)
- Model identifiers and assignment keys

Empty canonical symbol groups (`"symbol_groups": []`) are legal and hash deterministically.

### Cycle Observer Projection & Validation Gate
In `scripts/two_site_cycle_metrics.py`, the pure validator `work_lineage(value)` enforces an exact 2-key schema:
- **Exact 2-Key Schema**: Matches exactly `{"schema_version", "fingerprint"}`; any extra or missing keys trigger fail-closed `None`.
- **Integer Version**: `schema_version` must be integer `1` (`type(schema_version) is int and not isinstance(schema_version, bool) and schema_version == 1`). Floating point and boolean types are rejected.
- **Lowercase Hex Fingerprint**: `fingerprint` must be a 64-character lowercase hex string (`[a-f0-9]{64}`).
- **Independent Copy**: Returns a fresh dictionary `{"schema_version": schema_version, "fingerprint": fingerprint}` so downstream mutations cannot mutate telemetry or source fixtures.

In `scripts/two_site_cycle_metrics.py::record_cycle`, observer work projection is gated as follows:
- **Complete Provider States Gate**: Only provider states in `_COMPLETE_PROVIDER_STATES = frozenset({"OBSERVED_CONTROLLER_ATTEMPTS", "OBSERVED_MATCHING_ATTEMPTS"})` are eligible for `work_lineage` projection.
- **Semantic Identity Requirement**: A valid 5-key `semantic_identity` projection (`stage_id`, `scenario_key`, `gap_reference`, `source_sha256`, `fingerprint`) must be present.
- **Legacy Compatibility**: Valid legacy provider states `"OK"` and `"PARTIAL"` preserve semantic identity projection without new `work_lineage` projection.
- **Stale Fallback Clearing**: When fallback is invoked (e.g. initial provider metrics unavailable), any fallback failure, source drift (`source_head` mismatch), or provider exception (`OSError`) explicitly clears stale `semantic_identity` and `work_lineage` keys from the cycle record via `pop()`.
- **Unchanged Public API**: All existing function signatures and behavior in `scripts/two_site_cycle_metrics.py` (`record_cycle`, `summarize_efficiency_ledger`, `command_group`, etc.) remain identical and backwards compatible. No source grouping helper exists.

## 3. Marker, Readers, Link Safety, and Caps
- **Unique Canonical Marker**: `goal` in `task.json` must contain exactly one `\nCANONICAL_SOURCE_EXCERPTS_JSON:\n` marker. Harmless escaped marker substrings within valid source excerpt JSON strings are accepted; inline, unanchored, or duplicate canonical delimiter occurrences cause fail-closed rejection. Legacy single-inline metadata reading remains unchanged and permissive.
- **Link Safety**: Symlinks and hardlinks in `status.json` or `task.json` are rejected by link-safe snapshot readers. Furthermore, link-safety verification asserts direct `bounded_file_text` read refusal (`ProposalError`) for BOTH actual per-run `metrics.json` and ledger `metrics.json` symlinks and hardlinks, preserving external target file content, inode identities, and link counts.
- **Resource Bounds**: Directory scanning is capped at 8,192 entries; matching attempts are capped at 32; task snapshots are capped at 480,000 bytes (`3 * MAX_CONTEXT`); goal text is capped at 160,000 bytes (`MAX_CONTEXT`).
- **Token & Usage Fidelity**: A complete BLOCKED controller attempt retains its exact observed usage (e.g. 14 tokens) and its valid `work_lineage`, while setting `first_pass: null`. Genuine partial attempts discard `work_lineage` and keep full usage null with an honest observed subtotal.
- **Privacy**: Only the 64-hex SHA-256 fingerprint and schema version are emitted; no raw symbols, source bodies, prompt tokens, findings, or error traces leak into telemetry. Sentinel tokens in status prompts or source excerpts do not leak, and task/status files remain immutable before and after reading.
- **Legacy Compatibility**: `unavailable()` retains its exact 8-field shape without `work_lineage`. `_extract_semantic_identity` preserves its exact 5-field projection wrapper signature.

## 4. Source A -> Source B Invariance Scenario
When product code evolves from revision A to revision B (modifying files and updating `source_manifest` SHA values):
- Semantic identity `source_sha256` and `fingerprint` change between revisions.
- Because `gap_reference`, `stage_id`, `scenario_key`, and `symbol_groups` remain identical, the computed `work_lineage.fingerprint` remains exactly identical across revisions A and B.

## 5. Verification & Test Selectors

### Producer & Marker-Ingress Verification
Implemented in `tests/test_two_site_work_lineage_marker_ingress.py` (advisory drafting target <= 18,000 UTF-8 bytes; hard cap <= 24,000 bytes enforced by test):
1. `test_actual_collector_accepts_embedded_marker_with_one_canonical_delimiter`: Verifies both actual legacy prefix and actual controller routes accept valid embedded markers with harmless escaped occurrences, producing identical 28-token totals, first_pass, exact work lineage, and 5-key semantic projections; controller BLOCKED retains 14 tokens without first pass.
2. `test_actual_collector_preserves_rejected_delimiters_and_bindings`: Verifies fail-closed rejection for loose/inline delimiters, duplicates, malformed envelopes, incomplete identities, and manifest tampering, while preserving legacy permissive single-inline metadata with independently frozen task SHA.
3. `test_guard_mutation_same_collector_witness_and_source_free_usage`: Executes the same independent `_assert_complete` witness oracle before mutation, asserts it raises `AssertionError` under mutation using `pytest.raises`, and verifies restoration. Verifies absence of sentinel tokens from telemetry and immutability of task/status files. Tests exact 480,000 byte snapshot limit acceptance and 480,001 byte limit+1 rejection.
4. `test_candidate_utf8_cap_and_legacy_schema`: Verifies candidate test file byte bounds (hard cap `0 < len(raw_bytes) <= 24000` enforced; 18,000 is an advisory drafting target and not an enforced gate in the test) and exact 8-field legacy unavailable schema.

### Cycle Observer Prerequisite Verification
Implemented in `tests/test_two_site_work_lineage_consumer.py` (hard cap <= 24,000 UTF-8 bytes; actual file is 19,628 bytes with no 18,000 enforced claim):
1. `test_actual_cycle_work_projection_and_fallback`: Verifies frozen work payload SHA-256 (`41de8c6f7d2f61cc10a9a71beb9ef4d33e06b52e6a3dfdffd02266d2a32c10e2`), runs 4 actual unmocked `record_cycle` scenarios (controller A unmocked 28 tokens first_pass True; controller B revision B invariance with identical work SHA 28 tokens; blocked controller 14 tokens first_pass None; legacy un-prefixed controller 28 tokens), verifies input task/status immutability (`meta == meta_snap`, byte snapshot equality), verifies returned record `work_lineage` and `semantic_identity` are independent copies (`is not`), validates provider metrics usages, subtotals, attempt counts, first_pass, and Codex turn stream accounting (100 in, 80 cached, 20 uncached, 10 out, 2 reasoning). Separately bounded-reads actual per-run `metrics.json` and ledger `metrics.json`, verifying absence of sentinel tokens, valid JSON/list structure, and matching work lineage and semantic identity on the last ledger entry.
2. `test_cycle_projection_validation_legacy_and_link_guards`: Tests `metrics.work_lineage` pure validator rejecting invalid schemas, bad types, non-1 int, bool, non-hex, wrong lengths, extra keys, and ensuring independent copies. Tests `record_cycle` projection gates: legacy "OK" and "PARTIAL" preserve semantic identity without work lineage; complete state without semantic identity suppresses work lineage; fallback source drift and provider exception clear stale projections. Tests `bounded_file_text` read refusal (`ProposalError`) for symlinks and hardlinks for both per-run `metrics.json` and ledger `metrics.json`, preserving external target content, inode, and link counts. Asserts unchanged AST of `summarize_efficiency_ledger` matching literal AST hash `6a1ec137f80e58182be2a136fb17bfc58235931d033a48d4bf4fc61636ccf977`.
3. `test_cycle_projection_full_value_witness_mutation_and_bytes`: Executes `_assert_cycle_witness` helper checking top-level `work_lineage` and `semantic_identity` in the returned record and last ledger entry (the helper does NOT read per-run metrics itself or check nested usage; per-run and nested usage are separately verified in the positive test). Mutates `metrics.work_lineage` to return None, verifies `_assert_cycle_witness` raises `AssertionError`, and verifies restoration. Verifies candidate test file hard cap `<= 24000` UTF-8 bytes (actual file size 19,628 bytes), with no 18,000 enforced claim.

## 6. Verification Evidence, Administrative Diagnostics & Open Consumer Roadmap

### Verification Evidence & Diagnostic Distinction
- **Candidate Project Tests**: 96 candidate tests executed across all 7 focused test selectors with fresh Gemini VERIFIED; 179 host tests passed across 8 related test suites.
- **Supplementary Codex Diagnostic**: Codex executed an additional ignored final-review diagnostic on the exact bound pre-apply proposal. This is administrative review evidence, NOT a tracked product test. It exercised 4 actual Producer fixtures using the SAME independent full literal oracle that bounded-read BOTH persisted per-run and ledger `metrics.json`, checked top + nested `work_lineage` and `semantic_identity`, full usage / attempt counts / `first_pass`, cache and reasoning token arithmetic, and asserted ALL inputs immutable before injecting a missing-work implementation mutation -> verified `AssertionError` -> verified restore.
- **Prerequisite Scope Only**: This combined evidence accepted ONLY a limited observer prerequisite (cycle record projection and validation in `record_cycle`), NOT whole Consumer or accepted product behavior. All requirements are not proved by a single narrower test.

### Open Consumer Roadmap & Non-Authority
All full pure Consumer capabilities remain completely OPEN, REQUIRED, and UNIMPLEMENTED:
- Pure Consumer grouping and ledger summarization (`summarize_work_lineage_ledger`)
- UNKNOWN-not-zero denominator
- Conflicts, partial cost allocation, and value reports
- Billing and token savings calculations
- Comparable accepted-behavior cohorts
- Subsequent work packages AE00-AE06
No historical telemetry backfill, no replay authority, no startup authority, no live activation, and no acceptance authority exist. Producer-only goal closure is prohibited; whole Consumer completion is not achieved.
