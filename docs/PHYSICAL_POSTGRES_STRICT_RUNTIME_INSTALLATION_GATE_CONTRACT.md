# Strict durable-replay runtime installation gate

`core.physical_postgres_strict_runtime_installation_gate` is a default-off,
local-only prerequisite for a future reviewed strict durable-replay runtime.
It does **not** change the deliberately fail-closed behavior of
`core.physical_postgres_deployment_scaffold`: that scaffold still refuses to
render `strict_zero_loss` because no reviewed Object-Storage durable-replay
runtime/coordinator exists yet.

The gate never opens Docker, PostgreSQL, a shell, SSH, a peer connection,
Object Storage, a credential, or a deployment path.  It cannot start a
service, write a record, request strict rendering, change traffic, fence a
writer, promote WA-IR, or run Full Matrix.  Its result is named an installation
observation and always carries both `not_a_launch_authorization: true` and
`strict_rendering_still_refused_by_scaffold: true`.

## Exact strict-manifest binding

`build_physical_postgres_strict_runtime_installation_request` accepts only an
already validated `strict_zero_loss` physical PostgreSQL manifest.  It binds
one immutable request to the manifest lock, campaign/release, normal
`webapp_fi -> webapp_ir` Object-Storage pull-only route, Writer term, strict
remote-durable-replay identity, and writer-admission integration identity.

Four components are mandatory and their set is fixed:

| Component | Required existing contract identity |
| --- | --- |
| `wa_fi_local_wal_archive_capture` | WA-FI root-owned PostgreSQL archive-command runtime |
| `encrypted_private_versioned_object_storage_publish_receipt` | encrypted exact-version remote-ack Object-Storage transport |
| `witness_locator_ledger` | root-owned Witness locator replay/order ledger |
| `writer_response_commit_boundary` | strict remote-ack-to-local durable writer response boundary |

For every component, the request pins a non-secret component ID plus contract,
implementation, configuration, and expected canonical-attestation SHA-256.
The installation binding excludes the expected attestation hashes so each
attestation can bind it without a self-referential hash cycle.  The full
request still includes those expected file hashes.

## Root-owned attestation file contract

The only path accepted for component `<component>` is:

```text
/etc/trading-bot/physical-postgres/strict-runtime/<component>/installation-attestation.json
```

A future injected local inspector must report a single-link regular file with
root owner, exact mode `0600`, root-controlled ancestors, its raw payload, and
the payload SHA-256.  The gate itself performs no filesystem reads.  It
rejects missing inspector output, a different path, non-root ownership,
non-regular/symlinked evidence, unsafe ancestors, and any other mode.

The file must be canonical ASCII JSON plus exactly one trailing newline, with
no duplicate fields or extra fields.  Its exact schema is
`gold-trade-physical-postgres-strict-runtime-installation-attestation-v1` and
it must contain only these non-secret facts:

- immutable component/contract/implementation/configuration hashes;
- the installation binding, manifest lock, campaign/release, route, Writer
  term, strict identity, and writer-integration hashes;
- root attestation and expiry timestamps;
- explicit `direct_fi_to_ir_*: false` flags and
  `not_a_launch_authorization: true`.

There is no endpoint, bucket, recipient, credential, token, password, private
key, raw locator, or payload field in this format.  Exact fields and hash
comparisons prevent a component from substituting another component's
attestation or a different manifest/term/configuration.

## Freshness and fail-closed behavior

The config is disabled by default and requires a root runtime before the
injected inspector is called.  It accepts a bounded attestation age (default
five minutes; maximum fifteen minutes), a canonical UTC attestation timestamp,
and a bounded expiry no later than that same allowed age.  Future, stale,
expired, malformed, hash-mismatched, mode-mismatched, missing, or
binding-mismatched evidence fails closed.

The opaque verification result also expires at the earliest of its four
attestation expiries.  Rechecking it with a different request, a forged data
class, a relabelled authorization field, or after expiry fails closed.

This only establishes that four expected local installation attestations were
observed.  It is not evidence that the binaries/services work, that Object
Storage is immutable, that WA-IR replayed a write, that the live Witness term
is current, or that any physical Full-Matrix gate is ready.  Those independent
live proofs remain required.
