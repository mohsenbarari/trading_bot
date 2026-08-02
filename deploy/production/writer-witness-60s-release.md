# Writer Witness 60-Second Release

This is a separately staged immutable Witness release. It is not part of the
WebApp-FI or WebApp-IR application release and it does not activate, restart,
or change any host by itself.

The source is pinned to `a0d8fa5a3b696ecfee3c0e787ea0791d035b1f32` and its
exact built release-manifest hash is
`e99de3ec1de9179791754d4cadcbd85ea581e3ac118c620404baf91efc9dcbc0`.
It adds a release-bound runtime profile, server enforcement which rejects
`acquire` and `renew` requests whose duration differs from 60 seconds, and a
signed configuration-attestation endpoint. Its Ed25519 response binds the
authenticated WebApp site and a hash of its HMAC key id, so an FI proof cannot
be relabelled as IR proof. The profile fixes the lease to 60 seconds, with a
10-second renew interval and 15-second safety margin.

Before a later activation transaction, attest both root-only WebApp lease agent
configurations without printing any secret:

```bash
python3 scripts/prepare_writer_witness_immutable_release.py verify-paired-client-timing \
  --webapp-fi-agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-fi-fenced-2c08.json \
  --webapp-ir-agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-ir.json
```

An old 180-second client is incompatible. Both clients must be changed and
verified before the Witness starts enforcing 60 seconds, otherwise a renewal
is rejected and that writer loses its lease.

After the new Witness release is running, but before either writer activation,
each WebApp produces its own non-secret live receipt:

```bash
python3 scripts/attest_writer_witness_client.py \
  --agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-fi-fenced-2c08.json \
  --output /var/lib/trading-bot-three-site/witness-attestations/webapp-fi.json

python3 scripts/attest_writer_witness_client.py \
  --agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-ir.json \
  --output /var/lib/trading-bot-three-site/witness-attestations/webapp-ir.json
```

Only these non-secret receipts may be carried to the control gate. Each
contains hashes rather than the Witness URL or HMAC secret; it includes the
signed Witness response, the local CA-bundle hash, and the non-secret pinned
Witness public key.

The root-controlled control host keeps credential-rotation state below
`/etc/trading-bot-three-site/writer-witness-credential-rotation-v1`. Its parent
and every state directory must already be canonical, non-symlink `root:root`
directories with mode `0700`. A rotation creates, without replacement:

* a versioned immutable `0400` policy in `policies/`;
* a matching immutable selector candidate in `selectors/`;
* a hash-linked immutable activation record in `activations/`; and
* an atomically replaced `current-selector.json` pointer.

The pointer is accepted only when it exactly matches the latest immutable
activation ledger record. A rollback to an older pointer, selector tampering,
or policy replacement therefore blocks verification. Old policies are retained
for audit and are never deleted by these helpers.

```bash
python3 scripts/create_writer_witness_credential_rotation_policy.py \
  --webapp-fi-attestation /var/lib/trading-bot-three-site/witness-attestations/webapp-fi.json \
  --webapp-ir-attestation /var/lib/trading-bot-three-site/witness-attestations/webapp-ir.json \
  --policy-id witness-current-YYYYMMDD-gN \
  --webapp-fi-generation fi-gN \
  --webapp-ir-generation ir-gN \
  --not-after "$APPROVED_NOT_AFTER"
```

Set `APPROVED_NOT_AFTER` to a real, review-approved RFC3339 expiry no more than
24 hours after issuance. The release profile enforces that maximum policy TTL,
in addition to the 60-second receipt freshness rule. The command
re-verifies both receipts before creating any lifecycle record, derives only
public hashes, and emits neither an HMAC secret nor a Witness URL. It creates
and activates a new versioned policy; reusing an existing policy id fails
without changing the old policy or selector.

For a crash after the atomic pointer switch but before its immutable activation
record, the next root-controlled rotation operation can only finalize the exact
selector-derived record. A normal verifier does not repair that state; it fails
closed until the lifecycle operation completes it.

The pair gate resolves the current selector, its exact immutable policy, the
fixed release profile, and its real local UTC clock. None of those are CLI
options:

```bash
python3 scripts/verify_writer_witness_paired_attestation.py \
  --webapp-fi-attestation /var/lib/trading-bot-three-site/witness-attestations/webapp-fi.json \
  --webapp-ir-attestation /var/lib/trading-bot-three-site/witness-attestations/webapp-ir.json \
  --output /var/lib/trading-bot-three-site/witness-attestations/paired.json
```

The pair verifier rechecks the Ed25519 signature, request nonce, freshness,
TLS/CA binding, client-pinned public key, exact runtime-profile hash, and exact
source release-manifest hash. It also requires the signed caller key-id hash to
equal the current FI or IR policy entry, checks the policy validity window and
the profile TTL ceiling, and pins every receipt's endpoint/CA/Witness key hash
to the same immutable policy. It is a hard pre-activation gate: a missing,
stale, mismatched, previous-key, expired-key, rolled-back selector, tampered
policy, or unsigned receipt is not an advisory warning.

Receipt transport is local-contract-only before the external transfer. On each
source host, seal the already non-secret receipt into a create-only,
content-addressed envelope:

```bash
python3 scripts/manage_writer_witness_attestation_transport.py seal \
  --attestation /var/lib/trading-bot-three-site/witness-attestations/webapp-fi.json \
  --destination-directory /var/lib/trading-bot-three-site/witness-attestation-transport
```

The external Object Storage transaction is an explicit remaining gate: upload
those exact envelope bytes only to the emitted object key with private,
versioned, create-only semantics and exact-VersionId read-back. After that
external transaction, bind its returned VersionId locally without storing a
presigned URL:

```bash
python3 scripts/manage_writer_witness_attestation_transport.py bind-published-version \
  --envelope /var/lib/trading-bot-three-site/witness-attestation-transport/ENVELOPE.json \
  --object-version-id EXACT_VERSION_ID \
  --destination-directory /var/lib/trading-bot-three-site/witness-attestation-publish
```

The VersionId is treated as an opaque non-URL value. The controller receives
only the exact downloaded envelope and its URL-free publish receipt, verifies
the key, VersionId, bytes, hashes, and source site, then imports the embedded
receipt create-only. This helper makes no Object Storage call and does not
authorize an external upload by itself.

This Witness gate is narrower than the fenced FI application preflight. It
does not replace the signed Release-0 identity, control-release bytes, Compose
bytes, image repository digests, or post-health runtime receipt. Those checks
must also pass through `preflight_fenced_fi_writer.py --phase cutover-pre`
before a future controlled FI cutover can acquire a Writer Witness term.

Prepare the transferable local package without contacting a host or Object
Storage. The destination parent must already be a canonical, non-symlink
`root:root` directory with mode `0700`; the destination itself must not exist.

```bash
python3 scripts/prepare_writer_witness_immutable_release.py prepare \
  --source-repository /root/trading-bot/trading_bot \
  --destination /root/secure-envs/trading-bot/witness-release-package
```

The package contains a deterministic source-tree archive, the profile, the
non-secret runtime template, and a manifest with their hashes. A later,
separately authorized transaction must stage it through the private/versioned
Object Storage path, attest the detached source, build the host release with
the approved offline wheelhouse, verify the release manifest, then use the
reversible Witness activation procedure. It does not copy legacy key, issuer,
state, or TLS material. If preparation fails after creating its destination,
the helper intentionally preserves partial artifacts for forensic review; do
not reuse that directory for another attempt. The source worktree and its
resolved Git directories, including a normal worktree `.git` pointer target,
must be root-owned and not writable by group or other users.
