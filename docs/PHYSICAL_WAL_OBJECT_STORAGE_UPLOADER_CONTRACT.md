# Physical recovery Object-Storage uploader contract

`core.physical_wal_object_storage_uploader` is a default-disabled, root-only
adapter for publishing immutable encrypted recovery artifacts from a local
spool.  It has two deliberately separate entry points:

- `PhysicalWalObjectStorageUploader` accepts only a canonical physical WAL
  segment handoff descriptor and returns the WAL spool receipt type.
- `PhysicalWalBaseBackupObjectStorageUploader` accepts only a canonical
  completed physical base-backup handoff descriptor and returns the
  base-backup spool receipt type.

The adapters share only the low-level encryption/create-only/readback
primitive.  They do not dispatch one descriptor kind into the other.

Each enabled configuration pins distinct source and destination sites from
`{webapp_fi, webapp_ir}`, the destination age recipient, a root-owned 0700
workspace, a fixed protected spool root, and a non-secret bucket/region
identity.  Therefore normal FI-to-IR recovery publication and the separate
IR-to-FI failback direction use different configurations; the descriptor,
writer-term holder, recipient, and deterministic object key must all match
that configuration exactly.

The top-level namespace is also a canonical route pin, not a cosmetic key
prefix: `webapp_fi → webapp_ir` is only `physical-wal/`; the promoted
`webapp_ir → webapp_fi` failback route is only `physical-failback/`. The
namespace appears in the spool manifest binding and canonical descriptor, is
included in its route hash, and is rechecked by the uploader before it opens
the client or encryptor. A normal-path descriptor/config cannot be replayed
on the reverse path, and the reverse path cannot publish under the normal IAM
scope.

The destination remains an Object-Storage pull consumer.  Direct site
control is explicitly forbidden.  The module neither opens a network client
at import nor provides a credential, endpoint, SSH, replication, promotion,
or direct FI-to-IR control path.  Age encryptor and Object-Storage client
factories are injected only when an upload is invoked.

Before encrypting, it requires enabled bucket versioning and a strict ACL
proof: an identified canonical owner with nonempty grants consisting only of
that same owner with `FULL_CONTROL`.  Missing ACL evidence is rejected; this
uses the audited S3-compatible privacy proof supported by the deployment
provider rather than assuming optional policy-status APIs exist.  It rejects
an existing object version or delete marker, uses a conditional
`IfNoneMatch="*"` create-only write, rejects provider-side encryption fields,
then proves the exact returned VersionId through version history, HEAD
metadata/size, and GET ciphertext hash/size readback.  Readback bodies are
closed even on failure.

An accepted receipt is encrypted archive/recovery evidence only.  It is not
native PostgreSQL `remote_apply`, a strict acknowledgement, a writer permit,
or a promotion proof.
