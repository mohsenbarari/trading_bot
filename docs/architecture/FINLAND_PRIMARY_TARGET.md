# Finland Primary target

Status: accessible, inventoried read-only, unprovisioned, not production-authoritative
Last verified: 2026-09-01 UTC

## Approved target

- Role: sole planned Finland Primary
- IPv4: `65.109.214.203`
- Intended services after an approved migration: WebApp/API, Telegram Bot,
  background workers, PostgreSQL, Redis and the Finland-side Market services
- Iran remains the planned WebApp standby. Nothing in this document activates,
  promotes or provisions either host.

## Verified identity and capacity

The approved SSH identity authenticated as `root` and only read-only inventory
commands were run. The host presented the previously observed ED25519 host key:

```text
SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs
```

| Property | Observed value |
| --- | --- |
| Hostname | `ubuntu-32gb-hel1-1` |
| Operating system | Ubuntu 26.04 |
| Kernel | `7.0.0-29-generic` |
| CPU | 16 vCPU |
| RAM | 31.3 GiB |
| Swap | none |
| Root filesystem | 301 GiB total; about 286 GiB free |
| Public IPv4 | `65.109.214.203/32` |
| Time | UTC; NTP synchronized |
| Listening services | SSH on port 22 only |
| Container runtime | Docker/Compose not installed |
| Application services/data | none observed |

Raw capacity is materially above either current Finland host, but this is not a
capacity acceptance test. `P1-06` still requires a production-shaped load test,
soak and explicit resource budgets before production cutover.

## Security and provisioning blockers

The host is intentionally treated as unprovisioned. Read-only inspection found:

- UFW is inactive and no effective nftables rules were observed.
- SSH effective policy permits password authentication and X11 forwarding;
  root login is limited to public-key authentication (`prohibit-password`).
- the `ssh` unit is active but not enabled as a conventional boot service;
  socket/service activation and reboot behavior must be tested explicitly.
- unattended upgrades are enabled and active; 13 cached upgrades were pending at
  inspection time.
- no swap exists; an explicit memory-pressure policy is required.
- Docker/Compose, reverse proxy, application directories, monitoring, backup and
  restore tooling are absent.

These are provisioning inputs, not authorization to change the server. The
approved hardening baseline must define provider firewall, host firewall, SSH
policy, update/reboot policy, least-privilege operators, secret mounts, disk and
volume layout, time sync, audit logging, alerting and recovery access.

## Next gates

1. Approve the target security/capacity contract in `P1-02` and `P1-03`.
2. Provision through an auditable, idempotent path; do not configure manually
   without recording the equivalent automation.
3. Run target topology and failure-isolation tests without production secrets or
   external side effects.
4. Complete data-merge rehearsal, staging parity, load, soak, backup/restore and
   rollback gates before any production cutover.

Package installation, SSH hardening, data transfer, DNS changes, deploy and
production cutover each require a separately approved operational Task Card.
