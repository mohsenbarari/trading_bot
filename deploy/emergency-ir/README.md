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
and relay image tag matching the Emergency patch.  Before starting anything,
run `verify_emergency_ir_standalone.py --profile sms-otp` with the base
Compose file, the SMS overlay, `sms-egress.nginx.conf`, and
`nginx.sms-otp.rate-limit.conf`; then verify the relay image with
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
3. Load images, render a fresh root-only runtime environment locally, restore
   the snapshot, run the session-reset SQL, migrate, and verify API health.
4. Only after all local checks pass, install the Nginx configuration.  Move the
   pre-existing Nginx default site to a recoverable backup; never delete it.
   Test Nginx, then open UFW TCP 80/443.
5. Confirm the exact certificate/domain with `--resolve`, the blocked sync and
   OTP routes, and that existing three-site containers on 8213/8443 remain
   healthy.  Only then is a DNS A-record switch to `95.38.164.29` the sole
   cutover action.

The DNS-01 certificate currently expires on 2026-10-30.  Renewal needs a
manual DNS challenge before that date; it is not an automatic renewal setup.

## Rollback

Keep the existing Nginx default-site backup and do not delete any Docker
volume.  To withdraw Emergency ingress, restore the Nginx symlink/configuration
from that backup, test it, reload Nginx, and remove only the two Emergency UFW
rules after confirming the intended alternate ingress.  Preserve the Emergency
volumes and release directory for forensic/manual recovery.
