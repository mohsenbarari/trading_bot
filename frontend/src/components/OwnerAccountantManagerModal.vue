<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ChevronLeft, SlidersHorizontal, UserPlus, Users } from 'lucide-vue-next'
import { apiFetch } from '../utils/auth'
import { formatIranDateTime, parseIranDisplayDate } from '../utils/iranTime'
import HelpPopover from './HelpPopover.vue'

const emit = defineEmits<{
  (e: 'close'): void
}>()

type RelationStatus = 'pending' | 'active' | 'expired' | 'revoked' | 'deleted' | string
type ActivePanel = 'create' | 'relations' | null

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

interface AccountantSessionSummary {
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

interface AccountantSessionTerminateResponse {
  detail: string
  terminated_session_id: string
  promoted_primary_session_id: string | null
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
const openSessionsRelationId = ref<number | null>(null)
const sessionsByRelationId = ref<Record<number, AccountantSessionSummary[]>>({})
const loadingSessionsRelationId = ref<number | null>(null)
const terminatingSessionId = ref<string | null>(null)
const currentTimeMs = ref(Date.now())
const activePanel = ref<ActivePanel>(null)

const createForm = reactive(makeEmptyCreateForm())
const editForm = reactive(makeEmptyEditForm())
const openSections = reactive({
  create: true,
  createIdentity: true,
  createDuty: true,
  relations: true,
})
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

function toggleSection(section: keyof typeof openSections) {
  openSections[section] = !openSections[section]
}

function openPanel(panel: Exclude<ActivePanel, null>) {
  activePanel.value = panel
}

function backToCategories() {
  activePanel.value = null
}

function clearEditState() {
  editingRelationId.value = null
  Object.assign(editForm, makeEmptyEditForm())
}

function formatDateTime(value: string | null) {
  if (!value) return '---'
  return formatIranDateTime(value) || value
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

function getRemainingMs(value: string | null) {
  if (!value) return null
  const timestamp = parseIranDisplayDate(value)?.getTime() ?? Number.NaN
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

const summaryStats = computed(() => {
  const pending = relations.value.filter((relation) => relation.status === 'pending').length
  const active = relations.value.filter((relation) => relation.status === 'active').length
  const archived = relations.value.length - pending - active
  return {
    total: relations.value.length,
    pending,
    active,
    archived: Math.max(0, archived),
  }
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
    if (openSessionsRelationId.value !== null) {
      const openRelation = relations.value.find((relation) => relation.id === openSessionsRelationId.value)
      if (!openRelation || openRelation.status !== 'active' || !openRelation.accountant_user_id) {
        openSessionsRelationId.value = null
      }
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت لیست حسابداران ناموفق بود.'
  } finally {
    isLoading.value = false
    isRefreshing.value = false
  }
}

async function loadSessionsForRelation(relationId: number) {
  loadingSessionsRelationId.value = relationId
  error.value = ''

  try {
    const response = await apiFetch(`/api/accountants/owner-relations/${relationId}/sessions`, {
      method: 'GET',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'دریافت نشست‌های حسابدار ناموفق بود.'))
    }
    sessionsByRelationId.value = {
      ...sessionsByRelationId.value,
      [relationId]: Array.isArray(payload) ? (payload as AccountantSessionSummary[]) : [],
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت نشست‌های حسابدار ناموفق بود.'
  } finally {
    if (loadingSessionsRelationId.value === relationId) {
      loadingSessionsRelationId.value = null
    }
  }
}

async function toggleSessionPanel(relation: AccountantRelation) {
  if (relation.status !== 'active' || !relation.accountant_user_id) return
  if (openSessionsRelationId.value === relation.id) {
    openSessionsRelationId.value = null
    return
  }
  openSessionsRelationId.value = relation.id
  await loadSessionsForRelation(relation.id)
}

async function terminateAccountantSession(relation: AccountantRelation, session: AccountantSessionSummary) {
  if (terminatingSessionId.value === session.id) return
  if (!window.confirm(`نشست «${session.device_name || 'دستگاه حسابدار'}» پایان یابد؟`)) return

  terminatingSessionId.value = session.id
  error.value = ''
  notice.value = ''

  try {
    const response = await apiFetch(`/api/accountants/owner-relations/${relation.id}/sessions/${session.id}`, {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, 'پایان دادن نشست حسابدار ناموفق بود.'))
    }
    const result = payload as AccountantSessionTerminateResponse | null
    notice.value = result?.detail || 'نشست حسابدار با موفقیت پایان یافت.'
    await loadSessionsForRelation(relation.id)
  } catch (err: any) {
    error.value = err?.message || 'پایان دادن نشست حسابدار ناموفق بود.'
  } finally {
    if (terminatingSessionId.value === session.id) {
      terminatingSessionId.value = null
    }
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
    activePanel.value = 'relations'
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

async function unlinkRelation(relation: AccountantRelation) {
  const isPending = relation.status === 'pending'
  const isActive = relation.status === 'active'
  if (!isPending && !isActive) return

  const promptMessage = isPending
    ? `دعوت ${relation.relation_display_name} لغو شود؟`
    : `ارتباط حسابدار ${relation.relation_display_name} قطع شود؟ این عملیات دسترسی حسابدار را کامل غیرفعال می‌کند.`
  if (!window.confirm(promptMessage)) return

  error.value = ''
  notice.value = ''
  try {
    const response = await apiFetch(`/api/accountants/owner-relations/${relation.id}`, {
      method: 'DELETE',
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(parseApiError(payload, isPending ? 'لغو دعوت حسابدار ناموفق بود.' : 'قطع ارتباط حسابدار ناموفق بود.'))
    }
    relations.value = relations.value.filter((item) => item.id !== relation.id)
    if (editingRelationId.value === relation.id) {
      clearEditState()
    }
    notice.value = isPending ? 'دعوت حسابدار لغو شد.' : 'ارتباط حسابدار قطع شد و دسترسی او غیرفعال گردید.'
  } catch (err: any) {
    error.value = err?.message || (isPending ? 'لغو دعوت حسابدار ناموفق بود.' : 'قطع ارتباط حسابدار ناموفق بود.')
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
          <HelpPopover
            button-test="accountant-manager-help"
            note-test="accountant-manager-help-note"
            label="راهنمای مدیریت حسابداران"
            text="این لیست فقط حسابداران فعال و در انتظار ثبت‌نام را نشان می‌دهد. در ویرایش فقط شرح وظیفه قابل تغییر است."
          />
          <button type="button" class="accountant-manager-close" @click="emit('close')">بستن</button>
        </div>

        <div v-if="error" class="accountant-banner error">{{ error }}</div>
        <div v-else-if="notice" class="accountant-banner success">{{ notice }}</div>

        <section class="accountant-summary-strip" aria-label="خلاصه وضعیت حسابداران">
          <article class="summary-card">
            <span class="summary-label">کل رابطه‌ها</span>
            <strong class="summary-value">{{ summaryStats.total }}</strong>
          </article>
          <article class="summary-card summary-card--active">
            <span class="summary-label">فعال</span>
            <strong class="summary-value">{{ summaryStats.active }}</strong>
          </article>
          <article class="summary-card summary-card--pending">
            <span class="summary-label">در انتظار</span>
            <strong class="summary-value">{{ summaryStats.pending }}</strong>
          </article>
          <article class="summary-card summary-card--archived">
            <span class="summary-label">آرشیوی</span>
            <strong class="summary-value">{{ summaryStats.archived }}</strong>
          </article>
        </section>

        <section v-if="activePanel === null" class="manager-category-menu card-with-help" aria-label="دسته‌بندی مدیریت حسابداران">
          <HelpPopover
            floating
            button-test="accountant-category-menu-help"
            note-test="accountant-category-menu-help-note"
            label="راهنمای دسته‌بندی حسابداران"
            text="ابتدا دسته مورد نظر را انتخاب کنید. سپس زیرمنوهای همان دسته، مثل مشخصات پایه یا شرح وظیفه، نمایش داده می‌شود."
          />
          <div class="manager-category-heading">دسته‌بندی مدیریت حسابداران</div>
          <button type="button" class="menu-button settings-btn open-create-category" @click="openPanel('create')">
            <span class="menu-button-icon"><UserPlus :size="18" /></span>
            <span class="menu-button-copy">
              <span class="menu-button-label">افزودن حسابدار</span>
              <span class="menu-button-note">ثبت دعوت، مشخصات پایه و شرح وظیفه حسابدار</span>
            </span>
          </button>
          <button type="button" class="menu-button settings-btn open-relations-category" @click="openPanel('relations')">
            <span class="menu-button-icon"><Users :size="18" /></span>
            <span class="menu-button-copy">
              <span class="menu-button-label">مدیریت حسابداران</span>
              <span class="menu-button-note">{{ summaryStats.total.toLocaleString('fa-IR') }} رابطه ثبت‌شده، شامل فعال، در انتظار و آرشیوی</span>
            </span>
          </button>
        </section>

        <section v-if="activePanel === 'create'" class="accountant-panel accountant-panel--accordion">
          <div class="ds-accordion" :class="{ open: openSections.create }">
            <div class="ds-accordion-header" @click="toggleSection('create')">
              <div class="ds-accordion-header-info">
                <UserPlus :size="18" class="accountant-section-icon" />
                <div>
                  <h4>افزودن حسابدار جدید</h4>
                  <p>دعوت حسابدار و شرح نقش او را مرحله‌بندی شده ثبت کنید.</p>
                </div>
              </div>
              <div class="accordion-header-actions">
                <HelpPopover
                  button-test="accountant-create-help"
                  note-test="accountant-create-help-note"
                  label="راهنمای افزودن حسابدار"
                  text="پس از ثبت، لینک ثبت‌نام مخصوص همان حسابدار ساخته می‌شود."
                />
                <button type="button" class="ghost-btn ghost-btn--inline" @click.stop="backToCategories">بازگشت به دسته‌ها</button>
                <ChevronLeft :size="20" class="ds-accordion-icon" />
              </div>
            </div>

            <div v-show="openSections.create" class="ds-accordion-body accountant-accordion-body">
              <div class="accountant-form-sections accountant-form-sections--stacked">
                <section class="form-subpanel form-subpanel--accordion">
                  <div class="ds-accordion" :class="{ open: openSections.createIdentity }">
                    <div class="ds-accordion-header" @click.stop="toggleSection('createIdentity')">
                      <div class="ds-accordion-header-info">
                        <UserPlus :size="16" class="accountant-subsection-icon" />
                        <div>
                          <h5>مشخصات پایه</h5>
                          <p>نام کاربری، عنوان نمایشی و شماره موبایل حسابدار</p>
                        </div>
                      </div>
                      <ChevronLeft :size="18" class="ds-accordion-icon" />
                    </div>
                    <div v-show="openSections.createIdentity" class="ds-accordion-body">
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
                      </div>
                    </div>
                  </div>
                </section>

                <section class="form-subpanel form-subpanel--accordion">
                  <div class="ds-accordion" :class="{ open: openSections.createDuty }">
                    <div class="ds-accordion-header" @click.stop="toggleSection('createDuty')">
                      <div class="ds-accordion-header-info">
                        <SlidersHorizontal :size="16" class="accountant-subsection-icon" />
                        <div>
                          <h5>شرح وظیفه</h5>
                          <p>اختیاری، برای تفکیک نقش حسابداران در گروه کاری</p>
                        </div>
                      </div>
                      <ChevronLeft :size="18" class="ds-accordion-icon" />
                    </div>
                    <div v-show="openSections.createDuty" class="ds-accordion-body">
                      <label class="field-block">
                        <span>شرح وظیفه</span>
                        <textarea v-model="createForm.duty_description" class="accountant-input accountant-textarea create-duty-description" rows="3" placeholder="مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه"></textarea>
                      </label>
                    </div>
                  </div>
                </section>
              </div>

              <div class="panel-actions">
                <button type="button" class="secondary-btn" :disabled="isSubmitting" @click="resetCreateForm">پاک کردن</button>
                <button type="button" class="primary-btn submit-create" :disabled="isSubmitting" @click="createRelation">
                  {{ isSubmitting ? 'در حال ثبت...' : 'ثبت حسابدار' }}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section v-if="activePanel === 'relations'" class="accountant-panel accountant-panel--accordion">
          <div class="ds-accordion" :class="{ open: openSections.relations }">
            <div class="ds-accordion-header" @click="toggleSection('relations')">
              <div class="ds-accordion-header-info">
                <Users :size="18" class="accountant-section-icon" />
                <div>
                  <h4>حسابداران فعال و در انتظار</h4>
                  <p>رابطه‌ها، وضعیت ثبت‌نام، شرح وظیفه و نشست‌های حسابداران</p>
                </div>
              </div>
              <div class="accordion-header-actions">
                <HelpPopover
                  button-test="accountant-list-help"
                  note-test="accountant-list-help-note"
                  label="راهنمای لیست حسابداران"
                  :text="`${orderedRelations.length.toLocaleString('fa-IR')} مورد فعال یا در انتظار ثبت‌نام در این لیست وجود دارد.`"
                />
                <button type="button" class="ghost-btn ghost-btn--inline refresh-relations" :disabled="isRefreshing" @click.stop="loadRelations({ silent: true })">
                  {{ isRefreshing ? 'در حال بروزرسانی...' : 'بروزرسانی لیست' }}
                </button>
                <button type="button" class="ghost-btn ghost-btn--inline" @click.stop="backToCategories">بازگشت به دسته‌ها</button>
                <ChevronLeft :size="20" class="ds-accordion-icon" />
              </div>
            </div>

            <div v-show="openSections.relations" class="ds-accordion-body accountant-accordion-body">
              <div v-if="isLoading" class="accountant-loading">در حال دریافت حسابداران...</div>
              <div v-else-if="orderedRelations.length === 0" class="accountant-empty">
            هنوز هیچ حسابداری برای این مالک ثبت نشده است.
              </div>
              <div v-else class="accountant-list">
            <article v-for="relation in orderedRelations" :key="relation.id" class="accountant-card" :class="`status-${relation.status}`">
              <div class="accountant-card-head">
                <div class="accountant-identity-block">
                  <h5>{{ relation.relation_display_name }}</h5>
                  <p class="accountant-global-name">@{{ relation.global_account_name }}</p>
                  <p class="accountant-mobile-number">{{ relation.mobile_number }}</p>
                </div>
                <span class="accountant-status-badge" :class="`status-${relation.status}`">{{ statusLabel(relation.status) }}</span>
              </div>

              <div class="accountant-meta-grid">
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
                  v-if="relation.status === 'active' && relation.accountant_user_id"
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
                    <h6>نشست‌های فعال حسابدار</h6>
                  </div>
                  <HelpPopover
                    button-test="accountant-sessions-help"
                    note-test="accountant-sessions-help-note"
                    label="راهنمای نشست‌های حسابدار"
                    text="نشست‌های فعال این حسابدار را می‌توانید ببینید و هر نشست را جداگانه خاتمه دهید."
                  />
                  <button
                    type="button"
                    class="ghost-btn refresh-sessions"
                    :disabled="loadingSessionsRelationId === relation.id"
                    @click="loadSessionsForRelation(relation.id)"
                  >
                    {{ loadingSessionsRelationId === relation.id ? 'در حال نوسازی...' : 'نوسازی' }}
                  </button>
                </div>

                <div v-if="loadingSessionsRelationId === relation.id" class="accountant-loading session-loading">
                  در حال دریافت نشست‌های حسابدار...
                </div>
                <div v-else-if="!getRelationSessions(relation.id).length" class="accountant-empty session-empty">
                  در حال حاضر نشست فعالی برای این حسابدار ثبت نشده است.
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
                      @click="terminateAccountantSession(relation, session)"
                    >
                      {{ terminatingSessionId === session.id ? 'در حال پایان...' : 'پایان نشست' }}
                    </button>
                  </li>
                </ul>
              </div>
            </article>
          </div>
            </div>
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

.card-with-help {
  position: relative;
}

.manager-category-menu {
  padding: 1rem;
  padding-left: 3.8rem;
  border: 1px solid rgba(15, 23, 42, 0.06);
  border-radius: 1.25rem;
  background: linear-gradient(135deg, rgba(255, 251, 235, 0.72), rgba(255, 255, 255, 0.96));
  box-shadow: 0 14px 32px rgba(15, 23, 42, 0.07);
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.manager-category-heading {
  margin-bottom: 0.7rem;
  padding-right: 0.2rem;
  font-size: 0.8rem;
  font-weight: 800;
  color: #92400e;
}

.menu-button {
  width: 100%;
  min-height: 3.4rem;
  padding: 0.78rem 0.9rem;
  font-size: 0.85rem;
  font-weight: 850;
  background: rgba(255, 255, 255, 0.94);
  color: #1f2937;
  border: 1px solid rgba(15, 23, 42, 0.07);
  border-radius: 1rem;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.72rem;
  transition: all 0.2s;
  text-align: right;
  -webkit-tap-highlight-color: transparent;
}

.menu-button:hover {
  border-color: rgba(245, 158, 11, 0.3);
  background: #fffbeb;
}

.menu-button:active {
  transform: scale(0.98);
}

.menu-button-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  border-radius: 0.8rem;
  background: rgba(245, 158, 11, 0.12);
  color: #92400e;
  flex: 0 0 auto;
}

.menu-button-copy {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.18rem;
}

.menu-button-label {
  flex: 1;
  min-width: 0;
}

.menu-button-note {
  font-size: 0.72rem;
  line-height: 1.55;
  font-weight: 600;
  color: #6b7280;
}

.settings-btn {
  background: linear-gradient(135deg, #fffbeb, #fef3c7) !important;
  color: #92400e !important;
  border-color: rgba(245, 158, 11, 0.2) !important;
}

.settings-btn .menu-button-icon {
  background: rgba(245, 158, 11, 0.12);
  color: #92400e;
}

.accountant-summary-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.summary-card {
  border-radius: 20px;
  padding: 14px 16px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(248, 250, 252, 0.98));
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.summary-card--active {
  background: linear-gradient(180deg, rgba(236, 253, 245, 0.98), rgba(240, 253, 244, 0.98));
}

.summary-card--pending {
  background: linear-gradient(180deg, rgba(255, 251, 235, 0.98), rgba(255, 247, 237, 0.98));
}

.summary-card--archived {
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.98), rgba(241, 245, 249, 0.98));
}

.summary-label {
  font-size: 0.8rem;
  font-weight: 700;
  color: #64748b;
}

.summary-value {
  font-size: 1.35rem;
  line-height: 1;
  color: #0f172a;
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

.accountant-panel--accordion {
  border: 0;
  background: transparent;
  padding: 0;
  display: block;
}

.accountant-panel--accordion .ds-accordion {
  border-radius: 24px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(255, 255, 255, 0.82);
  overflow: hidden;
}

.accountant-panel--accordion .ds-accordion-header {
  gap: 14px;
}

.accountant-panel--accordion .ds-accordion-header-info {
  gap: 12px;
}

.accountant-panel--accordion .ds-accordion-header-info h4,
.form-subpanel--accordion .ds-accordion-header-info h5 {
  margin: 0;
  color: #0f172a;
}

.accountant-panel--accordion .ds-accordion-header-info p,
.form-subpanel--accordion .ds-accordion-header-info p {
  margin: 3px 0 0;
  font-size: 0.84rem;
  color: #64748b;
}

.accountant-accordion-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.accountant-section-icon,
.accountant-subsection-icon {
  color: #d97706;
}

.accordion-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex: 0 0 auto;
  direction: ltr;
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

.accountant-form-sections {
  display: grid;
  grid-template-columns: 1fr;
  gap: 16px;
}

.form-subpanel {
  border-radius: 20px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  background: rgba(248, 250, 252, 0.72);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.form-subpanel--accordion {
  padding: 0;
  overflow: hidden;
}

.form-subpanel--accordion .ds-accordion {
  border: 0;
  border-radius: 20px;
  background: transparent;
}

.form-subpanel--accordion .ds-accordion-body {
  padding-top: 2px;
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

.ghost-btn--inline {
  min-height: 34px;
  padding: 0 12px;
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

.accountant-identity-block {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.accountant-global-name {
  margin: 0;
  color: #64748b;
  direction: ltr;
  text-align: right;
}

.accountant-mobile-number {
  margin: 0;
  font-size: 0.8rem;
  color: #94a3b8;
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

.session-panel {
  margin-top: 14px;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 18px;
  padding: 14px;
  background: rgba(248, 250, 252, 0.8);
}

.session-panel-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.session-panel-header h6 {
  margin: 0;
  font-size: 0.95rem;
  color: #0f172a;
}

.session-panel-header .refresh-sessions {
  margin-inline-start: auto;
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
  border: 1px solid rgba(148, 163, 184, 0.16);
  border-radius: 14px;
  padding: 12px;
  background: rgba(255, 255, 255, 0.86);
}

.session-item-main {
  min-width: 0;
}

.session-item-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.session-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.session-badge {
  border-radius: 999px;
  padding: 2px 8px;
  font-size: 0.72rem;
  font-weight: 750;
}

.session-badge.primary {
  background: rgba(245, 158, 11, 0.14);
  color: #b45309;
}

.session-badge.neutral {
  background: rgba(148, 163, 184, 0.14);
  color: #475569;
}

.session-item-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  margin-top: 7px;
  color: #64748b;
  font-size: 0.78rem;
}

.terminate-session {
  flex: 0 0 auto;
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

  .accountant-summary-strip,
  .accountant-form-sections,
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
  .ghost-btn,
  .ghost-btn--inline {
    width: 100%;
  }

  .accordion-header-actions {
    width: 100%;
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .session-panel-header,
  .session-item {
    align-items: stretch;
    flex-direction: column;
  }

  .session-panel-header .refresh-sessions,
  .terminate-session {
    width: 100%;
  }
}
</style>
