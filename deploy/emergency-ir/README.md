# Emergency IR Standalone

This is a temporary, isolated Iran-only WebApp deployment.  It is not a
three-site role, a standby writer, a sync peer, or a replacement for the
Release-0/Full-Matrix promotion path.

## Security and ownership contract

- The application image must be built from the attested production base
  `2c08da14bfa0ef94d9c788e478d30ddc3f31a3c5` plus an immutable Emergency
  patch.  `verify_emergency_ir_image_provenance.py` rejects an unlabelled,
  staging, or mis-tagged image.
- Docker uses only `trading-bot-emergency-ir-*` names, its own internal
  `172.29.250.0/28` network, and its own volumes.  Never point it at a
  three-site path, volume, network, image, secret, or Compose project.
- The application network is internal-only.  There is no bot, sync worker,
  DR worker, writer service, or outbound service credential.  Direct sync is
  additionally disabled by `TRADING_BOT_DISABLE_DIRECT_SYNC_PUSH=true`.
- `WEBAPP_INITDATA_BOT_TOKEN` is a narrowly scoped Telegram WebApp HMAC
  validation credential, not `BOT_TOKEN`.  It is used only by the explicit
  Emergency image and never starts a bot process.

## Auth and snapshot contract

Restore the encrypted production snapshot only into the fresh Emergency
PostgreSQL volume.  Before migration/API start, run
`reset-emergency-sessions.sql` against that database.  It deactivates copied
sessions and makes pending requests terminal without deleting users, accounts,
or changing `home_server`; users must obtain fresh Emergency tokens.

The Emergency frontend automatically attempts a Telegram WebApp login only
when Telegram provides signed `initData`; its normal OTP UI remains a fallback.
As shipped, the isolated runtime has no SMS credential or external egress, so
the Telegram path covers only Telegram-linked accounts.  Do not claim a
web-only/SMS account is ready until a separate, reviewed domestic-SMS plan has
been installed and tested.

## Optional local SMS OTP profile (off by default)

`docker-compose.sms-otp.yml` is a separate opt-in overlay; it is never loaded
by the normal standalone command.  Its services all use the `sms-otp` Compose
profile, so merely combining the files without `--profile sms-otp` starts no
Emergency service.  The default rendered runtime has
`EMERGENCY_AUTH_PROFILE=telegram-only` and contains no SMS.ir credential.

The SMS profile uses the existing Stage-6 one-code state machine in direct-SMS
mode: `TELEGRAM_LOGIN_OTP_ENABLED=true` is the legacy switch that enables that
state machine, while `OTP_SMS_AUTO_FALLBACK_ENABLED=false` prevents every
Telegram delivery, peer request, and background fallback path.  Telegram
WebApp initData validation remains available for the existing Telegram login
path; no `BOT_TOKEN` or bot process is introduced.

Only the `sms-egress` container has an external Docker network.  The API has
only its normal internal network plus a private relay bridge, and is pinned to
`http://sms-egress:8080` with ambient proxy handling disabled.  The relay
configuration is baked into its separately attested image, does TLS/SNI
verification for `api.sms.ir`, and exposes exactly one operation: `POST
/v1/send/verify`.  It is not a generic HTTP proxy and must never receive a
host configuration bind mount or published port.

The profile requires a fresh, encrypted transfer of the SMS.ir API key and an
attested relay image through the same private Object Storage flow as the rest
of the Emergency package.  WA-IR must not pull either image or package from a
registry during activation.  The renderer accepts the WebApp validation token
and SMS.ir API key only as one JSON stdin payload, never as command-line
arguments, and writes them only to the root-owned runtime environment:

```text
{"webapp_initdata_token":"...","smsir_api_key":"..."}
```

Use `--enable-sms-otp --sms-otp-secrets-stdin`, plus the exact SMS template ID
and relay image tag matching the Emergency patch.  Before any Compose start,
the activator runs the sealed `verify_emergency_ir_standalone.py --profile
sms-otp` with the base Compose file, the SMS overlay, `sms-egress.nginx.conf`,
and `nginx.sms-otp.rate-limit.conf`; it then runs
`verify_emergency_ir_sms_egress_image.py`.  Start it only with both Compose
files and `--profile sms-otp`.

For ingress, install `nginx.sms-otp.rate-limit.conf` in Nginx's `http {}`
scope and use `nginx.sms-otp.conf.template` in place of—not in addition to—
the default standalone vhost.  Only the three OTP endpoints get the dedicated
per-source-IP limit; every sync, peer, registration, internal, and metrics
route remains blocked.  The app also retains its normal per-mobile and
per-verification controls.

This is intentionally not a promise that `api.sms.ir` will be reachable after
an Iran connectivity incident.  Before selecting this profile, perform the
approved local relay/provider delivery preflight from WA-IR and record its
result.  If that preflight cannot reach the provider, leave the default
Telegram-only profile active rather than bypassing the fixed relay or enabling
a generic proxy.

## Transfer and activation order

1. Transfer every artifact through the private, versioned Arvan Object Storage
   bucket.  The sealed manifest binds exactly the encrypted image bundle,
   package tar, database snapshot, and settings tar to immutable VersionIds,
   hashes, the WA-IR age recipient, and allowlisted Emergency inbox paths.
2. Verify the manifest with its pinned Ed25519 public key before downloading.
   Verify ciphertext and plaintext hashes after download/decryption.
3. Load images, render a fresh root-only runtime environment locally, then run
   the sealed standalone semantic verifier and the sealed image-provenance
   verifier(s) before *any* Docker/Compose operation.  A verifier failure
   creates no Emergency volume, network, or container.  Only then restore the
   snapshot, run the session-reset SQL, migrate, and verify API health.
4. Verify the local Certbot `live` symlink only through its root-controlled
   `archive` target.  A regular file at the `live` leaf is rejected.  The
   activator then creates a root-only, non-symlink pinned certificate and
   private-key pair under `/etc/trading-bot-emergency/standalone/tls/`.
   Nginx consumes only that pinned pair, never Certbot's mutable live links.
5. Run the separately confirmed `firewall` stage before any Nginx change.  It
   records an immutable, strict WA-IR prearm baseline: UFW 0.36 with IPv6
   enabled, incoming deny, and only the paired `three-site-wa-ir-control`
   SSH rule; no pre-existing owned, broad, or other TCP 80/443 rule is allowed.
   It also captures root-controlled UFW static files and raw
   `iptables-save`, `ip6tables-save`, and `nft` state.  Only live packet/byte
   counters are normalized; all rules remain bound.  A raw direct public
   TCP 80/443 path is rejected, while the separate protected staging mapping
   from host TCP 8443 to its private container is not treated as public
   80/443 ingress.
6. Before switching ingress, inventory `nginx -T`.  The normal sole Debian
   default vhost with only port 80 is acceptable only because that exact
   root-owned default symlink is transactionally journaled and restorable;
   any additional effective 80/443 vhost blocks prearm.  Create a new,
   immutable attempt intent, then recoverably replace the default symlink,
   enable/start or reload Nginx as needed, and require the rendered candidate
   to be the sole owner of both 80 and 443.  Make a direct local CA-validated
   TLS/SNI request for `coin.gold-trade.ir`; `/api/config` must return 200 and
   `/api/sync` must remain 404, and protected three-site listeners on
   8213/8443 must remain healthy.  Failures before the durable
   `ufw-pending` receipt restore the default and record the attempt as
   aborted.  Only after that exact receipt is durable can the one bounded
   dual-stack UFW TCP 80,443 command run.
7. Only then is a DNS A-record switch to `95.38.164.29` the sole cutover
   action.

The DNS-01 certificate currently expires on 2026-10-30.  Renewal needs a
manual DNS challenge before that date; it is not an automatic renewal setup.

## Fail-closed local activation

`scripts/emergency_ir_standalone_activate.py` is included in the pinned
receiver bootstrap as well as the sealed release package.  It has no Object
Storage, SSH, registry, or DNS client: it consumes only the four ciphertexts
already received in the fixed Emergency inbox.  First run its plan mode on
WA-IR as root:

```text
python3 -I -B /run/trading-bot-emergency-bootstrap/<campaign>/receiver/scripts/emergency_ir_standalone_activate.py --campaign <campaign>
```

The result contains a distinct confirmation phrase for each stage.  Copy the
phrase for exactly one stage into `--confirm`, together with `--apply` and
`--stage prepare`, then repeat in order for `images`, `database`, `api`,
`tls`, `firewall`, and finally `prearm`.  The activator re-checks the pinned
manifest/public key, age recipient identity, ciphertext hash/size, and
plaintext hash/size before it decrypts or uses an artifact.  It uses
create-only files and receipts, so a failed attempt is intentionally not
retried by overwriting state.

The `prepare` stage accepts the following exact uncompressed `settings.tar`
member layout and nothing else:

```text
trading_settings.json
webapp_initdata_token
```

For the separately selected `--profile sms-otp`, it additionally requires
exactly:

```text
smsir_api_key
smsir_otp_template_id
smsir_otp_template_parameter
```

Secrets are read from these root-only artifact members into the renderer's
stdin; they are never accepted on a command line or emitted in a receipt.
The SMS profile also refuses Nginx/UFW prearm without a root-only canonical
provider-preflight receipt for the same campaign.

Before Docker/Compose can create anything, the image tar is inspected for the
exact app tag, provenance labels, isolated image namespace, expected image
count, regular layers, and no pre-existing target tags.  After rendering, the
package's standalone semantic verifier and app/SMS image verifier(s) run from
the sealed root-only package.  The database stage refuses an existing
Emergency volume/network/container, starts only DB and Redis, restores the
custom dump, runs session reset, then runs migration.  It never uses a broad
`compose up` before this sequence.

The `tls` stage accepts only Certbot's normal `live` terminal symlinks as a
local source whose resolved root-owned archive target remains inside the exact
Emergency archive directory.  It rejects a regular `live` leaf, verifies the
leaf/key pairing, exact DNS SAN, and a seven-day validity margin, then creates
fixed pinned TLS files and a campaign receipt.  The `firewall` stage is an
explicit human-confirmed baseline, not a claim that arbitrary post-UFW raw
netfilter changes can be inferred later.  It must be re-read exactly before a
fresh Nginx transaction begins and immediately before the PONR receipt.

The ingress stage creates an attempt-specific root-only intent journal before
it moves the existing default Nginx *symlink* to an attempt-specific backup.
Candidate-test, lifecycle, listener-inventory, TLS-probe, staging-health, and
every other failure before the durable `ufw-pending` receipt restore that
default by rename and write an immutable `aborted` receipt.  If Nginx began
disabled/inactive, a failed prearm stops/disables only the service action it
attempted, returning it to that prior lifecycle.  A fresh attempt rejects even
an exact pre-existing Emergency UFW 80/443 rule; it may start only from the
closed attested baseline.

`ufw-pending` is the sole point of no return: after it is durable, the tool
never guesses that a failed command had no side effect, never restores Nginx,
and never deletes or repeats a UFW rule.  A later invocation of the same
confirmed `prearm` stage performs only exact read-only verification: pinned
TLS, rendered-config digest, Nginx link/backup layout, enabled/active
lifecycle, sole candidate 80/443 listener inventory, direct TLS/SNI routes,
protected staging listeners, and the paired active IPv4/IPv6 UFW rule against
the recorded baseline.  Only if all checks match may it create the attempt's
`armed` and final `prearmed` receipts; otherwise it fails closed for manual
recovery.  The activator never deletes the prior configuration, a Docker
resource, or a UFW rule.

## Rollback

Keep the existing Nginx default-site backup and do not delete any Docker
volume. To withdraw Emergency ingress, restore the Nginx symlink/configuration
from that backup and test/reload it only through an audited manual procedure.
Do not remove an existing UFW rule merely because it has the Emergency port
shape: consult the immutable prearm journal first. Preserve the Emergency
volumes, release directory, and journals for forensic/manual recovery.
