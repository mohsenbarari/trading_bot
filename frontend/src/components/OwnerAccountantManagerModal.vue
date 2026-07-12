<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ChevronLeft, ShieldCheck, SlidersHorizontal, UserPlus, Users } from 'lucide-vue-next'
import {
  createOwnerAccountantRelation,
  deleteOwnerAccountantRelation,
  fetchOwnerAccountantRelations,
  fetchOwnerAccountantSessions,
  makeEmptyAccountantCreateForm,
  makeEmptyAccountantEditForm,
  normalizeDutyDescription,
  terminateOwnerAccountantSession,
  updateOwnerAccountantRelation,
  type AccountantRelation,
  type AccountantSessionSummary,
} from '../composables/useOwnerAccountants'
import type { RelationStatus } from '../composables/useOwnerCustomers'
import { formatIranDateTime, parseIranDisplayDate } from '../utils/iranTime'
import { invitationRelationLink, invitationSmsStatusMessage } from '../utils/invitationContract'
import HelpPopover from './HelpPopover.vue'

const props = withDefaults(defineProps<{
  presentation?: 'modal' | 'workspace'
  initialRelationId?: string | number | null
  initialPanel?: string | null
}>(), {
  presentation: 'modal',
  initialRelationId: null,
  initialPanel: null,
})

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'open-relation', relationId: number): void
  (e: 'back-to-list'): void
}>()

type DetailSection = 'detailOverview' | 'detailSessions' | 'detailDanger'

function makeEmptyCreateForm() {
  return makeEmptyAccountantCreateForm()
}

function makeEmptyEditForm() {
  return makeEmptyAccountantEditForm()
}

const relations = ref<AccountantRelation[]>([])
const isLoading = ref(true)
const isSubmitting = ref(false)
const isSavingEdit = ref(false)
const error = ref('')
const notice = ref('')
const detailSaveNotice = ref('')
const viewportToast = ref<{ type: 'success' | 'error' | 'info'; text: string } | null>(null)
const copiedRelationId = ref<number | null>(null)
const openSessionsRelationId = ref<number | null>(null)
const sessionsByRelationId = ref<Record<number, AccountantSessionSummary[]>>({})
const loadingSessionsRelationId = ref<number | null>(null)
const terminatingSessionId = ref<string | null>(null)
const currentTimeMs = ref(Date.now())
const selectedRelationId = ref<number | null>(null)
const isWorkspace = computed(() => props.presentation === 'workspace')
const initialRelationIdNumber = computed(() => {
  if (props.initialRelationId == null || props.initialRelationId === '') return null
  const normalized = Number(props.initialRelationId)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
})

const createForm = reactive(makeEmptyCreateForm())
const editForm = reactive(makeEmptyEditForm())
const openSections = reactive({
  create: false,
  createDuty: false,
  relations: false,
  detailOverview: false,
  detailSessions: false,
  detailDanger: false,
})
let countdownTimer: number | null = null
let viewportToastTimer: number | null = null

function resetCreateForm() {
  Object.assign(createForm, makeEmptyCreateForm())
}

function toggleSection(section: keyof typeof openSections) {
  openSections[section] = !openSections[section]
}

function handleShellDismiss() {
  if (!isWorkspace.value) {
    emit('close')
  }
}

function closeManager() {
  emit('close')
}

function clearEditState() {
  Object.assign(editForm, makeEmptyEditForm())
}

function showViewportToast(type: 'success' | 'error' | 'info', text: string, timeoutMs = 4200) {
  viewportToast.value = { type, text }
  if (viewportToastTimer !== null && typeof window !== 'undefined') {
    window.clearTimeout(viewportToastTimer)
  }
  viewportToastTimer = typeof window !== 'undefined'
    ? window.setTimeout(() => {
        viewportToast.value = null
        viewportToastTimer = null
      }, timeoutMs)
    : null
}

function clearViewportToast() {
  viewportToast.value = null
  if (viewportToastTimer !== null && typeof window !== 'undefined') {
    window.clearTimeout(viewportToastTimer)
  }
  viewportToastTimer = null
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

const selectedRelation = computed(() => {
  if (selectedRelationId.value == null) return null
  return relations.value.find((relation) => relation.id === selectedRelationId.value) ?? null
})

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

const pendingInvitationRelations = computed(() => orderedRelations.value.filter((relation) => relation.status === 'pending'))

const manageableRelations = computed(() => orderedRelations.value.filter((relation) => relation.status !== 'pending'))

function openDefaultDetailSections() {
  openSections.detailOverview = true
  openSections.detailSessions = false
  openSections.detailDanger = false
}

function applyInitialRouteState() {
  if (!isWorkspace.value) return

  const routeRelationId = initialRelationIdNumber.value
  if (routeRelationId !== null) {
    selectedRelationId.value = routeRelationId
    const relation = relations.value.find((item) => item.id === routeRelationId)
    if (relation) {
      editForm.duty_description = relation.duty_description || ''
    }
    openSections.relations = true
    openDefaultDetailSections()
    return
  }

  if (selectedRelationId.value !== null) {
    selectedRelationId.value = null
    clearEditState()
    detailSaveNotice.value = ''
  }

  if (props.initialPanel === 'create') {
    openSections.create = true
    openSections.relations = false
  } else if (props.initialPanel === 'pending' || props.initialPanel === 'manage' || props.initialPanel === 'relations') {
    openSections.relations = true
  }
}

async function loadRelations() {
  isLoading.value = true
  error.value = ''

  try {
    relations.value = await fetchOwnerAccountantRelations()
    if (openSessionsRelationId.value !== null) {
      const openRelation = relations.value.find((relation) => relation.id === openSessionsRelationId.value)
      if (!openRelation || openRelation.status !== 'active' || !openRelation.accountant_user_id) {
        openSessionsRelationId.value = null
      }
    }
    if (selectedRelationId.value !== null && !relations.value.some((relation) => relation.id === selectedRelationId.value)) {
      selectedRelationId.value = null
      clearEditState()
    }
    applyInitialRouteState()
  } catch (err: any) {
    error.value = err?.message || 'دریافت لیست حسابداران ناموفق بود.'
  } finally {
    isLoading.value = false
  }
}

async function loadSessionsForRelation(relationId: number) {
  loadingSessionsRelationId.value = relationId
  error.value = ''

  try {
    const payload = await fetchOwnerAccountantSessions(relationId)
    sessionsByRelationId.value = {
      ...sessionsByRelationId.value,
      [relationId]: payload,
    }
  } catch (err: any) {
    error.value = err?.message || 'دریافت نشست‌های حسابدار ناموفق بود.'
  } finally {
    if (loadingSessionsRelationId.value === relationId) {
      loadingSessionsRelationId.value = null
    }
  }
}

async function terminateAccountantSession(relation: AccountantRelation, session: AccountantSessionSummary) {
  if (terminatingSessionId.value === session.id) return
  if (!window.confirm(`نشست «${session.device_name || 'دستگاه حسابدار'}» پایان یابد؟`)) return

  terminatingSessionId.value = session.id
  error.value = ''
  notice.value = ''

  try {
    const result = await terminateOwnerAccountantSession(relation.id, session.id)
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

async function createRelation() {
  if (isSubmitting.value) return
  isSubmitting.value = true
  error.value = ''
  notice.value = ''

  try {
    const created = await createOwnerAccountantRelation({
      account_name: createForm.account_name,
      relation_display_name: createForm.relation_display_name,
      mobile_number: createForm.mobile_number,
      duty_description: normalizeDutyDescription(createForm.duty_description),
    })
    relations.value = [created, ...relations.value.filter((item) => item.id !== created.id)]
    resetCreateForm()
    notice.value = invitationSmsStatusMessage(created.sms_status) || 'دعوت حسابدار ثبت شد.'
    openSections.relations = true
  } catch (err: any) {
    error.value = err?.message || 'ایجاد حسابدار ناموفق بود.'
  } finally {
    isSubmitting.value = false
  }
}

async function saveDetailEdit() {
  const relation = selectedRelation.value
  if (!relation || isSavingEdit.value) return
  isSavingEdit.value = true
  error.value = ''
  notice.value = ''
  detailSaveNotice.value = ''

  try {
    const updated = await updateOwnerAccountantRelation(relation.id, {
      duty_description: normalizeDutyDescription(editForm.duty_description),
    })
    relations.value = relations.value.map((item) => (item.id === updated.id ? updated : item))
    notice.value = 'اطلاعات حسابدار به‌روزرسانی شد.'
    detailSaveNotice.value = notice.value
    editForm.duty_description = updated.duty_description || ''
    showViewportToast('success', notice.value)
  } catch (err: any) {
    error.value = err?.message || 'ویرایش حسابدار ناموفق بود.'
    showViewportToast('error', error.value)
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
    await deleteOwnerAccountantRelation(relation.id, isPending ? 'لغو دعوت حسابدار ناموفق بود.' : 'قطع ارتباط حسابدار ناموفق بود.')
    relations.value = relations.value.filter((item) => item.id !== relation.id)
    if (selectedRelationId.value === relation.id) {
      selectedRelationId.value = null
      clearEditState()
    }
    if (openSessionsRelationId.value === relation.id) {
      openSessionsRelationId.value = null
    }
    notice.value = isPending ? 'دعوت حسابدار لغو شد.' : 'ارتباط حسابدار قطع شد و دسترسی او غیرفعال گردید.'
  } catch (err: any) {
    error.value = err?.message || (isPending ? 'لغو دعوت حسابدار ناموفق بود.' : 'قطع ارتباط حسابدار ناموفق بود.')
  }
}

async function copyRegistrationLink(relation: AccountantRelation) {
  const link = invitationRelationLink(relation, 'web')
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
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

function openAccountantDetail(relation: AccountantRelation) {
  selectedRelationId.value = relation.id
  editForm.duty_description = relation.duty_description || ''
  error.value = ''
  notice.value = ''
  detailSaveNotice.value = ''
  openDefaultDetailSections()
  if (isWorkspace.value) {
    emit('open-relation', relation.id)
  }
}

function backToAccountantList() {
  selectedRelationId.value = null
  clearEditState()
  detailSaveNotice.value = ''
  if (isWorkspace.value) {
    emit('back-to-list')
  }
}

async function toggleDetailSection(section: DetailSection) {
  openSections[section] = !openSections[section]
  const relation = selectedRelation.value
  if (!relation || !openSections[section]) return
  if (section === 'detailSessions' && relation.status === 'active' && relation.accountant_user_id) {
    openSessionsRelationId.value = relation.id
    await loadSessionsForRelation(relation.id)
  }
}

onMounted(() => {
  startCountdownTimer()
  applyInitialRouteState()
  void loadRelations()
})

watch(
  () => [props.initialRelationId, props.initialPanel],
  () => applyInitialRouteState(),
)

onBeforeUnmount(() => {
  stopCountdownTimer()
  clearViewportToast()
})
</script>

<template>
  <Teleport to="body" :disabled="isWorkspace">
    <div
      v-if="viewportToast"
      class="accountant-viewport-toast"
      :class="`accountant-viewport-toast--${viewportToast.type}`"
      role="status"
      aria-live="polite"
    >
      {{ viewportToast.text }}
    </div>
    <div
      :class="isWorkspace ? 'accountant-manager-page' : 'accountant-manager-backdrop'"
      @click.self="handleShellDismiss"
    >
      <div
        class="accountant-manager-shell"
        :class="{ 'accountant-manager-shell--workspace': isWorkspace }"
      >
        <div v-if="!isWorkspace" class="accountant-owner-header">
          <button type="button" class="accountant-manager-back" aria-label="بازگشت" @click="closeManager">
            <ChevronLeft :size="24" />
          </button>
          <div class="accountant-manager-title">
            <h3>حسابداران</h3>
          </div>
          <span class="accountant-owner-header-spacer" aria-hidden="true"></span>
        </div>

        <div v-if="notice" class="accountant-banner success">{{ notice }}</div>
        <div v-if="error" class="accountant-banner error">{{ error }}</div>

        <section class="accountant-panel accountant-panel--accordion">
          <div class="accountant-accordion-panel" :class="{ open: openSections.create }">
            <div class="accountant-accordion-header accountant-main-menu-header" @click="toggleSection('create')">
              <div class="accountant-accordion-header-info accountant-menu-title">
                <UserPlus :size="18" class="accountant-section-icon" />
                <h4>افزودن حسابدار جدید</h4>
              </div>
              <div class="accordion-header-actions">
                <HelpPopover
                  button-test="accountant-create-help"
                  note-test="accountant-create-help-note"
                  label="راهنمای افزودن حسابدار"
                  text="پس از ثبت، لینک ثبت‌نام مخصوص همان حسابدار ساخته می‌شود."
                />
                <ChevronLeft :size="20" class="accountant-accordion-chevron" />
              </div>
            </div>

            <div v-show="openSections.create" class="accountant-accordion-body-shell accountant-accordion-body">
              <div class="accountant-form-sections accountant-form-sections--stacked">
                <section class="form-subpanel">
                  <div class="form-subpanel-head">
                    <h5>مشخصات حسابدار</h5>
                    <p>نام کاربری، عنوان نمایشی و شماره موبایل حسابدار را وارد کنید.</p>
                  </div>
                  <div class="accountant-form-grid">
                    <label class="field-block">
                      <span>نام کاربری جهانی</span>
                      <input v-model.trim="createForm.account_name" class="accountant-input create-account-name" type="text" placeholder="accountant_01" />
                    </label>
                    <label class="field-block">
                      <span>نام نمایشی رابطه</span>
                      <input v-model.trim="createForm.relation_display_name" class="accountant-input create-display-name" type="text" placeholder="حسابدار فروش" />
                    </label>
                    <label class="field-block">
                      <span>شماره موبایل</span>
                      <input v-model.trim="createForm.mobile_number" class="accountant-input create-mobile-number" type="tel" inputmode="numeric" placeholder="09120000000" />
                    </label>
                  </div>
                </section>

                <section class="form-subpanel form-subpanel--accordion">
                  <div class="accountant-accordion-panel" :class="{ open: openSections.createDuty }">
                    <div class="accountant-accordion-header" @click.stop="toggleSection('createDuty')">
                      <div class="accountant-accordion-header-info">
                        <SlidersHorizontal :size="16" class="accountant-subsection-icon" />
                        <div>
                          <h5>شرح وظیفه</h5>
                          <p>اختیاری، برای تفکیک نقش حسابداران در گروه کاری</p>
                        </div>
                      </div>
                      <ChevronLeft :size="18" class="accountant-accordion-chevron" />
                    </div>
                    <div v-show="openSections.createDuty" class="accountant-accordion-body-shell">
                      <label class="field-block">
                        <span>شرح وظیفه</span>
                        <textarea v-model="createForm.duty_description" class="accountant-input accountant-textarea create-duty-description" rows="3" placeholder="مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه"></textarea>
                      </label>
                    </div>
                  </div>
                </section>
              </div>

              <div class="panel-actions">
                <button type="button" class="accountant-secondary-control" :disabled="isSubmitting" @click="resetCreateForm">پاک کردن</button>
                <button type="button" class="accountant-primary-control submit-create" :disabled="isSubmitting" @click="createRelation">
                  {{ isSubmitting ? 'در حال ثبت...' : 'ثبت حسابدار' }}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section class="accountant-panel accountant-panel--accordion">
          <div class="accountant-accordion-panel" :class="{ open: openSections.relations }">
            <div class="accountant-accordion-header accountant-main-menu-header" @click="toggleSection('relations')">
              <div class="accountant-accordion-header-info accountant-menu-title">
                <Users :size="18" class="accountant-section-icon" />
                <h4>مدیریت حسابداران</h4>
              </div>
              <div class="accordion-header-actions">
                <HelpPopover
                  button-test="accountant-list-help"
                  note-test="accountant-list-help-note"
                  label="راهنمای لیست حسابداران"
                  text="برای حسابدار فعال می‌توانید شرح وظیفه، نشست‌ها و قطع ارتباط را از صفحه تنظیمات مدیریت کنید."
                />
                <ChevronLeft :size="20" class="accountant-accordion-chevron" />
              </div>
            </div>

            <div v-show="openSections.relations" class="accountant-accordion-body-shell accountant-accordion-body">
              <div v-if="selectedRelation" class="accountant-detail-page">
                <div class="accountant-detail-topbar">
                  <button type="button" class="ghost-btn ghost-btn--inline" @click="backToAccountantList">بازگشت به لیست</button>
                  <div>
                    <h4>{{ selectedRelation.relation_display_name }}</h4>
                    <p>@{{ selectedRelation.global_account_name }}</p>
                  </div>
                </div>

                <div class="detail-accordion accountant-accordion-panel" :class="{ open: openSections.detailOverview }">
                  <div class="accountant-accordion-header" @click="toggleDetailSection('detailOverview')">
                    <div class="accountant-accordion-header-info">
                      <SlidersHorizontal :size="18" class="accountant-section-icon" />
                      <div>
                        <h4>مشخصات و شرح وظیفه</h4>
                        <p>وضعیت، زمان‌ها و شرح وظیفه قابل ویرایش حسابدار</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="accountant-accordion-chevron" />
                  </div>
                  <div v-show="openSections.detailOverview" class="accountant-accordion-body-shell accountant-accordion-body">
                    <div class="accountant-meta-grid">
                      <div class="meta-item">
                        <span class="meta-label">وضعیت</span>
                        <span class="meta-value">{{ statusLabel(selectedRelation.status) }}</span>
                      </div>
                      <div class="meta-item">
                        <span class="meta-label">کاربر لینک‌شده</span>
                        <span class="meta-value">{{ selectedRelation.accountant_account_name || 'هنوز ثبت‌نام نشده' }}</span>
                      </div>
                      <div class="meta-item">
                        <span class="meta-label">موبایل</span>
                        <span class="meta-value accountant-mobile-value">{{ selectedRelation.mobile_number }}</span>
                      </div>
                      <div class="meta-item">
                        <span class="meta-label">ایجاد</span>
                        <span class="meta-value">{{ formatDateTime(selectedRelation.created_at) }}</span>
                      </div>
                      <div class="meta-item">
                        <span class="meta-label">فعال‌سازی</span>
                        <span class="meta-value">{{ formatDateTime(selectedRelation.activated_at) }}</span>
                      </div>
                      <div v-if="selectedRelation.status === 'pending'" class="meta-item">
                        <span class="meta-label">انقضا</span>
                        <span class="meta-value">{{ formatDateTime(selectedRelation.expires_at) }}</span>
                      </div>
                    </div>
                    <p v-if="getRelationStateText(selectedRelation)" class="accountant-state-copy" :class="`status-${selectedRelation.status}`">{{ getRelationStateText(selectedRelation) }}</p>
                    <div class="form-subpanel">
                      <div class="form-subpanel-head">
                        <h5>ویرایش شرح وظیفه</h5>
                        <p>شرح وظیفه حسابدار را برای تفکیک نقش او در گروه کاری به‌روزرسانی کنید.</p>
                      </div>
                      <label class="field-block">
                        <span>شرح وظیفه</span>
                        <textarea v-model="editForm.duty_description" class="accountant-input accountant-textarea edit-duty-description" rows="3" :placeholder="selectedRelation.duty_description || 'مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه'"></textarea>
                      </label>
                      <div class="panel-actions compact">
                        <button type="button" class="accountant-secondary-control" :disabled="isSavingEdit" @click="editForm.duty_description = selectedRelation.duty_description || ''">بازنشانی</button>
                        <button type="button" class="accountant-primary-control save-edit" :disabled="isSavingEdit" @click="saveDetailEdit">
                          {{ isSavingEdit ? 'در حال ذخیره...' : 'ذخیره تغییرات' }}
                        </button>
                      </div>
                      <p v-if="detailSaveNotice" class="detail-save-feedback success">{{ detailSaveNotice }}</p>
                    </div>
                  </div>
                </div>

                <div class="detail-accordion accountant-accordion-panel" :class="{ open: openSections.detailSessions }">
                  <div class="accountant-accordion-header" @click="toggleDetailSection('detailSessions')">
                    <div class="accountant-accordion-header-info">
                      <ShieldCheck :size="18" class="accountant-section-icon" />
                      <div>
                        <h4>نشست حسابدار</h4>
                        <p>مشاهده و منقضی کردن نشست‌های فعال حسابدار</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="accountant-accordion-chevron" />
                  </div>
                  <div v-show="openSections.detailSessions" class="accountant-accordion-body-shell accountant-accordion-body">
                    <div v-if="selectedRelation.status !== 'active' || !selectedRelation.accountant_user_id" class="accountant-empty">نشست فقط برای حسابدار فعال قابل مدیریت است.</div>
                    <div v-else-if="loadingSessionsRelationId === selectedRelation.id" class="accountant-loading session-loading">در حال دریافت نشست‌های حسابدار...</div>
                    <div v-else-if="!getRelationSessions(selectedRelation.id).length" class="accountant-empty session-empty">در حال حاضر نشست فعالی برای این حسابدار ثبت نشده است.</div>
                    <ul v-else class="session-list">
                      <li v-for="session in getRelationSessions(selectedRelation.id)" :key="session.id" class="session-item">
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
                        <button type="button" class="danger-btn terminate-session" :disabled="terminatingSessionId === session.id" @click="terminateAccountantSession(selectedRelation, session)">
                          {{ terminatingSessionId === session.id ? 'در حال پایان...' : 'پایان نشست' }}
                        </button>
                      </li>
                    </ul>
                    <div v-if="selectedRelation.status === 'active' && selectedRelation.accountant_user_id" class="panel-actions compact">
                      <button type="button" class="ghost-btn refresh-sessions" :disabled="loadingSessionsRelationId === selectedRelation.id" @click="loadSessionsForRelation(selectedRelation.id)">نوسازی نشست‌ها</button>
                    </div>
                  </div>
                </div>

                <div class="detail-accordion accountant-accordion-panel danger-accordion" :class="{ open: openSections.detailDanger }">
                  <div class="accountant-accordion-header" @click="toggleDetailSection('detailDanger')">
                    <div class="accountant-accordion-header-info">
                      <Users :size="18" class="accountant-section-icon" />
                      <div>
                        <h4>قطع رابطه با حسابدار</h4>
                        <p>غیرفعال کردن رابطه و دسترسی حسابدار</p>
                      </div>
                    </div>
                    <ChevronLeft :size="20" class="accountant-accordion-chevron" />
                  </div>
                  <div v-show="openSections.detailDanger" class="accountant-accordion-body-shell accountant-accordion-body">
                    <p class="danger-copy">این عملیات رابطه حسابدار را غیرفعال می‌کند و نشست‌های مربوط به او باید جداگانه مدیریت شوند.</p>
                    <button v-if="selectedRelation.status === 'active'" type="button" class="danger-btn unlink-active" @click="unlinkRelation(selectedRelation)">قطع ارتباط با حسابدار</button>
                    <button v-else-if="selectedRelation.status === 'pending'" type="button" class="danger-btn cancel-pending" @click="unlinkRelation(selectedRelation)">لغو دعوت حسابدار</button>
                    <div v-else class="accountant-empty">این رابطه در وضعیت قابل قطع نیست.</div>
                  </div>
                </div>
              </div>

              <div v-else-if="isLoading" class="accountant-loading">در حال دریافت لیست حسابداران...</div>
              <div v-else-if="orderedRelations.length === 0" class="accountant-empty">هنوز حسابداری برای این مالک ثبت نشده است.</div>

              <div v-else class="accountant-management-stack">
                <section v-if="pendingInvitationRelations.length" class="pending-invitations-panel">
                  <div class="pending-invitations-head">
                    <div>
                      <h5>دعوت‌نامه‌های در انتظار</h5>
                      <p>فقط دعوت‌هایی که هنوز توسط حسابدار تکمیل نشده‌اند نمایش داده می‌شوند.</p>
                    </div>
                    <span>{{ pendingInvitationRelations.length.toLocaleString('fa-IR') }}</span>
                  </div>
                  <article v-for="relation in pendingInvitationRelations" :key="`pending-${relation.id}`" class="pending-invitation-card">
                    <div class="pending-invitation-main">
                      <strong>{{ relation.relation_display_name }}</strong>
                      <span>@{{ relation.global_account_name }}</span>
                      <p>{{ getRelationStateText(relation) }}</p>
                      <p v-if="invitationSmsStatusMessage(relation.sms_status)">{{ invitationSmsStatusMessage(relation.sms_status) }}</p>
                    </div>
                    <div class="pending-invitation-actions">
                      <button v-if="invitationRelationLink(relation, 'web')" type="button" class="accountant-secondary-control copy-link" @click="copyRegistrationLink(relation)">
                        {{ copiedRelationId === relation.id ? 'کپی شد' : 'کپی لینک وب' }}
                      </button>
                      <button type="button" class="danger-btn cancel-pending expire-pending-invitation" @click="unlinkRelation(relation)">
                        منقضی کردن دعوت
                      </button>
                    </div>
                  </article>
                </section>

                <div v-if="manageableRelations.length" class="accountant-list">
                  <article v-for="relation in manageableRelations" :key="relation.id" class="accountant-card profile-relation-card profile-relation-card--accountant">
                    <div class="accountant-card-head accountant-card-head--manage">
                      <div class="accountant-card-main">
                        <div class="accountant-card-title-row">
                          <div class="accountant-identity-block">
                            <h5>{{ relation.relation_display_name }}</h5>
                            <p class="accountant-global-name">@{{ relation.global_account_name }}</p>
                          </div>
                          <span class="accountant-status-badge" :class="`status-${relation.status}`">{{ statusLabel(relation.status) }}</span>
                        </div>
                        <div class="accountant-card-meta-pills">
                          <span class="accountant-info-pill accountant-mobile-number">
                            <span>موبایل</span>
                            <strong>{{ relation.mobile_number }}</strong>
                          </span>
                          <span class="accountant-info-pill">
                            <span>کاربر لینک‌شده</span>
                            <strong>{{ relation.accountant_account_name || 'ثبت‌نام نشده' }}</strong>
                          </span>
                          <span class="accountant-info-pill">
                            <span>ایجاد</span>
                            <strong>{{ formatDateTime(relation.created_at) }}</strong>
                          </span>
                          <span class="accountant-info-pill">
                            <span>فعال‌سازی</span>
                            <strong>{{ formatDateTime(relation.activated_at) }}</strong>
                          </span>
                        </div>
                        <p v-if="relation.duty_description" class="accountant-duty compact-duty">{{ relation.duty_description }}</p>
                        <div class="accountant-card-footer">
                          <button type="button" class="accountant-primary-control accountant-settings-btn" @click="openAccountantDetail(relation)">
                            تنظیمات حسابدار
                          </button>
                        </div>
                      </div>
                    </div>
                  </article>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.accountant-manager-page {
  width: 100%;
}

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

.accountant-manager-shell--workspace {
  width: 100%;
  max-height: none;
  overflow: visible;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  padding: 0;
}

.accountant-owner-header {
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

.accountant-owner-header h3 {
  margin: 0;
  font-size: 1.35rem;
  color: #111827;
}

.ghost-btn,
.accountant-primary-control,
.accountant-secondary-control,
.danger-btn {
  border: 0;
  border-radius: 999px;
  min-height: 40px;
  padding: 0 16px;
  font-weight: 700;
  cursor: pointer;
}

.ghost-btn,
.accountant-secondary-control {
  background: rgba(148, 163, 184, 0.14);
  color: #334155;
}

.accountant-primary-control {
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

.accountant-panel--accordion {
  border: 0;
  background: transparent;
  padding: 0;
  display: block;
}

.accountant-panel--accordion .accountant-accordion-panel {
  border-radius: 24px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(255, 255, 255, 0.82);
  overflow: hidden;
}

.accountant-panel--accordion .accountant-accordion-header {
  gap: 14px;
}

.accountant-panel--accordion .accountant-accordion-header-info {
  gap: 12px;
}

.accountant-panel--accordion .accountant-accordion-header-info h4,
.form-subpanel--accordion .accountant-accordion-header-info h5 {
  margin: 0;
  color: #0f172a;
}

.accountant-panel--accordion .accountant-accordion-header-info p,
.form-subpanel--accordion .accountant-accordion-header-info p {
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

.form-subpanel--accordion .accountant-accordion-panel {
  border: 0;
  border-radius: 20px;
  background: transparent;
}

.form-subpanel--accordion .accountant-accordion-body-shell {
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

.accountant-manager-shell {
  width: min(1040px, 100%);
  gap: 0.625rem;
}

.accountant-owner-header {
  display: grid;
  grid-template-columns: 44px 1fr 44px;
  align-items: center;
  min-height: 74px;
  gap: 12px;
  direction: ltr;
}

.accountant-manager-title {
  text-align: center;
  direction: rtl;
}

.accountant-manager-back {
  width: 44px;
  height: 44px;
  min-height: 44px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.92);
  color: #334155;
  border: 1px solid rgba(148, 163, 184, 0.16);
  box-shadow: 0 10px 24px rgba(15, 23, 42, 0.08);
  cursor: pointer;
}

.accountant-owner-header-spacer {
  width: 44px;
  height: 44px;
}

.accountant-viewport-toast {
  position: fixed;
  top: calc(env(safe-area-inset-top, 0px) + 14px);
  left: 50%;
  z-index: 1305;
  width: min(520px, calc(100vw - 28px));
  transform: translateX(-50%);
  border-radius: 18px;
  padding: 0.85rem 1rem;
  direction: rtl;
  text-align: right;
  font-size: 0.88rem;
  font-weight: 850;
  line-height: 1.8;
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.24);
  backdrop-filter: blur(12px);
}

.accountant-viewport-toast--success {
  border: 1px solid rgba(16, 185, 129, 0.28);
  background: rgba(240, 253, 244, 0.96);
  color: #047857;
}

.accountant-viewport-toast--error {
  border: 1px solid rgba(239, 68, 68, 0.26);
  background: rgba(254, 242, 242, 0.96);
  color: #b91c1c;
}

.accountant-viewport-toast--info {
  border: 1px solid rgba(245, 158, 11, 0.28);
  background: rgba(255, 251, 235, 0.96);
  color: #92400e;
}

.accountant-panel {
  border-radius: 1rem;
}

.accountant-panel--accordion .accountant-accordion-panel {
  border-radius: 1rem;
  border: 1px solid rgba(245, 158, 11, 0.18);
  background: linear-gradient(135deg, #fffbeb, #fef3c7);
  overflow: hidden;
  box-shadow: 0 10px 28px rgba(245, 158, 11, 0.08);
}

.accountant-panel--accordion .accountant-accordion-header {
  gap: 0.6rem;
  min-height: 3.28rem;
  padding: 0.72rem 0.82rem;
}

.accountant-panel--accordion .accountant-accordion-header-info {
  gap: 0.58rem;
}

.accountant-panel--accordion .accountant-accordion-header-info h4,
.form-subpanel--accordion .accountant-accordion-header-info h5 {
  color: #92400e;
}

.accountant-main-menu-header {
  display: grid;
  grid-template-columns: minmax(9rem, 1fr) auto;
  align-items: center;
  direction: rtl;
}

.accountant-menu-title {
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  min-width: 0;
}

.accountant-menu-title h4 {
  flex: 0 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: inherit;
  font-size: 0.88rem;
  font-weight: 850;
  letter-spacing: 0;
  line-height: 1.6;
}

.accountant-main-menu-header .accordion-header-actions {
  min-width: 0;
  gap: 0.38rem;
  justify-content: flex-end;
}

.accountant-accordion-body,
.accountant-management-stack,
.accountant-list,
.accountant-detail-page {
  display: flex;
  flex-direction: column;
  gap: 0.625rem;
}

.form-subpanel {
  border-radius: 1rem;
  padding: 0.8rem;
  gap: 0.625rem;
}

.form-subpanel-head h5 {
  margin: 0 0 4px;
  font-size: 0.92rem;
  color: #0f172a;
}

.form-subpanel-head p {
  margin: 0;
  font-size: 0.8rem;
  color: #64748b;
}

.accountant-form-grid,
.accountant-meta-grid {
  gap: 0.625rem;
}

.pending-invitations-panel {
  display: flex;
  flex-direction: column;
  gap: 0.55rem;
  padding: 0.7rem;
  border-radius: 1rem;
  border: 1px solid rgba(245, 158, 11, 0.16);
  background: rgba(255, 251, 235, 0.72);
}

.pending-invitations-head,
.pending-invitation-card,
.pending-invitation-actions,
.accountant-card-footer {
  display: flex;
  align-items: center;
}

.pending-invitations-head {
  justify-content: space-between;
  gap: 0.75rem;
}

.pending-invitations-head h5,
.pending-invitations-head p,
.pending-invitation-main p {
  margin: 0;
}

.pending-invitations-head h5 {
  color: #92400e;
  font-size: 0.82rem;
  line-height: 1.7;
}

.pending-invitations-head p {
  color: #64748b;
  font-size: 0.7rem;
  line-height: 1.7;
}

.pending-invitations-head > span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 2rem;
  height: 2rem;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.16);
  color: #92400e;
  font-size: 0.78rem;
  font-weight: 900;
}

.pending-invitation-card {
  justify-content: space-between;
  gap: 0.65rem;
  padding: 0.62rem;
  border-radius: 0.9rem;
  border: 1px solid rgba(15, 23, 42, 0.06);
  background: rgba(255, 255, 255, 0.9);
}

.pending-invitation-main {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 0.12rem;
}

.pending-invitation-main strong {
  overflow: hidden;
  color: #0f172a;
  font-size: 0.82rem;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pending-invitation-main span {
  color: #64748b;
  direction: ltr;
  font-size: 0.7rem;
  text-align: right;
}

.pending-invitation-main p {
  color: #b45309;
  font-size: 0.7rem;
  font-weight: 750;
  line-height: 1.65;
}

.pending-invitation-actions {
  flex: 0 0 auto;
  gap: 0.45rem;
}

.accountant-card {
  border-radius: 14px;
  border: 1px solid rgba(15, 23, 42, 0.07);
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.98), rgba(255, 251, 235, 0.72));
  padding: 0.7rem;
  gap: 0.6rem;
  box-shadow: 0 8px 20px rgba(15, 23, 42, 0.045);
}

.profile-relation-card.accountant-card {
  border-radius: var(--ds-radius-md);
  border-color: var(--ds-border-accent);
  background: var(--ds-bg-card);
  box-shadow: var(--ds-shadow-sm);
  padding: 0.85rem;
}

.accountant-card-head {
  align-items: center;
  justify-content: flex-start;
  gap: 0.6rem;
}

.accountant-card-main {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 0.48rem;
}

.accountant-card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.accountant-card-head h5 {
  margin: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 0.86rem;
  font-weight: 850;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.accountant-global-name {
  font-size: 0.72rem;
}

.accountant-status-badge {
  padding: 4px 9px;
  font-size: 0.7rem;
  line-height: 1.5;
}

.accountant-card-meta-pills {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(6.8rem, 1fr));
  gap: 0.42rem;
}

.accountant-mobile-number {
  grid-column: 1 / -1;
}

.accountant-info-pill {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  min-height: 2rem;
  gap: 0.42rem;
  padding: 0.28rem 0.52rem;
  border-radius: 0.78rem;
  background: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(148, 163, 184, 0.13);
  color: #475569;
  font-size: 0.68rem;
  font-weight: 700;
  line-height: 1.5;
}

.accountant-info-pill span {
  color: #94a3b8;
  font-weight: 750;
}

.accountant-info-pill strong {
  min-width: 0;
  overflow: hidden;
  color: #0f172a;
  font-size: 0.72rem;
  font-weight: 850;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.accountant-mobile-number strong,
.accountant-mobile-value {
  overflow: visible;
  direction: ltr;
  text-align: left;
  text-overflow: clip;
}

.accountant-card-footer {
  justify-content: flex-start;
}

.accountant-settings-btn {
  min-height: 2.35rem;
  padding: 0 0.85rem;
  border-radius: 0.85rem;
  box-shadow: none;
  font-size: 0.76rem;
}

.compact-duty {
  padding: 0.5rem 0.65rem;
  border-radius: 0.78rem;
  font-size: 0.72rem;
  line-height: 1.7;
}

.accountant-detail-topbar {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 0.9rem;
  padding: 0.9rem;
  border-radius: 1.1rem;
  background: rgba(255, 251, 235, 0.72);
  border: 1px solid rgba(245, 158, 11, 0.14);
}

.accountant-detail-topbar h4,
.accountant-detail-topbar p {
  margin: 0;
}

.accountant-detail-topbar p {
  color: #64748b;
  direction: ltr;
  text-align: right;
}

.detail-accordion {
  border-radius: 1.15rem;
  overflow: hidden;
}

.detail-accordion .accountant-accordion-header-info h4 {
  font-size: 0.86rem;
  line-height: 1.7;
}

.detail-accordion .accountant-accordion-header-info p {
  margin: 2px 0 0;
  color: #64748b;
  font-size: 0.74rem;
  line-height: 1.6;
}

.detail-save-feedback {
  margin: 0;
  padding: 0.62rem 0.75rem;
  border-radius: 0.9rem;
  font-size: 0.76rem;
  font-weight: 800;
  line-height: 1.7;
}

.detail-save-feedback.success {
  border: 1px solid rgba(16, 185, 129, 0.18);
  background: rgba(16, 185, 129, 0.12);
  color: #047857;
}

.danger-accordion {
  border-color: rgba(239, 68, 68, 0.18) !important;
}

.danger-copy {
  margin: 0;
  color: #991b1b;
  line-height: 1.8;
  font-weight: 700;
}

@media (max-width: 720px) {
  .accountant-manager-backdrop {
    padding: 0;
  }

  .accountant-manager-shell {
    width: 100%;
    border-radius: 24px 24px 0 0;
    min-height: 100%;
    padding: 12px 20px 22px;
    gap: 0.625rem;
  }

  .panel-title-row,
  .accountant-card-head {
    flex-direction: column;
  }

  .accountant-form-sections,
  .accountant-form-grid,
  .accountant-meta-grid {
    grid-template-columns: 1fr;
  }

  .accountant-detail-topbar {
    align-items: stretch;
    flex-direction: column;
  }

  .panel-actions,
  .accountant-actions {
    justify-content: stretch;
  }

  .panel-actions > button,
  .accountant-actions > button,
  .ghost-btn,
  .ghost-btn--inline {
    width: 100%;
  }

  .accordion-header-actions {
    justify-content: flex-start;
  }

  .accountant-main-menu-header {
    grid-template-columns: minmax(0, 1fr) auto;
    min-height: 3.85rem;
  }

  .accountant-menu-title h4 {
    font-size: 0.88rem;
  }

  .accountant-card {
    padding: 0.62rem;
  }

  .accountant-card-head {
    flex-direction: row;
    align-items: flex-start;
    gap: 0.5rem;
  }

  .accountant-card-title-row {
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
  }

  .accountant-card-meta-pills {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .pending-invitation-card {
    align-items: stretch;
    flex-direction: column;
  }

  .pending-invitation-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .pending-invitation-actions > button,
  .accountant-settings-btn {
    width: 100%;
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
