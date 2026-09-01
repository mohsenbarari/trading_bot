# Web/Bot Surface Policy Matrix

Status: provenance contract approved; implementation and behavior mapping open

## Rule that consolidation must preserve

Web and Telegram Bot are separate product surfaces even when they use the same
database and run on the same host. A policy decision may depend on all of these
dimensions:

```text
offer_origin × request_origin × actor_role × customer_tier × time_context
× policy_version × authority/home_site
```

Physical host, current DNS destination and process name are not substitutes for
those dimensions.

## Provenance fields

| Entity/action | Current durable evidence | Consolidation requirement | Status |
| --- | --- | --- | --- |
| Offer creation | `offers.home_server`; creation service derives surface from it | immutable `offer_origin_surface` independent of home/placement | contract approved; field/backfill still a blocker |
| Request creation | `request_source_surface`, `request_source_server`, `request_home_server`, tier/commission/workflow snapshots | retain immutable source and snapshots | present; migration/parity tests required |
| Trade creation | Offer/request references and actors; no complete immutable provenance snapshot observed | snapshot Offer origin, Request origin, execution surface, policy version and sensitive actor/role/tier context | contract approved; schema/migration/tests open |
| Expiry action | `expire_source_surface` and command receipt | preserve action source separately from Offer origin | present for expiry; full command coverage required |
| Actor/persona | user/relation/accountant/admin context | snapshot every policy-sensitive role/tier value that may later change | partial; behavior audit required |

Current Offer logic maps `TELEGRAM_BOT` to the historical `foreign` home and
`WEBAPP` to the historical `iran` home. After co-location both may have the same
physical home, so this inference would destroy provenance. Before any rewrite of
`home_server`, an ADR and migration must add/backfill the immutable surface using
the old mapping while it is still unambiguous.

## Required combination matrix

The rows below are distinct test families. “Preserve current policy” means the
reference topology and target topology must return the same eligibility,
confirmation, quota, commission, publication, notification, timeout and failure
behavior for that combination. It does not claim those values are identical
between rows.

| Offer origin | Request/action origin | Personas to cover | Time contexts | Required assertion |
| --- | --- | --- | --- | --- |
| Web | Web | owner, customer tier 1/2, accountant, admin | normal, overtime, closed/expired | Web-origin rules and Web notifications unchanged |
| Web | Bot | linked/unlinked user, tier 1/2, accountant/admin where allowed | normal, overtime, retry | cross-surface request keeps both origins; Bot copy/callback and Web state agree |
| Bot | Web | owner, tier 1/2, accountant/admin where allowed | normal, overtime, retry | Web request does not turn Bot offer into Web-origin |
| Bot | Bot | linked/unlinked user, tier 1/2, accountant, admin | normal, overtime, close/republish | Telegram publication/callback/delivery ownership remains sole and idempotent |
| Internal/system | either | scheduled job/reconciliation/operator | expiry, retry, outage recovery | action source and business origin remain separately auditable |

For each row, tests must cover at least:

- accepted, rejected and permission-denied outcomes;
- tier-2 and relation/accountant differences;
- normal-market and overtime paths, including owner approval freshness;
- duplicate click/request/retry and concurrent action;
- expiry, cancellation, republish and terminal state;
- commission/policy snapshots and later user/tier changes;
- notification audience, Web realtime/Web Push and Telegram publication/delivery;
- failure before commit, after commit/before side effect and side-effect retry.

## Approved semantic contract for the next ADR

The semantics below are approved. The ADR may refine physical column names and
normalization, but it may not weaken or derive them from physical topology:

```text
origin_surface     immutable product provenance: WEBAPP | TELEGRAM_BOT | INTERNAL
home_site          mutable only by an explicit authority/migration contract
action_surface     provenance of the current command/request
policy_version     version used for the decision
actor/tier snapshot values required to reproduce the decision
```

The same immutable provenance must travel through DB rows, event/outbox payloads,
sync/merge mapping, audit logs and parity hashes. Backfill confidence and any row
that cannot be derived uniquely must be reported; guessing is forbidden.

## Gate

`P1-04` cannot remove internal Finland sync or rewrite topology labels until:

1. the Offer and Trade ADR implements the approved semantic contract without
   collapsing its independent dimensions;
2. every existing row has a deterministic migration or explicit quarantine;
3. all combinations above have reference-topology characterization tests;
4. the target runs the same tests without deriving surface from physical host;
5. policy differences are documented as intentional, not normalized for
   convenience.
