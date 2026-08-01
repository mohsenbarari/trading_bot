# Witness ledger for WA-IR preflight attestation

`core.dedicated_host_preflight_witness_attestation_ledger` is the local,
default-off Witness persistence boundary for one fixed WA-IR preflight
request. It is not an HTTP server, SSH handler, Object-Storage adapter, proxy,
Writer Witness term, promotion coordinator, or Full-Matrix runner.

## Injected ingress only

The sole transport-facing interface is
`WitnessPreflightAttestationIngress.accept_wa_ir_attestation`. A future
network service may inject received bytes into that method only after its own
separate authentication and rate-limit boundary. This module neither chooses
nor creates that transport. It accepts no URL, command, host, credential,
bucket, or caller-supplied receipt selector.

On every admission it independently verifies the fresh WA-IR envelope against
the one configured root-pinned request and WA-IR public key. It then emits a
separate domain-separated Witness Ed25519 evidence envelope. The evidence
retains the entire canonical WA-IR envelope so a controller can verify both
signatures rather than trusting a Witness assertion alone.

## Durable append-only state

The only state root is:

```text
/var/lib/trading-bot/dedicated-host-preflight/witness-wa-ir-attestation-ledger
```

It must already be a root-owned non-symlink `0700` directory below safe
root-owned ancestors. The ledger may create only its fixed root-owned `0600`
lock and state files. It uses no-follow opens, exclusive `flock`, canonical
state parsing, entry hash chaining, temporary no-follow writes, atomic rename,
and file/directory `fsync`.

An entry binds the WA-IR envelope hash, fixed attestation UUID and nonce, the
accepted timestamp, exact Witness evidence, and predecessor entry hash.
The first acceptance is persisted before it is returned. Any repetition of
the envelope hash, attestation ID, or nonce is a replay and is refused, even
if the bytes are identical. Corrupt, modified, reordered, unsigned, or
different-request state prevents both admission and retrieval.

`collect_pinned_evidence` exposes only the one evidence record matching the
configured request's fixed ID and nonce; it has no historical selector. It
does not claim freshness itself. The later controller verifier must still
perform both signature and current-expiry checks.

All persisted and emitted evidence states, explicitly and immutably:

```text
writer_authorized: false
promotion_authorized: false
execution_authorized: false
```

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -m unittest -q \
  tests.test_dedicated_host_preflight_witness_attestation_ledger
```
