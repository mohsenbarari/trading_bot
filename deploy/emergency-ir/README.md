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
4. Verify the local Certbot `live` symlink only through its root-controlled
   `archive` target, then create a root-only, non-symlink pinned certificate
   and private-key pair under `/etc/trading-bot-emergency/standalone/tls/`.
   Nginx consumes only that pinned pair, never Certbot's mutable live links.
5. Before switching ingress, create an immutable local prearm-intent journal.
   Only after all local checks pass, recoverably replace the Nginx default
   symlink, enable/start or reload Nginx as needed, and make a direct local
   CA-validated TLS/SNI request for `coin.gold-trade.ir`. The probe requires
   `/api/config` to return 200 and `/api/sync` to remain 404; it also rechecks
   protected three-site listeners on 8213/8443. Only then does it preserve an
   already-owned exact UFW rule or add the one bounded TCP multiport rule for
   80,443.
6. Only then is a DNS A-record switch to `95.38.164.29` the sole cutover
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
`tls`, and finally `prearm`.  The activator re-checks the pinned manifest/public key,
age recipient identity, ciphertext hash/size, and plaintext hash/size before
it decrypts or uses an artifact.  It uses create-only files and receipts, so a
failed attempt is intentionally not retried by overwriting state.

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

Before any Docker mutation, the image tar is inspected for the exact app tag,
provenance labels, isolated image namespace, expected image count, regular
layers, and no pre-existing target tags.  The database stage refuses an
existing Emergency volume/network/container, starts only DB and Redis,
restores the custom dump, runs session reset, then runs migration.  It never
uses a broad `compose up` before this sequence. The `tls` stage accepts
Certbot's normal `live` symlinks only as a local source whose resolved
root-owned archive target remains inside the exact Emergency archive directory.
It verifies the leaf/key pairing, exact DNS SAN, and a seven-day validity
margin before creating fixed pinned TLS files and a campaign receipt. The
ingress stage creates a root-only `prearm-intent` journal before it moves the
existing default Nginx *symlink* to a root-only backup. Candidate-test,
lifecycle, TLS-probe, staging-health, and failures before a possible UFW
mutation restore that default by rename. If Nginx began disabled/inactive, a
failed prearm stops/disables only the service action it attempted, returning
it to that prior lifecycle.

After the UFW arm point, the tool never guesses that a failed command had no
side effect and never deletes a rule. It preserves the journaled candidate and
requires a later invocation of the same confirmed `prearm` stage to perform
only exact read-only verification: pinned TLS, rendered-config digest,
Nginx link/backup layout, enabled/active lifecycle, direct TLS/SNI routes,
protected staging listeners, and the exact active UFW rule. Only if all of
those checks match may it create the `prearm-armed` and final `prearmed`
receipts; otherwise it fails without repeating a Nginx or UFW mutation. An
already-present exact owned UFW rule is recorded as such and not added,
changed, or deleted; an unowned overlapping 80/443 rule blocks prearm for
manual review. The activator never deletes the prior configuration, a Docker
resource, or a UFW rule.

## Rollback

Keep the existing Nginx default-site backup and do not delete any Docker
volume. To withdraw Emergency ingress, restore the Nginx symlink/configuration
from that backup and test/reload it only through an audited manual procedure.
Do not remove an existing UFW rule merely because it has the Emergency port
shape: consult the immutable prearm journal first. Preserve the Emergency
volumes, release directory, and journals for forensic/manual recovery.
