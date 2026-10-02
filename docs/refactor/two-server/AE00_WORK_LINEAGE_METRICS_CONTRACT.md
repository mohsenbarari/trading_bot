# AE-00 Work Lineage Metrics Contract (Producer Only)

## 1. Scope & Non-Authority Disclaimer
This contract defines the Producer implementation of `work_lineage` in `scripts/two_site_provider_metrics.py`.
- **Observational Only**: Work identity produced by this module is purely observational telemetry over provider attempts.
- **No Authority**: It does NOT constitute proof of accepted product work, product behavior, replay authority, startup authority, or billing/token savings.
- **Unimplemented Dimensions**: Cycle record propagation, downstream grouping/UNKNOWN-not-zero denominator, semantic-attempt vs logical work cardinality, failed/shared/maintenance/interactive cost allocation, and comparable accepted-behavior cohorts remain completely UNIMPLEMENTED by this package.
- **No Backfill or Activation**: No historical telemetry is backfilled, and no live consumer activation is performed.

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

## 3. Marker, Readers, Link Safety, and Caps
- **Unique Canonical Marker**: `goal` in `task.json` must contain exactly one `\nCANONICAL_SOURCE_EXCERPTS_JSON:\n` marker. Harmless escaped marker substrings within valid source excerpt JSON strings are accepted; inline, unanchored, or duplicate canonical delimiter occurrences cause fail-closed rejection. Legacy single-inline metadata reading remains unchanged and permissive.
- **Link Safety**: Symlinks and hardlinks in `status.json` or `task.json` are rejected by link-safe snapshot readers.
- **Resource Bounds**: Directory scanning is capped at 8,192 entries; matching attempts are capped at 32; task snapshots are capped at 480,000 bytes (`3 * MAX_CONTEXT`); goal text is capped at 160,000 bytes (`MAX_CONTEXT`).
- **Token & Usage Fidelity**: A complete BLOCKED controller attempt retains its exact observed usage (e.g. 14 tokens) and its valid `work_lineage`, while setting `first_pass: null`. Genuine partial attempts discard `work_lineage` and keep full usage null with an honest observed subtotal.
- **Privacy**: Only the 64-hex SHA-256 fingerprint and schema version are emitted; no raw symbols, source bodies, prompt tokens, findings, or error traces leak into telemetry. Sentinel tokens in status prompts or source excerpts do not leak, and task/status files remain immutable before and after reading.
- **Legacy Compatibility**: `unavailable()` retains its exact 8-field shape without `work_lineage`. `_extract_semantic_identity` preserves its exact 5-field projection wrapper signature.

## 4. Source A -> Source B Invariance Scenario
When product code evolves from revision A to revision B (modifying files and updating `source_manifest` SHA values):
- Semantic identity `source_sha256` and `fingerprint` change between revisions.
- Because `gap_reference`, `stage_id`, `scenario_key`, and `symbol_groups` remain identical, the computed `work_lineage.fingerprint` remains exactly identical across revisions A and B.

## 5. Verification & Test Selectors
Implemented in `tests/test_two_site_work_lineage_marker_ingress.py` (strictly byte-gated: drafting target <= 18,000 UTF-8 bytes, hard cap <= 24,000 bytes):
1. `test_actual_collector_accepts_embedded_marker_with_one_canonical_delimiter`: Verifies both actual legacy prefix and actual controller routes accept valid embedded markers with harmless escaped occurrences, producing identical 28-token totals, first_pass, exact work lineage, and 5-key semantic projections; controller BLOCKED retains 14 tokens without first pass.
2. `test_actual_collector_preserves_rejected_delimiters_and_bindings`: Verifies fail-closed rejection for loose/inline delimiters, duplicates, malformed envelopes, incomplete identities, and manifest tampering, while preserving legacy permissive single-inline metadata with independently frozen task SHA.
3. `test_guard_mutation_same_collector_witness_and_source_free_usage`: Executes the same independent `_assert_complete` witness oracle before mutation, asserts it raises `AssertionError` under mutation using `pytest.raises`, and verifies restoration. Verifies absence of sentinel tokens from telemetry and immutability of task/status files. Tests exact 480,000 byte snapshot limit acceptance and 480,001 byte limit+1 rejection.
4. `test_candidate_utf8_cap_and_legacy_schema`: Verifies candidate test file byte bounds (drafting target <= 18,000 bytes, hard cap <= 24,000 bytes) and exact 8-field legacy unavailable schema.
