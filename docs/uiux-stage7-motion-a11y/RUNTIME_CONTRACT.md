# Runtime contract — Stage 7 Phase 1

- Copy is opt-in. Global `html, body { user-select: none }` stays. Product copy uses `.app-copyable-info .app-route-scroll` plus V2/portal `user-select: text`.
- Protected-legacy shells (`/market`, `/chat`, `/admin/channels`, `/share-receive`) never receive `app-copyable-info`.
- Mixed interiors `/admin/messages` and `/admin/system` are excluded by route name so market/messenger delivery chrome is unchanged.
- Buttons, tabs, tablists, icon-buttons, action-cards stay unselectable.
- `AppTabs` always moves focus after Arrow/Home/End. `AppFilterChips` still requires `focusSelectionOnKeyboard`.
- Jalali day arrows move focus only; they do not emit `update:modelValue`.
- `AppEmptyState` is `role="status"`. Errors stay `role="alert"`. Loading stays `role="status"` + `aria-live="polite"`.
- Reduced-motion collapses `.fade-*` and `.ui-tabs__tab` transitions. V2 motion tokens already collapse to 1ms on V2 scopes.
- No PII, token, or raw server detail in URL/history/storage/DOM of the Stage 7 harness.
