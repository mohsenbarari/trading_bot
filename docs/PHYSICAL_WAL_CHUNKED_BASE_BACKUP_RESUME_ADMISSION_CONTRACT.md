# V2 chunked base-backup resume-admission contract

This contract is a fail-closed reconciliation boundary for an interrupted V2
chunked PostgreSQL base-backup publication.  It is not a resume executor and
does not authorize a new publication, an Object-Storage write, a restore, a
promotion, or a writer transition.

## Why a checkpoint is insufficient

Each V2 chunk is bound to its Witness-issued session and permit.  After a
crash, opening a new session cannot safely reuse the old object selectors or
silently treat old accepted chunks as members of a new manifest.  Doing so
would make the durable Witness record, immutable object version, and eventual
manifest describe different transfer sessions.

The existing publisher therefore continues to reject every `resume_checkpoint`
input.  A later operational continuation protocol must be separately reviewed
and must consume a fresh Witness-signed cross-session continuation capability.

## What admission verifies

When explicitly enabled by root, admission reads exactly one root-owned,
mode-`0600`, no-link checkpoint beneath a root-owned, mode-`0700` directory.
It requires canonical V3 checkpoint bytes and re-verifies:

- the historic signed transfer session, permits, source completions, and
  Witness commitments;
- contiguous, complete chunk coverage matching the independently supplied
  source snapshot scope;
- the exact durable Witness commitment for every historic chunk;
- the exact immutable `(object_key, version_id)` `HEAD` observation for every
  historic chunk; and
- a fresh, disjoint live Witness session and permit set.

There is no list, delete, upload, broad object read, direct WebApp control,
fallback, or V1 activation surface.

## Result boundary

The result is an opaque, process-local and non-serializable evidence object.
It is revalidated on use, including checkpoint and remote evidence readback.
It must never be converted into permission to restart, re-upload, reuse a
permit, reuse an object key, or claim a completed backup.
