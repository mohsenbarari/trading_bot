# Physical PostgreSQL recovery preflight contract

`core.physical_postgres_recovery_preflight` is a pure, default-off admission
check that runs only after a receiver has staged a physical Object-Storage
bundle. It does not open the stage path or import the receiver implementation.
The caller supplies a typed projection containing only the stage receipt hash,
opaque bundle ID and route-binding hash.

## Inputs that must agree exactly

The preflight revalidates all of the following without performing I/O:

- the opaque, signature-verified physical base/WAL/blob bundle;
- an opaque, signature-verified Witness term whose holder is the bundle
  source, and whose epoch, lease and proof hash equal the signed bundle term;
- the local standby site and typed stage projection; and
- bounded canonical ASCII receiver readback evidence.

The readback must bind the exact ordered manifest hashes and immutable object
key/version pairs from the bundle, the base manifest hash, terminal WAL LSN,
stage receipt hash, bundle ID and route hash. It must also report the same
source/destination, system identifier, timeline, base generation and the
fixed 16 MiB WAL geometry.

Both ordered routes are supported: `webapp_fi → webapp_ir` and
`webapp_ir → webapp_fi`. In either direction, the bundle destination must be
the caller's local standby site.

## Result states

Only these states are emitted:

- `staged-not-replay-verified` — an explicitly conservative staging report;
  it is not a replay assertion.
- `replay-evidence-observed` — the receiver reported `in_recovery=true`, role
  `standby`, and a replay LSN at or beyond the exact bundle terminal frontier.
- `blocked` — malformed, stale, noncanonical, tampered, foreign, promoted,
  under-replayed, or otherwise mismatched evidence.

`replay-evidence-observed` is an observation, not a restore, startup,
promotion, strict-acknowledgement, or writer-authority permit. A separate
root-controlled transition must independently recheck live Witness authority,
fence the former writer, and perform any state-changing operation.
