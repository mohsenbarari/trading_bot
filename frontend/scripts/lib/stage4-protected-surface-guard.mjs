import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'

export const STAGE4_BASE_COMMIT = '9dfa961000832c830729ce67e8a54357915c716a'
export const STAGE4_BASE_TREE = '1540c2534d8052a3a8cfcffcdc2f65e4b85fc874'

export const STAGE4_SCOPE_MANIFEST_PATH = 'frontend/src/design-system-v2/scope-manifest.json'
export const STAGE4_ROUTE_CONTRACT_PATH = 'frontend/src/router/uiRouteContract.ts'
export const ADMIN_MESSAGES_PATH = 'frontend/src/components/AdminMessagesView.vue'
export const ADMIN_MESSAGES_SHA256 =
  '5572589b83a8a07776d5b983777a14a91e2104f9577fa76960df5a54562a431a'
export const TRADING_SETTINGS_PATH = 'frontend/src/components/TradingSettings.vue'
export const TRADING_SETTINGS_SHA256 =
  '509dd32235e1cb98aa164940cf7722604f16b6518f7387699554bf3a828ecfaa'

// This is a one-purpose disposition, not a Stage 4 baseline rewrite. It permits
// only the Stage 6 replacement of the unprotected system-reset native confirm.
export const STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND =
  'stage6-trading-settings-reset-dialog'
export const STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256 =
  'a3718e8beccbdd6eddcbcd72eebd1838fdf4584430f4ed8ba12c5ec95030eea0'
export const STAGE6_TRADING_SETTINGS_PROTECTED_CALENDAR_CONFIRM =
  "if (!confirm('آیا از حذف این استثنای تقویمی مطمئن هستید؟'))"
export const STAGE6_TRADING_SETTINGS_REMOVED_RESET_CONFIRM =
  "if (!confirm('آیا از بازنشانی تنظیمات به مقادیر پیش‌فرض مطمئن هستید؟'))"

export const NATIVE_APP_ADMIN_MESSAGES_VISUAL_KIND = 'native-app-admin-messages-visual-v1'
export const NATIVE_APP_ADMIN_MESSAGES_REQUIRED_MARKERS = Object.freeze([
  'message-mode-button--market',
  'message-mode-button--chat',
  'data-test="active-market-message"',
  'data-test="market-history-list"',
  'data-test="market-composer-card"',
  'data-test="broadcast-panel"',
  'publishMarketMessage',
  'publishBroadcastMessage',
  'history-item--compact',
])
export const NATIVE_APP_ADMIN_MESSAGES_VISUAL_SHA256 =
  '01fbceddc03bf04067c48ad83ef8ab4be2ce0f3c9c182e8fa80e584416705122'

export const NATIVE_APP_TRADING_SETTINGS_VISUAL_KIND = 'native-app-trading-settings-visual-v1'
export const NATIVE_APP_TRADING_SETTINGS_VISUAL_SHA256 =
  '8e88ff4917b5eff0ced6e5099f771423aaccc58e20cda65071b05db18d0efdd7'

export const STAGE4_SHARED_DEPENDENCY_ISOLATION_PATHS = Object.freeze([
  'frontend/src/App.vue',
  'frontend/src/assets/main.css',
  'frontend/src/components/JalaliDatePicker.vue',
  'frontend/src/components/ui/AppEmptyState.vue',
  TRADING_SETTINGS_PATH,
  'frontend/src/components/UserProfile.vue',
  'frontend/src/components/PublicProfile.vue',
  'frontend/src/views/MarketView.vue',
  'frontend/src/components/CreateChannelView.vue',
  'frontend/src/views/NotificationsView.vue',
  'frontend/src/views/CustomerWorkspaceView.vue',
  'frontend/src/views/SettingsView.vue',
  'frontend/src/views/OperationsView.vue',
  'frontend/src/views/AccountantWorkspaceView.vue',
  'frontend/src/components/CommodityManager.vue',
  'frontend/src/components/UserManager.vue',
  'frontend/src/components/CreateInvitationView.vue',
])

const STAGE7_JALALI_CONSUMER_PATHS = Object.freeze([
  'frontend/src/components/UserProfile.vue',
  'frontend/src/components/PublicProfile.vue',
])

const PROTECTED_EMPTY_STATE_CONSUMER_PATHS = Object.freeze([
  'frontend/src/views/MarketView.vue',
  'frontend/src/components/CreateChannelView.vue',
])

const STAGE7_EMPTY_STATE_CONSUMER_PATHS = Object.freeze([
  'frontend/src/views/NotificationsView.vue',
  'frontend/src/views/CustomerWorkspaceView.vue',
  'frontend/src/views/SettingsView.vue',
  'frontend/src/views/OperationsView.vue',
  'frontend/src/views/AccountantWorkspaceView.vue',
  'frontend/src/components/PublicProfile.vue',
  'frontend/src/components/CommodityManager.vue',
  'frontend/src/components/UserManager.vue',
  'frontend/src/components/CreateInvitationView.vue',
])

export const MARKET_RUNTIME_CONTRACT = 'stage4-market-owned-runtime-v1'
export const MESSENGER_RUNTIME_CONTRACT = 'stage4-messenger-owned-runtime-v1'

export const MARKET_RUNTIME_BASELINE = Object.freeze({
  count: 19,
  contentBytes: 137246,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '162e9e618684a24f3db3298eb8ff2c62498b18753cd4e0b6d6b97650d0202058',
})

// This exact integration disposition preserves the immutable Stage 4
// baseline while admitting only the reviewed Market changes already present
// on main when UI/UX candidate fed8fa49 is integrated with main 443ea5a1.
export const MAIN_UIUX_INTEGRATION_MARKET_KIND =
  'main-443ea5a-uiux-fed8fa49-market-integration'

export const MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/ui/AppOfferCard.vue',
  'frontend/src/composables/useOffers.ts',
  'frontend/src/utils/settlementType.ts',
  'frontend/src/views/MarketView.vue',
])

export const MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OfferPreviewModal.vue':
    '8a8aa129152070e192876eb9924e56d860c60b610cc4b2695a929d0c0dfa3e42',
  'frontend/src/components/OffersList.vue':
    '5e1d017e17f772e9a1621be54af16758128aaceb687942c123fc68bbfa21d6d9',
  'frontend/src/components/ui/AppOfferCard.vue':
    'edf2a78ed0a556b4b5e6ae2dbb81c6499da305ef5e36fc2de26c5271e1fff864',
  'frontend/src/composables/useOffers.ts':
    '4ce35b122ccfe94bcdac910663b9409211cac50eedd4bc0e08293e6067865bec',
  'frontend/src/utils/settlementType.ts':
    '4b1648a7310806d4d4bee7e5b241af663c6c998aaa7dde279ebee63a3dc6e5af',
  'frontend/src/views/MarketView.vue':
    'a03b608c63d2fc4ae397399ffb1bb5cf9d2b88adf201e4cf4dd4cd3a981a8d11',
})

export const MAIN_UIUX_INTEGRATION_MARKET_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 147307,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: 'cff97c36d965737605b80c098918c517999fb11f2c66108c2dae4573aac07867',
})

// This is a one-purpose disposition, not a Stage 4 baseline rewrite. It admits
// only the reviewed Market A+C visual/interaction overlay on the same 19-file
// runtime set. Trade semantics, API contracts, and protection stay unchanged.
export const MARKET_A_PLUS_C_KIND = 'market-a-plus-c-visual-decision-clarity'

export const MARKET_A_PLUS_C_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/TradeLotSuggestionAlert.vue',
  'frontend/src/components/ui/AppOfferCard.vue',
  'frontend/src/views/MarketView.vue',
])

export const MARKET_A_PLUS_C_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OfferPreviewModal.vue':
    'f7f725371f1a26076ae641832220891b9ba39b134a0bd8be09850f30f45fd075',
  'frontend/src/components/OffersList.vue':
    'bbfb0b738c79efda5b3a04ae0a5ee466e6f4e09c0e166090e14b9e67aa653d89',
  'frontend/src/components/TradeLotSuggestionAlert.vue':
    'a3cf12e0ff70739020830c48acf2eab7673a7bcc910c21530680db2036b5da2c',
  'frontend/src/components/ui/AppOfferCard.vue':
    '6c9844533065cb51603b9e55b9a22b8822cfeb2ebda24fde39a913079df970e6',
  'frontend/src/views/MarketView.vue':
    '250d50ce16db4b1d95ea76e0a5bf533b97359b42825e0caf1d023225fcac2c15',
})

export const MARKET_A_PLUS_C_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 162211,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: 'e0b32d312b578fd6698beefb68e6d2a17c6c8efe024d408b917a05eb0dd5a531',
})

// Successor overlay after independent audit. This is not a Stage 4 baseline
// rewrite and does not loosen the frozen A+C visual/decision hashes.
export const MARKET_A_PLUS_C_LIFECYCLE_KIND = 'market-a-plus-c-lifecycle-clarity'

export const MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/TradeLotSuggestionAlert.vue',
  'frontend/src/components/ui/AppOfferCard.vue',
  'frontend/src/views/MarketView.vue',
])

export const MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OfferPreviewModal.vue':
    '3278a01042eace0c754353a24a1de10afccd6e4c1899baa67ca927076a650a12',
  'frontend/src/components/OffersList.vue':
    '310992fd5cb6a8197fa6c8c3f7293bcad9af1f6f39a7105f6ec4079d17c53a5e',
  'frontend/src/components/TradeLotSuggestionAlert.vue':
    '9674841528b6092832816744cf34e499b73b59e204503bfc5353ce965cab5452',
  'frontend/src/components/ui/AppOfferCard.vue':
    '6c9844533065cb51603b9e55b9a22b8822cfeb2ebda24fde39a913079df970e6',
  'frontend/src/views/MarketView.vue':
    '5441b793a7ca2f50a34847775a24ab973f6433dfde72592aaae0640c4e4e68f2',
})

export const MARKET_A_PLUS_C_LIFECYCLE_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 163628,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '3d512eac8b60c18e7c7139f8040ffcd0ff749853de117d4888754ca730a92b80',
})

// Owner-approved successor after visual review. It restores a precise SVG
// perimeter countdown and replaces the duplicated overtime text chip with a
// reduced-motion-safe Lucide hourglass sticker. Prior dispositions stay frozen.
export const MARKET_A_PLUS_C_PERIMETER_KIND =
  'market-a-plus-c-perimeter-deadline-hourglass'

export const MARKET_A_PLUS_C_PERIMETER_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/TradeLotSuggestionAlert.vue',
  'frontend/src/components/ui/AppOfferCard.vue',
  'frontend/src/views/MarketView.vue',
])

export const MARKET_A_PLUS_C_PERIMETER_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OfferPreviewModal.vue':
    '3278a01042eace0c754353a24a1de10afccd6e4c1899baa67ca927076a650a12',
  'frontend/src/components/OffersList.vue':
    '61b7f6f9d662ba8160b4dd27e908f5fed1781480305e6ca4a56f906abb455cd1',
  'frontend/src/components/TradeLotSuggestionAlert.vue':
    '9674841528b6092832816744cf34e499b73b59e204503bfc5353ce965cab5452',
  'frontend/src/components/ui/AppOfferCard.vue':
    '29dc50030550476956345b3bef54b9faef736cbe9e937be91a2bbc2df15a3fb2',
  'frontend/src/views/MarketView.vue':
    '5441b793a7ca2f50a34847775a24ab973f6433dfde72592aaae0640c4e4e68f2',
})

export const MARKET_A_PLUS_C_PERIMETER_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 165085,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: 'f7bb91fa317bbfeb2c2bee573d5cee38ce380f492c984c9b2cf0215b3bdaed55',
})

// Owner-directed successor after the full-card perimeter proved visually
// nonlinear across card aspect ratios. It keeps server-authoritative timing
// and the hourglass, removes the overlapping side rail, and separates all
// three read-only lifecycle states without changing trade behavior.
export const MARKET_A_PLUS_C_LINEAR_METER_KIND =
  'market-a-plus-c-linear-deadline-terminal-clarity'

export const MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/TradeLotSuggestionAlert.vue',
  'frontend/src/components/ui/AppOfferCard.vue',
  'frontend/src/views/MarketView.vue',
])

export const MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OfferPreviewModal.vue':
    '3278a01042eace0c754353a24a1de10afccd6e4c1899baa67ca927076a650a12',
  'frontend/src/components/OffersList.vue':
    '939ae95167aff3ef4fb3da3b5fa8706d48632828555aa59fcbc7107c3d3030c7',
  'frontend/src/components/TradeLotSuggestionAlert.vue':
    '9674841528b6092832816744cf34e499b73b59e204503bfc5353ce965cab5452',
  'frontend/src/components/ui/AppOfferCard.vue':
    '3cd57a98d9d3213a0e5fb2ea5da42fee05cc7cae171fbc28ae52d2e8f5b952ab',
  'frontend/src/views/MarketView.vue':
    '5441b793a7ca2f50a34847775a24ab973f6433dfde72592aaae0640c4e4e68f2',
})

export const MARKET_A_PLUS_C_LINEAR_METER_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 169989,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '76903997d27e1a260100fef4d88bc3bff3fb99e96e0eef49f3bb1b5b927261a5',
})

// Owner-directed post-Stage-8 refinement. The web interaction remains
// two-tap, but the first tap is confined to the chosen button and the card
// becomes denser without shrinking any trade action below 44px. Every prior
// Market disposition remains immutable.
export const MARKET_COMPACT_BUTTON_CONFIRM_KIND =
  'market-compact-button-local-confirmation'

export const MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OffersList.vue',
])

export const MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OffersList.vue':
    '6785f2c20ab80d9b79a05fcc4519aa4d09b42941f27d11246454b14986d8572a',
})

export const MARKET_COMPACT_BUTTON_CONFIRM_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 165788,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: 'fc3684c998ed042b5c5c9cb587dcf62adde2d2d3ba69d727680b9ad350777e24',
})

// Owner-directed post-Stage-8 cleanup. Only the redundant feed heading and
// subtitle are removed; the compact card, lifecycle and trade contracts stay
// on their previously reviewed exact sources.
export const MARKET_FEED_HEADING_REMOVAL_KIND =
  'market-redundant-feed-heading-removal'

export const MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS = Object.freeze([
  'frontend/src/views/MarketView.vue',
])

export const MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/views/MarketView.vue':
    '92cb621e01b4005e2c693da665913049f26672334f2f29fd40cdf1c153238b2d',
})

export const MARKET_FEED_HEADING_REMOVAL_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 165291,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: 'd4f2e67bc291454872298c20d0e2a7a809eb7fce5ad0e69ee630f37f80993ad9',
})

// Owner-directed terminal-history refinement. It changes only the visual
// hierarchy of read-only traded/expired cards and their status stamp; trade,
// timer, filtering, API and interaction contracts remain inherited unchanged.
export const MARKET_HISTORY_TERMINAL_VISUAL_KIND =
  'market-history-terminal-minimal-clarity'

export const MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/ui/AppOfferHistoryStamp.vue',
])

export const MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OffersList.vue':
    '2ba59224feb7dd817c491be193a769f21d7ea3cf6989ba9e8450398c9ca535bd',
  'frontend/src/components/ui/AppOfferHistoryStamp.vue':
    '3a3a91c1a279cdc98529c4505a3272ab9ddc0eec6a3a47357af9ddd354d2d385',
})

export const MARKET_HISTORY_TERMINAL_VISUAL_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 166827,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '8320a622ec35748d46c50a86488d039ad82cf1ef0e8557ea70e525c612e38dff',
})

// Owner-approved post-Stage-8 access correction. It only admits the removal
// of the customer-tier gate from the read-only terminal Market history; the
// accountant gate and every terminal-card visual contract remain inherited.
export const MARKET_CUSTOMER_HISTORY_ACCESS_KIND =
  'market-customer-read-only-history-access'

export const MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_PATHS = Object.freeze([
  'frontend/src/views/MarketView.vue',
])

export const MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/views/MarketView.vue':
    '821aa2766f977bfef9e32ec56d68250a21d7d15aa5f9a7c1709ab0cf56f6ad13',
})

export const MARKET_CUSTOMER_HISTORY_ACCESS_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 166783,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '9209fd37b6eb1335f3656004988f259da3836831938dc1b74a33d29b9d7cfbf9',
})

// Functional post-Stage-8 correction. A successful overtime request is
// acknowledged locally so its requester control does not wait for the
// cross-server mirror. All visual, two-tap, trade and history contracts remain
// inherited from the latest reviewed Market disposition.
export const MARKET_OVERTIME_REQUESTER_ACK_KIND =
  'market-overtime-requester-local-acknowledgement'

export const MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OffersList.vue',
])

export const MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OffersList.vue':
    '739458aaaaa4346a71423ad623657168f8d4a846ee86beb010815ac938240dc9',
})

export const MARKET_OVERTIME_REQUESTER_ACK_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 166934,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '337868bcd27df759d8cb643c5d4e74f6c887aac1b9b2b2d5e93ea08a7f7df9b1',
})

// Functional cross-server recovery correction. Numeric offer ids are local to
// each database, so a suggested-lot retry must retain the source mirror id and
// carry the stable public id back to the authoritative home. No visual,
// lifecycle, history, pricing or confirmation contract is widened.
export const MARKET_CROSS_SERVER_LOT_SUGGESTION_KIND =
  'market-cross-server-lot-suggestion-identity'

export const MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OffersList.vue',
])

export const MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OffersList.vue':
    '063581d59aac95a2a497f7dc0fe2f741e7f9425df28aa53fb4af8d5b8cb054f2',
})

export const MARKET_CROSS_SERVER_LOT_SUGGESTION_EVIDENCE = Object.freeze({
  count: 19,
  contentBytes: 167797,
  pathSetSha256: '37aa0b51e20f4ae86f7daf6c3c231d93b3d1f288ade1471490a1f843a57c9589',
  sha256: '310a154c29b733c13534d8f290b065b69f14bdefc64b4c34a5ceaa09a7971425',
})

// Explicit product-requested UX correction for omitted commodity names. A
// unique model suggestion enters the real offer preview with publish/edit/
// cancel actions; ambiguous results remain a choice list. Editing opens only
// server-filtered nearby same-family choices and cannot inherit the model
// receipt.
export const MARKET_INFERENCE_CONFIRMATION_UX_KIND =
  'market-inference-confirmation-ux'

export const MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/CommodityInferenceSelectionModal.vue',
  'frontend/src/views/MarketView.vue',
])

export const MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/CommodityInferenceSelectionModal.vue':
    '81f08b1b7f9c4812b88a79b13e13c4f27efa2f246570dc9185d2b2165b0aeeef',
  'frontend/src/views/MarketView.vue':
    '1a2675955498d366d6f2b8171f7ff971d8b70450906864b13e23aee70de45429',
})

export const MARKET_INFERENCE_CONFIRMATION_UX_EVIDENCE = Object.freeze({
  count: 20,
  contentBytes: 175500,
  pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
  sha256: '70c3dffbaa4f6f7cbfd39498ff8b170576a207ea60b6eabe898d41a9ccfec2ee',
})

// Owner-directed terminal-history density refinement. Only read-only Market
// cards opt into the compact summary grid; active cards, trade actions,
// lifecycle colors, inference UX and server-authoritative behavior remain
// inherited from the prior dispositions.
export const MARKET_HISTORY_COMPACT_SUMMARY_KIND =
  'market-history-compact-summary-layout'

export const MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OffersList.vue',
])

export const MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OffersList.vue':
    '4668b8819b41f6eee76b4fe7898bfab6f473c11addabffd8ae9685af882b16ea',
})

export const MARKET_HISTORY_COMPACT_SUMMARY_EVIDENCE = Object.freeze({
  count: 20,
  contentBytes: 177578,
  pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
  sha256: '270e165727b0e2c6206a838059ea23626e89090652d050218c7d2abec1720c6e',
})

// Owner-directed follow-up to the compact terminal-history layout. It adds
// only a subtle top-edge separation to read-only history cards; active-card
// shadows, deadline meter, lifecycle tints and trade interactions stay on the
// previously reviewed sources and semantics.
export const MARKET_HISTORY_COMPACT_SEPARATION_KIND =
  'market-history-compact-top-separation'

export const MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OffersList.vue',
])

export const MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OffersList.vue':
    'dc794b51bda821aab696ee66f5a671af94ae949be61edcc205d65580382c1225',
})

export const MARKET_HISTORY_COMPACT_SEPARATION_EVIDENCE = Object.freeze({
  count: 20,
  contentBytes: 177912,
  pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
  sha256: '55ebb7e27f40240eaf3e69eb88439b0349d2696e567ec86f6181e9b7232736d3',
})

// Product-requested pack-offer successor. It extends only the existing
// inference surface with a typed PACK_HINT and a concise pack note; every
// Market layout, history, trade, lifecycle and confirmation contract remains
// inherited from the prior frozen dispositions.
export const MARKET_PACK_INFERENCE_KIND = 'market-pack-price-inference'

export const MARKET_PACK_INFERENCE_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/CommodityInferenceSelectionModal.vue',
  'frontend/src/views/MarketView.vue',
])

export const MARKET_PACK_INFERENCE_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/CommodityInferenceSelectionModal.vue':
    'cbdd96f657b6839fa92ee637b789bc0ff4ce3e9cf112a52f68f0db239996ace3',
  'frontend/src/views/MarketView.vue':
    '4e47eb6103fb2f4a084b844d8138d03a9199d62f3f172fa4108744b63944efd1',
})

export const MARKET_PACK_INFERENCE_EVIDENCE = Object.freeze({
  count: 20,
  contentBytes: 178370,
  pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
  sha256: '79b72f4776ce5feef1416ee4f7cf02b9d37259a9f5e87180eb2b423881e41dc5',
})

// Owner-directed history follow-up. Read-only terminal cards gain a uniform
// responsive minimum height and a stronger top edge. Market history also
// converges from the existing active-offer poll when a terminal WebSocket
// event is missed; trading, timers and offer lifecycle authority are unchanged.
export const MARKET_HISTORY_UNIFORM_LIVE_KIND =
  'market-history-uniform-live-convergence'

export const MARKET_HISTORY_UNIFORM_LIVE_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/OffersList.vue',
  'frontend/src/views/MarketView.vue',
])

export const MARKET_HISTORY_UNIFORM_LIVE_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/OffersList.vue':
    '9a58458142f8b0213ce6a853b152a5b04ef93d6f87f8f98e6cb1f37d2b2c086c',
  'frontend/src/views/MarketView.vue':
    '6eea08979c7a91ae4ea5f96939165c28459f2729fb6a4c4c75f15f169c80e608',
})

export const MARKET_HISTORY_UNIFORM_LIVE_EVIDENCE = Object.freeze({
  count: 20,
  contentBytes: 180207,
  pathSetSha256: '6035c31eab716d0061c81427da214fbe9765571ba0d370e218b11edab27678f2',
  sha256: 'b6055a4b3a3d5dfa92f40294d2617875a58761a75babe45342e08bc979f46d97',
})

export const MESSENGER_RUNTIME_BASELINE = Object.freeze({
  count: 85,
  contentBytes: 1312405,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: 'f66debf9809180d97b2bac98f5195ba24200d3b61b0d8e0e5cd423a8a7b97248',
})

// This is a one-purpose disposition, not a Stage 4 baseline update. It permits
// only the Stage 6 removal of profile labels from Messenger-originated URLs.
export const STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/ChatView.vue',
  'frontend/src/components/CreateChannelView.vue',
  'frontend/src/views/MessengerView.vue',
])

export const STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/ChatView.vue':
    'e03ded196c369871f3ecd6763c09535c5a57efc5c0a767d848b2c5a94994273b',
  'frontend/src/components/CreateChannelView.vue':
    '708cabb84325114d03b35b5db8a0b4add64193f438c1a3375a5e66232034102c',
  'frontend/src/views/MessengerView.vue':
    '1cabee73dc161c456130f131f53274a5b546816ff0652d68a4e6ea290e0f83fb',
})

export const STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE = Object.freeze({
  count: 85,
  contentBytes: 1311100,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: '3089210a77936d29754c9478fcdf40619acd08f35d1e8c64f6266fe8efb1699a',
})

// This is a one-purpose disposition, not a Stage 4 or Stage 6 rewrite. It
// permits only the Stage 8 CreateChannel HelpPopover placement remediation.
export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND =
  'stage8-createchannel-helppopover-placement'

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/CreateChannelView.vue',
])

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/CreateChannelView.vue':
    '2e92310e8c74150f9d94162405b68b4ed7bc36198bdfd3536faaae7b5568149a',
})

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS = Object.freeze([
  'frontend/src/components/ChatView.vue',
  'frontend/src/views/MessengerView.vue',
])

export const STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE = Object.freeze({
  count: 85,
  contentBytes: 1311122,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: '7659633875a604e75b925dcd9938ac71f74090b8b077d55ec9d4809107224124',
})

// This is a one-purpose disposition, not a Stage 4/6 rewrite. It permits only
// aria-label names on the four unnamed Messenger list controls found by Gate A.
export const STAGE8_MESSENGER_UNNAMED_CONTROL_KIND = 'stage8-messenger-unnamed-control-names'

export const STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_PATHS = Object.freeze([
  'frontend/src/components/chat/ChatHeader.vue',
  'frontend/src/components/chat/ChatConversationList.vue',
])

export const STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_FILE_SHA256 = Object.freeze({
  'frontend/src/components/chat/ChatHeader.vue':
    'a18d717f9823c262d2bbc9d3dc01cfca488be2a9cbc46d89a3dffb29429ad635',
  'frontend/src/components/chat/ChatConversationList.vue':
    '20359ff625de5faf7fcff7e739d181ee14e5d2262be8acaa559f2ba39f03f142',
})

export const STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE8_PATHS = Object.freeze([
  'frontend/src/components/CreateChannelView.vue',
])

export const STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE6_PATHS = Object.freeze([
  'frontend/src/components/ChatView.vue',
  'frontend/src/views/MessengerView.vue',
])

export const STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE = Object.freeze({
  count: 85,
  contentBytes: 1311357,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: '32dde68767fbcf6dfd070e25547ca5c2d69199aaf9d1999fff26bfcac05bedbb',
})

export const NATIVE_APP_MESSENGER_VISUAL_KIND = 'native-app-messenger-visual-v1'

export const NATIVE_APP_MESSENGER_VISUAL_REQUIRED_MARKERS = Object.freeze([
  'album_id',
  'album_index',
  'data-messenger-ui-version',
  'legacy-default',
  'aria-label="بازگشت"',
  'aria-label="جستجو"',
  'aria-label="گزینه‌های بیشتر"',
  'aria-label="شروع گفتگوی جدید"',
])

export const NATIVE_APP_MESSENGER_VISUAL_EVIDENCE = Object.freeze({
  count: 85,
  contentBytes: 1295000,
  pathSetSha256: 'f6af1f961e45d785ba9c752ee670643571086c6a946843807fe6f581d11aea58',
  sha256: '3ef576caebfd4ddccab6990d3f2c03efeaaa31bb570d015d9df8dfe59b5505c7',
})

const RUNTIME_SOURCE_EXTENSION = /\.(?:css|[cm]?[jt]sx?|vue)$/
const TEST_SOURCE = /(?:^|\/)[^/]+\.(?:spec|test)\.[^/]+$/

const MARKET_EXACT_RUNTIME_PATHS = new Set([
  'frontend/src/components/CommodityInferenceSelectionModal.vue',
  'frontend/src/components/OfferPreviewModal.vue',
  'frontend/src/components/OffersList.vue',
  'frontend/src/components/TradeLotSuggestionAlert.vue',
  'frontend/src/components/ui/AppSettlementBadge.vue',
  'frontend/src/components/ui/AppTradeActionButton.vue',
  'frontend/src/composables/useMarketRuntime.ts',
  'frontend/src/composables/useOffers.ts',
  'frontend/src/utils/offerDraftText.ts',
  // A direct Market dependency omitted from the Stage 3 glob expansion.
  'frontend/src/utils/settlementType.ts',
  'frontend/src/views/MarketView.vue',
])

export const MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS = Object.freeze([
  'frontend/src/components/AdminBroadcastModal.vue',
  'frontend/src/stores/audio.ts',
  'frontend/src/utils/accountantChatIdentity.ts',
  'frontend/src/utils/audioRecorder.ts',
  'frontend/src/utils/composerOverlayState.ts',
  'frontend/src/utils/conversationListModel.ts',
  'frontend/src/utils/emojiStickerCatalog.ts',
  'frontend/src/utils/imagePreprocessClient.ts',
  'frontend/src/utils/messageContextMenuModel.ts',
  'frontend/src/utils/messageReactions.ts',
  'frontend/src/utils/shareTargetStore.ts',
  'frontend/src/utils/sharedVisibilityObserver.ts',
  'frontend/src/workers/imagePreprocess.worker.ts',
])

const MESSENGER_EXACT_RUNTIME_PATHS = new Set([
  'frontend/src/components/ChatView.vue',
  'frontend/src/components/CreateChannelView.vue',
  'frontend/src/styles/messenger-design-tokens.css',
  'frontend/src/types/chat.ts',
  'frontend/src/views/MessengerView.vue',
  'frontend/src/views/ShareReceiveView.vue',
  ...MESSENGER_OMITTED_DIRECT_RUNTIME_PATHS,
])

export const STAGE4_PROTECTED_ROUTE_CONTRACT = Object.freeze([
  Object.freeze({
    path: '/',
    shellClass: 'standard-authenticated',
    protection: 'mixed',
    protectedInteriors: Object.freeze(['home-market-widget']),
    v2Scope: 'section',
  }),
  Object.freeze({
    path: '/admin/channels',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
  Object.freeze({
    path: '/admin/messages',
    shellClass: 'standard-authenticated',
    protection: 'mixed',
    protectedInteriors: Object.freeze([
      'admin-messages-market-delivery',
      'admin-messages-messenger-delivery',
    ]),
    v2Scope: 'section',
  }),
  Object.freeze({
    path: '/admin/system',
    shellClass: 'standard-authenticated',
    protection: 'mixed',
    protectedInteriors: Object.freeze(['trading-settings-market-controls']),
    v2Scope: 'section',
  }),
  Object.freeze({
    path: '/chat',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
  Object.freeze({
    path: '/market',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
  Object.freeze({
    path: '/share-receive',
    shellClass: 'protected-legacy',
    protection: 'full',
    protectedInteriors: Object.freeze([]),
    v2Scope: 'off',
  }),
])

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex')
}

function posixPath(value) {
  return value.split(path.sep).join('/')
}

function isRuntimeSource(repoPath) {
  return RUNTIME_SOURCE_EXTENSION.test(repoPath) && !TEST_SOURCE.test(repoPath)
}

export function isMarketOwnedRuntimePath(repoPath) {
  const normalized = posixPath(repoPath)
  if (!isRuntimeSource(normalized)) return false
  return (
    MARKET_EXACT_RUNTIME_PATHS.has(normalized) ||
    /^frontend\/src\/components\/ui\/AppOffer[^/]*\.vue$/.test(normalized)
  )
}

export function isMessengerOwnedRuntimePath(repoPath) {
  const normalized = posixPath(repoPath)
  if (!isRuntimeSource(normalized)) return false
  return (
    MESSENGER_EXACT_RUNTIME_PATHS.has(normalized) ||
    normalized.startsWith('frontend/src/components/chat/') ||
    normalized.startsWith('frontend/src/components/messenger-v2/') ||
    normalized.startsWith('frontend/src/composables/chat/') ||
    normalized.startsWith('frontend/src/services/chat/') ||
    /^frontend\/src\/services\/chat[^/]*\.[cm]?[jt]sx?$/.test(normalized) ||
    normalized.startsWith('frontend/src/stores/chat/') ||
    /^frontend\/src\/utils\/(?:chat|messenger)[^/]*\.[cm]?[jt]sx?$/.test(normalized)
  )
}

function walkFiles(directory) {
  if (!fs.existsSync(directory)) return []
  const files = []
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) files.push(...walkFiles(entryPath))
    else if (entry.isFile()) files.push(entryPath)
  }
  return files
}

export function discoverStage4OwnedRuntimePaths(repoRoot) {
  const sourceRoot = path.join(repoRoot, 'frontend', 'src')
  const allSourcePaths = walkFiles(sourceRoot).map((filePath) =>
    posixPath(path.relative(repoRoot, filePath)),
  )
  return {
    market: allSourcePaths.filter(isMarketOwnedRuntimePath).sort(),
    messenger: allSourcePaths.filter(isMessengerOwnedRuntimePath).sort(),
  }
}

export function readFileEntries(repoRoot, repoPaths) {
  return repoPaths.map((repoPath) => ({
    path: repoPath,
    content: fs.readFileSync(path.join(repoRoot, repoPath)),
  }))
}

export function pathSetSha256(repoPaths) {
  const paths = [...repoPaths].sort()
  if (new Set(paths).size !== paths.length)
    throw new Error('protected path set contains duplicates')
  return sha256(Buffer.from(`${paths.join('\n')}\n`, 'utf8'))
}

export function protectedFileSetEvidence(entries, contract) {
  if (!Array.isArray(entries) || typeof contract !== 'string' || contract.length === 0) {
    throw new TypeError('protected file-set evidence requires entries and a contract')
  }

  const sorted = [...entries].sort((left, right) => left.path.localeCompare(right.path))
  const paths = sorted.map((entry) => entry.path)
  if (new Set(paths).size !== paths.length)
    throw new Error('protected file set contains duplicates')

  let contentBytes = 0
  const chunks = [Buffer.from(`${contract}\0`, 'utf8')]
  for (const entry of sorted) {
    if (typeof entry.path !== 'string' || entry.path.length === 0) {
      throw new TypeError('protected file-set entry path must be a non-empty string')
    }
    const content = Buffer.isBuffer(entry.content)
      ? entry.content
      : Buffer.from(entry.content, 'utf8')
    contentBytes += content.byteLength
    chunks.push(Buffer.from(`${entry.path}\0${content.byteLength}\0`, 'utf8'), content)
  }

  return {
    contract,
    count: sorted.length,
    contentBytes,
    pathSetSha256: pathSetSha256(paths),
    sha256: sha256(Buffer.concat(chunks)),
  }
}

export function assertProtectedFileSetEvidence(label, actual, expected) {
  for (const field of ['count', 'contentBytes', 'pathSetSha256', 'sha256']) {
    if (actual[field] !== expected[field]) {
      throw new Error(`${label} ${field} drift: ${expected[field]} -> ${actual[field]}`)
    }
  }
  return actual
}

function assertMainUiuxIntegrationMarketAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`main/UIUX Market integration allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MAIN_UIUX_INTEGRATION_MARKET_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `main/UIUX Market integration allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMainUiuxIntegrationMarketDisposition(entries) {
  assertMainUiuxIntegrationMarketAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'main/UIUX Market integration disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MAIN_UIUX_INTEGRATION_MARKET_EVIDENCE,
  )
}

function assertMarketAPlusCAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_A_PLUS_C_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Market A+C allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_A_PLUS_C_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market A+C allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketAPlusCDisposition(entries) {
  assertMarketAPlusCAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'Market A+C visual/decision disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_A_PLUS_C_EVIDENCE,
  )
}

function sourceByPath(entries, repoPath) {
  const entry = entries.find((item) => item.path === repoPath)
  if (!entry) throw new Error(`Market lifecycle-clarity source missing: ${repoPath}`)
  return Buffer.isBuffer(entry.content) ? entry.content.toString('utf8') : String(entry.content)
}

export function assertMarketLifecycleClaritySemantics(entries) {
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const market = sourceByPath(entries, 'frontend/src/views/MarketView.vue')
  const preview = sourceByPath(entries, 'frontend/src/components/OfferPreviewModal.vue')
  const suggestion = sourceByPath(entries, 'frontend/src/components/TradeLotSuggestionAlert.vue')
  if (!offers.includes('const pendingConfirm = ref<string | null>(null); // "offerId:amount"')) {
    throw new Error('Market lifecycle-clarity disposition lost two-tap pendingConfirm')
  }
  if (!offers.includes('تایید {{ amount }} عدد؟')) {
    throw new Error('Market lifecycle-clarity disposition lost pending confirm copy')
  }
  if (!offers.includes(':aria-label="tradeButtonAriaLabel(offer, amount)"')) {
    throw new Error('Market lifecycle-clarity disposition lost accessible trade names')
  }
  if (!offers.includes("apiFetch('/api/trades/'")) {
    throw new Error('Market lifecycle-clarity disposition changed trade endpoint')
  }
  if (!offers.includes("...(intent.offerPublicId ? { offer_public_id: intent.offerPublicId } : {})")) {
    throw new Error('Market lifecycle-clarity disposition lost public offer identity in trade requests')
  }
  if (!offers.includes('const normalizedOfferId = Number(offerId)') || !offers.includes('const normalizedQuantity = Number(quantity)')) {
    throw new Error('Market lifecycle-clarity disposition lost fail-closed trade payload normalization')
  }
  if (!offers.includes("return offer?.offer_type === 'buy' ? 'فروش' : 'خرید'")) {
    throw new Error('Market lifecycle-clarity disposition lost responder inversion')
  }
  if (!preview.includes('نوع لفظ شما:')) {
    throw new Error('Market lifecycle-clarity disposition lost uninverted preview copy')
  }
  if (preview.includes('userActionLabel')) {
    throw new Error('Market lifecycle-clarity disposition inverted own-offer preview')
  }
  if ([offers, market, preview, suggestion].some((source) => source.includes('buy-column') || source.includes('sell-column'))) {
    throw new Error('Market lifecycle-clarity disposition introduced direction B columns')
  }
  if ([offers, market, preview, suggestion].some((source) => (
    source.includes('app-route--persian-typography') || /font-family:\s*['"]Vazirmatn/.test(source)
  ))) {
    throw new Error('Market lifecycle-clarity disposition leaked typography marker')
  }
  if (!market.includes('--market-rail-max: 60rem')) {
    throw new Error('Market lifecycle-clarity disposition lost desktop rail contract')
  }
  const style = market.slice(market.lastIndexOf('<style'))
  if (style.lastIndexOf('--ds-page-max-width: 480px') > style.lastIndexOf('--market-rail-max: 60rem')) {
    throw new Error('Market lifecycle-clarity disposition lost desktop cascade order')
  }
}

function assertMarketLifecycleClarityAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Market A+C lifecycle-clarity allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_A_PLUS_C_LIFECYCLE_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market A+C lifecycle-clarity allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketLifecycleClarityDisposition(entries) {
  assertMarketLifecycleClarityAllowedFiles(entries)
  assertMarketLifecycleClaritySemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market A+C lifecycle-clarity disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_A_PLUS_C_LIFECYCLE_EVIDENCE,
  )
}

export function assertMarketPerimeterDeadlineSemantics(entries) {
  assertMarketLifecycleClaritySemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const card = sourceByPath(entries, 'frontend/src/components/ui/AppOfferCard.vue')
  if (!card.includes('data-test="offer-deadline-perimeter"')) {
    throw new Error('Market perimeter disposition lost the card perimeter')
  }
  if (!card.includes('stroke-dasharray: var(--t-pct, 100) 100')) {
    throw new Error('Market perimeter disposition lost authoritative progress binding')
  }
  if (offers.includes('offer-deadline-bar') || offers.includes('offer-deadline-fill')) {
    throw new Error('Market perimeter disposition restored a bottom-only deadline bar')
  }
  if (!offers.includes('data-test="offer-overtime-sticker"')) {
    throw new Error('Market perimeter disposition lost the overtime sticker')
  }
  if (!offers.includes('role="img"') || !offers.includes('aria-label="وقت اضافه"')) {
    throw new Error('Market perimeter disposition lost the overtime accessible name')
  }
  if (!offers.includes('@keyframes overtime-hourglass-turn')) {
    throw new Error('Market perimeter disposition lost the bounded hourglass motion')
  }
  if (!offers.includes('@media (prefers-reduced-motion: reduce)')) {
    throw new Error('Market perimeter disposition lost reduced-motion handling')
  }
  if (offers.includes('⏳')) {
    throw new Error('Market perimeter disposition replaced the Lucide icon with emoji')
  }
}

function assertMarketPerimeterDeadlineAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_A_PLUS_C_PERIMETER_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Market perimeter allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_A_PLUS_C_PERIMETER_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market perimeter allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketPerimeterDeadlineDisposition(entries) {
  assertMarketPerimeterDeadlineAllowedFiles(entries)
  assertMarketPerimeterDeadlineSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market A+C perimeter-deadline disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_A_PLUS_C_PERIMETER_EVIDENCE,
  )
}

export function assertMarketLinearDeadlineSemantics(entries) {
  assertMarketLifecycleClaritySemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const card = sourceByPath(entries, 'frontend/src/components/ui/AppOfferCard.vue')
  if (!offers.includes('data-test="offer-deadline-meter"')) {
    throw new Error('Market linear-meter disposition lost the deadline meter')
  }
  if (!offers.includes('role="progressbar"') || !offers.includes(':aria-valuenow="Math.round(getDeadlineMeterPercent(offer))"')) {
    throw new Error('Market linear-meter disposition lost progressbar semantics')
  }
  if (!offers.includes('return isOvertimePhase(offer) ? 100 - remainingPercent : remainingPercent')) {
    throw new Error('Market linear-meter disposition lost the zero-origin overtime reset')
  }
  if (!offers.includes('transform: scaleX(var(--t-ratio, 1))')) {
    throw new Error('Market linear-meter disposition lost linear authoritative progress')
  }
  if (!offers.includes("'--t-color': timerColor(offer, remainingPct)")) {
    throw new Error('Market linear-meter disposition lost lifetime color binding')
  }
  const meterValueRule = offers.match(/\.offer-deadline-meter__value\s*\{([^}]*)\}/u)?.[1] || ''
  if (meterValueRule.includes('transition:')) {
    throw new Error('Market linear-meter disposition restored an animated reverse reset')
  }
  if (offers.includes('offer-trade-rail')) {
    throw new Error('Market linear-meter disposition restored the overlapping trade rail')
  }
  if (offers.includes('offer-deadline-perimeter') || card.includes('offer-deadline-perimeter')) {
    throw new Error('Market linear-meter disposition restored the nonlinear perimeter')
  }
  for (const label of ['منقضی · بدون معامله', 'بخشی معامله شد', 'کامل معامله شد']) {
    if (!offers.includes(label)) {
      throw new Error(`Market linear-meter disposition lost terminal label: ${label}`)
    }
  }
  if (!card.includes("'is-partially-traded': traded && partiallyTraded")) {
    throw new Error('Market linear-meter disposition lost partial-trade state')
  }
  if (!card.includes("'is-fully-traded': traded && !partiallyTraded")) {
    throw new Error('Market linear-meter disposition lost full-trade state')
  }
  if (!offers.includes('data-test="offer-overtime-sticker"')) {
    throw new Error('Market linear-meter disposition lost the overtime sticker')
  }
  if (!offers.includes('role="img"') || !offers.includes('aria-label="وقت اضافه"')) {
    throw new Error('Market linear-meter disposition lost the overtime accessible name')
  }
  if (!offers.includes('@keyframes overtime-hourglass-turn') || !offers.includes('@media (prefers-reduced-motion: reduce)')) {
    throw new Error('Market linear-meter disposition lost bounded hourglass motion')
  }
}

function assertMarketLinearDeadlineAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Market linear-meter allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_A_PLUS_C_LINEAR_METER_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market linear-meter allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketLinearDeadlineDisposition(entries) {
  assertMarketLinearDeadlineAllowedFiles(entries)
  assertMarketLinearDeadlineSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market A+C linear deadline/terminal-clarity disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_A_PLUS_C_LINEAR_METER_EVIDENCE,
  )
}

export function assertMarketCompactButtonConfirmSemantics(entries) {
  assertMarketLinearDeadlineSemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  if (offers.includes('data-test="offer-decision-panel"') || offers.includes('مرور و تأیید معامله')) {
    throw new Error('Market compact-confirm disposition restored the expanded first-tap panel')
  }
  if (offers.includes(':decision-focus=') || offers.includes("'is-decision-focus'")) {
    throw new Error('Market compact-confirm disposition restored card-wide decision focus')
  }
  if (!offers.includes('تایید {{ amount }} عدد؟') || !offers.includes(':pending="isPending(offer.id, amount)"')) {
    throw new Error('Market compact-confirm disposition lost button-local two-tap feedback')
  }
  const tradeButtonRule = offers.match(/\.trade-btn\s*\{([^}]*)\}/u)?.[1] || ''
  if (!/min-width:\s*44px/u.test(tradeButtonRule) || !/min-height:\s*44px/u.test(tradeButtonRule)) {
    throw new Error('Market compact-confirm disposition shrank the trade touch target below 44px')
  }
  if (!offers.includes('padding: 8px 9px 9px') || !offers.includes('gap: 7px')) {
    throw new Error('Market compact-confirm disposition lost the reviewed compact card rhythm')
  }
  if (offers.includes('data-test="offer-deadline-label"')) {
    throw new Error('Market compact-confirm disposition restored the redundant visible countdown')
  }
  if (!offers.includes(':aria-label="deadlineMeterAriaLabel(offer)"')) {
    throw new Error('Market compact-confirm disposition lost the accessible deadline name')
  }
}

function assertMarketCompactButtonConfirmAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Market compact-confirm allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_COMPACT_BUTTON_CONFIRM_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market compact-confirm allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketCompactButtonConfirmDisposition(entries) {
  assertMarketCompactButtonConfirmAllowedFiles(entries)
  assertMarketCompactButtonConfirmSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market compact button-local confirmation disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_COMPACT_BUTTON_CONFIRM_EVIDENCE,
  )
}

export function assertMarketFeedHeadingRemovalSemantics(entries) {
  assertMarketCompactButtonConfirmSemantics(entries)
  const market = sourceByPath(entries, 'frontend/src/views/MarketView.vue')
  if (market.includes('market-feed-heading') || market.includes('market-feed-title') || market.includes('market-feed-subtitle')) {
    throw new Error('Market feed-heading removal disposition restored the redundant heading structure')
  }
  if (market.includes('لفظ‌های فعال') || market.includes('مرتب‌شده بر اساس زمان')) {
    throw new Error('Market feed-heading removal disposition restored the redundant heading copy')
  }
  if (!market.includes('<h1 class="market-page-title">بازار</h1>')) {
    throw new Error('Market feed-heading removal disposition lost the page heading')
  }
}

function assertMarketFeedHeadingRemovalAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_FEED_HEADING_REMOVAL_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Market feed-heading removal allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_FEED_HEADING_REMOVAL_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market feed-heading removal allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketFeedHeadingRemovalDisposition(entries) {
  assertMarketFeedHeadingRemovalAllowedFiles(entries)
  assertMarketFeedHeadingRemovalSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market redundant feed-heading removal disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_FEED_HEADING_REMOVAL_EVIDENCE,
  )
}

export function assertMarketHistoryTerminalVisualSemantics(entries) {
  assertMarketFeedHeadingRemovalSemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const stamp = sourceByPath(entries, 'frontend/src/components/ui/AppOfferHistoryStamp.vue')
  if (!offers.includes('.offer-card-wrap.is-expired .offer-card-inner')
    || !offers.includes('border-inline-start: 3px solid var(--ds-text-tertiary)')) {
    throw new Error('Market terminal-history disposition lost the muted expired-card treatment')
  }
  if (!offers.includes('.offer-card-wrap.is-fully-traded .offer-card-inner')
    || !offers.includes('border-inline-start: 3px solid var(--ds-success-600)')) {
    throw new Error('Market terminal-history disposition lost the green traded-card treatment')
  }
  const partialRibbonRule = offers.match(
    /\.offer-card-wrap\.is-partially-traded \.traded-ribbon\s*\{([^}]*)\}/u,
  )?.[1] || ''
  if (!partialRibbonRule.includes('--ds-success-') || partialRibbonRule.includes('--ds-warning-')) {
    throw new Error('Market terminal-history disposition lost the green partial-trade family')
  }
  if (!offers.includes(':is(.offer-body, .offer-time, .offer-settlement, .role-badge)')
    || !offers.includes('opacity: 0.8')) {
    throw new Error('Market terminal-history disposition lost the bounded secondary-content fade')
  }
  if (!stamp.includes('CircleCheckBig') || !stamp.includes('Clock3')) {
    throw new Error('Market terminal-history disposition lost the distinct traded/expired icons')
  }
  if (!stamp.includes('class="history-ribbon__icon"') || !stamp.includes('aria-hidden="true"')) {
    throw new Error('Market terminal-history disposition exposed or removed the decorative status icon')
  }
}

function assertMarketHistoryTerminalVisualAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Market terminal-history allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_HISTORY_TERMINAL_VISUAL_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market terminal-history allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketHistoryTerminalVisualDisposition(entries) {
  assertMarketHistoryTerminalVisualAllowedFiles(entries)
  assertMarketHistoryTerminalVisualSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market terminal-history minimal-clarity disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_HISTORY_TERMINAL_VISUAL_EVIDENCE,
  )
}

export function assertMarketCustomerHistoryAccessSemantics(entries) {
  assertMarketHistoryTerminalVisualSemantics(entries)
  const market = sourceByPath(entries, 'frontend/src/views/MarketView.vue')
  const visibilityRule = market.match(
    /const canViewExpiredMarketOffers = computed\(\(\) => \(([\s\S]*?)\n\)\)/u,
  )?.[1] || ''
  if (!visibilityRule.includes('currentUserLoaded.value')
    || !visibilityRule.includes('!currentUserIsAccountant.value')) {
    throw new Error('Market customer-history disposition lost the authenticated non-accountant gate')
  }
  if (visibilityRule.includes('currentUserCustomerTier.value')) {
    throw new Error('Market customer-history disposition restored the customer-tier exclusion')
  }
}

function assertMarketCustomerHistoryAccessAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market customer-history allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_CUSTOMER_HISTORY_ACCESS_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market customer-history allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketCustomerHistoryAccessDisposition(entries) {
  assertMarketCustomerHistoryAccessAllowedFiles(entries)
  assertMarketCustomerHistoryAccessSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market customer read-only history access disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_CUSTOMER_HISTORY_ACCESS_EVIDENCE,
  )
}

export function assertMarketOvertimeRequesterAcknowledgementSemantics(entries) {
  assertMarketCustomerHistoryAccessSemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  if (!offers.includes("import { publishRequesterOvertimeAcknowledgement } from '../services/offerOvertimeRuntimeEvents';")) {
    throw new Error('Market overtime requester acknowledgement lost its typed runtime event import')
  }
  if (!/if \(response\.ok\) \{\s*publishRequesterOvertimeAcknowledgement\(data\);/u.test(offers)) {
    throw new Error('Market overtime requester acknowledgement no longer follows a successful trade response')
  }
}

function assertMarketOvertimeRequesterAcknowledgementAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market overtime requester acknowledgement allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_OVERTIME_REQUESTER_ACK_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market overtime requester acknowledgement allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketOvertimeRequesterAcknowledgementDisposition(entries) {
  assertMarketOvertimeRequesterAcknowledgementAllowedFiles(entries)
  assertMarketOvertimeRequesterAcknowledgementSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market overtime requester local-acknowledgement disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_OVERTIME_REQUESTER_ACK_EVIDENCE,
  )
}

export function assertMarketCrossServerLotSuggestionSemantics(entries) {
  assertMarketOvertimeRequesterAcknowledgementSemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const requiredFragments = [
    'offerPublicId?: string | null;',
    'const rawOfferPublicId = sourceOffer?.offer_public_id ?? data?.offer_public_id',
    'tradeSuggestion.value = createTradeSuggestionState(data, sourceOffer);',
    'executeTrade(tradeSuggestion.offerId, amount, tradeSuggestion.offerPublicId || null)',
  ]
  for (const fragment of requiredFragments) {
    if (!offers.includes(fragment)) {
      throw new Error(`Market cross-server lot suggestion lost identity binding: ${fragment}`)
    }
  }
  const localIdBeforePeerId = offers.indexOf('const sourceOfferId = Number(sourceOffer?.id)')
  const selectedLocalId = offers.indexOf('Number.isInteger(sourceOfferId) && sourceOfferId > 0')
  if (localIdBeforePeerId < 0 || selectedLocalId < localIdBeforePeerId) {
    throw new Error('Market cross-server lot suggestion no longer prefers the local mirror id')
  }
}

function assertMarketCrossServerLotSuggestionAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market cross-server lot suggestion allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_CROSS_SERVER_LOT_SUGGESTION_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market cross-server lot suggestion allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketCrossServerLotSuggestionDisposition(entries) {
  assertMarketCrossServerLotSuggestionAllowedFiles(entries)
  assertMarketCrossServerLotSuggestionSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market cross-server lot suggestion identity disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_CROSS_SERVER_LOT_SUGGESTION_EVIDENCE,
  )
}

export function assertMarketInferenceConfirmationUxSemantics(entries) {
  assertMarketCrossServerLotSuggestionSemantics(entries)
  const market = sourceByPath(entries, 'frontend/src/views/MarketView.vue')
  const modal = sourceByPath(
    entries,
    'frontend/src/components/CommodityInferenceSelectionModal.vue',
  )
  const requiredMarketFragments = [
    'edit_candidates?: Array<Pick<CoinInferenceShadowCandidate',
    ':edit-candidates="pendingCommodityInference.commodity_inference.edit_candidates"',
    "commodity_resolution: explicitCorrection ? 'EXPLICIT' : 'INFERRED'",
    'commodity_inference: explicitCorrection ? undefined : parsed.commodity_inference',
    '@select="selectInferredCommodity"',
    'if (inference.candidates.length === 1)',
    "commodity_resolution: 'INFERRED'",
    ':start-editing="Number.isInteger(pendingCommodityInference.commodity_id)',
  ]
  for (const fragment of requiredMarketFragments) {
    if (!market.includes(fragment)) {
      throw new Error(`Market inference confirmation UX lost explicit correction contract: ${fragment}`)
    }
  }
  if (market.includes('@edit="editPendingCommodityInference"')) {
    throw new Error('Market inference confirmation UX restored free-text editing from the selector')
  }
  const requiredModalFragments = [
    'const isSingleSuggestion = computed',
    'editing.value ? (props.editCandidates ?? []) : props.candidates',
    "emit('select', candidate, editing.value)",
    'data-test="commodity-inference-suggestion"',
    'data-test="commodity-inference-confirm"',
    'data-test="commodity-inference-edit"',
    'data-test="commodity-inference-cancel"',
    'v-if="isSingleSuggestion"',
    'v-else class="commodity-inference-options"',
    'props.startEditing === true',
    'تا ۱۰٪ اختلاف',
  ]
  for (const fragment of requiredModalFragments) {
    if (!modal.includes(fragment)) {
      throw new Error(`Market inference confirmation UX lost selector semantics: ${fragment}`)
    }
  }
  for (const label of ['تأیید', 'ویرایش', 'انصراف']) {
    if (!modal.includes(label)) {
      throw new Error(`Market inference confirmation UX lost action label: ${label}`)
    }
  }
}

function assertMarketInferenceConfirmationUxAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market inference confirmation UX allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_INFERENCE_CONFIRMATION_UX_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market inference confirmation UX allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketInferenceConfirmationUxDisposition(entries) {
  assertMarketInferenceConfirmationUxAllowedFiles(entries)
  assertMarketInferenceConfirmationUxSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market inference confirmation UX disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_INFERENCE_CONFIRMATION_UX_EVIDENCE,
  )
}

export function assertMarketHistoryCompactSummarySemantics(entries) {
  assertMarketInferenceConfirmationUxSemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const requiredFragments = [
    "'offer-card-inner--history': isReadOnlyOffer(offer)",
    '.offer-card-inner--history .offer-main',
    'grid-template-columns: minmax(6.5rem, 1fr) auto;',
    'grid-template-columns: minmax(19rem, 0.92fr) minmax(0, 1.08fr);',
    'overflow-wrap: anywhere;',
    '.offer-card-inner--history .offer-settlement :deep(.ui-settlement-badge__caption)',
  ]
  for (const fragment of requiredFragments) {
    if (!offers.includes(fragment)) {
      throw new Error(`Market history compact summary lost bounded layout: ${fragment}`)
    }
  }
}

function assertMarketHistoryCompactSummaryAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market history compact summary allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_HISTORY_COMPACT_SUMMARY_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market history compact summary allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketHistoryCompactSummaryDisposition(entries) {
  assertMarketHistoryCompactSummaryAllowedFiles(entries)
  assertMarketHistoryCompactSummarySemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market history compact summary disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_HISTORY_COMPACT_SUMMARY_EVIDENCE,
  )
}

export function assertMarketHistoryCompactSeparationSemantics(entries) {
  assertMarketHistoryCompactSummarySemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const requiredFragments = [
    '.offer-card-wrap.is-history {',
    'box-shadow: 0 -5px 12px -9px color-mix(in srgb, var(--ds-text-primary) 42%, transparent);',
  ]
  for (const fragment of requiredFragments) {
    if (!offers.includes(fragment)) {
      throw new Error(`Market history compact separation lost bounded styling: ${fragment}`)
    }
  }
}

function assertMarketHistoryCompactSeparationAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market history compact separation allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_HISTORY_COMPACT_SEPARATION_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market history compact separation allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketHistoryCompactSeparationDisposition(entries) {
  assertMarketHistoryCompactSeparationAllowedFiles(entries)
  assertMarketHistoryCompactSeparationSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market history compact separation disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_HISTORY_COMPACT_SEPARATION_EVIDENCE,
  )
}

function assertMarketPackInferenceTypedSemantics(entries) {
  const market = sourceByPath(entries, 'frontend/src/views/MarketView.vue')
  const modal = sourceByPath(
    entries,
    'frontend/src/components/CommodityInferenceSelectionModal.vue',
  )
  const requiredMarketFragments = [
    "'PACK_HINT'",
    'pack_hint?: boolean',
    'parsed.pack_hint',
    ':pack-hint="pendingCommodityInference.pack_hint"',
  ]
  for (const fragment of requiredMarketFragments) {
    if (!market.includes(fragment)) {
      throw new Error(`Market pack inference lost typed preview contract: ${fragment}`)
    }
  }
  const requiredModalFragments = [
    'packHint?: boolean',
    'v-if="packHint"',
    'نوع پک از روی قیمت تشخیص داده می‌شود؛ مقدار همواره ۱۰۰ است.',
  ]
  for (const fragment of requiredModalFragments) {
    if (!modal.includes(fragment)) {
      throw new Error(`Market pack inference lost selector guidance: ${fragment}`)
    }
  }
}

export function assertMarketPackInferenceSemantics(entries) {
  assertMarketHistoryCompactSeparationSemantics(entries)
  assertMarketPackInferenceTypedSemantics(entries)
}

function assertMarketPackInferenceAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_PACK_INFERENCE_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market pack inference allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_PACK_INFERENCE_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market pack inference allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketPackInferenceDisposition(entries) {
  assertMarketPackInferenceAllowedFiles(entries)
  assertMarketPackInferenceSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market pack inference disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_PACK_INFERENCE_EVIDENCE,
  )
}

export function assertMarketHistoryUniformLiveSemantics(entries) {
  assertMarketHistoryCompactSummarySemantics(entries)
  assertMarketPackInferenceTypedSemantics(entries)
  const offers = sourceByPath(entries, 'frontend/src/components/OffersList.vue')
  const market = sourceByPath(entries, 'frontend/src/views/MarketView.vue')
  const requiredOfferFragments = [
    '--history-card-min-block-size: 8.75rem;',
    'min-block-size: calc(var(--history-card-min-block-size) - 2px);',
    '0 -8px 18px -8px color-mix(in srgb, var(--ds-text-primary) 55%, transparent)',
    '--history-card-min-block-size: 5.1875rem;',
  ]
  for (const fragment of requiredOfferFragments) {
    if (!offers.includes(fragment)) {
      throw new Error(`Market uniform history lost bounded styling: ${fragment}`)
    }
  }
  const requiredMarketFragments = [
    "cache: 'no-store'",
    'watch(offers, (nextOffers, previousOffers) => {',
    'previousOffers.some((offer) => !nextIdentities.has(offerRuntimeIdentity(offer)))',
    "wsOn('offer:cancelled', handleOfferTerminalHistoryEvent)",
    "wsOn('ws:reconnect', handleRealtimeReconnect)",
    'remainingQuantity <= 0',
  ]
  for (const fragment of requiredMarketFragments) {
    if (!market.includes(fragment)) {
      throw new Error(`Market live history lost convergence guard: ${fragment}`)
    }
  }
}

function assertMarketHistoryUniformLiveAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of MARKET_HISTORY_UNIFORM_LIVE_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) throw new Error(`Market uniform live history allowed file is missing: ${repoPath}`)
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = MARKET_HISTORY_UNIFORM_LIVE_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Market uniform live history allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertMarketHistoryUniformLiveDisposition(entries) {
  assertMarketHistoryUniformLiveAllowedFiles(entries)
  assertMarketHistoryUniformLiveSemantics(entries)
  return assertProtectedFileSetEvidence(
    'Market uniform live history disposition',
    protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT),
    MARKET_HISTORY_UNIFORM_LIVE_EVIDENCE,
  )
}

export function resolveMarketRuntimeDisposition(entries) {
  const actual = protectedFileSetEvidence(entries, MARKET_RUNTIME_CONTRACT)
  try {
    return {
      kind: 'stage4-baseline',
      evidence: assertProtectedFileSetEvidence(
        'Market runtime',
        actual,
        MARKET_RUNTIME_BASELINE,
      ),
    }
  } catch (baselineError) {
    try {
      return {
        kind: MAIN_UIUX_INTEGRATION_MARKET_KIND,
        evidence: assertMainUiuxIntegrationMarketDisposition(entries),
      }
    } catch (integrationError) {
      try {
        return {
          kind: MARKET_A_PLUS_C_KIND,
          evidence: assertMarketAPlusCDisposition(entries),
        }
      } catch (aPlusCError) {
        try {
          return {
            kind: MARKET_A_PLUS_C_LIFECYCLE_KIND,
            evidence: assertMarketLifecycleClarityDisposition(entries),
          }
        } catch (lifecycleError) {
          try {
            return {
              kind: MARKET_A_PLUS_C_PERIMETER_KIND,
              evidence: assertMarketPerimeterDeadlineDisposition(entries),
            }
          } catch (perimeterError) {
            try {
              return {
                kind: MARKET_A_PLUS_C_LINEAR_METER_KIND,
                evidence: assertMarketLinearDeadlineDisposition(entries),
              }
            } catch (linearMeterError) {
              try {
                return {
                  kind: MARKET_COMPACT_BUTTON_CONFIRM_KIND,
                  evidence: assertMarketCompactButtonConfirmDisposition(entries),
                }
              } catch (compactConfirmError) {
                try {
                  return {
                    kind: MARKET_FEED_HEADING_REMOVAL_KIND,
                    evidence: assertMarketFeedHeadingRemovalDisposition(entries),
                  }
                } catch (feedHeadingError) {
                  try {
                    return {
                      kind: MARKET_HISTORY_TERMINAL_VISUAL_KIND,
                      evidence: assertMarketHistoryTerminalVisualDisposition(entries),
                    }
                  } catch (terminalVisualError) {
                    try {
                      return {
                        kind: MARKET_CUSTOMER_HISTORY_ACCESS_KIND,
                        evidence: assertMarketCustomerHistoryAccessDisposition(entries),
                      }
                    } catch (customerHistoryError) {
                      try {
                        return {
                          kind: MARKET_OVERTIME_REQUESTER_ACK_KIND,
                          evidence: assertMarketOvertimeRequesterAcknowledgementDisposition(entries),
                        }
                      } catch (requesterAckError) {
                        try {
                          return {
                            kind: MARKET_CROSS_SERVER_LOT_SUGGESTION_KIND,
                            evidence: assertMarketCrossServerLotSuggestionDisposition(entries),
                          }
                        } catch (lotSuggestionError) {
                          try {
                            return {
                              kind: MARKET_INFERENCE_CONFIRMATION_UX_KIND,
                              evidence: assertMarketInferenceConfirmationUxDisposition(entries),
                            }
                          } catch (inferenceUxError) {
                            try {
                              return {
                                kind: MARKET_HISTORY_COMPACT_SUMMARY_KIND,
                                evidence: assertMarketHistoryCompactSummaryDisposition(entries),
                              }
                            } catch (historyCompactError) {
                              try {
                                return {
                                  kind: MARKET_HISTORY_COMPACT_SEPARATION_KIND,
                                  evidence: assertMarketHistoryCompactSeparationDisposition(entries),
                                }
                              } catch (historySeparationError) {
                                try {
                                  return {
                                    kind: MARKET_PACK_INFERENCE_KIND,
                                    evidence: assertMarketPackInferenceDisposition(entries),
                                  }
                                } catch (packInferenceError) {
                                  try {
                                    return {
                                      kind: MARKET_HISTORY_UNIFORM_LIVE_KIND,
                                      evidence: assertMarketHistoryUniformLiveDisposition(entries),
                                    }
                                  } catch (uniformLiveError) {
                                    const messages = [
                                      baselineError,
                                      integrationError,
                                      aPlusCError,
                                      lifecycleError,
                                      perimeterError,
                                      linearMeterError,
                                      compactConfirmError,
                                      feedHeadingError,
                                      terminalVisualError,
                                      customerHistoryError,
                                      requesterAckError,
                                      lotSuggestionError,
                                      inferenceUxError,
                                      historyCompactError,
                                      historySeparationError,
                                      packInferenceError,
                                      uniformLiveError,
                                    ].map((error) => error instanceof Error ? error.message : String(error))
                                    throw new Error(
                                      `Market runtime rejected after Stage 4 baseline drift (${messages[0]}); main/UIUX integration disposition rejected (${messages[1]}); Market A+C disposition rejected (${messages[2]}); Market A+C lifecycle-clarity disposition rejected (${messages[3]}); Market A+C perimeter-deadline disposition rejected (${messages[4]}); Market A+C linear-meter disposition rejected (${messages[5]}); Market compact-confirm disposition rejected (${messages[6]}); Market feed-heading removal disposition rejected (${messages[7]}); Market terminal-history visual disposition rejected (${messages[8]}); Market customer-history access disposition rejected (${messages[9]}); Market overtime requester acknowledgement disposition rejected (${messages[10]}); Market cross-server lot suggestion identity disposition rejected (${messages[11]}); Market inference confirmation UX disposition rejected (${messages[12]}); Market history compact summary disposition rejected (${messages[13]}); Market history compact separation disposition rejected (${messages[14]}); Market pack inference disposition rejected (${messages[15]}); Market uniform live history disposition rejected (${messages[16]})`,
                                    )
                                  }
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

export function fileSha256(value) {
  return sha256(value)
}

function requiredTextSource(sources, repoPath) {
  const value = sources instanceof Map ? sources.get(repoPath) : sources?.[repoPath]
  if (typeof value !== 'string') {
    throw new Error(`shared dependency source is missing: ${repoPath}`)
  }
  return value
}

function componentTags(source, componentName) {
  const expression = new RegExp(`<${componentName}\\b[^>]*>`, 'g')
  return [...source.matchAll(expression)].map((match) => match[0])
}

function styleBlocks(source) {
  const blocks = [...source.matchAll(/<style(?:\s[^>]*)?>([\s\S]*?)<\/style>/g)].map(
    (match) => match[1],
  )
  return blocks.length ? blocks.join('\n') : source
}

function mediaBlockBodies(source, params) {
  const bodies = []
  let searchFrom = 0
  while (searchFrom < source.length) {
    const mediaIndex = source.indexOf(`@media ${params}`, searchFrom)
    if (mediaIndex === -1) break
    const openBrace = source.indexOf('{', mediaIndex)
    if (openBrace === -1) throw new Error(`${params}: media block is malformed`)

    let depth = 1
    let cursor = openBrace + 1
    while (cursor < source.length && depth > 0) {
      if (source[cursor] === '{') depth += 1
      else if (source[cursor] === '}') depth -= 1
      cursor += 1
    }
    if (depth !== 0) throw new Error(`${params}: media block is malformed`)
    bodies.push(source.slice(openBrace + 1, cursor - 1))
    searchFrom = cursor
  }
  return bodies
}

function assertNoBareFadeReducedMotion(label, source) {
  const reducedMotionBlocks = mediaBlockBodies(
    styleBlocks(source),
    '(prefers-reduced-motion: reduce)',
  )
  for (const block of reducedMotionBlocks) {
    if (/(?:^|[},])\s*\.fade-(?:enter|leave)-active(?=\s*[,\{])/m.test(block)) {
      throw new Error(`${label}: bare fade reduced-motion selector bypasses protected routes`)
    }
  }
  return reducedMotionBlocks
}

/**
 * Protects shared dependencies by behavior instead of rebasing immutable
 * whole-file hashes. Defaults must remain inert for protected consumers;
 * Stage 7 behavior is allowed only through explicit call-site opt-ins.
 */
export function assertStage4SharedDependencyIsolation(sources) {
  const appSource = requiredTextSource(sources, 'frontend/src/App.vue')
  const mainCssSource = requiredTextSource(sources, 'frontend/src/assets/main.css')
  const appStyleSource = styleBlocks(appSource)
  const reducedMotionEligibilityBlock = appSource.match(
    /const allowsReducedMotionRouteTransition = computed\(([\s\S]*?)\n\)/,
  )?.[1]
  if (
    !appSource.includes('getUiRouteContractByName,') ||
    !appSource.includes('UI_ROUTE_PROTECTION,') ||
    !reducedMotionEligibilityBlock?.includes('getUiRouteContractByName(route.name)?.protection') ||
    !reducedMotionEligibilityBlock.includes('UI_ROUTE_PROTECTION.NONE') ||
    !reducedMotionEligibilityBlock.includes('v2Scope.value === UI_V2_SCOPE.SECTION') ||
    !appSource.includes("shouldScopeRoute.value ? 'ui-v2-route-fade' : 'fade'") ||
    !appSource.includes(
      "allowsReducedMotionRouteTransition.value ? 'app-reduced-motion-route' : undefined",
    ) ||
    !appSource.includes('<transition :name="routeTransitionName">') ||
    !appSource.includes(':class="[reducedMotionRouteClass, persianTypographyRouteClass]"')
  ) {
    throw new Error(
      'App route transition is not isolated behind the unprotected-section opt-in contract',
    )
  }

  for (const [label, source] of [
    ['App.vue', appSource],
    ['main.css', mainCssSource],
  ]) {
    assertNoBareFadeReducedMotion(label, source)
  }
  const appReducedMotion = mediaBlockBodies(appStyleSource, '(prefers-reduced-motion: reduce)')
  if (
    !appReducedMotion.some(
      (block) =>
        block.includes('.app-reduced-motion-route.fade-enter-active') &&
        block.includes('.app-reduced-motion-route.fade-leave-active') &&
        block.includes('transition: none;'),
    )
  ) {
    throw new Error('App V2 route reduced-motion opt-in is missing')
  }

  const jalaliSource = requiredTextSource(sources, 'frontend/src/components/JalaliDatePicker.vue')
  if (
    !jalaliSource.includes('arrowKeyNavigation?: boolean') ||
    !jalaliSource.includes('arrowKeyNavigation: false') ||
    !jalaliSource.includes('if (!props.arrowKeyNavigation || !date || props.disabled) return')
  ) {
    throw new Error('JalaliDatePicker arrow navigation must remain default-off and guarded')
  }

  const protectedJalaliTags = componentTags(
    requiredTextSource(sources, TRADING_SETTINGS_PATH),
    'JalaliDatePicker',
  )
  if (!protectedJalaliTags.length) {
    throw new Error('TradingSettings JalaliDatePicker consumer is missing')
  }
  if (protectedJalaliTags.some((tag) => tag.includes('arrow-key-navigation'))) {
    throw new Error('TradingSettings must not opt in to Jalali arrow navigation')
  }

  let stage7JalaliOptIns = 0
  for (const repoPath of STAGE7_JALALI_CONSUMER_PATHS) {
    const tags = componentTags(requiredTextSource(sources, repoPath), 'JalaliDatePicker')
    if (!tags.length || tags.some((tag) => !tag.includes('arrow-key-navigation'))) {
      throw new Error(`${repoPath}: Stage 7 Jalali consumer lacks explicit arrow opt-in`)
    }
    stage7JalaliOptIns += tags.length
  }

  const emptyStateSource = requiredTextSource(
    sources,
    'frontend/src/components/ui/AppEmptyState.vue',
  )
  if (
    !emptyStateSource.includes("role?: 'status' | 'alert'") ||
    !emptyStateSource.includes(':role="role"') ||
    /\brole\s*:\s*['"]status['"]/.test(emptyStateSource) ||
    /<section\b[^>]*\srole=['"]status['"]/.test(emptyStateSource)
  ) {
    throw new Error('AppEmptyState role must remain opt-in with no default status semantics')
  }

  let protectedEmptyStateConsumers = 0
  for (const repoPath of PROTECTED_EMPTY_STATE_CONSUMER_PATHS) {
    const tags = componentTags(requiredTextSource(sources, repoPath), 'AppEmptyState')
    if (!tags.length) throw new Error(`${repoPath}: protected AppEmptyState consumer is missing`)
    if (tags.some((tag) => /\s:?role\s*=/.test(tag))) {
      throw new Error(`${repoPath}: protected AppEmptyState consumer must use the inert default`)
    }
    protectedEmptyStateConsumers += tags.length
  }

  let stage7EmptyStateOptIns = 0
  for (const repoPath of STAGE7_EMPTY_STATE_CONSUMER_PATHS) {
    const tags = componentTags(requiredTextSource(sources, repoPath), 'AppEmptyState')
    if (!tags.length || tags.some((tag) => !/\srole="(?:status|alert)"/.test(tag))) {
      throw new Error(`${repoPath}: Stage 7 empty state lacks an explicit semantic role`)
    }
    stage7EmptyStateOptIns += tags.length
  }

  return {
    reducedMotionSources: 2,
    protectedJalaliConsumers: protectedJalaliTags.length,
    stage7JalaliOptIns,
    protectedEmptyStateConsumers,
    stage7EmptyStateOptIns,
  }
}

function assertStage6MessengerUrlPrivacyAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 6 Messenger URL-privacy allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 6 Messenger URL-privacy allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

/**
 * Accepts only the one reviewed Stage 6 Messenger URL-privacy remediation.
 * The aggregate evidence freezes every other Messenger-owned file, while the
 * per-file hashes make the three permitted paths independently auditable.
 */
export function assertStage6MessengerUrlPrivacyDisposition(entries) {
  assertStage6MessengerUrlPrivacyAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'Stage 6 Messenger URL-privacy disposition',
    protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT),
    STAGE6_MESSENGER_URL_PRIVACY_EVIDENCE,
  )
}

function assertStage8CreateChannelHelpPopoverPlacementAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement allowed file is missing: ${repoPath}`,
      )
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
  for (const repoPath of STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_LOCKED_STAGE6_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement locked Stage 6 file is missing: ${repoPath}`,
      )
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 CreateChannel HelpPopover placement requires unchanged Stage 6 file: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

/**
 * Accepts only the one reviewed Stage 8 CreateChannel HelpPopover placement
 * remediation. ChatView and MessengerView stay on their Stage 6 hashes; only
 * CreateChannelView may carry the new exact file hash.
 */
export function assertStage8CreateChannelHelpPopoverPlacementDisposition(entries) {
  assertStage8CreateChannelHelpPopoverPlacementAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'Stage 8 CreateChannel HelpPopover placement remediation',
    protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT),
    STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_EVIDENCE,
  )
}

function assertStage8MessengerUnnamedControlAllowedFiles(entries) {
  const entriesByPath = new Map(entries.map((entry) => [entry.path, entry]))
  for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 8 Messenger unnamed-control allowed file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE8_MESSENGER_UNNAMED_CONTROL_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 Messenger unnamed-control allowed file drift: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
    const text = entry.content.toString('utf8')
    if (repoPath.endsWith('ChatHeader.vue')) {
      if (!text.includes('aria-label="بازگشت"') || !text.includes('aria-label="جستجو"') || !text.includes('aria-label="گزینه‌های بیشتر"')) {
        throw new Error('Stage 8 Messenger unnamed-control disposition lost ChatHeader accessible names')
      }
    }
    if (repoPath.endsWith('ChatConversationList.vue') && !text.includes('aria-label="شروع گفتگوی جدید"')) {
      throw new Error('Stage 8 Messenger unnamed-control disposition lost the new-chat accessible name')
    }
  }
  for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE8_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 8 Messenger unnamed-control locked Stage 8 file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 Messenger unnamed-control requires unchanged CreateChannel file: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
  for (const repoPath of STAGE8_MESSENGER_UNNAMED_CONTROL_LOCKED_STAGE6_PATHS) {
    const entry = entriesByPath.get(repoPath)
    if (!entry) {
      throw new Error(`Stage 8 Messenger unnamed-control locked Stage 6 file is missing: ${repoPath}`)
    }
    const actualSha256 = fileSha256(entry.content)
    const expectedSha256 = STAGE6_MESSENGER_URL_PRIVACY_ALLOWED_FILE_SHA256[repoPath]
    if (actualSha256 !== expectedSha256) {
      throw new Error(
        `Stage 8 Messenger unnamed-control requires unchanged Stage 6 file: ${repoPath} ${expectedSha256} -> ${actualSha256}`,
      )
    }
  }
}

export function assertStage8MessengerUnnamedControlDisposition(entries) {
  assertStage8MessengerUnnamedControlAllowedFiles(entries)
  return assertProtectedFileSetEvidence(
    'Stage 8 Messenger unnamed-control names',
    protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT),
    STAGE8_MESSENGER_UNNAMED_CONTROL_EVIDENCE,
  )
}

/**
 * Accepts messenger frontend visual restyle while freezing behavior contracts.
 * Historical Stage 4/6/8 hashes stay readable; this is not a baseline rewrite.
 */
export function assertNativeAppMessengerVisualDisposition(entries) {
  const actual = protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT)
  if (actual.count < MESSENGER_RUNTIME_BASELINE.count) {
    throw new Error(
      `native-app-messenger-visual-v1 lost owned files: ${MESSENGER_RUNTIME_BASELINE.count} -> ${actual.count}`,
    )
  }
  if (actual.pathSetSha256 !== MESSENGER_RUNTIME_BASELINE.pathSetSha256) {
    const paths = new Set(entries.map((entry) => entry.path))
    for (const repoPath of MESSENGER_EXACT_RUNTIME_PATHS) {
      if (!paths.has(repoPath)) {
        throw new Error(`native-app-messenger-visual-v1 missing owned file: ${repoPath}`)
      }
    }
    for (const repoPath of paths) {
      if (!isMessengerOwnedRuntimePath(repoPath)) {
        throw new Error(`native-app-messenger-visual-v1 admitted a non-messenger file: ${repoPath}`)
      }
    }
  }

  const joined = entries.map((entry) => entry.content.toString('utf8')).join('\n')
  for (const marker of NATIVE_APP_MESSENGER_VISUAL_REQUIRED_MARKERS) {
    if (!joined.includes(marker)) {
      throw new Error(`native-app-messenger-visual-v1 lost required marker: ${marker}`)
    }
  }
  if (joined.includes('--ui-v2-') || joined.includes('data-ui-system')) {
    throw new Error('native-app-messenger-visual-v1 must not introduce V2 catalog markers')
  }
  return assertProtectedFileSetEvidence(
    'native-app-messenger-visual-v1',
    actual,
    NATIVE_APP_MESSENGER_VISUAL_EVIDENCE,
  )
}

/**
 * Stage 4 remains immutable. If it no longer matches, the exact Stage 6
 * URL-privacy disposition is tried next, then the exact Stage 8
 * CreateChannel HelpPopover placement remediation. All other drift fails.
 */
function tradingSettingsSourceText(source) {
  return Buffer.isBuffer(source) ? source.toString('utf8') : String(source)
}

/**
 * Accepts only the one reviewed Stage 6 TradingSettings reset-dialog change.
 * The protected market-calendar native confirm stays exactly as Stage 4 left it.
 */
export function assertStage6TradingSettingsResetDialogDisposition(source) {
  const text = tradingSettingsSourceText(source)
  if (!text.includes(STAGE6_TRADING_SETTINGS_PROTECTED_CALENDAR_CONFIRM)) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition lost the protected calendar confirm',
    )
  }
  if (text.includes(STAGE6_TRADING_SETTINGS_REMOVED_RESET_CONFIRM)) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition must not keep the native reset confirm',
    )
  }
  if (!text.includes('<AppConfirmDialog') || !text.includes('requestResetConfirmation')) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition is missing the shared reset dialog',
    )
  }
  if (text.includes('arrow-key-navigation')) {
    throw new Error(
      'Stage 6 TradingSettings reset-dialog disposition must not opt in Jalali arrows',
    )
  }
  const actualSha256 = fileSha256(source)
  if (actualSha256 !== STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256) {
    throw new Error(
      `Stage 6 TradingSettings reset-dialog allowed file drift: ${STAGE6_TRADING_SETTINGS_RESET_DIALOG_SHA256} -> ${actualSha256}`,
    )
  }
  return actualSha256
}

export function assertNativeAppAdminMessagesVisualDisposition(source) {
  const text = tradingSettingsSourceText(source)
  for (const marker of NATIVE_APP_ADMIN_MESSAGES_REQUIRED_MARKERS) {
    if (!text.includes(marker)) {
      throw new Error(`native-app-admin-messages-visual-v1 lost required marker: ${marker}`)
    }
  }
  if (text.includes('--ui-v2-') || text.includes('data-ui-system')) {
    throw new Error('native-app-admin-messages-visual-v1 must not introduce V2 catalog markers')
  }
  const actualSha256 = fileSha256(source)
  if (actualSha256 !== NATIVE_APP_ADMIN_MESSAGES_VISUAL_SHA256) {
    throw new Error(
      `native-app-admin-messages-visual-v1 allowed file drift: ${NATIVE_APP_ADMIN_MESSAGES_VISUAL_SHA256} -> ${actualSha256}`,
    )
  }
  return actualSha256
}

export function resolveAdminMessagesDisposition(source) {
  const actualSha256 = fileSha256(source)
  if (actualSha256 === ADMIN_MESSAGES_SHA256) {
    return {
      kind: 'stage4-baseline',
      sha256: actualSha256,
    }
  }
  try {
    return {
      kind: NATIVE_APP_ADMIN_MESSAGES_VISUAL_KIND,
      sha256: assertNativeAppAdminMessagesVisualDisposition(source),
    }
  } catch (visualError) {
    const visualMessage = visualError instanceof Error ? visualError.message : String(visualError)
    throw new Error(
      `AdminMessagesView rejected after Stage 4 whole-file drift (${ADMIN_MESSAGES_SHA256} -> ${actualSha256}); native-app-admin-messages-visual-v1 rejected (${visualMessage})`,
    )
  }
}

export function assertNativeAppTradingSettingsVisualDisposition(source) {
  const text = tradingSettingsSourceText(source)
  if (!text.includes(STAGE6_TRADING_SETTINGS_PROTECTED_CALENDAR_CONFIRM)) {
    throw new Error('native-app-trading-settings-visual-v1 lost the protected calendar confirm')
  }
  if (text.includes(STAGE6_TRADING_SETTINGS_REMOVED_RESET_CONFIRM)) {
    throw new Error('native-app-trading-settings-visual-v1 must not keep the native reset confirm')
  }
  if (!text.includes('<AppConfirmDialog') || !text.includes('requestResetConfirmation')) {
    throw new Error('native-app-trading-settings-visual-v1 is missing the shared reset dialog')
  }
  if (text.includes('arrow-key-navigation')) {
    throw new Error('native-app-trading-settings-visual-v1 must not opt in Jalali arrows')
  }
  if (text.includes('--ui-v2-') || text.includes('data-ui-system')) {
    throw new Error('native-app-trading-settings-visual-v1 must not introduce V2 catalog markers')
  }
  if (
    !text.includes('trading-settings-market-schedule-header') ||
    !text.includes('trading-settings-market-calendar-header')
  ) {
    throw new Error('native-app-trading-settings-visual-v1 lost market settings interiors')
  }
  const actualSha256 = fileSha256(source)
  if (actualSha256 !== NATIVE_APP_TRADING_SETTINGS_VISUAL_SHA256) {
    throw new Error(
      `native-app-trading-settings-visual-v1 allowed file drift: ${NATIVE_APP_TRADING_SETTINGS_VISUAL_SHA256} -> ${actualSha256}`,
    )
  }
  return actualSha256
}

/**
 * Stage 4 remains the immutable whole-file baseline. If it no longer matches,
 * Stage 6 reset-dialog is tried next, then native-app visual restyle.
 */
export function resolveTradingSettingsDisposition(source) {
  const actualSha256 = fileSha256(source)
  if (actualSha256 === TRADING_SETTINGS_SHA256) {
    return {
      kind: 'stage4-baseline',
      sha256: actualSha256,
    }
  }
  try {
    return {
      kind: STAGE6_TRADING_SETTINGS_RESET_DIALOG_KIND,
      sha256: assertStage6TradingSettingsResetDialogDisposition(source),
    }
  } catch (stage6Error) {
    try {
      return {
        kind: NATIVE_APP_TRADING_SETTINGS_VISUAL_KIND,
        sha256: assertNativeAppTradingSettingsVisualDisposition(source),
      }
    } catch (visualError) {
      const stage6Message = stage6Error instanceof Error ? stage6Error.message : String(stage6Error)
      const visualMessage = visualError instanceof Error ? visualError.message : String(visualError)
      throw new Error(
        `TradingSettings rejected after Stage 4 whole-file drift (${TRADING_SETTINGS_SHA256} -> ${actualSha256}); Stage 6 reset-dialog disposition rejected (${stage6Message}); native-app-trading-settings-visual-v1 rejected (${visualMessage})`,
      )
    }
  }
}

export function resolveMessengerRuntimeDisposition(entries) {
  const actual = protectedFileSetEvidence(entries, MESSENGER_RUNTIME_CONTRACT)
  try {
    return {
      kind: 'stage4-baseline',
      evidence: assertProtectedFileSetEvidence(
        'Messenger runtime',
        actual,
        MESSENGER_RUNTIME_BASELINE,
      ),
    }
  } catch (baselineError) {
    try {
      return {
        kind: 'stage6-url-privacy',
        evidence: assertStage6MessengerUrlPrivacyDisposition(entries),
      }
    } catch (stage6Error) {
      try {
        return {
          kind: STAGE8_CREATECHANNEL_HELPPOPOVER_PLACEMENT_KIND,
          evidence: assertStage8CreateChannelHelpPopoverPlacementDisposition(entries),
        }
      } catch (stage8Error) {
        try {
          return {
            kind: STAGE8_MESSENGER_UNNAMED_CONTROL_KIND,
            evidence: assertStage8MessengerUnnamedControlDisposition(entries),
          }
        } catch (stage8NamesError) {
          try {
            return {
              kind: NATIVE_APP_MESSENGER_VISUAL_KIND,
              evidence: assertNativeAppMessengerVisualDisposition(entries),
            }
          } catch (nativeVisualError) {
            const baselineMessage =
              baselineError instanceof Error ? baselineError.message : String(baselineError)
            const stage6Message =
              stage6Error instanceof Error ? stage6Error.message : String(stage6Error)
            const stage8Message =
              stage8Error instanceof Error ? stage8Error.message : String(stage8Error)
            const stage8NamesMessage =
              stage8NamesError instanceof Error ? stage8NamesError.message : String(stage8NamesError)
            const nativeVisualMessage =
              nativeVisualError instanceof Error
                ? nativeVisualError.message
                : String(nativeVisualError)
            throw new Error(
              `Messenger runtime rejected after Stage 4 baseline drift (${baselineMessage}); Stage 6 URL-privacy disposition rejected (${stage6Message}); Stage 8 CreateChannel HelpPopover placement remediation rejected (${stage8Message}); Stage 8 Messenger unnamed-control names rejected (${stage8NamesMessage}); native-app-messenger-visual-v1 rejected (${nativeVisualMessage})`,
            )
          }
        }
      }
    }
  }
}

function sameArray(left, right) {
  return (
    Array.isArray(left) &&
    Array.isArray(right) &&
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  )
}

function routeShape(row) {
  return {
    path: row.path,
    shellClass: row.shellClass,
    protection: row.protection,
    protectedInteriors: row.protectedInteriors,
    v2Scope: row.v2Scope,
  }
}

export function assertStage4RouteProtection(routes) {
  if (!Array.isArray(routes)) throw new TypeError('route protection requires a routes array')

  const paths = routes.map((route) => route?.path)
  if (paths.some((routePath) => typeof routePath !== 'string')) {
    throw new Error('route protection found an invalid path')
  }
  if (new Set(paths).size !== paths.length)
    throw new Error('route protection found duplicate paths')

  const expectedPaths = STAGE4_PROTECTED_ROUTE_CONTRACT.map(({ path: routePath }) => routePath)
  const actualProtectedPaths = routes
    .filter(({ protection }) => protection === 'full' || protection === 'mixed')
    .map(({ path: routePath }) => routePath)
    .sort()
  if (!sameArray(actualProtectedPaths, expectedPaths)) {
    throw new Error(
      `protected route set drift: ${JSON.stringify(expectedPaths)} -> ${JSON.stringify(actualProtectedPaths)}`,
    )
  }

  for (const expected of STAGE4_PROTECTED_ROUTE_CONTRACT) {
    const actual = routes.find(({ path: routePath }) => routePath === expected.path)
    if (!actual) throw new Error(`protected route is missing: ${expected.path}`)
    const actualShape = routeShape(actual)
    for (const field of ['shellClass', 'protection', 'v2Scope']) {
      if (actualShape[field] !== expected[field]) {
        throw new Error(
          `${expected.path} ${field} drift: ${expected[field]} -> ${String(actualShape[field])}`,
        )
      }
    }
    if (!sameArray(actualShape.protectedInteriors, expected.protectedInteriors)) {
      throw new Error(`${expected.path} protected interiors drift`)
    }
  }

  return {
    count: STAGE4_PROTECTED_ROUTE_CONTRACT.length,
    full: STAGE4_PROTECTED_ROUTE_CONTRACT.filter(({ protection }) => protection === 'full').length,
    mixed: STAGE4_PROTECTED_ROUTE_CONTRACT.filter(({ protection }) => protection === 'mixed')
      .length,
  }
}

function uniqueRouteBlock(source, routePath) {
  const escapedPath = routePath.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const expression = new RegExp(`  \\{\\n    path: '${escapedPath}',[\\s\\S]*?\\n  \\},`, 'g')
  const matches = [...source.matchAll(expression)]
  if (matches.length !== 1) {
    throw new Error(`${routePath}: expected exactly one runtime route block`)
  }
  return matches[0][0]
}

function parseQuotedArray(block, property) {
  const match = block.match(new RegExp(`^    ${property}: \\[(.*?)\\],$`, 'm'))
  if (!match) throw new Error(`${property}: runtime route array is missing`)
  if (match[1].trim() === '') return []
  const values = [...match[1].matchAll(/'([^']+)'/g)].map((item) => item[1])
  const residue = match[1].replace(/'[^']+'/g, '').replace(/[\s,]/g, '')
  if (residue !== '') throw new Error(`${property}: runtime route array is invalid`)
  return values
}

function parseEnumProperty(block, property, enumName) {
  const match = block.match(new RegExp(`^    ${property}: ${enumName}\\.([A-Z_]+),$`, 'm'))
  if (!match) throw new Error(`${property}: runtime route enum is missing`)
  return match[1].toLowerCase().replaceAll('_', '-')
}

export function assertStage4RuntimeRouteProtection(source) {
  if (typeof source !== 'string') throw new TypeError('runtime route contract must be text')
  const parsed = STAGE4_PROTECTED_ROUTE_CONTRACT.map((expected) => {
    const block = uniqueRouteBlock(source, expected.path)
    return {
      path: expected.path,
      shellClass: parseEnumProperty(block, 'shellClass', 'UI_ROUTE_SHELL'),
      protection: parseEnumProperty(block, 'protection', 'UI_ROUTE_PROTECTION'),
      protectedInteriors: parseQuotedArray(block, 'protectedInteriors'),
      v2Scope: parseEnumProperty(block, 'v2Scope', 'UI_V2_SCOPE'),
    }
  })
  return assertStage4RouteProtection(parsed)
}
