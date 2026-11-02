# PostgreSQL candidate backup contract

This opt-in source change supports a verified old PostgreSQL source restored in
the reviewed new image **before** that image replaces a production database.
After rollout it can also back up an explicitly admitted candidate source.
It performs no LOAD, production recreation, retirement or activation by itself.

The sole new restore candidate is config
`49a811b28ed9289d4e981c8f6cd36e1cf05259653a858e40df0cb6d6413c9aa1`,
from run `40142076769`, recipe `ecfb667ee1583168f2777a6fa5f54766a63ef787`,
candidate receipt SHA256
`7995bb380b621119b56e58c7b7ba063884ecb888e5d323c8fffbf5a85fcbdb60`.
Its exact twelve ordered RootFS DiffIDs are fixed in
`scripts/backup_postgres_candidate.py`. The unchanged PostgreSQL executable is
15.19, SHA256 `1e08a0e796ae0461ec40031e9bd7a76831d453e723812b9c3e8757bc411a38c7`.
The raw Windows-only `golang.org/x/sys` UNKNOWN row remains: no finding is removed,
the module is not claimed fixed, and all security acceptance flags stay false.

## Explicit public input

Both existing CLIs accept the paired options `--postgres-candidate-control` and
`--postgres-candidate-sha256`. Omitting both preserves the existing path. A
missing member, changed buffer or invalid closed control refuses before the
normal private manifest/Env reader. No secret Env field selects the candidate.

The new root-owned private control has schema
`backup_postgres_candidate_identity/1`. It binds the fixed CI identity, receipt,
server bytes/version, twelve layers, unchanged raw counts and false acceptance
flags. Its `sites` contains only the explicitly measured `foreign` and/or `iran`
host/port entries. Each site separates:

- The loaded **new restore image**, with its actual local immutable daemon ID
  and typed archive-to-config49a8/full-RootFS proof. A manifest/index ID is not
  mislabeled as the config ID; index semantic/signature/trust flags remain false.
- `source_bindings`, keyed only by the fixed production database container names.
  Each entry explicitly admits `baseline`, `candidate`, or both modes.

Baseline mode admits Native config `aad6289…`, Web App daemon image `7bf293…`,
or Web PRIMARY daemon index `fe0737…`, as appropriate to the exact named role.
All use the known eleven baseline layers and unchanged 15.19 server SHA. The
Web image-to-config equality evidence is separately pinned by public receipt
`4af0d3e9d694e4869c0158bde2171b339713df4e4b2dcfc75d0b5e533a9ffbdb`.
An old mutable `Config.Image` string is recorded and compared only as source
metadata; it never becomes restore authority. Candidate mode requires the exact
new per-host daemon ID as both actual image and immutable runtime reference.

No actual loaded-site control is produced by this source packet. Root must
review and seal the measured per-host archive/config/RootFS association after
normal LOAD. An artifact-admitted or hypothetical image is insufficient.

## Runtime and evidence boundaries

The shared validator retains the public control FD and verifies its complete
file metadata/path identity through the operation. Image checks project only
ID, OS, architecture and ordered RootFS. Named source checks compare actual
container ID/name/project, `.Image` and the separately admitted `.Config.Image`.
Fixed bounded metadata commands verify only the public postgres executable hash
and version, then repeat the container identity projection. Runtime Env, keys,
sessions, SQL contents and arbitrary stderr are never diagnostic outputs.

Market CREATE/current VERIFY/HOT and quiesced PRIMARY APIs accept the optional
public control/path SHA. The default and historical interfaces keep their
existing schemas and behavior. Candidate source binding is recorded in source
metadata; candidate restore binding is recorded separately. Candidate CREATE
retains the verified artifact FD through restore/publication even without the
optional execution-limits object. Existing independent deadline/watchdog,
capacity, ownership, source parity and cleanup requirements remain in force.

The App producer seals the validator source and exact public control bytes into
its existing local/strict-SSH command. It validates the named source before dump,
uses only the verified immutable candidate with `--pull never` for an isolated
restore, checks that actual restore image/server, then repeats the original
source identity before publishing its public binding. A source switch during
backup refuses. Private settings continue through the normal trusted reader.

The old App default still restores using the local mutable
`postgres:15-alpine` reference. That is a documented legacy boundary, not new
candidate evidence. Future periodic timers need separately reviewed public
argv/control/SHA bindings and staged validator bytes; this packet edits no
timer, protected Env, old receipt, image tag or runtime configuration.

## Retirement and rollout order

Complete the five-role App rollout against its unchanged 15/27 inventory first.
Then a new operation may reversibly park the exact legacy set with restart=no,
retaining container IDs, images, volumes and all data. Stopping is not deletion
or final retirement acceptance. Fresh clean-stopped backup and a compatible
candidate isolated restore are required before RETIREMENT_FINAL or any deletion.
Unknown source versions, ownership or backup coverage remain blockers.

The existing production App receipt does not cover Native staging. OP08's
one-filesystem engine archive does not cover the orphan PostgreSQL payload on
the attached three-site volume. Preserve those histories and require their
separate actual data coverage. These tools' new scope is the three fixed
production DB names; they do not grant access to staging/orphan datasets.

After approved retirement, record a fresh baseline and update the five remaining
datastores one at a time. Keep the 8 GiB floor, stream verified image archives to
Native, and avoid large Native plaintext artifacts. Same-volume image rollback
preserves current committed data; never silently overwrite it with an older
backup. Unknown daemon writes remain unknown and receive no automatic retry.

Validation uses inert controls, real private tempfiles/FDs and named metadata
mocks under outer pre-import command/network/native/signal/resource denials.
No actual candidate backup, data restore or production operation is claimed.
