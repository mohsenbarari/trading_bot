# WebApp-FI Fenced Writer Guard Staging

The historical `production-writer-lease-guard-preflight-v1` path is retired.
It managed the mutable root Compose project (`app` plus `sync_worker`) and
could therefore reopen an unreviewed direct FI--IR transport.  Its legacy
unit/preflight now fail closed and are retained only for forensic inspection;
they must not be installed, enabled, or used for a cutover.

The only candidate WebApp-FI writer path is the separate
`fenced_fi_writer` project.  It is an admission gate, not a deployment
procedure: it never starts, stops, pulls, builds, recreates, or removes a
Docker resource, and it does not contact WebApp-IR, Object Storage, or a peer.
WA-IR remains dark in this FI staging path.

## Hard Application Release Gate

**Do not execute `cutover-fenced-fi` against the historical `2c08` app/bot
images.**  The checked `2c08` application does not implement the mounted
Writer Witness term contract, the bot readiness helper, or a schema-bootstrap
disable switch.  Both processes can call `init_db()` before a healthcheck can
fail, so a Compose-level environment variable is not a fence for startup DDL,
database writes, or bot polling.

This control package deliberately pins `2c08` only to make that mismatch
auditable; it is **not activation-ready**.  A future candidate must use a new
immutable application commit and image pair that validates the live mounted
term before *all* startup side effects, has a real schema-safe startup mode,
and exposes a term-bound bot readiness check.  It must then receive a new
release SHA, clean-tree claim, image provenance, Compose/preflight constants,
and signed release identity.  Rebuilding patched code under a `2c08` tag is
not acceptable.

## What the Fenced Gate Proves

Before `cutover-fenced-fi` can acquire a Writer Witness term, the root-only
v2 preflight binds all of the following to one signed Release-0 descriptor:

- a new application commit (the fixed legacy
  `2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5` is a hard refusal);
- clean Git checkouts whose application/control commit and tree IDs equal the
  signed identity, plus the exact fenced Compose bytes and canonical
  term-fenced source-evidence SHA-256;
- the preloaded app and bot repository digests and Docker image IDs, whose
  OCI revision/source-tree/evidence labels exactly equal that evidence;
- the app+bot-only Compose scope, fixed project/container names, no build or
  pull, `restart: "no"`, a loopback-only staged app port (`18001`, distinct
  from the legacy Nginx upstream on `8000`), and a read-only term mount;
- the reviewed SHA-256 of the complete private runtime environment plus its
  exact application root, external network, and writable upload/audit volumes;
- the rendered root-owned systemd unit and a fixed 60/15/10 Witness timing;
- the matching root-only lease-agent configuration.

The signed v2 descriptor, its authority key, and the root-only canonical
term-fenced evidence file are installation inputs, not
values inferred from Docker.  A tag, local image ID, source SHA, or a release
directory name alone is never sufficient.  A missing descriptor, wrong hash,
wrong Compose bytes, or missing repository digest blocks before a Writer
Witness term is acquired.

Build each candidate app/bot image from the reviewed application checkout with
the three `Dockerfile` build arguments `TERM_FENCED_RELEASE_SHA`,
`TERM_FENCED_RELEASE_TREE_SHA`, and
`TERM_FENCED_APPLICATION_EVIDENCE_SHA256`.  They set the required OCI
revision/source-tree/evidence labels.  Blank or substituted labels are not a
warning: preflight rejects the pinned image before a Witness term is acquired.
The release procedure must record the resulting image ID and repository digest
in the signed v2 descriptor; the Docker labels never replace those pins.

After controlled start, the guard-start phase additionally requires the
post-health runtime receipt, live local Witness lease, and inspected app/bot
container identities.  It does not authorize a second writer, a promotion, or
any direct FI--IR transfer.

## Inputs to Stage Outside Git

Use only these new fenced templates:

- `deploy/production/production-writer-lease-agent.webapp-fi-fenced-2c08.json.example`
- `deploy/production/webapp-fi-fenced-writer-2c08.env.example`
- `deploy/production/webapp-fi-writer-lease-guard-preflight.json.example`
- `deploy/systemd/trading-bot-production-writer-fi-fenced-lease-guard.service.template`

Copy them to root-owned, non-symlink files outside the repository and replace
every placeholder after a reviewed local inventory.  The runtime environment
contains secrets and stays root-only.  The v2 identity descriptor, authority
public key, expected descriptor SHA-256, and canonical term-fenced evidence
file are separate root-owned inputs.  A v1 identity remains audit-readable
but is rejected by `cutover-pre`.
Do not use the older generic FI unit or any `mode: "writer"` /
`site: "webapp_fi"` configuration: those define the retired guard and are
intentionally rejected.

After an immutable control release, local images, root-only inputs, and the
candidate unit are staged, run only the read-only pre-cutover check:

```bash
python3 /srv/trading-bot-three-site/control-releases/<control-release-sha>/scripts/preflight_fenced_fi_writer.py \
  --config /etc/trading-bot-three-site/webapp-fi-fenced-writer-preflight.json \
  --phase cutover-pre
```

`status: ready` is necessary but is not authorization to start the unit or
switch routing.  After the required future application release exists, a
separate controlled cutover must first prove the legacy scope stopped and the
fenced scope absent, obtain one Witness term, repeat static preflight, renew
that exact term immediately before Compose start, then verify its health
receipt and guard-start preflight before returning a non-routable
`status: staged` result.  Routing still requires a separate proof that the
guarded systemd unit is active; no result from `cutover-fenced-fi` authorizes
it. This FI writer staging path adds no
transport route and cannot replace the existing `127.0.0.1:8000` Nginx
upstream by binding its isolated `18001` listener. Direct sync endpoint retirement and the Object-Storage-only
release/snapshot data plane are separate mandatory Release-0 controls.

The fenced container names must be absent for `cutover-pre`. A stopped
container is not reused under a new Witness term; after any failed attempt,
perform a separately reviewed cleanup before retrying.
