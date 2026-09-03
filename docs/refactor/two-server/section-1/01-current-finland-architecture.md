# Current Finland Architecture Dossier

Status: historical baseline accepted; refreshed evidence and Codex Final Review pending
Observed: 2026-09-01 UTC

## Human scenario

Today “Finland” is two different production authorities, not two identical
copies. One host owns Telegram/Bot execution and Finland-side Market estimation;
the other owns WebApp, sessions, Messenger and the Market fact archive/capture
pipeline. Their application databases exchange selected shared tables, while
surface-local state stays on the role that created it.

The target must place both roles on one physical server without collapsing them
into one process or erasing their behavioral differences:

```text
CURRENT                                      TARGET (after approved cutover)

Bot-Finland ---- selected DB sync ---- Web-Finland     Finland Primary
  Bot/API                                  Web/API       ├── Web/API process
  Bot workers                              Web workers   ├── Bot process (sole Telegram owner)
  app DB/Redis                             app DB/Redis  ├── capability-scoped workers
  estimator/snapshot                       Market DB     ├── one app PostgreSQL + Redis
                                           capture       └── consolidated Market data plane
```

“One server” therefore means one failure domain and one shared application data
plane, not one executable. Web/API, Bot, workers, database, Redis and Market
services remain separately observable and restartable.

## Observed hosts

| Logical role | Identity | Runtime responsibility | Application data |
| --- | --- | --- | --- |
| Current Bot-Finland | `ubuntu-4gb-hel1-1` / `65.109.216.187` | Bot, API producer, Telegram delivery/publication, sync worker, estimator/snapshot | 173 MiB PostgreSQL; 60 tables |
| Current Web-Finland | `iran` / `65.109.220.59` | Web/API, Web jobs, sync worker, Market capture/archive/processor | 60 MiB app PostgreSQL; 60 tables; 3.95 GiB Market PostgreSQL |
| New Finland Primary | `ubuntu-32gb-hel1-1` / `65.109.214.203` | none yet | none yet; unprovisioned |

The current Web host's hostname and multiple path/container labels say `iran` or
`three-site`; in this document it is called **current Web-Finland** according to
its observed function. Those historical labels are migration inputs, not target
role names.

## Application path today

1. Web mutations enter Web-Finland. Sessions, Messenger/uploads and Web Push are
   local there; selected business tables emit durable sync records.
2. Bot mutations enter Bot-Finland. Telegram delivery, callback state and
   publication execution are local there; selected business tables also sync.
3. Both databases apply shared-table changes and retain their own `change_log`,
   watermarks and blocks. Internal bookkeeping is not business truth.
4. A complete read-only comparison of all 23 sync tables found equal business
   rows/counts. Differences were limited to explicitly local/volatile fields.
5. Market fact capture/archive is concentrated on current Web-Finland; snapshot
   estimation and relay are concentrated on current Bot-Finland.

## Authority today

| Authority | Current owner | Target rule |
| --- | --- | --- |
| Telegram Bot token/session/executor | Bot-Finland only | exactly one Bot process on Finland Primary |
| Web sessions, OTP/SMS, Messenger and Web Push | Web-Finland local runtime | Web/API capability on Finland Primary |
| Telegram delivery/publication jobs | Bot-Finland | `TELEGRAM_OWNER`, independent of Web Writer |
| Web delivery/account/connectivity jobs | Web-Finland | explicit Web/local capability, not a historical server label |
| Offer expiry | each host for its home offers | explicit home/authority rule; no double scheduler |
| Market schedule | split: Web authority plus Bot-local consequences | one command path with separated authoritative and side-effect capabilities |
| Cross-Finland sync | both local outbox workers | removed only after a shared database is proven |

## Failure meaning after consolidation

- Web/API process failure must not restart or disable Bot.
- Bot failure must not make Web/API unavailable.
- PostgreSQL/Redis/host failure affects both and therefore requires tested
  backup/restore and later Iran Web continuity; it cannot be hidden by process
  isolation.
- Web Writer fencing during an Iran handover must not disable valid Bot-home
  mutations on Finland.
- No current host may remain an unfenced Telegram executor or scheduler after
  cutover.

## Baseline conclusion

The consolidation is feasible, but a raw restore of both databases into one is
incorrect. The merge must use one canonical copy of the 23 shared tables,
selectively import surface-local state, rebuild internal sync bookkeeping and
handle the Market archive under a separate contract. The source-provenance issue
in `06-current-drift-register.md` is a blocker before rewriting any historical
`home_server` value.
