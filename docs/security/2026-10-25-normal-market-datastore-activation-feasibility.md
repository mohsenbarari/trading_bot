# Normal Market and datastore activation feasibility

This is a source-only proposal. No serving adapter, activation control or new
execution authority is implemented. Image-store receipts do not establish serving
readiness; security acceptance and the full matrix remain false.

The published `canonical_app_activation_*` packet supplies useful primitives:
typed complete comparisons in RAM, held public sources and opaque references,
permanent mutex metadata, bounded commands, durable journals, stopped-first
creation, exact-CID start, and known-failure versus unknown-outcome handling.
Its App entrypoints, five-role policy and receipt validators cannot be invoked
unchanged for Market or datastores.

## Minimum source adaptation

- Bind a separate scope **per role**: project, workdir, complete ordered Compose
  prefix, profiles, CLI environment references and all normal overrides. The
  ten Market roles use different historical release prefixes; one prefix per
  host would silently omit active inputs. Authenticate actual public execution
  labels and source metadata before trusted normal rendering in RAM.
- Preserve the ten-role map in `market_candidate_images.json`. Native's normal
  chain is fact receiver, store adapter, coin estimator and snapshot sender.
  Web has snapshot receiver, processor, fact sync and three capture roles. A
  fixed receiver-first handoff order requires separate review; dependency
  services and migrations must never be started implicitly.
- Model Market's actual `profiles`, `secrets`, `tmpfs`, resource limits and
  market PostgreSQL `shm_size`. The App contract currently rejects these
  features and requires App-specific `TRADING_BOT_SERVICE` and container names.
  Account for them explicitly rather than deleting fields for comparison.
- Bind secret-file aliases and actual mounts without reading their payloads.
  Preserve both exact existing F79 public source overrides and all session,
  state and owner-lock references. Changing the diagnostic source is separate
  authority. Model Market executable paths and deny unapproved source masks.
- Use each admitted image's exact Config and provenance. The App contract's
  fixed665 labels, Python-default delta and static execution defaults are not
  a Market or vendor-image contract. Preserve complete business Env, HostConfig,
  mounts and network policy in RAM; do not hash or serialize them.
- Reuse the stopped-first transaction mechanics with narrow role backends and
  pure contracts. Add no general lease, resume, retry or rollout framework and
  invoke no historical C/S/R/COW or fresh-profile entrypoint.

## Required evidence before any serving request

1. Actual admitted LOAD receipts and fresh full Config/RootFS measurements for
   every selected image on its target host. PostgreSQL and Redis span three
   PostgreSQL roles and two Redis roles, not merely two image identities.
2. Fresh normal backups and actual isolated restore/persistence tests for the
   affected PostgreSQL, Redis and Market durable data. The failed first App
   backup remains failed evidence; a new attempt must supply its own receipt.
3. Data-format/version and rollback compatibility. Starting PostgreSQL or Redis
   can write existing PGDATA/AOF before readiness. App-style recreation of an
   old image is not sufficient proof of safe datastore rollback. Unknown
   outcomes retain guards; never restore production data automatically.
4. Exact per-role normal-render versus actual Config/HostConfig/mount parity
   before stop, followed by complete stopped-candidate parity before start.
   Preserve all other containers, eight reader units, legacy-owner fences,
   Writer and DNS policy. Prove old cgroups empty and no competing owner.
5. Normal role readiness and persistence/transport checks on the exact started
   CID, with PID/start/restart and release binding. Use existing authenticated
   datastore clients and normal Market mTLS, owner and progress contracts.
   The image-store-only H↔U observation policy grants no serving-health waiver;
   capture and receiver-health incidents remain open.

Actual receipts, fresh normal prefix observations and compatibility outcomes
must fill these gates. Missing values remain unarmed; no future IDs, successful
restores or readiness results are inferred from source tests.
