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
authenticated WebApp site and a hash of its HMAC key-id, so an FI proof cannot
be relabelled as an IR proof. The profile fixes the lease to 60 seconds, with a
10-second renew interval and 15-second safety margin.

Before a later activation transaction, attest both root-only WebApp lease
agent configurations without printing any secret:

```bash
python3 scripts/prepare_writer_witness_immutable_release.py verify-paired-client-timing \
  --webapp-fi-agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-fi-fenced-2c08.json \
  --webapp-ir-agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-ir.json
```

An old 180-second client is incompatible. Both clients must be changed and
verified before the Witness starts enforcing 60 seconds, otherwise a renewal
is rejected and that writer loses its lease.

After the new Witness release is running, but before either writer activation,
each WebApp must independently run the live read-only attestation with its own
local HMAC secret and CA bundle:

```bash
python3 scripts/attest_writer_witness_client.py \
  --agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-fi-fenced-2c08.json \
  --output /var/lib/trading-bot-three-site/witness-attestations/webapp-fi.json

python3 scripts/attest_writer_witness_client.py \
  --agent-config /etc/trading-bot-three-site/production-writer-lease-agent.webapp-ir.json \
  --output /var/lib/trading-bot-three-site/witness-attestations/webapp-ir.json
```

Only these non-secret receipts may be carried to the control gate. Each receipt
contains hashes rather than the Witness URL or HMAC secret; it includes the
signed Witness response, the local CA-bundle hash, and the non-secret pinned
Witness public key. The control gate must reject activation unless both are
fresh and verify together:

```bash
python3 scripts/verify_writer_witness_paired_attestation.py \
  --webapp-fi-attestation /var/lib/trading-bot-three-site/witness-attestations/webapp-fi.json \
  --webapp-ir-attestation /var/lib/trading-bot-three-site/witness-attestations/webapp-ir.json \
  --maximum-age-seconds 60 \
  --output /var/lib/trading-bot-three-site/witness-attestations/paired.json
```

The pair verifier rechecks the Ed25519 signature, request nonce, freshness,
TLS/CA binding, client-pinned public key, exact runtime-profile hash, and exact
source release-manifest hash. It is a hard pre-activation gate: a missing,
stale, mismatched, or unsigned receipt is not an advisory warning.

This Witness gate is intentionally narrower than the fenced FI application
preflight. It does not replace the signed Release-0 identity, control-release
bytes, Compose bytes, image repository digests, or post-health runtime receipt.
Those checks must also pass through `preflight_fenced_fi_writer.py --phase
cutover-pre` before a future controlled FI cutover can acquire a Writer Witness
term.

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
the helper intentionally preserves the partial artifacts for forensic review;
do not reuse that directory for another attempt. The source worktree and its
resolved Git directories, including a normal worktree `.git` pointer target,
must be root-owned and not writable by group or other users.
