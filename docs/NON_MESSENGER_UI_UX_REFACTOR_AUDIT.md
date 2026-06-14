# Non-Messenger UI/UX Refactor Audit

Last updated: 2026-06-14

## Scope

This document tracks the mobile-first refactor of all PWA surfaces outside the internal messenger experience.

Included:
- Global authenticated shell and bottom navigation
- Dashboard/home
- Market entry points and market shell links
- Profile, public profile, account settings
- Customer/accountant management entry points
- Notifications
- Admin panel, invitations, users, commodities, system settings, admin messages
- Shared route/deep-link behavior for the sections above

Excluded from this refactor slice:
- `frontend/src/views/MessengerView.vue`
- `frontend/src/components/ChatView.vue`
- `frontend/src/components/chat/**`
- `frontend/src/composables/chat/**`
- `frontend/src/services/chat/**`
- Chat event gateway, message stores, chat virtualization, media renderer internals

Messenger compatibility that remains in scope:
- Bottom navigation item for `/chat`
- Chat unread and mention badges in `BottomNav.vue`
- Global shell services that already run outside the route view

## Current Route Map

| Route | Name | Purpose | Refactor Treatment |
|---|---|---|---|
| `/` | `home` | Dashboard, current-day trades, market entry, profile shortcut | Keep as primary Home tab |
| `/market` | `market` | Market and offer workflow | Keep as primary Market tab |
| `/chat` | `messenger` | Messenger | Keep route; internal UI untouched |
| `/operations` | `operations` | New operational hub | Added as primary Operations tab |
| `/account` | `account` | New account hub | Added as primary Account tab |
| `/profile` | `profile` | Current user's profile and owner actions | Keep; supports `workspace=customers/accountants` |
| `/settings` | `settings` | Sessions, storage, logout | Keep; supports `section=sessions/storage` |
| `/admin` | `admin` | Admin sections | Keep; supports safe `section=...` deep links |
| `/users/:id` | `public-profile` | Public/customer/user profile | Keep as deep link |
| `/notifications` | `notifications` | Notification center | Keep as account/operations shortcut |
| `/i/:code` | `invite-landing` | Invitation landing | Keep public |
| `/register` | `web-register` | Web registration | Keep public |
| `/share-receive` | `share-receive` | PWA share target | Keep hidden utility route |

## Bottom Navigation Contract

Primary tabs:
1. `خانه` -> `/`
2. `بازار` -> `/market`
3. `پیام‌رسان` -> `/chat`
4. `عملیات` -> `/operations`
5. `حساب` -> `/account`

Rules:
- Accountants still do not see the Market tab.
- Messenger unread/mention badges remain on the Messenger tab and FAB menu.
- Market and Messenger keep the collapsed draggable FAB behavior to preserve screen space.
- Legacy direct routes remain valid; no old deep link should break.
- Admin is no longer a sixth bottom-tab item. It is reachable through `/operations` and `/admin`.

## Current Feature Entry Points

### Dashboard

Current functions:
- Notification shortcut
- Profile shortcut
- Logout for non-accountants
- Market entry, disabled/blocked states
- Restricted/inactive account warnings
- Current-day trade summary
- Super-admin/dev account switching

Treatment:
- Dashboard remains the lightweight landing page.
- Operational shortcuts move toward `/operations`.
- Account-level shortcuts move toward `/account`.

### Profile / Public Profile

Current functions:
- Self profile view
- Settings action
- Customer manager modal
- Accountant manager modal
- Visitor actions: message, block/unblock where allowed
- Admin user settings action for admin visitors
- Trade history, relations, project user directory

Treatment:
- Existing UI and permissions remain.
- `/profile?workspace=customers` opens the existing customer manager.
- `/profile?workspace=accountants` opens the existing accountant manager.
- Public profile route remains unchanged.

### Settings

Current functions:
- Active sessions for non-accountants
- Storage/cache management
- Logout for non-accountants

Treatment:
- Existing accordions remain.
- `/settings?section=sessions` opens sessions when the current user is allowed.
- `/settings?section=storage` opens storage.

### Admin

Current functions:
- Invitation creation
- User management
- Commodity management
- Admin messages for super admin
- System settings for super admin
- User profile sub-view

Treatment:
- Existing components remain.
- `/admin?section=create_invitation`
- `/admin?section=manage_users`
- `/admin?section=manage_commodities`
- `/admin?section=admin_messages`
- `/admin?section=settings`
- `/admin?section=user_profile&user_id=...`

System settings and admin messages remain guarded by the existing super-admin check.

### Customer Manager

Current functions:
- Add customer
- Commission and limit fields
- Pending invitations and expiry/cancel actions
- Customer list
- Customer detail/edit page
- Trade history
- Stats and commission profit reporting
- Session termination
- Relationship unlink/cancel

Treatment:
- Existing behavior remains.
- Entry point is available from `/operations` and `/profile`.
- Visual consolidation is a later stage; this slice only standardizes access.

### Accountant Manager

Current functions:
- Add accountant
- Pending invitations
- Accountant list
- Accountant detail/edit page
- Session termination
- Relationship unlink/cancel

Treatment:
- Existing behavior remains.
- Entry point is available from `/operations` and `/profile`.
- Visual consolidation is a later stage.

### Notifications

Current functions:
- List, refresh, delete, clear, read/unread
- Route-based open action

Treatment:
- Existing behavior remains.
- Entry point appears in `/account`; also available from dashboard as before.

## Role/Permission Notes

| Role/State | Important UI Rules |
|---|---|
| Accountant | Market tab hidden; sessions/logout remain hidden in settings |
| Customer | Owner relation management should not be promoted |
| Middle manager | Admin operation shortcuts limited to invitation and user management |
| Super admin | Full admin operation shortcuts |
| Normal user | Operations hub promotes owner relation management and notifications |

## Risks And Guardrails

- Do not duplicate business permissions in hub cards. Hub cards may hide/promote actions, but backend and existing component checks remain authoritative.
- Do not move messenger state or chat rendering during this non-messenger slice.
- Do not remove old URLs. The refactor must be additive until all flows are verified.
- Avoid adding a new global state layer for this slice; use existing `currentUserSummary` and route guards.
- Keep bottom nav labels short enough for mobile width.

## Proposed Next Stages

Stage N1 - Audit and additive navigation:
- Add route map documentation.
- Add `/operations` and `/account`.
- Update bottom nav to the 5-tab product model.
- Add query-driven openings for existing sections.
- Status: completed in commit `0ac5a1e`.

Stage N2 - Dashboard simplification:
- Reduce dashboard to status, today's work, and primary market/profile signals.
- Move operational density to `/operations`.
- Status: completed. Dashboard now keeps market entry and today's trades as primary content, while routing relation/admin/account work through `/operations` and `/account` shortcut cards.

Stage N3 - Operations hub refinement:
- Convert admin/customer/accountant actions into consistent accordion groups.
- Add role-aware empty states and disabled explanations.
- Status: completed. `/operations` now uses project-standard accordion groups for relations, management, and shortcuts, with customer/non-admin empty states and middle-manager scope notes.

Stage N4 - Account hub refinement:
- Standardize settings, sessions, storage, notifications and profile links.
- Keep accountant restrictions visible and consistent.
- Status: completed. `/account` now uses project-standard accordion groups for profile/settings, security/data, and notifications. Accountant sessions remain unavailable, but the restriction is shown as an explicit empty state while storage and allowed settings remain accessible.

Stage N5 - Public profile/customer/accountant visual consolidation:
- Align customer and accountant management details with the same accordion spacing, typography, and action hierarchy.
- Preserve all current actions.
- Status: completed. Public profile customer/accountant relation cards and owner customer/accountant management cards now share the token-based relation-card treatment for spacing, radius, border, shadow, and typography. Existing profile navigation and management actions were preserved.

Stage N6 - Admin workspace consolidation:
- Standardize admin section headers, empty states, and save feedback.
- Keep existing section components and permission gates.
- Status: completed. The admin landing workspace now uses project-standard intro and accordion groups for access/users, catalog, and super-admin system tools. Admin sub-section headers include concise descriptions, route-profile loading uses a standard empty-state card, and system-settings calendar save/delete paths now use the same viewport feedback toast as primary settings saves. Existing admin route guards and component-level permission gates were preserved.

Stage N7 - Responsive and accessibility pass:
- Check text fit, focus states, tap targets, RTL alignment, and keyboard navigation.
- Status: completed. Account, operations, admin, settings, and trading-settings accordions now expose explicit `aria-expanded` / `aria-controls` / `region` relationships, settings/trading settings accordion headers are real keyboard-focusable buttons, shared accordion headers and primary action buttons have stronger focus-visible/tap-target behavior, and focused unit coverage verifies the expanded/collapsed accessibility state. Messenger internals were not touched.

Stage N8 - Regression and production rollout:
- Run focused unit/build checks.
- Run high-risk e2e flows only where UI contracts changed.
- Commit, push, deploy, and record the change.
