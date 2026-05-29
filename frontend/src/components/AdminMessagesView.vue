<script setup lang="ts">
import { Megaphone, Pin, PinOff, Radio, SendHorizontal, Users } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { apiFetch } from '../utils/auth'

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
const broadcastHistory = ref<AdminBroadcastMessage[]>([])
const marketError = ref('')
const broadcastError = ref('')
const marketSuccess = ref('')
const broadcastSuccess = ref('')
const isPublishingMarket = ref(false)
const isPublishingBroadcast = ref(false)
const isClearingMarketPin = ref(false)
const isLoading = ref(false)

const marketArchive = computed(() => marketHistory.value.filter((message) => message.id !== activeMarketMessage.value?.id))
const selectedBroadcastLabels = computed(() => targetOptions.filter((option) => broadcastTargets.value.includes(option.key)).map((option) => option.label))
const selectedBroadcastCount = computed(() => selectedBroadcastLabels.value.length)

function formatDate(value: string | undefined) {
  if (!value) return ''
  return new Intl.DateTimeFormat('fa-IR-u-ca-persian', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'Asia/Tehran',
  }).format(new Date(value))
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
    <section class="admin-messages-hero">
      <p class="hero-eyebrow">پیام‌های مدیریت</p>
      <h2>بازار و پیام‌رسان را جدا اما هم‌زمان مدیریت کن</h2>
      <p>
        پیام پین‌شده بازار و پیام‌های همگانی پیام‌رسان دو خروجی متفاوت دارند. این صفحه هر کدام را در lane مستقل نگه می‌دارد تا انتشار، بازبینی تاریخچه،
        و کنترل وضعیت فعال بدون سردرگمی انجام شود.
      </p>
    </section>

    <div class="message-workspace" :aria-busy="isLoading">
      <section class="message-lane message-lane--market" data-test="market-lane">
        <header class="lane-header lane-header--market">
          <span class="lane-icon"><Pin :size="18" /></span>
          <div>
            <p class="lane-kicker">بازار</p>
            <h3>پیام پین‌شده صفحه بازار</h3>
            <p>پیام فعال همان چیزی است که بالای صفحه بازار دیده می‌شود. انتشار جدید، پیام قبلی را از پین خارج می‌کند ولی تاریخچه را نگه می‌دارد.</p>
          </div>
        </header>

        <article v-if="activeMarketMessage" class="status-card status-card--market-active" data-test="active-market-message">
          <div class="status-card-header">
            <div>
              <span class="status-pill status-pill--active">پین فعال</span>
              <p class="status-meta">{{ formatDate(activeMarketMessage.published_at) }}</p>
            </div>
            <button
              type="button"
              class="secondary-action secondary-action--danger"
              data-test="clear-market-pin"
              :disabled="isClearingMarketPin"
              @click="clearMarketPin"
            >
              <PinOff :size="16" />
              <span>{{ isClearingMarketPin ? 'در حال برداشتن...' : 'برداشتن پین' }}</span>
            </button>
          </div>
          <p class="status-copy">{{ activeMarketMessage.content }}</p>
          <div class="status-footer">
            <span>{{ Number(activeMarketMessage.notified_recipients_count || 0).toLocaleString('fa-IR') }} گیرنده اعلان</span>
            <button type="button" class="ghost-link" @click="reuseMarketMessage(activeMarketMessage)">ویرایش و بازنشر</button>
          </div>
        </article>

        <article v-else class="status-card status-card--empty">
          <div class="status-card-header">
            <div>
              <span class="status-pill status-pill--muted">بدون پین فعال</span>
              <p class="status-meta">بازار اکنون پیام سنجاق‌شده‌ای ندارد.</p>
            </div>
          </div>
          <p class="status-copy status-copy--muted">در حال حاضر هیچ پیام پین‌شده‌ای برای بازار فعال نیست. از فرم زیر برای انتشار پیام جدید استفاده کن.</p>
        </article>

        <section class="composer-card">
          <div class="composer-header">
            <div>
              <h4>نوشتن پیام بازار</h4>
              <p>متنی بنویس که در بالای بازار نمایش داده شود و برای مخاطبان بازار اعلان ایجاد کند.</p>
            </div>
            <span class="composer-counter">{{ marketContent.trim().length.toLocaleString('fa-IR') }} کاراکتر</span>
          </div>
          <textarea v-model="marketContent" class="message-textarea" rows="7" placeholder="متن پیام بازار..."></textarea>
          <div v-if="marketError" class="alert error">{{ marketError }}</div>
          <div v-if="marketSuccess" class="alert success">{{ marketSuccess }}</div>
          <div class="composer-actions">
            <div class="composer-hint">
              <Radio :size="16" />
              <span>فقط یک پیام بازار می‌تواند هم‌زمان پین باشد.</span>
            </div>
            <button class="primary-action" :disabled="!marketContent.trim() || isPublishingMarket" @click="publishMarketMessage">
              <Pin :size="16" />
              <span>{{ isPublishingMarket ? 'در حال ثبت...' : 'انتشار و پین در بازار' }}</span>
            </button>
          </div>
        </section>

        <section class="history-card">
          <div class="history-header">
            <div>
              <h4>آرشیو پیام‌های بازار</h4>
              <p>پیام‌های قبلی را برای بازنشر یا بازبینی نگه می‌داریم.</p>
            </div>
            <span class="history-badge">{{ marketArchive.length.toLocaleString('fa-IR') }} مورد</span>
          </div>

          <article v-for="message in marketArchive" :key="message.id" class="history-item">
            <div class="history-meta">
              <span>{{ formatDate(message.published_at) }}</span>
              <span>{{ Number(message.notified_recipients_count || 0).toLocaleString('fa-IR') }} اعلان</span>
            </div>
            <p>{{ message.content }}</p>
            <div class="history-footer">
              <button type="button" class="ghost-link" @click="reuseMarketMessage(message)">استفاده مجدد</button>
            </div>
          </article>

          <p v-if="!marketArchive.length" class="empty-history">هنوز آرشیو جداگانه‌ای برای بازار ثبت نشده است.</p>
        </section>
      </section>

      <section class="message-lane message-lane--broadcast" data-test="broadcast-lane">
        <header class="lane-header lane-header--broadcast">
          <span class="lane-icon"><Megaphone :size="18" /></span>
          <div>
            <p class="lane-kicker">پیام‌رسان</p>
            <h3>ارسال همگانی مدیریت</h3>
            <p>این پیام‌ها در اتاق‌های مدیریتِ فقط‌خواندنی نمایش داده می‌شوند و مستقل از کانال بازار هستند.</p>
          </div>
        </header>

        <article class="status-card status-card--broadcast">
          <div class="status-card-header">
            <div>
              <span class="status-pill status-pill--info">مخاطبان انتخاب‌شده</span>
              <p class="status-meta">{{ selectedBroadcastCount.toLocaleString('fa-IR') }} گروه مقصد</p>
            </div>
            <span class="history-badge">{{ selectedBroadcastLabels.join('، ') || 'بدون انتخاب' }}</span>
          </div>
          <p class="status-copy status-copy--muted">پیام با عنوان «پیام مدیریت» برای هر کاربر در اتاق مدیریت خودش ثبت می‌شود و هویت واقعی ادمین به گیرنده نشان داده نمی‌شود.</p>
        </article>

        <section class="composer-card">
          <div class="composer-header">
            <div>
              <h4>نوشتن پیام همگانی</h4>
              <p>گیرنده‌ها را انتخاب کن و پیام را یک‌بار برای همه آن‌ها در پیام‌رسان ثبت کن.</p>
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
            <div class="composer-hint">
              <SendHorizontal :size="16" />
              <span>اتاق مقصد فقط‌خواندنی است و unread عادی پیام‌رسان را هم به‌روزرسانی می‌کند.</span>
            </div>
            <button class="primary-action" :disabled="!broadcastContent.trim() || broadcastTargets.length === 0 || isPublishingBroadcast" @click="publishBroadcastMessage">
              <Megaphone :size="16" />
              <span>{{ isPublishingBroadcast ? 'در حال ارسال...' : 'ارسال همگانی در پیام‌رسان' }}</span>
            </button>
          </div>
        </section>

        <section class="history-card">
          <div class="history-header">
            <div>
              <h4>آرشیو ارسال‌های همگانی</h4>
              <p>آخرین پیام‌های ثبت‌شده برای بازاستفاده و بررسی مقصدها.</p>
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
  gap: 1.2rem;
}

.admin-messages-hero {
  padding: 1.15rem 1.2rem;
  border-radius: 24px;
  background:
    radial-gradient(circle at top left, rgba(245, 158, 11, 0.18), transparent 32%),
    linear-gradient(135deg, rgba(15, 118, 110, 0.09), rgba(255, 255, 255, 0.92));
  border: 1px solid rgba(15, 118, 110, 0.12);
  box-shadow: 0 22px 40px rgba(15, 23, 42, 0.08);
}

.hero-eyebrow {
  margin: 0 0 0.35rem;
  font-size: 0.74rem;
  font-weight: 900;
  letter-spacing: 0.08em;
  color: #0f766e;
}

.admin-messages-hero h2 {
  margin: 0;
  color: #0f172a;
  font-size: 1.35rem;
  font-weight: 950;
}

.admin-messages-hero p:last-child {
  margin: 0.55rem 0 0;
  color: #475569;
  line-height: 1.9;
  font-size: 0.88rem;
}

.message-workspace {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
  align-items: start;
}

.message-lane {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 1rem;
  padding: 1rem;
  border-radius: 24px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: rgba(255, 255, 255, 0.82);
  box-shadow: 0 20px 40px rgba(15, 23, 42, 0.07);
}

.message-lane--market {
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98), rgba(255, 255, 255, 0.92));
}

.message-lane--broadcast {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.98), rgba(255, 255, 255, 0.92));
}

.lane-header {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.9rem;
  align-items: start;
}

.lane-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.4rem;
  height: 2.4rem;
  border-radius: 16px;
  color: #fff;
  box-shadow: 0 10px 20px rgba(15, 23, 42, 0.14);
}

.lane-header--market .lane-icon {
  background: linear-gradient(135deg, #f59e0b, #d97706);
}

.lane-header--broadcast .lane-icon {
  background: linear-gradient(135deg, #0f766e, #14b8a6);
}

.lane-kicker {
  margin: 0 0 0.18rem;
  color: #64748b;
  font-size: 0.76rem;
  font-weight: 900;
}

.lane-header h3,
.composer-header h4,
.history-header h4 {
  margin: 0;
  color: #0f172a;
  font-size: 1rem;
  font-weight: 950;
}

.lane-header p,
.composer-header p,
.history-header p,
.status-meta,
.status-copy--muted,
.empty-history,
.composer-hint,
.target-summary {
  margin: 0;
  color: #64748b;
  line-height: 1.75;
}

.status-card,
.composer-card,
.history-card,
.audience-panel {
  border-radius: 20px;
  border: 1px solid rgba(15, 23, 42, 0.08);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.05);
}

.status-card,
.composer-card,
.history-card {
  padding: 1rem;
}

.status-card--market-active {
  background: linear-gradient(135deg, rgba(255, 247, 237, 0.98), rgba(255, 255, 255, 0.92));
}

.status-card-header,
.composer-header,
.history-header,
.audience-header,
.history-meta,
.history-footer,
.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.8rem;
}

.status-card-header,
.composer-header,
.history-header,
.audience-header {
  align-items: start;
}

.status-pill,
.history-badge,
.composer-counter {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.9rem;
  padding: 0 0.75rem;
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 900;
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
.history-badge,
.composer-counter {
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

.status-footer,
.audience-title {
  display: flex;
  align-items: center;
  gap: 0.55rem;
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

.audience-title {
  font-size: 0.82rem;
  font-weight: 900;
  color: #0f172a;
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

@media (min-width: 1120px) {
  .message-workspace {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .composer-actions,
  .history-meta,
  .history-footer,
  .status-card-header,
  .audience-header {
    flex-direction: column;
    align-items: stretch;
  }

  .target-grid {
    grid-template-columns: 1fr;
  }

  .primary-action,
  .secondary-action {
    width: 100%;
  }
}
</style>
