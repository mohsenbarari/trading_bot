# Three-site human approval: passphrase + TOTP

## Decision and scope

The former two-laptop/two-human-signature ceremony is superseded. Human
authorization for three-site staging and DR operations now uses one fixed
passphrase plus one standards-compatible six-digit TOTP from Google
Authenticator, Aegis, 2FAS, or another RFC 6238 application.

This changes only the human authorization ceremony. It does not remove or
weaken machine identities, TLS keys, Witness proofs, Object Storage hashes,
release signatures, hash-chained journals, fencing, rollback rules, or
production isolation.

The issuer lives on the trusted Witness/controller. The TOTP seed, passphrase
verifier, recovery-code digests, and passphrase-encrypted issuer private key
never leave its root-owned directory. Other hosts receive only the public
policy and either an exact-action approval or a release-bound staging session.

## Security properties

- A passphrase alone or a TOTP alone cannot issue a token.
- The Ed25519 issuer key is encrypted at rest with AES-256-GCM using a separate
  bounded-scrypt key derived from the passphrase; a copied issuer directory
  does not expose a plaintext signing key.
- A TOTP counter is accepted once; replay of the same code is rejected.
- Five failures start a persistent exponential lockout, capped at one hour.
- Ten recovery codes are generated during enrollment. Each still requires the
  passphrase and is accepted only once.
- Exact-action tokens remain signed and bound to one action, environment,
  release SHA, artifact hash, policy hash, and action-specific bindings.
- Routine staging preparation and Full Matrix work should use one signed
  operations session. It is bound to one exact release SHA, staging only, an
  explicit action allowlist, and a maximum lifetime of 48 hours. It cannot
  authorize production or survive a source-code SHA change.
- Full Matrix, Gate D, Writer/Witness, promotion, and failback tokens have a
  ten-minute start window. Inventory and migration have longer bounded windows
  because their immutable approvals are consumed by multi-step preparation.
- A long-running operation may continue or recover from its hash-chained
  journal after the start window expires; an expired token cannot start a new
  operation.
- The issuer appends to an fsync'd, owner-only, hash-chained audit log and
  revalidates the complete chain before every issuance.
- There is no generic break-glass action. A staging session can authorize only
  actions already present in the public policy.

TOTP is not phishing-resistant. Never enter the passphrase or code into chat,
Git, shell history, a web form, or a host other than the trusted issuer.

## Prerequisites

1. Run enrollment on the trusted Witness/controller as root from a real TTY.
2. Keep that host on UTC with working NTP. The tool fails closed unless
   `timedatectl` reports `NTPSynchronized=yes`.
3. Install the exact reviewed release containing the issuer scripts. Do not
   copy secrets into the repository or an Object Storage object.
4. Choose a unique passphrase of at least 16 characters. It is stored only as
   a memory-hard scrypt verifier.
5. Have the authenticator phone available and prepare an offline location for
   recovery codes. At least one recovery code should be reachable during
   travel without depending on a second laptop.
6. Create the issuer parent once as root; enrollment will not create or follow
   a missing or writable parent directory:

   ```bash
   sudo install -d -o root -g root -m 0700 /etc/trading-bot/security
   ```

## One-time enrollment

Enrollment is interactive and will not overwrite an existing issuer:

```bash
sudo python3 scripts/manage_three_site_human_approval.py enroll \
  --operator mohsen
```

The tool requires the exact legacy-supersession phrase, asks twice for the
passphrase, prints the manual TOTP setup key and `otpauth://` URI once, verifies
one current code, prints ten recovery codes once, and requires explicit
confirmation that those codes were stored.

The resulting directory is:

```text
/etc/trading-bot/security/human-approval/
  issuer-secrets.json          secret, Witness only, 0600
  issuer-ed25519.key.enc.json  passphrase-encrypted secret, Witness only, 0600
  issuer-state.json            replay/lockout state, Witness only, 0600
  approval-audit.jsonl         hash-chained audit, Witness only, 0600
  bootstrap-receipt.json       supersession receipt, 0600
  human-approval-policy.json   public verifier policy, 0600
```

Only `human-approval-policy.json` may be distributed. Publish it through the
private, versioned Object Storage control path, pin its VersionId and SHA-256,
download it on each consumer, verify both, and install it at that consumer's
pinned policy path with root ownership and mode `0600`. The Writer/Witness
controller provisioner installs its attested copy at
`/etc/trading-bot-witness-matrix/human-approval-policy.json`; the general DR
consumer path is `/etc/trading-bot/security/human-approval/human-approval-policy.json`.
Never upload the other five files.

## Controlled activation order

1. Commit and review the exact release first. Installing this source does not
   authorize or execute an operation.
2. Enroll only on the Witness from its trusted TTY; do not run enrollment
   through chat, CI, `tmux` logging, or a copied shell transcript.
3. Record the public policy SHA-256 and distribute only that policy through the
   private, versioned Object Storage control path.
4. Attest the pinned VersionId, SHA-256, root ownership, and mode `0600` at
   every consumer before enabling a new gate. A mixed old/new policy state is
   blocked, not tolerated.
5. Create one release-bound staging session for routine inventory, migration,
   Full Matrix, Gate D, Writer/Witness and staging failover/failback work.
   Exact-action approvals remain available when a narrower grant is preferred.
   Legacy approval files are historical evidence only.
6. Archive the old public policies and signature files away from live canonical
   paths only after all consumers prove the new policy hash. Do not delete
   historical evidence or active operation journals.

This activation changes no DNS/CDN route, writer lease, production process, or
database by itself. Those mutations remain behind their existing independent
confirmation, fencing, evidence, and rollback controls.

## Recommended: authorize one 48-hour staging work session

After selecting the exact reviewed release SHA, issue one session:

```bash
sudo python3 scripts/manage_three_site_human_approval.py issue-session \
  --release-sha <40-or-64-character-release-sha> \
  --ttl-seconds 172800 \
  --output /var/lib/trading-bot/human-approvals/staging-session-<unique-id>.json
```

The command shows the release, staging-only boundary, complete action allowlist
and expiry; asks once for the fixed passphrase and current TOTP; then writes one
owner-only token. The same file may be supplied wherever that release asks for
an inventory, migration, Full Matrix, Gate D, Writer/Witness, staging promote,
or staging failback approval. Verifiers still record their exact action subject
in their own journals.

The session stops working when it expires, when the expected release SHA
differs, or when a consumer asks for production. A new code commit therefore
requires one new session; ordinary artifacts generated by the same release do
not require another authenticator prompt.

To restrict the session further, pass an explicit allowlist:

```bash
sudo python3 scripts/manage_three_site_human_approval.py issue-session \
  --release-sha <release-sha> \
  --actions approve_inventory approve_migration start_full_matrix \
  --ttl-seconds 172800 \
  --output /var/lib/trading-bot/human-approvals/staging-session-<unique-id>.json
```

Production cutover is intentionally outside this session model and requires a
separate, exact-action approval.

## Optional: issue one exact approval

First build or obtain the exact subject. Inventory, migration, and failover
subjects can be produced by the validated builder:

```bash
python3 scripts/build_three_site_human_approval_subject.py inventory \
  --artifact /root/secure/inventory.json \
  --output /root/secure/approval-subject.json
```

Full Matrix and Gate D builders already emit their exact subject as
`--approval-request-output`. The Writer/Witness `approve` mode emits a subject
through `--subject-output`.

Transfer the non-secret subject to the Witness using the private, versioned
Object Storage control path. Then issue a new token into a new file:

```bash
sudo python3 scripts/manage_three_site_human_approval.py issue \
  --action approve_inventory \
  --environment staging \
  --subject /root/secure/approval-subject.json \
  --ttl-seconds 600 \
  --output /var/lib/trading-bot/human-approvals/inventory-<unique-id>.json
```

The command displays the exact action, environment, subject, SHA-256, and TTL;
requires a typed confirmation containing the subject hash; then reads the
passphrase and TOTP without echo. Output is refused inside the issuer directory
or when the output path already exists.

Return the token through private, versioned Object Storage. A token contains no
passphrase, TOTP seed, current code, recovery code, or private key, but it is
still an authorization artifact and must remain owner-only.

To verify before use:

```bash
sudo python3 scripts/manage_three_site_human_approval.py verify \
  --policy /etc/trading-bot/security/human-approval/human-approval-policy.json \
  --token /root/secure/approval-token.json \
  --action approve_inventory \
  --environment staging \
  --subject /root/secure/approval-subject.json
```

Use `--historical` only for retained evidence or a proven idempotent journal
resume. It must never authorize a new operation.

## Recovery and loss scenarios

If the phone is temporarily unavailable, use one stored recovery code:

```bash
sudo python3 scripts/manage_three_site_human_approval.py issue \
  --action start_full_matrix \
  --environment staging \
  --subject /root/secure/full-matrix-subject.json \
  --output /var/lib/trading-bot/human-approvals/full-matrix-<unique-id>.json \
  --use-recovery-code
```

If the passphrase, all recovery codes, the TOTP seed, or the issuer private key
is lost, do not weaken verification and do not copy a secret from logs. Stop
new mutations, create a new enrollment in a separate directory during a
controlled rotation, distribute and attest the new public policy, atomically
switch all consumers, and archive the old issuer material offline. Existing
operations may only finish through their already-bound journal and old public
policy; old tokens cannot authorize a new-policy operation.

## Rotation and incident rules

- Rotate after suspected phone, passphrase, Witness, or recovery-code exposure.
- Never edit a policy, token, subject, issuer state, or audit line by hand.
- Never restore only `issuer-state.json`; rolling it back can weaken replay and
  recovery-code consumption tracking. Restore the issuer as one sealed unit or
  perform a new enrollment.
- Preserve the old public policy and audit with historical evidence, but remove
  it from every live canonical policy path after rotation.
- A policy change invalidates outstanding tokens by design. Finish or safely
  recover active journals before switching policy.
- Legacy two-device policy/signature documents are rejected by current
  verifiers. Retain them only as historical evidence; never place them back at
  a live policy path.

## Supported actions

| Action | Environments | Maximum token lifetime |
| --- | --- | ---: |
| `approve_inventory` | staging | 24 hours |
| `approve_migration` | staging | 4 hours |
| `start_full_matrix` | staging | 10 minutes |
| `approve_gate_d` | staging | 10 minutes |
| `run_writer_witness_matrix` | staging | 10 minutes |
| `promote_ir` | staging, production | 10 minutes |
| `failback_fi` | staging, production | 10 minutes |

Use the shortest practical TTL. The table is a hard ceiling, not a default.
The staging operations session may combine any staging entries in this table
for at most 48 hours; the individual ceilings apply only to exact-action
tokens.
