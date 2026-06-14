<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BarChart3, Clock, Copy, ReceiptText, ShieldAlert, UserPlus, Users } from 'lucide-vue-next'
import OwnerCustomerManagerModal from '../components/OwnerCustomerManagerModal.vue'
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
  AppNumberStepper,
  AppResponsiveDialog,
  AppSearchField,
  AppSelect,
  AppStatusBadge,
  AppTabs,
} from '../components/ui'
import {
  buildCustomerDetailUpdatePayload,
  buildCustomerPayload,
  createOwnerCustomerRelation,
  deleteOwnerCustomerRelation,
  fetchOwnerCustomerRelations,
  fetchOwnerCustomerSessions,
  fetchOwnerCustomerTradeStats,
  fetchOwnerCustomerTrades,
  normalizeCommissionRate,
  normalizeLatinDigits,
  terminateOwnerCustomerSession,
  updateOwnerCustomerRelation,
  useOwnerCustomers,
  type CustomerRelation,
  type CustomerSessionSummary,
  type CustomerTradeStats,
  type CustomerTradeSummary,
} from '../composables/useOwnerCustomers'

const route = useRoute()
const router = useRouter()
const customerState = useOwnerCustomers()
const isLoading = ref(true)
const isMobile = ref(false)
const error = ref('')
const isCompatibilityManagerOpen = ref(false)
const compatibilityPanel = ref<string | null>(null)
const isCreatePanelOpen = ref(false)
const isCreateSubmitting = ref(false)
const createError = ref('')
const createNotice = ref('')
const isSavingLimits = ref(false)
const limitsError = ref('')
const limitsNotice = ref('')
const copiedRelationId = ref<number | null>(null)
const isConfirmDialogOpen = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmAction = ref<'terminate-session' | 'cancel-invitation' | 'unlink-relation' | null>(null)
const confirmRelation = ref<CustomerRelation | null>(null)
const confirmSession = ref<CustomerSessionSummary | null>(null)
const searchQuery = ref('')
const relationFilter = ref('all')
const detailTrades = ref<CustomerTradeSummary[]>([])
const detailStats = ref<CustomerTradeStats | null>(null)
const detailSessions = ref<CustomerSessionSummary[]>([])
const detailTradesLoading = ref(false)
const detailStatsLoading = ref(false)
const detailSessionsLoading = ref(false)
const detailTradesError = ref('')
const detailStatsError = ref('')
const detailSessionsError = ref('')
const statsPeriodDays = ref(7)

const relationFilterOptions = [
  { key: 'all', label: 'همه' },
  { key: 'active', label: 'فعال' },
  { key: 'pending', label: 'دعوت‌ها' },
  { key: 'tier2', label: 'سطح ۲' },
  { key: 'inactive', label: 'غیرفعال' },
]

const detailTabOptions = [
  { key: 'profile', label: 'مشخصات' },
  { key: 'limits', label: 'محدودیت‌ها' },
  { key: 'trades', label: 'معاملات' },
  { key: 'stats', label: 'آمار' },
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
  return customerState.relations.value.find((relation) => relation.id === id) ?? null
})

const activeCount = computed(() => customerState.relations.value.filter((relation) => relation.status === 'active').length)
const tier2Count = computed(() => customerState.relations.value.filter((relation) => relation.customer_tier === 'tier2').length)
const inactiveCount = computed(() => customerState.relations.value.filter((relation) => relation.status !== 'active' && relation.status !== 'pending').length)

const detailTab = computed({
  get() {
    const value = route.query.tab
    const normalized = Array.isArray(value) ? value[0] : value
    return detailTabOptions.some((option) => option.key === normalized) ? normalized as string : 'profile'
  },
  set(tab: string) {
    if (!detailTabOptions.some((option) => option.key === tab)) return
    router.push({
      name: relationId.value ? 'operations-customers-detail' : 'operations-customers',
      params: relationId.value ? { relationId: String(relationId.value) } : {},
      query: { ...route.query, tab },
    })
  },
})

const filteredRelations = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase('fa-IR')
  return customerState.orderedRelations.value.filter((relation) => {
    const filter = relationFilter.value
    if (filter === 'active' && relation.status !== 'active') return false
    if (filter === 'pending' && relation.status !== 'pending') return false
    if (filter === 'tier2' && relation.customer_tier !== 'tier2') return false
    if (filter === 'inactive' && (relation.status === 'active' || relation.status === 'pending')) return false
    if (!query) return true
    const haystack = [
      relation.management_name,
      relation.customer_account_name,
      relation.invitation_account_name,
      relation.mobile_number,
      relation.customer_tier,
      relation.status,
    ].filter(Boolean).join(' ').toLocaleLowerCase('fa-IR')
    return haystack.includes(query)
  })
})

const visiblePendingRelations = computed(() => filteredRelations.value.filter((relation) => relation.status === 'pending'))
const visibleManageableRelations = computed(() => filteredRelations.value.filter((relation) => relation.status !== 'pending'))

const activeRelationLimits = computed(() => {
  const relation = activeRelation.value
  if (!relation) return []
  return [
    { label: 'حداقل مقدار معامله', value: formatMaybeNumber(relation.min_trade_quantity), description: 'کمترین حجمی که مشتری مجاز به معامله است.' },
    { label: 'حداکثر مقدار معامله', value: formatMaybeNumber(relation.max_trade_quantity), description: 'بیشترین حجم مجاز برای هر معامله.' },
    { label: 'حداکثر تعداد روزانه', value: formatMaybeNumber(relation.max_daily_trades), description: 'سقف تعداد معاملات مشتری در یک روز.' },
    { label: 'حداکثر حجم روزانه', value: formatMaybeNumber(relation.max_daily_commodity_volume), description: 'سقف مجموع حجم کالایی در روز.' },
  ]
})

const createCommissionRate = computed({
  get: () => normalizeCommissionRate(customerState.createForm.commission_rate),
  set: (value: number) => {
    customerState.createForm.commission_rate = normalizeCommissionRate(value).toFixed(2)
  },
})

const detailCommissionRate = computed({
  get: () => {
    const seeded = customerState.detailEditForm.commission_rate || activeRelation.value?.commission_rate || 0.5
    return normalizeCommissionRate(seeded)
  },
  set: (value: number) => {
    customerState.detailEditForm.commission_rate = normalizeCommissionRate(value).toFixed(2)
  },
})

const createCommissionPreview = computed(() => {
  const amount = 100_000_000 * createCommissionRate.value / 100
  return formatToman(amount)
})

const detailCommissionPreview = computed(() => {
  const amount = 100_000_000 * detailCommissionRate.value / 100
  return formatToman(amount)
})

const generatedCreateAccountName = computed(() => {
  const mobileDigits = normalizeLatinDigits(customerState.createForm.mobile_number).replace(/\D/g, '')
  return mobileDigits ? `customer_${mobileDigits}` : ''
})

async function loadRelations() {
  isLoading.value = true
  error.value = ''
  try {
    customerState.relations.value = await fetchOwnerCustomerRelations()
  } catch (err: any) {
    error.value = err?.message || 'دریافت لیست مشتریان ناموفق بود.'
  } finally {
    isLoading.value = false
  }
}

function goToOperations() {
  router.push({ name: 'operations' })
}

function openRelation(relationId: number) {
  router.push({
    name: 'operations-customers-detail',
    params: { relationId: String(relationId) },
    query: route.query,
  })
}

function backToList() {
  router.push({
    name: 'operations-customers',
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

function handleCreateTierChange() {
  if (customerState.createForm.customer_tier === 'tier2') {
    customerState.createForm.commission_rate = customerState.createForm.commission_rate || '0.50'
  } else {
    customerState.createForm.commission_rate = '0.50'
  }
}

function resetCreateForm() {
  Object.assign(customerState.createForm, {
    management_name: '',
    mobile_number: '',
    customer_tier: 'tier1',
    commission_rate: '0.50',
    min_trade_quantity: '',
    max_trade_quantity: '',
    max_daily_trades: '',
    max_daily_commodity_volume: '',
  })
}

function seedDetailEditForm(relation: CustomerRelation | null, options: { resetFeedback?: boolean } = {}) {
  const { resetFeedback = true } = options
  if (!relation) {
    Object.assign(customerState.detailEditForm, {
      customer_tier: '',
      commission_rate: '',
      min_trade_quantity: '',
      max_trade_quantity: '',
      max_daily_trades: '',
      max_daily_commodity_volume: '',
    })
    if (resetFeedback) {
      limitsError.value = ''
      limitsNotice.value = ''
    }
    return
  }

  Object.assign(customerState.detailEditForm, {
    customer_tier: relation.customer_tier,
    commission_rate: relation.commission_rate == null ? '0.50' : String(relation.commission_rate),
    min_trade_quantity: relation.min_trade_quantity == null ? '' : String(relation.min_trade_quantity),
    max_trade_quantity: relation.max_trade_quantity == null ? '' : String(relation.max_trade_quantity),
    max_daily_trades: relation.max_daily_trades == null ? '' : String(relation.max_daily_trades),
    max_daily_commodity_volume: relation.max_daily_commodity_volume == null ? '' : String(relation.max_daily_commodity_volume),
  })
  if (resetFeedback) {
    limitsError.value = ''
    limitsNotice.value = ''
  }
}

async function loadDetailTrades(force = false) {
  const relation = activeRelation.value
  if (!relation?.customer_user_id) {
    detailTrades.value = []
    return
  }
  if (!force && detailTrades.value.length) return
  detailTradesLoading.value = true
  detailTradesError.value = ''
  try {
    detailTrades.value = await fetchOwnerCustomerTrades(relation.customer_user_id, { limit: 20 })
  } catch (err: any) {
    detailTradesError.value = err?.message || 'دریافت معاملات مشتری ناموفق بود.'
  } finally {
    detailTradesLoading.value = false
  }
}

async function loadDetailStats(force = false) {
  const relation = activeRelation.value
  if (!relation) {
    detailStats.value = null
    return
  }
  if (!force && detailStats.value?.relation_id === relation.id && detailStats.value.period_days === statsPeriodDays.value) return
  detailStatsLoading.value = true
  detailStatsError.value = ''
  try {
    detailStats.value = await fetchOwnerCustomerTradeStats(relation.id, statsPeriodDays.value)
  } catch (err: any) {
    detailStatsError.value = err?.message || 'دریافت آمار مشتری ناموفق بود.'
  } finally {
    detailStatsLoading.value = false
  }
}

async function loadDetailSessions(force = false) {
  const relation = activeRelation.value
  if (!relation || relation.status !== 'active' || !relation.customer_user_id) {
    detailSessions.value = []
    return
  }
  if (!force && detailSessions.value.length) return
  detailSessionsLoading.value = true
  detailSessionsError.value = ''
  try {
    detailSessions.value = await fetchOwnerCustomerSessions(relation.id)
  } catch (err: any) {
    detailSessionsError.value = err?.message || 'دریافت نشست‌های مشتری ناموفق بود.'
  } finally {
    detailSessionsLoading.value = false
  }
}

function refreshCurrentDetailTab() {
  if (detailTab.value === 'trades') void loadDetailTrades(true)
  if (detailTab.value === 'stats') void loadDetailStats(true)
  if (detailTab.value === 'sessions') void loadDetailSessions(true)
}

function setStatsPeriod(days: number) {
  statsPeriodDays.value = days
}

async function createRelation() {
  if (isCreateSubmitting.value) return
  isCreateSubmitting.value = true
  createError.value = ''
  createNotice.value = ''
  try {
    const created = await createOwnerCustomerRelation({
      account_name: generatedCreateAccountName.value,
      management_name: customerState.createForm.management_name,
      mobile_number: customerState.createForm.mobile_number,
      ...buildCustomerPayload(customerState.createForm),
    })
    customerState.relations.value = [created, ...customerState.relations.value.filter((item) => item.id !== created.id)]
    createNotice.value = 'دعوت مشتری با موفقیت ثبت شد.'
    resetCreateForm()
    closeCreatePanel()
  } catch (err: any) {
    createError.value = err?.message || 'ایجاد مشتری ناموفق بود.'
  } finally {
    isCreateSubmitting.value = false
  }
}

async function saveDetailLimits() {
  const relation = activeRelation.value
  if (!relation || isSavingLimits.value) return
  const payload = buildCustomerDetailUpdatePayload(relation, customerState.detailEditForm)
  if (!Object.keys(payload).length) {
    limitsNotice.value = 'تغییری برای ذخیره انتخاب نشده است.'
    return
  }
  isSavingLimits.value = true
  limitsError.value = ''
  limitsNotice.value = ''
  try {
    const updated = await updateOwnerCustomerRelation(relation.id, payload)
    customerState.relations.value = customerState.relations.value.map((item) => (item.id === updated.id ? updated : item))
    seedDetailEditForm(updated)
    limitsNotice.value = 'تنظیمات مشتری ذخیره شد.'
  } catch (err: any) {
    limitsError.value = err?.message || 'ذخیره تنظیمات مشتری ناموفق بود.'
  } finally {
    isSavingLimits.value = false
  }
}

async function copyRegistrationLink(relation: CustomerRelation) {
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
    limitsError.value = 'کپی لینک ثبت‌نام ممکن نشد.'
  }
}

function openConfirmDialog(
  kind: 'terminate-session' | 'cancel-invitation' | 'unlink-relation',
  relation: CustomerRelation,
  session: CustomerSessionSummary | null = null,
) {
  confirmAction.value = kind
  confirmRelation.value = relation
  confirmSession.value = session
  confirmTitle.value = kind === 'terminate-session'
    ? 'پایان نشست'
    : kind === 'cancel-invitation'
      ? 'لغو دعوت مشتری'
      : 'قطع ارتباط مشتری'
  confirmMessage.value = kind === 'terminate-session'
    ? `نشست «${session?.device_name || 'دستگاه مشتری'}» پایان یابد؟`
    : kind === 'cancel-invitation'
      ? `دعوت «${relation.management_name}» لغو شود؟`
      : `ارتباط «${relation.management_name}» قطع شود؟ این عملیات دسترسی مشتری را غیرفعال می‌کند.`
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
      await terminateOwnerCustomerSession(relation.id, session.id)
      await loadDetailSessions(true)
    } catch (err: any) {
      detailSessionsError.value = err?.message || 'پایان دادن نشست مشتری ناموفق بود.'
    }
    return
  }

  try {
    await deleteOwnerCustomerRelation(
      relation.id,
      action === 'cancel-invitation' ? 'لغو دعوت مشتری ناموفق بود.' : 'قطع ارتباط مشتری ناموفق بود.',
    )
    customerState.relations.value = customerState.relations.value.filter((item) => item.id !== relation.id)
    if (activeRelation.value?.id === relation.id) {
      backToList()
    }
  } catch (err: any) {
    detailSessionsError.value = err?.message
      || (action === 'cancel-invitation' ? 'لغو دعوت مشتری ناموفق بود.' : 'قطع ارتباط مشتری ناموفق بود.')
  }
}

function getRelationTitle(relation: CustomerRelation) {
  return relation.management_name || relation.customer_account_name || relation.invitation_account_name || 'مشتری'
}

function getRelationDescription(relation: CustomerRelation) {
  const mobile = relation.mobile_number || 'بدون شماره'
  const tier = relation.customer_tier === 'tier2' ? 'سطح ۲' : 'سطح ۱'
  return `${tier} - ${mobile}`
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

function getTierLabel(tier: string) {
  return tier === 'tier2' ? 'سطح ۲' : 'سطح ۱'
}

function formatMaybeNumber(value: number | null | undefined) {
  if (value == null) return 'بدون محدودیت'
  return Number(value).toLocaleString('fa-IR')
}

function formatPercent(value: number | null | undefined) {
  if (value == null) return 'ثبت نشده'
  return `${Number(value).toLocaleString('fa-IR', { maximumFractionDigits: 2 })}٪`
}

function formatToman(value: number | null | undefined) {
  if (!value) return '۰ تومان'
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toLocaleString('fa-IR', { maximumFractionDigits: 2 })} میلیون تومان`
  }
  if (Math.abs(value) >= 1_000) {
    return `${(value / 1_000).toLocaleString('fa-IR', { maximumFractionDigits: 2 })} هزار تومان`
  }
  return `${Number(value).toLocaleString('fa-IR')} تومان`
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

watch(initialPanel, (panel) => {
  if (panel === 'create') {
    openCreatePanel()
    return
  }
  if (panel === 'manage') {
    openCompatibilityManager(panel)
  }
}, { immediate: true })

watch([activeRelation, detailTab], () => {
  detailTrades.value = []
  detailStats.value = null
  detailSessions.value = []
  detailTradesError.value = ''
  detailStatsError.value = ''
  detailSessionsError.value = ''
  refreshCurrentDetailTab()
}, { flush: 'post' })

watch(activeRelation, (relation, previousRelation) => {
  seedDetailEditForm(relation, {
    resetFeedback: relation?.id !== previousRelation?.id,
  })
}, { immediate: true })

watch(statsPeriodDays, () => {
  if (detailTab.value === 'stats') {
    void loadDetailStats(true)
  }
})

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
  <div class="ds-page customer-workspace-view">
    <WorkspaceShell
      title="مشتریان"
      eyebrow="عملیات"
      description="افزودن، مرور و تنظیم روابط مشتریان در یک فضای کاری یکپارچه."
      layout="split"
      show-back
      back-label="بازگشت"
      @back="handleBack"
    >
      <template #actions>
        <AppButton variant="secondary" class="customer-workspace-action" @click="goToOperations">
          عملیات
        </AppButton>
        <AppButton variant="primary" class="customer-workspace-create" @click="openCreatePanel">
          <template #icon>
            <UserPlus :size="16" />
          </template>
          افزودن مشتری
        </AppButton>
      </template>

      <section v-if="isCompatibilityManagerOpen" class="workspace-compatibility-panel">
        <OwnerCustomerManagerModal
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
          title="نمای کلی مشتریان"
          description="مرور سریع روابط، دعوت‌ها و مشتریان سطح ۲ بدون ورود به فرم‌های مدیریتی."
          tone="primary"
        >
          <div class="customer-summary-grid">
            <AppMetricCard label="کل روابط" :value="customerState.relations.value.length" />
            <AppMetricCard label="فعال" :value="activeCount" tone="success" />
            <AppMetricCard label="دعوت‌ها" :value="customerState.pendingInvitationRelations.value.length" tone="warning" />
            <AppMetricCard label="سطح ۲" :value="tier2Count" tone="primary" />
            <AppMetricCard label="غیرفعال" :value="inactiveCount" tone="neutral" />
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          v-if="relationIdNumber"
          title="پرونده مشتری"
          description="مشخصات، محدودیت‌ها، معاملات، آمار، نشست‌ها و اقدامات حساس در یک نمای یکپارچه."
        >
          <WorkspaceNotice
            v-if="!activeRelation && !isLoading"
            tone="warning"
            title="مشتری پیدا نشد"
            message="این رابطه در لیست فعلی وجود ندارد یا هنوز همگام‌سازی نشده است."
          />
          <div v-else-if="activeRelation" class="customer-detail-shell">
            <header class="customer-detail-header">
              <div>
                <h2>{{ getRelationTitle(activeRelation) }}</h2>
                <p>{{ getRelationDescription(activeRelation) }}</p>
              </div>
              <div class="customer-detail-badges">
                <AppStatusBadge :tone="getStatusTone(activeRelation.status)">
                  {{ getStatusLabel(activeRelation.status) }}
                </AppStatusBadge>
                <AppStatusBadge :tone="activeRelation.customer_tier === 'tier2' ? 'primary' : 'neutral'">
                  {{ getTierLabel(activeRelation.customer_tier) }}
                </AppStatusBadge>
              </div>
            </header>

            <AppTabs v-model="detailTab" label="بخش‌های پرونده مشتری" :options="detailTabOptions" />

            <div v-if="detailTab === 'profile'" class="customer-detail-grid">
              <AppCard>
                <span class="customer-field-label">نام مدیریتی</span>
                <strong>{{ activeRelation.management_name || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-field-label">شماره موبایل</span>
                <strong>{{ activeRelation.mobile_number || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-field-label">حساب کاربری</span>
                <strong>{{ activeRelation.customer_account_name || activeRelation.invitation_account_name || 'در انتظار ثبت‌نام' }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-field-label">نرخ کمیسیون</span>
                <strong>{{ activeRelation.customer_tier === 'tier2' ? formatPercent(activeRelation.commission_rate) : 'ندارد' }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-field-label">فعال‌سازی</span>
                <strong>{{ formatDate(activeRelation.activated_at) }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-field-label">ایجاد رابطه</span>
                <strong>{{ formatDate(activeRelation.created_at) }}</strong>
              </AppCard>
            </div>

            <div v-else-if="detailTab === 'limits'" class="customer-detail-list">
              <div class="customer-detail-grid">
                <AppListItem
                  v-for="item in activeRelationLimits"
                  :key="item.label"
                  :title="item.label"
                  :description="item.description"
                  :meta="item.value"
                />
              </div>

              <AppCard class="customer-edit-form-card">
                <div class="customer-edit-form-grid">
                  <AppFormField label="سطح مشتری" hint="سطح مشتری، رفتار کمیسیون را تعیین می‌کند.">
                    <template #default="{ id }">
                      <AppSelect
                        :id="id"
                        v-model="customerState.detailEditForm.customer_tier"
                        :options="[
                          { value: 'tier1', label: 'سطح ۱' },
                          { value: 'tier2', label: 'سطح ۲' },
                        ]"
                      />
                    </template>
                  </AppFormField>

                  <AppFormField
                    v-if="customerState.detailEditForm.customer_tier === 'tier2'"
                    label="نرخ کمیسیون"
                    :hint="`به ازای هر ۱۰۰ میلیون: ${detailCommissionPreview}`"
                  >
                    <template #default>
                      <AppNumberStepper
                        v-model="detailCommissionRate"
                        label="درصد کمیسیون مشتری"
                        :min="0"
                        :max="100"
                        :step="0.01"
                      />
                    </template>
                  </AppFormField>

                  <AppFormField label="حداقل مقدار معامله" hint="خالی بماند یعنی بدون محدودیت.">
                    <template #default="{ id }">
                      <AppInput :id="id" v-model="customerState.detailEditForm.min_trade_quantity" placeholder="مثلاً ۱۰" />
                    </template>
                  </AppFormField>

                  <AppFormField label="حداکثر مقدار معامله" hint="خالی بماند یعنی بدون محدودیت.">
                    <template #default="{ id }">
                      <AppInput :id="id" v-model="customerState.detailEditForm.max_trade_quantity" placeholder="مثلاً ۵۰۰" />
                    </template>
                  </AppFormField>

                  <AppFormField label="حداکثر تعداد روزانه" hint="خالی بماند یعنی بدون محدودیت.">
                    <template #default="{ id }">
                      <AppInput :id="id" v-model="customerState.detailEditForm.max_daily_trades" placeholder="مثلاً ۴" />
                    </template>
                  </AppFormField>

                  <AppFormField label="حداکثر حجم روزانه" hint="خالی بماند یعنی بدون محدودیت.">
                    <template #default="{ id }">
                      <AppInput :id="id" v-model="customerState.detailEditForm.max_daily_commodity_volume" placeholder="مثلاً ۱۰۰۰" />
                    </template>
                  </AppFormField>
                </div>

                <WorkspaceNotice v-if="limitsError" tone="danger" title="ذخیره تنظیمات ناموفق بود" :message="limitsError" />
                <WorkspaceNotice v-else-if="limitsNotice" tone="success" title="تغییرات ذخیره شد" :message="limitsNotice" />

                <div class="customer-inline-actions">
                  <AppButton variant="primary" :loading="isSavingLimits" @click="saveDetailLimits">
                    ذخیره تغییرات
                  </AppButton>
                </div>
              </AppCard>
            </div>

            <div v-else-if="detailTab === 'trades'" class="customer-detail-list">
              <div class="customer-detail-toolbar">
                <strong>آخرین معاملات</strong>
                <AppButton size="sm" variant="secondary" :loading="detailTradesLoading" @click="loadDetailTrades(true)">
                  نوسازی
                </AppButton>
              </div>
              <WorkspaceNotice v-if="detailTradesError" tone="danger" title="خطا در دریافت معاملات" :message="detailTradesError" />
              <WorkspaceNotice v-else-if="detailTradesLoading" tone="info" title="در حال دریافت معاملات" message="لطفاً چند لحظه صبر کنید." />
              <WorkspaceNotice v-else-if="!detailTrades.length" tone="info" title="معامله‌ای ثبت نشده است" message="برای این مشتری هنوز معامله‌ای در بازه اخیر پیدا نشد." />
              <template v-else>
                <AppListItem
                  v-for="trade in detailTrades"
                  :key="trade.id"
                  :title="`${trade.commodity_name} - ${trade.trade_type}`"
                  :description="`${trade.counterparty_name || 'طرف مقابل نامشخص'} · ${formatDate(trade.created_at)}`"
                  :meta="`${Number(trade.quantity).toLocaleString('fa-IR')} × ${Number(trade.price).toLocaleString('fa-IR')}`"
                >
                  <template #leading>
                    <ReceiptText :size="18" />
                  </template>
                </AppListItem>
              </template>
            </div>

            <div v-else-if="detailTab === 'stats'" class="customer-detail-list">
              <div class="customer-period-tabs" aria-label="بازه گزارش مشتری">
                <button
                  v-for="days in [1, 3, 7, 30, 90, 180]"
                  :key="days"
                  type="button"
                  :class="{ 'is-active': statsPeriodDays === days }"
                  @click="setStatsPeriod(days)"
                >
                  {{ days.toLocaleString('fa-IR') }} روز
                </button>
              </div>
              <WorkspaceNotice v-if="detailStatsError" tone="danger" title="خطا در دریافت آمار" :message="detailStatsError" />
              <WorkspaceNotice v-else-if="detailStatsLoading" tone="info" title="در حال محاسبه آمار" message="لطفاً چند لحظه صبر کنید." />
              <div v-else-if="detailStats" class="customer-stats-grid">
                <AppMetricCard label="تعداد معاملات" :value="detailStats.trade_count" />
                <AppMetricCard label="حجم کل" :value="detailStats.total_quantity" tone="primary" />
                <AppMetricCard label="سود کمیسیون" :value="formatToman(detailStats.commission_profit_toman)" tone="success" />
                <AppCard class="customer-stats-commodities">
                  <span class="customer-field-label">تفکیک کالا</span>
                  <ul>
                    <li v-for="commodity in detailStats.commodities" :key="commodity.commodity_id">
                      <span>{{ commodity.commodity_name }}</span>
                      <strong>{{ Number(commodity.total_quantity).toLocaleString('fa-IR') }}</strong>
                    </li>
                  </ul>
                </AppCard>
              </div>
              <WorkspaceNotice v-else tone="info" title="آماری در دسترس نیست" message="برای این مشتری هنوز گزارش قابل نمایش وجود ندارد." />
            </div>

            <div v-else-if="detailTab === 'sessions'" class="customer-detail-list">
              <div class="customer-detail-toolbar">
                <strong>نشست‌های فعال مشتری</strong>
                <AppButton size="sm" variant="secondary" :loading="detailSessionsLoading" @click="loadDetailSessions(true)">
                  نوسازی
                </AppButton>
              </div>
              <WorkspaceNotice v-if="detailSessionsError" tone="danger" title="خطا در دریافت نشست‌ها" :message="detailSessionsError" />
              <WorkspaceNotice v-else-if="activeRelation.status !== 'active' || !activeRelation.customer_user_id" tone="info" title="نشست قابل نمایش نیست" message="نشست‌ها فقط برای مشتری فعال نمایش داده می‌شوند." />
              <WorkspaceNotice v-else-if="detailSessionsLoading" tone="info" title="در حال دریافت نشست‌ها" message="لطفاً چند لحظه صبر کنید." />
              <WorkspaceNotice v-else-if="!detailSessions.length" tone="info" title="نشست فعالی وجود ندارد" message="برای این مشتری نشست فعالی ثبت نشده است." />
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
                    <div class="customer-session-actions">
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

            <div v-else class="customer-detail-list">
              <AppDangerZone
                title="اقدامات حساس مشتری"
                :description="activeRelation.status === 'pending'
                  ? 'دعوت ثبت‌شده را لغو کنید یا ابتدا مشتری را فعال نگه دارید.'
                  : 'در این بخش می‌توانید دسترسی مشتری را به‌طور کامل قطع کنید.'"
              >
                <div class="customer-danger-card">
                  <ShieldAlert :size="22" />
                  <div>
                    <strong>{{ activeRelation.status === 'pending' ? 'لغو دعوت مشتری' : 'قطع ارتباط مشتری' }}</strong>
                    <p>
                      {{ activeRelation.status === 'pending'
                        ? 'لغو دعوت، لینک ثبت‌نام را بی‌اعتبار می‌کند.'
                        : 'قطع ارتباط، دسترسی مشتری به حساب فعلی را غیرفعال می‌کند.' }}
                    </p>
                  </div>
                </div>
                <div class="customer-inline-actions">
                  <AppButton
                    variant="danger"
                    @click="openConfirmDialog(activeRelation.status === 'pending' ? 'cancel-invitation' : 'unlink-relation', activeRelation)"
                  >
                    {{ activeRelation.status === 'pending' ? 'لغو دعوت مشتری' : 'قطع ارتباط مشتری' }}
                  </AppButton>
                </div>
              </AppDangerZone>
            </div>
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          title="لیست مشتریان"
          description="جستجو، فیلتر و انتخاب مشتری با ساختار روشن و بدون accordion تو در تو."
        >
          <div class="customer-list-controls">
            <AppSearchField
              v-model="searchQuery"
              label="جستجوی مشتری"
              placeholder="نام، شماره موبایل یا نام حساب را جستجو کنید."
            />
            <AppFilterChips v-model="relationFilter" label="فیلتر مشتریان" :options="relationFilterOptions" />
          </div>

          <WorkspaceNotice
            v-if="createNotice"
            tone="success"
            title="دعوت مشتری ثبت شد"
            :message="createNotice"
          />

          <WorkspaceNotice
            v-if="error"
            tone="danger"
            title="خطا در دریافت مشتریان"
            :message="error"
          />
          <WorkspaceNotice
            v-else-if="isLoading"
            tone="info"
            title="در حال دریافت مشتریان"
            message="لطفاً چند لحظه صبر کنید."
          />
          <WorkspaceNotice
            v-else-if="!customerState.orderedRelations.value.length"
            tone="info"
            title="هنوز مشتری ثبت نشده است"
            message="برای شروع، از دکمه افزودن مشتری استفاده کنید."
          />
          <WorkspaceNotice
            v-else-if="!filteredRelations.length"
            tone="info"
            title="نتیجه‌ای پیدا نشد"
            message="فیلتر یا عبارت جستجو را تغییر دهید."
          />
          <AppMasterDetail class="customer-master-detail-grid">
            <template #master>
              <div class="workspace-relation-list">
                <div v-if="visiblePendingRelations.length" class="customer-list-group">
                  <h3>دعوت‌های در انتظار</h3>
                  <AppCard
                    v-for="relation in visiblePendingRelations"
                    :key="relation.id"
                    tone="warning"
                    class="customer-pending-card"
                  >
                    <div class="customer-pending-card__header">
                      <div>
                        <strong>{{ getRelationTitle(relation) }}</strong>
                        <p>{{ getRelationDescription(relation) }}</p>
                      </div>
                      <AppStatusBadge tone="warning">دعوت</AppStatusBadge>
                    </div>
                    <div class="customer-inline-actions">
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

                <div v-if="visibleManageableRelations.length" class="customer-list-group">
                  <h3>مشتریان قابل مدیریت</h3>
                  <AppListItem
                    v-for="relation in visibleManageableRelations"
                    :key="relation.id"
                    :title="getRelationTitle(relation)"
                    :description="getRelationDescription(relation)"
                    interactive
                    @select="openRelation(relation.id)"
                  >
                    <template #leading>
                      <Users :size="18" />
                    </template>
                    <template #trailing>
                      <div class="customer-list-badges">
                        <AppStatusBadge :tone="activeRelation?.id === relation.id ? 'primary' : getStatusTone(relation.status)">
                          {{ activeRelation?.id === relation.id ? 'انتخاب‌شده' : getStatusLabel(relation.status) }}
                        </AppStatusBadge>
                        <AppStatusBadge v-if="relation.customer_tier === 'tier2'" tone="primary">
                          سطح ۲
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
                title="مشتری انتخاب نشده است"
                message="برای دیدن پرونده و تنظیمات، یکی از مشتریان فعال را از لیست انتخاب کنید."
              />
              <AppCard v-else tone="primary" class="customer-selection-card">
                <span class="customer-field-label">مشتری انتخاب‌شده</span>
                <strong>{{ getRelationTitle(activeRelation) }}</strong>
                <p>{{ getRelationDescription(activeRelation) }}</p>
                <div class="customer-inline-actions">
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
          title="میانبرهای مشتری"
          description="دعوت‌های در انتظار و پرونده مشتریان از همین فضای کاری قابل مدیریت است."
        >
          <div class="workspace-side-actions">
            <AppActionCard
              title="دعوت‌های در انتظار"
              :description="`${customerState.pendingInvitationRelations.value.length.toLocaleString('fa-IR')} دعوت در وضعیت انتظار`"
              tone="warning"
              @select="relationFilter = 'pending'"
            >
              <template #icon>
                <Clock :size="18" />
              </template>
            </AppActionCard>
            <AppActionCard
              title="افزودن مشتری"
              description="دعوت مشتری جدید با سطح، کمیسیون و محدودیت‌های اولیه"
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
      title="افزودن مشتری"
      description="اطلاعات اولیه مشتری و محدودیت‌های پایه را ثبت کنید."
      @close="closeCreatePanel"
    >
      <div class="customer-create-panel">
        <AppFormField label="نام مدیریتی" hint="نامی که در فضای کاری خودتان می‌بینید.">
          <template #default="{ id }">
            <AppInput :id="id" v-model="customerState.createForm.management_name" placeholder="مثلاً حسن رضایی" />
          </template>
        </AppFormField>

        <AppFormField label="شماره موبایل" hint="برای ساخت حساب دعوتی و ثبت لینک استفاده می‌شود.">
          <template #default="{ id }">
            <AppInput :id="id" v-model="customerState.createForm.mobile_number" placeholder="0912xxxxxxx" />
          </template>
        </AppFormField>

        <AppFormField label="سطح مشتری">
          <template #default="{ id }">
            <AppSelect
              :id="id"
              v-model="customerState.createForm.customer_tier"
              :options="[
                { value: 'tier1', label: 'سطح ۱' },
                { value: 'tier2', label: 'سطح ۲' },
              ]"
              @update:model-value="handleCreateTierChange"
            />
          </template>
        </AppFormField>

        <AppFormField
          v-if="customerState.createForm.customer_tier === 'tier2'"
          label="نرخ کمیسیون"
          :hint="`به ازای هر ۱۰۰ میلیون: ${createCommissionPreview}`"
        >
          <template #default>
            <AppNumberStepper
              v-model="createCommissionRate"
              label="درصد کمیسیون مشتری"
              :min="0"
              :max="100"
              :step="0.01"
            />
          </template>
        </AppFormField>

        <div class="customer-edit-form-grid">
          <AppFormField label="حداقل مقدار معامله" hint="خالی بماند یعنی بدون محدودیت.">
            <template #default="{ id }">
              <AppInput :id="id" v-model="customerState.createForm.min_trade_quantity" placeholder="اختیاری" />
            </template>
          </AppFormField>
          <AppFormField label="حداکثر مقدار معامله" hint="خالی بماند یعنی بدون محدودیت.">
            <template #default="{ id }">
              <AppInput :id="id" v-model="customerState.createForm.max_trade_quantity" placeholder="اختیاری" />
            </template>
          </AppFormField>
          <AppFormField label="حداکثر تعداد روزانه" hint="خالی بماند یعنی بدون محدودیت.">
            <template #default="{ id }">
              <AppInput :id="id" v-model="customerState.createForm.max_daily_trades" placeholder="اختیاری" />
            </template>
          </AppFormField>
          <AppFormField label="حداکثر حجم روزانه" hint="خالی بماند یعنی بدون محدودیت.">
            <template #default="{ id }">
              <AppInput :id="id" v-model="customerState.createForm.max_daily_commodity_volume" placeholder="اختیاری" />
            </template>
          </AppFormField>
        </div>

        <AppCard v-if="generatedCreateAccountName" class="customer-generated-account">
          <span class="customer-field-label">نام حساب دعوتی</span>
          <strong>@{{ generatedCreateAccountName }}</strong>
        </AppCard>

        <WorkspaceNotice v-if="createError" tone="danger" title="ثبت دعوت ناموفق بود" :message="createError" />
        <WorkspaceNotice v-else-if="createNotice" tone="success" title="دعوت ثبت شد" :message="createNotice" />
      </div>

      <template #actions>
        <AppButton variant="secondary" @click="closeCreatePanel">
          انصراف
        </AppButton>
        <AppButton variant="primary" :loading="isCreateSubmitting" @click="createRelation">
          ثبت دعوت مشتری
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
.customer-workspace-view {
  min-height: 100%;
}

.customer-summary-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.65rem;
}

.workspace-relation-list,
.workspace-side-actions {
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
}

.customer-list-controls,
.customer-detail-shell,
.customer-detail-list {
  display: grid;
  gap: 0.85rem;
}

.customer-master-detail-grid {
  min-width: 0;
}

.customer-list-group {
  display: grid;
  gap: 0.55rem;
}

.customer-list-group h3 {
  margin: 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  font-weight: 900;
}

.customer-list-badges,
.customer-detail-badges {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 0.35rem;
}

.customer-detail-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 0.8rem;
  padding: 0.95rem;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-lg);
  background: linear-gradient(135deg, var(--ds-surface), var(--ds-surface-soft));
}

.customer-detail-header h2,
.customer-detail-header p {
  margin: 0;
}

.customer-detail-header h2 {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-xl);
  font-weight: 950;
}

.customer-detail-header p {
  margin-top: 0.25rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.customer-detail-grid,
.customer-stats-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.65rem;
}

.customer-field-label {
  display: block;
  margin-bottom: 0.35rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-xs);
  font-weight: 800;
}

.customer-detail-grid strong,
.customer-stats-commodities strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
  word-break: break-word;
}

.customer-detail-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.customer-detail-toolbar strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
}

.customer-period-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.customer-period-tabs button {
  min-height: 36px;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-full);
  background: var(--ds-surface);
  color: var(--ds-text-muted);
  font: inherit;
  font-size: var(--ds-font-xs);
  font-weight: 850;
  padding: 0.45rem 0.75rem;
  cursor: pointer;
}

.customer-period-tabs button.is-active {
  border-color: var(--ds-primary);
  background: var(--ds-primary-soft);
  color: var(--ds-primary-strong);
}

.customer-stats-commodities {
  grid-column: 1 / -1;
}

.customer-stats-commodities ul {
  display: grid;
  gap: 0.45rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

.customer-stats-commodities li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
}

.customer-danger-card {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 0.75rem;
  align-items: start;
}

.customer-danger-card strong,
.customer-danger-card p {
  margin: 0;
}

.customer-danger-card strong {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
  font-weight: 900;
}

.customer-danger-card p {
  margin-top: 0.25rem;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.workspace-compatibility-panel {
  min-width: 0;
}

.customer-edit-form-card,
.customer-create-panel,
.customer-generated-account,
.customer-selection-card {
  display: grid;
  gap: 0.85rem;
}

.customer-edit-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
}

.customer-inline-actions,
.customer-session-actions,
.customer-pending-card__header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.65rem;
}

.customer-selection-card p,
.customer-pending-card__header p {
  margin: 0.2rem 0 0;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

@media (max-width: 520px) {
  .customer-summary-grid,
  .customer-detail-grid,
  .customer-stats-grid {
    grid-template-columns: 1fr;
  }

  .customer-edit-form-grid,
  .customer-detail-header {
    grid-template-columns: 1fr;
  }

  .customer-detail-badges,
  .customer-list-badges {
    justify-content: flex-start;
  }
}

@media (max-width: 767px) {
  .customer-workspace-view :deep(.ds-workspace-main),
  .customer-workspace-view :deep(.ds-workspace-aside) {
    padding-bottom: calc(var(--ds-bottom-nav-height) + var(--ds-safe-area-bottom) + 1.5rem);
  }
}
</style>
