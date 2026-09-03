# Two-Site Refactor Change Ledger

این ledger فقط تغییر وضعیت و پیوند evidence را ثبت می‌کند؛ شرح تصمیم‌ها در
[`MASTER_PLAN.md`](MASTER_PLAN.md) و ترتیب در [`execution-order.yaml`](execution-order.yaml)
است و این فایل source of truth موازی نیست.

| Date | Record | Status | Scope | Evidence / note |
| --- | --- | --- | --- | --- |
| 2026-09-03 | Plan sections 1–5 | `DESIGN_APPROVED` | Architecture, Market, Deploy, Documentation | owner review completed; execution remains unauthorized |
| 2026-09-03 | `D-02` | `APPROVED` | Product DNS TTL | permanent 30-second TTL; DNS verification/fence still required |
| 2026-09-03 | `D-03..D-06` | `APPROVED` | Deploy SLO, rollback and artifact transport | recorded in Master Plan and Section 4 |
| 2026-09-03 | `DOC-10` | `COMPLETE_PLAN_ARTIFACT` | Plan packaging | Master, YAML, section plans, templates and Cursor Skill created on plan branch |

## Append-only record contract

هر entry آینده باید `date`, `stage_id`, `from_state`, `to_state`, `actor`, `commit`,
`environment`, `evidence_uri`, `decision_or_gap_ids` و `notes` داشته باشد. secret،
raw payload یا credential در ledger ممنوع است. اصلاح entry قبلی با entry جدید و
`supersedes` انجام می‌شود؛ history بازنویسی نمی‌شود.
