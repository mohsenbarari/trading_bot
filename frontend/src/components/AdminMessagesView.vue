<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { apiFetch } from '../utils/auth'

type AdminMarketMessage = {
  id: number
  content: string
  is_active: boolean
  notified_recipients_count: number
  published_at: string
}

type AdminBroadcastMessage = {
  id: number
  content: string
  target_groups: string[]
  recipient_count: number
  published_at: string
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
const marketHistory = ref<AdminMarketMessage[]>([])
const broadcastHistory = ref<AdminBroadcastMessage[]>([])
const marketError = ref('')
const broadcastError = ref('')
const marketSuccess = ref('')
const broadcastSuccess = ref('')
const isPublishingMarket = ref(false)
const isPublishingBroadcast = ref(false)
const isLoading = ref(false)

function formatDate(value: string) {
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

async function loadHistories() {
  isLoading.value = true
  try {
    const [marketRes, broadcastRes] = await Promise.all([
      apiFetch('/api/admin-messages/market/history?limit=50'),
      apiFetch('/api/admin-messages/broadcasts/history?limit=50'),
    ])
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
    await loadHistories()
  } catch (err) {
    marketError.value = err instanceof Error ? err.message : 'ثبت پیام بازار ناموفق بود'
  } finally {
    isPublishingMarket.value = false
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
    await loadHistories()
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

onMounted(loadHistories)
</script>

<template>
  <div class="admin-messages-view">
    <section class="admin-message-card">
      <div class="section-heading">
        <h3>پیام صفحه بازار</h3>
        <p>پیام فعال بالای صفحه بازار نمایش داده می‌شود. انتشار پیام جدید، پیام قبلی را از حالت فعال خارج می‌کند اما تاریخچه را نگه می‌دارد.</p>
      </div>
      <textarea v-model="marketContent" class="message-textarea" rows="6" placeholder="متن پیام بازار..."></textarea>
      <div v-if="marketError" class="alert error">{{ marketError }}</div>
      <div v-if="marketSuccess" class="alert success">{{ marketSuccess }}</div>
      <button class="primary-action" :disabled="!marketContent.trim() || isPublishingMarket" @click="publishMarketMessage">
        {{ isPublishingMarket ? 'در حال ثبت...' : 'انتشار در بازار' }}
      </button>
    </section>

    <section class="admin-message-card">
      <div class="section-heading">
        <h3>ارسال پیام مدیریت به پیام‌رسان</h3>
        <p>این پیام مستقل از کانال‌ها برای گیرندگان انتخاب‌شده ارسال می‌شود و در پیام‌رسان به شکل گفتگوی مدیریتی خواندنی نمایش داده می‌شود.</p>
      </div>
      <textarea v-model="broadcastContent" class="message-textarea" rows="6" placeholder="متن پیام همگانی..."></textarea>
      <div class="target-grid">
        <label v-for="option in targetOptions" :key="option.key" class="target-option">
          <input v-model="broadcastTargets" type="checkbox" :value="option.key" />
          <span>{{ option.label }}</span>
        </label>
      </div>
      <div v-if="broadcastError" class="alert error">{{ broadcastError }}</div>
      <div v-if="broadcastSuccess" class="alert success">{{ broadcastSuccess }}</div>
      <button class="primary-action" :disabled="!broadcastContent.trim() || broadcastTargets.length === 0 || isPublishingBroadcast" @click="publishBroadcastMessage">
        {{ isPublishingBroadcast ? 'در حال ارسال...' : 'ارسال همگانی' }}
      </button>
    </section>

    <section class="history-grid" :aria-busy="isLoading">
      <div class="history-card">
        <h3>تاریخچه پیام‌های بازار</h3>
        <article v-for="message in marketHistory" :key="message.id" class="history-item">
          <div class="history-meta">
            <span>{{ formatDate(message.published_at) }}</span>
            <strong v-if="message.is_active">فعال</strong>
          </div>
          <p>{{ message.content }}</p>
          <div class="history-footer">
            <span>{{ Number(message.notified_recipients_count || 0).toLocaleString('fa-IR') }} گیرنده اعلان</span>
            <button type="button" @click="reuseMarketMessage(message)">استفاده مجدد</button>
          </div>
        </article>
        <p v-if="!marketHistory.length" class="empty-history">هنوز پیامی ثبت نشده است.</p>
      </div>

      <div class="history-card">
        <h3>تاریخچه ارسال همگانی</h3>
        <article v-for="message in broadcastHistory" :key="message.id" class="history-item">
          <div class="history-meta">
            <span>{{ formatDate(message.published_at) }}</span>
          </div>
          <p>{{ message.content }}</p>
          <div class="target-summary">
            {{ message.target_groups.map(targetLabel).join('، ') }}
          </div>
          <div class="history-footer">
            <span>{{ Number(message.recipient_count || 0).toLocaleString('fa-IR') }} گیرنده</span>
            <button type="button" @click="reuseBroadcastMessage(message)">استفاده مجدد</button>
          </div>
        </article>
        <p v-if="!broadcastHistory.length" class="empty-history">هنوز پیامی ارسال نشده است.</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.admin-messages-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.admin-message-card,
.history-card {
  background: rgba(255, 255, 255, 0.78);
  border: 1px solid rgba(15, 118, 110, 0.14);
  border-radius: 18px;
  padding: 1rem;
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.07);
}

.section-heading h3,
.history-card h3 {
  margin: 0 0 0.35rem;
  color: #0f766e;
  font-size: 1rem;
  font-weight: 950;
}

.section-heading p {
  margin: 0 0 0.9rem;
  line-height: 1.75;
  color: #64748b;
  font-size: 0.8rem;
}

.message-textarea {
  width: 100%;
  min-height: 130px;
  resize: vertical;
  border: 1px solid rgba(245, 158, 11, 0.24);
  border-radius: 16px;
  padding: 0.85rem;
  background: #fff;
  color: #0f172a;
  font: inherit;
  line-height: 1.8;
}

.target-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.65rem;
  margin: 0.85rem 0;
}

.target-option {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  padding: 0.7rem 0.8rem;
  border-radius: 14px;
  background: rgba(236, 253, 245, 0.72);
  border: 1px solid rgba(15, 118, 110, 0.14);
  font-weight: 850;
  color: #0f172a;
}

.primary-action {
  margin-top: 0.85rem;
  min-height: 44px;
  padding: 0 1.2rem;
  border: 0;
  border-radius: 999px;
  background: linear-gradient(135deg, #0f766e, #f59e0b);
  color: #fff;
  font: inherit;
  font-weight: 950;
  cursor: pointer;
}

.primary-action:disabled {
  opacity: 0.52;
  cursor: not-allowed;
}

.alert {
  margin-top: 0.75rem;
  padding: 0.75rem 0.85rem;
  border-radius: 14px;
  font-weight: 850;
  font-size: 0.82rem;
}

.alert.error {
  color: #b91c1c;
  background: rgba(254, 226, 226, 0.92);
}

.alert.success {
  color: #047857;
  background: rgba(220, 252, 231, 0.92);
}

.history-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
}

.history-item {
  padding: 0.85rem 0;
  border-top: 1px solid rgba(15, 23, 42, 0.08);
}

.history-item p {
  margin: 0.55rem 0;
  white-space: pre-wrap;
  line-height: 1.75;
  color: #0f172a;
  font-weight: 700;
}

.history-meta,
.history-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.75rem;
  color: #64748b;
  font-size: 0.76rem;
}

.history-meta strong {
  color: #047857;
}

.history-footer button {
  border: 0;
  border-radius: 999px;
  padding: 0.4rem 0.7rem;
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
  font: inherit;
  font-weight: 900;
  cursor: pointer;
}

.target-summary {
  margin: 0.4rem 0 0.65rem;
  color: #0f766e;
  font-size: 0.78rem;
  font-weight: 850;
}

.empty-history {
  color: #94a3b8;
  font-size: 0.85rem;
}

@media (min-width: 900px) {
  .history-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
