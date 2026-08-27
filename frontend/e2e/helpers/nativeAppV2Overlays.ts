import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

export type OverlayRecord = {
  id: string
  file: string
  family: 'messenger' | 'auth' | 'account' | 'operations' | 'admin' | 'pwa' | 'market-protected' | 'chrome' | 'profile'
  role: 'dialog' | 'alertdialog' | 'menu'
  name: string
  modal: boolean
  escapeCloses: boolean
  marketProtected: boolean
  notes: string
}

export const OVERLAY_INVENTORY: OverlayRecord[] = [
  { id: 'chat-forward', file: 'frontend/src/components/chat/ChatForwardModal.vue', family: 'messenger', role: 'dialog', name: 'ارسال به', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'chat-lightbox', file: 'frontend/src/components/chat/ChatLightbox.vue', family: 'messenger', role: 'dialog', name: 'نمایش رسانه', modal: true, escapeCloses: true, marketProtected: false, notes: 'lightbox + album sheet' },
  { id: 'chat-location', file: 'frontend/src/components/chat/ChatLocationModal.vue', family: 'messenger', role: 'dialog', name: 'موقعیت', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'gallery-preview', file: 'frontend/src/components/chat/GalleryPreviewModal.vue', family: 'messenger', role: 'dialog', name: 'پیش‌نمایش گالری', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'image-editor', file: 'frontend/src/components/chat/ImageEditorModal.vue', family: 'messenger', role: 'dialog', name: 'ویرایش تصویر', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'attachment-menu', file: 'frontend/src/components/chat/AttachmentMenu.vue', family: 'messenger', role: 'dialog', name: 'پیوست', modal: true, escapeCloses: true, marketProtected: false, notes: 'sheet + camera' },
  { id: 'context-menu', file: 'frontend/src/components/chat/ChatContextMenu.vue', family: 'messenger', role: 'menu', name: 'منوی پیام', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'group-manager', file: 'frontend/src/components/chat/ChatGroupManagerModal.vue', family: 'messenger', role: 'dialog', name: 'مدیریت گروه', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'new-conversation', file: 'frontend/src/components/chat/ChatNewConversationModal.vue', family: 'messenger', role: 'dialog', name: 'گفتگوی جدید', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'location-viewer', file: 'frontend/src/components/chat/LocationViewerModal.vue', family: 'messenger', role: 'dialog', name: 'نمایش موقعیت', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'seen-list', file: 'frontend/src/components/ChatView.vue', family: 'messenger', role: 'dialog', name: 'لیست بازدید', modal: true, escapeCloses: true, marketProtected: false, notes: 'seen-list overlay in ChatView' },
  { id: 'conversation-menu', file: 'frontend/src/components/ChatView.vue', family: 'messenger', role: 'menu', name: 'گزینه‌های گفتگو', modal: false, escapeCloses: true, marketProtected: false, notes: '#chat-header-menu' },
  { id: 'confirm-dialog', file: 'frontend/src/components/ui/AppConfirmDialog.vue', family: 'chrome', role: 'dialog', name: 'تأیید', modal: true, escapeCloses: true, marketProtected: false, notes: 'shared primitive' },
  { id: 'bottom-sheet', file: 'frontend/src/components/ui/AppBottomSheet.vue', family: 'chrome', role: 'dialog', name: 'برگه', modal: true, escapeCloses: true, marketProtected: false, notes: 'shared primitive' },
  { id: 'responsive-dialog', file: 'frontend/src/components/ui/AppResponsiveDialog.vue', family: 'chrome', role: 'dialog', name: 'پنجره', modal: true, escapeCloses: true, marketProtected: false, notes: 'shared primitive' },
  { id: 'account-deletion', file: 'frontend/src/components/workspace/WorkspaceAccountDeletionDialog.vue', family: 'account', role: 'dialog', name: 'حذف حساب', modal: true, escapeCloses: true, marketProtected: false, notes: 'useOverlayA11y' },
  { id: 'session-approval', file: 'frontend/src/components/SessionApprovalModal.vue', family: 'auth', role: 'dialog', name: 'تأیید نشست', modal: true, escapeCloses: false, marketProtected: false, notes: 'Escape must stay closed for security' },
  { id: 'pwa-install', file: 'frontend/src/components/PWAInstallOverlay.vue', family: 'pwa', role: 'dialog', name: 'نصب برنامه', modal: true, escapeCloses: true, marketProtected: false, notes: 'wraps AppBottomSheet' },
  { id: 'overtime-approval', file: 'frontend/src/components/OvertimeApprovalModal.vue', family: 'market-protected', role: 'dialog', name: 'تأیید وقت اضافه', modal: true, escapeCloses: false, marketProtected: true, notes: 'Market overtime; no behavior change' },
  { id: 'jalali-picker', file: 'frontend/src/components/JalaliDatePicker.vue', family: 'market-protected', role: 'dialog', name: 'تقویم جلالی', modal: true, escapeCloses: true, marketProtected: true, notes: 'calendar confirm frozen' },
  { id: 'commodity-inference', file: 'frontend/src/components/CommodityInferenceSelectionModal.vue', family: 'market-protected', role: 'dialog', name: 'انتخاب کالا', modal: true, escapeCloses: true, marketProtected: true, notes: 'Market interior' },
  { id: 'offer-preview', file: 'frontend/src/components/OfferPreviewModal.vue', family: 'market-protected', role: 'dialog', name: 'پیش‌نمایش پیشنهاد', modal: true, escapeCloses: true, marketProtected: true, notes: 'Market interior' },
  { id: 'trade-lot-alert', file: 'frontend/src/components/TradeLotSuggestionAlert.vue', family: 'market-protected', role: 'dialog', name: 'پیشنهاد حجم', modal: true, escapeCloses: true, marketProtected: true, notes: 'Market interior' },
  { id: 'owner-customer-manager', file: 'frontend/src/components/OwnerCustomerManagerModal.vue', family: 'operations', role: 'dialog', name: 'مدیریت مشتری', modal: true, escapeCloses: true, marketProtected: false, notes: 'Teleport sheet' },
  { id: 'owner-accountant-manager', file: 'frontend/src/components/OwnerAccountantManagerModal.vue', family: 'operations', role: 'dialog', name: 'مدیریت حسابدار', modal: true, escapeCloses: true, marketProtected: false, notes: 'Teleport sheet' },
  { id: 'admin-broadcast', file: 'frontend/src/components/AdminBroadcastModal.vue', family: 'admin', role: 'dialog', name: 'پیام همگانی', modal: true, escapeCloses: true, marketProtected: false, notes: 'admin overlay' },
  { id: 'create-invitation-confirm', file: 'frontend/src/components/CreateInvitationView.vue', family: 'admin', role: 'dialog', name: 'تأیید دعوت', modal: true, escapeCloses: true, marketProtected: false, notes: 'AppConfirmDialog' },
  { id: 'commodity-manager-confirm', file: 'frontend/src/components/CommodityManager.vue', family: 'admin', role: 'dialog', name: 'تأیید کالا', modal: true, escapeCloses: true, marketProtected: false, notes: 'AppConfirmDialog' },
  { id: 'trading-settings-calendar', file: 'frontend/src/components/TradingSettings.vue', family: 'market-protected', role: 'dialog', name: 'حذف استثنای تقویمی', modal: true, escapeCloses: true, marketProtected: true, notes: 'calendar confirm frozen' },
  { id: 'settings-confirm', file: 'frontend/src/views/SettingsView.vue', family: 'account', role: 'dialog', name: 'تأیید تنظیمات', modal: true, escapeCloses: true, marketProtected: false, notes: 'AppConfirmDialog' },
  { id: 'dashboard-account-sheet', file: 'frontend/src/views/DashboardView.vue', family: 'account', role: 'dialog', name: 'حساب', modal: true, escapeCloses: true, marketProtected: false, notes: 'account sheet' },
  { id: 'customer-workspace-confirm', file: 'frontend/src/views/CustomerWorkspaceView.vue', family: 'operations', role: 'dialog', name: 'تأیید مشتری', modal: true, escapeCloses: true, marketProtected: false, notes: 'AppConfirmDialog' },
  { id: 'accountant-workspace-confirm', file: 'frontend/src/views/AccountantWorkspaceView.vue', family: 'operations', role: 'dialog', name: 'تأیید حسابدار', modal: true, escapeCloses: true, marketProtected: false, notes: 'AppConfirmDialog' },
  { id: 'public-profile-sheet', file: 'frontend/src/components/PublicProfile.vue', family: 'profile', role: 'dialog', name: 'پروفایل', modal: true, escapeCloses: true, marketProtected: false, notes: 'profile overlay' },
  { id: 'user-profile-sheet', file: 'frontend/src/components/UserProfile.vue', family: 'profile', role: 'dialog', name: 'پروفایل', modal: true, escapeCloses: true, marketProtected: false, notes: 'profile overlay' },
  { id: 'market-view-overlays', file: 'frontend/src/views/MarketView.vue', family: 'market-protected', role: 'dialog', name: 'بازار', modal: true, escapeCloses: true, marketProtected: true, notes: 'Market interiors; no behavior change' },
]

function walk(dir: string, files: string[] = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry.startsWith('.')) continue
    const full = join(dir, entry)
    const stat = statSync(full)
    if (stat.isDirectory()) walk(full, files)
    else if (/\.vue$/.test(entry) && !/\.(spec|test)\./.test(entry)) files.push(full)
  }
  return files
}

export function scanLiveOverlayFiles(repoRoot: string) {
  const src = join(repoRoot, 'frontend/src')
  const hits: string[] = []
  for (const file of walk(src)) {
    const text = readFileSync(file, 'utf8')
    if (
      /role=["']dialog["']/.test(text)
      || /role=["']alertdialog["']/.test(text)
      || /<Teleport/.test(text)
      || /AppBottomSheet/.test(text)
      || /AppConfirmDialog/.test(text)
      || /AppResponsiveDialog/.test(text)
    ) {
      hits.push(relative(repoRoot, file).replaceAll('\\', '/'))
    }
  }
  return hits.sort()
}

export function overlayInventoryGaps(repoRoot: string) {
  const scanned = scanLiveOverlayFiles(repoRoot)
  const known = new Set(OVERLAY_INVENTORY.map((item) => item.file))
  known.add('frontend/src/components/ui/AppDesignSystemCatalog.vue')
  const missing = scanned.filter((file) => !known.has(file) && !file.includes('AppDesignSystemCatalog'))
  const stale = OVERLAY_INVENTORY.map((item) => item.file).filter((file) => !scanned.includes(file) && !file.endsWith('AppConfirmDialog.vue') && !file.endsWith('AppBottomSheet.vue') && !file.endsWith('AppResponsiveDialog.vue'))
  return { scanned, missing, stale }
}
