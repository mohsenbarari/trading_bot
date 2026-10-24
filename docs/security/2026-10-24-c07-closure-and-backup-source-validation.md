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
