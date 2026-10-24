# C07 closure and backup source validation

The old C07 transaction stopped before package installation. Its immutable plan,
journal, failed recovery records, and original deadline remain historical
evidence. Security acceptance is still false; automation remains stopped.

## Read-only normal backup source validator

Run `deploy/maintenance/web_backup_env_probe.yml` with the existing exact
`web_engine` inventory. This stages only the two SHA-pinned public backup scripts
under the new private `/root/.local/security-backup-env-probe-20261024-01`
directory, takes the original marker lock (bounded five-second wait), and takes
both existing source/reservation locks without waiting or adopting their owner.
The existing normal `validate_release_env` function alone consumes the protected
role Env in memory. The wrapper does not inspect its returned mapping, copy or
hash the Env, run Docker, create a backup, or change services. It binds the
original plan and Env metadata, and checks that metadata again afterward.

Output contains only `validation_passed`, a closed reason code, and explicit
operation flags. In particular `backup_source_release_sha_invalid` and
`backup_source_image_identity_invalid` distinguish the old combined error.
Other contract refusals are deliberately redacted. Exit zero means that the
probe completed; **only `validation_passed: true` means validation passed**.
An Ansible changed result means public scripts were first staged, never a
runtime change. The staged public source is reusable only with identical bytes
and safe ownership/modes. Retain it as incident evidence for ten days, then
remove only after the incident no longer needs it.

This is not HOT3, a restore test, or quiesced backup acceptance. The obsolete
CREATE02 operation must not run. A future real HOT backup needs its own current
runtime proof and the completed C07 closure receipt.

## Independent terminal closure of the aborted C07 transaction

`deploy/maintenance/web_c07_terminal_closure.yml` defaults to `check`. It has a
fresh ten-minute clock and never extends the old C07 or QA deadlines. The
immutable C07 plan, journal at `writers_quiesced`, original failed recovery
records, accepted capture06 receipt, and QA parking receipt are bound by their
reviewed identities. Dead owned units/scopes/processes and pending owned jobs
must be absent. Unrelated ordinary healthcheck ExecIDs and watchdog jobs do not
block this action because it does not quiesce or stop any datastore.

The checks take the permanent marker first with a bounded five-second wait,
then the exact same two original dead-owner fence inodes without waiting or
changing their payloads/PIDs. They observe three sample points at least thirty
seconds apart in total: all ten QA containers remain parked with no restart,
port 8443 stays closed, four volume backings/images/secret metadata remain
preserved, the retained runtime intent is restored, six real datastore probes
and the Web API pass, native timers keep their original intent, and collector
inhibitors remain effective. Capture06's accepted `6a16...`/`f79...` identity is
the one explicitly superseding the old capture identity. It must remain healthy
and the sole session owner. Session contents are never opened. Full private
container configurations stay only in bounded RAM and are compared without
printing, serializing, or hashing their values.

Known processor/receiver health-cadence oscillation remains an **open incident**.
Only their exact two bound IDs may change observed process state; their complete
configuration and identities remain strict. A stopped/restarting retained
process or a new unhealthy component refuses closure. No original-all-27-health
or security-completion claim is made. Runtime collector guards remain in place;
persistent boot guards are not installed by this task, and Web reboot is not
authorized by this receipt.

After final review, use `closure_action=close` and the exact confirmation
`CLOSE-ONLY-ABORTED-C07-OWNED-FENCES-20261024-01`. The new receipt is written and
fsynced in phase `aborted_before_install_fence_release_pending` before removing
anything. Only the exact source fence is unlinked first, then the exact global
reservation last, with inode/payload checks and parent-directory fsync. The
permanent marker, old journal/plan and historical receipts stay untouched. The
new receipt then becomes `aborted_before_install_fences_released`, retaining
`security_transition_completed: false` and known incidents `open`.

A repeated read-only check of a completed receipt verifies its historical
evidence and that both old fences remain absent; it explicitly does not claim a
fresh runtime check. A pending receipt, partially removed fences, or a foreign
new reservation refuses automatic adoption/retry and requires a separately
reviewed recovery. No deletion of data/images/volumes is part of closure. Keep
the original incident evidence; review only disposable public staging/logs for
cleanup after ten days. A subsequent HOT3 operation must bind the actual
completed closure receipt SHA and the current runtime; it cannot reuse the
obsolete CREATE02's ownership assumptions.

## Actual continuation result

The reviewed packet was committed as `7c9143c6` and the real finite closure
finished with systemd exit0 on24Oct. Its new receipt has phase
`aborted_before_install_fences_released`, owned-fence release true, global
security completion false and known Market cadence incidents open. Receipt
SHA-256 is `712af9788ef73e3934252d7b7e50bd0681bbfea13c25088da59a123efabb5dc0`.
This is the anchor for a newly reviewed HOT operation, not approval to reboot
or to reuse CREATE02. No service restart or old evidence/deadline rewrite was
part of closure. The first connection attempt failed before remote execution;
the verified explicit Web SSH identity was then used successfully.

The subsequent serialized normal validator completed without mutation and
explicitly returned `validation_passed: false`, reason
`backup_source_image_identity_invalid`. The earlier generic refusal is not
evidence of this exact cause; the new verdict is. No Env values were exposed,
copied or hashed by this probe, and no HOT backup was attempted.

An additional source-review blocker precedes any new real HOT action: the
ordinary archive implementation still calls `file_digest(env_file)` for its
`role_env_sha256` journal/receipt binding. The current no-Env-content-hashing
instruction forbids using that path unchanged. Prepare a reviewed non-content
identity binding and appropriate contract tests before actual CREATE/VERIFY;
do not silently weaken immutable image validation, rewrite the original plan,
or make a diagnostic copy of the protected Env to work around either blocker.

## Source-only successor binding contract

The archive tool now creates receipt `market_pipeline_backup_restore/1.3` and
journal `market_pipeline_backup_journal/1.1`. Their `role_env_binding` records
only canonical path, device, inode, owner/group, mode, link count, size and
nanosecond modification/change times. It also records the opened parent chain's
path/device/inode/owner/group/mode. No Env content digest is created, compared,
printed or copied. The normal grammar and source/image/lane validators still
consume the bounded Env in memory, directly from one retained file descriptor.
They do not reopen the Env by path.

Every parent must be canonical, owned by root or the effective user and not
writable by group/others. A safe `0755` parent is accepted; the Env itself must
remain effective-user owned, regular, mode `0600`, singly linked and at most
64 KiB. The file and directory descriptors stay open through the operation.
File metadata, parent identities and path-to-inode bindings are rechecked around
transaction work, before journal/receipt publication, and before successful
return. Parent modification/change times are deliberately excluded because
creating backup siblings changes them. Replacing a parent/file, changing modes,
adding a hard link, or rewriting the file even while restoring its old mtime
refuses the transaction. Existing source database, artifact digest, restore
inventory, immutable release/image and authority gates retain their contracts.

Current `create`/`verify` reject historical v1.2/v1.0 evidence with
`backup_role_env_binding_migration_required` before opening the protected Env.
They never upgrade, rewrite or resume an old journal as a metadata-bound one.
The separate `verify-historical` command requires explicit receipt and journal
SHA-256 pins plus the original release/image identity. It checks their old
stored Env digest agreement and the ordinary artifact/source/restore evidence
without any Env argument or read. Its result always includes
`historical_only: true`, `current_environment_verified: false` and
`eligible_for_new_deployment: false`. Historical `INITIAL_EMPTY` evidence makes
no present-day empty-store claim.

Historical control documents are each capped at 16 MiB, independently of the
PostgreSQL artifact size. Each document is hashed and parsed from the same
bounded buffer read through one retained no-follow descriptor. The containing
directory and both file descriptors stay open through validation and successful
return; parent/path/inode and complete file metadata, including ctime, are
rechecked. Rewriting and restoring public controls between separate hash and
JSON reads cannot produce a successful pinned-evidence claim.

Existing v1.2-only deploy/controller gates remain unchanged and reject v1.3.
Old public script pins and CREATE02 remain unchanged. A new HOT3 consumer and
operational packet require separate review and fresh source pins; this source
change authorizes no backup, renderer, service, fence or deadline operation.
The existing hot CREATE window allows row drift, while the ordinary VERIFY
contract still requires exact source/after/restore row inventories. A future
HOT3 acceptance contract must explicitly address that difference without
weakening database identity, schema, archive integrity or restore checks.

## Legitimate source image provisioning remains outstanding

The last actual normal source verdict remains
`backup_source_image_identity_invalid`. The metadata-binding change does not
repair that verdict or accept a tag where an immutable `sha256:` image identity
is required. Do not edit a diagnostic copy, substitute a candidate identity for
the running source, or infer the private Env's contents from this reason code.

The next provisioning design must use the authoritative managed topology
sources and their owner, then bind the actual source daemon's immutable image
ID, release SHA/tree and image-input provenance to that managed configuration.
The current retained runtime and its capture `6a16...`/`f79...` identity must be
proved afresh; target release identity remains a separate binding. Preserve the
observed coherent source lane and project/data roots. Use the established
Shadow or PRIMARY renderer appropriate to that authoritative lane only after
its contract has been reviewed; regeneration must not change product authority
or create another capture/session owner.

The existing Shadow and PRIMARY render/check/verify implementations also hash
source/output Env contents. Consequently neither is approved to run unchanged
under the current no-content-hashing instruction. First review a successor
managed rendering and verification contract with non-content file identities,
then provision atomically at the authoritative managed destination through its
normal owner. Existing authorization covers necessary reviewed security work;
it does not require another routine permission request. Retain the original protected Env and evidence; no ad hoc export,
copy, transformation or hash of it is part of this plan. Rerun only a freshly
pinned normal validator after that reviewed managed provisioning. A real
HOT3 still needs its own finite operation deadline, current runtime proof and the
completed closure SHA
`712af9788ef73e3934252d7b7e50bd0681bbfea13c25088da59a123efabb5dc0`.
An operation that actually stops production services is separate and must fit
a valid user-approved downtime window; a hot backup does not grant that window.

## Additive managed Web metadata renderer (source-only)

The following 1.0 description is historical. The actual03 attempt refused before
creating an Env or receipt; the distinct-identity 1.1 contract below supersedes
its assumption that configuration release and image OCI revision coincide.

The PRIMARY preparer now offers `render-web-metadata` and
`verify-web-metadata`. These are the managed single-Web-role successor to the
old hash-based derive/render path. Legacy `derive-source`, pair rendering and
pair verification stay unchanged and remain prohibited under the current
no-Env-content-hashing instruction. No implementation here has been executed
against the protected Env or provisioned on a host.

The new commands accept the original rendered Env, a fresh managed Web Env and
receipt path, plus public **actual source** image ID/release SHA/tree/input
signature and a caller-verified source evidence SHA. There are no deployment
target image/release, Bot output, research-key override or backfill override
arguments. The normal parser reads the original only through its retained
`BoundRoleEnv` descriptor. Topology derivation removes dynamic keys in RAM;
normal `validate_source('web', ...)`, `_rendered` and Env encoding produce the
managed output. No source/output Env or encoded payload is hashed.

The original must already have a coherent live PRIMARY lane, a valid project
name and the same release SHA as the caller's actual source baseline. Rendering
must change exactly `MARKET_PIPELINE_IMAGE`; even an already-matching image
refuses as a no-op. Every other setting, including all non-dynamic values,
project, release, PRIMARY triple, data/publication roots, research/secret path
references and backfill settings, remains equal in memory. No referenced secret
or session file is opened. The original remains untouched and retained.

Both destinations must be distinct from the source and each other, under one
pre-existing owner-only managed parent. The tool creates or chmods no directory.
Outputs are fresh-only: partial files, symlinks, hard links and foreign or
concurrent publications cannot be adopted or overwritten. The established
atomic writer's metadata-only path publishes by exclusive hard link after file
fsync, removes its temporary name and fsyncs the directory. The output's full
metadata binding and parsed settings are checked before the completed receipt
is published. Retained source/output descriptors are checked again immediately
before publication and successful return. If staging stops after the output is
written, no completed receipt is produced; retry refuses the partial output.
A caller requires successful rendering **and** successful metadata verification;
existence of an output or receipt alone is never acceptance.

Receipt `market_pipeline_primary_web_managed_metadata/1.0` binds source/output
file metadata and the caller's public provenance identifiers. Exact JSON types
and the full expected mapping are checked during verification; it consumes the
receipt from a retained bounded descriptor. The receipt and CLI explicitly set
`runtime_verified_by_tool: false`, `runtime_mutated: false` and
`deployment_eligible: false`. They attest a staged backup configuration only.
They do not attest the running source, prove OCI lineage, enable product
authority, activate a container or create another capture/session owner.

A future finite caller must hold the permanent marker **M**, bind current public
source daemon evidence and the retained 17-container/Capture06 `6a16...`/`f79...`
profile, and refuse foreign/pending ownership. The old C07 source/reservation
fences are absent: do not recreate or adopt them merely to acquire flock locks.
Public evidence must distinguish the shared source image/release/tree/input
signature from the derived Capture06 f79 image and its parent-layer lineage,
and distinguish both from a new deployment target. Only that actual source
baseline may supply the new Env's image ID. Stage the configuration through its
normal managed owner and then run a freshly pinned normal backup validator;
never activate containers as part of configuration staging. A bounded operation
deadline is required. Staging without downtime does not itself require a new
human permission; any actual stop still requires a valid downtime window.

## Explicit hot backup contract (source-only)

The current archive receipt/journal schemas are `market_pipeline_backup_restore/1.4`
and `market_pipeline_backup_journal/1.2`. They bind `backup_mode` as `hot` or
`quiesced`; a mode cannot be chosen retrospectively. Earlier 1.3/1.1 and 1.2/1.0
controls refuse live use before opening the Env. Explicit, pinned historical
inspection remains separate and preserves the older strict verification rules.
Unknown historical mode never grants permission to ignore row differences.

Ordinary `verify` remains strict for quiesced backups. The distinct `verify-hot`
requires exact receipt/journal SHA256 pins, a fresh 60–86400-second age bound and
an independent restore run ID. Both controls are parsed from the same bounded
descriptor buffers whose hashes were verified, with their identities retained
through return. The source Env remains metadata-bound without content hashing.
Hot receipts bind the actual source release/image, not a Blue/Green target.

Source database/container identity, migration versions and complete schema must
match before and after each operation. Schema introspection runs an ordered
`SET search_path TO pg_catalog` and checks that setting in the same connection
before canonical JSON hashing; a CTE evaluation-order assumption is insufficient.
Hot source rows/sequences may advance, while table/sequence identities and schema
remain fixed. Source and restored schema hashes must match in both backup modes.

Hot verification hashes the dump through a retained descriptor, bounds reads to
the advertised actual size plus one byte, and gives a duplicate of that same
descriptor to `pg_restore`. It performs a fresh isolated restore with different
owned resources, then requires its entire invariant inventory to equal CREATE's
stored restored inventory. A changed source, artifact, control, restored schema,
row/sequence inventory or incomplete cleanup refuses verification. Malformed
control types and bools impersonating integer metadata are closed refusals.

Success explicitly remains `eligible_for_new_deployment: false`: HOT3 still
needs a finite owning producer, measured capacity, daemon-side deadline/cleanup,
managed source provenance, and durable verified offhost transport. This source
packet has not created a production dump or independent restore. It grants no
production stop, daemon upgrade, reboot or activation authority.

## Separate configuration and image identities for managed04

Actual04 provisioning subsequently **passed** through Ansible from reviewed
source `d6b06990`, with its own deadline `2026-10-24T17:17:17.122226Z`.
The strict normal validator accepted the staged source. An independent
read-only check matched the completed receipt, canonical public evidence,
original file metadata and its complete parent chain. Only IMAGE changed in
the new configuration; the original Env and all runtime intent were preserved.

The managed directory is
`/srv/trading-bot/market-pipeline-releases/security-backup-managed-20261024-04`.
Its `web-managed-receipt.json` SHA256 is
`305de6c1fc3a1997e53f91c689f83d038c37383d27b15d6d4571281dfb233e9c`;
`public-source-evidence.json` SHA256 is
`2e13ad0997eacd9ec8cf1f6cd6dc22494b3f81b0216a4ec684559f28e4520f3c`.
These are hashes of public/metadata control evidence, never Env contents.
This completed configuration staging only: no dump, restore, offhost copy,
service stop, engine upgrade, reboot or security acceptance occurred.

Actual03 provisioning refused cleanly with
`primary_managed_source_release_mismatch`. Its public script staging and empty
managed directory remain immutable failure evidence and cannot be adopted.
No Env, renderer receipt, dump or runtime change was produced. The failure does
not reveal the original RELEASE value. A later candidate must use fresh04
destinations and validate that field through the normal parser.

Managed receipt `market_pipeline_primary_web_managed_metadata/1.1` now separates
`configuration_source.release_sha/release_tree` from
`source_image.image_id/oci_revision/oci_tree/image_input_signature`. Its required
API arguments name both origins explicitly. The proposed configuration identity
is public Git commit `f3be9ae2c4e9df0da31abaa41e91f327eb9573a2`, tree
`d87bb5a0f7d9f96edfdad177c66f7afd120e02f7`, independently resolved from public Git
metadata. This is a candidate assertion: the normal renderer must still require
the original parsed `MARKET_PIPELINE_RELEASE_SHA` to equal that exact argument.
It never infers the value from the directory or rewrites a mismatch.

The actual E46 anchor retains its separate OCI revision `a6dcd636a1525a3a3d47e2a6083ca887b16080db`,
tree `259c9c4964864b7acfd92f94c9856e664130d082` and existing input signature. The
producer preserves all seven heterogeneous runtime role images and verifies
the separate Capture06 f79/223 layer and functional-label lineage. Only the new
backup configuration's IMAGE key changes; RELEASE, project, PRIMARY lane,
backfill, roots and every other setting stay equal to the bound original.

The original directory has no historical renderer receipt. The public proof
therefore records the verified public Git commit/tree and the image's separately
verified OCI provenance, and explicitly denies historical whole-Env content
verification. No configuration input signature is invented. Legacy Env digest
fields are never recomputed or used to authenticate the current Env. Receipt
1.0 cannot be silently reinterpreted as 1.1; exact fields and JSON types remain
required, alongside retained source/output metadata and canonical public proof.

Normal backup source validation now has an explicit
`allow_private_primary_source=True` permission independent of
`allow_target_identity_mismatch`. Managed04 uses identity mismatch **false**,
the proposed configuration SHA and the actual immutable E46 image, so both
identities must match exactly. Ordinary callers remain Shadow-only by default;
existing create/verify and HOT1.4 interfaces keep their contracts. Future HOT3
uses the configuration SHA/tree in its backup receipt and binds the managed1.1
receipt/public envelope separately for actual image OCI provenance. This source
change grants no dump, deployment, activation or production acceptance.

The finite `web_market_managed_config.py` producer and its Ansible wrapper now
bind the completed closure, permanent M inode and absent old fences. They check
the public 27-container inventory, parked10/retained17 intent, heterogeneous role
images, actual E46 source OCI identity and the separate f79/223 two-layer lineage
before and after normal rendering. Only the four pinned public helper scripts
and a fresh managed backup configuration are staged. Original Env metadata is
retained; no diagnostic Env copy/hash or referenced secret/session read occurs.
Canonical public source evidence is persisted and hashed independently of the
private configuration. Source tests and public-module loading passed; actual04
provisioning also passed as recorded above. Dump, restore and offhost acceptance
remain separate pending actions.

## Finite HOT3 source preparation

The new `web_market_hot3.py` producer and `web_market_hot3.yml` wrapper prepare
one fresh hot backup operation. This paragraph records reviewed source behavior,
not an executed backup or security acceptance. Managed04's completed receipt and
public source proof remain stable inputs; its expired staging deadline grants no
authority to the new operation. HOT3 requires a fresh UTC deadline of at most two
hours, a fresh nonce, the original permanent M file description, absent old
fences, and fresh public control/profile validation. It preserves all 27 current
container identities and intended running/restart/mount/network settings. It
does not claim unchanged PIDs, uptime or transient processor health.

Before any dump, the independent watchdog scope is armed with the inherited M
descriptor. The worker uses a nonce-specific PostgreSQL application name and two
distinct owned restore container/volume bindings sequentially. The normal hot1.4
helper performs CREATE plus its restore, then a separately pinned `verify-hot`
performs the independent restore. Metadata, dump, restore and server processes
have finite client bounds and absolute in-container deadlines; a killed client
alone is never treated as daemon cancellation. Cleanup terminates only exact
owned backend PID/start/database/application tuples and removes only exact
nonce-labelled restore resources. Disk pressure stops only those owned tasks.
Transient metadata/stop failures keep the watchdog retrying under M. A killed
worker or failed supervisor durably signals cancellation before any stop RPC.

Docker volume/container creation has no daemon-side request cancellation proof.
If cancellation interrupts a creation phase before its synchronous completion,
the watchdog retains M and retries cleanup through the full operation deadline
plus its 120-second cleanup reserve. It then records unresolved/manual recovery,
never claims all resources absent, and forbids automatic retry/adoption. Negative
inventory polls cannot prove an accepted timed-out creation request will never
materialize later. Existing partial directories, artifacts and phase journals
are retained; a subsequent operation needs a fresh namespace and review of any
unresolved daemon outcome.

Capacity is measured in bytes on the actual Web work/Docker filesystems and the
Native attached backup mount. The attempted dump cap is the smallest of fresh
Native capacity minus cipher overhead, the helper's 64-GiB bound, and Web's
available space after conservative `2 * database_size + 4 GiB` restore/WAL/temp
space and an 8-GiB reserve. No compression ratio is assumed. A free-space guard
continues throughout both restores. Only after both restores and owned-resource
cleanup pass does the unchanged normal crypt tool encrypt the retained dump and
verify the decrypted plaintext hash/size. Key metadata is bound; no key hash,
copy or diagnostic read is introduced.

The old Web-to-Native `-07` SSH authorization expired and failed authentication.
HOT3 instead requires the fresh public transport receipt at
`/root/.ssh/security-web-market-hot3-20261024-01/transport.json`, SHA256
`539910168632df009a175f373693bb441d499292fd24413bdca9328141f394e0`.
It binds the new root-only key metadata, original pinned public known-hosts file,
fixed Web/Native endpoints and expiry `2026-10-24T20:59:13Z`; the fresh HOT3
deadline cannot exceed that expiry. The old immutable plan remains unchanged.
The actual new-lane capacity-only probe passed without creating receiver files;
this is transport preflight evidence only.

Two other benign actual Web prerequisites passed separately: the exact
`systemd-run --scope` launch flags preserved an inherited descriptor containing
only a new public literal under a 20-second scope limit, and the exact source
PostgreSQL image's fixed absolute-deadline guard ran `/bin/true` with return code
0 for a future deadline and 124 for an expired deadline. These probes read no
production SQL/data, used no M descriptor and restarted no production service.
They prove those launch/tool seams only, not dump, restore or backup completion.

The receiver CLI verifies Native's exact device 2064/ext4/UUID
`9d19fc8e-f71a-4089-b826-5dddfe83ea28` at `/srv/trading-bot/backups`, never Native
root storage. Cipher size plus 8 GiB and 1 MiB of control allowance must fit
fresh measured capacity. It creates a fresh namespace, accepts only exact-sized
ciphertext, fsyncs and rereads the same descriptor, then independently reopens
and verifies the completed receipt/cipher. No plaintext or key is sent offhost.
The producer publishes completion only after both restore proofs, cryptographic
reconciliation, receiver receive/separate verification, and final source/control
checks pass. Capacity or durability refusal remains unaccepted; this operation
has no ObjectStorage fallback, production service stop, deployment eligibility,
engine upgrade, reboot or security acceptance.

HOT3 attempt01 from `f384a1b8` refused before intent publication. Its stage holds
only public source; no work directory, watchdog, dump or restore was created.
Read-only diagnosis proved all preceding source/control/transport checks passed,
then found the public plan's PostgreSQL probes have exactly `database`, `id`,
`kind`, `user` fields. The caller had incorrectly indexed a nonexistent `name`.
The corrected producer selects and validates that exact probe shape by immutable
container ID. A sanitized actual-schema fixture exercises this pre-intent path.

Attempt01 evidence is retained untouched. Continuation uses fresh stage/work
paths ending in `security-web-market-hot3-20261024-01-attempt02`, an explicit
intent `attempt_id` and a fresh nonce, while preserving the same operation and
transport receipt. The original deadline `2026-10-24T20:08:55.730262Z` is required
in source and wrapper; this continuation extends no authority. Before each
preflight boundary, an exclusive public checkpoint records only its fixed phase
name. A refusal emits only a closed reason token from an exact trusted helper
exception type, or a fixed safe error category, plus that phase and verdict
booleans. Exception text, private paths/values and tracebacks are not emitted;
failure evidence is written only to the producer's own fresh stage.
