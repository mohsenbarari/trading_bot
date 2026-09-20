# Automation Efficiency

- 2026-09-20 | AE01-00 documentation/scope is under final acceptance; exact evidence—not HEAD alone—resolved the capacity-interrupted P1 receipt. AE01-10 is next after this documentation commit; no AE01 code package is implemented. Full plan: `docs/refactor/two-server/AE01_IMPLEMENTATION_PLAN.md`. F1: AE01-40 selects new/legacy preparation route strictly from validated startup configuration fixed for the whole cycle with no runtime toggle or environment switch; rollback occurs only at a safe cycle boundary after any ambiguous active result is resolved. F2: UNKNOWN/null telemetry stays unknown and blocks token/time efficiency or pilot-improvement claims, but does not alone block separate technical/product acceptance when normal quality evidence passes; missing telemetry is never zero or success.
- 2026-09-19 | `AE-20260919` is PLANNED, NOT IMPLEMENTED. Owner requested a precise
  remembered plan for the next step, not runtime changes in this turn. Full plan:
  `docs/refactor/two-server/AUTOMATION_EFFICIENCY_PLAN.md`. Load its relevant package
  before implementation; it does not expand the current P1 assignment or supersede
  `PROPOSAL_AUTOMATION.md` by itself.
- Success means more accepted behavior per token/time, with quality preserved or
  improved. Count semantic behavior, not commits or claimed requirements; include
  failed attempts/corrections/reconciliation. Missing usage is unknown, not zero.
  Compare same model/effort, family, risk, batch size and warm/cold startup. Track
  Codex uncached/cached/output separately, Gemini where observed, active/elapsed
  time, first-pass rate, repeats, review escapes and data coverage.
- Evidence through `cycle-0001-20260919T081117Z-2174794`: 19 successful cycles spent
  about 15% of elapsed cycle time in Provider. Remaining time is not all waste.
  That cycle had four builds, three preflight failures and redundant discovery.
  Independent Codex review previously caught an assertion Gemini missed.
- Order: AE-00 fair baseline; AE-01 deterministic schema-aware task/context prep;
  AE-02 durable accepted checkpoint separate from live status; AE-03 typed quota/
  recovery waits; AE-04 bounded evidence continuation; AE-05 one-ahead read-only
  preparation; AE-06 conditional decision on two isolated Workers. Reuse existing
  helpers/batching/telemetry; no new model-driven Supervisor. Two Workers are not
  authorized merely by recording this plan and current HEAD-bound locks cannot
  simply be removed.
- Preserve fresh Gemini review, final Codex semantic review, hermetic tests,
  source/task/HEAD bindings, protected refs, semantic reservations and failure
  budgets. Resolve in-flight ambiguity before checkpoint reuse; never auto-expire
  reservations or invent acceptance from Git HEAD. Explicit STOP wins recovery.
- Next: refresh live state/disk, drain at a cycle boundary, establish scoped
  maintenance task, implement AE-00 then AE-01 with offline tests and independent
  review; small commits, restart normal development between packages. Pilot only
  necessary real work; do not rerun Providers to manufacture a baseline.
- Keep main/production untouched. Roll back specific reviewed changes at a safe
  boundary, preserving evidence. AE-05/06 require measured net benefit; do not
  introduce parallel complexity if coordination remains the bottleneck.
