# Dual-Platform Registration Stage 3: Canonical Invitation Creation

Date: 2026-07-11

Status: source implementation complete; independent review required before Stage 4.

## Purpose

Stage 3 removes split Invitation authorship. Iran is the only authority that creates Standard,
accountant, and customer invitations. The foreign Telegram bot submits signed commands and receives
the canonical links produced from the Iran WebApp origin. This is source work only: no runtime flag,
migration, deployment, or production behavior was enabled.

## Implemented Contract

- `core/services/canonical_invitation_creation_service.py` is the only Invitation constructor under
  `api`, `bot`, and `core`.
- Creation normalizes the Stage 1 natural identity, takes sorted creator and identity transaction
  advisory locks, checks canonical User identity, and reserves mobile/account identity atomically.
- An exact pending retry by the same creator, kind, role, mobile, and account returns the existing
  invitation. Cross-owner, cross-kind, cross-role, or changed-payload reuse fails closed.
- Standard bot creation uses the existing signed Iran transport and a deterministic request key.
  Iran revalidates the signature, source server, requesting admin, role permission, and key.
- Accountant and customer relation creation reuse the canonical primitive in the same transaction;
  exact relation retries must match every relation-owned field and the Invitation expiry.
- One owner-specific creation lock serializes relation capacity and duplicate-display checks.
- Standard/admin and Tier-1 invitations suppress invitation SMS through central flags. Accountant
  and Tier-2 keep the existing templates and report disabled, accepted, or failed status. Exact
  pending retries never resend SMS.
- Canonical Telegram and Iran WebApp links are additive to legacy response aliases. Accountant and
  Tier-2 remain Web-only under the existing eligibility policy.
- Touched registration links use validated `PUBLIC_WEBAPP_URL`; validation occurs before mutation
  and at startup when invitation contract v2 is enabled.
- Pending deletion and user-deletion invalidation preserve Invitation history through the shared
  transition lock, soft revocation, and transactional reservation release.
- Invitation-open audit records only the invitation id and surface, never token or mobile.

## Main Source Surfaces

- `core/services/canonical_invitation_creation_service.py`
- `core/invitation_creation_contracts.py`
- `core/invitation_creation_forwarding.py`
- `core/invitation_sms_policy.py`
- `api/routers/invitations.py`
- `api/routers/accountants.py`
- `api/routers/customers.py`
- `bot/handlers/admin.py`
- `bot/handlers/panel.py`
- `bot/handlers/start.py`
- `core/services/accountant_relation_service.py`
- `core/services/customer_relation_service.py`
- `core/services/user_deletion_service.py`

## Verification

- Focused backend: `162` tests passed.
- Full backend: `2774` tests passed; `29` opt-in tests skipped.
- Real PostgreSQL: `6` tests passed for exact concurrent retry, cross-owner/kind collision,
  relation exact/changed retry, owner-capacity race, and deletion-versus-completion.
- Static checks: `git diff --check`, Python compile, single-writer inventory, no hard Invitation
  delete, and no touched `FRONTEND_URL` registration link all passed.
- Database safety: active database `trading_bot_db` stayed at Alembic `f7c8d9e0a1b2`, Stage 1 schema
  object count stayed zero, and Stage 3 scratch database count returned to zero.

The full and focused logs contain expected deprecation and simulated audit-sink warnings from older
tests. The sandbox-only full run first failed three environment checks because Docker socket access
and local Nginx bind were denied; the same complete suite passed outside the sandbox and that passing
run is the final evidence.

## Open Gates

- Independent review of the exact Stage 3 commit and evidence ZIP must return GO before Stage 4.
- Any new or missed Invitation writer must be moved to the canonical service before schema rollout.
- Stage 1/2 review findings may reopen their foundation and therefore invalidate Stage 3 assumptions.
- Web post-commit response-loss, the complete foreign writer inventory for Iran-owned User fields,
  migration ordering, feature flags, and two-server rollout remain later-stage or rollout gates.
- No Stage 1 migration, contract-v2 flag, registration-sync-v2 flag, or direct Telegram registration
  flow is enabled by this stage.

## Rollback Boundary

This stage is one source-only commit. Until a later approved migration and rollout, rollback is the
revert of that commit. Do not partially enable the internal endpoint or new response contract on one
server, and do not deploy this stage independently of the reviewed migration/compatibility sequence.
