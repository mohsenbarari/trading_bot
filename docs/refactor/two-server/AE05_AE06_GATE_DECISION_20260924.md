# AE05/AE06 conditional gate decision (2026-09-24)

Decision: **retain serial preparation and one Gemini Worker**. Do not implement speculative overlap or parallel Worker lanes now. This is the plan's evidence gate, not a claim that overlap could never help or that AE00–AE06 is complete.

## Evidence available

A read-only sample of 74 `autopilot/runs/*/metrics.json` files modified since 2026-09-20 contained 61 `BLOCKED` and 13 `CONTINUE` results. The recorded `controller_phase_seconds.task_context` had median 0.002 s, p90 0.007 s and maximum 0.011 s across all 74. Among the 13 `CONTINUE` runs, median `task_context` was 0.006 s (max 0.011), median Coordinator time 546.364 s, median reported Provider time 140.538 s, and median `outside_provider_unattributed` time 406.566 s.

These are **not matched cohorts** and `task_context` is not an end-to-end measurement of every deterministic preparation action. `outside_provider_unattributed` includes other Coordinator activity; it must not be relabeled as preparation. The data thus does not establish a preparation bottleneck or an overlap speedup. No token saving is inferred.

## Gate consequence

- AE05: no draft queue, speculative snapshot or overlap hook is activated. Continue single-task serial preparation. Revisit only after instrumentation separately measures build/preflight/context latency and a comparable accepted-work cohort shows that latency is material relative to Provider waiting and discarded-draft cost.
- AE06: keep one Worker and one Git/Writer owner. AE03 no-replay recovery and AE04 current inventory-reader validity are open; immutable-base, conflict and global-reservation contracts for two lanes are not proven. Do not weaken HEAD checks or locks to force parallelism.
- Re-evaluate with at least 10 comparable accepted units, including failed attempts, discarded drafts, Codex uncached/cached/output tokens, Gemini usage and active/calendar time. A pilot must preserve all review, tests, semantic identity and STOP gates. If measurement remains insufficient, the serial decision stands.

This decision can close the **current activation question** for AE05/AE06 without shipping complexity lacking evidence. It does not close AE03, AE04 or the empirical acceptance of the whole optimization plan.
