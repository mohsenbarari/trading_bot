# Physical Full-Matrix live status

This document is a factual handoff aid for the replacement three-site
physical data plane.  It is not an approval, a deployment manifest, or a
claim that a Full Matrix campaign has started.

## Current status

`Full Matrix: not started`

### Latest verified local hardening — still not live readiness

- The legacy FI<->IR peer HTTP/SSH/control surfaces and the generic root
  Compose entrypoints are fail-closed.  The static architecture preflight
  currently checks 73 registered artifacts with zero findings.  Historical
  business forwarders deliberately return an unavailable response until a
  separately reviewed Object-Storage command protocol exists; this is an
  intentional availability restriction, not a replacement transport.
- WA-FI now has a default-off, root-only exact-argv Docker runner for the
  already reviewed local PostgreSQL helper container.  It is not installed or
  wired to a campaign, performs no Object-Storage or peer operation, and has
  not captured any live data.
- WA-IR has a default-off FD-only recovery input binder.  It validates the
  existing local materialization intent, frozen stage receipt, and inherited
  root-owned descriptors without accepting paths or launching a runner.  It
  is a prerequisite for a future attested local recovery runner, not a
  recovery, promotion, or V4 adapter.
- V4 Phase 1 has a typed, process-local strict-ACK provenance verifier that
  cross-pins real Gen2 ACK/strict-response evidence to the exact V4 request.
  Its result is explicitly *not* a phase success.  The V4 driver now delivers
  a process-local durable effect-start correlation plus an immutable
  Witness-anchor projection to an adapter, but the legacy ACK payload/runtime
  still has no V4 effect correlation.  An experimental default-off checkpoint
  grammar and append-only child relation reserve the intended post-start
  capture shape, but are quarantined outside both the release candidate and
  standard Alembic chain: no transaction coordinator/reconciliation verifier
  can yet prove a newly generated ACK occurred after that start and known
  outer commit.
- Phase 2 has a separate, default-off retired-FI predecessor-fence grammar.
  It accepts only independently signed executor, observer, and Witness
  anti-replay evidence bound to the exact former FI term, V4 request, and
  immutable start anchor.  It is not a fence executor, observer service, or
  Witness ledger, and produces no writer/promotion/traffic authority.
- Phase 3 has a separate canonical evidence-only admission, without importing
  the retired V1 recovery runtime.  It needs both the verified retired-FI
  evidence and the root journal's typed, process-local Phase-2
  *completion-anchor* proof.  That proof is attached only to the driver's
  private adapter request and exact-cross-pins the P2 start/fence, P2
  completion receipt/anchor, and P3 start; it is not caller input.  The seam
  still cannot start a runner or bind an FD.
- Phases 4 and 7 have an isolated successor-transition evidence grammar.  It
  requires fresh successor terms/readiness projections and distinct executor,
  observer, and Witness evidence, but deliberately does not create a
  promotion service or treat a start-anchor as permission for the next phase.
- Phase 5's reverse strict-ACK carrier has V2R wire/replay plus a pure,
  signed eight-role admission/profile layer, a claims-only public-bundle
  issuer, a canonical public 2/2/4 site-manifest renderer, and a complete-set
  preparer.  Those pieces only package already verified local claims and
  explicitly remain non-operational.  A fresh eight-role V4R Object-Storage
  control plane (IAM, scoped credentials, adapters, runtimes, durable sender
  state, and an installed manifest) is still missing; none of the
  recovery-data or normal-V2 roles may be reused.
- Phase 8 has a default-off typed convergence admission requiring four
  independent future owner verifiers (FI primary, IR standby/replay,
  object/blob parity, and fresh Witness/route state) and a Phase-7
  completion-anchor proof.  It rejects a generic oracle/evidence hash and
  cannot report final convergence today.

The decisive execution gaps remain open: no real V4 phase adapter is
installed, legacy promotion proof cannot mint the required V4 successor
readiness, and the separately versioned IR->FI reverse carrier is not yet a
deployed reverse strict-ACK/runtime/IAM plane.  These are implementation and
live-evidence gates, not paperwork that can be waived.

### Source-level execution audit (current)

The local proof boundaries have now been audited against their claimed
operational seams.  This is deliberately more restrictive than treating a
passing unit test as host readiness:

- Phase 1 has no production V4-correlated post-effect checkpoint coordinator.
  The legacy Gen2 ACK records lack all 13 required V4 effect-start and
  immutable-anchor correlation fields, so they can never become a P1 result.
  An experimental default-off checkpoint grammar/table and isolated migration
  (under `migrations/experimental/`, not the standard Alembic chain) exist
  only outside the release-candidate inventory: they do **not** currently
  prove a Gen2 parent was created in the same root transaction or remained
  pending at capture time.  They must not be materialized until a transaction
  envelope, database-level causal fence, and post-commit reconciliation
  verifier exist.
- Phase 2's runtime is only an injected executor/observer/Witness seam.  No
  concrete FI fencer, independent observer, or durable Witness replay ledger
  exists outside test doubles.  The new structured scope/install verifier
  precisely requires application, database, service-manager, and provider
  write-path coverage, but is evidence-only and is not yet consumed by that
  runtime.
- Phase 3 has no V4 recovery executor.  The existing IR-only Object-Storage
  pull/materialization pieces are legacy and require a live FI-holder term,
  which makes them intentionally incompatible after the P2 retirement fence.
  The future V4 runner must consume P3's typed admission, use fixed local FDs
  and a pinned PostgreSQL image, and enforce Object-Storage-only egress; it
  must not adapt the legacy FI-term runtime.
- Phases 4 and 7 invoke their future executor seam before they receive
  Witness evidence.  Their Witness result is post-transition evidence, not a
  durable pre-operation reservation, so they cannot safely provide the sole
  promotion/single-writer gate until an independent durable Witness operation
  reservation and concrete executor/observer exist.
- Phase 6's current binder only duplicates a read-only descriptor for an
  empty root-owned FI PGDATA directory.  It does not pull, decrypt, restore,
  replay WAL, prove terminal LSN, or start PostgreSQL; the real reverse
  rebuild still needs an independently reviewed runner and durable completion
  evidence.

Consequently no phase runner should be wired by merely enabling a flag or by
returning a semantic `oracle-succeeded` value.  Each missing executor must
bind its real post-effect evidence to the driver's exact journaled start and
completion anchors.

The driver now enforces this mechanically: execution requires an exact,
separate eight-phase owner-verifier map before it claims or starts any effect.
After an adapter returns, and again immediately before a completion receipt is
appended, the matching owner verifier must accept a process-local,
non-serializable post-effect capability cross-pinned to the private V4 start
authority and immutable anchor.  A generic/no-op oracle is rejected; the
current evidence-only P6 admission, for example, cannot create a P6 receipt.
No real owner capability has yet been installed, so this deliberately blocks
all V4 execution rather than treating tests as a campaign.

### Latest V4 sequencing verification

- The root V4 driver, receipt journal, journal integration, root composition,
  and non-operational CLI currently pass 72 focused local tests.  The CLI was
  checked again and reports `no-phase-execution-path`; it cannot execute a
  phase from command-line input.
- The Phase-2 retired-FI predecessor-fence, Phase-3 recovery-admission,
  Witness successor-transition, and final-convergence contracts currently
  pass 41 focused local tests.  These are all evidence-only and default-off.
- In particular, Phase 3 cannot infer that a P2 effect-start anchor directly
  precedes a P3 effect-start anchor.  The P2 completion receipt is necessarily
  between those records.  P3 therefore requires the root journal's separately
  typed, verified P2-completion-to-P3-start anchor projection and fails closed
  with `P2_COMPLETION_ANCHOR_REQUIRED` when it is absent or
  `P2_COMPLETION_ANCHOR_INVALID` when it is tampered or mismatched.  No
  recovery runner, PostgreSQL action, writer change, or traffic switch can
  result from this local contract.
- The completion projection is now a process-local, non-serializable journal
  capability: P3 receives it only on the driver's private adapter request.
  It cross-pins the signed P2 fence, P2 start, P2 completion receipt and
  completion anchor, and P3's exact previous anchor head/sequence.  It is
  still proof plumbing, not a P2 host fence, recovery executor, or a phase
  success.
- P2 also has a default-off root-gated one-shot orchestration boundary.  Its
  only injected seams are FI fence executor, independent observer, and
  Witness durable admission, in that order; an attempt is consumed before
  the first call and no ambiguity is retried.  The repository contains no
  concrete implementation of those three live seams.
- P4 and P7 have the same one-shot, root-gated orchestration discipline for
  a successor transition: target executor, independent observer, then
  Witness admission.  Their policy and request are revalidated before and
  after each seam, so a mutation aborts before a later seam is reached.  The
  boundary neither marks the phase complete nor creates a next-phase permit.
- The first V2R Phase-5 control-plane boundary now has a distinct signed
  admission/profile matrix for the eight reverse-mailbox roles.  It requires
  eight separate V2R identities, labeled deny-pins for all four recovery-data
  and eight normal-V2 identities, exact host-attestation hashes, and the
  fixed site/role/prefix/action topology.  A pure claims-only issuer, public
  2/2/4 manifest renderer, and complete-manifest-set preparer round-trip
  through independent admission, but have no credential opener, S3 client,
  provider call, installer, or delivery callback, so they cannot execute
  Phase 5.
- P6 is an evidence-only reverse-rebuild admission.  It accepts only an exact
  P5 completion receipt and typed completion anchor immediately preceding the
  P6 start, a pinned IR-to-private-Object-Storage-to-FI plan, and socket-only
  FI inputs.  It contains no file-descriptor binder, Object-Storage pull
  executor, legacy runtime, or materialization implementation.
- P8 has four explicitly independent diagnostic verifier domains—FI, IR,
  Object Storage/blob lineage, and Witness—and requires the typed P7
  completion bridge.  In particular, Object Storage cannot be relabelled as
  a Witness verifier.  This remains diagnostic-only and cannot declare
  convergence or authorize a campaign.
- The static three-site preflight was re-run against 73 registered artifacts
  and returned zero findings.  This validates source-level route restrictions;
  it is not a fresh provider, host, IAM, Object-Lock, database, or Witness
  observation.

The only permissible normal data route remains:

```text
WA-FI -> private versioned Arvan Object Storage -> WA-IR
```

WA-IR must not receive a direct FI-to-IR SSH, SCP, rsync, SFTP, PostgreSQL
streaming, or database-control path.  The Witness remains independent.

If WA-IR is promoted after a witnessed FI fence, the same restriction applies
in reverse: FI becomes a standby only through a distinct private, versioned
Object-Storage route:

```text
WA-IR -> private versioned Arvan Object Storage -> WA-FI
```

Neither direction permits a host-to-host data or control shortcut.  A
promotion is not a reason to copy, move, or reuse the other site's secret
material.

Object Storage has a deliberately narrow role in this design: it is a
private, versioned relay for encrypted, immutable recovery, deployment, and
preflight artifacts.  It is not a shared database volume, a plaintext secret
store, a writer-election mechanism, or evidence that the two PostgreSQL sites
are already synchronized.  Writer authority remains Witness-fenced; exact
database recovery and both-direction verification remain separate gates.

## Completed local protocol gates — not live readiness

The following are completed local code/proof gates. They reduce protocol
ambiguity, but they are neither deployment evidence nor permission to start a
campaign.

- The V1/V2 writer-term and bridge surfaces now use one canonical
  `writer_lease_id` grammar. In particular, the established short canonical
  form such as `writer-lease-73` is accepted consistently, while generic or
  lookalike aliases are rejected. This is a local validation invariant; it is
  not evidence of a current Witness lease or an active writer.
- The isolated Gen2 ACK chain and Gen2 campaign-readiness capability are now
  complete local boundaries. They cross-pin recovery evidence, the portable
  Witness attestation, strict Gen2 response, exact V1 parent, and signed
  bridge binding; they reject Gen1/reconstructed substitutes and revalidate
  against a supplied time. Their result remains process-local and explicitly
  non-authorizing for recovery, promotion, writer ownership, transport, or
  execution.
- The Gen2 bound SQL transaction envelope is complete as a default-off local
  transaction boundary. It accepts a fresh pre-PostgreSQL bridge capability,
  opens one clean root transaction, persists the reviewed Gen2 work before
  exposing restricted business DML, and finalizes only after the one commit.
  Its disposable, loopback-only PostgreSQL proof exercised the envelope on a
  dedicated scratch database; no project, staging, production database, or
  application request path was used. It does not prove host readiness,
  provider access, or deployment.
- The V4 materialization preflight is complete as a default-off local
  diagnostic boundary. It requires one exact root-composition capability, all
  eight named phase-adapter bindings, a narrow Witness-anchor interface, and
  exact Gen2 readiness freshly revalidated at the composition's
  timezone-aware trusted-clock sample. It rejects stale cached readiness,
  Gen1, partial/duplicate/mismatched adapter material, and bad clocks. It
  installs nothing, invokes no phase, anchor, provider, or host operation,
  and returns no execution, promotion, writer, or materialization authority.
- The release-seal boundary now has a concrete local, default-off inspection
  adapter. It pins one root-owned, no-symlink classic Git worktree and the
  fixed Git binary through descriptors, accepts only the four read-only query
  shapes emitted by admission, and suppresses repository aliases, fsmonitor,
  hooks, maintenance, submodule recursion, and transport protocols. Its
  pre/post observation includes device, inode, and timestamps so replacement
  during inspection fails closed. No seal was issued for the current dirty
  worktree, no image was inspected or created, and no provider/deployment
  action occurred.
- V4 phase-adapter material now also requires eight phase-specific,
  Ed25519-signed installation attestations under a root-built policy bound to
  the exact composition and fresh materialization preflight. This rejects a
  lookalike callback being labelled as a host installation. It remains local
  verification only: no host installer, provider call, phase, promotion, or
  campaign has run.
- The application database-construction audit now registers all 15 discovered
  construction boundaries. The historical alternate
  `src.infrastructure.database.connection` factory is inert and fails before
  it can create an engine; explicitly named migration, maintenance, and
  scratch paths remain outside the canonical runtime guard and therefore are
  not being misrepresented as covered application writers.

## Read-only dedicated-host observations

The recorded historical Bot-FI, WA-FI, WA-IR, and Witness instance identities
were read from Arvan ECC and matched the source-pinned instance IDs, regions,
and public addresses.  Each was `ACTIVE` at that collection time; this is not
a current provider readback.

The recorded ED25519 SSH fingerprints for Bot-FI, WA-FI, and Witness matched
their pinned `known_hosts` entries before any authenticated read-only SSH
inventory.  The historical, mismatched SSH host key for an unrelated legacy
address is not a target for this campaign and must not be accepted.

Read-only pinned SSH observations on Bot-FI, WA-FI, and Witness found:

- their dedicated 50 GiB staging volumes mounted as `ext4` with `noexec`;
- Docker daemon active, no containers and no Docker volumes yet;
- root-capable, noninteractive installation access available;
- `age`, Python, Git, and Docker present;
- NTP reported synchronized.

The public TLS endpoint for the selected Arvan Object-Storage region was also
reachable from those three hosts.  This is connectivity-only evidence: it does
not prove bucket access, Object immutability, or WA-IR receipt delivery.

These observations are not host-preflight receipts and must be recollected
through the reviewed four-host controller before campaign use.

## Object-Storage foundation — not yet re-verified

A prior working note described a dedicated Object-Storage bucket for this
campaign.  That statement is **not current, signed provider readback** and
must not be treated as an available campaign input.  Until a fresh,
least-privilege provider readback proves the exact bucket, owner-only private
ACL, versioning, Object Lock/retention behaviour, and an empty scoped
namespace, Full Matrix treats Object Storage as unprepared.

No runtime may reuse, alter, delete, or assume the existence of a historical
bucket, artifact, or identity on the strength of that note.  A future creation
or adoption decision must be made only through the reviewed four-role
provisioning path and then verified independently.

The remaining recovery-data authorization boundary is intentional: a complete
reversible active/standby design needs four different, least-privilege
Object-Storage identities, not two:

| Direction | Publisher identity | Exact-version receiver identity |
| --- | --- | --- |
| normal FI writer to IR standby | `fi-publisher` | `ir-receiver` |
| promoted IR writer to FI standby | `ir-publisher` | `fi-receiver` |

Each publisher is limited to create-only publication and bounded read-back in
its own direction prefix; each receiver is limited to exact-version `Get` /
`Head` in that direction prefix.  No role gets delete, overwrite, a wildcard
cross-direction scope, or a reason to use another role's credential.  The
official Arvan IAM frontend exposes the Machine User plus HMAC-credential path
for this separation.  The currently available ECC API credential does not
authorize that IAM endpoint, so no shared S3 credential will be substituted.

The new Witness round-trip control carriage is a separate authority surface,
not a reuse of those four recovery-data identities.  It has eight fixed local
mailbox roles across four one-way prefixes: FI source outbox, Witness FI
ingress, Witness IR egress, IR inbox, IR durable-ack outbox, Witness IR
ingress, Witness FI egress, and FI acknowledgement inbox.  Each future
mailbox identity must be independently deployment-attested and constrained to
either create-only plus exact self-readback, or fixed-prefix listing plus
exact-version read.  It must not be widened to a paired or cross-direction
credential.

## Open live gates

1. Seal the reviewed immutable release, exact image set, and operator-facing
   manifest. No current release artifact is accepted as evidence merely
   because it exists locally or in a historical note.
2. Establish the private versioned Object-Storage namespace through the
   reviewed four-role path: fresh provider readback must prove the exact
   bucket/prefixes, owner-only private access, versioning, retention/Object
   Lock behavior, and an empty scoped namespace. Create and bind the four
   directional recovery-data identities and the separate fixed mailbox
   identities; collect least-privilege IAM/immutability proof in both
   directions. No shared credential is an acceptable substitute.
3. Recollect current four-host preflight evidence: independent provider
   identity/address readback, pinned-host-key verification, and the reviewed
   delivery/attestation path for Bot-FI, WA-FI, WA-IR, and Witness. A
   bootstrap artifact is not a WA-IR host receipt, and no FI-to-IR SSH/SCP
   route may be introduced for collection or repair.
4. Install and attest the actual root-owned local services only after those
   inputs exist: the trusted clock/checkpoint, signer boundary, Witness
   transport/anchor, receipt journal, continuity gate, readiness resolver,
   and eight exact phase adapters. The local V4 composition and
   materialization preflight do not constitute any of these installed
   services.
5. Provision the digest-pinned PostgreSQL helper/substrates, control roles,
   migrations, and guarded application-DML wiring on the designated hosts.
   The disposable PostgreSQL proof does not authorize use of a project,
   staging, or production database, and no live writer transaction envelope
   is wired today.
6. Publish a fresh encrypted WA-IR bootstrap only through its private
   versioned Object-Storage route and obtain an exact-version receiver pull
   receipt. Do not reuse a historical bootstrap artifact, private identity,
   or host evidence.
7. Obtain fresh Witness term/fence evidence, then run the physical
   base/WAL/recovery, exact-version/readback, and Blob-frontier coverage
   checks in both directions. These are live observations, not projections of
   the local Gen2 capability.
8. Complete the explicit approval/go-no-go process and only then consider a
   separately controlled campaign invocation. The destructive Full Matrix
   remains a final operation after every preceding live gate is observed;
   `Full Matrix: not started` remains in force until then.

## Architecture repair in progress

The host image currently does not provide a PostgreSQL 15 `pg_basebackup`
binary.  Installing an unrelated host package is not an acceptable repair.
The local deployment renderer now reserves dedicated shared Unix-socket
substrates, binds both PostgreSQL roles only to those sockets
(`listen_addresses = ''`), rejects host TCP port publication, and renders a
strict WA-IR HBA that permits only local `postgres` peer maintenance while
rejecting every other local/replication/TCP identity.  This is a substrate for a future
digest-pinned PostgreSQL 15 helper image to perform local capture; it is not
an installed helper and creates no cross-host connection or unpinned package
source.

WA-IR bootstrap/recovery material may be obtained only through its
independently pulled, exact-version, encrypted private Object-Storage route.
That artifact route is separate from dedicated-host preflight: the central
controller accepts WA-IR host evidence only through independently
dual-signed Witness evidence, never through FI-to-IR SSH, SCP, or an
Object-Storage artifact standing in for a host receipt.  A live bucket
capability/immutability probe and a fresh bootstrap publication are still
required before recovery material can be trusted.

The physical Full-Matrix readiness oracle remains non-authorizing even when
all of its local evidence slots are observed.

## Approval-path repair

The active v2 human-approval issuer receipt was checked through a
metadata-only hardened-path preflight and is ready.  The earlier
`SecureFileError` came from callers using the incomplete legacy default
directory.  Future approval invocations must explicitly bind the v2 issuer
directory; no receipt copy or symlink is an acceptable repair.

## Current V2 implementation checkpoint

`Full Matrix: still not started.` The V2 work is deliberately progressing as
a separate protocol generation; no V2 evidence is cast into a V1 readiness
slot.

- The receiver's V2 durable replay-ack ledger revalidates the signed V2
  recovery bridge at every use, cross-pins route, term, object-set, and
  readback facts, uses a host-owned anti-rollback clock, and persists only
  through descriptor-anchored atomic writes. Its focused test module passes
  20 tests.
- The four-role Arvan immutability path has a Witness-signed fixed stage
  chain and a root-only one-role local agent. Its focused protocol/agent tests
  pass 12 tests, including expiry-after-reservation, short-retention-floor,
  signer-missing, replay, and no-peer-transport cases. Those tests make no
  provider request and create no bucket, Machine User, or immutable object.
- The V2 strict-writer boundary, V2-only readiness, and V2-only execution
  driver remain required. The historical readiness and driver intentionally
  require V1 evidence and add the fixed V2 integration fence; they must not
  be weakened or used as a migration shortcut.
- The V2 strict-writer contract now requires an atomically persisted local
  response plus one-time receipt consumption, and rechecks the live Witness
  term and activation after that transaction.  It deliberately cannot consume
  WA-IR's process-local durable-ledger capability on WA-FI.  The required
  successor is a separately signed, one-time Witness-mediated durable-ACK
  bridge; inventing a direct FI-to-IR control path or serializing the opaque
  local capability would violate the architecture and remains prohibited.
- The bridge is a full bounded Witness round-trip, not an ACK-only relay:
  first WA-IR exports a locally revalidated recovery assertion to Witness,
  Witness certifies the canonical V2 context for WA-FI, WA-FI emits its
  source request only through Witness, WA-IR makes and exports its locally
  durable assertion through Witness, and only then may WA-FI verify a
  one-time Witness attestation before its own atomic response/consumption.
  This ordering is required because the original V2 context carries
  WA-IR-local recovery evidence; WA-FI must never copy that opaque capability
  just to construct a request.
- The bridge-bound Gen2 strict-writer generation is complete as a distinct
  local receipt/table/protocol path. It binds an opaque V1 transaction-commit
  parent through a short-lived signed bridge intent and never treats the
  historical Gen1 receipt as a runtime fallback. The cross-generation
  attestation-consumption registry claims `attestation_sha256` in the same
  transaction, so Gen1 and Gen2 cannot consume the same Witness attestation
  concurrently. This remains local schema/protocol evidence only: it has not
  been migrated to a live database or used to authorize a writer.
- The portable V2 wire contract and the separate root-owned Witness roundtrip
  ledger bind an exact context
  certificate or final attestation to the Witness sequence, immutable ledger
  entry, prior ledger head, and fixed ledger binding.  They have no network,
  provider, or peer-transport API.
- A distinct four-hop delivery runtime now exists for
  FI→Witness, Witness→IR, IR→Witness, and Witness→FI.  It is root-owned,
  default-off, fixed-role only, and durable-reservation based; it rechecks
  canonical signed bytes before and after every adapter callback, fails closed
  on post-callback expiry or clock rollback, and never returns an expired
  cached carrier.  Its focused delivery/runtime suite passed locally.  This is
  still not a provider adapter or a live IAM proof.
- The eight-mailbox admission grammar now binds a deployment-authority-signed
  host assertion to exactly one host, local role, fixed prefix, direction,
  delivery binding, and least-privilege action set.  A fixed-role S3 mailbox
  adapter now requires that typed admission, a fresh signed
  Object-Lock/retention proof, root-only no-follow credentials, create-only
  readback for publishers, and fixed-prefix exact-version reads for scanners.
  The separate root-owned Arvan S3v4 scopes now exist as eight named-only
  callbacks.  Before the SDK is loaded, and again for each callback, they
  require a fresh signed provider-route/IAM attestation that cross-pins the
  exact HTTPS endpoint, bucket, region, role, prefix, admission, retention
  proof, and allowed action set.  No such live IAM/immutability proof has
  been collected yet, so no live operation is permitted.
- The concrete eight-role delivery dispatcher is role-local as well: every
  named opener accepts only its own runtime/scope/admission configuration and
  cannot enumerate peer roots or peer credentials.  A short-lived portable
  full-bundle attestation contains public projections only, is signed by the
  deployment authority already pinned by the local admission, and cross-pins
  the exact release together with the shared deployment binding.  It is
  revalidated before every publish or consume, before a runtime or S3 callback
  can start.  The focused dispatcher suite and its admission/mailbox/scope/
  runtime combination passed 30 tests; this is still provider-free test
  evidence, not live IAM evidence.
- A separate nonsecret, default-off deployment-plan renderer now creates three
  canonical local artifacts: WA-FI has only its source-outbox and final-ACK
  inbox services, WA-IR only its standby inbox and durable-ACK outbox, and
  Witness its four ingress/egress services.  Credential *paths* are fixed
  below the owning site's root; no credential material, peer root, endpoint,
  or selector appears.  Before any future local installer may consume one,
  a root-pinned local admission checks the exact artifact SHA-256, plan,
  release, signed-bundle public pins, and named site.  The combined deployment
  plan/dispatcher tests passed 10 tests; it is neither an installer nor live
  deployment evidence.
- Full-bundle issuance is now an equally narrow, default-off prepare/finalize
  boundary.  It accepts exactly eight named public role projections, validates
  the common release/deployment/delivery/round-trip pins before the root-owned
  signer is invoked, and verifies the returned signature against the
  root-pinned public authority.  It exposes no private-key accessor,
  credential, route, peer root, installer, or transport.  A newly issued
  canonical bundle was exercised directly through the real FI publisher and
  Witness consumer dispatchers; signer substitution, signature tampering,
  deployment-binding or release substitution, expiry-after-open, a validly
  signed but cross-pinned route assertion, and a canonical raw manifest whose
  outer release conflicts with the signed-derived reference all fail before
  runtime or S3 effects.  The focused issuer/dispatcher/bridge/deployment/
  adversarial run passed 27 tests locally; this remains provider-free
  evidence, not live IAM or deployment evidence.  Older signed bundle wires
  without the exact release field are intentionally rejected and must be
  reissued with their references and manifests.
- V4 is the isolated execution generation.  Its phase driver is
  transition-safe: initial FI readiness is valid only before the witnessed
  promotion; after a writer transition, only fresh successor readiness for
  the active writer may gate a later phase.  It consumes only the exact opaque
  Gen2 witnessed campaign-readiness capability; historical Gen1 readiness is
  rejected at the V4 type boundary and has no adapter or fallback route.  The
  concrete V4 receipt journal
  writes each effect-start and completion first to a required external Witness
  anchor, rejects local rollback/pending external state, and permits a
  process restart to rehydrate only a new non-authorizing plan from a fresh
  typed continuity projection and its exact baseline pins.  The focused
  V4/V2 driver, journal, restart, Witnessed-ACK, and readiness suite passed
  41 tests locally before the signed-Witness boundary was integrated.  The
  live host/provider adapters remain separate gates; no V4 phase has run.
- The V4 Witness-anchor wire contract is now a separate pure canonical
  Ed25519 boundary: controller append requests, Witness heads, and a signed
  nonzero genesis carry the same run, plan, journal-binding, and baseline-plan
  pins.  The journal computes its anchor commitment only from that full
  canonical wire commitment (including its newline, phase label, and genesis
  facts); no local projection digest is accepted as an alternative.  The
  focused post-migration journal/driver/rehydration run passed 36 tests and
  the initial independent wire suite passed 6 tests.
- During adapter integration, a real restart-liveness flaw was caught before
  any release: a single expiring Witness head signature had been asked to
  serve both as the immutable append record retained by the journal and as a
  fresh read proof.  The repair is now implemented locally as two distinct
  signed facts: an immutable append record retained by the journal and a
  short-lived read observation bound to that exact record and to a fresh
  root-local 64-hex controller challenge.  The adapter accepts only its exact
  durable tail or one direct successor for the crash window; arbitrary remote
  gaps, stale heads, replayed observations, lookalike policy identities, and
  old one-layer entry points fail closed.  The root-owned ledger persists the
  immutable record before the derived pointer, refuses a pending crash retry,
  and can rebuild only that derived pointer without re-signing.  The broad
  V4 driver/journal/integration/Wire/adapter/ledger/non-operational-runner
  suite now passes 77 tests locally.  A subsequent temporal audit caught a blocked readiness resolver
  aging evidence after its pre-callback clock sample; the repair now samples
  the trusted clock after every injected journal, continuity, resolver, or
  phase callback and carries that newer monotonic floor into the next decision.
  Regressions cover delayed resolvers before both effect-start and adapter
  execution, plus callback-induced clock regression from continuity, resolver,
  and durable receipt reads.  This is software candidate evidence only, not a
  deployed Witness transport, host action, provider proof, or Full-Matrix
  execution authorization.
- The new V4 runner is explicitly non-operational: it can only build or
  rehydrate typed process-local plans and check adapter interfaces.  It has no
  network, SSH, Object-Storage, Docker, or phase-execution path.  There is no
  installed root-owned composition, live Witness anchor transport, or
  installer/service unit yet.  A local root-gated composition foundation now
  requires exactly the eight named adapters plus journal, resolver, clock,
  continuity gate, and exact campaign/release/policy/plan pins; it invokes no
  callback or phase and its focused tests pass 8 locally.  A controlled V4
  campaign must not copy its controller key or local receipt journal between
  FI and IR. The separate local V4 materialization preflight now requires
  that exact composition, the same eight identity-pinned adapter bindings, a
  narrow Witness-anchor interface, and fresh exact Gen2 readiness. It samples
  only the composition-pinned trusted clock to revalidate readiness, then
  returns an opaque all-false diagnostic result. It neither installs adapters
  nor calls a phase, anchor, host, provider, or transport seam; its local
  completion is not an installed composition or installer/service unit.
  The operational FI-self-fence / Witness-authorized IR-promotion path is a
  separate Witness-fenced runtime requirement; if WA-IR cannot reach the
  independent Witness through its private relay, it must fail closed rather
  than promote on a partition.
- A V4-only WA-FI↔Witness anchor-mailbox foundation now has a typed,
  root-local durable anti-replay registry on both local roles.  It admits only
  the fixed WA-FI request/Witness ingress and Witness response/WA-FI inbox
  callbacks, exact V4 policy/role/prefix/IAM/Object-Lock digests, and a fresh
  immutable-head plus challenge-bound observation.  It durably reserves the
  challenge and append replay ID before publication/service, and reserves the
  verified observation before publication/return; an ambiguity burns the
  value rather than allowing a retry.  The combined mailbox/registry/V4 suite
  passes 101 local tests.  This remains non-live: deployment must still supply
  the root-owned monotonic checkpoint and the create-only, exact-version,
  Object-Lock provider callbacks; neither an Object-Storage object nor this
  registry authorizes a phase or a writer.
- A separate operational failover V1 evidence grammar, Witness term ledger,
  and local writer-admission boundary pass 35 focused local tests.  Their FI
  self-fence receipt, IR promotion request, Witness grant, and IR completion
  are domain-separated, canonical, default-off evidence; every verified
  result explicitly carries `promotion_authorized = false`,
  `writer_authorized = false`, and `traffic_authorized = false`.  The ledger
  moves through `FI active → FI fenced/expired → IR grant pending → IR grant
  issued → IR active`, with no active writer before verified completion and
  exact CAS/readback, clock-floor, request/receipt/grant/completion bindings.
  A root-local append-only CAS store now implements the ledger persistence
  seam with fixed root, exact config binding, atomic readback, and mandatory
  external monotonic checkpoint; the ledger/CAS suite passes 14 local tests.
  A separate current-term attestation bridge now projects only the narrow
  evidence accepted by writer admission, after reservation-before-fetch,
  signature/config/request/ledger-head/time verification, and durable
  consumption.  It uses a key role separate from the promotion signer and has
  no process-local replay fallback; the bridge/ledger/admission suite passes
  23 local tests.  A root-local durable reservation/consumption guard now
  implements the replay seam with fixed role binding, append-only/fsync state,
  and an external monotonic checkpoint; the guard/revalidator suite passes 11
  local tests.  Its role-local fetcher and trusted clock are still injected
  seams, not installed services.
  On the Witness side, a root-gated issuer now reads exactly one active ledger
  snapshot and signs the existing current-term grammar only after matching the
  exact request, reservation, head, state, and term; its key is disjoint from
  every V1 evidence key.  The issuer/revalidator/ledger/CAS suite passes 25
  local tests.  A role-local authenticated transport and an HSM/KMS or
  root signer-daemon boundary are still missing.
  The writer-admission state itself now has a fixed-root atomic CAS/restore
  foundation that checks exact revision, fence generation, and state digest,
  and forces fresh Witness revalidation after restart; its focused suite
  passes 9 local tests.  It intentionally does not couple that local CAS to a
  database transaction or external effect.
  A separate PostgreSQL-only admission foundation now supplies an append-only
  receipt chain, a single CAS head, canonical state/receipt/commit digests,
  and a caller-owned `AsyncSession` boundary.  It requires an already-active
  PostgreSQL transaction with no pending ORM mutation, takes an advisory lock
  plus `SELECT ... FOR UPDATE`, flushes the immutable receipt first, then
  advances the exact head conditionally.  The migration rejects append/delete
  mutation and high-impact epoch/fence/clock rollback shapes.  This adapter
  accepts `transaction_commit` only: arbitrary external effects are
  deliberately excluded because they cannot be made atomic by this database
  transaction.  The local V1/schema/adapter/migration-smoke suite passes 44
  tests.  Its opt-in harness then exercised the migration and PL/pgSQL
  trigger path on one disposable loopback-only PostgreSQL 15 scratch database:
  bootstrap, successor CAS, replay rejection, append-only rejection, and the
  downgrade refusal all passed.  The temporary container had no project
  volume and was removed after verification.  No project/staging/production
  database, control role, or application write path has been exercised; the
  next required proof is root-owned control-role provisioning and real guarded
  application-DML wiring in the same transaction.
  A separate default-off V1 transaction envelope now obtains its fresh
  Witness/relay admission *before* opening PostgreSQL, rechecks that the
  caller-local session remained untouched, then opens exactly one short root
  transaction for the V1 CAS/receipt and guarded business DML.  Its narrow
  facade exposes neither commit/rollback nor the writer-admission control
  rows, and rejects a manual terminal action rather than concealing it with a
  second rollback.  This is local code/test evidence only; it is not wired to
  API, Bot, worker, or application settings.
  The V2 Witness-roundtrip strict response now also has one child PostgreSQL
  migration (`0v2strictdb01`) with a single append-only row that *locally*
  records a proposed V1 transaction-commit link beside the local response and
  one-time attestation consumption.  It has unique commit/attestation/
  consumption/local-response identities, a V1 site/epoch/lease/hash trigger
  check, and a downgrade refusal once evidence exists.  This database-only
  scalar link is intentionally not treated as proof: the currently signed V2
  receipt does not yet bind the V1 parent or a V1-to-V2 bridge, so it cannot
  authorize a runtime path.  Its static suites and a separate
  disposable PostgreSQL-15 loopback test passed for valid insert, replay,
  append-only mutation, V1-link mismatch, and downgrade refusal; no project,
  staging, or production database was touched and that temporary container
  was removed.  The V2 boundary is deliberately split into pure
  `prepare → transaction → finalize`: the instruction is in-process only,
  and `finalize` freshly rechecks term/activation before an observation can be
  released.  Scalar epoch/lease equality alone is not accepted as a bridge.
  A separate default-off V1-to-V2 pre-transaction intent-certificate contract
  now verifies a dedicated bridge signer, all V1/V2 key-role separation,
  exact term/provenance/configuration/activation pins, bounded certificate
  windows, and a deterministic post-persist parent-binding digest.  Its
  verified and bound handles are non-serializable. An immutable Gen2
  table/migration retains the full V1 parent projection, certificate
  bytes/digests, and binding digest without altering the Gen1 table. The
  signed Gen2 receipt/observation, reviewed SQL transaction adapter, and
  default-off one-transaction envelope are now present as local boundaries;
  the envelope's disposable loopback-only PostgreSQL proof did not use any
  project, staging, production, or host database. The required generation
  split and transaction order are fixed in
  [`PHYSICAL_V1_V2_WRITER_BRIDGE_CONTRACT.md`](PHYSICAL_V1_V2_WRITER_BRIDGE_CONTRACT.md):
  a short-lived Witness-signed *intent* is obtained before PostgreSQL, while
  the final V1 parent identity is bound only by the locally signed Gen2 receipt
  in the one short transaction.  No parent-bearing remote signature is sought
  after persistence.
  It is still a non-live foundation: a provisioned checkpoint outside that
  tree, root/HSM signer binding, pinned transport, installed database control
  role, and transactionally coupled DB/traffic writer enforcement remain
  required. A signed receipt alone is not a physical FI fence.
- The emergency reverse carrier is a separate V2R grammar, not a reuse of
  normal V2.  Its wire/replay/admission/profile tests cover the only permitted route
  `WA-IR → Witness → WA-FI → Witness → WA-IR`, four distinct signer domains,
  keys, prefixes, IAM pins, exact nested ACK correlation, eight distinct
  role identities, and rejection of a direct route or any Object-Storage
  authority flag.  It is deliberately evidence-only and default-off.  Its
  four receiver-local roles now also have a root-local durable
  replay-reservation foundation, with mandatory external monotonic checkpoint,
  role/stage/config binding, and restart/rollback/symlink-residue adversarial
  coverage.  It still needs a real root-owned checkpoint and integration into
  each receiver callback, plus role-local Object-Storage adapters, real IAM
  installation, and runtime deployment before it can carry recovery evidence.
- The selective release inventory now has a separate literal V4 execution
  boundary allow-list for the reviewed driver, journal, rehydrator, Wire,
  narrow adapter, root-owned ledger, root composition, and materialization
  preflight modules.  The reviewed preflight selection also includes the
  receipt-agent installer and the Witness-evidence dispatch/collection
  boundaries required to install and observe the staged read-only endpoint.
  It statically rejects a direct core dependency that is
  outside the reviewed candidate or belongs to a retired data-plane runtime.
  Its focused suite passed 7 tests. This is review material only; it does not
  seal, materialize, or deploy a release.

The live controller inputs are still absent: no *installed* root-owned V4
composition service, eight real phase adapters, Witness transport,
installer/service identity,
controller config, manifest, transport config, pinned `known_hosts`, or Arvan
ECC readback config has been installed. A fresh SSH check also detected a
changed WA-FI host key and a timed-out WA-IR SSH endpoint. Neither condition
is accepted automatically; fresh independent provider/console verification
and new root-owned pins are required before any host action.
