# Staging Offer Overtime Acceptance Matrix

Branch: `candidate/offer-overtime`

Status: Stage 16 acceptance contract. Runs only against staging Iran and
staging foreign. Production must not be touched.

## Goal

Prove `وقت اضافه` on the real two-server staging topology after migration-first
deploy, with every staging user remaining at overtime `0` until isolated test
users deliberately enable the preference.

## Topology

| Role | URL | Surface |
| --- | --- | --- |
| Iran staging | `https://staging.gold-trade.ir` | WebApp + Iran API/sync |
| Foreign staging | `https://staging.362514.ir` | Telegram bot + foreign API/sync |

## Scenario Catalog

| ID | Surface | Assertion |
| --- | --- | --- |
| OT-PREF-WEBAPP-SAVE | WebApp Settings | Eligible owner saves `0..10`; Iran authoritative row updates; foreign mirror converges |
| OT-PREF-BOT-SAVE | Bot panel | Bot save succeeds only after Iran persists; Iran unreachable returns M7 |
| OT-PREF-DISABLED-REGRESSION | Both | Users at `0` keep automatic-trade-only behavior |
| OT-OFFER-WEBAPP-ORIGIN | WebApp market | Offer snapshots creator preference; lifecycle fields public-safe |
| OT-OFFER-BOT-ORIGIN | Bot create | Same snapshot/inert marker rules for bot-created offers |
| OT-REQ-IRAN-TO-IRAN | WebApp | Overtime request on Iran-home offer queues/presents/decides on Iran |
| OT-REQ-FOREIGN-TO-FOREIGN | Bot | Overtime request on foreign-home offer stays foreign-authoritative |
| OT-REQ-CROSS-FORWARD | Mixed | Remote edge forwards without false trade-complete ack (M18 pending) |
| OT-QUEUE-ORDER | Owner queue | FIFO promote after reject/cancel; one owner-occupying prompt |
| OT-CANCEL-REQUESTER | Requester | `لغو درخواست` closes nonterminal row and frees offer seat |
| OT-FINAL-TAIL | Approval | Partial remainder after overtime trade uses final-tail rules |
| OT-CHANNEL-MARKER | Foreign channel | `⏳` lifecycle marker via publication queue actions |
| OT-SYNC-RECOVERY | Both | Interrupt/recover leaves no impossible nonterminal pair |
| OT-TG-RETRY | Foreign delivery | Owner/requester private status retries through queue |
| OT-UI-RECONNECT | WebApp | Poll/reconnect restores owner prompt / requester countdown |

## Runner

```bash
python3 scripts/run_staging_offer_overtime_acceptance.py --mode plan
python3 scripts/run_staging_offer_overtime_acceptance.py --mode preflight \
  --expected-branch candidate/offer-overtime \
  --expected-release-sha "$(git rev-parse HEAD)"

# Mutating execute remains fail-closed until confirm env is set and preflight is green.
STAGING_OFFER_OVERTIME_ACCEPTANCE_CONFIRM=execute-staging-offer-overtime-acceptance \
python3 scripts/run_staging_offer_overtime_acceptance.py --mode execute \
  --expected-branch candidate/offer-overtime \
  --expected-release-sha "$(git rev-parse HEAD)"
```

Artifacts land under `tmp/staging-offer-overtime-acceptance/<run-id>/`.

## Exit Criteria

- Migration head `e8a4b5c6d7e9` is live on both staging databases
- Prefight topology/identity/TLS/internal-ingress checks pass
- Every catalog scenario passes with Iran/foreign DB + sync + delivery evidence
- Feature-disabled users show no regression
- Evidence zip contains redacted logs only
