# Physical WAL remote-ack Witness locator ledger

`core.physical_wal_remote_ack_witness_locator_ledger` is a default-disabled,
root-only local replay and ordering gate for the Witness-signed exact Object
locators defined by `physical_wal_remote_ack_object_storage_transport`.

It becomes effectful only when a separately installed root-owned runtime calls
`admit_request_locator` or `admit_receipt_locator` with an enabled config and
an existing root-owned `0700` state root. This change installs neither a
runtime nor a config, and does not invoke Object Storage, age, PostgreSQL,
Docker, SSH, the Witness, a peer, or deployment tooling.

## Admission boundary

The public admission functions reject raw locator bytes and ordinary locator
wrappers. They accept only the opaque verified request/receipt locator
capabilities minted by the transport module, then re-check their canonical
signature, freshness, exact pinned Witness public key, exact binding, and
anti-tamper capability before opening ledger state.

The config is pinned to exactly one normal direction:

```text
webapp_fi -> webapp_ir
writer_term.writer_holder_site = webapp_fi
```

Its configuration hash includes the fully normalized remote-ack binding,
including Writer epoch/lease/Witness-term hash and all immutable frontier
pins, the Witness public-key hash, and capacity. Reusing a state directory
with a changed route, term, key, or capacity fails closed.

## Durable replay and ordering rules

The root-owned `0700` state directory contains an atomic, canonical `0600`
ledger and a `0600` advisory lock. The logical ledger is append-only: entry
sequence is contiguous and each later state atomically retains every earlier
entry. It stores only:

- locator ID, nonce, and SHA-256;
- hashes of the request/receipt exact Object pins;
- the route/term binding hash and canonical times.

It never stores or returns raw signed locators, Object keys, bucket details,
age recipients, credentials, or exception text.

Each locator ID, nonce, digest, request Object pin, and receipt Object pin has
one durable interpretation. An exact retry returns the same redacted result
with `idempotent=true` and appends nothing; any altered reuse fails. A receipt
locator must reference the exact request Object pin of an earlier admitted
request locator. Receipt-before-request, a mismatched request pin, or another
receipt for the same request fails without state mutation.

## Scope limit

An admission result is a local replay-gate observation only. It is not a
Witness delivery, Object download, encrypted transport action, receiver
receipt verification, PostgreSQL replay proof, strict remote acknowledgement,
writer permit, route switch, promotion authorization, or Full-Matrix result.
A future effectful transport adapter must perform its own explicit consuming
step and must not treat an idempotent retry as a new transport action.
