# Monitoring and process-start recovery evidence

## Native watchdog timer

The native timer was active/enabled but `SubState=elapsed`, with both next
monotonic deadlines zero. Its last trigger was 23 October 20:58 UTC; the health
report was from 20:57, before the 21:16 host reboot. Those old healthy rows did
not establish current monitoring. A separate manual non-restarting check first
verified all four native pipeline roles and produced no restart request.

The installer now includes `OnActiveSec=15s` alongside its existing boot and
15-second inactive-relative schedule. This gives timer activation its own
initial anchor, as defined by the
[systemd timer documentation](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html).
The native timer was changed atomically under the unchanged deployment lock,
validated by `systemd-analyze verify`, reloaded and restarted. Its service and
mount-gate drop-in were preserved; no application restart was requested.

Actual native timer hashes:

- Before: `8fd47c79a5f57abe2c662c25c670e63ecccc6fa5081f060654272f263502c4ec`.
- After: `dbfbf367bba7ea43d673b7e51aa50654150a6fe1de7c530cd652dbfe0620491c`.

At 08:17:18 UTC the timer had automatically triggered again. The next
observation showed `active/waiting`, a scheduled inactive-relative deadline,
health age 7.1 seconds, and all four native roles `LIVENESS_OK`. Future recovery
must check a future deadline and fresh evidence, not merely active/enabled.
This timer-template amendment was applied only on Native; Web's timer and old
maintenance records were not altered by it.

Both hosts also received the already reviewed public watchdog-script fix
`7b12a91cc2b32f6465807b2898ab161e6030d1833d31f1563ec65b3e70ce850b`:
Docker `Restarting=true` is classified before `Running=true`. The exact old
script digest, unit bytes, lock identity/payload and container IDs/images were
checked around atomic replacement. This reporting change is not proof that
the separate capture or projection incidents are repaired.

## Deployment process-group readiness

The broad CI run `40070154011` exposed a real pre-exec observation race: a child
inherits its parent's process group before `setsid` runs. One immediate `ps`
sample could reject a correctly isolating launch. A bounded synthetic 0.3-second
launcher delay reproduced the false refusal, without sourcing full deployment
scripts or contacting hosts.

The guard now waits at most the smaller of two seconds and its wall budget for
that exact owned PID to isolate or finish. Startup counts toward the original
deadline. A persistently wrong group still refuses and undergoes bounded cleanup.
There is no signal-stop launch protocol or weaker resource containment.

Six focused extracted-function tests passed, including delayed isolation,
persistent wrong group, startup deadline and termination-resistant descendants.
The fixtures now fail immediately on wrong status and verify child death with
owned cleanup, instead of relying on a two-second natural child expiry. These
are source/test results; they do not by themselves close broad CI or activate a
new application release.
