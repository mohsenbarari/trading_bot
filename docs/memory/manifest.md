# Memory Manifest

Loading map for local project memory. Load only the files listed for the current task plus explicitly requested optional modules.

## MemoryCustodian Protocol
- protocol_version: 0.5
- initialized_with: memory-custodian 0.9.1
- last_migrated_with: memory-custodian 0.9.1

## Always load
- brief.md

## Load by task

### Planning / architecture / refactoring
Load:
- decisions.md
- constraints.md
- do-not-use.md

### Implementation / execution / debugging
Load:
- decisions.md
- constraints.md
- do-not-use.md
Load if present:
- preferences.md

### User-facing artifact / output
Load:
- do-not-use.md
Load if present:
- rules/output.md
- preferences.md

### Preferences
Load if present:
- preferences.md

### Change history / recap
Load:
- decisions.md
Load if present:
- changelog.md

### Memory maintenance
Load:
- inbox.md
- do-not-use.md
Load if present:
- changelog.md

## Optional module index
Discover optional memory without loading it. Entries here are not default loads.

### Enabled rules
- None enabled.

### Enabled profiles
- None enabled.

### Enabled areas
- `areas/frontend-uiux.md` — load for Vue/PWA UI/UX refactoring, Design System V2, Figma/Sites evidence, or shared UI changes that can affect protected surfaces.
- `areas/coin-market-intelligence.md`: load for estimator, Market Store,
  coin-group parsing/trade linking, and public market ingestion work.
- `areas/offer-price-control.md` — load for estimator-range offer rejection,
  price-control tolerances, or offer-price guard rollout work.
- `areas/telegram-delivery.md` — load for Queue-v1, Telegram execution ownership, bot/API delivery boundaries, OTP delivery, or Telegram runtime cutover work.

## Optional rules
`rules/` files load only when listed above and the task clearly matches.

## Optional profiles
`profiles/` files load only when listed above and the workflow clearly matches.

## Area-specific memory
`areas/` files load only when listed above and the touched files or task scope match.

## Explicit only
- archive/

## Context budget
- brief.md: 500 tokens max
- decisions.md: 800 tokens max
- constraints.md: 400 tokens max
- do-not-use.md: 400 tokens max
- preferences.md: 300 tokens max, if present
- rules/*.md: 400 tokens max per file, if present
- profiles/*.md: 500 tokens max per file, if present
- areas/*.md: 600 tokens max per file, if present
- changelog.md: 800 tokens max, if present
- inbox.md: memory maintenance only
- archive/: explicit only
