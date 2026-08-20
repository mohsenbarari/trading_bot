---
name: native-app-standardization
description: Standardize the Vue PWA to a native iOS/Android UI/UX except Market. Use when restyling webapp surfaces, messenger frontend, forms, shell, settings lists, or executing docs/uiux-native-standardization/ROADMAP.md.
---

# Native App Standardization

## Goal

Make every live webapp surface except Market feel like a standard iOS/Android app. Messenger frontend is fully unlocked. Do not stop for mid-phase owner review.

## Always

- Work on `candidate/webapp-native-controls-v1`
- Touch target 48px; fields use `--ds-control-*` or `App*`
- Product CSS uses `--ds-*` only; no `--ui-v2-*` unless the file is already catalog V2
- One language: 48px, amber, inset, no extra chrome. V2 catalog target stays 48px.
- Keep Market route and market feed frozen
- Keep messenger behavior: legacy default, rollback, `album_id` + `album_index`
- Restyle AdminMessages, TradingSettings, and Jalali to the same 48px language; keep calendar confirm and historical hashes readable
- Do not change backend, auth, or business logic
- Do not push, merge, or deploy unless the user asks

## Messenger

Frontend restyle is free. Add or keep `native-app-messenger-visual-v1` so Stage 4 guards accept visual drift. Do not rewrite schema, websocket, upload, or album detection.

## Sequence

Follow `docs/uiux-native-standardization/ROADMAP.md` phases 0–11 without waiting between phases. Run `npm run guard:ui` and focused Vitest after each visual slice.

## Done when

Non-market surfaces share one native language, messenger `M01`–`M14` look native, Market is unchanged, and guards/tests pass.
