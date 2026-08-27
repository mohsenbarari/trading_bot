<script setup lang="ts">
import { ChevronDown, Megaphone, PencilLine, Pin, PinOff, Users } from 'lucide-vue-next'
import { computed, nextTick, onMounted, ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { formatIranDateTime } from '../utils/iranTime'
import HelpPopover from './HelpPopover.vue'
import { AppButton, AppCheckbox, AppIconButton, AppInsetGroup, AppTextarea } from './ui'

type AdminMarketMessage = {
  id: number
  content: string
  is_active: boolean
  notified_recipients_count: number
  published_at: string
  created_at?: string
  created_by_id?: number
  created_by_name?: string | null
  reused_from_id?: number | null
}

type AdminBroadcastMessage = {
  id: number
  content: string
  target_groups: string[]
  recipient_count: number
  published_at: string
  created_at?: string
  created_by_id?: number
  created_by_name?: string | null
}

const targetOptions = [
  { key: 'users', label: 'کاربران' },
  { key: 'managers', label: 'مدیران' },
  { key: 'accountants', label: 'حسابداران' },
  { key: 'customers', label: 'مشتریان' },
]

const marketContent = ref('')
const broadcastContent = ref('')
const broadcastTargets = ref<string[]>(targetOptions.map((option) => option.key))
const activeMarketMessage = ref<AdminMarketMessage | null>(null)
const marketHistory = ref<AdminMarketMessage[]>([])
const activePanel = ref<'market' | 'chat' | null>(null)
const isMarketHistoryOpen = ref(false)
const isMarketPinExpanded = ref(false)
const broadcastHistory = ref<AdminBroadcastMessage[]>([])
const marketError = ref('')
const broadcastError = ref('')
const marketSuccess = ref('')
const broadcastSuccess = ref('')
const isPublishingMarket = ref(false)
const isPublishingBroadcast = ref(false)
const isClearingMarketPin = ref(false)
const isLoading = ref(false)
const broadcastLoadError = ref('')
const marketComposerInputRef = ref<{
  focus: (options?: FocusOptions) => void
  scrollIntoView: (arg?: boolean | ScrollIntoViewOptions) => void
} | null>(null)
const panelOptions = [
  { key: 'market' as const, label: 'ارسال پیام در بازار' },
  { key: 'chat' as const, label: 'ارسال پیام در چت' },
]

const marketArchive = computed(() => marketHistory.value.filter((message) => message.id !== activeMarketMessage.value?.id))
const marketRecentHistory = computed(() => marketArchive.value.slice(0, 5))
const selectedBroadcastLabels = computed(() => targetOptions.filter((option) => broadcastTargets.value.includes(option.key)).map((option) => option.label))
const selectedBroadcastCount = computed(() => selectedBroadcastLabels.value.length)

function formatDate(value: string | undefined) {
  if (!value) return ''
  return formatIranDateTime(value, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }) || value
}

function targetLabel(key: string) {
  return targetOptions.find((option) => option.key === key)?.label || key
}

function selectPanel(panel: 'market' | 'chat') {
  activePanel.value = panel
}

function isPanelTabbable(panel: 'market' | 'chat') {
  return activePanel.value === null ? panel === 'market' : activePanel.value === panel
}

function handlePanelKeydown(event: KeyboardEvent, panel: 'market' | 'chat') {
  const currentIndex = panelOptions.findIndex((option) => option.key === panel)
  if (currentIndex === -1) return

  let nextIndex = currentIndex
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    nextIndex = currentIndex <= 0 ? panelOptions.length - 1 : currentIndex - 1
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    nextIndex = currentIndex >= panelOptions.length - 1 ? 0 : currentIndex + 1
  } else if (event.key === 'Home') {
    nextIndex = 0
  } else if (event.key === 'End') {
    nextIndex = panelOptions.length - 1
  } else {
    return
  }

  event.preventDefault()
  selectPanel(panelOptions[nextIndex]!.key)
}

async function readJsonIfOk(response: Response) {
  if (!response.ok) return null
  try {
    return await response.json()
  } catch {
    return null
  }
}

async function loadDashboard() {
  isLoading.value = true
  broadcastLoadError.value = ''
  const [currentResult, marketResult, broadcastResult] = await Promise.allSettled([
    apiFetch('/api/admin-messages/market/current'),
    apiFetch('/api/admin-messages/market/history?limit=50'),
    apiFetch('/api/admin-messages/broadcasts/history?limit=50'),
  ])
  if (currentResult.status === 'fulfilled') {
    activeMarketMessage.value = await readJsonIfOk(currentResult.value)
    isMarketPinExpanded.value = false
  }
  if (marketResult.status === 'fulfilled') {
    const history = await readJsonIfOk(marketResult.value)
    if (history) marketHistory.value = history
  }
  if (broadcastResult.status === 'fulfilled') {
    const history = await readJsonIfOk(broadcastResult.value)
    if (history) {
      broadcastHistory.value = history
    } else {
      broadcastLoadError.value = 'دریافت تاریخچه پیام‌های چت ممکن نشد. دوباره تلاش کنید.'
    }
  } else {
    broadcastLoadError.value = 'دریافت تاریخچه پیام‌های چت ممکن نشد. دوباره تلاش کنید.'
  }
  isLoading.value = false
}

function focusMarketComposer() {
  const input = marketComposerInputRef.value
  if (!input) return
  input.scrollIntoView({ behavior: 'smooth', block: 'center' })
  input.focus()
}

async function editMarketMessage(message: AdminMarketMessage) {
  activePanel.value = 'market'
  marketContent.value = message.content
  marketSuccess.value = ''
  marketError.value = ''
  isMarketHistoryOpen.value = false
  await nextTick()
  focusMarketComposer()
}

async function publishMarketMessage() {
  const content = marketContent.value.trim()
  if (!content || isPublishingMarket.value) return
  isPublishingMarket.value = true
  marketError.value = ''
  marketSuccess.value = ''
  try {
    const response = await apiFetch('/api/admin-messages/market', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload?.detail || 'ثبت پیام بازار ناموفق بود')
    }
    marketSuccess.value = `پیام بازار برای ${Number(payload.notified_recipients_count || 0).toLocaleString('fa-IR')} نفر اعلان شد.`
    marketContent.value = ''
    await loadDashboard()
  } catch (err) {
    marketError.value = err instanceof Error ? err.message : 'ثبت پیام بازار ناموفق بود'
  } finally {
    isPublishingMarket.value = false
  }
}

async function clearMarketPin() {
  if (!activeMarketMessage.value || isClearingMarketPin.value) return
  isClearingMarketPin.value = true
  marketError.value = ''
  marketSuccess.value = ''
  try {
    const response = await apiFetch('/api/admin-messages/market/current', {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload?.detail || 'برداشتن پین بازار ناموفق بود')
    }
    marketSuccess.value = 'پین فعال بازار برداشته شد.'
    await loadDashboard()
  } catch (err) {
    marketError.value = err instanceof Error ? err.message : 'برداشتن پین بازار ناموفق بود'
  } finally {
    isClearingMarketPin.value = false
  }
}

async function publishBroadcastMessage() {
  const content = broadcastContent.value.trim()
  if (!content || broadcastTargets.value.length === 0 || isPublishingBroadcast.value) return
  isPublishingBroadcast.value = true
  broadcastError.value = ''
  broadcastSuccess.value = ''
  try {
    const response = await apiFetch('/api/admin-messages/broadcasts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, target_groups: broadcastTargets.value }),
    })
    const payload = await response.json().catch(() => ({}))
    if (!response.ok) {
      throw new Error(payload?.detail || 'ارسال پیام همگانی ناموفق بود')
    }
    broadcastSuccess.value = `پیام برای ${Number(payload.recipient_count || 0).toLocaleString('fa-IR')} نفر ارسال شد.`
    broadcastContent.value = ''
    await loadDashboard()
  } catch (err) {
    broadcastError.value = err instanceof Error ? err.message : 'ارسال پیام همگانی ناموفق بود'
  } finally {
    isPublishingBroadcast.value = false
  }
}

function reuseMarketMessage(message: AdminMarketMessage) {
  marketContent.value = message.content
  marketSuccess.value = ''
  marketError.value = ''
}

function reuseBroadcastMessage(message: AdminBroadcastMessage) {
  broadcastContent.value = message.content
  broadcastTargets.value = message.target_groups.length ? [...message.target_groups] : targetOptions.map((option) => option.key)
  broadcastSuccess.value = ''
  broadcastError.value = ''
}

onMounted(loadDashboard)
</script>

<template>
  <div class="admin-messages-view">
    <div class="message-mode-switcher" role="tablist" aria-label="نوع پیام مدیریت">
      <AppButton
        type="button"
        class="message-mode-button message-mode-button--market"
        variant="secondary"
        data-test="message-mode-market"
        id="admin-message-tab-market"
        :class="{ 'message-mode-button--active': activePanel === 'market' }"
        role="tab"
        :aria-selected="activePanel === 'market'"
        aria-controls="admin-message-panel-market"
        :tabindex="isPanelTabbable('market') ? 0 : -1"
        @click="selectPanel('market')"
        @keydown="handlePanelKeydown($event, 'market')"
      >
        <Pin :size="16" />
        <span>ارسال پیام در بازار</span>
      </AppButton>
      <AppButton
        type="button"
        class="message-mode-button message-mode-button--chat"
        variant="secondary"
        data-test="message-mode-chat"
        id="admin-message-tab-chat"
        :class="{ 'message-mode-button--active': activePanel === 'chat' }"
        role="tab"
        :aria-selected="activePanel === 'chat'"
        aria-controls="admin-message-panel-chat"
        :tabindex="isPanelTabbable('chat') ? 0 : -1"
        @click="selectPanel('chat')"
        @keydown="handlePanelKeydown($event, 'chat')"
      >
        <Megaphone :size="16" />
        <span>ارسال پیام در چت</span>
      </AppButton>
    </div>

    <div
      v-if="broadcastLoadError"
      class="admin-messages-load-error"
      role="alert"
    >
      <p>{{ broadcastLoadError }}</p>
      <AppButton type="button" variant="secondary" :loading="isLoading" @click="loadDashboard">
        تلاش دوباره
      </AppButton>
    </div>

    <div class="message-workspace" :aria-busy="isLoading">
      <section
        v-if="activePanel === 'market'"
        id="admin-message-panel-market"
        class="message-panel message-panel--market"
        data-test="market-panel"
        role="tabpanel"
        aria-labelledby="admin-message-tab-market"
      >
        <article v-if="activeMarketMessage" class="market-pin-card" data-test="active-market-message">
          <div class="market-pin-card-header">
            <div class="market-pin-card-title-wrap">
              <span class="status-pill status-pill--active">پین فعال بازار</span>
              <span class="date-chip">{{ formatDate(activeMarketMessage.published_at) }}</span>
            </div>
            <div class="market-pin-card-actions">
              <AppButton
                type="button"
                class="ds-native-danger"
                variant="ghost"
                data-test="clear-market-pin"
                :disabled="isClearingMarketPin"
                @click="clearMarketPin"
              >
                <PinOff :size="16" />
                <span>{{ isClearingMarketPin ? 'در حال برداشتن...' : 'برداشتن از بازار' }}</span>
              </AppButton>
            </div>
          </div>

          <div class="admin-market-preview" :class="{ 'admin-market-preview--collapsed': !isMarketPinExpanded }">
            <div class="admin-market-preview-title">پیام مدیریت</div>
            <div class="admin-market-preview-body">{{ activeMarketMessage.content }}</div>
          </div>

          <div class="market-pin-footer">
            <AppButton
              type="button"
              class="ghost-link"
              variant="ghost"
              size="sm"
              data-test="market-pin-expand"
              @click="isMarketPinExpanded = !isMarketPinExpanded"
            >
              {{ isMarketPinExpanded ? 'بستن' : 'مشاهده همه پیام' }}
            </AppButton>
            <span>{{ Number(activeMarketMessage.notified_recipients_count || 0).toLocaleString('fa-IR') }} گیرنده اعلان</span>
          </div>
        </article>

        <article v-else class="status-card status-card--empty card-with-help">
          <HelpPopover
            floating
            button-test="market-empty-help"
            note-test="market-empty-help-note"
            label="توضیحات وضعیت پین بازار"
            text="در حال حاضر هیچ پیام پین‌شده‌ای برای بازار فعال نیست. از کادر پایین برای انتشار پیام جدید استفاده کن."
          />
          <div class="status-card-header">
            <div>
              <span class="status-pill status-pill--muted">بدون پین فعال</span>
              <p class="status-meta">بازار اکنون پیام سنجاق‌شده‌ای ندارد.</p>
            </div>
          </div>
        </article>

        <section class="history-card history-card--accordion card-with-help">
          <HelpPopover
            floating
            button-test="market-history-help"
            note-test="market-history-help-note"
            label="توضیحات تاریخچه بازار"
            text="اگر روی آیکن مداد کنار هر پیام بزنی، متن همان پیام به کادر پایین منتقل می‌شود تا همان‌جا ویرایش و دوباره منتشرش کنی."
          />
          <div class="history-header history-header--market">
            <div class="history-title-row">
              <h4>۵ پیام آخر بازار</h4>
              <AppIconButton
                type="button"
                class="history-toggle-button"
                data-test="market-history-toggle"
                :label="isMarketHistoryOpen ? 'بستن تاریخچه بازار' : 'نمایش تاریخچه بازار'"
                :aria-expanded="isMarketHistoryOpen"
                @click="isMarketHistoryOpen = !isMarketHistoryOpen"
              >
                <ChevronDown :size="22" class="history-toggle-icon" :class="{ 'history-toggle-icon--open': isMarketHistoryOpen }" />
              </AppIconButton>
            </div>
          </div>

          <div v-if="isMarketHistoryOpen" class="history-accordion-body" data-test="market-history-list">
            <article v-for="message in marketRecentHistory" :key="message.id" class="history-item history-item--compact">
              <div class="history-item-top">
                <span class="date-chip">{{ formatDate(message.published_at) }}</span>
                <AppIconButton
                  type="button"
                  class="icon-edit-button"
                  variant="primary"
                  size="sm"
                  :data-test="`market-history-edit-${message.id}`"
                  :label="`ویرایش ${message.content}`"
                  @click="editMarketMessage(message)"
                >
                  <PencilLine :size="16" />
                </AppIconButton>
              </div>
              <p>{{ message.content }}</p>
            </article>

            <p v-if="!marketRecentHistory.length" class="empty-history">هنوز پیام قبلی برای بازار ثبت نشده است.</p>
          </div>
        </section>

        <section class="composer-card card-with-help" data-test="market-composer-card">
          <HelpPopover
            floating
            button-test="market-composer-help"
            note-test="market-composer-help-note"
            label="توضیحات کادر پیام بازار"
            text="فقط یک پیام می‌تواند هم‌زمان در بازار پین باشد."
          />
          <div class="composer-header">
            <div class="section-title-with-help section-title-with-help--single">
              <h4>نوشتن پیام بازار</h4>
            </div>
          </div>
          <AppTextarea
            ref="marketComposerInputRef"
            v-model="marketContent"
            class="message-textarea"
            data-test="market-composer-input"
            rows="7"
            placeholder="متن پیام بازار..."
            aria-label="متن پیام بازار"
          />
          <div v-if="marketError" class="alert error">{{ marketError }}</div>
          <div v-if="marketSuccess" class="alert success">{{ marketSuccess }}</div>
          <div class="composer-actions composer-actions--market">
            <AppButton type="button" class="primary-action" block :disabled="!marketContent.trim() || isPublishingMarket" @click="publishMarketMessage">
              <Pin :size="16" />
              <span>{{ isPublishingMarket ? 'در حال ثبت...' : 'انتشار در بازار' }}</span>
            </AppButton>
          </div>
        </section>
      </section>

      <section
        v-else-if="activePanel === 'chat'"
        id="admin-message-panel-chat"
        class="message-panel message-panel--chat"
        data-test="broadcast-panel"
        role="tabpanel"
        aria-labelledby="admin-message-tab-chat"
      >
        <section class="composer-card composer-card--sheet">
          <div class="composer-header">
            <div>
              <h4>نوشتن پیام چت</h4>
            </div>
            <span class="history-badge">{{ selectedBroadcastLabels.join('، ') || 'بدون انتخاب' }}</span>
          </div>
          <AppTextarea v-model="broadcastContent" class="message-textarea" rows="7" placeholder="متن پیام همگانی..." aria-label="متن پیام همگانی" />

          <div class="audience-panel">
            <div class="audience-header">
              <div class="audience-title">
                <Users :size="16" />
                <span>گروه‌های دریافت‌کننده</span>
              </div>
              <span class="history-badge">{{ selectedBroadcastCount.toLocaleString('fa-IR') }} انتخاب</span>
            </div>
            <div class="target-grid">
              <label v-for="option in targetOptions" :key="option.key" class="target-option" :class="{ 'target-option--active': broadcastTargets.includes(option.key) }">
                <AppCheckbox v-model="broadcastTargets" class="target-option-input" :value="option.key" />
                <span>{{ option.label }}</span>
              </label>
            </div>
          </div>

          <div v-if="broadcastError" class="alert error">{{ broadcastError }}</div>
          <div v-if="broadcastSuccess" class="alert success">{{ broadcastSuccess }}</div>

          <div class="composer-actions">
            <AppButton type="button" class="primary-action" block :disabled="!broadcastContent.trim() || broadcastTargets.length === 0 || isPublishingBroadcast" @click="publishBroadcastMessage">
              <Megaphone :size="16" />
              <span>{{ isPublishingBroadcast ? 'در حال ارسال...' : 'ارسال در چت' }}</span>
            </AppButton>
          </div>
        </section>

        <AppInsetGroup title="تاریخچه پیام‌های چت">
          <article v-for="message in broadcastHistory" :key="message.id" class="history-item">
            <div class="history-meta">
              <span>{{ formatDate(message.published_at) }}</span>
              <span>{{ Number(message.recipient_count || 0).toLocaleString('fa-IR') }} گیرنده</span>
            </div>
            <p>{{ message.content }}</p>
            <div class="target-summary">{{ message.target_groups.map(targetLabel).join('، ') }}</div>
            <div class="history-footer">
              <AppButton type="button" class="ghost-link" variant="ghost" size="sm" @click="reuseBroadcastMessage(message)">استفاده مجدد</AppButton>
            </div>
          </article>

          <p v-if="!broadcastHistory.length" class="empty-history">هنوز هیچ ارسال همگانی ثبت نشده است.</p>
        </AppInsetGroup>
      </section>
    </div>
  </div>
</template>

<style scoped>
.admin-messages-view {
  display: flex;
  flex-direction: column;
  gap: 0.95rem;
}

.message-mode-switcher {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.85rem;
}

.message-mode-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.55rem;
  min-height: var(--ds-native-row-min-height, 48px);
  border: 1px solid var(--ds-native-hairline);
  border-radius: 12px;
  color: var(--ds-text-primary);
  font: inherit;
  font-weight: 800;
  cursor: pointer;
  background: var(--ds-bg-card);
  box-shadow: none;
  transition: background 0.18s ease, border-color 0.18s ease, color 0.18s ease;
}

.message-mode-button--market,
.message-mode-button--chat {
  border-color: var(--ds-native-hairline);
  background: var(--ds-bg-card);
}

.message-mode-button:hover {
  background: var(--ds-bg-inset);
}

.message-mode-button:focus-visible,
.history-toggle-button:focus-visible,
.icon-edit-button:focus-visible,
.ghost-link:focus-visible,
.primary-action:focus-visible,
.secondary-action:focus-visible,
.target-option:focus-within {
  outline: 3px solid rgba(245, 158, 11, 0.34);
  outline-offset: 3px;
}

.message-mode-button--active,
.message-mode-button--market.message-mode-button--active,
.message-mode-button--chat.message-mode-button--active {
  color: var(--ds-primary-700);
  border-color: var(--ds-primary-500);
  background: var(--ds-primary-50);
  box-shadow: none;
}

.message-workspace {
  min-width: 0;
}

.message-panel {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0;
  border-radius: 0;
  border: 0;
  box-shadow: none;
}

.message-panel--market,
.message-panel--chat {
  background: transparent;
}

.market-pin-card,
.status-card,
.composer-card,
.history-card,
.audience-panel {
  border-radius: 12px;
  border: 1px solid var(--ds-native-hairline);
  background: var(--ds-bg-card);
  box-shadow: none;
}

.market-pin-card,
.status-card,
.composer-card,
.history-card {
  padding: 1rem;
}

.message-panel--chat .status-card,
.message-panel--chat .audience-panel {
  padding: 0.85rem 1rem;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.message-panel--chat .composer-card--sheet {
  padding: 0.25rem 0 1rem;
  border-radius: 0;
  border: 0;
  background: transparent;
}

.message-panel--chat .history-item {
  padding: 0.9rem 1rem;
  border-top: 1px solid var(--ds-native-hairline);
}

.message-panel--chat .history-item:first-of-type {
  border-top: 0;
}

.card-with-help {
  position: relative;
}

.card-with-help .status-card-header,
.card-with-help .history-header,
.card-with-help .composer-header {
  padding-left: 2.9rem;
}

.market-pin-card {
  background: var(--ds-bg-card);
}

.market-pin-card-header,
.status-card-header,
.composer-header,
.history-header,
.audience-header,
.history-meta,
.history-footer,
.composer-actions,
.history-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
}

.market-pin-card-header,
.status-card-header,
.composer-header,
.history-header,
.audience-header,
.history-toggle {
  align-items: flex-start;
}

.market-pin-card-title-wrap,
.market-pin-card-actions,
.history-title-row {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
}

.section-title-with-help {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
}

.section-title-with-help--single {
  gap: 0;
}

.composer-header h4,
.history-header h4 {
  margin: 0;
  color: #0f172a;
  font-size: 1rem;
  font-weight: 950;
}

.status-meta,
.status-copy--muted,
.empty-history,
.composer-hint,
.target-summary {
  margin: 0;
  color: #64748b;
  font-size: var(--ds-font-meta);
  line-height: 1.75;
}

.admin-market-preview {
  margin-top: 0.95rem;
  border-radius: 12px;
  border: 1px solid var(--ds-native-hairline);
  background: var(--ds-bg-inset);
  padding: 0.9rem 0.95rem;
}

.admin-market-preview-title {
  margin: 0 0 0.2rem;
  color: #b45309;
  font-size: var(--ds-font-helper);
  font-weight: 950;
}

.admin-market-preview-body {
  color: #1f2937;
  line-height: 1.9;
  white-space: pre-wrap;
  font-weight: 700;
}

.admin-market-preview--collapsed .admin-market-preview-body {
  display: -webkit-box;
  line-clamp: 1;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.market-pin-footer {
  margin-top: 0.8rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.8rem;
  color: #64748b;
  font-size: var(--ds-font-meta);
}

.history-header--market {
  align-items: center;
}

.history-toggle-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--ds-native-row-min-height, 48px);
  height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: var(--ds-radius-full);
  background: var(--ds-bg-inset);
  color: var(--ds-text-secondary);
  cursor: pointer;
}

.history-toggle-button:hover {
  background: var(--ds-bg-hover);
}

.history-toggle-icon {
  transition: transform 0.2s ease;
}

.history-toggle-icon--open {
  transform: rotate(180deg);
}

.help-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border: 1px solid rgba(15, 23, 42, 0.08);
  border-radius: 999px;
  background: rgba(248, 250, 252, 0.94);
  color: #475569;
  cursor: pointer;
  transition: border-color 0.2s ease, color 0.2s ease, background 0.2s ease;
}

.help-trigger--floating {
  position: absolute;
  top: 1rem;
  left: 1rem;
  z-index: 1;
}

.help-trigger:hover {
  color: var(--ds-primary-700);
  border-color: rgba(245, 158, 11, 0.28);
  background: var(--ds-primary-50);
}

.inline-help-note {
  margin-top: 0.7rem;
  padding: 0.72rem 0.85rem;
  border-radius: 14px;
  border: 1px solid rgba(245, 158, 11, 0.2);
  background: var(--ds-primary-50);
  color: var(--ds-primary-800);
  font-size: var(--ds-font-helper);
  line-height: 1.8;
  box-shadow: none;
}

.history-accordion-body {
  margin-top: 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.history-item--compact {
  padding: 0.9rem;
  border-radius: 12px;
  border: 1px solid var(--ds-native-hairline);
  background: var(--ds-bg-inset);
}

.history-item-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
}

.icon-edit-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--ds-native-row-min-height, 48px);
  height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: var(--ds-radius-full);
  background: var(--ds-bg-inset);
  color: var(--ds-text-secondary);
  cursor: pointer;
}

.date-chip,
.status-pill,
.history-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.9rem;
  padding: 0 0.75rem;
  border-radius: 999px;
  font-size: var(--ds-font-badge);
  font-weight: 900;
}

.date-chip {
  background: rgba(15, 23, 42, 0.06);
  color: #475569;
}

.status-pill--active {
  background: rgba(217, 119, 6, 0.12);
  color: #b45309;
}

.status-pill--muted {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.status-pill--info,
.history-badge {
  background: rgba(245, 158, 11, 0.12);
  color: var(--ds-primary-700);
}

.status-copy {
  margin: 0.85rem 0;
  white-space: pre-wrap;
  line-height: 1.95;
  color: #0f172a;
  font-weight: 700;
}

.audience-title {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
  font-size: var(--ds-font-helper);
  font-weight: 900;
  color: #0f172a;
}

.message-textarea {
  width: 100%;
  min-height: 168px;
  resize: vertical;
  margin-top: 0.85rem;
  border: 1.5px solid var(--ds-control-border);
  border-radius: var(--ds-control-radius);
  padding: 0.95rem var(--ds-control-padding-inline);
  background: var(--ds-control-bg);
  color: var(--ds-text-primary);
  font: inherit;
  line-height: 1.9;
}

.message-textarea:focus {
  outline: none;
  border-color: var(--ds-primary-500);
  box-shadow: var(--ds-focus-ring);
}

.audience-panel {
  margin-top: 0.9rem;
  padding: 0.9rem;
}

.target-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
  margin-top: 0.85rem;
  overflow: hidden;
  border-radius: 12px;
  background: var(--ds-bg-card);
}

.target-option {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  min-height: var(--ds-native-row-min-height, 48px);
  padding: 0.75rem 0.9rem;
  border-radius: 0;
  border: 0;
  border-block-end: 1px solid var(--ds-native-hairline);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  font-weight: 750;
  text-align: start;
  cursor: pointer;
}

.target-option:last-child {
  border-block-end: 0;
}

.target-option--active {
  background: var(--ds-primary-50);
  border-color: var(--ds-primary-500);
  color: var(--ds-primary-700);
  box-shadow: none;
}

.target-option-input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.alert {
  margin-top: 0.85rem;
  padding: 0.82rem 0.9rem;
  border-radius: 16px;
  font-size: 0.82rem;
  font-weight: 850;
}

.alert.error {
  color: #b91c1c;
  background: rgba(254, 226, 226, 0.92);
}

.alert.success {
  color: #047857;
  background: rgba(220, 252, 231, 0.92);
}

.admin-messages-load-error {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  min-height: var(--ds-native-row-min-height, 48px);
  padding: 0.82rem 0.9rem;
  border-radius: 16px;
  color: #b91c1c;
  background: rgba(254, 226, 226, 0.92);
}

.admin-messages-load-error p {
  margin: 0;
  font-size: 0.82rem;
  font-weight: 850;
}

.composer-actions {
  margin-top: 0.95rem;
  display: flex;
  flex-direction: column;
  align-items: stretch;
}

.composer-actions--market {
  justify-content: stretch;
}

.composer-hint {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
  max-width: 28rem;
}

.primary-action,
.secondary-action,
.ghost-link {
  border: 0;
  font: inherit;
  font-weight: 900;
  cursor: pointer;
}

.primary-action,
.secondary-action {
  width: 100%;
}

.ghost-link {
  padding: 0 0.75rem;
}

.primary-action:disabled,
.secondary-action:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.history-card {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.history-item {
  padding: 0.9rem 0;
  border-top: 1px solid rgba(15, 23, 42, 0.08);
}

.history-item:first-of-type {
  border-top: 0;
  padding-top: 0.2rem;
}

.history-item p {
  margin: 0.5rem 0 0.65rem;
  white-space: pre-wrap;
  line-height: 1.85;
  color: #0f172a;
  font-weight: 700;
}

.history-meta,
.history-footer {
  color: #64748b;
  font-size: 0.77rem;
}

.empty-history {
  font-size: 0.86rem;
  padding-top: 0.2rem;
}

@media (max-width: 720px) {
  .message-panel {
    padding-bottom: calc(7rem + env(safe-area-inset-bottom, 0px));
  }

  .message-mode-switcher {
    grid-template-columns: 1fr;
  }

  .composer-actions,
  .history-meta,
  .history-footer,
  .history-item-top,
  .market-pin-card-header,
  .market-pin-footer,
  .status-card-header,
  .audience-header,
  .history-header--market {
    flex-direction: column;
    align-items: stretch;
  }

  .target-grid {
    grid-template-columns: 1fr;
  }

  .primary-action,
  .secondary-action,
  .message-mode-button {
    width: 100%;
  }
}
</style>
