<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BriefcaseBusiness, Clock, Copy, ShieldAlert, UserPlus, Users } from 'lucide-vue-next'
import OwnerAccountantManagerModal from '../components/OwnerAccountantManagerModal.vue'
import { WorkspaceNotice, WorkspaceSection, WorkspaceShell } from '../components/workspace'
import {
  AppActionCard,
  AppBottomSheet,
  AppButton,
  AppCard,
  AppConfirmDialog,
  AppDangerZone,
  AppEmptyState,
  AppFilterChips,
  AppFormField,
  AppInput,
  AppListItem,
  AppMasterDetail,
  AppMetricCard,
  AppResponsiveDialog,
  AppSearchField,
  AppStatusBadge,
  AppTabs,
  AppTextarea,
} from '../components/ui'
import {
  createOwnerAccountantRelation,
  deleteOwnerAccountantRelation,
  fetchOwnerAccountantRelations,
  fetchOwnerAccountantSessions,
  normalizeDutyDescription,
  terminateOwnerAccountantSession,
  updateOwnerAccountantRelation,
  useOwnerAccountants,
  type AccountantRelation,
  type AccountantSessionSummary,
} from '../composables/useOwnerAccountants'

const route = useRoute()
const router = useRouter()
const accountantState = useOwnerAccountants()
const isLoading = ref(true)
const isMobile = ref(false)
const error = ref('')
const isCompatibilityManagerOpen = ref(false)
const compatibilityPanel = ref<string | null>(null)
const isCreatePanelOpen = ref(false)
const isCreateSubmitting = ref(false)
const createError = ref('')
const createNotice = ref('')
const isSavingDuty = ref(false)
const dutyError = ref('')
const dutyNotice = ref('')
const copiedRelationId = ref<number | null>(null)
const isConfirmDialogOpen = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmAction = ref<'terminate-session' | 'cancel-invitation' | 'unlink-relation' | null>(null)
const confirmRelation = ref<AccountantRelation | null>(null)
const confirmSession = ref<AccountantSessionSummary | null>(null)
const searchQuery = ref('')
const relationFilter = ref('all')
const detailSessions = ref<AccountantSessionSummary[]>([])
const detailSessionsLoading = ref(false)
const detailSessionsError = ref('')

const relationFilterOptions = [
  { key: 'all', label: 'همه' },
  { key: 'active', label: 'فعال' },
  { key: 'pending', label: 'دعوت‌ها' },
  { key: 'inactive', label: 'غیرفعال' },
]

const detailTabOptions = [
  { key: 'profile', label: 'مشخصات' },
  { key: 'duty', label: 'شرح وظیفه' },
  { key: 'sessions', label: 'نشست‌ها' },
  { key: 'danger', label: 'حساس' },
]

const relationId = computed(() => {
  const value = route.params.relationId
  return Array.isArray(value) ? value[0] ?? null : value ?? null
})

const relationIdNumber = computed(() => {
  if (relationId.value == null || relationId.value === '') return null
  const normalized = Number(relationId.value)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
})

const initialPanel = computed(() => {
  const value = route.query.panel ?? route.query.section
  return Array.isArray(value) ? value[0] ?? null : value ?? null
})

const activeRelation = computed(() => {
  const id = relationIdNumber.value
  if (id == null) return null
  return accountantState.relations.value.find((relation) => relation.id === id) ?? null
})

const activeCount = computed(() => accountantState.relations.value.filter((relation) => relation.status === 'active').length)
const inactiveCount = computed(() => accountantState.relations.value.filter((relation) => relation.status !== 'active' && relation.status !== 'pending').length)

const detailTab = computed({
  get() {
    const value = route.query.tab
    const normalized = Array.isArray(value) ? value[0] : value
    return detailTabOptions.some((option) => option.key === normalized) ? normalized as string : 'profile'
  },
  set(tab: string) {
    if (!detailTabOptions.some((option) => option.key === tab)) return
    router.push({
      name: relationId.value ? 'operations-accountants-detail' : 'operations-accountants',
      params: relationId.value ? { relationId: String(relationId.value) } : {},
      query: { ...route.query, tab },
    })
  },
})

const filteredRelations = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase('fa-IR')
  return accountantState.orderedRelations.value.filter((relation) => {
    const filter = relationFilter.value
    if (filter === 'active' && relation.status !== 'active') return false
    if (filter === 'pending' && relation.status !== 'pending') return false
    if (filter === 'inactive' && (relation.status === 'active' || relation.status === 'pending')) return false
    if (!query) return true
    const haystack = [
      relation.relation_display_name,
      relation.accountant_account_name,
      relation.global_account_name,
      relation.mobile_number,
      relation.duty_description,
      relation.status,
    ].filter(Boolean).join(' ').toLocaleLowerCase('fa-IR')
    return haystack.includes(query)
  })
})

const visiblePendingRelations = computed(() => filteredRelations.value.filter((relation) => relation.status === 'pending'))
const visibleManageableRelations = computed(() => filteredRelations.value.filter((relation) => relation.status !== 'pending'))

const generatedGlobalAccountName = computed(() => accountantState.createForm.account_name.trim())

async function loadRelations() {
  isLoading.value = true
  error.value = ''
  try {
    accountantState.relations.value = await fetchOwnerAccountantRelations()
  } catch (err: any) {
    error.value = err?.message || 'دریافت لیست حسابداران ناموفق بود.'
  } finally {
    isLoading.value = false
  }
}

function goToOperations() {
  router.push({ name: 'operations' })
}

function openRelation(relationId: number) {
  router.push({
    name: 'operations-accountants-detail',
    params: { relationId: String(relationId) },
    query: route.query,
  })
}

function backToList() {
  router.push({
    name: 'operations-accountants',
    query: route.query,
  })
}

function handleBack() {
  if (isCompatibilityManagerOpen.value) {
    closeCompatibilityManager()
    return
  }
  if (relationId.value) {
    backToList()
    return
  }
  goToOperations()
}

function openCompatibilityManager(panel: string | null = null) {
  compatibilityPanel.value = panel
  isCompatibilityManagerOpen.value = true
}

function closeCompatibilityManager() {
  isCompatibilityManagerOpen.value = false
  compatibilityPanel.value = null
  void loadRelations()
}

function updateIsMobile() {
  if (typeof window === 'undefined') return
  isMobile.value = window.innerWidth < 768
}

function openCreatePanel() {
  isCreatePanelOpen.value = true
  createError.value = ''
  createNotice.value = ''
}

function closeCreatePanel() {
  isCreatePanelOpen.value = false
}

function resetCreateForm() {
  Object.assign(accountantState.createForm, {
    account_name: '',
    relation_display_name: '',
    mobile_number: '',
    duty_description: '',
  })
}

function seedEditForm(relation: AccountantRelation | null, options: { resetFeedback?: boolean } = {}) {
  const { resetFeedback = true } = options
  accountantState.editForm.duty_description = relation?.duty_description || ''
  if (resetFeedback) {
    dutyError.value = ''
    dutyNotice.value = ''
  }
}

async function loadDetailSessions(force = false) {
  const relation = activeRelation.value
  if (!relation || relation.status !== 'active' || !relation.accountant_user_id) {
    detailSessions.value = []
    return
  }
  if (!force && detailSessions.value.length) return
  detailSessionsLoading.value = true
  detailSessionsError.value = ''
  try {
    detailSessions.value = await fetchOwnerAccountantSessions(relation.id)
  } catch (err: any) {
    detailSessionsError.value = err?.message || 'دریافت نشست‌های حسابدار ناموفق بود.'
  } finally {
    detailSessionsLoading.value = false
  }
}

function refreshCurrentDetailTab() {
  if (detailTab.value === 'sessions') {
    void loadDetailSessions(true)
  }
}

async function createRelation() {
  if (isCreateSubmitting.value) return
  isCreateSubmitting.value = true
  createError.value = ''
  createNotice.value = ''
  try {
    const created = await createOwnerAccountantRelation({
      account_name: accountantState.createForm.account_name,
      relation_display_name: accountantState.createForm.relation_display_name,
      mobile_number: accountantState.createForm.mobile_number,
      duty_description: normalizeDutyDescription(accountantState.createForm.duty_description),
    })
    accountantState.relations.value = [created, ...accountantState.relations.value.filter((item) => item.id !== created.id)]
    createNotice.value = 'دعوت حسابدار با موفقیت ثبت شد.'
    resetCreateForm()
    closeCreatePanel()
  } catch (err: any) {
    createError.value = err?.message || 'ایجاد حسابدار ناموفق بود.'
  } finally {
    isCreateSubmitting.value = false
  }
}

async function saveDuty() {
  const relation = activeRelation.value
  if (!relation || isSavingDuty.value) return
  const normalizedDuty = normalizeDutyDescription(accountantState.editForm.duty_description)
  const currentDuty = normalizeDutyDescription(relation.duty_description || '')
  if (normalizedDuty === currentDuty) {
    dutyNotice.value = 'تغییری برای ذخیره انتخاب نشده است.'
    return
  }
  isSavingDuty.value = true
  dutyError.value = ''
  dutyNotice.value = ''
  try {
    const updated = await updateOwnerAccountantRelation(relation.id, {
      duty_description: normalizedDuty,
    })
    accountantState.relations.value = accountantState.relations.value.map((item) => (item.id === updated.id ? updated : item))
    seedEditForm(updated, { resetFeedback: false })
    dutyNotice.value = 'شرح وظیفه حسابدار ذخیره شد.'
  } catch (err: any) {
    dutyError.value = err?.message || 'ذخیره شرح وظیفه ناموفق بود.'
  } finally {
    isSavingDuty.value = false
  }
}

async function copyRegistrationLink(relation: AccountantRelation) {
  if (!relation.registration_link) return
  try {
    await navigator.clipboard.writeText(relation.registration_link)
    copiedRelationId.value = relation.id
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        if (copiedRelationId.value === relation.id) copiedRelationId.value = null
      }, 1800)
    }
  } catch {
    dutyError.value = 'کپی لینک ثبت‌نام ممکن نشد.'
  }
}

function openConfirmDialog(
  kind: 'terminate-session' | 'cancel-invitation' | 'unlink-relation',
  relation: AccountantRelation,
  session: AccountantSessionSummary | null = null,
) {
  confirmAction.value = kind
  confirmRelation.value = relation
  confirmSession.value = session
  confirmTitle.value = kind === 'terminate-session'
    ? 'پایان نشست'
    : kind === 'cancel-invitation'
      ? 'لغو دعوت حسابدار'
      : 'قطع ارتباط حسابدار'
  confirmMessage.value = kind === 'terminate-session'
    ? `نشست «${session?.device_name || 'دستگاه حسابدار'}» پایان یابد؟`
    : kind === 'cancel-invitation'
      ? `دعوت «${relation.relation_display_name}» لغو شود؟`
      : `ارتباط «${relation.relation_display_name}» قطع شود؟ این عملیات دسترسی حسابدار را غیرفعال می‌کند.`
  isConfirmDialogOpen.value = true
}

function closeConfirmDialog() {
  isConfirmDialogOpen.value = false
  confirmAction.value = null
  confirmRelation.value = null
  confirmSession.value = null
}

async function handleConfirmAction() {
  const relation = confirmRelation.value
  if (!relation || !confirmAction.value) return
  const action = confirmAction.value
  const session = confirmSession.value
  closeConfirmDialog()

  if (action === 'terminate-session' && session) {
    detailSessionsError.value = ''
    try {
      await terminateOwnerAccountantSession(relation.id, session.id)
      await loadDetailSessions(true)
    } catch (err: any) {
      detailSessionsError.value = err?.message || 'پایان دادن نشست حسابدار ناموفق بود.'
    }
    return
  }

  try {
    await deleteOwnerAccountantRelation(
      relation.id,
      action === 'cancel-invitation' ? 'لغو دعوت حسابدار ناموفق بود.' : 'قطع ارتباط حسابدار ناموفق بود.',
    )
    accountantState.relations.value = accountantState.relations.value.filter((item) => item.id !== relation.id)
    if (activeRelation.value?.id === relation.id) {
      backToList()
    }
  } catch (err: any) {
    detailSessionsError.value = err?.message
      || (action === 'cancel-invitation' ? 'لغو دعوت حسابدار ناموفق بود.' : 'قطع ارتباط حسابدار ناموفق بود.')
  }
}

function getRelationTitle(relation: AccountantRelation) {
  return relation.relation_display_name || relation.accountant_account_name || relation.global_account_name || 'حسابدار'
}

function getRelationDescription(relation: AccountantRelation) {
  const mobile = relation.mobile_number || 'بدون شماره'
  return relation.duty_description ? `${mobile} - ${relation.duty_description}` : mobile
}

function getStatusTone(status: string) {
  if (status === 'active') return 'success'
  if (status === 'pending') return 'warning'
  if (status === 'deleted' || status === 'revoked') return 'danger'
  return 'neutral'
}

function getStatusLabel(status: string) {
  if (status === 'active') return 'فعال'
  if (status === 'pending') return 'دعوت'
  if (status === 'expired') return 'منقضی'
  if (status === 'revoked') return 'لغوشده'
  if (status === 'deleted') return 'حذف‌شده'
  return status || 'نامشخص'
}

function formatDate(value: string | null | undefined) {
  if (!value) return 'ثبت نشده'
  try {
    return new Intl.DateTimeFormat('fa-IR', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  } catch {
    return value
  }
}

function getDutyText(relation: AccountantRelation) {
  return relation.duty_description || 'شرح وظیفه‌ای ثبت نشده است.'
}

watch(initialPanel, (panel) => {
  if (panel === 'create') {
    openCreatePanel()
    return
  }
  if (panel === 'pending') {
    relationFilter.value = 'pending'
    return
  }
  if (panel === 'legacy') {
    openCompatibilityManager(panel)
  }
}, { immediate: true })

watch([activeRelation, detailTab], () => {
  detailSessions.value = []
  detailSessionsError.value = ''
  refreshCurrentDetailTab()
}, { flush: 'post' })

watch(activeRelation, (relation, previousRelation) => {
  seedEditForm(relation, {
    resetFeedback: relation?.id !== previousRelation?.id,
  })
}, { immediate: true })

onMounted(() => {
  updateIsMobile()
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', updateIsMobile)
  }
  void loadRelations()
})

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', updateIsMobile)
  }
})
</script>

<template>
  <div class="ds-page accountant-workspace-view">
    <WorkspaceShell
      title="حسابداران"
      eyebrow="عملیات"
      description="افزودن، مرور و تنظیم روابط حسابداران در یک فضای کاری یکپارچه."
      layout="split"
      show-back
      back-label="بازگشت"
      @back="handleBack"
    >
      <template #actions>
        <AppButton variant="secondary" class="accountant-workspace-action" @click="goToOperations">
          عملیات
        </AppButton>
        <AppButton variant="primary" class="accountant-workspace-create" @click="openCreatePanel">
          <template #icon>
            <UserPlus :size="16" />
          </template>
          افزودن حسابدار
        </AppButton>
      </template>

      <section v-if="isCompatibilityManagerOpen" class="workspace-compatibility-panel">
        <OwnerAccountantManagerModal
          presentation="workspace"
          :initial-relation-id="relationId"
          :initial-panel="compatibilityPanel || initialPanel"
          @close="closeCompatibilityManager"
          @open-relation="openRelation"
          @back-to-list="backToList"
        />
      </section>

      <template v-else>
        <WorkspaceSection
          title="نمای کلی حسابداران"
          description="مرور سریع حسابداران فعال، دعوت‌ها و وضعیت دسترسی‌های قابل مدیریت."
          tone="primary"
        >
          <div class="accountant-summary-grid">
            <AppMetricCard label="کل روابط" :value="accountantState.relations.value.length" />
            <AppMetricCard label="فعال" :value="activeCount" tone="success" />
            <AppMetricCard label="دعوت‌ها" :value="accountantState.pendingInvitationRelations.value.length" tone="warning" />
            <AppMetricCard label="غیرفعال" :value="inactiveCount" tone="neutral" />
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          v-if="relationIdNumber"
          title="پرونده حسابدار"
          description="مشخصات، شرح وظیفه، نشست‌ها و اقدامات حساس در یک نمای یکپارچه."
        >
          <WorkspaceNotice
            v-if="!activeRelation && !isLoading"
            tone="warning"
            title="حسابدار پیدا نشد"
            message="این رابطه در لیست فعلی وجود ندارد یا هنوز همگام‌سازی نشده است."
          />
          <div v-else-if="activeRelation" class="accountant-detail-shell">
            <header class="accountant-detail-header">
              <div>
                <h2>{{ getRelationTitle(activeRelation) }}</h2>
                <p>{{ getRelationDescription(activeRelation) }}</p>
              </div>
              <div class="accountant-detail-badges">
                <AppStatusBadge :tone="getStatusTone(activeRelation.status)">
                  {{ getStatusLabel(activeRelation.status) }}
                </AppStatusBadge>
              </div>
            </header>

            <AppTabs v-model="detailTab" label="بخش‌های پرونده حسابدار" :options="detailTabOptions" />

            <div v-if="detailTab === 'profile'" class="accountant-detail-grid">
              <AppCard>
                <span class="accountant-field-label">نام نمایشی</span>
                <strong>{{ activeRelation.relation_display_name || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-field-label">شماره موبایل</span>
                <strong>{{ activeRelation.mobile_number || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-field-label">حساب کاربری</span>
                <strong>{{ activeRelation.accountant_account_name || activeRelation.global_account_name || 'در انتظار ثبت‌نام' }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-field-label">نام کاربری جهانی</span>
                <strong>@{{ activeRelation.global_account_name || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-field-label">فعال‌سازی</span>
                <strong>{{ formatDate(activeRelation.activated_at) }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-field-label">ایجاد رابطه</span>
                <strong>{{ formatDate(activeRelation.created_at) }}</strong>
              </AppCard>
            </div>

            <div v-else-if="detailTab === 'duty'" class="accountant-detail-list">
              <AppCard class="accountant-edit-form-card">
                <span class="accountant-field-label">شرح وظیفه فعلی</span>
                <strong>{{ getDutyText(activeRelation) }}</strong>
              </AppCard>

              <AppCard class="accountant-edit-form-card">
                <AppFormField label="ویرایش شرح وظیفه" hint="این توضیح برای تفکیک نقش حسابدار در فضای کاری شما استفاده می‌شود.">
                  <template #default="{ id }">
                    <AppTextarea
                      :id="id"
                      v-model="accountantState.editForm.duty_description"
                      rows="4"
                      :placeholder="activeRelation.duty_description || 'مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه'"
                    />
                  </template>
                </AppFormField>

                <WorkspaceNotice v-if="dutyError" tone="danger" title="ذخیره شرح وظیفه ناموفق بود" :message="dutyError" />
                <WorkspaceNotice v-else-if="dutyNotice" tone="success" title="شرح وظیفه ذخیره شد" :message="dutyNotice" />

                <div class="accountant-inline-actions">
                  <AppButton variant="secondary" @click="accountantState.editForm.duty_description = activeRelation.duty_description || ''">
                    بازنشانی
                  </AppButton>
                  <AppButton variant="primary" :loading="isSavingDuty" @click="saveDuty">
                    ذخیره تغییرات
                  </AppButton>
                </div>
              </AppCard>
            </div>

            <div v-else-if="detailTab === 'sessions'" class="accountant-detail-list">
              <div class="accountant-detail-toolbar">
                <strong>نشست‌های فعال حسابدار</strong>
                <AppButton size="sm" variant="secondary" :loading="detailSessionsLoading" @click="loadDetailSessions(true)">
                  نوسازی
                </AppButton>
              </div>
              <WorkspaceNotice v-if="detailSessionsError" tone="danger" title="خطا در دریافت نشست‌ها" :message="detailSessionsError" />
              <WorkspaceNotice v-else-if="activeRelation.status !== 'active' || !activeRelation.accountant_user_id" tone="info" title="نشست قابل نمایش نیست" message="نشست‌ها فقط برای حسابدار فعال نمایش داده می‌شوند." />
              <WorkspaceNotice v-else-if="detailSessionsLoading" tone="info" title="در حال دریافت نشست‌ها" message="لطفاً چند لحظه صبر کنید." />
              <WorkspaceNotice v-else-if="!detailSessions.length" tone="info" title="نشست فعالی وجود ندارد" message="برای این حسابدار نشست فعالی ثبت نشده است." />
              <template v-else>
                <AppListItem
                  v-for="session in detailSessions"
                  :key="session.id"
                  :title="session.device_name || session.platform || 'دستگاه بدون نام'"
                  :description="`${session.home_server || 'سرور نامشخص'} · آخرین فعالیت ${formatDate(session.last_active_at)}`"
                >
                  <template #leading>
                    <Clock :size="18" />
                  </template>
                  <template #trailing>
                    <div class="accountant-session-actions">
                      <AppStatusBadge :tone="session.is_primary ? 'primary' : 'neutral'">
                        {{ session.is_primary ? 'اصلی' : 'فرعی' }}
                      </AppStatusBadge>
                      <AppButton size="sm" variant="secondary" @click.stop="openConfirmDialog('terminate-session', activeRelation, session)">
                        پایان نشست
                      </AppButton>
                    </div>
                  </template>
                </AppListItem>
              </template>
            </div>

            <div v-else class="accountant-detail-list">
              <AppDangerZone
                title="اقدامات حساس حسابدار"
                :description="activeRelation.status === 'pending'
                  ? 'دعوت ثبت‌شده را لغو کنید یا قبل از فعال‌سازی آن را متوقف نگه دارید.'
                  : 'در این بخش می‌توانید دسترسی حسابدار را به‌طور کامل قطع کنید.'"
              >
                <div class="accountant-danger-card">
                  <ShieldAlert :size="22" />
                  <div>
                    <strong>{{ activeRelation.status === 'pending' ? 'لغو دعوت حسابدار' : 'قطع ارتباط حسابدار' }}</strong>
                    <p>
                      {{ activeRelation.status === 'pending'
                        ? 'لغو دعوت، لینک ثبت‌نام را بی‌اعتبار می‌کند.'
                        : 'قطع ارتباط، دسترسی حسابدار به حساب فعلی را غیرفعال می‌کند.' }}
                    </p>
                  </div>
                </div>
                <div class="accountant-inline-actions">
                  <AppButton
                    variant="danger"
                    @click="openConfirmDialog(activeRelation.status === 'pending' ? 'cancel-invitation' : 'unlink-relation', activeRelation)"
                  >
                    {{ activeRelation.status === 'pending' ? 'لغو دعوت حسابدار' : 'قطع ارتباط حسابدار' }}
                  </AppButton>
                </div>
              </AppDangerZone>
            </div>
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          title="لیست حسابداران"
          description="جستجو، فیلتر و انتخاب حسابدار با ساختار روشن و بدون accordionهای تو در تو."
        >
          <div class="accountant-list-controls">
            <AppSearchField
              v-model="searchQuery"
              label="جستجوی حسابدار"
              placeholder="نام، شماره موبایل، حساب یا شرح وظیفه را جستجو کنید."
            />
            <AppFilterChips v-model="relationFilter" label="فیلتر حسابداران" :options="relationFilterOptions" />
          </div>

          <WorkspaceNotice
            v-if="createNotice"
            tone="success"
            title="دعوت حسابدار ثبت شد"
            :message="createNotice"
          />

          <WorkspaceNotice
            v-if="error"
            tone="danger"
            title="خطا در دریافت حسابداران"
            :message="error"
          />
          <WorkspaceNotice
            v-else-if="isLoading"
            tone="info"
            title="در حال دریافت حسابداران"
            message="لطفاً چند لحظه صبر کنید."
          />
          <WorkspaceNotice
            v-else-if="!accountantState.orderedRelations.value.length"
            tone="info"
            title="هنوز حسابداری ثبت نشده است"
            message="برای شروع، از دکمه افزودن حسابدار استفاده کنید."
          />
          <WorkspaceNotice
            v-else-if="!filteredRelations.length"
            tone="info"
            title="نتیجه‌ای پیدا نشد"
            message="فیلتر یا عبارت جستجو را تغییر دهید."
          />
          <AppMasterDetail v-else class="accountant-master-detail-grid">
            <template #master>
              <div class="workspace-relation-list">
                <div v-if="visiblePendingRelations.length" class="accountant-list-group">
                  <h3>دعوت‌های در انتظار</h3>
                  <AppCard
                    v-for="relation in visiblePendingRelations"
                    :key="relation.id"
                    tone="warning"
                    class="accountant-pending-card"
                  >
                    <div class="accountant-pending-card__header">
                      <div>
                        <strong>{{ getRelationTitle(relation) }}</strong>
                        <p>{{ getRelationDescription(relation) }}</p>
                      </div>
                      <AppStatusBadge tone="warning">دعوت</AppStatusBadge>
                    </div>
                    <div class="accountant-inline-actions">
                      <AppButton
                        v-if="relation.registration_link"
                        size="sm"
                        variant="secondary"
                        @click="copyRegistrationLink(relation)"
                      >
                        <template #icon>
                          <Copy :size="16" />
                        </template>
                        {{ copiedRelationId === relation.id ? 'کپی شد' : 'کپی لینک' }}
                      </AppButton>
                      <AppButton size="sm" variant="danger" @click="openConfirmDialog('cancel-invitation', relation)">
                        لغو دعوت
                      </AppButton>
                    </div>
                  </AppCard>
                </div>

                <div v-if="visibleManageableRelations.length" class="accountant-list-group">
                  <h3>حسابداران قابل مدیریت</h3>
                  <AppListItem
                    v-for="relation in visibleManageableRelations"
                    :key="relation.id"
                    :title="getRelationTitle(relation)"
                    :description="getRelationDescription(relation)"
                    interactive
                    @select="openRelation(relation.id)"
                  >
                    <template #leading>
                      <BriefcaseBusiness :size="18" />
                    </template>
                    <template #trailing>
                      <div class="accountant-list-badges">
                        <AppStatusBadge :tone="activeRelation?.id === relation.id ? 'primary' : getStatusTone(relation.status)">
                          {{ activeRelation?.id === relation.id ? 'انتخاب‌شده' : getStatusLabel(relation.status) }}
                        </AppStatusBadge>
                      </div>
                    </template>
                  </AppListItem>
                </div>
              </div>
            </template>

            <template #detail>
              <AppEmptyState
                v-if="!activeRelation"
                title="حسابداری انتخاب نشده است"
                message="برای دیدن پرونده و تنظیمات، یکی از حسابداران فعال را از لیست انتخاب کنید."
              />
              <AppCard v-else tone="primary" class="accountant-selection-card">
                <span class="accountant-field-label">حسابدار انتخاب‌شده</span>
                <strong>{{ getRelationTitle(activeRelation) }}</strong>
                <p>{{ getRelationDescription(activeRelation) }}</p>
                <div class="accountant-inline-actions">
                  <AppButton size="sm" variant="secondary" @click="openRelation(activeRelation.id)">
                    مشاهده پرونده
                  </AppButton>
                  <AppButton
                    v-if="activeRelation.registration_link"
                    size="sm"
                    variant="secondary"
                    @click="copyRegistrationLink(activeRelation)"
                  >
                    {{ copiedRelationId === activeRelation.id ? 'کپی شد' : 'کپی لینک ثبت‌نام' }}
                  </AppButton>
                </div>
              </AppCard>
            </template>
          </AppMasterDetail>
        </WorkspaceSection>
      </template>

      <template #aside>
        <WorkspaceSection
          v-if="!isCompatibilityManagerOpen"
          title="میانبرهای حسابدار"
          description="دعوت‌های در انتظار و پرونده حسابداران از همین فضای کاری قابل مدیریت است."
        >
          <div class="workspace-side-actions">
            <AppActionCard
              title="دعوت‌های در انتظار"
              :description="`${accountantState.pendingInvitationRelations.value.length.toLocaleString('fa-IR')} دعوت در وضعیت انتظار`"
              tone="warning"
              @select="relationFilter = 'pending'"
            >
              <template #icon>
                <Clock :size="18" />
              </template>
            </AppActionCard>
            <AppActionCard
              title="افزودن حسابدار"
              description="دعوت حسابدار جدید با نام کاربری، شماره و شرح وظیفه"
              tone="primary"
              @select="openCreatePanel"
            >
              <template #icon>
                <UserPlus :size="18" />
              </template>
            </AppActionCard>
          </div>
        </WorkspaceSection>
      </template>
    </WorkspaceShell>

    <component
      :is="isMobile ? AppBottomSheet : AppResponsiveDialog"
      :open="isCreatePanelOpen"
      title="افزودن حسابدار"
      description="اطلاعات اولیه حسابدار و شرح وظیفه را ثبت کنید."
      @close="closeCreatePanel"
    >
      <div class="accountant-create-panel">
        <AppFormField label="نام کاربری جهانی" hint="این نام برای ساخت دعوت و ورود حسابدار استفاده می‌شود.">
          <template #default="{ id }">
            <AppInput :id="id" v-model="accountantState.createForm.account_name" placeholder="مثلاً accountant_01" />
          </template>
        </AppFormField>

        <AppFormField label="نام نمایشی رابطه" hint="نامی که در فضای کاری خودتان می‌بینید.">
          <template #default="{ id }">
            <AppInput :id="id" v-model="accountantState.createForm.relation_display_name" placeholder="مثلاً حسابدار فروش" />
          </template>
        </AppFormField>

        <AppFormField label="شماره موبایل" hint="برای ثبت دعوت و ساخت لینک مخصوص حسابدار استفاده می‌شود.">
          <template #default="{ id }">
            <AppInput :id="id" v-model="accountantState.createForm.mobile_number" placeholder="0912xxxxxxx" />
          </template>
        </AppFormField>

        <AppFormField label="شرح وظیفه" hint="اختیاری، برای تفکیک نقش حسابدار در گروه کاری.">
          <template #default="{ id }">
            <AppTextarea
              :id="id"
              v-model="accountantState.createForm.duty_description"
              rows="4"
              placeholder="مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه"
            />
          </template>
        </AppFormField>

        <AppCard v-if="generatedGlobalAccountName" class="accountant-generated-account">
          <span class="accountant-field-label">نام کاربری دعوتی</span>
          <strong>@{{ generatedGlobalAccountName }}</strong>
        </AppCard>

        <WorkspaceNotice v-if="createError" tone="danger" title="ثبت دعوت ناموفق بود" :message="createError" />
        <WorkspaceNotice v-else-if="createNotice" tone="success" title="دعوت ثبت شد" :message="createNotice" />
      </div>

      <template #actions>
        <AppButton variant="secondary" @click="closeCreatePanel">
          انصراف
        </AppButton>
        <AppButton variant="primary" :loading="isCreateSubmitting" @click="createRelation">
          ثبت دعوت حسابدار
        </AppButton>
      </template>
    </component>

    <AppConfirmDialog
      :open="isConfirmDialogOpen"
      :title="confirmTitle"
      :message="confirmMessage"
      :confirm-label="confirmAction === 'terminate-session' ? 'پایان نشست' : confirmAction === 'cancel-invitation' ? 'لغو دعوت' : 'قطع ارتباط'"
      :tone="confirmAction === 'terminate-session' ? 'warning' : 'danger'"
      @cancel="closeConfirmDialog"
      @confirm="handleConfirmAction"
    />
  </div>
</template>

<style scoped>
.accountant-workspace-view {
  min-height: 100%;
}

.accountant-summary-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.65rem;
}

.workspace-relation-list,
.workspace-side-actions {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.accountant-list-controls,
.accountant-detail-shell,
.accountant-detail-list {
  display: grid;
  gap: 0.85rem;
}

.accountant-master-detail-grid {
  min-width: 0;
}

.accountant-list-group {
  display: grid;
  gap: 0.55rem;
}

.accountant-list-group h3 {
  margin: 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  font-weight: 900;
}

.accountant-list-badges,
.accountant-detail-badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.35rem;
}

.accountant-detail-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 0.8rem;
  padding: 0.95rem;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-lg);
  background: linear-gradient(135deg, var(--ds-surface), var(--ds-surface-soft));
}

.accountant-detail-header h2,
.accountant-detail-header p {
  margin: 0;
}

.accountant-detail-header h2 {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 950;
}

.accountant-detail-header p {
  margin-top: 0.25rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.accountant-detail-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.65rem;
}

.accountant-field-label {
  display: block;
  margin-bottom: 0.35rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  font-weight: 800;
}

.accountant-detail-grid strong,
.accountant-detail-list strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
  word-break: break-word;
}

.accountant-detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.accountant-detail-toolbar strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
}

.accountant-danger-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.75rem;
  align-items: start;
}

.accountant-danger-card strong,
.accountant-danger-card p {
  margin: 0;
}

.accountant-danger-card strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
}

.accountant-danger-card p {
  margin-top: 0.25rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.workspace-compatibility-panel {
  min-width: 0;
}

.accountant-edit-form-card,
.accountant-create-panel,
.accountant-generated-account,
.accountant-selection-card {
  display: grid;
  gap: 0.85rem;
}

.accountant-inline-actions,
.accountant-session-actions,
.accountant-pending-card__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
}

.accountant-selection-card p,
.accountant-pending-card__header p {
  margin: 0.2rem 0 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

@media (max-width: 520px) {
  .accountant-summary-grid,
  .accountant-detail-grid {
    grid-template-columns: 1fr;
  }

  .accountant-detail-header {
    grid-template-columns: 1fr;
  }

  .accountant-detail-badges,
  .accountant-list-badges {
    justify-content: flex-start;
  }
}

@media (max-width: 767px) {
  .accountant-workspace-view :deep(.ds-workspace-main),
  .accountant-workspace-view :deep(.ds-workspace-aside) {
    padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 2.25rem);
  }
}
</style>
