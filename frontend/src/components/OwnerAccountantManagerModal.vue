<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { apiFetch } from '../utils/auth'

const emit = defineEmits<{
  (e: 'close'): void
}>()

type RelationStatus = 'pending' | 'active' | 'expired' | 'revoked' | 'deleted' | string

interface AccountantRelation {
  id: number
  owner_user_id: number
  accountant_user_id: number | null
  accountant_account_name: string | null
  global_account_name: string
  relation_display_name: string
  duty_description: string | null
  mobile_number: string
  status: RelationStatus
  invitation_token: string
  registration_link: string | null
  expires_at: string
  activated_at: string | null
  deleted_at: string | null
  created_at: string
}

function makeEmptyCreateForm() {
  return {
    account_name: '',
    relation_display_name: '',
    mobile_number: '',
    duty_description: '',
  }
}

function makeEmptyEditForm() {
  return {
    duty_description: '',
  }
}

const relations = ref<AccountantRelation[]>([])
const isLoading = ref(true)
const isRefreshing = ref(false)
const isSubmitting = ref(false)
const isSavingEdit = ref(false)
const editingRelationId = ref<number | null>(null)
const error = ref('')
const notice = ref('')
const copiedRelationId = ref<number | null>(null)
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

function normalizeDutyDescription(value: string) {
  const cleaned = value.trim()
  return cleaned || null
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

function getRelationStateText(relation: AccountantRelation) {
  if (relation.status === 'pending') {
    const remainingMs = getRemainingMs(relation.expires_at)
    if (remainingMs == null) return 'دعوت ثبت شده و در انتظار ثبت نام حسابدار است.'
    if (remainingMs <= 0) return 'مهلت این دعوت تمام شده و در انتظار همگام سازی وضعیت است.'
    return `مهلت ثبت نام: ${formatCountdown(relation.expires_at)}`
  }

  if (relation.status === 'active') {
    if (relation.accountant_account_name) {
      return `این حسابدار با @${relation.accountant_account_name} فعال است.`
    }
    return 'این رابطه فعال شده است.'
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
    const response = await apiFetch('/api/accountants/owner-relations')
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت لیست حسابداران ناموفق بود.'))
    }
    relations.value = Array.isArray(payload) ? payload : []
  } catch (err: any) {
    error.value = err?.message || 'دریافت لیست حسابداران ناموفق بود.'
  } finally {
    isLoading.value = false
    isRefreshing.value = false
  }
}

function startEditing(relation: AccountantRelation) {
  editingRelationId.value = relation.id
  editForm.duty_description = relation.duty_description || ''
  notice.value = ''
  error.value = ''
}

async function createRelation() {
  if (isSubmitting.value) return
  isSubmitting.value = true
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch('/api/accountants/owner-relations', {
      method: 'POST',
      body: JSON.stringify({
        account_name: createForm.account_name,
        relation_display_name: createForm.relation_display_name,
        mobile_number: createForm.mobile_number,
        duty_description: normalizeDutyDescription(createForm.duty_description),
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'ایجاد حسابدار ناموفق بود.'))
    }
    relations.value = [payload as AccountantRelation, ...relations.value.filter((item) => item.id !== (payload as AccountantRelation).id)]
    resetCreateForm()
    notice.value = 'دعوت حسابدار ثبت شد.'
  } catch (err: any) {
    error.value = err?.message || 'ایجاد حسابدار ناموفق بود.'
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
    const response = await apiFetch(`/api/accountants/owner-relations/${relationId}`, {
      method: 'PATCH',
      body: JSON.stringify({
        duty_description: normalizeDutyDescription(editForm.duty_description),
      }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'ویرایش حسابدار ناموفق بود.'))
    }
    const updated = payload as AccountantRelation
    relations.value = relations.value.map((item) => (item.id === updated.id ? updated : item))
    clearEditState()
    notice.value = 'اطلاعات حسابدار به‌روزرسانی شد.'
  } catch (err: any) {
    error.value = err?.message || 'ویرایش حسابدار ناموفق بود.'
  } finally {
    isSavingEdit.value = false
  }
}

async function cancelPendingRelation(relation: AccountantRelation) {
  if (relation.status !== 'pending') return
  if (!window.confirm(`دعوت ${relation.relation_display_name} لغو شود؟`)) return

  error.value = ''
  notice.value = ''
  try {
    const response = await apiFetch(`/api/accountants/owner-relations/${relation.id}`, {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'لغو دعوت حسابدار ناموفق بود.'))
    }
    relations.value = relations.value.filter((item) => item.id !== relation.id)
    if (editingRelationId.value === relation.id) {
      clearEditState()
    }
    notice.value = 'دعوت حسابدار لغو شد.'
  } catch (err: any) {
    error.value = err?.message || 'لغو دعوت حسابدار ناموفق بود.'
  }
}

async function copyRegistrationLink(relation: AccountantRelation) {
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
    <div class="accountant-manager-backdrop" @click.self="emit('close')">
      <div class="accountant-manager-shell">
        <div class="accountant-manager-header">
          <div>
            <p class="accountant-manager-kicker">مدیریت ارتباطات</p>
            <h3>حسابداران مالک</h3>
          </div>
          <button type="button" class="accountant-manager-close" @click="emit('close')">بستن</button>
        </div>

        <div class="accountant-manager-note">
          این لیست فقط حسابداران فعال و در انتظار ثبت‌نام را نشان می‌دهد. در ویرایش فقط شرح وظیفه قابل تغییر است.
        </div>

        <div v-if="error" class="accountant-banner error">{{ error }}</div>
        <div v-else-if="notice" class="accountant-banner success">{{ notice }}</div>

        <section class="accountant-panel create-panel">
          <div class="panel-title-row">
            <div>
              <h4>افزودن حسابدار جدید</h4>
              <p>پس از ثبت، لینک ثبت‌نام مخصوص همان حسابدار ساخته می‌شود.</p>
            </div>
            <button type="button" class="ghost-btn" :disabled="isRefreshing" @click="loadRelations({ silent: true })">
              {{ isRefreshing ? 'در حال بروزرسانی...' : 'بروزرسانی لیست' }}
            </button>
          </div>

          <div class="accountant-form-grid">
            <label class="field-block">
              <span>نام کاربری جهانی</span>
              <input v-model="createForm.account_name" class="accountant-input create-account-name" type="text" placeholder="accountant_01" />
            </label>
            <label class="field-block">
              <span>نام نمایشی رابطه</span>
              <input v-model="createForm.relation_display_name" class="accountant-input create-display-name" type="text" placeholder="حسابدار فروش" />
            </label>
            <label class="field-block">
              <span>شماره موبایل</span>
              <input v-model="createForm.mobile_number" class="accountant-input create-mobile-number" type="tel" inputmode="numeric" placeholder="09120000000" />
            </label>
            <label class="field-block full-width">
              <span>شرح وظیفه</span>
              <textarea v-model="createForm.duty_description" class="accountant-input accountant-textarea create-duty-description" rows="3" placeholder="مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه"></textarea>
            </label>
          </div>

          <div class="panel-actions">
            <button type="button" class="secondary-btn" :disabled="isSubmitting" @click="resetCreateForm">پاک کردن</button>
            <button type="button" class="primary-btn submit-create" :disabled="isSubmitting" @click="createRelation">
              {{ isSubmitting ? 'در حال ثبت...' : 'ثبت حسابدار' }}
            </button>
          </div>
        </section>

        <section class="accountant-panel list-panel">
          <div class="panel-title-row">
            <div>
              <h4>لیست حسابداران</h4>
              <p>{{ orderedRelations.length }} مورد فعال یا در انتظار ثبت‌نام</p>
            </div>
          </div>

          <div v-if="isLoading" class="accountant-loading">در حال دریافت حسابداران...</div>
          <div v-else-if="orderedRelations.length === 0" class="accountant-empty">
            هنوز هیچ حسابداری برای این مالک ثبت نشده است.
          </div>
          <div v-else class="accountant-list">
            <article v-for="relation in orderedRelations" :key="relation.id" class="accountant-card" :class="`status-${relation.status}`">
              <div class="accountant-card-head">
                <div>
                  <h5>{{ relation.relation_display_name }}</h5>
                  <p class="accountant-global-name">@{{ relation.global_account_name }}</p>
                </div>
                <span class="accountant-status-badge" :class="`status-${relation.status}`">{{ statusLabel(relation.status) }}</span>
              </div>

              <div class="accountant-meta-grid">
                <div class="meta-item">
                  <span class="meta-label">موبایل</span>
                  <span class="meta-value">{{ relation.mobile_number }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">کاربر لینک‌شده</span>
                  <span class="meta-value">{{ relation.accountant_account_name || 'هنوز ثبت‌نام نشده' }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">ایجاد</span>
                  <span class="meta-value">{{ formatDateTime(relation.created_at) }}</span>
                </div>
                <div class="meta-item">
                  <span class="meta-label">فعال‌سازی</span>
                  <span class="meta-value">{{ formatDateTime(relation.activated_at) }}</span>
                </div>
                <div v-if="relation.status === 'pending'" class="meta-item">
                  <span class="meta-label">انقضا</span>
                  <span class="meta-value">{{ formatDateTime(relation.expires_at) }}</span>
                </div>
              </div>

              <p v-if="getRelationStateText(relation)" class="accountant-state-copy" :class="`status-${relation.status}`">{{ getRelationStateText(relation) }}</p>
              <p v-if="relation.duty_description" class="accountant-duty">{{ relation.duty_description }}</p>

              <div v-if="editingRelationId === relation.id" class="edit-panel">
                <label class="field-block">
                  <span>شرح وظیفه قابل ویرایش</span>
                  <textarea v-model="editForm.duty_description" class="accountant-input accountant-textarea edit-duty-description" rows="3"></textarea>
                </label>
                <div class="panel-actions compact">
                  <button type="button" class="secondary-btn" :disabled="isSavingEdit" @click="clearEditState">انصراف</button>
                  <button type="button" class="primary-btn save-edit" :disabled="isSavingEdit" @click="saveEdit(relation.id)">
                    {{ isSavingEdit ? 'در حال ذخیره...' : 'ذخیره تغییرات' }}
                  </button>
                </div>
              </div>
              <div v-else class="accountant-actions">
                <button type="button" class="secondary-btn start-edit" @click="startEditing(relation)">ویرایش</button>
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
                  @click="cancelPendingRelation(relation)"
                >
                  لغو دعوت
                </button>
              </div>
            </article>
          </div>
        </section>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.accountant-manager-backdrop {
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

.accountant-manager-shell {
  width: min(960px, 100%);
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

.accountant-manager-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.accountant-manager-kicker {
  margin: 0 0 6px;
  font-size: 0.78rem;
  font-weight: 700;
  color: #d97706;
}

.accountant-manager-header h3 {
  margin: 0;
  font-size: 1.35rem;
  color: #111827;
}

.accountant-manager-close,
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

.accountant-manager-close,
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

.accountant-manager-note,
.accountant-banner {
  border-radius: 20px;
  padding: 14px 16px;
  font-size: 0.92rem;
}

.accountant-manager-note {
  background: rgba(251, 191, 36, 0.14);
  color: #92400e;
}

.accountant-banner.success {
  background: rgba(16, 185, 129, 0.14);
  color: #047857;
}

.accountant-banner.error {
  background: rgba(239, 68, 68, 0.14);
  color: #b91c1c;
}

.accountant-panel {
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

.accountant-form-grid,
.accountant-meta-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
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

.field-block.full-width {
  grid-column: 1 / -1;
}

.accountant-input {
  width: 100%;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(255, 255, 255, 0.96);
  min-height: 46px;
  padding: 0 14px;
  font: inherit;
  color: #0f172a;
}

.accountant-textarea {
  min-height: 104px;
  padding: 12px 14px;
  resize: vertical;
}

.panel-actions,
.accountant-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 10px;
  flex-wrap: wrap;
}

.panel-actions.compact {
  justify-content: flex-start;
}

.accountant-loading,
.accountant-empty {
  text-align: center;
  padding: 20px 12px;
  color: #64748b;
}

.accountant-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.accountant-card {
  border-radius: 22px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.98), rgba(248, 250, 252, 0.98));
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.accountant-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.accountant-card-head h5 {
  margin: 0 0 4px;
  font-size: 1rem;
  color: #0f172a;
}

.accountant-global-name {
  margin: 0;
  color: #64748b;
  direction: ltr;
  text-align: right;
}

.accountant-status-badge {
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 0.8rem;
  font-weight: 800;
}

.accountant-status-badge.status-pending {
  background: rgba(251, 191, 36, 0.18);
  color: #b45309;
}

.accountant-status-badge.status-active {
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

.accountant-state-copy {
  margin: 0;
  font-size: 0.82rem;
  font-weight: 700;
  line-height: 1.8;
}

.accountant-state-copy.status-pending {
  color: #b45309;
}

.accountant-state-copy.status-active {
  color: #047857;
}

.accountant-state-copy.status-expired,
.accountant-state-copy.status-revoked,
.accountant-state-copy.status-deleted {
  color: #b91c1c;
}

.accountant-duty {
  margin: 0;
  padding: 12px 14px;
  border-radius: 16px;
  background: rgba(241, 245, 249, 0.9);
  color: #334155;
  line-height: 1.8;
}

.edit-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 8px;
  border-top: 1px dashed rgba(148, 163, 184, 0.4);
}

@media (max-width: 720px) {
  .accountant-manager-backdrop {
    padding: 0;
  }

  .accountant-manager-shell {
    width: 100%;
    border-radius: 24px 24px 0 0;
    min-height: 100%;
    padding: 18px 14px 22px;
  }

  .accountant-manager-header,
  .panel-title-row,
  .accountant-card-head {
    flex-direction: column;
  }

  .accountant-form-grid,
  .accountant-meta-grid {
    grid-template-columns: 1fr;
  }

  .panel-actions,
  .accountant-actions {
    justify-content: stretch;
  }

  .panel-actions > button,
  .accountant-actions > button,
  .accountant-manager-close,
  .ghost-btn {
    width: 100%;
  }
}
</style>