# Ordinary App activation

`deploy/maintenance/normal_app_activation.py` is a separate, initially unarmed
entry for the five ordinary App/Bot/sync roles. Default is CHECK. Apply needs a
fresh source-bound two-host control and the exact confirmation token. Peer mode
receives apply authority only when the Native invocation explicitly requests
it; CHECK denies begin and mutating commands.

Each role retains its own admitted normal prefix. Historical creation versions
and present-empty `--no-deps` labels are distinguished from the current Compose
CLI. Web App's three-file rollback prefix is separate from sync's two files.
The authenticated runtime `always/0` restart policy is preserved through forward
activation and rollback even where a historical creation override says `no`.

The current immutable ordinary artifact remains source665220a2/ConfigE1, using
the original per-host LOAD association; Web's mapped identity is not relabelled
as ConfigE1. All complete normal Config/Env/Host/mount operands stay in RAM.
Every unresolved service Env reference is covered before full rendering.
Explicit aliases, full source/opaque-reference metadata and the fixed public
machine-id are retained through final checks. Writer/DNS never change.

The original canonical smoke, both LOADs, fresh two-host backup/restore and
candidate/old-image schema-compatibility gates remain mandatory. No unrelated
UI matrix is added as an activation gate. Passing source fixtures is not one
of those live gates and does not arm the controller.

The sealed lifecycle/coordinator order is reused: stop first, prove the old
cgroup empty, create without start, validate the actual new runtime, start,
check normal readiness, then retain exact rollback/terminal evidence. Full
normal parity is rechecked immediately before stop. The shared cgroup helper
holds the original directory and events descriptor and proves either empty
live state or kernel deactivation; directory link count is not deletion proof.
Permanent fences outlive source finalizers. Only the exact closed owned guard
is detached; unknown outcomes retain history and grant no retry/resume.

Root passed the original16 cases in the genuine Root/CAP_CHOWN-only/NNP,
separate-init/readonly/network-none own-COPY lane. The final dependency-only
update was also included in Root's complete274-case packet, manifest
`c723a6a2c7ef81c8ebd4c02f0492d43ea058d2ac3acf636907f749f577f7da44`,
result `273d6c81c4cc118b5d6001f4c0cb2ca5c08781dd12e33e7e0d84dee6ff474eef`.
No serving role has been activated by these tests. A real stage/control/check
and reviewed live gates are still required.
