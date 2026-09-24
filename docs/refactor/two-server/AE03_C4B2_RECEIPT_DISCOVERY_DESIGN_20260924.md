# AE03 C4b2: bounded discovery of an existing terminal receipt

Status: **DESIGN ONLY / NOT ACTIVATED**. C4b1 can read an exact old quota response if given the full old request. This unit discovers that request from its immutable receipt using a durable Supervisor reservation; it must never create a new request, call Provider, wake Supervisor, or infer retry authority from absence.

## Scenario and source of identity

The Broker generated `request_id` and `created_at` before an interruption. The strict terminal receipt already contains the full exact request, its SHA-256, the exact response, and independently checked quota witness. A second request journal would duplicate this terminal authority and add cleanup/ordering failure windows. Therefore discovery can scan only the bounded private `terminal-quota/` directory and read each receipt through the existing strict reader. The caller must supply durable reservation fields from its own accepted run record: `controller_run_id`, `run_prefix`, `task_path`, `task_sha256`, `source_head`, `assignment_id`, and `assignment_sha256`. No field may be guessed from the receipt to satisfy the query. `request_id` and `created_at` come from the one matched receipt and are verified by C4b1; they are not regenerated.

| Observation | Safe outcome |
| --- | --- |
| Exactly one fully valid receipt matches every reservation field | Re-read it with C4b1, verify same request/response fingerprint and any existing response queue file, then return the old response without writing. |
| No match, missing directory, or an in-flight claim without receipt | Hard stop / no reattachment. Absence never authorizes `make_request` or Provider replay. |
| Two receipt IDs match one reservation | Hard stop as duplicate semantic identity; do not pick newest. |
| Any corrupt, linked, unexpected or over-limit directory entry | Hard stop; do not skip a record that might have matched. |
| Directory names/inode/owner/mode change during scan, or receipt changes between discovery and C4b1 | Hard stop; do not return a result from mixed snapshots. |

Bound the scan with the existing 64-entry strict private-directory reader. Do not create/chmod the directory or scan unrelated proposal/run trees. Check full typed field equality (including exact strings, no Python numeric coercion), pin the directory identity across discovery, and compare the selected receipt before/after the C4b1 read. Unit tests must include unique/no/multiple matches, malformed unrelated receipt, symlink/hardlink, overflow, path swap, stale witness and zero mutation. This is an observation primitive, **not** the Supervisor wait transition; C5 separately proves that the supplied reservation was durably accepted and that STOP, refs, assignment, budget and lock gates permit a wakeup. C4c later defines retention so live waits cannot lose their receipt while old evidence does not fill disk forever.
