<script setup lang="ts">
import { ChevronDown, Megaphone, PencilLine, Pin, PinOff, Users } from 'lucide-vue-next'
import { computed, nextTick, onMounted, ref } from 'vue'
import { apiFetch } from '../utils/auth'
import { formatIranDateTime } from '../utils/iranTime'
import HelpPopover from './HelpPopover.vue'

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
const marketComposerInputRef = ref<HTMLTextAreaElement | null>(null)

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

async function loadDashboard() {
  isLoading.value = true
  try {
    const [currentRes, marketRes, broadcastRes] = await Promise.all([
      apiFetch('/api/admin-messages/market/current'),
      apiFetch('/api/admin-messages/market/history?limit=50'),
      apiFetch('/api/admin-messages/broadcasts/history?limit=50'),
    ])
    activeMarketMessage.value = currentRes.ok ? await currentRes.json().catch(() => null) : null
    isMarketPinExpanded.value = false
    if (marketRes.ok) {
      marketHistory.value = await marketRes.json()
    }
    if (broadcastRes.ok) {
      broadcastHistory.value = await broadcastRes.json()
    }
  } finally {
    isLoading.value = false
  }
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
      <button
        type="button"
        class="message-mode-button message-mode-button--market"
        data-test="message-mode-market"
        :class="{ 'message-mode-button--active': activePanel === 'market' }"
        :aria-selected="activePanel === 'market'"
        @click="activePanel = 'market'"
      >
        <Pin :size="16" />
        <span>ارسال پیام در بازار</span>
      </button>
      <button
        type="button"
        class="message-mode-button message-mode-button--chat"
        data-test="message-mode-chat"
        :class="{ 'message-mode-button--active': activePanel === 'chat' }"
        :aria-selected="activePanel === 'chat'"
        @click="activePanel = 'chat'"
      >
        <Megaphone :size="16" />
        <span>ارسال پیام در چت</span>
      </button>
    </div>

    <div class="message-workspace" :aria-busy="isLoading">
      <section v-if="activePanel === 'market'" class="message-panel message-panel--market" data-test="market-panel">
        <article v-if="activeMarketMessage" class="market-pin-card" data-test="active-market-message">
          <div class="market-pin-card-header">
            <div class="market-pin-card-title-wrap">
              <span class="status-pill status-pill--active">پین فعال بازار</span>
              <span class="date-chip">{{ formatDate(activeMarketMessage.published_at) }}</span>
            </div>
            <div class="market-pin-card-actions">
              <button
                type="button"
                class="secondary-action secondary-action--danger"
                data-test="clear-market-pin"
                :disabled="isClearingMarketPin"
                @click="clearMarketPin"
              >
                <PinOff :size="16" />
                <span>{{ isClearingMarketPin ? 'در حال برداشتن...' : 'برداشتن از بازار' }}</span>
              </button>
            </div>
          </div>

          <div class="admin-market-preview" :class="{ 'admin-market-preview--collapsed': !isMarketPinExpanded }">
            <div class="admin-market-preview-title">پیام مدیریت</div>
            <div class="admin-market-preview-body">{{ activeMarketMessage.content }}</div>
          </div>

          <div class="market-pin-footer">
            <button
              type="button"
              class="ghost-link"
              data-test="market-pin-expand"
              @click="isMarketPinExpanded = !isMarketPinExpanded"
            >
              {{ isMarketPinExpanded ? 'بستن' : 'مشاهده همه پیام' }}
            </button>
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
              <button
                type="button"
                class="history-toggle-button"
                data-test="market-history-toggle"
                :aria-expanded="isMarketHistoryOpen"
                @click="isMarketHistoryOpen = !isMarketHistoryOpen"
              >
                <ChevronDown :size="22" class="history-toggle-icon" :class="{ 'history-toggle-icon--open': isMarketHistoryOpen }" />
              </button>
            </div>
          </div>

          <div v-if="isMarketHistoryOpen" class="history-accordion-body" data-test="market-history-list">
            <article v-for="message in marketRecentHistory" :key="message.id" class="history-item history-item--compact">
              <div class="history-item-top">
                <span class="date-chip">{{ formatDate(message.published_at) }}</span>
                <button
                  type="button"
                  class="icon-edit-button"
                  :data-test="`market-history-edit-${message.id}`"
                  :aria-label="`ویرایش ${message.content}`"
                  @click="editMarketMessage(message)"
                >
                  <PencilLine :size="16" />
                </button>
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
          <textarea
            ref="marketComposerInputRef"
            v-model="marketContent"
            class="message-textarea"
            data-test="market-composer-input"
            rows="7"
            placeholder="متن پیام بازار..."
          ></textarea>
          <div v-if="marketError" class="alert error">{{ marketError }}</div>
          <div v-if="marketSuccess" class="alert success">{{ marketSuccess }}</div>
          <div class="composer-actions composer-actions--market">
            <button class="primary-action" :disabled="!marketContent.trim() || isPublishingMarket" @click="publishMarketMessage">
              <Pin :size="16" />
              <span>{{ isPublishingMarket ? 'در حال ثبت...' : 'انتشار در بازار' }}</span>
            </button>
          </div>
        </section>
      </section>

      <section v-else-if="activePanel === 'chat'" class="message-panel message-panel--chat" data-test="broadcast-panel">
        <article class="status-card status-card--broadcast card-with-help">
          <HelpPopover
            floating
            button-test="broadcast-status-help"
            note-test="broadcast-status-help-note"
            label="راهنمای مخاطبان پیام چت"
            text="پیام با عنوان «پیام مدیریت» برای هر کاربر در اتاق مدیریت خودش ثبت می‌شود و هویت واقعی ادمین به گیرنده نشان داده نمی‌شود."
          />
          <div class="status-card-header">
            <div>
              <span class="status-pill status-pill--info">مخاطبان انتخاب‌شده</span>
              <p class="status-meta">{{ selectedBroadcastCount.toLocaleString('fa-IR') }} گروه مقصد</p>
            </div>
            <span class="history-badge">{{ selectedBroadcastLabels.join('، ') || 'بدون انتخاب' }}</span>
          </div>
        </article>

        <section class="composer-card card-with-help">
          <HelpPopover
            floating
            button-test="broadcast-composer-help"
            note-test="broadcast-composer-help-note"
            label="راهنمای نوشتن پیام چت"
            text="گیرنده‌ها را انتخاب کن و پیام را یک‌بار برای همه آن‌ها در چت ثبت کن. اتاق مقصد فقط‌خواندنی است و unread عادی پیام‌رسان را هم به‌روزرسانی می‌کند."
          />
          <div class="composer-header">
            <div>
              <h4>نوشتن پیام چت</h4>
            </div>
            <span class="composer-counter">{{ broadcastContent.trim().length.toLocaleString('fa-IR') }} کاراکتر</span>
          </div>
          <textarea v-model="broadcastContent" class="message-textarea" rows="7" placeholder="متن پیام همگانی..."></textarea>

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
                <input v-model="broadcastTargets" type="checkbox" class="target-option-input" :value="option.key" />
                <span>{{ option.label }}</span>
              </label>
            </div>
          </div>

          <div v-if="broadcastError" class="alert error">{{ broadcastError }}</div>
          <div v-if="broadcastSuccess" class="alert success">{{ broadcastSuccess }}</div>

          <div class="composer-actions">
            <span></span>
            <button class="primary-action" :disabled="!broadcastContent.trim() || broadcastTargets.length === 0 || isPublishingBroadcast" @click="publishBroadcastMessage">
              <Megaphone :size="16" />
              <span>{{ isPublishingBroadcast ? 'در حال ارسال...' : 'ارسال در چت' }}</span>
            </button>
          </div>
        </section>

        <section class="history-card card-with-help">
          <HelpPopover
            floating
            button-test="broadcast-history-help"
            note-test="broadcast-history-help-note"
            label="راهنمای تاریخچه پیام‌های چت"
            text="ارسال‌های قبلی برای بازاستفاده و اصلاح سریع اینجا نگه داشته می‌شوند."
          />
          <div class="history-header">
            <div>
              <h4>تاریخچه پیام‌های چت</h4>
            </div>
            <span class="history-badge">{{ broadcastHistory.length.toLocaleString('fa-IR') }} مورد</span>
          </div>

          <article v-for="message in broadcastHistory" :key="message.id" class="history-item">
            <div class="history-meta">
              <span>{{ formatDate(message.published_at) }}</span>
              <span>{{ Number(message.recipient_count || 0).toLocaleString('fa-IR') }} گیرنده</span>
            </div>
            <p>{{ message.content }}</p>
            <div class="target-summary">{{ message.target_groups.map(targetLabel).join('، ') }}</div>
            <div class="history-footer">
              <button type="button" class="ghost-link" @click="reuseBroadcastMessage(message)">استفاده مجدد</button>
            </div>
          </article>

          <p v-if="!broadcastHistory.length" class="empty-history">هنوز هیچ ارسال همگانی ثبت نشده است.</p>
        </section>
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
  min-height: 54px;
  border: 1px solid transparent;
  border-radius: 18px;
  color: #1f2937;
  font: inherit;
  font-weight: 900;
  cursor: pointer;
  box-shadow: 0 14px 28px rgba(15, 23, 42, 0.08);
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease, color 0.2s ease;
}

.message-mode-button--market {
  border-color: rgba(217, 119, 6, 0.16);
  background: linear-gradient(135deg, rgba(251, 191, 36, 0.18), rgba(255, 247, 237, 0.98));
}

.message-mode-button--chat {
  border-color: rgba(13, 148, 136, 0.18);
  background: linear-gradient(135deg, rgba(45, 212, 191, 0.16), rgba(240, 253, 250, 0.98));
}

.message-mode-button:hover {
  transform: translateY(-1px);
  box-shadow: 0 18px 34px rgba(15, 23, 42, 0.11);
}

.message-mode-button--active {
  color: #0f766e;
  border-color: rgba(15, 118, 110, 0.22);
  background: linear-gradient(135deg, rgba(15, 118, 110, 0.1), rgba(255, 255, 255, 0.96));
  box-shadow: 0 14px 28px rgba(15, 118, 110, 0.1);
  transform: translateY(-1px);
}

.message-mode-button--market.message-mode-button--active {
  color: #9a3412;
  border-color: rgba(194, 65, 12, 0.24);
  background: linear-gradient(135deg, rgba(251, 191, 36, 0.28), rgba(255, 237, 213, 0.98));
  box-shadow: 0 16px 32px rgba(194, 65, 12, 0.14);
}

.message-mode-button--chat.message-mode-button--active {
  color: #0f766e;
  border-color: rgba(13, 148, 136, 0.24);
  background: linear-gradient(135deg, rgba(45, 212, 191, 0.24), rgba(240, 253, 250, 0.98));
  box-shadow: 0 16px 32px rgba(13, 148, 136, 0.14);
}

.message-workspace {
  min-width: 0;
}

.message-panel {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem;
  border-radius: 24px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.07);
}

.message-panel--market {
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98), rgba(255, 255, 255, 0.92));
}

.message-panel--chat {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.98), rgba(255, 255, 255, 0.92));
}

.market-pin-card,
.status-card,
.composer-card,
.history-card,
.audience-panel {
  border-radius: 20px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.05);
}

.market-pin-card,
.status-card,
.composer-card,
.history-card {
  padding: 1rem;
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
  background: linear-gradient(135deg, rgba(255, 247, 237, 0.98), rgba(255, 255, 255, 0.94));
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
  line-height: 1.75;
}

.admin-market-preview {
  margin-top: 0.95rem;
  border-radius: 18px;
  border: 1px solid rgba(245, 158, 11, 0.18);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(255, 247, 237, 0.9));
  padding: 0.9rem 0.95rem;
}

.admin-market-preview-title {
  margin: 0 0 0.2rem;
  color: #b45309;
  font-size: 0.82rem;
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
  font-size: 0.8rem;
}

.history-header--market {
  align-items: center;
}

.history-toggle-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.15rem;
  height: 2.15rem;
  border: 1px solid rgba(15, 118, 110, 0.12);
  border-radius: 14px;
  background: rgba(240, 253, 250, 0.92);
  color: #0f766e;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}

.history-toggle-button:hover {
  border-color: rgba(15, 118, 110, 0.28);
  box-shadow: 0 8px 16px rgba(15, 118, 110, 0.12);
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
  color: #0f766e;
  border-color: rgba(15, 118, 110, 0.24);
  background: rgba(240, 253, 250, 0.95);
}

.inline-help-note {
  margin-top: 0.7rem;
  padding: 0.72rem 0.85rem;
  border-radius: 14px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  background: rgba(240, 253, 250, 0.92);
  color: #0f4c48;
  font-size: 0.78rem;
  line-height: 1.8;
  box-shadow: 0 10px 20px rgba(15, 118, 110, 0.08);
}

.history-accordion-body {
  margin-top: 0.9rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.history-item--compact {
  padding: 0.9rem;
  border-radius: 18px;
  border: 1px solid rgba(15, 23, 42, 0.07);
  background: rgba(248, 250, 252, 0.92);
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
  width: 2rem;
  height: 2rem;
  border: 0;
  border-radius: 999px;
  background: rgba(15, 118, 110, 0.12);
  color: #0f766e;
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
  font-size: 0.75rem;
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
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
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
  font-size: 0.82rem;
  font-weight: 900;
  color: #0f172a;
}

.message-textarea {
  width: 100%;
  min-height: 168px;
  resize: vertical;
  margin-top: 0.85rem;
  border: 1px solid rgba(15, 23, 42, 0.1);
  border-radius: 18px;
  padding: 0.95rem 1rem;
  background: #fff;
  color: #0f172a;
  font: inherit;
  line-height: 1.9;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.message-textarea:focus {
  outline: none;
  border-color: rgba(15, 118, 110, 0.35);
  box-shadow: 0 0 0 4px rgba(15, 118, 110, 0.08);
}

.audience-panel {
  margin-top: 0.9rem;
  padding: 0.9rem;
}

.target-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
  margin-top: 0.85rem;
}

.target-option {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 3rem;
  padding: 0.75rem 0.9rem;
  border-radius: 16px;
  border: 1px solid rgba(15, 118, 110, 0.12);
  background: rgba(248, 250, 252, 0.92);
  color: #0f172a;
  font-weight: 850;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.target-option--active {
  background: linear-gradient(135deg, rgba(15, 118, 110, 0.12), rgba(20, 184, 166, 0.1));
  border-color: rgba(15, 118, 110, 0.32);
  color: #0f766e;
  box-shadow: inset 0 0 0 1px rgba(15, 118, 110, 0.12);
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

.composer-actions {
  margin-top: 0.95rem;
  align-items: flex-end;
}

.composer-actions--market {
  justify-content: flex-end;
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
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.45rem;
  min-height: 46px;
  padding: 0 1.1rem;
  border-radius: 999px;
}

.primary-action {
  background: linear-gradient(135deg, #0f766e, #f59e0b);
  color: #fff;
  box-shadow: 0 16px 32px rgba(15, 118, 110, 0.2);
}

.secondary-action {
  background: rgba(15, 23, 42, 0.04);
  color: #0f172a;
}

.secondary-action--danger {
  background: rgba(185, 28, 28, 0.08);
  color: #b91c1c;
}

.ghost-link {
  padding: 0;
  background: transparent;
  color: #0f766e;
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
