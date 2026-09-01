# Runtime and Repository Inventory

Status: sanitised read-only snapshot
Observed: 2026-09-01 UTC

No full environment dump, credential, token, cookie or private key is included.
Counts and sizes are operational observations, not a backup receipt.

## Host capacity

| Host | CPU | RAM / swap | Root disk | Notable risk |
| --- | ---: | ---: | ---: | --- |
| Bot-Finland | 8 vCPU | 15.6 GiB / 3 GiB | 38 GiB; 89% used | about 4.4 GiB free; production and staging co-resident |
| Web-Finland | 4 vCPU | 7.7 GiB / 8 GiB | 150 GiB; 42% used | about 4 GiB swap in use; obsolete three-site stack active |
| Finland target | 16 vCPU | 31.3 GiB / none | 301 GiB; about 286 GiB free | unprovisioned; firewall/SSH hardening and load acceptance pending |

## Current application runtime

| Component | Bot-Finland | Web-Finland |
| --- | --- | --- |
| API | producer-oriented `SERVER_MODE=foreign` | Web runtime `SERVER_MODE=iran` |
| Bot | sole production Bot, runtime role `all` | absent by design |
| Queue worker | active | Web/local workers only |
| Sync worker | active | active |
| PostgreSQL / Redis | role-local instances | role-local instances |
| Product estimator mode | `LEGACY` | `LEGACY` |
| App migration | `ff6c7d8e9f01` | `ff6c7d8e9f01` |
| Release SHA | `e533d415f1fe085a251d8e6df2016fa775d86702` | same |
| Shared sync backlog | zero at observation | zero at observation |

The two hosts use different role-specific application image IDs even though
they report the same release SHA. That is not automatically a fault, but the
future artifact manifest must make role, source commit and digest explicit.

### Application database shape

| Metric | Bot-Finland | Web-Finland |
| --- | ---: | ---: |
| Database size | about 173 MiB | about 60 MiB |
| Model tables | 60 runtime tables | 60 runtime tables |
| Users | 149 | same shared count at parity audit |
| Offers | 1,463 | same shared count at parity audit |
| Offer requests | 184 | same shared count at parity audit |
| Trades | 214 | same shared count at parity audit |
| `change_log` | about 45,207 rows / 83 MiB | local bookkeeping; not merge truth |
| Telegram delivery jobs | about 11,230 rows / 24 MiB | local table empty/not authoritative |
| Telegram notification outbox | about 10,243 rows / 12 MiB | shared business rows converged |

Web-local state observed on Web-Finland includes 111 sessions, 45 push
subscriptions, 34 login requests, 11 messages, 9 chats and 2 chat files.
Telegram execution tables are local to Bot-Finland. These facts are why a
whole-database last-write-wins merge is prohibited.

## Market runtime

Bot-Finland currently runs:

- coin estimator;
- estimator snapshot sender;
- Market store adapter;
- Market fact receiver on a private address;
- snapshot relay/bridge and sync-health timers.

Web-Finland currently runs:

- Market PostgreSQL;
- two account capture paths plus external capture;
- fact sync worker;
- estimator snapshot receiver;
- Market processor.

The Web-Finland Market database is about 3.95 GiB. Its largest observed tables
are `market_fact_outbox` (~1.37M rows / 2.08 GiB), `market_fact_revisions`
(~1.36M / 589 MiB), `market_facts` (~1.13M / 899 MiB), raw research messages
(~205k / 102 MiB) and fact raw messages (~236k / 56 MiB). Market history needs
its own retention/replay plan and must not be folded into the application merge
as an incidental volume copy.

Several production Market mounts still contain names such as `staging-shadow`.
The observed services may be production-critical despite the name; rename or
cleanup requires reference and replay validation first.

## Non-production runtime on production hosts

- Bot-Finland still runs staging application, database, Redis, sync and Market
  bridge components.
- Web-Finland still runs staging components and an obsolete three-site WebApp,
  writer-control, effects, DR, TLS, database and Redis stack.

These are capacity, ownership and accidental-execution risks. They are
decommission candidates, not authorization to stop or delete them.

## Repository and host-side storage

| Location/class | Observed size/state | Initial classification |
| --- | --- | --- |
| tracked repository | 3,093 files; `.git` about 165 MiB | keep; review tracked evidence separately |
| `frontend/node_modules` | about 571 MiB, ignored | reproducible cache; retention required |
| `tmp/production-release` | about 1.2 GiB, ignored; 6,106 files | quarantine/delete candidate after reference check |
| `mutants/` | about 22 MiB, ignored | generated test artifact candidate |
| `.local/` | about 4.4 MiB | canonical local-artifact root; enforce retention |
| `/srv/trading-bot/backups` on Bot-Finland | about 2.2 GiB | classify by restore/protection/expiry before cleanup |
| `/srv/trading-bot/releases` on Bot-Finland | roughly 760 MiB across observed release dirs | protect active and rollback releases |

Only one Git worktree is present. Current local branches are `main`, the plan
branch and `candidate/wa-ir-standby-v1`; the candidate remains local-only by
explicit owner decision.

## Read-only parity receipt

All 23 registered sync tables were compared with a row cap of 50,000 per table.
The result was:

```text
status: non_business_difference
business_drift: 0
critical_drift: 0
incomplete_tables: 0
duplicate_records: 0
missing_records: 0
```

Eighteen tables contained differences only in fields classified as local or
volatile. This is a point-in-time audit, not permission to assume future cutover
parity; the rehearsal and final drain still require fresh receipts.
