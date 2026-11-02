# Parking obsolete QA while a production incident stays open

This NEW no-production-downtime scope parks only the ten exact obsolete dc32
QA containers. It does not retry the unstarted/expired retirement task, modify
the original C07 manifest/deadline, repair capture, or accept security health.
The old failed read-only retirement check and all source/audit evidence remain.

The default is read-only. The original marker/source/reservation inodes are
exclusively flocked; the original host, dead intent owner, linked audit and
unchanged original journal are independently proved. There must be no old
retirement audit/unit, active HOT backup or uncleaned owned restore resources.
The actual Web API and six retained datastores must be ready before parking.

An independent ten-minute runtime and dated task scope apply; the old 07:00 and
unstarted retirement 07:10 deadlines are not extended or passed as fake inputs
to an old validator. No original fence PID or payload is changed.

Explicit action `park` requires
`PARK-EXACT-TEN-OBSOLETE-QA-KEEP-INCIDENT-OPEN-20261024`. Only the ten approved QA
restart policies become `no`. Their eight apps/TLS stop gracefully first, then
their PostgreSQL/Redis pair. All ten containers, all images, four volumes and
all bind-file metadata including secrets/TLS files remain. Nothing is deleted;
public TCP 8443 must stop listening. Cleanup is reviewed in ten days, not
performed automatically.

These four QA volumes use Docker's local driver with bind options. Preservation
binds the complete Name/Driver/Mountpoint/Options/Labels to each fresh mount
descriptor, then verifies the exact approved `Options.device` directory's
device/inode on the expected attached parent filesystem. Stopping the last
container legitimately unmounts the bind from Docker's internal `_data`
directory; that transient `_data` inode is not the durable data identity.
Only the exact audit/postgres/redis/uploads backing paths are accepted, with
canonical non-symlink directories and the expected parent mount UUID. Other
drivers/options, changed backing identity or changed volume metadata refuse.
An ordinary local volume with no options uses its stable `_data` directory.

The 17 retained container identities, images, mounts, network/port bindings and
restart policies are checked before/after. Their full Config/HostConfig values
are compared **only inside the trusted process**, never printed, written,
copied or hashed. This in-memory parity check does not expose environment or
credential values. Full private inspection uses a separate memory PIPE reader
with a 2 MiB early output bound and 25-second deadline, not the older metadata
helper's temporary-file output. No production container, native unit, Writer or
DNS changes.

The existing account2 crash loop and processor/receiver freshness incidents are
explicitly recorded before and after. They do not make an isolated obsolete QA
stack useful, and they do not become passing health evidence. If the ten QA
containers and preservation proofs pass, the new audit says
`qa_parked_production_incident_open` with `qa_parking_verified=true`,
`security_transition_completed=false`, `all_prior_runtime_healthy=false`.
Parking always keeps `production_health_complete=false` and exit 3: a single
healthy snapshot cannot close those incidents without a separately reviewed
repair receipt. Required Web/datastore/native post-check failures also stay
openly recorded.

**Both original source/reservation fences are always retained.** The marker,
old manifest and original failed journals/audits remain byte-for-byte intact.
There is no fence-finalization or broader retirement path in this operation.
A separately reviewed single-owner capture repair and its actual receipt are
needed before a future healthy finalization can consider the legitimate new
container identity. Failed or interrupted parking is not automatically reused.
