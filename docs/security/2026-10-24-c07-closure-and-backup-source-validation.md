# C07 closure and backup source validation

The old C07 transaction stopped before package installation. Its immutable plan,
journal, failed recovery records, and original deadline remain historical
evidence. Security acceptance is still false; automation remains stopped.

## Read-only normal backup source validator

Run `deploy/maintenance/web_backup_env_probe.yml` with the existing exact
`web_engine` inventory. This stages only the two SHA-pinned public backup scripts
under the new private `/root/.local/security-backup-env-probe-20261024-01`
directory, takes the original marker lock (bounded five-second wait), and takes
both existing source/reservation locks without waiting or adopting their owner.
The existing normal `validate_release_env` function alone consumes the protected
role Env in memory. The wrapper does not inspect its returned mapping, copy or
hash the Env, run Docker, create a backup, or change services. It binds the
original plan and Env metadata, and checks that metadata again afterward.

Output contains only `validation_passed`, a closed reason code, and explicit
operation flags. In particular `backup_source_release_sha_invalid` and
`backup_source_image_identity_invalid` distinguish the old combined error.
Other contract refusals are deliberately redacted. Exit zero means that the
probe completed; **only `validation_passed: true` means validation passed**.
An Ansible changed result means public scripts were first staged, never a
runtime change. The staged public source is reusable only with identical bytes
and safe ownership/modes. Retain it as incident evidence for ten days, then
remove only after the incident no longer needs it.

This is not HOT3, a restore test, or quiesced backup acceptance. The obsolete
CREATE02 operation must not run. A future real HOT backup needs its own current
runtime proof and the completed C07 closure receipt.

## Independent terminal closure of the aborted C07 transaction

`deploy/maintenance/web_c07_terminal_closure.yml` defaults to `check`. It has a
fresh ten-minute clock and never extends the old C07 or QA deadlines. The
immutable C07 plan, journal at `writers_quiesced`, original failed recovery
records, accepted capture06 receipt, and QA parking receipt are bound by their
reviewed identities. Dead owned units/scopes/processes and pending owned jobs
must be absent. Unrelated ordinary healthcheck ExecIDs and watchdog jobs do not
block this action because it does not quiesce or stop any datastore.

The checks take the permanent marker first with a bounded five-second wait,
then the exact same two original dead-owner fence inodes without waiting or
changing their payloads/PIDs. They observe three sample points at least thirty
seconds apart in total: all ten QA containers remain parked with no restart,
port 8443 stays closed, four volume backings/images/secret metadata remain
preserved, the retained runtime intent is restored, six real datastore probes
and the Web API pass, native timers keep their original intent, and collector
inhibitors remain effective. Capture06's accepted `6a16...`/`f79...` identity is
the one explicitly superseding the old capture identity. It must remain healthy
and the sole session owner. Session contents are never opened. Full private
container configurations stay only in bounded RAM and are compared without
printing, serializing, or hashing their values.

Known processor/receiver health-cadence oscillation remains an **open incident**.
Only their exact two bound IDs may change observed process state; their complete
configuration and identities remain strict. A stopped/restarting retained
process or a new unhealthy component refuses closure. No original-all-27-health
or security-completion claim is made. Runtime collector guards remain in place;
persistent boot guards are not installed by this task, and Web reboot is not
authorized by this receipt.

After final review, use `closure_action=close` and the exact confirmation
`CLOSE-ONLY-ABORTED-C07-OWNED-FENCES-20261024-01`. The new receipt is written and
fsynced in phase `aborted_before_install_fence_release_pending` before removing
anything. Only the exact source fence is unlinked first, then the exact global
reservation last, with inode/payload checks and parent-directory fsync. The
permanent marker, old journal/plan and historical receipts stay untouched. The
new receipt then becomes `aborted_before_install_fences_released`, retaining
`security_transition_completed: false` and known incidents `open`.

A repeated read-only check of a completed receipt verifies its historical
evidence and that both old fences remain absent; it explicitly does not claim a
fresh runtime check. A pending receipt, partially removed fences, or a foreign
new reservation refuses automatic adoption/retry and requires a separately
reviewed recovery. No deletion of data/images/volumes is part of closure. Keep
the original incident evidence; review only disposable public staging/logs for
cleanup after ten days. A subsequent HOT3 operation must bind the actual
completed closure receipt SHA and the current runtime; it cannot reuse the
obsolete CREATE02's ownership assumptions.
