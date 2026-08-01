# Dedicated-host preflight pinned-SSH delivery contract

`core.dedicated_host_preflight_pinned_ssh_delivery` is a narrow adapter for
the controller's `pinned-ssh-readonly-agent` contract.  It is intentionally
not an SSH client: importing it, constructing it, and running its unit tests
open no socket and execute no process.  A separately reviewed integration may
inject one async runner, but the adapter constructs all runner input itself.

This boundary is for the disposable Finland roles only:

- `bot_fi`
- `webapp_fi`
- `witness`

`webapp_ir` is refused before any runner or filesystem operation with
`PINNED_SSH_WEBAPP_IR_OBJECT_STORAGE_PULL_REQUIRED`.  Its controller contract
is the private/versioned Object Storage pull-agent route; this adapter must
never become a Finland-to-Iran transport path.

## Enablement and trust boundary

The adapter is disabled by default.  An integration must explicitly construct
`PinnedSshReadonlyDeliveryConfig(enabled=True)` and run as root.  It accepts
only an injected object implementing `async run(invocation=...)`; there is no
subprocess, shell, network, SSH-library, credential, provider, Docker, or
Object Storage code in this module.

The injected runner is part of the trusted deployment integration.  It must
directly execute the supplied immutable argument tuple, with the supplied
stdin bytes and no ambient credential/agent substitution.  It must not log
stdin, stdout, host-key data, or any extra diagnostic output.  It returns only
an integer exit status and bounded stdout bytes.  The adapter accepts a zero
exit status only, parses stdout as the canonical preflight receipt, and emits
fixed-code errors rather than forwarding runner errors or stdout.

Nothing here authorizes a host, service, Docker, provider, data, or network
mutation.  A successful receipt is still only read-only observation evidence.

## Immutable transport selection

For every permitted role, the adapter independently rechecks the controller
target against the source-owned disposable identity and controller delivery
contract.  The caller cannot select or alter any of the following:

- executable: exactly `/usr/bin/ssh`;
- OpenSSH configuration: exactly `-F /dev/null` (no ambient user config);
- user: exactly `preflight`;
- port: exactly `22`;
- remote command: exactly `collect-readonly-receipt`;
- logical receipt path: the controller's role-specific
  `RECEIPT_PATH_BY_ROLE` value;
- endpoint: the source-pinned public IPv4 for that role;
- timeout: exactly five seconds with one connection attempt;
- environment: empty;
- host-key source: the dedicated fixed known-hosts path.
- client identity: one dedicated fixed root-only private-key path.

The argv includes batch mode, no password or keyboard-interactive auth, no
agent forwarding, no local command, no TTY, no global known-hosts file, no host
key update, and strict host-key checking.  It selects exactly the fixed client
identity with `-i` and does not search any ambient default identity location.
Request bytes must be the exact
canonical `READONLY_REQUEST_SCHEMA` object with only its six prescribed
fields; arbitrary commands, URLs, credentials, extra fields, malformed JSON,
or a mismatched role/hash are rejected before the runner receives anything.

## Root-owned host-key pin

The only allowed known-hosts location is:

```text
/etc/trading-bot/security/dedicated-host-preflight/known_hosts
```

The only allowed client-identity location is:

```text
/etc/trading-bot/security/dedicated-host-preflight/identity_ed25519
```

Before every invocation, the adapter requires a root-owned, single-link,
regular file with mode exactly `0600`; every ancestor must be root controlled
and non-group/world-writable (a sticky root-owned `/tmp` is allowed only for
local test fixtures).  Symlinks, path replacement races, oversized files,
comments, hashed/wildcard/multi-host aliases, duplicate hosts, non-ASCII data,
and unsupported key types are refused.

The file contains exactly three entries, in source role order: `bot_fi`,
`webapp_fi`, then `witness`.  Each entry uses the corresponding source-pinned
public IPv4 as its sole hostname and one of `ssh-ed25519`,
`ecdsa-sha2-nistp256`, or `ssh-rsa`.  The decoded SSH wire blob must begin with
the declared key type.  The controller's `host_key_sha256` is the lowercase
hex SHA-256 digest of that decoded wire blob (not a URL, a PEM fingerprint, or
an untyped display string).  The target entry must match it exactly.

The root-owned provisioning step that creates this file is outside this local
adapter and must obtain/verify the host keys and dedicated preflight identity
through the approved bootstrap process.  Both fixed files are root-owned,
single-link regular `0600` files beneath root-controlled non-writable
ancestors.  The identity material never appears in an invocation repr, receipt,
exception, or output.  Do not place tokens, URLs, or production host details
in either file or in receipt delivery output.

## Controller response and redaction

On success the adapter returns only the exact
`AGENT_DELIVERY_RESPONSE_SCHEMA` wrapper required by
`DedicatedHostPreflightController`: role, fixed route/phase, typed host-key and
request hashes, fixed logical receipt path, receipt hash, and raw canonical
receipt bytes.  It never returns argv, executable path, user, environment,
runner exception, stderr, or secret-shaped stdout.  Failures use stable error
codes such as `PINNED_SSH_KNOWN_HOSTS_INVALID`,
`PINNED_SSH_HOST_KEY_PIN_MISMATCH`, and `PINNED_SSH_RECEIPT_INVALID`.

Run the local contract tests with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_dedicated_host_preflight_pinned_ssh_delivery
```
