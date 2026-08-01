# IR-to-FI Object Storage failback contract

The IR-to-FI route is an independent data plane for the interval in which
`webapp_ir` is the Witness-authorized writer and `webapp_fi` is rebuilt as a
standby. It is not a parameterized reuse of the ordinary FI-to-IR publisher,
receiver, credential loader, or S3 client factory.

## Fixed direction and namespace

The only permitted route is:

```text
webapp_ir writer → private versioned Object Storage → webapp_fi standby
```

It has the distinct top-level namespace `physical-failback/`. The ordinary
`webapp_fi → webapp_ir` route remains exclusively under `physical-wal/`.
Both namespace and directed sites are canonical fields of the archive/base
manifest binding, handoff descriptor, completion record, source-manifest
assembly binding, reverse preflight, and uploader configuration. They are
included in the route hash where a route hash is emitted. A direction/prefix
mismatch is rejected before an encryptor, Object Storage client, or local
capture/readback action is opened.

## Four identity boundary

Exactly four non-overlapping identities are required:

- `fi-publisher` — normal route immutable creator only;
- `ir-receiver` — normal route exact reader only;
- `ir-publisher` — reverse route immutable creator only; and
- `fi-receiver` — reverse route exact reader only.

`PhysicalIrToFiObjectStorageFailbackPreflight` accepts only redacted SHA-256
identity facts and separate normal/reverse route scope digests. It requires all
four identities and both scopes to be distinct, confirms the fixed reverse
namespace, and produces a nonserializable opaque preflight capability. It has
no credential file, SDK, client, endpoint, bucket, object selector, Docker,
PostgreSQL, or network dependency.

Before an enabled reverse preflight may be built, verified, or consumed, a
pure four-role compatibility binder must receive exactly four role-local
factory projections: FI publisher, IR receiver, IR publisher, and FI
receiver.  It accepts neither a historical paired projection nor a profile
alias.  The profiles are fixed to
`fi-publisher-immutable-create-only-v1`,
`ir-receiver-exact-readonly-v1`,
`ir-publisher-immutable-create-only-v1`, and
`fi-receiver-exact-readonly-v1`, with their exact role operation tuples.  The
binder carries only redacted identity and route facts; it is not provider IAM
evidence, a credential authority, or an execution permit.  A later
Witness-signed live-IAM evidence collector remains mandatory before any live
campaign claim.

The preflight provider/identity route hash is deliberately distinct from a
physical WAL or base-backup descriptor's lineage hash. A generic descriptor
binds concrete stream or artifact facts and the Writer term, so its value
cannot safely be reused as a provider route identity (and WAL and base-backup
values differ from each other). The reverse handoff validates each domain
independently; it never forces those hashes to be equal.

`RootOwnedArvanS3FailbackSeparatedCredentialLoader` and
`RootOwnedArvanS3FailbackSeparatedClientFactory` are the concrete reverse-only
implementation of the two new machine roles. They use fixed root-owned local
paths for `ir-publisher` and `fi-receiver`, reject duplicate credentials and
the normal namespace, expose only public identity projections to the
four-identity preflight, and open only the relevant local role credential
inside its corresponding callback. The publisher wrapper permits only
create-only/exact-readback operations under `physical-failback/`; the receiver
wrapper permits only exact `GET(Key, VersionId)` under that namespace. Neither
normal-direction credential path is accepted or imported.

The reverse publisher's exact live-IAM surface also includes
`GetBucketAcl` and `GetBucketVersioning`, because the controlled uploader
checks both before it attempts its immutable create-only operation.  Those
reads do not broaden the object namespace or permit mutable publication.

## IR publisher

`RootOwnedWaIrPostgresFailbackHandoff` is default-off and root-only. It
requires a fresh reverse preflight, a live IR-held Witness term, a canonical
`physical-failback/` descriptor, and a separate
`PhysicalWaIrFailbackObjectStoragePublisherFactory`. The factory can expose a
bucket/region and create-only/readback client only inside a synchronous,
bounded callback after it returns a nonserializable admission capability bound
to the preflight, IR writer term, `ir-publisher` identity, and `fi-receiver`
identity.

The callback is active only for that single synchronous factory call. It must
run exactly once and the factory must return the exact receipt object produced
by it; a skipped, forged, reentrant, retained, or later callback is rejected.

The runtime never imports the normal FI publisher/IR receiver credential
loader or factory. It delegates only the generic local age-v1/create-only
primitive after all reverse-route checks. Publication is archive/recovery
evidence; it is never `remote_apply`, a writer permit, promotion, traffic, or
Full-Matrix authorization.

`RootOwnedWaIrPostgresFailbackCaptureBridge` is the separate, default-off,
root-only local capture seam used only while the live Witness term is held by
IR. It accepts one injected local consistent-base-backup runner beneath a
fixed private capture root, independently hashes and reopens the resulting
root-owned artifact, and releases it only to the reverse handoff's
base-backup uploader. The runner receives a canonical local invocation, not a
shell command, peer, URL, environment, credential, or traffic control. A
capture is rejected if the four-role preflight or IR term changes before its
handoff can complete. Its result is archive/recovery evidence only; it does
not claim that FI has received, restored, replayed, promoted, or served that
artifact.

## FI receiver staging

`RootOwnedWaFiPostgresFailbackPullRuntime` is the separate default-off,
root-only FI-receiver boundary. It requires fresh four-identity preflight, a
live IR-held Witness term, a verified IR-to-FI signed bundle, a root-pinned
fresh locator, and a dedicated
`PhysicalWaFiFailbackExactVersionReceiverFactory`. The factory exposes an
exact-GET client and its endpoint/region/bucket only during one same-thread,
one-shot callback. It may fetch only pre-pinned `(Key, VersionId)` pairs
beneath `physical-failback/`; it has no list, latest, mutable alias, PUT,
delete, or IR-publisher credential capability. The factory must return the
exact staging result from that callback; forged, skipped, retained, cross-
thread, or replayed callbacks fail closed.

The output is a secure FI-local staging receipt and a separate FI failback
stage-evidence type. It explicitly authorizes neither PostgreSQL recovery
materialization, replay, promotion, traffic, nor Full Matrix. A later FI
materializer must remain distinct from the existing WA-IR bootstrap
materializer while retaining the Witness-term, signed-bundle, exact-version,
age-recipient, source-quiescence, and no-promotion gates.

## FI detached replay materialization

`RootOwnedWaFiPostgresFailbackMaterializationRuntime` is a distinct,
default-off root-only continuation of the reverse pull. It accepts only the
typed `PhysicalWaFiPostgresFailbackPullResult`, a verified signed reverse
bundle, and a live IR-held Witness term. It rejects a normal namespace,
unverified/stale preflight, changed term, changed stage receipt, a source
candidate outside the fixed FI staging root, or a caller-chosen target.

Before *and after* its injected local runner, the runtime requires a fresh
four-role preflight and a separately signed, root-pinned writer-quiescence
receipt for FI's fenced writer root. The detached target root, source-stage
root, receipt root, and fenced writer root must be root-owned private and
non-overlapping. The runner is given exactly one canonical invocation with
`network_mode=none`, TCP disabled, and `standby-replay-only`; it receives no
current-writer API, promotion API, direct peer, endpoint, secret, command, or
Object-Storage client.

The target candidate and any prior durable receipt for that exact bundle must
be absent before the runner is invoked; neither is deleted or silently reused.
A runner acknowledgement is accepted only if its target is the exact private
candidate path and its device/inode match a no-follow local recheck. It must
also return canonical `PhysicalPostgresRecoveryReceiverReadbackEvidence`. The
generic recovery assessor then independently requires an in-recovery standby
whose replay LSN reaches the exact bundle terminal frontier under the same
stage binding and Witness term. On success the runtime emits a root-owned
redacted receipt with all authority flags false. That receipt is evidence of a
detached FI standby candidate only, never a promotion, writer, traffic, or
Full-Matrix permit.

Direct host-to-host data/control, FI-to-IR SSH/SCP, source-side receiver
control, and reuse of a normal-direction secret are forbidden in both
directions.
