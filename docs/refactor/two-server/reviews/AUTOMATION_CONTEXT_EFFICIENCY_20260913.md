# Independent Technical Audit: Automation Context Efficiency and Telemetry Maintenance

**Audit Date:** 2026-09-13
**Audit Identifier:** `maint-codex-exception-audit-20260913-07`
**Auditor:** Gemini Independent Reviewer (Worker Role, Clean Session)
**Target:** Owner-authorized ONE-OFF Codex-authored automation maintenance candidate

---

## 1. Scope, Authority, and Operational Limits

- **Maintenance Exception Scope:** This audit represents an independent technical evaluation of an owner-authorized one-off maintenance patch authored by Codex to optimize automation context efficiency and provider telemetry. It is not normal unattended project automation.
- **Workflow Integrity:** Normal Gemini-author / independent Gemini review / Codex Git flow remains completely unchanged following this maintenance exception. The reviewer does not claim authorship of the underlying maintenance code.
- **No Production Readiness Claim:** This review constitutes a static verification of candidate contracts, assertions, and bounds. No claim of production readiness or general operational release is made.
- **No Measured Savings Claim:** Telemetry mechanisms are audited for correctness, non-leakage, and bounds only; no assertion of realized compute, token, or financial savings is asserted or implied.

---

## 2. Authoritative Source SHA-256 Manifest

All audited candidate sources were evaluated against their exact controller-supplied cryptographic digests:

| File Path | SHA-256 Digest |
| :--- | :--- |
| `scripts/two_site_task_context.py` | `a3b3db814046728ba8f58d7d45cde36f81de2a08c1a057e2e51edc5f3144bf98` |
| `tests/test_two_site_task_context.py` | `bf130c9e76de8ded2fa5c2d36d855dc5349a99ba6e942ab1c54417b9a5f62112` |
| `scripts/two_site_provider_metrics.py` | `1845f787f043a48b35fb20812e27413ba5a0b3b420e144c043e4b3dca9c5220d` |
| `tests/test_two_site_provider_metrics.py` | `e7a221f984cfcb5670ec4e77a429c8e01d62296edd8ebebfcc6f778098a9304d` |
| `scripts/two_site_cycle_metrics.py` | `a0a7bf5dcdee9b754622f1a0297b69d1d4f54636f66a5bc3f7715d13c16fbd03` |
| `scripts/two_site_proposal_runner.py` | `14bec419d5838ccd198602da1b044c78096fec38528c57a1245d17761ae909eb` |

### Controller-Bound Proposal Runner Test SHA and Excerpts
- **Target Path:** `tests/test_two_site_proposal_runner.py`
- **Candidate SHA-256:** `ad5524ad5ffad91911f3d80139c8cf71334ef0f45c19f4bf0578d0beeb5eb8fb`

#### Excerpt 1 (Lines 88–92: Task Immutability & Advisory Nature)
```python
    def test_preparation_check_is_advisory_and_does_not_mutate_task(self):
        original = deepcopy(self.task)
        notes = runner.check(self.root, self.task_path)["preparation_notes"]
        self.assertEqual(notes["schema_version"], 1)
        self.assertEqual(json.loads(self.task_path.read_text()), original)
```

#### Excerpt 2 (Lines 94–107: Injection Ordering and Context Budget Caps)
```python
    def test_preparation_injected_before_provider_and_prompt_limits(self):
        with patch.object(runner, "git", return_value="head"), \
             patch.object(runner, "proposal_cli_context", side_effect=RuntimeError("sentinel")) as provider, \
             patch.object(runner, "task_preparation_notes", wraps=runner.task_preparation_notes) as notes:
            with self.assertRaisesRegex(RuntimeError, "sentinel"): runner.run(self.root, self.task_path, "worker", "notes-test", 10)
            self.assertEqual(notes.call_args.args, (self.task, self.sources))
            provider.assert_called_once()
        with patch.object(runner, "git", return_value="head"), \
             patch.object(runner, "proposal_cli_context") as provider, \
             patch.object(runner, "task_preparation_notes", return_value={"test": "x" * (2 * runner.MAX_CONTEXT)}):
            with self.assertRaisesRegex(runner.ProposalError, "smaller coherent unit"): runner.run(self.root, self.task_path, "worker", "notes-cap", 10)
            provider.assert_not_called()
```

#### Excerpt 3 (Prompt Key Set Contract & Non-Mutation Verification)
```python
        self.assertNotIn("proposal", prompt_json)
        self.assertEqual(
            set(prompt_json.keys()),
            {"task", "source_head", "sources", "feedback", "prior_identity",
             "correction_transport", "preparation_notes"},
        )
        self.assertEqual(prompt_json["preparation_notes"],
                         runner.task_preparation_notes(self.task, self.sources))
        self.assertNotIn("preparation_notes", task_data)
        self.assertNotIn('"canonical_proposal_sha256"', prompt_text)
```

---

## 3. Detailed Technical Audit Findings

### Requirement 1: Task Preparation Notes (`preparation_notes`)
- **Advisory & Deterministic:** `preparation_notes` in `scripts/two_site_task_context.py` acts purely as an advisory hint generator. It produces identical output across repeated runs on identical inputs.
- **Strict Output Boundary (<= 8192 bytes):** The payload size is monitored during assembly and subjected to final truncation loops (popping `literal_hints`, then `exact_blocker_ids`, then `warnings`) ensuring the serialized UTF-8 byte count never exceeds 8192 bytes.
- **Explicit Omission Indicators:** When limits are reached, deterministic warning tokens (`BLOCKER_IDS_OMITTED`, `SOURCE_BLOCK_OMITTED`, `MISSING_EXACT_BLOCKER_ID`) are emitted rather than failing silently.
- **Exact Source-Origin Unicode Handling:** Format characters (Unicode category `Cf`), including Persian Zero-Width Non-Joiner (`\u200c`), Zero-Width Joiner (`\u200d`), bidirectional markers (`\u200e`, `\u200f`), and Arabic letter marks (`\u061c`), are captured and serialized via `json.dumps(val, ensure_ascii=True)`, preserving exact byte semantics without ambiguous decode mutation.
- **Canonical Excerpt Support:** Excerpts supplied via `CANONICAL_SOURCE_EXCERPTS_JSON:\n` in the task goal or `excerpts` keys are parsed with start-line offsets correctly computed.
- **Immutability & Zero External Ingress:** `load_task` and `check` do not mutate input files or dictionary structures. Input sizes remain pre-bounded by `MAX_CONTEXT` (160,000 bytes) in `two_site_proposal_runner.py`; no unbounded external network or file discovery is performed.

### Requirement 2: Provider Telemetry and Cycle Ledger (`provider_metrics` & `record_cycle`)
- **Strict Attribution:** Metrics aggregation in `scripts/two_site_provider_metrics.py` requires exact matching across run ID prefix, task ID, and git source head (`head_before`). Mismatched runs are marked `PARTIAL` rather than attributing unverified attempts or emitting false zeros.
- **Bounded Discovery & Traversal:** Directory scanning via `os.scandir` is hard-capped at 8192 entries and 32 matching attempt status directories. Path canonicalization verifies targets remain strictly inside `.local/two-site-refactor/proposals` without link traversal.
- **Link-Safe Readers:** File reads use `bounded_file_text` and `bounded_file_bytes`, verifying regular file status (`st_mode`), unique hardlink count (`st_nlink == 1`), and non-symlink invariants.
- **Unique Failed Result Usage Recovery:** For attempts ending in `BLOCKED` where `status.json` lacks usage, recovery scans `stream.jsonl` for exactly one `result` event. Streams with multiple terminal results or malformed lines are rejected, setting usage to `None`.
- **Numeric Validation & Non-Leakage:** Durations and token counters are validated as finite, non-negative numbers `<= 10^12` via `_number()` and `_usage()`. Thinking tokens are contained within output tokens and not double-counted. Free-form text (prompts, model responses, commands) is never returned or leaked into metrics JSON.
- **Separation of Totals and Subtotals:** Incomplete attempts set overall `usage` token totals to `None`, preserving `observed_usage_subtotal` for observed partial sums.
- **Timing Coverage:** Provider timing is classified as `MATCHING_PROVIDER_ATTEMPTS_ONLY` or `UNKNOWN`. The residual difference (`outside_provider_unattributed`) represents unprofiled controller, test, and review execution, not waste.
- **Telemetry Non-Authority:** Telemetry structures have no acceptance gating authority. `record_cycle` in `scripts/two_site_cycle_metrics.py` maintains cycle state fidelity, respects 2000-record / 2 MB ledger limits, and defaults unbound counters (`declared_requirement_count`, `focused_selector_count`) to `None`.

### Requirement 3: Controller Test Verification
- **Controller-Reported Test Evidence:** The controller observed 166 focused unit tests pass across the designated test selectors:
  - `tests/test_two_site_task_context.py`
  - `tests/test_two_site_provider_metrics.py`
  - `tests/test_two_site_proposal_runner.py`
  - `tests/test_two_site_cycle_tools.py`
  - `tests/test_two_site_cycle_efficiency.py`
  - `tests/test_two_site_prompt_transport_contract.py`
  - `tests/test_two_site_cycle_packet_controls.py`
- **Evidence Classification:** Reported evidence reflects bounded controller test execution, not independent test execution by the auditor.

---

## 4. Conclusion

Static verification reveals no contract violations, security regressions, unbounded I/O, or data mutation issues in the candidate sources. The implementation adheres strictly to bounded advisory constraints, link safety, strict telemetry attribution, and robust error handling. The candidate is ready for independent review and Codex application.
