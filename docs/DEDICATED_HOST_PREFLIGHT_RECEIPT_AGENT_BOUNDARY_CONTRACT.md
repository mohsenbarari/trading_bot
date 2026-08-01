# Dedicated-host preflight receipt-agent boundary

`core.dedicated_host_preflight_receipt_agent_boundary` and its three fixed
scripts make the previously missing server-side endpoint for the controller's
`pinned-ssh-readonly-agent` route installable as reviewed, default-off assets.
They do not change a host when imported, rendered, or tested. They do not
create an account, modify `sshd`/`sudoers`, open a network connection, or run
the Full Matrix.

This boundary is only for these three locally reachable roles:

- `bot_fi`
- `webapp_fi`
- `witness`

`webapp_ir` is rejected by every ordinary receipt config and request parser
before any local runner or privilege boundary. It is not a fallback SSH path
and is not a place to publish or read a WA-IR secret. Its evidence is exposed
only through the separately rendered Witness-only account described below.

## Exact request and response boundary

The controller already sends the canonical, newline-terminated
`three-site-dedicated-host-readonly-preflight-request-v2` request. The
unprivileged dispatcher accepts no argument and only this request on stdin:

```text
schema, campaign_id, operation_id, release_sha, role, manifest_sha256
```

It rejects duplicate keys, non-ASCII/noncanonical JSON, a payload above 4 KiB,
unknown fields, `webapp_ir`, and any role outside the three named roles. The
only accepted remote command is exactly:

```text
collect-readonly-receipt
```

On success it emits only the existing bounded canonical redacted preflight
receipt (at most 32 KiB) on stdout. On every failure it emits no stdout
diagnostic. It never exposes a shell command, path, environment, sudo error,
credential, host key, or local probe output.

## Account and OpenSSH constraints

The rendered account policy is exact:

```text
account: preflight
home:    /nonexistent
groups:  no supplementary groups
shell:   /usr/local/libexec/trading-bot/dedicated-host-preflight/preflight-force-shell
```

The shell is deliberately not `/usr/sbin/nologin`. OpenSSH runs `ForceCommand`
through the account's login shell with `-c`; a `nologin` shell would reject the
forced dispatcher before it could collect a receipt. The rendered root-owned
`0755` `preflight-force-shell` instead accepts only this literal command:

```text
exec /usr/bin/python3 -I /srv/trading-bot-three-site/releases/<agent-release-sha>/scripts/run_dedicated_host_preflight_receipt_dispatcher.py
```

It rejects interactive login and every other client command. This gives the
agent a working ForceCommand path without granting the account a general
shell. The `sshd` Match block and `authorized_keys` `restrict` option are
independent second and third controls.

The generated `Match User preflight` block pins public-key authentication,
the root-owned authorized-keys file, that exact `ForceCommand`, no TTY,
no user environment, no user rc, no X11, no agent forwarding, no TCP or
stream-local forwarding, no tunnel, `PermitOpen none`, `PermitListen none`,
and `DisableForwarding yes`. It ends with `Match all`, so later configuration
is not accidentally captured by the restricted block.

The authorized-key file contains only one comment-free `ssh-ed25519` public
key with the `restrict` option. The renderer verifies the decoded SSH wire
format (`ssh-ed25519` plus exactly a 32-byte public key), so options,
comments, key-type substitutions, and malformed blobs are refused.

## Narrow root collector boundary

The unprivileged dispatcher starts exactly this no-shell argv with a clean
four-variable environment:

```text
/usr/bin/sudo -n -u root -- /usr/bin/python3 -I \
  /srv/trading-bot-three-site/releases/<agent-release-sha>/scripts/run_dedicated_host_preflight_root_collector.py
```

The rendered sudoers file is intentionally narrow:

- `env_reset`, `!setenv`, and a fixed `secure_path` apply to `preflight`;
- only root is the run-as identity;
- `NOPASSWD:NOSETENV` is explicit; and
- the trailing `""` in the sudoers command means *no caller arguments*.

Do not simplify that last rule to a bare command path: in sudoers matching,
that would permit arguments. `NOEXEC` is intentionally not used because the
fixed root collector calls the separately reviewed read-only collector, which
uses a small fixed set of local read-only probes. There is still no generic
root shell or arbitrary subprocess capability.

The root collector requires root, no argv, a root-owned non-symlink `0600`
runtime config at:

```text
/etc/trading-bot/security/dedicated-host-preflight/receipt-agent.json
```

It rechecks the source release layout, root-only config, default-off enable
flag, its own source release SHA, and that the request role equals the local
configured role before calling the existing
`run_dedicated_host_readonly_preflight.py` collector in-process. The existing
collector still owns the fixed local Git/Docker/process/mount observations and
canonical receipt validation. A timeout, malformed stdout, or nonzero root
collector exit is discarded by the dispatcher.

## Separate Witness-only evidence command

When, and only when, `site_role` is `witness`, the renderer emits a second,
independent account boundary. It does not reuse the `preflight` account or its
ordinary receipt sudoers rule:

```text
account: preflight-witness-evidence
home:    /nonexistent
shell:   /usr/local/libexec/trading-bot/dedicated-host-preflight/
         preflight-witness-evidence-force-shell
```

Its `sshd` Match block, restricted authorized-keys file, force shell, root
config and sudoers file are all distinct. The only accepted original SSH
command is exactly:

```text
collect-wa-ir-witness-preflight-evidence
```

It accepts no stdin payload, argument, receipt request, timestamp, nonce, or
historical selector. The unprivileged dispatcher may invoke only its separate
no-argument root collector. That collector requires a root-owned `0600`
Witness-only config at:

```text
/etc/trading-bot/security/dedicated-host-preflight/witness-evidence-agent.json
```

and then calls only the default-off local Witness attestation runtime. That
runtime calls the durable ledger's `collect_pinned_evidence()` method, which
has no selector and returns only the evidence matching its fixed attestation
request ID and nonce. The dispatcher checks canonical bounded evidence shape;
the central controller independently verifies both signatures before it uses
the embedded WA-IR v2 receipt.

This endpoint has no WA-IR network route, attester/ingress method, Object
Storage client, age identity, writer/promotion capability, arbitrary root
command, or generic receipt command. Bot-FI and WA-FI never receive these
extra assets.

## Rendering and staged review

The source-owned non-secret request template is:

```text
deploy/production/dedicated-host-preflight-receipt-agent-install-request.json.example
```

An actual request must be canonical ASCII JSON, root-owned, single-link,
regular `0600`, and located only at:

```text
/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-install-request.json
```

The renderer has no caller-selectable config or output path:

```text
python3 -I scripts/render_dedicated_host_preflight_receipt_agent.py
python3 -I scripts/render_dedicated_host_preflight_receipt_agent.py --render
```

The first form validates and prints a non-authorizing summary. The second can
write only a fresh, pre-created root-owned `0700` review tree at:

```text
/var/lib/trading-bot/dedicated-host-preflight/receipt-agent-rendered
```

It never applies assets to their live destinations. A later explicit root
change window must review the staged tree, verify the exact `sshd` syntax and
sudoers syntax with the system validators, install the system account with the
rendered force-shell, and atomically place only the reviewed files at their
fixed destinations. That later action must preserve rollback access and is not
authorized by a successful render, a receipt, or this document.

The rendered files are fixed to these live destinations and modes:

| Destination | Mode | Purpose |
| --- | ---: | --- |
| `/etc/trading-bot/security/dedicated-host-preflight/receipt-agent.json` | `0600` | Root collector role/enable policy |
| `/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-authorized_keys` | `0644` | One restricted controller public key |
| `/etc/ssh/sshd_config.d/80-trading-bot-dedicated-host-preflight.conf` | `0644` | `preflight` Match/ForceCommand restrictions |
| `/etc/sudoers.d/80-trading-bot-dedicated-host-preflight` | `0440` | Exact root collector allowance |
| `/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-account.json` | `0600` | Account policy evidence |
| `/usr/local/libexec/trading-bot/dedicated-host-preflight/preflight-force-shell` | `0755` | Exact-command account shell |
| `/etc/trading-bot/security/dedicated-host-preflight/receipt-agent-installation.json` | `0600` | Rendered file digest attestation |

For `site_role=witness`, the staged output additionally contains these
separate literal-evidence files:

| Destination | Mode | Purpose |
| --- | ---: | --- |
| `/etc/trading-bot/security/dedicated-host-preflight/witness-evidence-agent.json` | `0600` | Witness-only root collector policy |
| `/etc/trading-bot/security/dedicated-host-preflight/witness-evidence-agent-authorized_keys` | `0644` | Restricted controller key for the separate account |
| `/etc/ssh/sshd_config.d/81-trading-bot-dedicated-host-preflight-witness-evidence.conf` | `0644` | Literal-command-only Witness Match block |
| `/etc/sudoers.d/81-trading-bot-dedicated-host-preflight-witness-evidence` | `0440` | Exact evidence root collector allowance |
| `/etc/trading-bot/security/dedicated-host-preflight/witness-evidence-agent-account.json` | `0600` | Account-policy evidence |
| `/usr/local/libexec/trading-bot/dedicated-host-preflight/preflight-witness-evidence-force-shell` | `0755` | Exact-command account shell |

## Release selection

The following runtime paths must be literal entries in the reviewed release
inventory before a release can be materialized:

```text
core/dedicated_host_preflight_receipt_agent_boundary.py
scripts/run_dedicated_host_preflight_receipt_dispatcher.py
scripts/run_dedicated_host_preflight_root_collector.py
scripts/render_dedicated_host_preflight_receipt_agent.py
core/dedicated_host_preflight_witness_attestation_runtime.py
scripts/run_dedicated_host_preflight_witness_evidence_dispatcher.py
scripts/run_dedicated_host_preflight_witness_evidence_root_collector.py
```

The deployment template and this document are reviewed/committed alongside the
release but are not a substitute for runtime selection. No wildcard such as
`core/physical_*.py` or `scripts/*preflight*.py` is acceptable.

## Focused verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest -q \
  tests.test_dedicated_host_preflight_receipt_agent_boundary \
  tests.test_dedicated_host_preflight_receipt_agent_installation \
  tests.test_dedicated_host_preflight_pinned_ssh_delivery \
  tests.test_dedicated_host_preflight_runtime_transport \
  tests.test_run_dedicated_host_readonly_preflight
git diff --check
```

The new boundary tests use only a fake local root-collector runner. They do
not invoke sudo, SSH, a local collector, an account tool, Docker, Object
Storage, or a remote host.
