# AE03 Recovery Status and Gates (2026-09-24)

- Scope & Context: [AE Plan](AUTOMATION_EFFICIENCY_PLAN.md), [AE01 Evaluation](AE01_EVALUATION_20260923.md), [AE02 Evaluation](AE02_EVALUATION_20260923.md), and [Automation Efficiency Memory](../../memory/areas/automation-efficiency.md).
- Current Baseline: Clean HEAD `6cc3b475e3667df7ae6aa2773fd699c1f7449862` on branch `refactor/two-site-architecture`.
- Governing Rule: No modifications to `main`, production, DNS, or Writer. No active Supervisor recovery or runtime hooks are enabled.

## Update at `6cc3b475` (2026-09-24)

`dc058d7b` propagated valid quota evidence into Pipeline's blocked output. `36d1a0ab` added a pure Broker detector; its first proposal lacked tests and was corrected after Gemini Reviewer rejection. `56bece05` bound the observation to the exact Runner role, attempt ID and SHA of the same bounded status bytes. A pre-review test caught that correction-Worker quota retains both prior Worker and Reviewer IDs in Pipeline state; the corrected test proves the current role comes from the quota attempt, not those stale fields. `6cc3b475` requires Broker to re-read that status through the guarded reader, match its digest and role/model/task/head/evidence, and reject missing, changed or linked status and any result artifact for the quota invocation. Independent selected suites passed 152, 175 and 173 tests, respectively; Worker transport retry occurred in the latter two units and remains real cost.

This is **observational validation only**. Broker still does not journal a terminal quota response, recover an acknowledged response, wait or resume the existing semantic reservation; Supervisor remains STOPPED. The existing detector accepts an unnecessary `verifier-N` run-ID spelling as well as the actual `reviewer-N` spelling; close that conservative-parser gap before runtime custody. Monitor's old ref pins are ancestors of current `main`/`origin/main` but do not match them. No live quota stream or comparable efficiency cohort has been observed, so effect remains **UNKNOWN**. The monolithic quota-journal task 091 was rejected after a hanging `serve(once=True)` test and a truncated correction, with no code applied.

## Update at `1ee73dd0` (2026-09-24)

After the cross-epoch gate, `d8462a89` added a conservative classifier for bounded, model/session-bound, zero-usage Gemini quota error streams. `8b4f1c32` records its **observational** result in Runner status; `e16eb442` tests both CLI exit paths and negative cases through the real Runner. This is not `WAITING_QUOTA`, not a retry permit, and not evidence of an actual Antigravity quota stream. Only an explicit timezone-qualified reset time may be known; an ambiguous time remains `UNKNOWN`.

`1ee73dd0` prevents Monitor restart of an absent Supervisor when a durable rescope intent exists or a bounded, no-follow scan is unsafe. Broker-only recovery with a live Supervisor remains eligible. The first Gemini proposal was rejected for unbounded `ls` output/newline splitting; the second failed independent root-run tests because root can scan mode-000 directories. The accepted correction checks owner/permission bits explicitly; exact bindings and bytes passed, and **209 selected independent tests passed**. Both corrections are real cost, not a measured efficiency gain.

Supervisor remains **STOPPED**, not a live recovery pilot. Monitor's pinned `main`/`origin/main` SHAs are stale, so `protected_refs_ok` is false; audit ancestry before any operational resume. A manually launched Broker uses a relative script path invisible to Monitor's exact process matcher, unlike the canonical tmux launcher. Typed quota propagation, durable same-task wait/wakeup without replay, STOP/restart/fault matrix and 1/3/10/20 comparable live evidence remain open. Efficiency effect remains `UNKNOWN`.

## Update at `c77320ab` (2026-09-24)

The six-helper table below is a historical snapshot, not the current implementation boundary. Subsequent accepted units added sequential receipts, post-write validation, runtime RESCOPE wiring, a bounded cross-epoch startup gate, and a hermetic startup matrix. `0ec45a51` requires a canonical accepted checkpoint matching the full current repository identity before a prior epoch can be crossed; `c77320ab` covers missing/corrupt/stale checkpoints, full-identity drift, unsafe epoch entries, overflow and STOP priority. The selected startup/runtime/autopilot suites passed **60/60** after Codex's byte-binding and independent test gate.

Supervisor activation and quota/reset handling remain **not accepted**. The cross-epoch guard is deliberately conservative: a manually changed HEAD without a matching accepted receipt blocks instead of manufacturing acceptance. The effect on token use or delivery speed is still `UNKNOWN`; this document is not a live-success claim. The next work is typed quota evidence and restart-safe waiting, monitor coordination, the remaining fault matrix, then measured pilot cohorts before AE03 can close.

## 1. Accepted Technical Units vs. Runtime Recovery

The following six isolated helper units have been accepted and merged up to HEAD `94c01ab2`:

| Component | Commit | Test Coverage | Technical Status |
| --- | --- | --- | --- |
| Pure canonical codec | `992b2f5e` | Unit tests | Accepted isolated unit |
| Safe state reader | `e966cf132b06942f8420403e23a45365d56120a5` | Unit tests | Accepted isolated unit |
| Private temporary writer | `34317190a5946c8152d3b0831438b387e384a63f` | 118 independent selected tests | Accepted isolated unit |
| Pinned private directory opener | `a7986e6a259257443a1ad935905ada8c5034e292` | 123 independent tests | Accepted isolated unit |
| Fail-closed link-collision classifier | `429c2c4ef6ac80a5a52e02a21a92d77612a45d3b` | 129 independent tests | Accepted isolated unit |
| First no-overwrite attempt-1 publisher | `94c01ab2159274acb372b4663573dd41cbcc69fd` | 136 independent tests | Accepted isolated unit |

**Critical Operational Boundary**: These six units represent technically accepted code units only. They do **not** constitute an active Supervisor recovery loop. Runtime recovery remains completely inactive.

## 2. Observed Live Results and Pilot Impact

- **Live P1 Smoke Attempt**: Following the temp writer unit, a real P1 cycle (`cycle-0001-20260924T052155Z-4018814`) halted at `STOPPED cycle2`. The pre-review test gate failed, and the subsequent Gemini correction ended in `MODEL_OUTPUT_TRUNCATED` prior to Reviewer invocation. No proposal bytes were applied or committed, and HEAD remained clean.
- **Pilot Impact**: Pilot efficiency and token impact are **UNKNOWN**. The latest attempt-1 publisher (`94c01ab2`) has not undergone an end-to-end operational cycle.
- **Preceding Stages**: AE01 is technically accepted but has UNKNOWN efficiency effect; AE02 checkpoint/restart passed technically but also has UNKNOWN efficiency effect.

## 3. Rejected and Unapplied Proposals History

- **Proposals 009, 010, 011, 016, 017, 018**: Rejected during Codex final semantic review (zero bytes applied) due to issues including link ambiguity, ignored directory cleanup, missing exact-envelope verification, and improper interrupt handling.
- **Proposals 013 and 014**: Failed preflight / review due to oversized payload or missing test suites (zero bytes applied).
- **Decomposition Rationale**: Decomposing the publisher into modular helper units avoided repeating unverified monolith code; this architectural partition does not represent a quantified token or runtime savings.

## 4. Open Safety Gates and Roadmap Status

### AE03 Open Gates
1. **Sequential Advancement**: Durable `attempt-1 -> attempt-2 -> attempt-3` state transitions.
2. **Durable Counters & Recovery**: Restart-safe counter persistence across cycle boundaries.
3. **Verified Clearing**: State clearing permitted strictly after verified successful advancement.
4. **Quota Policy**: Deterministic handling of `WAITING_QUOTA` and unambiguous timezone/reset parsing.
5. **Supervisor & Monitor Integration**: Safe coordination between autopilot monitor and Supervisor.
6. **Fault Matrix**: Validation against fake-clock and simulated transport/filesystem faults.
7. **Empirical Evidence Gates**: Observable metrics across real 1-cycle, 3-cycle, 10-cycle, and 20-unit comparable cohorts.

### Roadmap Dependencies (AE04–AE06)
- **AE04**: Evidence indexing deferred until triggered by inventory growth or transport limits.
- **AE05**: Speculative preparation overlap deferred until measurement identifies preparation as a bottleneck.
- **AE06**: Decision point requiring explicit review; does not imply automatic multi-worker activation.

## 5. Next Bounded Step

1. **Design & Test Attempt 2/3 Advancement**: Focus exclusively on offline tests for sequential attempt 2 and 3 recovery advancement before introducing any runtime hooks.
2. **Preserve Isolation**: Maintain P1 task assignment restorability with zero mutations to `main`, production, DNS, or Writer components.
