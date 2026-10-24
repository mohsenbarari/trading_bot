# Explicit bounded quiesced PRIMARY backup API

This is an additive source API for the fresh engine operation. It supplies no
maintenance-window authority and does not adopt HOT3 or expired C07 authority.
Ordinary CREATE/VERIFY/HOT interfaces and defaults remain unchanged.

`create_quiesced_primary_backup` accepts the ordinary release/image identity
arguments plus mandatory `execution_limits` and `restore_resource_binding`.
There are no mode, identity-mismatch, PRIMARY-permission, refresh or adoption
switches. The API requires a coherent PRIMARY lane and exact configuration
RELEASE/image equality, with identity mismatch always false. Configuration
release/tree identify managed04's f3be/d87b configuration; the owning producer
separately validates actual E46 image OCI a6/tree259/input669 lineage. The API
does not equate those configuration and image source identities.

CREATE requires a fresh empty backup namespace, refuses legacy/existing evidence
before opening the role Env, and proves actual project writers quiesced before
dump work. The shared backup core uses quiesced mode throughout, bounded daemon
commands, an explicit owner-labelled restore tuple and the retained dump FD.
Source/database/schema/row/sequence parity is strict. A final source inspection
rechecks actual writer quiescence with the artifact FD retained. The role Env
FD remains bound throughout; normal parsed configuration is used in bounded RAM,
without Env hashing, diagnostic copying or content output.

`verify_quiesced_primary_receipt` requires the same identity arguments, exact
receipt and journal SHA256 pins, finite maximum age (60..86400 seconds), and
mandatory execution limits. It accepts only current schema1.4 quiesced receipts
and schema1.2 journals. It retains the exact control and Env descriptors, validates
all stored restore/source parity, and proves the current source matches before
and after bounded artifact hashing. Both fresh source inspections require actual
writer quiescence; health alone cannot satisfy them. Artifact size, SHA and
retained inode/path metadata must match. It returns the original receipt payload,
without rewriting stored proof or claiming a new restore.

CREATE performs one fresh owned restore. VERIFY does **not** perform or claim a
second independent restore. Unknown daemon outcomes and cleanup exceptions
propagate as failure; these APIs do not turn client cancellation or resource
absence into a cleanup acknowledgement.

The caller must retain its three inherited locks, exact current runtime/source
profile, prearmed PostgreSQL/restore watchdog, capacity budgets and absolute
operation cutoff. It must supply a unique restore tuple and application name,
clip the daemon deadline to the pre-install cutoff, and preserve the reserved
recovery interval. Client timeouts do not prove daemon cancellation. Neither API
performs encryption, offhost transport, cold datastore archival, package or
engine installation, service changes, acceptance, or operation-window renewal.
The owner still requires those separate proofs before proceeding.

Hermetic tests exercise the real shared CREATE/evidence paths with ordinary
public temporary fixtures and named command mocks. They cover retained artifact,
control and Env ABA guards, fresh namespace refusal, finite controls, coherent
PRIMARY/config identity, actual writer-quiescence refusal, strict row/schema/
sequence/database parity, unchanged ordinary permissions and unknown restore
failure. No actual Env, database, container, daemon or host is accessed by tests.
