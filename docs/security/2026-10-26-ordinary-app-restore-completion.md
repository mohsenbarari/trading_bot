# Actual ordinary App restore completion

Global security acceptance is still false. Serving App, Market and datastores
have not been replaced. Writer/DNS are unchanged and refactor automation remains
stopped; its required consecutive accepted-unit streak has not started.

Both fresh O10 requests completed with actual outer controller return code zero:

| Site | Restore nonce | Original completion UTC | Result |
| --- | --- | --- | --- |
| Native | `5c3532cca4a44777b2613410e094f1a5` | 2026-10-26 06:04:52 | passed |
| Web | `ed808acbeaa24d89b905f19d22e317a6` | 2026-10-26 06:04:27 | passed |

The installed candidate ran with the normal Env, entrypoint and command against
owned restored DB/Redis/uploads/audit. Its installed management command wrote
the synthetic record; stores restarted and the old installed image read the
same candidate-written data. Schema/head, persistence and complete owned
container/volume/network/firewall cleanup passed. Live model contents, Market
output equivalence, serving validation and provider sync were explicitly not
claimed. Original production data and services were unchanged.

Source cohort: operator `e9876ce7…`, producer `b9df3507…`, restore contract
`49983650…`, normal admission `26547560…`, shared contract `c07784fd…` and site
`d6c0f59c…`. The old failed O7 request remains failed; no nonce was resumed.
The successful executions passed the previously failing OOM readiness boundary.

Root retained the actually received final O10 stdout from completed local and
SSH calls, then copied only the public control/intent/result/terminal/LOAD
records with retained FD checks. Separate closed Root completion receipts bind
actual final controller/transport status zero. Inner terminal success alone was
not used. The public assembler preserves the earliest original completion time.

The assembler's nine public Root fixtures passed: manifest
`d010fff9de5885116a485e073cfb31c2f24e3cc45f85141371740f988fde10a2`,
result `b1da62e62546c00d1468d55443a8f1eaac0b70f630f6e4bb287a6d0d5527d9fb`.
Actual assembly `eb05495a298f423abafb6fee1769f2e5` succeeded and remains
**unarmed**; it grants no serving authority. Backup05's actual creation/expiry
remain 02:48/06:48 UTC; gate assembly never refreshes protected backup age.

## Exact absent-unit metadata

Fresh Native read-only systemctl observations of the known retired
`coin-capture.service` and `market-channel-capture.service` returned absent:
LoadState `not-found`, empty UnitFileState/FragmentPath/DropInPaths, inactive/dead,
MainPID and ControlPID zero, NeedDaemonReload `no`. The normal App validator now
admits that exact nine-field absence tuple. The existing parked-unit path stays
separate, and a nonempty named inventory remains required.

The unchanged runtime unit barrier compares every captured property exactly.
Unit appearance, enablement, PID, fragment/drop-in or state drift refuses.
No active Market collector was classified as legacy and no absent unit was
assigned a fictional enablement value. C/S, normal configurations and O10
evidence were unchanged.

All 18 affected normal App tests passed in the genuine Root/CAP_CHOWN/NNP,
separate-init, read-only, network-none fixture lane: manifest
`ab41aae2360d3f1594e0a08b9efcdcf30b3bc9b6e0f195e395e3c4fc2d97c563`,
result `fed5ebbc1f928076ea7a10d8d8bc0775be725f5cd5427b9f064a652f8622ad09`.
Actual final status was zero, with no failures, errors or skips.
