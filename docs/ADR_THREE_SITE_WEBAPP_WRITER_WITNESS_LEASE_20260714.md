# ADR: Three-Site WebApp Writer Witness And Lease

Status: Accepted as the implementation contract; production enablement remains blocked
Date: 2026-07-14
Roadmap: `docs/THREE_SITE_WEBAPP_DR_FAILOVER_RECOVERY_ROADMAP_20260710.md`

## Decision

Use one durable Iran-reachable witness for the logical WebApp writer term. The
witness is independent of both product-writer decisions: it grants a
time-bounded lease but never decides sync readiness, application readiness, or
Arvan routing.

The authoritative witness deployment is associated with `webapp_ir`, but its
state is separate from ordinary product sync and must eventually use a
least-privilege database identity and a control endpoint/process that is not
the public WebApp API.

Normal WebApp-FI operation obtains and renews a signed lease through the
Iran-reachable witness. If that path is lost, WebApp-FI may continue only until
the locally verified lease approaches expiry, then all WebApp-authoritative
writes and jobs fail closed. WebApp-IR cannot acquire the next term until the
witness's database clock proves the previous lease expired.

This intentionally makes safe failover no faster than the accepted short-outage
window. Initial tunable values are:

| Parameter | Initial value | Safety purpose |
|---|---:|---|
| Lease duration | 180 seconds | Exceeds the 120-second short-outage window without making failover unbounded |
| Renewal interval | 30 seconds | Provides several retry opportunities under ordinary jitter |
| Local expiry safety margin | 15 seconds | Fences before the signed deadline when clocks or scheduling differ |
| Maximum acceptable clock offset | 5 seconds | Blocks readiness when witness/client time evidence is unsafe |

These values are provisional operational defaults. Production enablement
requires measured Iran-Finland link behavior and an owner-approved RTO.

## Safety Invariants

1. The witness database clock, not a requesting application's clock, decides
   whether an existing lease is still valid.
2. A non-expired lease can be renewed only by the same site, epoch, and lease
   identifier.
3. A new acquisition is rejected until the previous lease expires.
4. Every successful acquisition increments `writer_epoch`; renewal never does.
5. A handoff/drain action only blocks renewal. It does not make the cached old
   lease expire early and therefore cannot authorize immediate takeover.
6. Lease proofs are signed with Ed25519. The private key exists only in the
   witness control surface; WebApp runtimes receive only the public key.
7. A local writer imports a proof only after signature, site, epoch, lifetime,
   and safety-margin validation. The proof hash and witness transition are
   stored with local writer state.
8. HTTP mutations, startup mutations, background jobs, and SQL commit fencing
   reject an expired, mismatched, or missing lease whenever witness enforcement
   is enabled.
9. `/health/origin-ready` remains false while witness enforcement is disabled,
   even if local dependencies and local writer state are otherwise healthy.
10. Arvan mutation occurs only after writer lease, local writer state,
    application readiness, and later sync/parity gates all agree.

## State Machine

```text
vacant/expired -- acquire(site) --> leased(site, epoch+1, lease_id)
leased(site)   -- renew(same term) --> leased(site, same epoch, later expiry)
leased(site)   -- drain(same term) --> draining(site, same expiry)
draining       -- renew -----------> rejected
leased/draining -- acquire(any) ---> rejected until witness-time expiry
expired        -- acquire(site) ---> leased(site, epoch+1, new lease_id)
```

Repeated requests use a stable request identifier and payload hash. The witness
returns the original transition for an exact retry and rejects reuse with
different parameters.

## Failure Behavior

| Failure | Required behavior |
|---|---|
| FI cannot reach witness | FI writes continue only to the local safety deadline, then fail closed |
| IR cannot reach FI but FI can renew | IR cannot promote; availability is sacrificed until ownership is unambiguous |
| Witness is unavailable | No new lease; current holder fences at local deadline; no bypass |
| Witness restarts | Durable state preserves holder, epoch, lease id, deadline, and request receipts |
| Delayed renewal arrives after expiry | Rejected; requester must acquire a new term after observing current state |
| Duplicate acquisition request | Same request/payload returns the original result; different payload is rejected |
| Operator requests early takeover | Rejected until prior expiry; `drain` alone is not proof of early revocation |
| Client clock offset exceeds threshold | Origin readiness and promotion fail closed |
| Asymmetric partition | At most the site holding a still-valid signed lease may write |

## Why Not The Alternatives

- Peer heartbeat between WebApp-FI and WebApp-IR is insufficient: each side can
  mistake one-way loss for peer death.
- Arvan origin selection is not a writer lock: it does not stop background,
  internal, delayed, or direct-origin writes.
- A Finland-only witness is unreachable from Iran in the exact outage where
  Iran must promote.
- Immediate manual force takeover can overlap a cached FI lease and is
  therefore forbidden.
- Cross-site acknowledgement on every user transaction was already rejected
  because normal writes must not depend on unstable international connectivity.
- A long-lived static promotion token cannot prove that the previous writer has
  stopped.

## Rollout Boundary

The first implementation slice may add the durable witness state machine,
signed proof validation, local proof storage, feature-gated fencing, CLIs, and
failure tests. It must not enable `WRITER_WITNESS_REQUIRED`, start WebApp-IR,
create a public witness endpoint, or change Arvan.

Before production enablement, later slices must add:

1. a dedicated witness process/API with replay-safe authenticated requests;
2. automatic FI renewal and local proof refresh;
3. separate least-privilege witness credentials and private-key custody;
4. multi-vantage clock-offset and witness availability checks;
5. deterministic partition, pause, delayed-packet, and concurrent-acquisition
   tests against independent site databases;
6. operator RACI, two-person promotion approval, audit retention, and break-glass
   rules that still cannot bypass a live prior lease;
7. measured lease/RTO tuning and repeated three-site staging drills.

## Source Progress - 2026-07-15

The next source slice adds the separately runnable `writer_witness_app:app`, a
minimal configuration class that does not import product settings, a dedicated
two-table PostgreSQL bootstrap, pairwise site-bound HMAC authentication, and
automatic active-writer renewal with atomic local proof refresh. Successful
commands and state-dependent rejected commands both receive durable receipts;
this closes the case where a rejected acquisition packet is replayed only after
the old lease expires. Ambiguous transport retries keep the exact request id.

This is not a production deployment. Both `WRITER_WITNESS_REQUIRED` and
`WRITER_WITNESS_AUTO_RENEW_ENABLED` remain false, origin readiness rejects an
enabled witness without automatic renewal, and no CDN/origin setting changes.
Items 1 and 2 above are source-complete but remain operationally open until the
service is deployed with isolated credentials and proven against independent
site databases. Items 3 through 7 remain production blockers.
