# ACL caller assessment and static inventory

This packet is source-only. It does not close CVE-2026-54369, modify scanner
results, authorize production installation, or execute an inspected binary.

The [upstream ACL maintainer's advisory](https://www.openwall.com/lists/oss-security/2026/06/29/1)
explains that pathname APIs retain their historical link-following semantics;
each caller must adopt the new descriptor/flags APIs where appropriate. Debian
lists ACL 2.4.0-1 as fixed, but this package-level classification does not prove
that an unchanged caller became safe.

The exact pinned upstream sources confirm that `acl_get_file` and
`acl_set_file` call their new `_at` counterpart with `AT_FDCWD` and flags zero.
The exact Debian tar -5 patch only renames conflicting static functions. Its
wrappers still call old ACL APIs through gnulib's `/proc/self/fd/<parent>/<name>`
or `fchdir` fallback. Those wrappers retain the parent directory, but reopen the
final pathname. Tar's ACL extraction call also discards an available file
descriptor passed to `set_stat`; delayed directory restoration passes fd -1.

Read-only parsing of the actual published tar DEB confirms that `/usr/bin/tar`
(SHA256 `1d9d884a06465ad5c0551858ceaaaa4dc60eda57ad5f12b710ab52d63e817892`)
imports `acl_get_file`, `acl_set_file`, and `acl_delete_def_file`, without the new
`_at` APIs. The actual libacl ELF exports the new ABI while preserving the old
one. This corroborates the caller gap; it is not a privileged regression test.

`scripts/verify_acl_consumer_presence.py` supplies the next missing inventory.
The successful composition run 40125049596 produced image
`sha256:0e4f132a9696569af9c138204fea381d3a7b02e43a7f845dc613eba107ef377c`.
The image artifact has not been downloaded onto Native by this packet. The
observational probe accepts a syntactically strict immutable image ID; a fresh
ordinary composition can produce a different ID because of build timestamps.
The outer CI controller must validate the composition's exact Docker image ID
and provenance, then
execute the Git-sealed probe buffer in an isolated disposable container with
network none, read-only filesystem, no production mounts or environment,
dropped capabilities, no-new-privileges, and finite memory/PID/time limits.
The supplied `--image-id` is an outer identity binding, not self-attestation.
`--source-commit` and `--verifier-sha256` bind the probe's sealed source bytes.

The probe uses only Python's standard library. It reads retained directory/file
descriptors under `/usr`, `/app`, and `/opt`, refuses changed identities, and
caps entries, files, bytes, names, ELF structures, output, and elapsed time.
It reports each ELF's hash, `DT_NEEDED`, and undefined legacy, `_at`, or fd ACL
symbols. Sectionless dynamic objects are refused. Directory symlinks are not
traversed; regular-file aliases are resolved only through the already observed
in-scope path map. Non-ELF regular files are inspected only for their four-byte
magic; no file contents are output. Production configuration and environment
files must never be mounted into this container.

The result identifies callers for review, including potential coreutils callers
which package-version receipts alone cannot enumerate. It does not establish
call-site flags, safety, static embedding, `dlopen`/ctypes reachability, future
runtime code, or production mount contents. All acceptance and scanner-change
flags remain false. Preserve the full raw vulnerability report and this evidence.

A scoped tar correction should carry the already opened object descriptor into
ACL reads and writes and use `_at(fd, "", AT_EMPTY_PATH, ...)`, including default
directory ACLs. Where no descriptor exists, establish the intended object with
safe parent traversal and retained inode identity before changing its ACL.
Preserve explicitly requested dereference behavior while binding metadata to
that same opened object; a global change to legacy libacl semantics or a blind
`AT_SYMLINK_NOFOLLOW` substitution would change existing behavior. Review the
actual consumer inventory before selecting all affected callers. Ordinary
unprivileged file/directory ACL round trips, both dereference modes, descriptor
identity checks, compiled API-call assertions, and the existing full tar suite
are appropriate CI checks. No custom exploit or malformed archive is needed.

The hermetic suite denies host/native/network execution before importing the
probe and exercises real temporary filesystem reads, inert ELF metadata, alias
resolution, malformed metadata refusal, exact image identity, file replacement,
short/growing files, and byte/count/time limits. A separate read-only source
check parsed all 97 ELF objects from the already published ACL/tar, util-linux,
and ncurses DEBs without running or loading any of them; only tar imported ACL
functions among those packages. This is not the whole-image caller inventory.
