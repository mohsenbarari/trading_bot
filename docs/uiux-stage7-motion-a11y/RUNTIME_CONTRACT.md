# Runtime contract — Stage 7 Phase 1

- Copy is opt-in. Global `html, body { user-select: none }` stays. Product copy uses `.app-copyable-info .app-route-scroll` plus V2/portal `user-select: text`.
- Protected-legacy shells (`/market`, `/chat`, `/admin/channels`, `/share-receive`) never receive `app-copyable-info`.
- Mixed interiors `/admin/messages` and `/admin/system` are excluded by route name so market/messenger delivery chrome is unchanged.
- Buttons, tabs, tablists, icon-buttons, action-cards stay unselectable.
- `AppTabs` always moves focus after Arrow/Home/End. `AppFilterChips` still requires `focusSelectionOnKeyboard`.
- Jalali arrow navigation is default-off. UserProfile/PublicProfile opt in; TradingSettings does not. In opted-in calendars arrows move focus only and do not emit `update:modelValue`.
- `AppEmptyState` has no default role. Approved Stage 7 call sites opt in to `status`; protected Market/CreateChannel consumers stay inert. Errors stay `role="alert"`; loading stays `role="status"` + `aria-live="polite"`.
- Unscoped routes keep the stable transition name `fade`. Reduced-motion eligibility is stored on each keyed route vnode with `app-reduced-motion-route`; only `protection:none` + `v2Scope:section` enter/leave collapse to `0ms`. Protected/full/mixed routes keep legacy `200ms`, including cross-boundary navigation.
- V2 route-scoped motion tokens continue to collapse independently inside their existing V2 scope.
- The Stage 4 guard checks shared dependencies and explicit call-site opt-ins without rebasing protected Market/Messenger/Home/AdminMessages/TradingSettings hashes.
- No PII, token, or raw server detail in URL/history/storage/DOM of the Stage 7 harness.
