# Release-0 / Three-Site Operational Handoff

Status: planning and admission contract only.  This document does not
authorize a host change, Object Storage publication, DNS/CDN change, service
start, or Writer Witness transition.

## Scope boundary

The temporary Emergency IR Standalone is a separate, isolated application. It
must not become an input to, or be overlaid onto, the durable three-site
release.  Keep its images, volumes, root-only configuration, Nginx site,
receipts, and Object Storage campaign namespace separate until a separately
reviewed retirement procedure exists.

The source package at Release-0 commit
`72174c1f17b787f4d6d382adedad85b74096d67e` contains useful safety work:

- application-side Writer Witness checks before API/bot startup effects;
- term checks at request, ORM/Core-DML, raw-SQL, SMS, push, and Telegram
  egress boundaries;
- an isolated FI app+bot Compose scope and signed image/checkout preflight;
- retirement fences for historical direct FI--IR transport and two-server
  deployment/full-matrix entrypoints.

It is **not** a production-cutover release by itself.  In particular, its
present FI and WA-IR operational control path is deliberately pinned to the
historical `2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5` application identity.
`preflight_fenced_fi_writer.py` rejects that identity after verification, so a
successful host preflight cannot be used to start the old image.  Do not
weaken that rejection or retag a rebuilt image as `2c08`.

## Blocking facts to resolve in source before host activation

1. Create a new immutable application candidate, distinct from `2c08`, whose
   app and bot images are built from one clean, signed Git SHA.  Preserve its
   source tree SHA, Docker repository digest, Docker image ID, Compose bytes,
   frontend/static manifest, and image archive hashes.
2. Replace every fixed `2c08` admission binding only through a new signed
   release identity and a new control release.  The FI preflight, FI lease
   agent, runtime receipt, environment template, service unit, and Compose
   path must bind the same new application SHA; a loose directory name, tag,
   or environment value is insufficient.
3. Make the WA-IR promoted application use the same term contract before it
   can be called a writer: `SINGLE_WRITER_RUNTIME_ENABLED=true`,
   `APPLICATION_WRITER_TERM_ENFORCED=true`, a read-only root-owned term mount,
   `APPLICATION_WRITER_TERM_LOCAL_SITE=webapp_ir`, bounded timing values, and
   `DATABASE_SCHEMA_BOOTSTRAP_ENABLED=false`.  The current historical
   WA-IR promotion Compose file does not supply this contract and sets
   background jobs true; it is therefore not a valid Release-0 promoted
   writer runtime.
4. Bind WA-IR application/control provenance, snapshot receipt, static files,
   promoted listener, and route proof to that same new candidate.  Do not
   mix a new app image with a `2c08` provenance receipt or snapshot.
5. Select one coherent three-site control lineage.  The current `main`
   three-site implementation and Release-0 share only the historical base;
   wholesale merging is not a safe operational shortcut.  Each imported path
   needs an explicit reviewed digest/provenance decision and focused tests.

## Ordered operational plan after source completion

### A. Freeze and attest the candidate (no host mutation)

1. Start from a clean, reviewed candidate worktree and record its Git commit
   and tree IDs.
2. Run the focused Writer-term, FI release-identity, FI preflight, lease-agent,
   legacy-transport retirement, and WA-IR promotion tests.  Run static
   imports/compile checks for the exact control scripts.
3. Build the app and bot images from that exact source only.  Record both
   immutable repository digests and local image IDs; do not accept a mutable
   tag as evidence.
4. Produce the signed FI release identity, root-owned expected descriptor
   digest, root-owned runtime environment digest, and a deterministic
   app/bot image archive manifest.  The identity remains non-authorizing.
5. Prepare the pinned Writer Witness package and verify the 60/15/10 client
   timing against the exact root-only FI and IR agent configurations.  This
   package must be installed and independently attested before any product
   writer can obtain a term.

### B. Stage bytes through the data plane

1. Use only the private, versioned, client-side encrypted Object Storage
   campaign path for application/control release bytes, images, role material,
   snapshots, and static assets.  SSH may carry only bounded control commands
   and non-secret status JSON.
2. Before publication, validate bucket privacy/versioning, per-object
   ciphertext/plaintext hashes, recipient binding, object version IDs, and
   campaign expiry.  Preserve every receipt; do not overwrite or delete a
   previous candidate.
3. On FI and WA-IR, install into fresh immutable release roots and perform
   local readback/hash/provenance checks.  Keep the application stopped and
   public routing unchanged.

### C. Establish the dark, data-ready standby

1. Keep WA-IR dark: no public application listener, no Nginx route change,
   no bot, no direct sync worker, no schema bootstrap, and no writer lease.
2. Restore each verified FI snapshot into a new generation-qualified WA-IR
   candidate volume.  Retain the previous good candidate and stop on an
   incomplete restore journal rather than reusing a partial volume.
3. Record schema revision, source snapshot start time, release/source hashes,
   object version IDs, disk reserve, UTC/NTP evidence, and parity/consistency
   results.  A delivery/backlog health result is not parity proof.
4. Refresh only while no active WA-IR writer term exists.  A live term fences
   snapshot replacement.

### D. Read-only activation admission

1. Verify the Witness runtime, client timing, host clock bounds, and explicit
   no-live-predecessor state.
2. Verify FI legacy writer containers are stopped with restart disabled and
   that no legacy deployment process or direct FI--IR transport remains.
3. Run the FI identity preflight in `cutover-pre`; it must bind the signed
   candidate identity, clean Git roots, Compose bytes, runtime environment,
   external resources, images, unit bytes, and absent fenced containers.
4. Verify the WA-IR active snapshot, provenance/control root, local term
   mount, promoted app/bot (if applicable) image IDs, dark listener, TLS,
   and a fresh bounded promotion proof.
5. Abort on any mismatch or stale timing evidence.  A read-only `ready`
   result is necessary but never routing authorization.

### E. Controlled writer and route transition

1. Use the root-owned fenced FI cutover controller only after the preceding
   admission passes.  It must acquire/renew the exact Witness term immediately
   before app+bot start, validate both health checks, emit the runtime receipt,
   and pass guard-start preflight before its systemd renewal loop begins.
2. Keep public routing unchanged until the live guard/unit, term, container
   identities, and readiness receipt are all verified.
3. For an Iran promotion, allow acquisition only after the old FI term has
   expired according to Witness time, a current WA-IR candidate is verified,
   the promoted runtime is healthy under the mounted term, and the route
   proof is fresh.  Never use a manual force takeover.
4. Apply an Arvan route change only as the final, separately approved action,
   with read-before-write, post-write readback, exact expected prior origin,
   rollback evidence, and an audit record.

### F. Matrix and failure rehearsal

1. Do not run the retired `scripts/run_production_full_matrix.py` or the
   historical two-server plan: Release-0 deliberately makes them return a
   blocked legacy-two-server status.
2. First run a new three-site preflight/matrix against the selected immutable
   control lineage.  It must prove the current writer term, dark standby,
   encrypted object path, parity/delta freshness, route gating, and recovery
   receipts.
3. Run destructive/fault scenarios only in the isolated campaign namespace
   and one failure class at a time: FI--IR partition, FI--Witness loss,
   WA-IR--FI loss, delayed/duplicate object delivery, expired approval,
   stale snapshot, clock skew, app/bot crash, and rejected dual writer.
4. Keep a stable evidence interval before any production-domain routing
   declaration.  Full-matrix evidence is a later validation gate, not a
   substitute for the primary/standby admission proof.

## Non-negotiable stop conditions

- Any `2c08` application identity, retagged image, or mixed control/app
  provenance appears in a new candidate.
- A payload transfer would use SCP, rsync, SFTP, direct FI--IR HTTP sync, or
  SSH-embedded data instead of the approved Object Storage campaign.
- A writer term is absent, stale, held by a different site, longer than the
  signed bound, or too close to expiry.
- A process can initialize schema, background jobs, bot polling, or external
  delivery before proving its mounted term.
- WA-IR has an active public listener or route before a current promotion
  proof and a term-bound healthy runtime exist.
- Sync delivery is healthy but deep parity/delta evidence is absent or stale.
