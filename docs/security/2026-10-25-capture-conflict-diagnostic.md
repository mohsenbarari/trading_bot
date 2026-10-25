# Capture identity conflict: bounded diagnosis

This packet is source and test evidence. It has not replaced a serving container,
fixed the capture conflict, or accepted the security release. The actual differing
semantic fields are still unknown. Refactor automation remains stopped.

`prepare_f79_conflict_diagnostic.py` admits the exact historical public capture
source, applies two reversible diagnostic changes, and preserves the original
exception and rollback behavior. The generated source is132537 bytes, SHA256
`2227443411d2d2b9ffcf56259b6750b5517025387f83dee024125c2abeb912c8`.
It changes neither event identity, deduplication, SQL nor capture ownership.

On the existing seen-identity conflict, at most once per60seconds, an owned child
compares the prior public event with the incoming event in RAM. It reads only
bounded, fixed event-spool files. Wall, CPU, memory, byte and output limits apply;
the parent kills and reaps a stalled child. Only a closed set of changed-field
labels may be reported. Message text, values, identifiers, hashes, credentials
and session contents are not emitted. Missing or bounded-out comparisons remain
explicitly unavailable, and the original error is raised.

`capture_conflict_diagnostic.py` is a separate stopped-first controller. Its
default is a check. A fresh immutable control, exact source pins, unchanged
permanent mutex metadata and existing legacy-capture inhibitors are required.
Trusted normal Compose rendering and complete private configuration comparisons
stay in RAM. The sole proposed change is the read-only public capture-source bind
for account2; image F79 and the foundation override remain unchanged. Other
containers, mounts, configuration and the single Telegram-session owner are
protected. Never invoke the older unsafe rollout work in this packet's place.

Apply requires proof that the old owner's cgroup is empty, creation without
starting or dependencies/build/pull, complete stopped-candidate parity, and an
exact container-ID start. Known failures may restore the original configuration;
an ambiguous mutation retains ownership and evidence without an automatic retry.
An installed diagnostic returns an incident-open outcome, including when the
original conflict recurs. It is not a functional or security acceptance receipt.

All Market and datastore image-store operations pinned to the old capture
container must finish before this diagnostic replacement changes that identity.
After an actual comparison, any functional repair requires its own evidence,
regression tests and controlled activation.

Root ran18 preparation tests and24 controller tests. The latter also passed with
genuine UID0 and only CAP_CHOWN, with ambient service/network effects denied.
The required CI root-fixture inventory includes the controller module. No live
diagnostic or production replacement is claimed by those tests.
