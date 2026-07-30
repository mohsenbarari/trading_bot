# Writer Witness 60-Second Release

This is a separately staged immutable Witness release. It is not part of the
WebApp-FI or WebApp-IR application release and it does not activate, restart,
or change any host by itself.

The source is pinned to `be6067c0c61a8eabea3241448c8adc32257ce631`. It adds
opt-in server enforcement which rejects `acquire` and `renew` requests whose
duration differs from the configured Witness duration. The profile fixes that
duration to 60 seconds, with a 10-second renew interval and 15-second safety
margin.

Before a later activation transaction, attest the root-only WebApp-FI lease
agent configuration without printing any secret:

```bash
python3 scripts/prepare_writer_witness_immutable_release.py verify-client-timing \
  --webapp-fi-agent-config /etc/trading-bot-three-site/production-writer-lease-agent.json
```

An old 180-second WebApp-FI client is incompatible. It must be changed and
verified before the Witness starts enforcing 60 seconds, otherwise its next
renewal is rejected and the FI writer loses its lease. WebApp-IR is pinned by
its promotion agent to 60/10/15 too, but its installed root-only configuration
must be checked before a real failover drill.

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
