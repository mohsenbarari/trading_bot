---
name: native-app-standardization
description: Standardize the Vue PWA to a native iOS/Android UI/UX except Market. Use when restyling webapp surfaces, messenger frontend, forms, shell, settings lists, or executing docs/uiux-native-app-v2/ROADMAP.md.
---

# Native App Feel V2

## Goal

Make every live webapp surface except Market feel like a standard iOS/Android app. Messenger frontend is fully unlocked. Do not stop for mid-phase owner review.

Previous tracks unified 48px controls. This track closes the remaining native-feel gap: inset grouped lists, page gutters, button overflow, copy diet, and immersive messenger chrome.

## Always

- Work on `candidate/webapp-native-app-v2`
- Follow `docs/uiux-native-app-v2/ROADMAP.md` and `docs/uiux-native-app-v2/LANGUAGE.md`
- Touch target 48px; fields use `--ds-control-*` or `App*`
- Lists use inset grouped rows with 16px page gutter; cards do not flush to the screen edge
- Button labels stay inside the control; long Persian text wraps or moves into an overflow menu
- Product CSS uses `--ds-*` only; no `--ui-v2-*` unless the file is already catalog V2
- Keep Market route and market feed frozen
- Keep messenger behavior: legacy default, rollback, `album_id` + `album_index`
- Restyle AdminMessages, TradingSettings, and Jalali chrome; keep calendar confirm and market interiors
- Do not change backend, auth, or business logic
- Do not push, merge, or deploy unless the user asks

## Messenger

Frontend restyle is free. Keep `native-app-messenger-visual-v1` so Stage 4 guards accept visual drift. Hide the global navigation FAB on `/chat`. Do not rewrite schema, websocket, upload, or album detection.

## Sequence

Follow `docs/uiux-native-app-v2/ROADMAP.md` phases 1–11 without waiting between phases. Run `npm run guard:ui` and focused Vitest after each visual slice.

## Done when

Non-market surfaces share one native language, owner native-feel score can reach a passing grade, messenger `M01`–`M14` look native, Market is unchanged, and guards/tests pass.
