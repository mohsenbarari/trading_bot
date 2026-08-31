# Finland Primary target

Status: created, reachable, access not established, not production-authoritative
Last verified: 2026-08-31

## Approved target

- Role: sole planned Finland Primary
- IPv4: `65.109.214.203`
- Intended services after an approved migration: WebApp/API, Telegram bot,
  background workers, PostgreSQL, and Redis
- Iran remains the planned WebApp standby; this document does not activate or
  promote either host.

## SSH reachability scenario

Precondition: a new server was created and the project owner authorized a
read-only SSH attempt.

Observed result:

- Port 22 answered and presented an ED25519 host key.
- Observed fingerprint: `SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs`
- Authentication failed for both `root` and `ubuntu` with the currently
  available SSH identities: `Permission denied (publickey,password)`.
- No remote command executed, so hostname, operating system, resources, disk,
  firewall, packages, and service state remain unknown.

## Security gate before the next attempt

1. Verify the observed ED25519 fingerprint out of band through the hosting
   provider console or rescue environment.
2. Confirm the intended SSH account.
3. Install an approved public key through the provider console; do not copy a
   private key into the repository, prompt, log, or temporary directory.
4. Repeat a read-only inventory before any provisioning.

## Read-only inventory after access is established

- identity: hostname, OS/release, kernel, timezone, and architecture
- capacity: CPU, memory, disks, filesystems, and free space
- network: addresses, routes, DNS, listening ports, firewall, and SSH policy
- runtime: Docker/Compose, systemd state, Nginx, PostgreSQL/Redis presence
- security: pending updates, automatic updates, time synchronization, and
  provider firewall status

Provisioning, package installation, SSH hardening, data transfer, DNS changes,
deploy, and production cutover each require a separate approved plan.
