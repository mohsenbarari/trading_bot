# Two-Site Refactor Change Ledger

این ledger فقط تغییر وضعیت و پیوند evidence را ثبت می‌کند؛ شرح تصمیم‌ها در
[`MASTER_PLAN.md`](MASTER_PLAN.md) و ترتیب در [`execution-order.yaml`](execution-order.yaml)
است و این فایل source of truth موازی نیست.

| Date | Record | Status | Scope | Evidence / note |
| --- | --- | --- | --- | --- |
| 2026-09-04 | Antigravity execution adapter candidate | `STATIC_VALIDATED / RUNTIME_DISCOVERY_BLOCKED` | Repository orchestration only | CLI ran but discovered no project agents; cause unclassified; independent governance review and runtime smoke required; no Stage, remote, or production action |
| 2026-09-03 | Plan governance final review | `APPROVE` | `90b8bf4737ba934dac02f7b04781ad9f5fd5e7d0` | `reviews/PLAN-GOVERNANCE-20260903.md`; next action is a separate `BRANCH_BARRIER`, not Stage completion |
| 2026-09-03 | Execution authority | `AUTHORIZED_CODEX_GATED` | All Stages | supersedes prior execution-not-authorized note; owner delegated management/final acceptance to Codex; no per-Stage user approval; human Writer action unchanged |
| 2026-09-03 | Parallel execution governance | `COMPLETE_PLAN_ARTIFACT` | Cursor/Codex workflow | one Coordinator; one writer by default; max two with Codex pairing receipt; serial integration and independent final review |
| 2026-09-03 | Plan baseline | `REFRESHED` | `main@f3be9ae2c4e9` | three Market recovery/performance commits reviewed; 45 focused tests passed; no plan invariant invalidated; Stage baseline still refreshes at execution |
| 2026-09-03 | Plan sections 1–5 | `DESIGN_APPROVED` | Architecture, Market, Deploy, Documentation | owner review completed; execution remains unauthorized |
| 2026-09-03 | `D-02` | `APPROVED` | Product DNS TTL | permanent 30-second TTL; DNS verification/fence still required |
| 2026-09-03 | `D-03..D-06` | `APPROVED` | Deploy SLO, rollback and artifact transport | recorded in Master Plan and Section 4 |
| 2026-09-03 | `DOC-10` | `COMPLETE_PLAN_ARTIFACT` | Plan packaging | Master, YAML, section plans, templates and Cursor Skill created on plan branch |
| 2026-09-04 | `P1-00` | `COMPLETE` | `a0f22b7d476429974b9d4c8a7ae3d0d5e4a99be9` | Codex `APPROVE` for Assignment `WA-P1-00-SCOPE-20260903-002`; receipt `reviews/P1-00-WA-P1-00-SCOPE-20260903-002.md`; leftover freeze owned by `P1-00b`; `DR-015` still `DPL-1`; `DR-018` `NOT_REINSPECTED` |

### Record `P1-00-COMPLETE-20260904`

- date: `2026-09-04`
- stage_id: `P1-00`
- from_state: `IN_PROGRESS`
- to_state: `COMPLETE`
- actor: `CURSOR_COORDINATOR` after `CODEX_FINAL_REVIEWER` `APPROVE`
- commit: `a0f22b7d476429974b9d4c8a7ae3d0d5e4a99be9`
- environment: integration `refactor/two-site-architecture`; no live host mutation
- evidence_uri: `docs/refactor/two-server/reviews/P1-00-WA-P1-00-SCOPE-20260903-002.md`
- decision_or_gap_ids: `DR-015`, `DR-018`, `P1-00b`
- notes: leftover freeze owned by `P1-00b`; next dependency-ready Stage is `P1-00b`; `DR-015` remains `DPL-1`; `DR-018` remains `NOT_REINSPECTED`

## Append-only record contract

هر entry آینده باید `date`, `stage_id`, `from_state`, `to_state`, `actor`, `commit`,
`environment`, `evidence_uri`, `decision_or_gap_ids` و `notes` داشته باشد. secret،
raw payload یا credential در ledger ممنوع است. اصلاح entry قبلی با entry جدید و
`supersedes` انجام می‌شود؛ history بازنویسی نمی‌شود.
