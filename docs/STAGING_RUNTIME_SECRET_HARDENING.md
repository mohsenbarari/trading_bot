# Staging runtime secret hardening

## Why this exists

The staging controller keeps a generated Compose file under a root-only runtime
directory.  The generated file previously contained the resolved values of
database passwords, bot tokens, API keys, and signing secrets in repeated
`environment` blocks.  The file was not tracked by Git, but plaintext secrets
in a Compose document are easy to expose through diagnostics, backups, or
accidental command output.

## Applied remediation

The active staging Compose file now contains only `env_file` references.  Each
service has its own root-owned file under the protected runtime directory:

```text
/root/secure-envs/trading-bot/three-site-staging-<release>/rollback-bot-fi/runtime-env/<service>.env
```

The directory is mode `0700` and each file is mode `0600`.  The generated
Compose file is also mode `0600`.  The values are not committed, copied into
the repository, or emitted by health/rollout reports.

The migration was verified by:

1. `docker compose config --quiet` succeeding;
2. comparing the resolved environment maps before and after migration without
   printing their values; and
3. confirming that the active Compose file has no inline assignment for the
   secret-bearing keys.

The original rendered file is retained as a root-only rollback artifact.  It
must not be used for new deployments.  If the artifact is no longer needed,
remove it only after the staging rollback window closes.  Because the old
rendered file was previously readable by a privileged operator, credentials
used only by staging should be rotated during the next controlled maintenance
window; production credentials must not be rotated as part of this change.

## Deployment guardrails

- Never run `docker compose config`, `docker inspect`, or `systemctl show` with
  unredacted output in a shared log or chat.
- Keep secrets in the protected runtime directory, not in the repository,
  image layers, Compose labels, or command-line arguments.
- A future staging render must fail closed if it would write a non-empty
  secret-bearing value directly under a Compose `environment` key.
- This hardening changes secret transport only.  It does not enable the bot,
  inference selection, auto-selection, or production rollout.
