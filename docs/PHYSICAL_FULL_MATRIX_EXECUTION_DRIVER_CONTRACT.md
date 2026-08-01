# Physical Full-Matrix execution-driver contract

`core.physical_full_matrix_execution_driver` replaces the retired two-server
Full-Matrix executors with a narrow, root-only, default-off phase boundary.
It is not a live run and has no filesystem, network, Docker, PostgreSQL,
Object Storage, SSH, SCP, rsync, route, promotion, or historical-runner
implementation.

The initial accepted input is an opaque process-local
`VerifiedPhysicalFullMatrixCampaignReadiness`, minted by the readiness
boundary only after a complete positive report for the normal FI-writer
direction, plus an explicit binding for campaign, release, release-manifest
hash, readiness hash, route hash, Writer epoch/lease, Witness transition, and
witnessed-term proof hash. A raw report or caller-constructed wrapper is not
accepted. The underlying report must contain every declared evidence slot and
remain non-authorizing. Plan construction performs only a process-local
membership check; immediately before it can prepare adapters or read the
journal, the driver re-runs the readiness assessment at its execution clock.
Any stale, changed, blocked, missing, or unprovenanced result is rejected.
Any legacy runner artifact is rejected before a plan is produced.

The public plan is likewise only a redacted, process-local projection.  The
builder records its exact object identity against a private primitive snapshot;
copying or serializing the projection cannot make another usable plan.  The
driver derives phase facts, requests, receipt-chain checks, and receipt bodies
only from that snapshot.  Before and after every injected adapter or journal
callback it rechecks the visible plan, so a frozen-dataclass bypass or a
callback that mutates a public plan/request/phase cannot alter a later effect
or receipt.  Adapters receive a detached request copy, not the expected
request used by validation and receipt construction.

The graph is literal and closed:

1. normal FI writer durable-ack matrix — destructive;
2. fence FI writer — destructive;
3. recover WA-IR through private versioned Object Storage — destructive;
4. Witness-promote WA-IR — destructive;
5. WA-IR writer durable-ack matrix — destructive;
6. rebuild FI through private versioned Object Storage — destructive;
7. Witness-restore FI writer — destructive;
8. final three-site convergence oracle — non-destructive.

Each runtime phase adapter is injected by exact phase name and must return a
fresh redacted oracle bound to the active chain binding. Its transport profile
is fixed by the graph and every oracle/receipt declares both direct FI→IR and
direct IR→FI control, plus legacy compatibility, as `forbidden`.

The initial term cannot honestly predeclare the term that Witness will issue
when it promotes WA-IR. Therefore phase 4 must mint a required redacted
successor binding for `WA-IR → Object Storage → WA-FI`, with a strictly newer
Witness term, distinct route/readiness/evidence hashes, and no shared secret.
Phases 5–7 receive only that successor binding. Similarly, phase 7 must mint
a newer FI-writer successor binding before phase 8 can observe final
convergence. A successor attached to any other phase is rejected. The driver
does not prove an adapter's internal implementation; production adapters
remain a live gap and must be reviewed to use only local/Witness and private
versioned Object-Storage-pull boundaries.

Receipts are canonical JSON containing hashes and identifiers only. They form
an ordered chain with a previous-receipt hash and, at the two Witness
transition phases only, the validated successor binding. Before any adapter invocation,
the injected journal must atomically claim the exact run/plan/sequence/request
tuple. A pre-existing exact durable receipt is returned without another
adapter call; a live claim owned elsewhere fails closed. The journal must only
append through that live opaque claim and must never reassign a live claim as
an automatic retry. A crash while a claim is live therefore requires an
explicit, separately reviewed reconciliation rather than a second destructive
phase invocation. Mismatched, reordered, or non-durable receipts fail closed.
The driver invokes at most one adapter per call and the receipt still says
`full_matrix_executed: false`. Only a separate final evidence/reporting
boundary may ever make a completion claim.
