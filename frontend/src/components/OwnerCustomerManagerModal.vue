<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { apiFetch } from '../utils/auth'

const emit = defineEmits<{
  (e: 'close'): void
}>()

type RelationStatus = 'pending' | 'active' | 'expired' | 'revoked' | 'deleted' | string
type CustomerTier = 'tier1' | 'tier2'

interface CustomerRelation {
  id: number
  owner_user_id: number
  customer_user_id: number | null
  customer_account_name: string | null
  invitation_account_name: string | null
  mobile_number: string | null
  management_name: string
  customer_tier: CustomerTier
  commission_rate: number | null
  min_trade_quantity: number | null
  max_trade_quantity: number | null
  max_daily_trades: number | null
  max_daily_commodity_volume: number | null
  status: RelationStatus
  invitation_token: string
  registration_link: string | null
  expires_at: string | null
  activated_at: string | null
  deleted_at: string | null
  created_at: string
}

interface CustomerSessionSummary {
  id: string
  device_name: string
  device_ip: string | null
  platform: string
  home_server: string
  is_primary: boolean
  is_active: boolean
  created_at: string | null
  last_active_at: string | null
}

function makeEmptyCreateForm() {
  return {
    account_name: '',
    management_name: '',
    mobile_number: '',
    customer_tier: 'tier1' as CustomerTier,
    commission_rate: '',
    min_trade_quantity: '',
    max_trade_quantity: '',
    max_daily_trades: '',
    max_daily_commodity_volume: '',
  }
}

function makeEmptyEditForm() {
  return {
    customer_tier: 'tier1' as CustomerTier,
    commission_rate: '',
    min_trade_quantity: '',
    max_trade_quantity: '',
    max_daily_trades: '',
    max_daily_commodity_volume: '',
  }
}

const relations = ref<CustomerRelation[]>([])
const isLoading = ref(true)
const isRefreshing = ref(false)
const isSubmitting = ref(false)
const isSavingEdit = ref(false)
const editingRelationId = ref<number | null>(null)
const error = ref('')
const notice = ref('')
const copiedRelationId = ref<number | null>(null)
const openSessionsRelationId = ref<number | null>(null)
const sessionsByRelationId = ref<Record<number, CustomerSessionSummary[]>>({})
const loadingSessionsRelationId = ref<number | null>(null)
const terminatingSessionId = ref<string | null>(null)
const currentTimeMs = ref(Date.now())

const createForm = reactive(makeEmptyCreateForm())
const editForm = reactive(makeEmptyEditForm())
let countdownTimer: number | null = null

function parseApiError(payload: unknown, fallback: string) {
  if (typeof payload === 'object' && payload && 'detail' in payload) {
    const detail = (payload as { detail?: unknown }).detail
    if (typeof detail === 'string' && detail.trim()) {
      return detail
    }
  }
  return fallback
}

function resetCreateForm() {
  Object.assign(createForm, makeEmptyCreateForm())
}

function clearEditState() {
  editingRelationId.value = null
  Object.assign(editForm, makeEmptyEditForm())
}

function formatDateTime(value: string | null) {
  if (!value) return '---'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('fa-IR')
}

function getRelationSessions(relationId: number) {
  return sessionsByRelationId.value[relationId] ?? []
}

function formatSessionPlatform(platform: string) {
  if (platform === 'telegram_mini_app') return 'تلگرام'
  if (platform === 'android') return 'اندروید'
  if (platform === 'web') return 'وب'
  return platform || 'نامشخص'
}

function formatHomeServer(homeServer: string) {
  if (homeServer === 'iran') return 'ایران'
  if (homeServer === 'foreign') return 'خارج'
  return homeServer || 'نامشخص'
}

function normalizeOptionalNumber(value: string | number | null | undefined) {
  if (value == null) return null
  const cleaned = String(value).trim()
  if (!cleaned) return null
  const normalized = Number(cleaned)
  return Number.isFinite(normalized) ? normalized : null
}

function buildCustomerPayload(form: {
  customer_tier: CustomerTier
  commission_rate: string | number
  min_trade_quantity: string | number
  max_trade_quantity: string | number
  max_daily_trades: string | number
  max_daily_commodity_volume: string | number
}) {
  return {
    customer_tier: form.customer_tier,
    commission_rate: form.customer_tier === 'tier2' ? normalizeOptionalNumber(form.commission_rate) : null,
    min_trade_quantity: normalizeOptionalNumber(form.min_trade_quantity),
    max_trade_quantity: normalizeOptionalNumber(form.max_trade_quantity),
    max_daily_trades: normalizeOptionalNumber(form.max_daily_trades),
    max_daily_commodity_volume: normalizeOptionalNumber(form.max_daily_commodity_volume),
  }
}

function getRemainingMs(value: string | null) {
  if (!value) return null
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return null
  return timestamp - currentTimeMs.value
}

function formatCountdown(value: string | null) {
  const remainingMs = getRemainingMs(value)
  if (remainingMs == null) return '---'
  if (remainingMs <= 0) return '00:00:00'

  const totalSeconds = Math.floor(remainingMs / 1000)
  const days = Math.floor(totalSeconds / 86400)
  const hours = Math.floor((totalSeconds % 86400) / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const clock = [hours, minutes, seconds].map((part) => String(part).padStart(2, '0')).join(':')
  if (days > 0) {
    return `${days} روز و ${clock}`
  }
  return clock
}

function getRelationAccountName(relation: CustomerRelation) {
  return relation.customer_account_name || relation.invitation_account_name || 'unknown'
}

function getCustomerTierLabel(tier: CustomerTier) {
  return tier === 'tier2' ? 'سطح 2' : 'سطح 1'
}

function formatMaybeNumber(value: number | null, suffix = '') {
  if (value == null) return '---'
  return `${value}${suffix}`
}

function getRelationStateText(relation: CustomerRelation) {
  if (relation.status === 'pending') {
    const remainingMs = getRemainingMs(relation.expires_at)
    if (remainingMs == null) return 'دعوت ثبت شده و در انتظار ثبت نام مشتری است.'
    if (remainingMs <= 0) return 'مهلت این دعوت تمام شده و در انتظار همگام سازی وضعیت است.'
    return `مهلت ثبت نام: ${formatCountdown(relation.expires_at)}`
  }

  if (relation.status === 'active') {
    return `این مشتری با @${getRelationAccountName(relation)} در ${getCustomerTierLabel(relation.customer_tier)} فعال است.`
  }

  if (relation.status === 'expired') {
    return 'مهلت این دعوت به پایان رسیده است.'
  }

  if (relation.status === 'revoked') {
    return 'این دعوت توسط مالک لغو شده است.'
  }

  if (relation.status === 'deleted') {
    return 'این رابطه حذف شده است.'
  }

  return ''
}

function refreshCurrentTime() {
  currentTimeMs.value = Date.now()
}

function startCountdownTimer() {
  if (countdownTimer !== null || typeof window === 'undefined') return
  countdownTimer = window.setInterval(() => {
    refreshCurrentTime()
  }, 1000)
}

function stopCountdownTimer() {
  if (countdownTimer === null || typeof window === 'undefined') return
  window.clearInterval(countdownTimer)
  countdownTimer = null
}

function statusLabel(status: RelationStatus) {
  return {
    pending: 'در انتظار ثبت‌نام',
    active: 'فعال',
    expired: 'منقضی‌شده',
    revoked: 'لغوشده',
    deleted: 'حذف‌شده',
  }[status] || status
}

const orderedRelations = computed(() => {
  const weight = (status: RelationStatus) => {
    if (status === 'pending') return 0
    if (status === 'active') return 1
    return 2
  }
  return [...relations.value].sort((left, right) => {
    const statusDiff = weight(left.status) - weight(right.status)
    if (statusDiff !== 0) return statusDiff
    return String(right.created_at).localeCompare(String(left.created_at))
  })
})

async function loadRelations(options?: { silent?: boolean }) {
  if (options?.silent) {
    isRefreshing.value = true
  } else {
    isLoading.value = true
  }
  error.value = ''

  try {
    const response = await apiFetch('/api/customers/owner-relations')
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت لیست مشتریان ناموفق بود.'))
    }
    relations.value = Array.isArray(payload) ? payload : []
    if (openSessionsRelationId.value !== null) {
      const openRelation = relations.value.find((relation) => relation.id === openSessionsRelationId.value)
      if (!openRelation || openRelation.status !== 'active' || !openRelation.customer_user_id) {
        openSessionsRelationId.value = null
      }
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت لیست مشتریان ناموفق بود.'
  } finally {
    isLoading.value = false
    isRefreshing.value = false
  }
}

async function loadSessionsForRelation(relationId: number) {
  loadingSessionsRelationId.value = relationId
  error.value = ''

  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relationId}/sessions`, {
      method: 'GET',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت نشست‌های مشتری ناموفق بود.'))
    }
    sessionsByRelationId.value = {
      ...sessionsByRelationId.value,
      [relationId]: Array.isArray(payload) ? (payload as CustomerSessionSummary[]) : [],
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت نشست‌های مشتری ناموفق بود.'
  } finally {
    if (loadingSessionsRelationId.value === relationId) {
      loadingSessionsRelationId.value = null
    }
  }
}

async function toggleSessionPanel(relation: CustomerRelation) {
  if (relation.status !== 'active' || !relation.customer_user_id) return
  if (openSessionsRelationId.value === relation.id) {
    openSessionsRelationId.value = null
    return
  }
  openSessionsRelationId.value = relation.id
  await loadSessionsForRelation(relation.id)
}

async function terminateCustomerSession(relation: CustomerRelation, session: CustomerSessionSummary) {
  if (terminatingSessionId.value === session.id) return
  if (!window.confirm(`نشست «${session.device_name || 'دستگاه مشتری'}» پایان یابد؟`)) return

  terminatingSessionId.value = session.id
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relation.id}/sessions/${session.id}`, {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'پایان دادن نشست مشتری ناموفق بود.'))
    }
    notice.value = parseApiError(payload, 'نشست مشتری با موفقیت پایان یافت.')
    await loadSessionsForRelation(relation.id)
  } catch (err: any) {
    error.value = err?.message || 'پایان دادن نشست مشتری ناموفق بود.'
  } finally {
    if (terminatingSessionId.value === session.id) {
      terminatingSessionId.value = null
    }
  }
}

function startEditing(relation: CustomerRelation) {
  editingRelationId.value = relation.id
  editForm.customer_tier = relation.customer_tier
  editForm.commission_rate = relation.commission_rate == null ? '' : String(relation.commission_rate)
  editForm.min_trade_quantity = relation.min_trade_quantity == null ? '' : String(relation.min_trade_quantity)
  editForm.max_trade_quantity = relation.max_trade_quantity == null ? '' : String(relation.max_trade_quantity)
  editForm.max_daily_trades = relation.max_daily_trades == null ? '' : String(relation.max_daily_trades)
  editForm.max_daily_commodity_volume = relation.max_daily_commodity_volume == null ? '' : String(relation.max_daily_commodity_volume)
  if (editForm.customer_tier !== 'tier2') {
    editForm.commission_rate = ''
  }
  notice.value = ''
  error.value = ''
}

function handleCreateTierChange() {
  if (createForm.customer_tier !== 'tier2') {
    createForm.commission_rate = ''
  }
}

function handleEditTierChange() {
  if (editForm.customer_tier !== 'tier2') {
    editForm.commission_rate = ''
  }
}

async function createRelation() {
  if (isSubmitting.value) return
  isSubmitting.value = true
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch('/api/customers/owner-relations', {
      method: 'POST',
      body: JSON.stringify({
        account_name: createForm.account_name,
        management_name: createForm.management_name,
        mobile_number: createForm.mobile_number,
        ...buildCustomerPayload(createForm),
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'ایجاد مشتری ناموفق بود.'))
    }
    const created = payload as CustomerRelation
    relations.value = [created, ...relations.value.filter((item) => item.id !== created.id)]
    resetCreateForm()
    notice.value = 'دعوت مشتری ثبت شد.'
  } catch (err: any) {
    error.value = err?.message || 'ایجاد مشتری ناموفق بود.'
  } finally {
    isSubmitting.value = false
  }
}

async function saveEdit(relationId: number) {
  if (isSavingEdit.value) return
  isSavingEdit.value = true
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relationId}`, {
      method: 'PATCH',
      body: JSON.stringify(buildCustomerPayload(editForm)),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'ویرایش مشتری ناموفق بود.'))
    }
    const updated = payload as CustomerRelation
    relations.value = relations.value.map((item) => (item.id === updated.id ? updated : item))
    clearEditState()
    notice.value = 'اطلاعات مشتری به‌روزرسانی شد.'
  } catch (err: any) {
    error.value = err?.message || 'ویرایش مشتری ناموفق بود.'
  } finally {
    isSavingEdit.value = false
  }
}

async function unlinkRelation(relation: CustomerRelation) {
  const isPending = relation.status === 'pending'
  const isActive = relation.status === 'active'
  if (!isPending && !isActive) return

  const promptMessage = isPending
    ? `دعوت ${relation.management_name} لغو شود؟`
    : `ارتباط مشتری ${relation.management_name} قطع شود؟ این عملیات دسترسی مشتری را کامل غیرفعال می‌کند.`
  if (!window.confirm(promptMessage)) return

  error.value = ''
  notice.value = ''
  try {
    const response = await apiFetch(`/api/customers/owner-relations/${relation.id}`, {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, isPending ? 'لغو دعوت مشتری ناموفق بود.' : 'قطع ارتباط مشتری ناموفق بود.'))
    }
    relations.value = relations.value.filter((item) => item.id !== relation.id)
    if (editingRelationId.value === relation.id) {
      clearEditState()
    }
    if (openSessionsRelationId.value === relation.id) {
      openSessionsRelationId.value = null
    }
    notice.value = isPending ? 'دعوت مشتری لغو شد.' : 'ارتباط مشتری قطع شد و دسترسی او غیرفعال گردید.'
  } catch (err: any) {
    error.value = err?.message || (isPending ? 'لغو دعوت مشتری ناموفق بود.' : 'قطع ارتباط مشتری ناموفق بود.')
  }
}

async function copyRegistrationLink(relation: CustomerRelation) {
  if (!relation.registration_link) return
  try {
    await navigator.clipboard.writeText(relation.registration_link)
    copiedRelationId.value = relation.id
    window.setTimeout(() => {
      if (copiedRelationId.value === relation.id) {
        copiedRelationId.value = null
      }
    }, 1800)
  } catch {
    error.value = 'کپی لینک ثبت‌نام ممکن نشد.'
  }
}

onMounted(() => {
  startCountdownTimer()
  void loadRelations()
})

onBeforeUnmount(() => {
  stopCountdownTimer()
})
</script>

<template>
  <Teleport to="body">
    <div class="customer-manager-backdrop" @click.self="emit('close')">
      <div class="customer-manager-shell">
        <div class="customer-manager-header">
          <div>
            <p class="customer-manager-kicker">مدیریت ارتباطات</p>
            <h3>مشتریان مالک</h3>
          </div>
          <button type="button" class="customer-manager-close" @click="emit('close')">بستن</button>
        </div>

        <div class="customer-manager-note">
          این لیست فقط مشتریان فعال و در انتظار ثبت‌نام را نشان می‌دهد. نام کاربری، نام مدیریتی و موبایل بعد از ایجاد ثابت می‌مانند و در ویرایش فقط سطح و محدودیت‌های معاملاتی تغییر می‌کند.
        </div>

        <div v-if="notice" class="customer-banner success">{{ notice }}</div>
        <div v-if="error" class="customer-banner error">{{ error }}</div>

        <section class="customer-panel">
          <div class="panel-title-row">
            <div>
              <h4>افزودن مشتری جدید</h4>
              <p>دعوت مشتری از همین پنل ثبت می‌شود و در صورت نیاز می‌توانید لینک ثبت‌نام را کپی کنید.</p>
            </div>
            <button type="button" class="ghost-btn" :disabled="isRefreshing" @click="loadRelations({ silent: true })">
              {{ isRefreshing ? 'در حال بروزرسانی...' : 'بروزرسانی لیست' }}
            </button>
          </div>

          <div class="customer-form-grid">
            <label class="field-block">
              <span>نام کاربری</span>
              <input v-model.trim="createForm.account_name" class="customer-input create-account-name" type="text" placeholder="customer_user" />
            </label>
            <label class="field-block">
              <span>نام مدیریتی</span>
              <input v-model.trim="createForm.management_name" class="customer-input create-management-name" type="text" placeholder="نام نمایشی مشتری" />
            </label>
            <label class="field-block">
              <span>شماره موبایل</span>
              <input v-model.trim="createForm.mobile_number" class="customer-input create-mobile-number" type="tel" inputmode="numeric" placeholder="0912xxxxxxx" />
            </label>
            <label class="field-block">
              <span>سطح مشتری</span>
              <select v-model="createForm.customer_tier" class="customer-input create-tier-select" @change="handleCreateTierChange">
                <option value="tier1">سطح 1</option>
                <option value="tier2">سطح 2</option>
              </select>
            </label>
            <label v-if="createForm.customer_tier === 'tier2'" class="field-block">
              <span>درصد کمیسیون</span>
              <input v-model.trim="createForm.commission_rate" class="customer-input create-commission-rate" type="number" min="0" max="100" step="0.01" placeholder="0.50" />
            </label>
            <label class="field-block">
              <span>حداقل مقدار معامله</span>
              <input v-model.trim="createForm.min_trade_quantity" class="customer-input create-min-trade" type="number" min="0" step="1" placeholder="اختیاری" />
            </label>
            <label class="field-block">
              <span>حداکثر مقدار معامله</span>
              <input v-model.trim="createForm.max_trade_quantity" class="customer-input create-max-trade" type="number" min="0" step="1" placeholder="اختیاری" />
            </label>
            <label class="field-block">
              <span>سقف معاملات روزانه</span>
              <input v-model.trim="createForm.max_daily_trades" class="customer-input create-max-daily-trades" type="number" min="0" step="1" placeholder="اختیاری" />
            </label>
            <label class="field-block">
              <span>سقف حجم روزانه</span>
              <input v-model.trim="createForm.max_daily_commodity_volume" class="customer-input create-max-daily-volume" type="number" min="0" step="1" placeholder="اختیاری" />
            </label>
          </div>

          <div class="panel-actions">
            <button type="button" class="secondary-btn" :disabled="isSubmitting" @click="resetCreateForm">پاک کردن فرم</button>
            <button type="button" class="primary-btn submit-create" :disabled="isSubmitting" @click="createRelation">
              {{ isSubmitting ? 'در حال ثبت...' : 'ثبت مشتری' }}
            </button>
          </div>
        </section>

        <section class="customer-panel">
          <div class="panel-title-row">
            <div>
              <h4>مشتریان فعال و pending</h4>
              <p>برای مشتری فعال می‌توانید سطح و محدودیت‌ها را به‌روزرسانی یا ارتباط را قطع کنید.</p>
            </div>
          </div>

          <div v-if="isLoading" class="customer-loading">در حال دریافت لیست مشتریان...</div>
          <div v-else-if="orderedRelations.length === 0" class="customer-empty">هنوز مشتری فعالی یا دعوت pending ثبت نشده است.</div>

          <div v-else class="customer-list">
            <article
              v-for="relation in orderedRelations"
              :key="relation.id"
              class="customer-card"
            >
              <div class="customer-card-head">
                <div>
                  <h5>{{ relation.management_name }}</h5>
                  <p class="customer-account-name">@{{ getRelationAccountName(relation) }}</p>
                </div>
                <span class="customer-status-badge" :class="`status-${relation.status}`">{{ statusLabel(relation.status) }}</span>
              </div>

              <div class="customer-meta-grid">
                <div class="meta-item">
                  <span class="meta-label">سطح</span>
                  <span class="meta-value">{{ getCustomerTierLabel(relation.customer_tier) }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">کمیسیون</span>
                  <span class="meta-value">{{ formatMaybeNumber(relation.commission_rate, '%') }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">حداقل معامله</span>
                  <span class="meta-value">{{ formatMaybeNumber(relation.min_trade_quantity) }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">حداکثر معامله</span>
                  <span class="meta-value">{{ formatMaybeNumber(relation.max_trade_quantity) }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">سقف معاملات روزانه</span>
                  <span class="meta-value">{{ formatMaybeNumber(relation.max_daily_trades) }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">سقف حجم روزانه</span>
                  <span class="meta-value">{{ formatMaybeNumber(relation.max_daily_commodity_volume) }}</span>
                </div>
                <div v-if="relation.status === 'active'" class="meta-item">
                  <span class="meta-label">فعال‌سازی</span>
                  <span class="meta-value">{{ formatDateTime(relation.activated_at) }}</span>
                </div>
                <div v-if="relation.status === 'pending'" class="meta-item">
                  <span class="meta-label">انقضا</span>
                  <span class="meta-value">{{ formatDateTime(relation.expires_at) }}</span>
                </div>
              </div>

              <p v-if="getRelationStateText(relation)" class="customer-state-copy" :class="`status-${relation.status}`">{{ getRelationStateText(relation) }}</p>

              <div v-if="editingRelationId === relation.id" class="edit-panel">
                <div class="customer-form-grid compact-grid">
                  <label class="field-block">
                    <span>سطح مشتری</span>
                    <select v-model="editForm.customer_tier" class="customer-input edit-tier-select" @change="handleEditTierChange">
                      <option value="tier1">سطح 1</option>
                      <option value="tier2">سطح 2</option>
                    </select>
                  </label>
                  <label v-if="editForm.customer_tier === 'tier2'" class="field-block">
                    <span>درصد کمیسیون</span>
                    <input v-model.trim="editForm.commission_rate" class="customer-input edit-commission-rate" type="number" min="0" max="100" step="0.01" placeholder="0.50" />
                  </label>
                  <label class="field-block">
                    <span>حداقل مقدار معامله</span>
                    <input v-model.trim="editForm.min_trade_quantity" class="customer-input edit-min-trade" type="number" min="0" step="1" placeholder="اختیاری" />
                  </label>
                  <label class="field-block">
                    <span>حداکثر مقدار معامله</span>
                    <input v-model.trim="editForm.max_trade_quantity" class="customer-input edit-max-trade" type="number" min="0" step="1" placeholder="اختیاری" />
                  </label>
                  <label class="field-block">
                    <span>سقف معاملات روزانه</span>
                    <input v-model.trim="editForm.max_daily_trades" class="customer-input edit-max-daily-trades" type="number" min="0" step="1" placeholder="اختیاری" />
                  </label>
                  <label class="field-block">
                    <span>سقف حجم روزانه</span>
                    <input v-model.trim="editForm.max_daily_commodity_volume" class="customer-input edit-max-daily-volume" type="number" min="0" step="1" placeholder="اختیاری" />
                  </label>
                </div>
                <div class="panel-actions compact">
                  <button type="button" class="secondary-btn" :disabled="isSavingEdit" @click="clearEditState">انصراف</button>
                  <button type="button" class="primary-btn save-edit" :disabled="isSavingEdit" @click="saveEdit(relation.id)">
                    {{ isSavingEdit ? 'در حال ذخیره...' : 'ذخیره تغییرات' }}
                  </button>
                </div>
              </div>
              <div v-else class="customer-actions">
                <button type="button" class="secondary-btn start-edit" @click="startEditing(relation)">ویرایش</button>
                <button
                  v-if="relation.status === 'active' && relation.customer_user_id"
                  type="button"
                  class="secondary-btn toggle-sessions"
                  @click="toggleSessionPanel(relation)"
                >
                  {{ openSessionsRelationId === relation.id ? 'بستن نشست‌ها' : 'نشست‌های فعال' }}
                </button>
                <button
                  v-if="relation.status === 'pending' && relation.registration_link"
                  type="button"
                  class="secondary-btn copy-link"
                  @click="copyRegistrationLink(relation)"
                >
                  {{ copiedRelationId === relation.id ? 'کپی شد' : 'کپی لینک ثبت‌نام' }}
                </button>
                <button
                  v-if="relation.status === 'pending'"
                  type="button"
                  class="danger-btn cancel-pending"
                  @click="unlinkRelation(relation)"
                >
                  لغو دعوت
                </button>
                <button
                  v-if="relation.status === 'active'"
                  type="button"
                  class="danger-btn unlink-active"
                  @click="unlinkRelation(relation)"
                >
                  قطع ارتباط
                </button>
              </div>

              <div v-if="relation.status === 'active' && openSessionsRelationId === relation.id" class="session-panel">
                <div class="session-panel-header">
                  <div>
                    <h6>نشست‌های فعال مشتری</h6>
                    <p>نشست‌های فعال این مشتری را می‌توانید ببینید و هر نشست را جداگانه خاتمه دهید.</p>
                  </div>
                  <button
                    type="button"
                    class="ghost-btn refresh-sessions"
                    :disabled="loadingSessionsRelationId === relation.id"
                    @click="loadSessionsForRelation(relation.id)"
                  >
                    {{ loadingSessionsRelationId === relation.id ? 'در حال نوسازی...' : 'نوسازی' }}
                  </button>
                </div>

                <div v-if="loadingSessionsRelationId === relation.id" class="customer-loading session-loading">
                  در حال دریافت نشست‌های مشتری...
                </div>
                <div v-else-if="!getRelationSessions(relation.id).length" class="customer-empty session-empty">
                  در حال حاضر نشست فعالی برای این مشتری ثبت نشده است.
                </div>
                <ul v-else class="session-list">
                  <li v-for="session in getRelationSessions(relation.id)" :key="session.id" class="session-item">
                    <div class="session-item-main">
                      <div class="session-item-top">
                        <strong>{{ session.device_name || 'دستگاه ناشناس' }}</strong>
                        <div class="session-badges">
                          <span v-if="session.is_primary" class="session-badge primary">primary</span>
                          <span class="session-badge neutral">{{ formatSessionPlatform(session.platform) }}</span>
                          <span class="session-badge neutral">{{ formatHomeServer(session.home_server) }}</span>
                        </div>
                      </div>
                      <div class="session-item-meta">
                        <span>آخرین فعالیت: {{ formatDateTime(session.last_active_at) }}</span>
                        <span>شروع نشست: {{ formatDateTime(session.created_at) }}</span>
                        <span v-if="session.device_ip">IP: {{ session.device_ip }}</span>
                      </div>
                    </div>
                    <button
                      type="button"
                      class="danger-btn terminate-session"
                      :disabled="terminatingSessionId === session.id"
                      @click="terminateCustomerSession(relation, session)"
                    >
                      {{ terminatingSessionId === session.id ? 'در حال پایان...' : 'پایان نشست' }}
                    </button>
                  </li>
                </ul>
              </div>
            </article>
          </div>
        </section>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.customer-manager-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: 20px 14px;
  background: rgba(15, 23, 42, 0.54);
  backdrop-filter: blur(10px);
}

.customer-manager-shell {
  width: min(1040px, 100%);
  max-height: 100%;
  overflow: auto;
  border-radius: 28px;
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98) 0%, rgba(255, 255, 255, 0.98) 26%, rgba(248, 250, 252, 0.98) 100%);
  box-shadow: 0 26px 80px rgba(15, 23, 42, 0.24);
  border: 1px solid rgba(245, 158, 11, 0.14);
  padding: 22px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.customer-manager-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.customer-manager-kicker {
  margin: 0 0 6px;
  font-size: 0.78rem;
  font-weight: 700;
  color: #d97706;
}

.customer-manager-header h3 {
  margin: 0;
  font-size: 1.35rem;
  color: #111827;
}

.customer-manager-close,
.ghost-btn,
.primary-btn,
.secondary-btn,
.danger-btn {
  border: 0;
  border-radius: 999px;
  min-height: 40px;
  padding: 0 16px;
  font-weight: 700;
  cursor: pointer;
}

.customer-manager-close,
.ghost-btn,
.secondary-btn {
  background: rgba(148, 163, 184, 0.14);
  color: #334155;
}

.primary-btn {
  background: linear-gradient(135deg, #f59e0b, #f97316);
  color: #fff;
  box-shadow: 0 10px 24px rgba(249, 115, 22, 0.24);
}

.danger-btn {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
}

.customer-manager-note,
.customer-banner {
  border-radius: 20px;
  padding: 14px 16px;
  font-size: 0.92rem;
}

.customer-manager-note {
  background: rgba(251, 191, 36, 0.14);
  color: #92400e;
}

.customer-banner.success {
  background: rgba(16, 185, 129, 0.14);
  color: #047857;
}

.customer-banner.error {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
}

.customer-panel {
  border-radius: 24px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(255, 255, 255, 0.82);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.panel-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.panel-title-row h4 {
  margin: 0 0 4px;
  color: #0f172a;
}

.panel-title-row p {
  margin: 0;
  color: #64748b;
  font-size: 0.9rem;
}

.customer-form-grid,
.customer-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.compact-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.field-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.field-block span,
.meta-label {
  font-size: 0.84rem;
  font-weight: 700;
  color: #475569;
}

.customer-input {
  width: 100%;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(255, 255, 255, 0.96);
  min-height: 46px;
  padding: 0 14px;
  font: inherit;
  color: #0f172a;
}

.panel-actions,
.customer-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.panel-actions.compact {
  justify-content: flex-start;
}

.customer-loading,
.customer-empty {
  text-align: center;
  padding: 20px 12px;
  color: #64748b;
}

.customer-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.customer-card {
  border-radius: 22px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.98));
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.customer-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.customer-card-head h5 {
  margin: 0 0 4px;
  font-size: 1rem;
  color: #0f172a;
}

.customer-account-name {
  margin: 0;
  color: #64748b;
  direction: ltr;
  text-align: right;
}

.customer-status-badge {
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 0.8rem;
  font-weight: 800;
}

.customer-status-badge.status-pending {
  background: rgba(251, 191, 36, 0.18);
  color: #b45309;
}

.customer-status-badge.status-active {
  background: rgba(16, 185, 129, 0.16);
  color: #047857;
}

.meta-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.meta-value {
  color: #0f172a;
  font-weight: 600;
}

.customer-state-copy {
  margin: 0;
  font-size: 0.82rem;
  font-weight: 700;
  line-height: 1.8;
}

.customer-state-copy.status-pending {
  color: #b45309;
}

.customer-state-copy.status-active {
  color: #047857;
}

.customer-state-copy.status-expired,
.customer-state-copy.status-revoked,
.customer-state-copy.status-deleted {
  color: #b91c1c;
}

.session-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 12px;
  border-top: 1px dashed rgba(148, 163, 184, 0.4);
}

.session-panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.session-panel-header h6 {
  margin: 0 0 4px;
  font-size: 0.95rem;
  color: #0f172a;
}

.session-panel-header p {
  margin: 0;
  color: #64748b;
  font-size: 0.84rem;
  line-height: 1.7;
}

.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.session-item-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.session-item-top {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.session-item-top strong {
  color: #0f172a;
}

.session-badges {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.session-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 800;
}

.session-badge.primary {
  background: rgba(16, 185, 129, 0.16);
  color: #047857;
}

.session-badge.neutral {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.session-item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  font-size: 0.82rem;
  color: #64748b;
}

.terminate-session {
  flex-shrink: 0;
}

.edit-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;
  border-top: 1px dashed rgba(148, 163, 184, 0.4);
}

@media (max-width: 720px) {
  .customer-manager-backdrop {
    padding: 0;
  }

  .customer-manager-shell {
    width: 100%;
    border-radius: 24px 24px 0 0;
    min-height: 100%;
    padding: 18px 14px 22px;
  }

  .customer-manager-header,
  .panel-title-row,
  .customer-card-head,
  .session-panel-header,
  .session-item {
    flex-direction: column;
  }

  .customer-form-grid,
  .customer-meta-grid,
  .compact-grid {
    grid-template-columns: 1fr;
  }

  .panel-actions,
  .customer-actions {
    justify-content: stretch;
  }

  .panel-actions > button,
  .customer-actions > button,
  .customer-manager-close,
  .ghost-btn {
    width: 100%;
  }
}
</style>