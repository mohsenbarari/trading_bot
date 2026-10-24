# Web kernel reboot: actual mount union and finite boot-ordering proposal

Status: read-only inventory, **not** a reboot-ready or applied receipt. Captured
`2026-10-24T03:49:22Z` on Web `65.109.220.59:37067`; kernel
`7.0.0-27-generic`, 27 existing containers, 26 running and one stopped Iran
migration. Subsequent maintenance must recapture this baseline rather than
reuse the following as current authority. No Writer, DNS, capture owner,
container policy, mount, service, package or host file was changed.

## Actual filesystem closure

| Backing filesystem | UUID | Observed device / primary inode |
| --- | --- | --- |
| Root `/`, ext4 `/dev/sda1` | `dce03124-bc1e-47f1-8767-b5bf64a7664a` | 2049 |
| `/srv/trading-bot-three-site-staging-data`, ext4 `/dev/sdb` | `e7f47458-ffc1-4181-81e1-58c6fc4519a2` | 2064 / 2 |

The attached primary is a real root-owned `0755` mount, with an enabled native
`srv-trading\x2dbot\x2dthree\x2dsite\x2dstaging\x2ddata.mount` unit,
`What=/dev/sdb`, and no pending daemon reload. The source/UUID and original
mount-unit bytes must be rebound in a fresh manifest; `/dev/sdb` alone is not
durable identity. Root mount identity is independently required.

All 44 unique host mount sources from **all 27**, including stopped containers,
are below. Only paths and stat/mount metadata were inspected: no secret,
environment, database, session, argv or configuration payload was read.
`R` and `V` mean the root and attached UUIDs above. Each `dev:inode` is a
capture-time identity, not a portable inode requirement after unrelated moves.

| Host source | FS / dev:inode | UID / mode |
| --- | --- | --- |
| `/etc/trading-bot-three-site/secrets/staging-dr-blob-keyring.json` | R / 2049:689848 | 0 / 600 |
| `/etc/trading-bot-three-site/secrets/staging-dr-blob-s3.json` | R / 2049:689847 | 0 / 600 |
| `/etc/trading-bot-three-site/secrets/staging-dr-ca.crt` | R / 2049:689844 | 0 / 644 |
| `/etc/trading-bot-three-site/secrets/webapp-fi-dr.crt` | R / 2049:689845 | 0 / 644 |
| `/etc/trading-bot-three-site/secrets/webapp-fi-dr.key` | R / 2049:689846 | 0 / 600 |
| `/srv/trading-bot-three-site/current/deploy/staging/nginx/webapp-fi-dr.conf` | V / 2064:1048995 | 0 / 644 |
| `/srv/trading-bot/market-data-production/snapshots` | R / 2049:947348 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/calibration/coin-groups` | R / 2049:817678 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/capture` | R / 2049:817673 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/capture/account1` | R / 2049:817674 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/capture/account2` | R / 2049:817675 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/capture/external` | R / 2049:817676 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/postgres` | R / 2049:817672 | 70 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/sessions/account1` | R / 2049:817680 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/sessions/account2` | R / 2049:817681 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/state/estimator-snapshot-receiver` | R / 2049:817690 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/state/market-capture-account1` | R / 2049:817685 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/state/market-capture-account2` | R / 2049:817686 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/state/market-capture-external` | R / 2049:817687 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/state/market-fact-sync-worker` | R / 2049:817689 | 10001 / 700 |
| `/srv/trading-bot/market-data-staging-shadow/state/market-processor` | R / 2049:817688 | 10001 / 700 |
| `/srv/trading-bot/production-data/coin-intelligence/production-runtime` | R / 2049:812227 | 0 / 755 |
| `/srv/trading-bot/secure/market-data/account1-config.json` | R / 2049:947605 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/account2-config.json` | R / 2049:947606 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/account2-peer-hmac` | R / 2049:947607 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/hmac-active` | R / 2049:947619 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/hmac-previous` | R / 2049:947620 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/postgres-password` | R / 2049:947604 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/research-archive.key` | R / 2049:947615 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/transport-ca.pem` | R / 2049:947616 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/web-transport-cert.pem` | R / 2049:947617 | 0 / 440 |
| `/srv/trading-bot/secure/market-data/web-transport-key.pem` | R / 2049:947618 | 0 / 440 |
| `/srv/trading-bot/staging-data/coin-intelligence` | R / 2049:820364 | 0 / 755 |
| `/var/lib/docker/volumes/current_audit_data/_data` | R / 2049:651252 | 0 / 755 |
| `/var/lib/docker/volumes/current_postgres_data/_data` | R / 2049:648588 | 70 / 700 |
| `/var/lib/docker/volumes/current_redis_data/_data` | R / 2049:648617 | 999 / 755 |
| `/var/lib/docker/volumes/current_uploads_data/_data` | R / 2049:651250 | 0 / 755 |
| `/var/lib/docker/volumes/three-site-727c1f337b75-webapp-fi_webapp_fi_postgres/_data` | V / 2064:2490371 | 70 / 700 |
| `/var/lib/docker/volumes/three-site-dc32d90392b0-webapp-fi_webapp_fi_audit/_data` | V / 2064:1312021 | 0 / 755 |
| `/var/lib/docker/volumes/three-site-dc32d90392b0-webapp-fi_webapp_fi_postgres/_data` | V / 2064:1310724 | 70 / 700 |
| `/var/lib/docker/volumes/three-site-dc32d90392b0-webapp-fi_webapp_fi_redis/_data` | V / 2064:1310725 | 999 / 755 |
| `/var/lib/docker/volumes/three-site-dc32d90392b0-webapp-fi_webapp_fi_uploads/_data` | V / 2064:1312020 | 0 / 755 |
| `/var/lib/docker/volumes/trading_bot_staging_iran_staging_audit/_data` | R / 2049:1460751 | 0 / 755 |
| `/var/lib/docker/volumes/trading_bot_staging_iran_staging_postgres_data/_data` | R / 2049:1460747 | 70 / 700 |
| `/var/lib/docker/volumes/trading_bot_staging_iran_staging_redis_data/_data` | R / 2049:1460742 | 999 / 755 |
| `/var/lib/docker/volumes/trading_bot_staging_iran_staging_uploads/_data` | R / 2049:1460749 | 0 / 755 |

Five `V` sources below Docker's `volumes/<name>/_data` are existing Docker
**local** volumes with exact `type=none,o=bind`; their `device` paths are
`<primary>/three-site-727c1f337b75/webapp_fi/postgres` and
`<primary>/three-site-dc32d90392b0/webapp_fi/{audit,postgres,redis,uploads}`.
Each generated `_data.mount` has no persistent fragment: Docker creates these
bind mounts. Do not require these child mount units before Docker or introduce
a startup cycle. Revalidate exact volume name, driver, bind options, canonical
backing path and UUID, then gate Docker on the **primary** mount.

The nginx source `/srv/trading-bot-three-site/current/deploy/staging/nginx/
webapp-fi-dr.conf` is an intentional alias. Its actual resolved path was
`<primary>/controller/releases/e53a56119ccc73c84aa12e21697820e7e0a1f90b/
deploy/staging/nginx/webapp-fi-dr.conf`, device 2064/inode1048995. Pin that
resolved target and alias relationship; neither silently rewrite the alias nor
reject it as an unexplained symlink. All other captured sources were canonical.

## Unit dependencies and preserved intent

`docker.service` and `containerd.service` have no existing drop-ins and no
`RequiresMountsFor`. Docker is enabled/active, requires its socket, follows
containerd, and has `TimeoutStopUSec=infinity`; containerd timeout is90seconds.
The attached primary already orders before `local-fs.target`/`multi-user.target`,
but an explicit Docker dependency and mount assertion closes fallback-to-root
risk independently of incidental boot ordering.

| Exact selected unit | Original active / enablement | Existing path dependency |
| --- | --- | --- |
| `coin-capture.service` | inactive / disabled | `/srv/coin_group_capture` |
| `coin-intelligence-capture-shadow-external.service` | inactive / static | `/srv/coin-intelligence-shadow/app` |
| `coin-intelligence-capture-shadow-external.timer` | active / enabled | `/var/lib/systemd/timers` |
| `coin-intelligence-capture-shadow-input.service` | inactive / static | `/srv/coin-intelligence-shadow/app` |
| `coin-intelligence-capture-shadow-input.timer` | active / enabled | `/var/lib/systemd/timers` |
| `containerd.service` | active / enabled | none |
| `docker.service` | active / enabled | none |
| `docker.socket` | active / enabled | `/run/docker.sock` |
| `market-channel-capture.service` | inactive / disabled | `/srv/market_channel_capture` |
| `market-pipeline-watchdog.service` | inactive / static | none |
| `market-pipeline-watchdog.timer` | active / enabled | `/var/lib/systemd/timers` |
| `trading-bot-sync-health-sampler.service` | inactive / static | `/srv/trading-bot/current` |
| `trading-bot-sync-health-sampler.timer` | active / enabled | none |

The two legacy collectors are **disabled/inactive**, with runtime
`market-container-authority.conf` drop-ins and `ConditionResult=no`.
The shadow-input service still Wants both collectors; removing these authority
drop-ins, enabling collectors, or treating timer activation as capture authority
is prohibited. Reboot admission must prove an equivalent persistent inhibitor
survives `/run` loss, or refuse; current runtime-only drop-ins alone are not
a persistent boot guarantee. Preserve the complete original unit graph.

## Smallest implementation / actual reboot sequence

1. Reuse the existing Native boot-gate validator's bounded metadata command
   capture, no-follow public-unit reads, exact owned drop-in creation/cleanup,
   `RequiresMountsFor` plus `AssertPathIsMountPoint`, and post-reload D-Bus
   assertion inspection. Use a **Web-specific** map/UUID; do not execute the
   Native file with Native paths or copy its owner assumptions onto data.
2. Proposed minimal map is `docker.service -> <attached primary>`. Verify all
   44 source paths and five local-volume backing paths remain covered by root
   or that primary. None of the ten selected native unit path properties
   points to this volume; do not add speculative mount dependencies to all ten.
   Root-resident `/srv/trading-bot/market-data-staging-shadow`, production-data
   and `/srv/trading-bot/secure/market-data` are **ordinary directories**, not
   mountpoints. Their legitimate UID10001/70/999 ownership is not a defect.
3. Before any reboot, close legacy collector boot inhibition, original27/26
   identities/images/mounts/policies, eight datastore probes, API health,
   exact native unit enablement, source/config/authority hashes, current
   controller ownership, and no pending unit jobs. Web host metadata proved
   all13 `LoadState=loaded; Job=` renders correspond to D-Bus
   `Unit.Job=(uo) 0 "/"`; do not interpret missing/unknown units as idle.
4. Only after the reviewed CE transaction completes and fresh guards pass,
   stage one bounded external Native-controller Ansible reboot transaction.
   Require installed target kernel/initrd/actual bootloader selection, changed
   boot ID, target kernel, correct root/attached UUIDs and restored boot gates.
   Reuse the original runtime contract for exact27 containers/26 running,
   original images/policies/mounts/network/health and native capture ownership.
   Do not authorize a fresh app/Market release as part of host reboot.
5. Preserve fail-closed intent and evidence on ambiguity. A lost controller
   or missing mount is not permission to adopt another reservation, recreate
   containers, remove authority inhibitors, enable collectors, or extend a
   maintenance/key window. The actual reboot controller and persistent
   collector inhibition are **not implemented by this inventory document**.

## Complete captured container identity set

| Container name | ID | Running / restart policy |
| --- | --- | --- |
| `/market-private-pipeline-primary-market-processor-1` | `f2dbc15b9c9a2d14ef9669d1f6de37096161469a50c47bf6609f0383886b3a27` | yes / unless-stopped |
| `/market-private-pipeline-primary-market-fact-sync-worker-1` | `132962d7bf4dfef52f618f4dbd2601ccf344a3868b4a691e40d37dc25e7e9682` | yes / unless-stopped |
| `/market-private-pipeline-primary-estimator-snapshot-receiver-1` | `f0f5ddc3efe1a55cc8a6f800ad59d1f41556a4f896cdc8ed7d20864c9d360e2a` | yes / unless-stopped |
| `/market-private-pipeline-primary-market-capture-account1-1` | `47f05db122d8e1acde43e8eb75ac55436b44d54de3200cfec6497759ba57a465` | yes / unless-stopped |
| `/trading_bot_sync_worker` | `d2912e20a407fc722a9d64c39011e93e146bdd739145eafb5307ae2096661727` | yes / always |
| `/trading_bot_app` | `1fd03c2fc42401785d6e526eed1b2d7be851e6bc15315ac5b5391e7d8adedf1a` | yes / always |
| `/trading_bot_staging_iran-sync_worker-1` | `a113f73fe1be1cfc778e6f75014db639b286629196930ebe6d331e40f4fd14ee` | yes / unless-stopped |
| `/trading_bot_staging_iran-app-1` | `e56e357ebd9aa31f8379904dd12b0ae5ca3b02ae2b116132ee6c78e5e6f0733c` | yes / unless-stopped |
| `/trading_bot_staging_iran-migration-1` | `b9b5acd06a685476980ed3f2768070d453422f91bb8a277022c4b1f92e9b4d54` | no / no |
| `/market-private-pipeline-primary-market-capture-account2-1` | `9870311065b13646767dfad1e48214d6a231e3898e1f248bb9559edd8926966c` | yes / unless-stopped |
| `/market-private-pipeline-primary-market-capture-external-1` | `731e55357f911c35ebbce383d7f88602cf5d490e4313e36535fb4dd6f01e3b43` | yes / unless-stopped |
| `/market-private-pipeline-primary-market-database-1` | `761ec216c2033dfaee2f1c1f41824fe9eb522515ca3d73ac2ceb2cc861da6087` | yes / unless-stopped |
| `/trading_bot_staging_iran-db-1` | `d013bebcfbaf09e64bc5fe6c4b8a35e5460cb5adaf49ea187150741c60c85b26` | yes / unless-stopped |
| `/trading_bot_staging_iran-redis-1` | `3dac9af2abc551e1f8b3df6ebee4cf60e07c6b6051b95244c80e03e21a9ecb72` | yes / unless-stopped |
| `/trading_bot_db` | `6c3259a1edd7ff6e5ef82a42da8417cdba8c17f138352fa697a0bc6f3daaa1f1` | yes / always |
| `/trading_bot_redis` | `12b43529d96f0c7cf2c9120c010a3e3999956e39842386ce2f7db420545854d2` | yes / always |
| `/three-site-727c1f337b75-webapp-fi-webapp_fi_db-1` | `707155a1c3d71bb4b18c41fc2673ce01af3ce269bd2ba24bfbd0cee826cbf9f5` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_effects-1` | `5a77d25b37bddbc28c36f7f3e4ddcad7da15ce609e191462d1ac584c525c5ec0` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_api-1` | `f82b0e9cad3219b3549e7350d023a6eaa4938570523a7cf563e8e4aeaa7b0384` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_blobs-1` | `26dd7428182e66afd2ce28c85fc78f30f61a2ea323b2fe21b3f7a69892acff95` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_dr_delivery-1` | `061af20a47a4d604f1950da7a27a67f77411b376eaa46899cf5e005f5244c870` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_writer_control-1` | `0b1c1ac348b0cc804c04ef1b0a421d161ce1fe262ff43666ba8a9a0bdceec61d` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_dr_projection-1` | `ab19a2dca24f4b4de6c5f3e87566af1c3956948b94e3fc2b99579e56a20eb535` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_dr_receiver-1` | `6baa54ddf92657de1fd063eefa45084472e66438ff2a4859fbd277fe161bdcdf` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_db-1` | `b7f75f523fbbc8783b9affed1cac9b5f030f045036e89670204fe6fee15f77a6` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_dr_tls-1` | `596ea9221bb8a471f6718e0df0a82e14b9d7b8ab1299aedbc3a73d8b6511f7fe` | yes / unless-stopped |
| `/three-site-dc32d90392b0-webapp-fi-webapp_fi_redis-1` | `e36fc1f59172ad0a60fdd14209379ef1a153467a4642fccf4ef267ca6997f87e` | yes / unless-stopped |

Evidence method: existing strict SSH trust and public-format Docker projections
(never whole inspect), systemctl selected properties, `findmnt`/stat metadata,
five `docker volume inspect --format` path-only projections, and independent
public D-Bus `Unit.Job`. No scanner upload, image download or runtime operation.
