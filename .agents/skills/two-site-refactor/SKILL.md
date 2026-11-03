---
name: two-site-refactor
description: Execute the Finland-primary/Iran-standby refactor through the configured Qoder coordinator, Gemini implementation and independent reviews, exact-byte acceptance, and serial Git integration.
---

# Two-Site Refactor

## Current execution contract — 2026-11-03

Read the project memory manifest and brief, then only routed memory and the active
Stage/Assignment. The canonical plan, dependency order and actual accepted evidence
remain authoritative. This current workflow supersedes historical fixed Codex-model,
JavaScript-tool, miniature drafting and incident-specific recovery instructions.

Models and reasoning come exclusively from
`docs/refactor/two-server/templates/automation-models.json`.
Do not infer a model from a CLI pathname, historical receipt or caller default.
Changing this configuration is a controlled boundary operation: complete or stop the
current unit, validate the file, commit the intended configuration, then start a new
cycle. Never silently downgrade a model or change settings within an active unit.

Roles:
- Qoder Coordinator selects one necessary, dependency-ready business outcome and
  supplies complete authoritative context, requirements and proportional tests.
- Gemini Worker writes the proposal, implementation, tests and documentation.
- A fresh Gemini Reviewer independently reviews the exact candidate after native
  tests pass. It does not modify the proposal.
- A separate read-only Qoder final-review session approves or requests corrections.
  A change request returns to Gemini; subsequent tests and reviews bind the new bytes.
- The deterministic controller validates all receipts and committed bytes, publishes
  the checkpoint, and retains one Git integration owner.

## Ordinary unit

1. Verify active Stage, Assignment, source HEAD and missing behavior. Prioritize a
   required uncovered scenario or Stage blocker; commits and bookkeeping alone do
   not constitute new behavior coverage. Group related scenarios when complete
   context and review capacity permit; never reduce assertions to fit a soft target.
2. Prepare/check the task with the existing deterministic builder. Supply exact
   dependency definitions, Unicode literals, scenario selectors and machine-derived
   budgets. The effective task/output contract owns hard limits; drafting hints are
   advisory. Repair preparation locally before spending a model call.
3. Submit through the trusted outside-sandbox Provider Broker. It owns networked
   Gemini execution, native hermetic tests, independent Gemini review and the final
   Qoder review. Only actual, bound APPROVE may produce READY_FOR_CODEX (a historical
   protocol label, not a requirement to run the Codex CLI).
4. Obtain the complete candidate packet and review its behavior. Use
   `python3 scripts/two_site_qoder_review.py --task <path> --worker <id> --reviewer <id> --sha <sha>`
   to validate/reuse the genuine final approval. This does not rerun an already
   valid final-review session.
5. Apply only the reviewed bytes with
   `python3 scripts/two_site_apply_reviewed.py --task <path> --worker <id> --reviewer <id> --sha <sha>`.
   The helper checks approval, source, scope and exact bytes; it never commits.
   Recheck `two_site_cycle_tools.py review --applied` before staging. Reuse only the
   exact outside-Broker native PASS whose task, source, candidate, selectors and
   environment remain unchanged. Changed test inputs require fresh Broker tests.
   Do not execute nested bwrap or duplicate pytest inside the Coordinator sandbox.
6. Stage every and only approved proposal path and make one ordinary single-parent
   commit on the integration branch. Require clean state, unchanged protected refs,
   exact committed proposal and final Qoder receipt before checkpoint publication.

## Recovery and boundaries

- Preserve user STOP and all genuine acceptance/security boundaries.
- Only proven transient late-Reviewer failures may reuse the exact Worker/native
  evidence with fresh Reviewer sessions and persisted finite attempt counts.
- Source/task drift, an accepted result, uncertain mutation, quota or semantic
  rejection is not permission to replay or reset a reservation.
- Keep failures and receipts truthful. Never invent model sessions, test passes,
  approval, coverage, cost savings or clean-cycle counts.
- A repeated semantic defect requires cause-specific Gemini correction, not the
  same unchanged request under a new ID. Keep unrelated ready work independent.
- Runtime/secret material remains outside model context. Native tests use the sample
  test environment; no live Env, private key, session or production data is exposed.
- Main, origin/main and candidate refs remain protected. No push, reset, amend,
  forced ref change, production/deploy/DNS/Writer mutation follows from this skill.
  Web Writer transfers remain explicit human Dashboard actions.
- Authorized maintenance intervention may repair the automation without routine
  reapproval, with independent review, focused tests and honest author provenance.
  Do not fabricate Gemini evidence for changes authored by a maintenance agent.

## References

Read `docs/refactor/two-server/QODER_AUTOMATION.md` for model configuration and the
current launcher. Read Stage/domain contracts only for the unit being implemented.
Historical SDK/recovery records remain evidence, not active startup instructions.
