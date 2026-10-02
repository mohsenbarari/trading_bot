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
- **Unique Canonical Marker**: `goal` in `task.json` must contain exactly one `\nCANONICAL_SOURCE_EXCERPTS_JSON:\n` marker. Inline, unanchored, or duplicate markers cause fail-closed rejection.
- **Link Safety**: Symlinks and hardlinks in `status.json` or `task.json` are rejected by link-safe snapshot readers.
- **Resource Bounds**: Directory scanning is capped at 8,192 entries; matching attempts are capped at 32; task snapshots are capped at 600,000 bytes (`3 * MAX_CONTEXT`); goal text is capped at 200,000 bytes (`MAX_CONTEXT`).
- **Token & Usage Fidelity**: A complete BLOCKED controller attempt retains its exact observed usage (e.g. 14 tokens) and its valid `work_lineage`, while setting `first_pass: null`. Genuine partial attempts discard `work_lineage` and keep full usage null with an honest observed subtotal.
- **Privacy**: Only the 64-hex SHA-256 fingerprint and schema version are emitted; no raw symbols, source bodies, prompt tokens, findings, or error traces leak into telemetry.
- **Legacy Compatibility**: `unavailable()` retains its exact 8-field shape without `work_lineage`. `_extract_semantic_identity` preserves its exact 5-field projection wrapper signature.

## 4. Source A -> Source B Invariance Scenario
When product code evolves from revision A to revision B (modifying files and updating `source_manifest` SHA values):
- Semantic identity `source_sha256` and `fingerprint` change between revisions.
- Because `gap_reference`, `stage_id`, `scenario_key`, and `symbol_groups` remain identical, the computed `work_lineage.fingerprint` remains exactly identical across revisions A and B.

## 5. Verification & Test Selectors
Implemented in `tests/test_two_site_work_identity_producer.py` (strictly byte-gated: target <= 18,000 UTF-8 bytes, hard cap <= 24,000 bytes):
1. `test_new_candidate_file_respects_utf8_byte_cap`: Dynamically measures actual `__file__` byte count and asserts <= 24,000.
2. `test_actual_work_hash_changes_only_logical_dimensions`: Asserts changes to stage, gap, scenario, symbols, or path modify the work hash against frozen expected SHA values.
3. `test_actual_collector_source_revision_and_task_run_rename_invariance`: Proves identical work hash across source revisions, commit heads, task IDs, and run renames.
4. `test_actual_collector_canonical_order_and_empty_symbols`: Verifies canonical sorting of paths/symbols, acceptance of empty groups, and rejection of unsorted markers.
5. `test_actual_legacy_and_controller_exact_work_projection`: Proves exact schema/hash output and zero sentinel token leakage on both legacy and controller routes.
6. `test_actual_collector_task_head_gap_marker_and_identity_rejection`: Proves fail-closed rejection for corrupted task IDs, HEADs, gap refs, duplicate/loose markers, tampered manifests, and worker/reviewer identity mismatches.
7. `test_actual_collector_link_and_byte_bound_rejection`: Proves rejection of symlinks, hardlinks, and limit+1 oversized snapshots with baseline restoration.
8. `test_actual_controller_failed_attempt_work_cost_and_partial_rejection`: Validates BLOCKED 14-token retention without first pass, and partial cost discarding.
9. `test_legacy_projection_and_unavailable_exact_backward_compatibility`: Validates exact 5-field wrapper projection, exact unavailable shape, and rejection of projected-only identities.
