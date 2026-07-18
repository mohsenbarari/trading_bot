# Stage 0B-1 Authentication — External Review Findings Disposition Register

Date: 2026-07-18

Design snapshot reviewed: `4058ffb51dfef0194df3ac8b2c824654e51dac2d`

Checkpoint: `docs/WEBAPP_UI_UX_REDESIGN_V2_STAGE0B_AUTH_CHECKPOINT_20260718.md`

## Purpose and authority

This is the durable, tracked disposition record for the Stage 0B-1 external-review cycle. It records the executor's evidence-based response to every formally identified finding, the bounded remediation applied to the artifact, and the work that must be carried into implementation acceptance. The revised checkpoint remains subject to product-owner approval.

The raw reviewer reports remain untracked review inputs. They are not copied into `docs/` and are not made part of the product history. This register, rather than the ignored raw reports, is the authoritative carry-forward source for later prompts and checkpoints.

## Report provenance

| Source label used in this register | Raw report SHA-256 | Treatment |
| --- | --- | --- |
| ChatGPT Ultra (`Ultra1`) | `4c9a24368f46e31939715bf01685b7e4f5d8c65f143f0fbc333efd7abd18f21d` | Independent product/contract/UX review; nine formal findings. |
| Report 2 (`hybrid`) | `98ed9afbe92d8c12f159bc4a2d0eaae119de47be4e44d625c9625147a520945c` | Supplemental report with eleven formal findings; useful as corroborating evidence only. |
| Gemini | `13c47c5361ae5f88988c6788cd275caa46154fac30eaac0699a6a62fe0de996b` | Independent engineering review; three formal findings. |
| Claude | `19664afd0f8cebc2edeb2f6b604c8ab2d7f4943b28a2e99d297105c476d38d0a` | Independent feasibility/regression review; fourteen formal findings. |

**Report 2 is not a valid independent ChatGPT Pro report.** Its own title and reviewer field identify ChatGPT Ultra, despite being returned for the Pro review slot. It therefore cannot satisfy the independent-Pro requirement or be counted as a distinct Pro opinion. Its non-duplicate observations are retained below under the explicit source label `hybrid`; this does not repair its provenance.

## Disposition meanings

- `accepted`: the finding or guardrail is adopted within the stated checkpoint boundary.
- `partially accepted`: a supported part is adopted, while an overbroad claim or unsupported remedy is explicitly declined.
- `rejected`: the finding is contradicted by inspected evidence or does not apply.
- `deferred`: the issue is legitimate but its decision or remediation belongs to a named later stage.

Acceptance of a finding does not mean that the current static artifact or runtime already implements it. The carry-forward column identifies the required closure point.

## Proposed checkpoint decisions used for remediation

1. The public shell contains only login, invitation landing, and Web registration. `/setup-password` remains authenticated; its final shell is a Stage 0B-2 decision.
2. `REG-01` has exactly three mandatory stages, followed by a separately presented optional Telegram suggestion. `REG-02` is direct completion after login verification: it has no fake `3/3` history and no Back path into a duplicate registration OTP.
3. Modern Finance tokens and layout variants are scoped to the auth/public-shell UI family. Stage 0B must not retokenize `:root`, and the public shell reserves no bottom-navigation space.
4. Login and registration OTP may share presentation, but each remains one semantic input with flow-specific behavior.
5. Web-registration persistence across Back, refresh, and temporary failure is an implementation acceptance requirement, not proof of current `WebRegister` runtime behavior.
6. Registration mobile values are masked in the rendered UI. The current token-bearing payload is tracked separately as security/privacy debt; client-side display masking does not require an endpoint change in this design stage.
7. Polling copy never exposes a hard-coded cadence. Address and identity-document copy is truthful and bounded by evidenced use and policy.
8. The required structural frames and the state atlas must be completed. One refined dense-recovery desktop proof is sufficient.
9. Evidence language is bounded to the exact elements, dimensions, browser behavior, and tests actually measured.

## Gemini findings

| Finding | Disposition | Evidence-based reason | Carry-forward |
| --- | --- | --- | --- |
| `GEMINI-AUTH-01` | `deferred` | `/setup-password` is and remains an authenticated route. The current checkpoint intentionally approves only its form/security gate; neither a standalone public-style canvas nor the final authenticated wrapper has been approved. | Decide the final authenticated setup-password shell in Stage 0B-2 and add shell-routing regression coverage then. |
| `GEMINI-AUTH-02` | `rejected` | `SetupPassword.test.ts` already exercises a frontend-valid password followed by a mocked backend `400` response and asserts that the API detail is displayed and submit is re-enabled. `SetupPassword.vue` already catches and renders the API error. The claimed missing catch-all behavior is therefore not present. | Preserve that behavior during refactoring; no new finding-specific work is required. |
| `GEMINI-AUTH-03` | `rejected` | The prototype already places transitions inside `@media (prefers-reduced-motion: no-preference)`. A user requesting reduced motion receives no transition, so a separate `reduce` override is not required to satisfy the stated intent. | Preserve the no-preference-only motion rule in implementation and verify it in the later motion/accessibility gate. |

## Claude findings

| Finding | Disposition | Evidence-based reason | Carry-forward |
| --- | --- | --- | --- |
| `F-01` | `partially accepted` | The displayed REG-02 mobile must be masked, and the current token-bearing response creates payload debt. However, the checkpoint's public-invitation privacy clause governs the anonymous public invitation surface, not every token-bearing registration response. Client-side masking can make the approved display truthful without changing the endpoint in this stage. | Mask registration mobile in the UI; separately track full-mobile/raw-invitation-token payload minimization as security/privacy debt. Do not imply that the payload is already minimized. |
| `F-02` | `partially accepted` | Current `WebRegister` state is not persistent, so the prototype must not present Back/refresh survival as runtime proof. The report's broader statement that no auth view wires Back is incorrect: `LoginView` imports `useBackButton`, pushes an OTP Back handler, and provides `goBackToMobile`. | Reclassify Web registration persistence as an implementation acceptance requirement; test Back/refresh/error restoration, and ensure REG-02 never navigates to duplicate registration OTP. |
| `F-03` | `accepted` | The prototype's password visibility text is not a semantic or measured 44px control, while the action is important on mobile. The existing runtime button supplies a useful semantic baseline but is also smaller than the accepted 44px target. | Refine the SEC-01 frames and later implementation to use labelled semantic buttons with at least 44×44px hit areas; include them in accessibility measurement. |
| `F-04` | `accepted` | The two-second interval is an internal implementation detail and can drift because of backoff, browser throttling, or network delay. The durable user promise is automatic status updating without manual refresh. | Remove exact cadence from all user-facing copy and keep timing details only in implementation/tests. |
| `F-05` | `accepted` | Anonymous invitation and registration currently fall through the authenticated shell gate. A closed public-shell mapping corrects that defect while minimizing protected-surface risk. | In Stage 0B-2, use additive route metadata or an equivalent closed mapping for login/invite/register; retain authenticated setup-password and add per-route shell tests including Market and Messenger. |
| `F-06` | `accepted` | Market consumes shared UI primitives and the current global token set. Retokenizing `:root` would change a protected surface and would also leave hard-coded amber literals inconsistent. | Scope Modern Finance tokens to auth/public shell. Do not change `:root`; treat global token cleanup/rebrand as separate work requiring Market sign-off. |
| `F-07` | `accepted` | A public shell without a bottom navigation must not retain either of the current bottom-nav padding reservations. Leaving them would waste substantial mobile height and invalidate the approved density. | Make the reservation conditional on actual bottom-nav presence and add mobile/keyboard regression coverage in implementation. |
| `F-08` | `partially accepted` | A shared segmented OTP presentation is appropriate only if backed by one real input with per-flow semantics. The current registration input's lack of `autocomplete` is not a binding design contract and, by itself, does not require copying login WebOTP behavior. | Implement/test one semantic OTP primitive with separate login and registration modes; login keeps `one-time-code`/WebOTP behavior, while registration stays numeric and exactly five digits without inheriting login semantics accidentally. |
| `F-09` | `partially accepted` | The current global layout tokens do not reproduce the proof exactly, but the remedy is not limited to reusing or extending `ds-workspace`. A scoped auth variant within the existing `ui-*` family is allowed and does not constitute a third global design system. | Produce one refined dense-recovery desktop proof and implement it later with scoped UI-family tokens/classes; introduce no global prefix and make no `:root` token change. |
| `F-10` | `accepted` | A visibility button nested inside a wrapping label can create ambiguous activation/focus behavior. The risk is implementation-specific and does not require changing `AppFormField` defaults used elsewhere. | Place the password toggle outside the label's interactive subtree or provide a targeted non-wrapping mode; add focus and accessible-name tests in implementation. |
| `F-11` | `partially accepted` | The current flex/overflow measurement can under-detect natural-content pressure, so it is too weak for a general no-clipping claim. The report's assertion that the predicate literally cannot fail is too categorical: it can fail when measured content actually produces scroll overflow under different structure/style conditions. | Replace or supplement it with natural-height/slack measurement across target widths and error states; report the exact method and bounds rather than an absolute guarantee. |
| `F-12` | `partially accepted` | The package explicitly disclosed that the 32-target metric covered only manually marked `.touch-target` elements, so it was not presented as an exhaustive semantic-control audit. The password visibility omission is nevertheless real, and author-selected marking is insufficient for future accessibility acceptance. | Measure all semantic interactive controls in the refined artifact/implementation, or publish an explicit exclusion list; include password toggles. |
| `F-13` | `accepted` | The multi-width sweep covered horizontal overflow but did not establish natural vertical fit at every width, where Persian wrapping can increase height. | Run the corrected natural-height/error-state measurement at 360, 375, 390, 414, and 430px and publish bounded results. |
| `F-14` | `accepted` | The ignored review package had no tracked location for durable dispositions. This register supplies that missing source of truth while keeping raw reports outside version control. | Update this file after every later review round; carry prior IDs and reasons into subsequent reviewer prompts. |

## ChatGPT Ultra (`Ultra1`) findings

| Finding | Disposition | Evidence-based reason | Carry-forward |
| --- | --- | --- | --- |
| `AUTH-UX-01` | `accepted` | A REG-02 user arrived through login verification and did not traverse the first two REG-01 screens. Showing two completed registration steps or an unexplained `3/3` is false progress. | Add a distinct full REG-02 frame labelled as direct completion after login verification, with no duplicate OTP Back destination. |
| `AUTH-UX-02` | `accepted` | Recovery can be authoritatively unavailable because of role/session policy, request state, or reviewer availability. A full state is needed to preserve the still-valid device-approval context and safe next action. | Add a full recovery-unavailable frame with bounded variants and only existing actions; do not invent a support/recovery workflow. |
| `REG-UX-03` | `accepted` | A numeric `4` visually makes Telegram part of the required sequence even when surrounding text calls it optional. | Remove numeric stage treatment; show Telegram only after registration/session completion as a separate optional recommendation with a non-penalizing skip. |
| `AUTH-STATE-04` | `accepted` | The current atlas omits structural recovery, upload, Telegram-link, password, and terminal states needed to judge error hierarchy and retained context. | Add full frames for structural denial/mismatch cases and expand the atlas to cover the checkpoint state matrix before design closure. |
| `INV-COPY-05` | `accepted` | No current self-service route issues a replacement invitation, so an action labelled “get a new invitation” invents behavior. Absolute “non-transferable” language also exceeds the evidenced contract. | Replace it with bounded guidance to contact the inviter/manager and factual advice not to share the link. |
| `VIS-UX-06` | `partially accepted` | Critical helper/privacy/expiry copy must be comfortably readable on the 95%-mobile surface. The report provides no device study that justifies a universal 12px numeric floor for every secondary label. | Refine scoped typography/contrast for consequential text, allow bounded scrolling instead of compression, and validate at target widths; do not alter global `:root` tokens. |
| `A11Y-UX-07` | `accepted` | Password visibility is a real mobile action and was omitted from both semantic markup and the marked-target metric. | Render and later implement it as a labelled 44×44px button and include it in the semantic target audit. |
| `AUTH-COPY-08` | `accepted` | Exact polling cadence is neither a stable nor useful user guarantee. | Use automatic-update/no-manual-refresh copy throughout the flow. |
| `SHELL-IMPLEMENTATION-09` | `accepted` | The current name-based shell gate is insufficient for the approved three-route public shell and creates indirect shared-shell regression risk. | Implement an explicit closed shell mapping in Stage 0B-2 and test public, authenticated, Market, and Messenger route behavior. |

## Report 2 (`hybrid`) findings

These findings are dispositioned because they add or corroborate useful evidence. Their inclusion does not make Report 2 an independent Pro review.

| Finding | Disposition | Evidence-based reason | Carry-forward |
| --- | --- | --- | --- |
| `AUTH-UX-01` | `accepted` | The combined REG-01/02 frame gives REG-02 false history and an undefined Back destination. This independently corroborates Ultra1's same-ID issue. | Add the path-specific direct-completion frame and prohibit Back into registration OTP. |
| `AUTH-STATE-01` | `accepted` | The mandatory matrix contains several structure-changing states not present in the evidence, including Web-only invitation, recovery denial, and password/recovery errors. | Supply the required full structural frames and expanded atlas with retained-data, action, focus, and terminal-path notes. |
| `AUTH-PRIV-01` | `accepted` | “Only used to complete the account” conflicts with the tracked persistence and later profile display of address data. | Replace the absolute claim with bounded copy reflecting account completion and subsequent product use/visibility under the actual policy. |
| `AUTH-PRIV-02` | `partially accepted` | The categorical identity-document purpose claim is not supported by reviewed retention/deletion evidence. Bounded copy can be approved now; defining the complete organization-wide retention and post-review access policy is separate governance work, not a design-stage endpoint change. | State that the file is sent to authorized reviewers for this recovery request; separately track recipient, retention/deletion, and post-decision access policy with security/privacy owners. |
| `AUTH-A11Y-01` | `accepted` | The reported muted/placeholder combinations and small consequential copy are not covered by the current validation and create a credible mobile readability risk. | Refine scoped auth tokens and typography, then run contrast and width/fit checks; make no global `:root` change. |
| `AUTH-SEC-01` | `accepted` | Password copy must state uppercase/lowercase English, show incomplete/mismatch/API states, and represent visibility as semantic controls. | Correct the full SEC-01 frame and atlas; preserve the stronger approved client rules and add semantic controls/tests during implementation. |
| `AUTH-PROGRESS-01` | `partially accepted` | The numeric optional `4` is misleading and is removed. The report does not establish that an LTR visual progress track is inherently invalid in every RTL financial flow; direction must be deliberate and tested rather than inferred from text direction alone. | Keep Telegram outside required progress; refine and document one consistent RTL-aware progress convention while preserving LTR only for numeric data such as phone, OTP, and timers. |
| `AUTH-OTP-01` | `accepted` | “Enter the system” overpromises because verification may lead to registration or device approval, and an active CTA beside an incomplete code misstates action readiness. | Use outcome-neutral “verify and continue” copy; show disabled-until-complete or completed-code verification/loading behavior in the refined frames. |
| `AUTH-DESKTOP-01` | `accepted` | One recovery proof is sufficient for the 95%-mobile scope, but the current large context panel weakens task/form priority. | Refine the same proof to reduce dead space and strengthen task hierarchy; do not add a second desktop scenario. |
| `AUTH-DEBT-01` | `deferred` | The backend's six-character minimum is weaker than the approved frontend password gate, but it is pre-existing security debt outside this design-only checkpoint. The design must not be weakened to match it. | Align server validation and add API-level tests in a later authorized security/implementation stage. |
| `AUTH-EVID-01` | `accepted` | Static frames, manually marked targets, CSS font stacks, and keyboard illustrations do not prove runtime semantics, actual font loading, real keyboard behavior, or exact image-element dimensions unless those items are directly asserted. | Rewrite evidence claims to name exact measured elements and methods; add runtime/browser evidence only at the appropriate implementation acceptance stage. |

## Closure rule

A later stage may close an accepted or partially accepted carry-forward item only with a linked artifact, test, policy decision, or implementation diff that directly addresses the stated requirement. Rejection reasons must be copied verbatim in substance into any later prompt that re-raises the same finding. Raw reports remain untracked; this register must remain tracked and current.
