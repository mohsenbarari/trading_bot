# Physical WAL remote acknowledgement evidence contract

`core.physical_wal_remote_ack` is a pure, default-off evidence contract for a
future Object-Storage pull-plane acknowledgement. It creates and verifies a
canonical Ed25519-signed source request plus an Ed25519-signed destination
receipt. It contains no endpoint, credential, Object Storage, network, `age`,
shell, PostgreSQL, container, SSH, restore, recovery, route-switch, or
promotion implementation.

Each signed artifact carries the same exact binding: distinct FI/IR direction,
destination recipient, campaign, release, stream, baseline generation and
baseline manifest, Writer term holder/epoch/lease/proof projection, requested
acknowledged WAL LSN, complete blob frontier, complete manifest-hash set, and exact immutable
`(object_key, version_id)` set. Both FI → IR and IR → FI are valid only when
the expected keys and complete binding match. Mutable aliases, duplicate
entries, incomplete blobs, and a blob frontier behind the requested WAL LSN
are rejected.

The receipt also binds the canonical source-request SHA-256, request ID and
nonce, a distinct receipt ID and nonce, and fresh canonical UTC timestamps.
The verifier takes caller-provided consumed-ID/nonce sets and a minimum prior
acknowledged WAL LSN, rejecting stale/future evidence, replay, and frontier
regression. Those sets are inputs only: this module does not persist them.

`verify_physical_wal_remote_ack_request` is the destination-side preliminary
boundary: it checks the source signature, exact expected binding, freshness,
and caller-provided consumed request IDs/nonces before a runtime considers
local replay state or signs a receipt.  Its opaque request capability is not a
receipt and cannot itself acknowledge, replay, commit, or promote anything.

A successful verifier result is an opaque evidence capability, not proof that
the target database committed/replayed the WAL and not a Writer, commit,
promotion, or route-change permit. The runtime still needs a durable replay
ledger, a real pull transport, destination database verification, former
writer fencing, and a live Witness decision before any operational action.
