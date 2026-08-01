# Phase-2 FI fence scope and installation provenance

## Purpose

`physical_full_matrix_v4_retired_fi_predecessor_fence` verifies the three
signed P2 retirement receipts, but the six evidence fields inside those
receipts are digest pins. By themselves, six well-formed hashes do not prove
that the intended physical FI fence covered every write path.

`physical_full_matrix_v4_fi_fence_scope_installation_provenance` is the
default-off, pure evidence boundary that closes that semantic gap before P2 is
configured. It verifies separate executor and independent-observer scope
policies and installation attestations, then projects only the six existing
`RetiredFiPredecessorFenceEvidencePins` fields for P2.

## Mandatory fixed coverage

The signed scope-policy grammar accepts exactly this coverage model, with no
generic policy hash or caller-selected list:

| Surface | Required policy outcome |
| --- | --- |
| app, bot, sync, and migration writers | block write entrypoints |
| database server side | revoke server-side write authority and drain existing write sessions |
| activation/deploy | fence systemd service, socket, timer, path, restart, and deploy units |
| provider paths | revoke writer-capable credentials and egress |

This is a policy requirement, not a claim that the operation already
succeeded. A missing or renamed item, a generic `policy_sha256`, an unsigned
change, or an executor receipt relabelled as observer evidence is rejected.

## Evidence flow

```text
executor signed scope + installation ─┐
                                      ├─ pure verifier ─> opaque provenance
observer signed scope + installation ─┘                         │
                                                                  └─ six P2 evidence pins
                                                                     (+ future executor/observer
                                                                        post-fence evidence hashes)
```

The executor and observer use distinct Ed25519 keys, role-specific signing
domains, scope-policy hashes, and installation-attestation hashes. The
verifier binds each artifact to the exact P2 effect start, immutable start
anchor, and former FI writer term; installation attestations are short-lived.

## Non-authority boundary

This module has no executor, host, PostgreSQL, systemd, provider, network,
socket, process, or Object Storage implementation. Its result and every
signed grammar carry `writer_authorized`, `promotion_authorized`,
`external_effect_authorized`, `installation_authorized`,
`execution_authorized`, and `full_matrix_authorized` as `false`.

The two future post-fence evidence hashes are shape-checked only and are
passed into P2 as correlation pins. P2's independent signed receipt verifier
must still bind them, and a future physical executor/observer must still
perform and attest the actual fence. Therefore this module is not a
Full-Matrix start condition and cannot authorize a live operation.
