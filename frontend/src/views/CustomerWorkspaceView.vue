<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Clock, Copy, ReceiptText, ShieldAlert, UserPlus, Users } from 'lucide-vue-next'
import CustomerNameWithBadge from '../components/CustomerNameWithBadge.vue'
import {
  WorkspaceAccountDeletionDialog,
  WorkspaceNotice,
  WorkspaceSection,
  WorkspaceShell,
} from '../components/workspace'
import {
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
  AppLoadingState,
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
  fetchOwnerCustomerRelation,
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
import { invitationRelationLink, invitationSmsStatusMessage } from '../utils/invitationContract'
import { tradeSettlementLabel } from '../utils/settlementType'

const route = useRoute()
const router = useRouter()
const customerState = useOwnerCustomers()
const isLoading = ref(false)
const isRefreshingRelations = ref(false)
const hasLoadedRelations = ref(false)
const isMobile = ref(typeof window !== 'undefined' && window.innerWidth < 900)
const error = ref('')
const isCreatePanelOpen = ref(false)
const isCreateSubmitting = ref(false)
const createError = ref('')
const createNotice = ref('')
const isSavingLimits = ref(false)
const isLimitsReviewOpen = ref(false)
const pendingLimitsPayload = ref<ReturnType<typeof buildCustomerDetailUpdatePayload> | null>(null)
const limitsError = ref('')
const limitsNotice = ref('')
const sessionNotice = ref('')
const listActionNotice = ref('')
const copiedRelationId = ref<number | null>(null)
const copiedInvitationSurface = ref<'bot' | 'web' | null>(null)
const invitationFeedback = ref<Record<number, { tone: 'success' | 'danger'; message: string }>>({})
const isConfirmDialogOpen = ref(false)
const isAccountDeletionDialogOpen = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmAction = ref<
  'terminate-session' | 'cancel-invitation' | 'close-relation' | 'delete-account' | null
>(null)
const confirmRelation = ref<CustomerRelation | null>(null)
const confirmSession = ref<CustomerSessionSummary | null>(null)
const isConfirmBusy = ref(false)
const confirmError = ref('')
const searchQuery = ref(routeQueryValue('q'))
const relationFilter = ref(routeQueryValue('filter') || 'all')
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
const relationListRef = ref<HTMLElement | null>(null)
const savedListScroll = ref(parseRouteScroll())
const detailTradesLoadedKey = ref<string | null>(null)
const detailStatsLoadedKey = ref<string | null>(null)
const detailSessionsLoadedKey = ref<string | null>(null)

let tradesRequestGeneration = 0
let statsRequestGeneration = 0
let sessionsRequestGeneration = 0
let relationsRequestGeneration = 0
let relationsMutationVersion = 0
let createOperationGeneration = 0
let limitsOperationGeneration = 0
let confirmOperationGeneration = 0
let confirmRouteGeneration = 0
let relationsRequestController: AbortController | null = null
let createRequestController: AbortController | null = null
let tradesRequestController: AbortController | null = null
let statsRequestController: AbortController | null = null
let sessionsRequestController: AbortController | null = null
let lastCanonicalReplaceSignature = ''
let routeScrollTarget: HTMLElement | Window | null = null
let isDeleteNavigationPending = false

const relationFilterOptions = [
  { key: 'all', label: 'همه' },
  { key: 'active', label: 'فعال' },
  { key: 'pending', label: 'دعوت‌ها' },
  { key: 'tier2', label: 'سطح ۲' },
  { key: 'inactive', label: 'غیرفعال' },
]

if (!relationFilterOptions.some((option) => option.key === relationFilter.value)) {
  relationFilter.value = 'all'
}

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
  return Array.isArray(value) ? (value[0] ?? null) : (value ?? null)
})

const hasDetailRoute = computed(() => relationId.value != null && relationId.value !== '')

const relationIdNumber = computed(() => {
  if (relationId.value == null || relationId.value === '') return null
  const normalized = Number(relationId.value)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
})

const activeRelation = computed(() => {
  const id = relationIdNumber.value
  if (id == null) return null
  return customerState.relations.value.find((relation) => relation.id === id) ?? null
})
const activeRelationId = computed(() => activeRelation.value?.id ?? null)
const activeRelationRequestKey = computed(() => {
  const relation = activeRelation.value
  return relation
    ? `${relation.id}:${relation.customer_user_id ?? 'none'}:${relation.status}`
    : null
})
const isPendingRelation = computed(() => activeRelation.value?.status === 'pending')
const isActiveRelation = computed(() => activeRelation.value?.status === 'active')
const isTerminalRelation = computed(
  () => Boolean(activeRelation.value) && !isPendingRelation.value && !isActiveRelation.value,
)
const hasLiveCustomerAccount = computed(
  () => isActiveRelation.value && Boolean(activeRelation.value?.customer_user_id),
)
const canEditLimits = computed(() => hasLiveCustomerAccount.value)
const canViewHistory = computed(
  () => Boolean(activeRelation.value?.customer_user_id) && !isPendingRelation.value,
)
const canManageSessions = computed(() => hasLiveCustomerAccount.value)
const canDeleteAccount = computed(() => hasLiveCustomerAccount.value)
const canCloseRelationOnly = computed(
  () => isActiveRelation.value && !activeRelation.value?.customer_user_id,
)

const availableDetailTabOptions = computed(() => {
  if (!hasLoadedRelations.value) return detailTabOptions
  if (!activeRelation.value) return []
  if (isPendingRelation.value) return []
  if (isTerminalRelation.value) {
    return detailTabOptions.filter(
      (option) =>
        option.key === 'profile' ||
        (canViewHistory.value && ['trades', 'stats'].includes(option.key)),
    )
  }
  return detailTabOptions.filter((option) => {
    if (option.key === 'limits') return canEditLimits.value
    if (['trades', 'stats'].includes(option.key)) return canViewHistory.value
    if (option.key === 'sessions') return canManageSessions.value
    if (option.key === 'danger') return canDeleteAccount.value || canCloseRelationOnly.value
    return true
  })
})

const actionablePendingCount = computed(() => customerState.pendingInvitationRelations.value.length)

const financialReviewRows = computed(() => {
  const relation = activeRelation.value
  const payload = pendingLimitsPayload.value
  if (!relation || !payload) return []

  const definitions = [
    { key: 'customer_tier', label: 'سطح مشتری' },
    { key: 'commission_rate', label: 'کمیسیون' },
    { key: 'min_trade_quantity', label: 'حداقل هر معامله' },
    { key: 'max_trade_quantity', label: 'حداکثر هر معامله' },
    { key: 'max_daily_trades', label: 'تعداد روزانه' },
    { key: 'max_daily_commodity_volume', label: 'سقف روزانه' },
  ] as const

  return definitions
    .filter(({ key }) => Object.prototype.hasOwnProperty.call(payload, key))
    .map(({ key, label }) => ({
      key,
      label,
      before: formatFinancialValue(key, relation[key]),
      after: formatFinancialValue(key, payload[key]),
    }))
})

function requestedDetailTabFromRoute() {
  const canonicalTab = routeQueryValue('tab')
  if (detailTabOptions.some((option) => option.key === canonicalTab)) return canonicalTab
  return (
    [routeQueryValue('panel'), routeQueryValue('section')].find((value) =>
      detailTabOptions.some((option) => option.key === value),
    ) || ''
  )
}

const detailTab = computed({
  get() {
    const normalized = requestedDetailTabFromRoute()
    const options = hasLoadedRelations.value ? availableDetailTabOptions.value : detailTabOptions
    return options.some((option) => option.key === normalized) ? (normalized as string) : 'profile'
  },
  set(tab: string) {
    if (!availableDetailTabOptions.value.some((option) => option.key === tab)) return
    router.push({
      name: relationIdNumber.value ? 'operations-customers-detail' : 'operations-customers',
      params: relationIdNumber.value ? { relationId: String(relationIdNumber.value) } : {},
      query: buildCanonicalQuery(tab),
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
    if (filter === 'inactive' && (relation.status === 'active' || relation.status === 'pending'))
      return false
    if (!query) return true
    const haystack = [
      relation.management_name,
      relation.customer_account_name,
      relation.invitation_account_name,
      relation.mobile_number,
      relation.customer_tier,
      relation.status,
    ]
      .filter(Boolean)
      .join(' ')
      .toLocaleLowerCase('fa-IR')
    return haystack.includes(query)
  })
})

const visiblePendingRelations = computed(() =>
  filteredRelations.value.filter((relation) => relation.status === 'pending'),
)
const visibleManageableRelations = computed(() =>
  filteredRelations.value.filter((relation) => relation.status !== 'pending'),
)

const activeRelationLimits = computed(() => {
  const relation = activeRelation.value
  if (!relation) return []
  return [
    {
      label: 'حداقل مقدار معامله',
      value: formatMaybeNumber(relation.min_trade_quantity),
      description: 'کمترین حجمی که مشتری مجاز به معامله است.',
    },
    {
      label: 'حداکثر مقدار معامله',
      value: formatMaybeNumber(relation.max_trade_quantity),
      description: 'بیشترین حجم مجاز برای هر معامله.',
    },
    {
      label: 'حداکثر تعداد روزانه',
      value: formatMaybeNumber(relation.max_daily_trades),
      description: 'سقف تعداد معاملات مشتری در یک روز.',
    },
    {
      label: 'حداکثر حجم روزانه',
      value: formatMaybeNumber(relation.max_daily_commodity_volume),
      description: 'سقف مجموع حجم کالایی در روز.',
    },
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
    const seeded =
      customerState.detailEditForm.commission_rate || activeRelation.value?.commission_rate || 0.5
    return normalizeCommissionRate(seeded)
  },
  set: (value: number) => {
    customerState.detailEditForm.commission_rate = normalizeCommissionRate(value).toFixed(2)
  },
})

const createCommissionPreview = computed(() => {
  const amount = (100_000_000 * createCommissionRate.value) / 100
  return formatToman(amount)
})

const detailCommissionPreview = computed(() => {
  const amount = (100_000_000 * detailCommissionRate.value) / 100
  return formatToman(amount)
})

const generatedCreateAccountName = computed(() => {
  const mobileDigits = normalizeLatinDigits(customerState.createForm.mobile_number).replace(
    /\D/g,
    '',
  )
  return mobileDigits ? `customer_${mobileDigits}` : ''
})

function routeQueryValue(key: string) {
  const value = route.query[key]
  if (Array.isArray(value)) return String(value[0] ?? '')
  return value == null ? '' : String(value)
}

function parseRouteScroll() {
  const parsed = Number(routeQueryValue('scroll') || routeQueryValue('listScroll'))
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : 0
}

function normalizeFilter(value: string) {
  return relationFilterOptions.some((option) => option.key === value) ? value : 'all'
}

function canonicalDetailTab(value: string) {
  if (!relationIdNumber.value) return ''
  const options = hasLoadedRelations.value
    ? activeRelation.value
      ? availableDetailTabOptions.value
      : []
    : detailTabOptions
  return options.some((option) => option.key === value) ? value : ''
}

function buildCanonicalQuery(tabOverride?: string) {
  const query: Record<string, string> = {}
  const normalizedSearch = searchQuery.value.trim()
  const normalizedFilter = normalizeFilter(relationFilter.value)
  const normalizedScroll = Math.max(0, Math.round(savedListScroll.value || 0))
  const requestedTab = tabOverride ?? requestedDetailTabFromRoute()
  const normalizedTab = canonicalDetailTab(requestedTab)

  if (normalizedSearch) query.q = normalizedSearch
  if (normalizedFilter !== 'all') query.filter = normalizedFilter
  if (normalizedScroll > 0) query.scroll = String(normalizedScroll)
  if (normalizedTab && normalizedTab !== 'profile') query.tab = normalizedTab
  return query
}

function serializeQuery(query: Record<string, unknown>) {
  return JSON.stringify(
    Object.keys(query)
      .sort()
      .map((key) => {
        const value = query[key]
        return [
          key,
          Array.isArray(value) ? value.map(String) : value == null ? null : String(value),
        ]
      }),
  )
}

function syncCanonicalRouteQuery(tabOverride?: string) {
  if (isDeleteNavigationPending) return
  const canonicalQuery = buildCanonicalQuery(tabOverride)
  const currentSignature = serializeQuery(route.query as Record<string, unknown>)
  const canonicalSignature = serializeQuery(canonicalQuery)
  if (currentSignature === canonicalSignature) {
    lastCanonicalReplaceSignature = ''
    return
  }
  const replaceSignature = `${currentSignature}->${canonicalSignature}`
  if (lastCanonicalReplaceSignature === replaceSignature) return
  lastCanonicalReplaceSignature = replaceSignature
  void router.replace({ query: canonicalQuery })
}

function syncLocalContextFromRoute() {
  const legacyPanel = routeQueryValue('panel')
  const legacySection = routeQueryValue('section')
  const legacyValues = [legacyPanel, legacySection]
  const nextSearch = routeQueryValue('q').trim()
  const canonicalFilter = routeQueryValue('filter')
  const requestedFilter = relationFilterOptions.some((option) => option.key === canonicalFilter)
    ? canonicalFilter
    : legacyValues.includes('pending')
      ? 'pending'
      : 'all'
  const nextFilter = normalizeFilter(requestedFilter)
  const nextScroll = parseRouteScroll()
  const canonicalTab = routeQueryValue('tab')
  const requestedTab = detailTabOptions.some((option) => option.key === canonicalTab)
    ? canonicalTab
    : legacyValues.find((value) => detailTabOptions.some((option) => option.key === value)) || ''
  const shouldRestoreScroll = savedListScroll.value !== nextScroll
  if (searchQuery.value !== nextSearch) searchQuery.value = nextSearch
  if (relationFilter.value !== nextFilter) relationFilter.value = nextFilter
  if (savedListScroll.value !== nextScroll) savedListScroll.value = nextScroll
  if (legacyPanel === 'create' && !isCreatePanelOpen.value) openCreatePanel()
  if (
    shouldRestoreScroll &&
    hasLoadedRelations.value &&
    (!hasDetailRoute.value || !isMobile.value)
  ) {
    restoreListScroll()
  }
  syncCanonicalRouteQuery(requestedTab)
}

function resolveRouteScrollTarget(): HTMLElement | Window | null {
  if (typeof document !== 'undefined') {
    return (
      document.querySelector<HTMLElement>('.app-route-scroll') ??
      (document.scrollingElement instanceof HTMLElement
        ? document.scrollingElement
        : document.documentElement)
    )
  }
  return typeof window !== 'undefined' ? window : null
}

function routeScrollTop() {
  if (routeScrollTarget instanceof HTMLElement) return routeScrollTarget.scrollTop
  if (typeof window === 'undefined') return 0
  return Math.max(
    window.scrollY,
    document.documentElement?.scrollTop ?? 0,
    document.body?.scrollTop ?? 0,
  )
}

function captureListScroll() {
  const nextScroll =
    hasDetailRoute.value && !isMobile.value
      ? (relationListRef.value?.scrollTop ?? 0)
      : routeScrollTop()
  savedListScroll.value = Math.max(0, Math.round(nextScroll))
}

function capturePageScroll() {
  if (hasDetailRoute.value) return
  captureListScroll()
  syncCanonicalRouteQuery()
}

function handleListScroll(event: Event) {
  savedListScroll.value = Math.max(0, Math.round((event.currentTarget as HTMLElement).scrollTop))
  syncCanonicalRouteQuery()
}

function restoreListScroll() {
  void nextTick(() => {
    if (hasDetailRoute.value && !isMobile.value) {
      if (relationListRef.value) relationListRef.value.scrollTop = savedListScroll.value
      return
    }
    if (!hasDetailRoute.value) {
      if (routeScrollTarget instanceof HTMLElement) {
        routeScrollTarget.scrollTop = savedListScroll.value
      } else if (typeof window !== 'undefined') {
        window.scrollTo(0, savedListScroll.value)
      }
    }
  })
}

function clearCustomerSearch() {
  searchQuery.value = ''
  relationFilter.value = 'all'
}

function abortRelationsRequest() {
  relationsRequestGeneration += 1
  relationsRequestController?.abort()
  relationsRequestController = null
  isLoading.value = false
  isRefreshingRelations.value = false
}

function isNotFoundError(error: unknown) {
  return Boolean(error && typeof error === 'object' && 'status' in error && error.status === 404)
}

function invalidateRelationsForMutation() {
  relationsMutationVersion += 1
  abortRelationsRequest()
  error.value = ''
}

function reconcileRelationUpsert(relation: CustomerRelation) {
  invalidateRelationsForMutation()
  customerState.relations.value = [
    relation,
    ...customerState.relations.value.filter((item) => item.id !== relation.id),
  ]
  hasLoadedRelations.value = true
}

function reconcileRelationDelete(relationId: number) {
  invalidateRelationsForMutation()
  customerState.relations.value = customerState.relations.value.filter(
    (item) => item.id !== relationId,
  )
  hasLoadedRelations.value = true
}

async function loadRelations(force = false) {
  if ((isLoading.value || isRefreshingRelations.value) && !force) return
  abortRelationsRequest()
  const isInitialLoad = !hasLoadedRelations.value
  const requestGeneration = ++relationsRequestGeneration
  const capturedMutationVersion = relationsMutationVersion
  const capturedDetailId = relationIdNumber.value
  const controller = new AbortController()
  relationsRequestController = controller
  const isCurrentRequest = () =>
    requestGeneration === relationsRequestGeneration &&
    capturedMutationVersion === relationsMutationVersion &&
    capturedDetailId === relationIdNumber.value
  if (isInitialLoad) isLoading.value = true
  else isRefreshingRelations.value = true
  error.value = ''
  try {
    const relations = await fetchOwnerCustomerRelations({ signal: controller.signal })
    if (!isCurrentRequest()) return
    if (capturedDetailId && !relations.some((relation) => relation.id === capturedDetailId)) {
      try {
        const detailRelation = await fetchOwnerCustomerRelation(capturedDetailId, {
          signal: controller.signal,
        })
        if (!isCurrentRequest()) return
        if (detailRelation.id !== capturedDetailId) {
          throw new Error('پاسخ پرونده مشتری معتبر نبود.')
        }
        relations.unshift(detailRelation)
      } catch (err: unknown) {
        if (!isCurrentRequest() || isAbortError(err)) return
        if (!isNotFoundError(err)) throw err
      }
    }
    customerState.relations.value = relations
    hasLoadedRelations.value = true
  } catch (err: any) {
    if (!isCurrentRequest() || isAbortError(err)) return
    error.value = err?.message || 'دریافت لیست مشتریان ناموفق بود.'
  } finally {
    if (isCurrentRequest()) {
      isLoading.value = false
      isRefreshingRelations.value = false
      if (relationsRequestController === controller) relationsRequestController = null
    }
  }
}

function goToOperations() {
  router.push({ name: 'operations' })
}

function openRelation(relationId: number) {
  if (!hasDetailRoute.value) captureListScroll()
  router.push({
    name: 'operations-customers-detail',
    params: { relationId: String(relationId) },
    query: buildCanonicalQuery('profile'),
  })
}

async function backToList() {
  await router.push({
    name: 'operations-customers',
    query: buildCanonicalQuery('profile'),
  })
  if (routeScrollTarget instanceof HTMLElement) {
    routeScrollTarget.scrollTop = savedListScroll.value
  } else if (typeof window !== 'undefined') {
    window.scrollTo(0, savedListScroll.value)
  }
}

function handleBack() {
  if (isCreatePanelOpen.value) {
    closeCreatePanel()
    return
  }
  if (hasDetailRoute.value) {
    backToList()
    return
  }
  goToOperations()
}

function updateIsMobile() {
  if (typeof window === 'undefined') return
  isMobile.value = window.innerWidth < 900
}

function openCreatePanel() {
  isCreatePanelOpen.value = true
  createError.value = ''
  createNotice.value = ''
}

function closeCreatePanel() {
  if (isCreateSubmitting.value) return
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

function snapshotCreateForm() {
  return {
    management_name: customerState.createForm.management_name,
    mobile_number: customerState.createForm.mobile_number,
    customer_tier: customerState.createForm.customer_tier,
    commission_rate: customerState.createForm.commission_rate,
    min_trade_quantity: customerState.createForm.min_trade_quantity,
    max_trade_quantity: customerState.createForm.max_trade_quantity,
    max_daily_trades: customerState.createForm.max_daily_trades,
    max_daily_commodity_volume: customerState.createForm.max_daily_commodity_volume,
  }
}

function createDraftSignature(draft = snapshotCreateForm()) {
  return JSON.stringify(draft)
}

function isValidCreateReceipt(
  created: CustomerRelation,
  capturedDraft: ReturnType<typeof snapshotCreateForm>,
) {
  const capturedMobile = normalizeLatinDigits(capturedDraft.mobile_number).replace(/\D/g, '')
  const receiptMobile = normalizeLatinDigits(created.mobile_number || '').replace(/\D/g, '')
  return (
    Number.isInteger(created.id) &&
    created.id > 0 &&
    created.status === 'pending' &&
    created.customer_user_id == null &&
    capturedMobile.length > 0 &&
    receiptMobile === capturedMobile &&
    Boolean(
      created.invitation_account_name ||
        invitationRelationLink(created, 'web') ||
        invitationRelationLink(created, 'bot'),
    )
  )
}

function seedDetailEditForm(
  relation: CustomerRelation | null,
  options: { resetFeedback?: boolean } = {},
) {
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
    min_trade_quantity:
      relation.min_trade_quantity == null ? '' : String(relation.min_trade_quantity),
    max_trade_quantity:
      relation.max_trade_quantity == null ? '' : String(relation.max_trade_quantity),
    max_daily_trades: relation.max_daily_trades == null ? '' : String(relation.max_daily_trades),
    max_daily_commodity_volume:
      relation.max_daily_commodity_volume == null
        ? ''
        : String(relation.max_daily_commodity_volume),
  })
  if (resetFeedback) {
    limitsError.value = ''
    limitsNotice.value = ''
  }
}

function isAbortError(err: unknown) {
  return Boolean(err && typeof err === 'object' && 'name' in err && err.name === 'AbortError')
}

function abortTradesRequest() {
  tradesRequestGeneration += 1
  tradesRequestController?.abort()
  tradesRequestController = null
  detailTradesLoading.value = false
}

function abortStatsRequest() {
  statsRequestGeneration += 1
  statsRequestController?.abort()
  statsRequestController = null
  detailStatsLoading.value = false
}

function abortSessionsRequest() {
  sessionsRequestGeneration += 1
  sessionsRequestController?.abort()
  sessionsRequestController = null
  detailSessionsLoading.value = false
}

function abortAllDetailRequests() {
  abortTradesRequest()
  abortStatsRequest()
  abortSessionsRequest()
}

function resetDetailRequestState() {
  abortAllDetailRequests()
  detailTrades.value = []
  detailStats.value = null
  detailSessions.value = []
  detailTradesLoadedKey.value = null
  detailStatsLoadedKey.value = null
  detailSessionsLoadedKey.value = null
  detailTradesError.value = ''
  detailStatsError.value = ''
  detailSessionsError.value = ''
}

async function loadDetailTrades(force = false) {
  const relation = activeRelation.value
  if (!relation?.customer_user_id || relation.status === 'pending') {
    abortTradesRequest()
    detailTrades.value = []
    detailTradesLoadedKey.value = null
    return
  }

  const capturedRelationId = relation.id
  const capturedCustomerUserId = relation.customer_user_id
  const loadedKey = `${capturedRelationId}:${capturedCustomerUserId}`
  if (!force && detailTradesLoadedKey.value === loadedKey) return

  abortTradesRequest()
  if (detailTradesLoadedKey.value !== loadedKey) detailTrades.value = []
  const requestGeneration = ++tradesRequestGeneration
  const controller = new AbortController()
  tradesRequestController = controller
  detailTradesLoading.value = true
  detailTradesError.value = ''

  const isCurrentRequest = () =>
    requestGeneration === tradesRequestGeneration &&
    activeRelation.value?.id === capturedRelationId &&
    activeRelation.value?.customer_user_id === capturedCustomerUserId &&
    detailTab.value === 'trades'

  try {
    const trades = await fetchOwnerCustomerTrades(capturedCustomerUserId, {
      limit: 20,
      signal: controller.signal,
    })
    if (!isCurrentRequest()) return
    detailTrades.value = trades
    detailTradesLoadedKey.value = loadedKey
  } catch (err: any) {
    if (!isCurrentRequest() || isAbortError(err)) return
    detailTradesError.value = err?.message || 'دریافت معاملات مشتری ناموفق بود.'
  } finally {
    if (isCurrentRequest()) {
      detailTradesLoading.value = false
      if (tradesRequestController === controller) tradesRequestController = null
    }
  }
}

async function loadDetailStats(force = false) {
  const relation = activeRelation.value
  if (!relation || relation.status === 'pending') {
    abortStatsRequest()
    detailStats.value = null
    detailStatsLoadedKey.value = null
    return
  }

  const capturedRelationId = relation.id
  const capturedPeriodDays = statsPeriodDays.value
  const loadedKey = `${capturedRelationId}:${capturedPeriodDays}`
  if (!force && detailStatsLoadedKey.value === loadedKey) return

  abortStatsRequest()
  if (detailStatsLoadedKey.value !== loadedKey) detailStats.value = null
  const requestGeneration = ++statsRequestGeneration
  const controller = new AbortController()
  statsRequestController = controller
  detailStatsLoading.value = true
  detailStatsError.value = ''

  const isCurrentRequest = () =>
    requestGeneration === statsRequestGeneration &&
    activeRelation.value?.id === capturedRelationId &&
    statsPeriodDays.value === capturedPeriodDays &&
    detailTab.value === 'stats'

  try {
    const stats = await fetchOwnerCustomerTradeStats(capturedRelationId, capturedPeriodDays, {
      signal: controller.signal,
    })
    if (!isCurrentRequest()) return
    detailStats.value = stats
    detailStatsLoadedKey.value = loadedKey
  } catch (err: any) {
    if (!isCurrentRequest() || isAbortError(err)) return
    detailStatsError.value = err?.message || 'دریافت آمار مشتری ناموفق بود.'
  } finally {
    if (isCurrentRequest()) {
      detailStatsLoading.value = false
      if (statsRequestController === controller) statsRequestController = null
    }
  }
}

async function loadDetailSessions(force = false) {
  const relation = activeRelation.value
  if (!relation || relation.status !== 'active' || !relation.customer_user_id) {
    abortSessionsRequest()
    detailSessions.value = []
    detailSessionsLoadedKey.value = null
    return
  }

  const capturedRelationId = relation.id
  const capturedCustomerUserId = relation.customer_user_id
  const loadedKey = `${capturedRelationId}:${capturedCustomerUserId}`
  if (!force && detailSessionsLoadedKey.value === loadedKey) return

  abortSessionsRequest()
  if (detailSessionsLoadedKey.value !== loadedKey) detailSessions.value = []
  const requestGeneration = ++sessionsRequestGeneration
  const controller = new AbortController()
  sessionsRequestController = controller
  detailSessionsLoading.value = true
  detailSessionsError.value = ''

  const isCurrentRequest = () =>
    requestGeneration === sessionsRequestGeneration &&
    activeRelation.value?.id === capturedRelationId &&
    activeRelation.value?.customer_user_id === capturedCustomerUserId &&
    activeRelation.value?.status === 'active' &&
    detailTab.value === 'sessions'

  try {
    const sessions = await fetchOwnerCustomerSessions(capturedRelationId, {
      signal: controller.signal,
    })
    if (!isCurrentRequest()) return
    detailSessions.value = sessions
    detailSessionsLoadedKey.value = loadedKey
  } catch (err: any) {
    if (!isCurrentRequest() || isAbortError(err)) return
    detailSessionsError.value = err?.message || 'دریافت نشست‌های مشتری ناموفق بود.'
  } finally {
    if (isCurrentRequest()) {
      detailSessionsLoading.value = false
      if (sessionsRequestController === controller) sessionsRequestController = null
    }
  }
}

function refreshCurrentDetailTab() {
  if (detailTab.value === 'trades') void loadDetailTrades(false)
  if (detailTab.value === 'stats') void loadDetailStats(false)
  if (detailTab.value === 'sessions') void loadDetailSessions(false)
}

function setStatsPeriod(days: number) {
  statsPeriodDays.value = days
}

async function createRelation() {
  if (isCreateSubmitting.value) return
  const capturedDraft = snapshotCreateForm()
  const capturedDraftSignature = createDraftSignature(capturedDraft)
  const requestGeneration = ++createOperationGeneration
  createRequestController?.abort()
  const controller = new AbortController()
  createRequestController = controller
  const isCurrentOperation = () => requestGeneration === createOperationGeneration
  isCreateSubmitting.value = true
  createError.value = ''
  createNotice.value = ''
  try {
    const created = await createOwnerCustomerRelation(
      {
        account_name: `customer_${normalizeLatinDigits(capturedDraft.mobile_number).replace(/\D/g, '')}`,
        management_name: capturedDraft.management_name,
        mobile_number: capturedDraft.mobile_number,
        ...buildCustomerPayload(capturedDraft),
      },
      { signal: controller.signal },
    )
    if (!isCurrentOperation()) return
    if (!created || !isValidCreateReceipt(created, capturedDraft)) {
      throw new Error('پاسخ ایجاد مشتری معتبر نبود.')
    }
    reconcileRelationUpsert(created)
    createNotice.value =
      invitationSmsStatusMessage(created.sms_status) || 'دعوت مشتری با موفقیت ثبت شد.'
    if (createDraftSignature() === capturedDraftSignature) {
      resetCreateForm()
      isCreateSubmitting.value = false
      isCreatePanelOpen.value = false
    }
  } catch (err: any) {
    if (!isCurrentOperation() || isAbortError(err)) return
    createError.value = err?.message || 'ایجاد مشتری ناموفق بود.'
  } finally {
    if (isCurrentOperation()) {
      isCreateSubmitting.value = false
      if (createRequestController === controller) createRequestController = null
    }
  }
}

async function saveDetailLimits() {
  const relation = activeRelation.value
  if (
    !relation ||
    relation.status !== 'active' ||
    !relation.customer_user_id ||
    isSavingLimits.value
  )
    return
  limitsError.value = ''
  limitsNotice.value = ''
  let payload: ReturnType<typeof buildCustomerDetailUpdatePayload>
  try {
    payload = buildCustomerDetailUpdatePayload(relation, customerState.detailEditForm)
  } catch (err: any) {
    limitsError.value = err?.message || 'مقادیر تنظیمات مشتری معتبر نیستند.'
    return
  }
  if (!Object.keys(payload).length) {
    limitsNotice.value = 'تغییری برای ذخیره انتخاب نشده است.'
    return
  }
  pendingLimitsPayload.value = payload
  isLimitsReviewOpen.value = true
}

function returnToLimitsEdit() {
  isLimitsReviewOpen.value = false
  pendingLimitsPayload.value = null
}

async function confirmDetailLimits() {
  const relation = activeRelation.value
  const payload = pendingLimitsPayload.value
  if (
    !relation ||
    relation.status !== 'active' ||
    !relation.customer_user_id ||
    !payload ||
    isSavingLimits.value
  )
    return
  const capturedRelationId = relation.id
  const operationGeneration = ++limitsOperationGeneration
  const isCurrentOperation = () =>
    operationGeneration === limitsOperationGeneration &&
    activeRelation.value?.id === capturedRelationId &&
    activeRelation.value?.status === 'active' &&
    Boolean(activeRelation.value?.customer_user_id)
  isSavingLimits.value = true
  limitsError.value = ''
  limitsNotice.value = ''
  try {
    const updated = await updateOwnerCustomerRelation(relation.id, payload)
    if (!updated || updated.id !== relation.id) {
      throw new Error('پاسخ ویرایش مشتری معتبر نبود.')
    }
    reconcileRelationUpsert(updated)
    if (!isCurrentOperation()) return
    seedDetailEditForm(updated)
    pendingLimitsPayload.value = null
    isLimitsReviewOpen.value = false
    limitsNotice.value = 'تنظیمات مشتری ذخیره شد و فقط بر معاملات آینده اثر می‌گذارد.'
  } catch (err: any) {
    if (!isCurrentOperation()) return
    limitsError.value = err?.message || 'ذخیره تنظیمات مشتری ناموفق بود.'
  } finally {
    if (operationGeneration === limitsOperationGeneration) isSavingLimits.value = false
  }
}

async function copyRegistrationLink(relation: CustomerRelation, surface: 'bot' | 'web' = 'web') {
  const link = invitationRelationLink(relation, surface)
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
    invitationFeedback.value = {
      ...invitationFeedback.value,
      [relation.id]: {
        tone: 'success',
        message: surface === 'bot' ? 'لینک تلگرام کپی شد.' : 'لینک وب‌اپ کپی شد.',
      },
    }
    copiedRelationId.value = relation.id
    copiedInvitationSurface.value = surface
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        if (copiedRelationId.value === relation.id && copiedInvitationSurface.value === surface) {
          copiedRelationId.value = null
          copiedInvitationSurface.value = null
          const nextFeedback = { ...invitationFeedback.value }
          delete nextFeedback[relation.id]
          invitationFeedback.value = nextFeedback
        }
      }, 1800)
    }
  } catch {
    invitationFeedback.value = {
      ...invitationFeedback.value,
      [relation.id]: { tone: 'danger', message: 'کپی لینک ثبت‌نام ممکن نشد؛ دوباره تلاش کنید.' },
    }
  }
}

function openConfirmDialog(
  kind: 'terminate-session' | 'cancel-invitation' | 'close-relation',
  relation: CustomerRelation,
  session: CustomerSessionSummary | null = null,
) {
  if (isConfirmBusy.value) return
  if (
    kind === 'terminate-session' &&
    (relation.status !== 'active' ||
      !relation.customer_user_id ||
      activeRelation.value?.id !== relation.id)
  )
    return
  if (kind === 'cancel-invitation' && relation.status !== 'pending') return
  if (
    kind === 'close-relation' &&
    (relation.status !== 'active' ||
      relation.customer_user_id ||
      activeRelation.value?.id !== relation.id)
  )
    return
  confirmAction.value = kind
  confirmRelation.value = relation
  confirmSession.value = session
  confirmError.value = ''
  confirmTitle.value =
    kind === 'terminate-session'
      ? 'پایان نشست'
      : kind === 'close-relation'
        ? 'بستن رابطه مشتری'
        : 'لغو رابطه در انتظار و دعوت مشتری'
  confirmMessage.value =
    kind === 'terminate-session'
      ? `فقط نشست «${session?.device_name || 'دستگاه مشتری'}» برای «${getRelationTitle(relation)}» پایان یابد؟ نشست‌های دیگر فعال می‌مانند و در صورت نیاز قدیمی‌ترین نشست، اصلی می‌شود.`
      : kind === 'close-relation'
        ? `فقط رابطه «${getRelationTitle(relation)}» بسته شود؟ رزرو هویت مرتبط با این رابطه آزاد می‌شود؛ حساب فعالی وجود ندارد و هیچ آبشار حذف حساب، نشست، پیشنهاد یا تاریخچه‌ای اجرا نمی‌شود.`
        : `رابطه در انتظار و دعوت «${getRelationTitle(relation)}» لغو شوند؟ لینک دعوت و رزرو هویت این دعوت لغو می‌شوند؛ چون حسابی فعال نشده است، هیچ آبشار حذف حساب فعالی اجرا نمی‌شود.`
  isConfirmDialogOpen.value = true
}

function openAccountDeletionDialog(relation: CustomerRelation) {
  if (
    isConfirmBusy.value ||
    relation.status !== 'active' ||
    !relation.customer_user_id ||
    activeRelation.value?.id !== relation.id
  )
    return
  confirmAction.value = 'delete-account'
  confirmRelation.value = relation
  confirmSession.value = null
  confirmError.value = ''
  isAccountDeletionDialogOpen.value = true
}

function closeAccountDeletionDialog() {
  if (isConfirmBusy.value) return
  resetConfirmDialog()
}

function closeConfirmDialog() {
  if (isConfirmBusy.value) return
  resetConfirmDialog()
}

function resetConfirmDialog() {
  confirmOperationGeneration += 1
  isConfirmDialogOpen.value = false
  isAccountDeletionDialogOpen.value = false
  confirmAction.value = null
  confirmRelation.value = null
  confirmSession.value = null
  confirmError.value = ''
  isConfirmBusy.value = false
}

async function handleConfirmAction() {
  const relation = confirmRelation.value
  if (!relation || !confirmAction.value || isConfirmBusy.value) return
  const action = confirmAction.value
  const session = confirmSession.value
  const capturedConfirmRouteGeneration = confirmRouteGeneration
  const capturedRouteRelationId = relationIdNumber.value
  const shouldReturnToList = capturedRouteRelationId === relation.id
  const currentRelation = customerState.relations.value.find((item) => item.id === relation.id)
  if (
    !currentRelation ||
    (action === 'terminate-session' &&
      (currentRelation.status !== 'active' || !currentRelation.customer_user_id)) ||
    (action === 'cancel-invitation' && currentRelation.status !== 'pending') ||
    (action === 'close-relation' &&
      (currentRelation.status !== 'active' || Boolean(currentRelation.customer_user_id))) ||
    (action === 'delete-account' &&
      (currentRelation.status !== 'active' || !currentRelation.customer_user_id))
  ) {
    confirmError.value = 'این اقدام دیگر برای وضعیت فعلی رابطه در دسترس نیست.'
    return
  }
  const operationGeneration = ++confirmOperationGeneration
  const shouldHoldCanonicalSync = action !== 'terminate-session' && shouldReturnToList
  if (shouldHoldCanonicalSync) isDeleteNavigationPending = true
  const isCurrentOperation = () => {
    const latestRelation = customerState.relations.value.find((item) => item.id === relation.id)
    const hasCurrentCapability =
      action === 'cancel-invitation'
        ? latestRelation?.status === 'pending'
        : action === 'close-relation'
          ? latestRelation?.status === 'active' && !latestRelation.customer_user_id
          : latestRelation?.status === 'active' && Boolean(latestRelation.customer_user_id)
    return (
      operationGeneration === confirmOperationGeneration &&
      activeRelationId.value === capturedRouteRelationId &&
      confirmAction.value === action &&
      confirmRelation.value?.id === relation.id &&
      (action !== 'terminate-session' || confirmSession.value?.id === session?.id) &&
      hasCurrentCapability
    )
  }
  isConfirmBusy.value = true
  confirmError.value = ''

  try {
    if (action === 'terminate-session') {
      if (!session) throw new Error('نشست مشتری برای پایان دادن در دسترس نیست.')
      const receipt = await terminateOwnerCustomerSession(relation.id, session.id)
      if (!receipt || receipt.terminated_session_id !== session.id) {
        throw new Error('پاسخ پایان نشست مشتری معتبر نبود.')
      }
      if (!isCurrentOperation() || activeRelationId.value !== relation.id) return
      detailSessions.value = detailSessions.value
        .filter((item) => item.id !== receipt.terminated_session_id)
        .map((item) => ({
          ...item,
          is_primary: receipt.promoted_primary_session_id === item.id ? true : item.is_primary,
        }))
      sessionNotice.value = `نشست «${session.device_name || 'دستگاه مشتری'}» پایان یافت.`
      resetConfirmDialog()
      return
    }

    const expectedAction =
      action === 'cancel-invitation'
        ? 'cancel-pending'
        : action === 'close-relation'
          ? 'delete-relation'
          : 'delete-account'
    const receipt = await deleteOwnerCustomerRelation(
      relation.id,
      expectedAction,
      action === 'cancel-invitation'
        ? 'لغو رابطه در انتظار و دعوت مشتری ناموفق بود.'
        : action === 'close-relation'
          ? 'بستن رابطه مشتری ناموفق بود.'
          : 'حذف حساب مشتری ناموفق بود.',
    )
    const expectedStatus = action === 'cancel-invitation' ? 'revoked' : 'deleted'
    if (!receipt || receipt.id !== relation.id || receipt.status !== expectedStatus) {
      throw new Error(
        action === 'cancel-invitation'
          ? 'پاسخ لغو رابطه در انتظار و دعوت مشتری معتبر نبود.'
          : action === 'close-relation'
            ? 'پاسخ بستن رابطه مشتری معتبر نبود.'
            : 'پاسخ حذف حساب مشتری معتبر نبود.',
      )
    }
    const shouldApplyContext =
      capturedConfirmRouteGeneration === confirmRouteGeneration &&
      relationIdNumber.value === capturedRouteRelationId
    reconcileRelationDelete(relation.id)
    if (!shouldApplyContext) return
    listActionNotice.value =
      action === 'cancel-invitation'
        ? `رابطه در انتظار، دعوت و رزرو هویت «${getRelationTitle(relation)}» لغو شدند؛ هیچ حساب فعالی حذف نشد.`
        : action === 'close-relation'
          ? `رابطه «${getRelationTitle(relation)}» بدون آبشار حذف حساب بسته شد.`
          : `حساب «${getRelationTitle(relation)}» حذف شد.`
    resetConfirmDialog()
    if (shouldReturnToList) {
      await backToList()
    }
  } catch (err: unknown) {
    if (!isCurrentOperation()) return
    const errorMessage = err instanceof Error ? err.message : ''
    confirmError.value =
      errorMessage ||
      (action === 'terminate-session'
        ? 'پایان دادن نشست مشتری ناموفق بود.'
        : action === 'cancel-invitation'
          ? 'لغو رابطه در انتظار و دعوت مشتری ناموفق بود.'
          : action === 'close-relation'
            ? 'بستن رابطه مشتری ناموفق بود.'
            : 'حذف حساب مشتری ناموفق بود.')
  } finally {
    if (shouldHoldCanonicalSync) isDeleteNavigationPending = false
    if (operationGeneration === confirmOperationGeneration) isConfirmBusy.value = false
  }
}

function getRelationTitle(relation: CustomerRelation) {
  return (
    relation.management_name ||
    relation.customer_account_name ||
    relation.invitation_account_name ||
    'مشتری'
  )
}

function getRelationDescription(relation: CustomerRelation) {
  return maskMobile(relation.mobile_number) || 'شماره ثبت نشده'
}

function maskMobile(value: string | null | undefined) {
  const digits = normalizeLatinDigits(String(value || '')).replace(/\D/g, '')
  if (digits.length < 7) return value || ''
  return `${digits.slice(0, 4)} *** ${digits.slice(-4)}`
}

function formatInvitationDeadline(value: string | null | undefined) {
  if (!value) return 'مهلت ثبت‌نام اعلام نشده است.'
  return `مهلت ثبت‌نام: ${formatDate(value)}`
}

function getSessionDescription(session: CustomerSessionSummary) {
  const platform = session.platform ? `بستر ${session.platform}` : 'دستگاه ثبت‌شده'
  return `${platform} · آخرین فعالیت ${formatDate(session.last_active_at)}`
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

function formatFinancialValue(
  key:
    | 'customer_tier'
    | 'commission_rate'
    | 'min_trade_quantity'
    | 'max_trade_quantity'
    | 'max_daily_trades'
    | 'max_daily_commodity_volume',
  value: string | number | null | undefined,
) {
  if (key === 'customer_tier') return getTierLabel(String(value || 'tier1'))
  if (key === 'commission_rate') return value == null ? 'ندارد' : formatPercent(Number(value))
  return formatMaybeNumber(value == null ? null : Number(value))
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

watch(
  [activeRelationRequestKey, detailTab],
  ([nextRelationKey, nextTab], [previousRelationKey, previousTab]) => {
    if (nextRelationKey !== previousRelationKey) {
      resetDetailRequestState()
      limitsOperationGeneration += 1
      isSavingLimits.value = false
      isLimitsReviewOpen.value = false
      pendingLimitsPayload.value = null
      limitsError.value = ''
      limitsNotice.value = ''
      sessionNotice.value = ''
      if (confirmRelation.value) resetConfirmDialog()
    } else if (nextTab !== previousTab) {
      if (previousTab === 'trades') abortTradesRequest()
      if (previousTab === 'stats') abortStatsRequest()
      if (previousTab === 'sessions') abortSessionsRequest()
    }
    refreshCurrentDetailTab()
  },
  { flush: 'post' },
)

watch(relationIdNumber, (nextRelationId, previousRelationId) => {
  if (nextRelationId === previousRelationId) return
  abortRelationsRequest()
  if (!nextRelationId) return
  if (customerState.relations.value.some((relation) => relation.id === nextRelationId)) return
  void loadRelations(true)
})

watch(relationId, (nextRelationId, previousRelationId) => {
  if (nextRelationId !== previousRelationId) confirmRouteGeneration += 1
})

watch(
  activeRelation,
  (relation, previousRelation) => {
    if (relation?.id === previousRelation?.id) return
    isLimitsReviewOpen.value = false
    pendingLimitsPayload.value = null
    sessionNotice.value = ''
    seedDetailEditForm(relation, {
      resetFeedback: relation?.id !== previousRelation?.id,
    })
  },
  { immediate: true },
)

watch(() => route.query, syncLocalContextFromRoute, { deep: true, immediate: true })

watch([searchQuery, relationFilter], () => syncCanonicalRouteQuery())

watch(
  () => [
    hasLoadedRelations.value,
    activeRelation.value?.status,
    activeRelation.value?.customer_user_id,
    route.query.tab,
  ],
  () => syncCanonicalRouteQuery(),
)

watch(
  [hasLoadedRelations, hasDetailRoute, isMobile, () => filteredRelations.value.length],
  ([loaded, detailRoute, mobile, visibleRelationCount]) => {
    if (loaded && visibleRelationCount > 0 && (!detailRoute || !mobile)) restoreListScroll()
  },
  { flush: 'post' },
)

watch(statsPeriodDays, () => {
  abortStatsRequest()
  if (detailTab.value === 'stats') {
    void loadDetailStats(false)
  }
})

onMounted(() => {
  updateIsMobile()
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', updateIsMobile)
    routeScrollTarget = resolveRouteScrollTarget()
    routeScrollTarget?.addEventListener('scroll', capturePageScroll, { passive: true })
  }
  void loadRelations()
})

onBeforeUnmount(() => {
  abortRelationsRequest()
  abortAllDetailRequests()
  createOperationGeneration += 1
  createRequestController?.abort()
  createRequestController = null
  limitsOperationGeneration += 1
  confirmOperationGeneration += 1
  confirmRouteGeneration += 1
  isDeleteNavigationPending = false
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', updateIsMobile)
    routeScrollTarget?.removeEventListener('scroll', capturePageScroll)
    routeScrollTarget = null
  }
})
</script>

<template>
  <div class="ds-page customer-workspace-view">
    <WorkspaceShell
      class="ui-v2-workspace-customer-root"
      title="مشتریان"
      eyebrow="عملیات"
      description="جستجو، دعوت و مدیریت مشتری با تمرکز بر اقدام بعدی."
      layout="split"
      v2-scope
      show-back
      back-label="بازگشت"
      @back="handleBack"
    >
      <template #actions>
        <AppButton variant="primary" class="customer-workspace-create" @click="openCreatePanel">
          <template #icon>
            <UserPlus :size="16" />
          </template>
          افزودن مشتری
        </AppButton>
      </template>

      <WorkspaceNotice
        v-if="createNotice"
        class="ui-v2-workspace-customer-global-notice"
        v2-scope
        tone="success"
        title="دعوت مشتری ثبت شد"
        :message="createNotice"
      />

      <div
        class="customer-workspace-content ui-v2-workspace-customer-layout"
        :class="{
          'has-detail': hasDetailRoute,
          'ui-v2-workspace-customer-layout--detail': hasDetailRoute,
        }"
      >
        <WorkspaceSection
          v-if="hasDetailRoute"
          class="customer-detail-section ui-v2-workspace-customer-detail-section"
          title="پرونده مشتری"
          description="مشخصات، محدودیت‌ها، معاملات، آمار، نشست‌ها و اقدامات حساس در یک نمای یکپارچه."
          v2-scope
        >
          <WorkspaceNotice
            v-if="!hasLoadedRelations && error"
            v2-scope
            tone="danger"
            role="alert"
            title="دریافت پرونده مشتری ممکن نشد"
            :message="error"
          >
            <AppButton
              class="customer-detail-retry"
              size="sm"
              variant="secondary"
              :loading="isLoading"
              @click="loadRelations(true)"
            >
              تلاش دوباره
            </AppButton>
          </WorkspaceNotice>
          <AppLoadingState
            v-else-if="!hasLoadedRelations && isLoading"
            label="در حال دریافت ساختار پرونده مشتری"
          />
          <AppEmptyState
            v-else-if="!activeRelation && hasLoadedRelations"
            tone="warning"
            title="مشتری پیدا نشد"
            message="این رابطه در لیست فعلی وجود ندارد یا هنوز همگام‌سازی نشده است."
          >
            <template #actions>
              <AppButton variant="secondary" @click="backToList">بازگشت به فهرست مشتریان</AppButton>
            </template>
          </AppEmptyState>
          <div
            v-else-if="activeRelation"
            class="customer-detail-shell ui-v2-workspace-customer-detail-shell"
          >
            <WorkspaceNotice
              v-if="error && hasLoadedRelations"
              v2-scope
              tone="danger"
              role="alert"
              title="نوسازی پرونده مشتری ناموفق بود"
              :message="error"
            >
              <AppButton
                class="customer-detail-refresh-retry"
                size="sm"
                variant="secondary"
                :loading="isRefreshingRelations"
                @click="loadRelations(true)"
              >
                تلاش دوباره
              </AppButton>
            </WorkspaceNotice>
            <header class="customer-detail-header ui-v2-workspace-customer-detail-header">
              <div>
                <h2><CustomerNameWithBadge :name="getRelationTitle(activeRelation)" /></h2>
                <p>{{ getRelationDescription(activeRelation) }}</p>
              </div>
              <div class="customer-detail-badges ui-v2-workspace-customer-detail-badges">
                <AppStatusBadge :tone="getStatusTone(activeRelation.status)">
                  {{ getStatusLabel(activeRelation.status) }}
                </AppStatusBadge>
                <AppStatusBadge
                  :tone="activeRelation.customer_tier === 'tier2' ? 'primary' : 'neutral'"
                >
                  {{ getTierLabel(activeRelation.customer_tier) }}
                </AppStatusBadge>
              </div>
            </header>

            <div
              v-if="activeRelation.status === 'pending'"
              class="customer-pending-detail ui-v2-workspace-customer-pending-detail"
            >
              <AppCard
                tone="warning"
                class="customer-pending-card ui-v2-workspace-customer-pending-card"
              >
                <strong>دعوت در انتظار ثبت‌نام</strong>
                <p>{{ getRelationDescription(activeRelation) }}</p>
                <p class="customer-pending-deadline ui-v2-workspace-customer-pending-deadline">
                  {{ formatInvitationDeadline(activeRelation.expires_at) }}
                </p>
              </AppCard>
              <WorkspaceNotice
                v-if="invitationSmsStatusMessage(activeRelation.sms_status)"
                v2-scope
                tone="warning"
                title="وضعیت پیامک دعوت"
                :message="invitationSmsStatusMessage(activeRelation.sms_status)"
              />
              <WorkspaceNotice
                v-if="invitationFeedback[activeRelation.id]"
                v2-scope
                :tone="invitationFeedback[activeRelation.id]?.tone"
                title="بازخورد دعوت"
                :message="invitationFeedback[activeRelation.id]?.message"
              />
              <div
                class="customer-pending-detail__actions ui-v2-workspace-customer-pending-actions"
              >
                <AppButton
                  v-if="invitationRelationLink(activeRelation, 'web')"
                  variant="primary"
                  block
                  @click="copyRegistrationLink(activeRelation, 'web')"
                >
                  {{
                    copiedRelationId === activeRelation.id && copiedInvitationSurface === 'web'
                      ? 'کپی شد'
                      : 'کپی لینک وب‌اپ'
                  }}
                </AppButton>
                <AppButton
                  v-if="invitationRelationLink(activeRelation, 'bot')"
                  variant="secondary"
                  block
                  @click="copyRegistrationLink(activeRelation, 'bot')"
                >
                  {{
                    copiedRelationId === activeRelation.id && copiedInvitationSurface === 'bot'
                      ? 'کپی شد'
                      : 'کپی لینک تلگرام'
                  }}
                </AppButton>
              </div>
              <div class="customer-pending-detail__danger ui-v2-workspace-customer-pending-danger">
                <AppButton
                  variant="danger"
                  block
                  @click="openConfirmDialog('cancel-invitation', activeRelation)"
                >
                  لغو دعوت
                </AppButton>
              </div>
            </div>

            <AppTabs
              class="ui-v2-workspace-customer-detail-tabs"
              v-else
              v-model="detailTab"
              label="بخش‌های پرونده مشتری"
              :options="availableDetailTabOptions"
            />

            <WorkspaceNotice
              v-if="isTerminalRelation"
              v2-scope
              tone="info"
              title="رابطه فقط خواندنی است"
              message="این رابطه پایان یافته است؛ مشخصات و تاریخچه قابل مشاهده‌اند، اما ویرایش محدودیت‌ها، مدیریت نشست و حذف حساب در دسترس نیست."
            />

            <div
              v-if="activeRelation.status !== 'pending' && detailTab === 'profile'"
              class="customer-detail-grid ui-v2-workspace-customer-detail-grid"
            >
              <AppCard>
                <span class="customer-meta-label ui-v2-workspace-customer-meta-label"
                  >نام مدیریتی</span
                >
                <strong>
                  <CustomerNameWithBadge
                    v-if="activeRelation.management_name"
                    :name="activeRelation.management_name"
                    compact
                  />
                  <template v-else>ثبت نشده</template>
                </strong>
              </AppCard>
              <AppCard>
                <span class="customer-meta-label ui-v2-workspace-customer-meta-label"
                  >شماره موبایل</span
                >
                <strong>{{ activeRelation.mobile_number || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-meta-label ui-v2-workspace-customer-meta-label"
                  >وضعیت ثبت‌نام</span
                >
                <strong>{{
                  activeRelation.customer_user_id ? 'تکمیل شده' : 'در انتظار ثبت‌نام'
                }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-meta-label ui-v2-workspace-customer-meta-label"
                  >نرخ کمیسیون</span
                >
                <strong>{{
                  activeRelation.customer_tier === 'tier2'
                    ? formatPercent(activeRelation.commission_rate)
                    : 'ندارد'
                }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-meta-label ui-v2-workspace-customer-meta-label"
                  >فعال‌سازی</span
                >
                <strong>{{ formatDate(activeRelation.activated_at) }}</strong>
              </AppCard>
              <AppCard>
                <span class="customer-meta-label ui-v2-workspace-customer-meta-label"
                  >ایجاد رابطه</span
                >
                <strong>{{ formatDate(activeRelation.created_at) }}</strong>
              </AppCard>
            </div>

            <div
              v-else-if="canEditLimits && detailTab === 'limits'"
              class="customer-detail-list ui-v2-workspace-customer-detail-list"
            >
              <div class="customer-detail-grid ui-v2-workspace-customer-detail-grid">
                <AppListItem
                  v-for="item in activeRelationLimits"
                  :key="item.label"
                  :title="item.label"
                  :description="item.description"
                  :meta="item.value"
                />
              </div>

              <AppCard
                v-if="!isLimitsReviewOpen"
                class="customer-edit-form-card ui-v2-workspace-customer-edit-card"
              >
                <div class="customer-edit-form-grid ui-v2-workspace-customer-form-grid">
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
                      <AppInput
                        :id="id"
                        v-model="customerState.detailEditForm.min_trade_quantity"
                        placeholder="مثلاً ۱۰"
                      />
                    </template>
                  </AppFormField>

                  <AppFormField label="حداکثر مقدار معامله" hint="خالی بماند یعنی بدون محدودیت.">
                    <template #default="{ id }">
                      <AppInput
                        :id="id"
                        v-model="customerState.detailEditForm.max_trade_quantity"
                        placeholder="مثلاً ۵۰۰"
                      />
                    </template>
                  </AppFormField>

                  <AppFormField label="حداکثر تعداد روزانه" hint="خالی بماند یعنی بدون محدودیت.">
                    <template #default="{ id }">
                      <AppInput
                        :id="id"
                        v-model="customerState.detailEditForm.max_daily_trades"
                        placeholder="مثلاً ۴"
                      />
                    </template>
                  </AppFormField>

                  <AppFormField label="حداکثر حجم روزانه" hint="خالی بماند یعنی بدون محدودیت.">
                    <template #default="{ id }">
                      <AppInput
                        :id="id"
                        v-model="customerState.detailEditForm.max_daily_commodity_volume"
                        placeholder="مثلاً ۱۰۰۰"
                      />
                    </template>
                  </AppFormField>
                </div>

                <WorkspaceNotice
                  v-if="limitsError"
                  v2-scope
                  tone="danger"
                  title="ذخیره تنظیمات ناموفق بود"
                  :message="limitsError"
                />
                <WorkspaceNotice
                  v-else-if="limitsNotice"
                  v2-scope
                  tone="success"
                  title="تغییرات ذخیره شد"
                  :message="limitsNotice"
                />

                <div class="customer-inline-actions ui-v2-workspace-customer-inline-actions">
                  <AppButton variant="primary" :loading="isSavingLimits" @click="saveDetailLimits">
                    مرور تغییرات
                  </AppButton>
                </div>
              </AppCard>

              <AppCard
                v-else
                class="customer-financial-review ui-v2-workspace-customer-financial-review"
              >
                <header
                  class="customer-financial-review__heading ui-v2-workspace-customer-financial-heading"
                >
                  <strong>مرور تغییرات</strong>
                  <AppStatusBadge tone="success">فعال</AppStatusBadge>
                </header>
                <p>پیش از ثبت، مقدارهای قبل و بعد را بررسی کنید.</p>
                <table
                  class="customer-financial-review__table ui-v2-workspace-customer-financial-table"
                >
                  <caption class="ui-v2-workspace-customer-financial-caption">
                    مقایسه تنظیمات مالی قبل و بعد
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col">مورد</th>
                      <th scope="col">قبل</th>
                      <th scope="col">بعد</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="row in financialReviewRows" :key="row.key">
                      <th scope="row">{{ row.label }}</th>
                      <td data-label="قبل">{{ row.before }}</td>
                      <td data-label="بعد">{{ row.after }}</td>
                    </tr>
                  </tbody>
                </table>
                <WorkspaceNotice
                  v2-scope
                  tone="info"
                  title="اثر از این لحظه به بعد"
                  message="این تغییرها فقط روی معاملات آینده اثر دارند؛ تاریخچه تکمیل‌شده عوض نمی‌شود."
                />
                <WorkspaceNotice
                  v-if="limitsError"
                  v2-scope
                  tone="danger"
                  title="ثبت تغییرات ناموفق بود"
                  :message="limitsError"
                />
                <div
                  class="customer-financial-review__actions ui-v2-workspace-customer-financial-actions"
                >
                  <AppButton
                    variant="primary"
                    :loading="isSavingLimits"
                    @click="confirmDetailLimits"
                  >
                    ثبت تغییرات
                  </AppButton>
                  <AppButton
                    variant="secondary"
                    :disabled="isSavingLimits"
                    @click="returnToLimitsEdit"
                  >
                    بازگشت و اصلاح
                  </AppButton>
                </div>
              </AppCard>
            </div>

            <div
              v-else-if="canViewHistory && detailTab === 'trades'"
              class="customer-detail-list ui-v2-workspace-customer-detail-list"
            >
              <div class="customer-detail-toolbar ui-v2-workspace-customer-detail-toolbar">
                <strong>آخرین معاملات</strong>
                <AppButton
                  size="sm"
                  variant="secondary"
                  :loading="detailTradesLoading"
                  @click="loadDetailTrades(true)"
                >
                  نوسازی
                </AppButton>
              </div>
              <WorkspaceNotice
                v-if="detailTradesError"
                v2-scope
                tone="danger"
                role="alert"
                title="خطا در دریافت معاملات"
                :message="detailTradesError"
              >
                <AppButton
                  size="sm"
                  variant="secondary"
                  :loading="detailTradesLoading"
                  @click="loadDetailTrades(true)"
                  >تلاش دوباره</AppButton
                >
              </WorkspaceNotice>
              <AppLoadingState
                v-if="detailTradesLoading && !detailTrades.length"
                label="در حال دریافت ساختار معاملات"
              />
              <WorkspaceNotice
                v-if="!detailTradesLoading && !detailTradesError && !detailTrades.length"
                v2-scope
                tone="info"
                title="معامله‌ای ثبت نشده است"
                message="برای این مشتری هنوز معامله‌ای در بازه اخیر پیدا نشد."
              />
              <template v-if="detailTrades.length">
                <AppListItem
                  v-for="trade in detailTrades"
                  :key="trade.id"
                  :title="`${trade.commodity_name} - ${trade.trade_type}`"
                  :description="`${trade.counterparty_name || 'طرف مقابل نامشخص'} · ${tradeSettlementLabel(trade.settlement_type)} · ${formatDate(trade.created_at)}`"
                  :meta="`${Number(trade.quantity).toLocaleString('fa-IR')} × ${Number(trade.price).toLocaleString('fa-IR')}`"
                >
                  <template #leading>
                    <ReceiptText :size="18" />
                  </template>
                </AppListItem>
              </template>
            </div>

            <div
              v-else-if="canViewHistory && detailTab === 'stats'"
              class="customer-detail-list ui-v2-workspace-customer-detail-list"
            >
              <div
                class="customer-period-tabs ui-v2-workspace-customer-period-tabs"
                aria-label="بازه گزارش مشتری"
              >
                <button
                  v-for="days in [1, 3, 7, 30, 90, 180]"
                  :key="days"
                  type="button"
                  class="ui-v2-workspace-customer-period-tab"
                  :class="{
                    'is-active': statsPeriodDays === days,
                    'ui-v2-workspace-customer-period-tab--active': statsPeriodDays === days,
                  }"
                  @click="setStatsPeriod(days)"
                >
                  {{ days.toLocaleString('fa-IR') }} روز
                </button>
              </div>
              <WorkspaceNotice
                v-if="detailStatsError"
                v2-scope
                tone="danger"
                role="alert"
                title="خطا در دریافت آمار"
                :message="detailStatsError"
              >
                <AppButton
                  size="sm"
                  variant="secondary"
                  :loading="detailStatsLoading"
                  @click="loadDetailStats(true)"
                  >تلاش دوباره</AppButton
                >
              </WorkspaceNotice>
              <AppLoadingState
                v-if="detailStatsLoading && !detailStats"
                label="در حال آماده‌سازی ساختار آمار"
              />
              <div
                v-if="detailStats"
                class="customer-stats-grid ui-v2-workspace-customer-stats-grid"
              >
                <AppMetricCard label="تعداد معاملات" :value="detailStats.trade_count" />
                <AppMetricCard label="حجم کل" :value="detailStats.total_quantity" tone="primary" />
                <AppMetricCard
                  label="سود کمیسیون"
                  :value="formatToman(detailStats.commission_profit_toman)"
                  tone="success"
                />
                <AppCard
                  class="customer-stats-commodities ui-v2-workspace-customer-stats-commodities"
                >
                  <span class="customer-meta-label ui-v2-workspace-customer-meta-label"
                    >تفکیک کالا</span
                  >
                  <ul>
                    <li v-for="commodity in detailStats.commodities" :key="commodity.commodity_id">
                      <span>{{ commodity.commodity_name }}</span>
                      <strong>{{
                        Number(commodity.total_quantity).toLocaleString('fa-IR')
                      }}</strong>
                    </li>
                  </ul>
                </AppCard>
              </div>
              <WorkspaceNotice
                v-if="!detailStatsLoading && !detailStatsError && !detailStats"
                v2-scope
                tone="info"
                title="آماری در دسترس نیست"
                message="برای این مشتری هنوز گزارش قابل نمایش وجود ندارد."
              />
            </div>

            <div
              v-else-if="canManageSessions && detailTab === 'sessions'"
              class="customer-detail-list ui-v2-workspace-customer-detail-list"
            >
              <div class="customer-detail-toolbar ui-v2-workspace-customer-detail-toolbar">
                <strong>نشست‌های فعال مشتری</strong>
                <AppButton
                  size="sm"
                  variant="secondary"
                  :loading="detailSessionsLoading"
                  @click="loadDetailSessions(true)"
                >
                  نوسازی
                </AppButton>
              </div>
              <WorkspaceNotice
                v-if="sessionNotice"
                v2-scope
                tone="success"
                title="نشست پایان یافت"
                :message="sessionNotice"
              />
              <WorkspaceNotice
                v-if="detailSessionsError"
                v2-scope
                tone="danger"
                role="alert"
                title="خطا در دریافت نشست‌ها"
                :message="detailSessionsError"
              >
                <AppButton
                  size="sm"
                  variant="secondary"
                  :loading="detailSessionsLoading"
                  @click="loadDetailSessions(true)"
                  >تلاش دوباره</AppButton
                >
              </WorkspaceNotice>
              <AppLoadingState
                v-if="detailSessionsLoading && !detailSessions.length"
                label="در حال دریافت ساختار نشست‌ها"
              />
              <WorkspaceNotice
                v-else-if="!detailSessionsError && !detailSessions.length"
                v2-scope
                tone="info"
                title="نشست فعالی وجود ندارد"
                message="برای این مشتری نشست فعالی ثبت نشده است."
              />
              <template v-if="detailSessions.length">
                <AppListItem
                  v-for="session in detailSessions"
                  :key="session.id"
                  :title="session.device_name || session.platform || 'دستگاه بدون نام'"
                  :description="getSessionDescription(session)"
                >
                  <template #leading>
                    <Clock :size="18" />
                  </template>
                  <template #trailing>
                    <div class="customer-session-actions ui-v2-workspace-customer-session-actions">
                      <AppStatusBadge :tone="session.is_primary ? 'primary' : 'neutral'">
                        {{ session.is_primary ? 'اصلی' : 'فرعی' }}
                      </AppStatusBadge>
                      <AppButton
                        size="sm"
                        variant="secondary"
                        @click.stop="
                          openConfirmDialog('terminate-session', activeRelation, session)
                        "
                      >
                        پایان نشست
                      </AppButton>
                    </div>
                  </template>
                </AppListItem>
              </template>
            </div>

            <div
              v-else-if="(canDeleteAccount || canCloseRelationOnly) && detailTab === 'danger'"
              class="customer-detail-list ui-v2-workspace-customer-detail-list"
            >
              <AppDangerZone
                v-if="canDeleteAccount"
                title="اقدامات حساس مشتری"
                description="حذف حساب دائمی است؛ پیامدهای کامل و تأیید تقویت‌شده در گام بعد نمایش داده می‌شود."
              >
                <div class="customer-danger-card ui-v2-workspace-customer-danger-card">
                  <ShieldAlert :size="22" />
                  <div>
                    <strong>حذف حساب</strong>
                    <p>
                      این اقدام فقط قطع یک رابطه نیست؛ پیش از ادامه، پیامدهای حساب و حفظ تاریخچه
                      معاملات را دقیق بخوانید.
                    </p>
                  </div>
                </div>
                <div class="customer-inline-actions ui-v2-workspace-customer-inline-actions">
                  <AppButton variant="danger" @click="openAccountDeletionDialog(activeRelation)">
                    بررسی و حذف حساب
                  </AppButton>
                </div>
              </AppDangerZone>
              <AppDangerZone
                v-else
                title="بستن رابطه مشتری"
                description="برای این رابطه حساب فعال ثبت نشده است؛ این اقدام فقط خود رابطه را می‌بندد."
              >
                <div class="customer-danger-card ui-v2-workspace-customer-danger-card">
                  <ShieldAlert :size="22" />
                  <div>
                    <strong>بستن رابطه بدون حذف حساب</strong>
                    <p>
                      رزرو هویت مرتبط آزاد می‌شود؛ چون حساب زنده‌ای وجود ندارد، آبشار حذف حساب فعال
                      اجرا نمی‌شود.
                    </p>
                  </div>
                </div>
                <div class="customer-inline-actions ui-v2-workspace-customer-inline-actions">
                  <AppButton
                    variant="danger"
                    @click="openConfirmDialog('close-relation', activeRelation)"
                  >
                    بررسی و بستن رابطه
                  </AppButton>
                </div>
              </AppDangerZone>
            </div>
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          v-if="!hasDetailRoute || !isMobile"
          class="customer-list-section ui-v2-workspace-customer-list-section"
          title="لیست مشتریان"
          description="جستجو، فیلتر و انتخاب مشتری با دسترسی مستقیم به دعوت‌ها و پرونده‌های فعال."
          v2-scope
        >
          <template #actions>
            <div
              v-if="hasLoadedRelations && actionablePendingCount"
              class="workspace-summary-badges ui-v2-workspace-customer-summary-badges"
            >
              <AppStatusBadge tone="warning">
                {{ actionablePendingCount.toLocaleString('fa-IR') }} دعوت در انتظار اقدام
              </AppStatusBadge>
            </div>
          </template>
          <div class="customer-list-controls ui-v2-workspace-customer-list-controls">
            <AppSearchField
              v-model="searchQuery"
              label="جستجوی مشتری"
              placeholder="نام، شماره موبایل یا نام حساب را جستجو کنید."
            />
            <AppFilterChips
              class="ui-v2-workspace-customer-filter-chips"
              v-model="relationFilter"
              label="فیلتر مشتریان"
              :options="relationFilterOptions"
            />
          </div>

          <WorkspaceNotice
            v-if="error && (!hasDetailRoute || hasLoadedRelations)"
            v2-scope
            tone="danger"
            role="alert"
            title="خطا در دریافت مشتریان"
            :message="error"
          >
            <AppButton
              class="customer-list-retry"
              size="sm"
              variant="secondary"
              :loading="isLoading || isRefreshingRelations"
              @click="loadRelations(true)"
            >
              تلاش دوباره
            </AppButton>
          </WorkspaceNotice>
          <AppLoadingState
            v-if="isLoading && !hasDetailRoute"
            label="در حال دریافت ساختار فهرست مشتریان"
          />
          <WorkspaceNotice
            v-if="listActionNotice"
            v2-scope
            tone="success"
            title="اقدام انجام شد"
            :message="listActionNotice"
          />
          <AppEmptyState
            v-if="hasLoadedRelations && !customerState.orderedRelations.value.length"
            tone="info"
            title="هنوز مشتری ثبت نشده است"
            message="برای شروع، از دکمه افزودن مشتری استفاده کنید."
          >
            <template #actions>
              <AppButton variant="primary" @click="openCreatePanel">افزودن مشتری</AppButton>
            </template>
          </AppEmptyState>
          <AppEmptyState
            v-else-if="hasLoadedRelations && !filteredRelations.length"
            tone="info"
            title="نتیجه‌ای پیدا نشد"
            message="فیلتر یا عبارت جستجو را تغییر دهید."
          >
            <template #actions>
              <AppButton variant="secondary" @click="clearCustomerSearch"
                >پاک کردن جستجو و فیلتر</AppButton
              >
            </template>
          </AppEmptyState>
          <div
            v-if="hasLoadedRelations && filteredRelations.length"
            ref="relationListRef"
            class="workspace-relation-list ui-v2-workspace-customer-relation-list"
            @scroll.passive="handleListScroll"
          >
            <div
              v-if="visiblePendingRelations.length"
              class="customer-list-group ui-v2-workspace-customer-list-group"
            >
              <h3>دعوت‌های در انتظار</h3>
              <AppCard
                v-for="relation in visiblePendingRelations"
                :key="relation.id"
                tone="warning"
                class="customer-pending-card ui-v2-workspace-customer-pending-card"
              >
                <div class="customer-pending-card__header ui-v2-workspace-customer-pending-header">
                  <div>
                    <strong
                      ><CustomerNameWithBadge :name="getRelationTitle(relation)" compact
                    /></strong>
                    <p>{{ getRelationDescription(relation) }}</p>
                  </div>
                  <AppStatusBadge tone="warning">دعوت</AppStatusBadge>
                </div>
                <p class="customer-pending-deadline ui-v2-workspace-customer-pending-deadline">
                  {{ formatInvitationDeadline(relation.expires_at) }}
                </p>
                <div class="customer-inline-actions ui-v2-workspace-customer-inline-actions">
                  <AppButton size="sm" variant="primary" @click="openRelation(relation.id)">
                    بررسی دعوت
                  </AppButton>
                  <AppButton
                    v-if="invitationRelationLink(relation, 'bot')"
                    size="sm"
                    variant="secondary"
                    @click="copyRegistrationLink(relation, 'bot')"
                  >
                    <template #icon>
                      <Copy :size="16" />
                    </template>
                    {{
                      copiedRelationId === relation.id && copiedInvitationSurface === 'bot'
                        ? 'کپی شد'
                        : 'کپی لینک تلگرام'
                    }}
                  </AppButton>
                  <AppButton
                    v-if="invitationRelationLink(relation, 'web')"
                    size="sm"
                    variant="secondary"
                    @click="copyRegistrationLink(relation, 'web')"
                  >
                    <template #icon>
                      <Copy :size="16" />
                    </template>
                    {{
                      copiedRelationId === relation.id && copiedInvitationSurface === 'web'
                        ? 'کپی شد'
                        : 'کپی لینک وب'
                    }}
                  </AppButton>
                  <AppButton
                    size="sm"
                    variant="danger"
                    @click="openConfirmDialog('cancel-invitation', relation)"
                  >
                    لغو دعوت
                  </AppButton>
                </div>
                <WorkspaceNotice
                  v-if="invitationSmsStatusMessage(relation.sms_status)"
                  v2-scope
                  tone="warning"
                  title="وضعیت پیامک دعوت"
                  :message="invitationSmsStatusMessage(relation.sms_status)"
                />
                <WorkspaceNotice
                  v-if="invitationFeedback[relation.id]"
                  v2-scope
                  :tone="invitationFeedback[relation.id]?.tone"
                  title="بازخورد دعوت"
                  :message="invitationFeedback[relation.id]?.message"
                />
              </AppCard>
            </div>

            <div
              v-if="visibleManageableRelations.length"
              class="customer-list-group ui-v2-workspace-customer-list-group"
            >
              <h3>پرونده‌های مشتریان</h3>
              <AppListItem
                v-for="relation in visibleManageableRelations"
                :key="relation.id"
                :title="getRelationTitle(relation)"
                :description="getRelationDescription(relation)"
                interactive
                @select="openRelation(relation.id)"
              >
                <template #title>
                  <CustomerNameWithBadge :name="getRelationTitle(relation)" compact />
                </template>
                <template #leading>
                  <Users :size="18" />
                </template>
                <template #trailing>
                  <div class="customer-list-badges ui-v2-workspace-customer-list-badges">
                    <AppStatusBadge
                      :tone="
                        activeRelation?.id === relation.id
                          ? 'primary'
                          : getStatusTone(relation.status)
                      "
                    >
                      {{
                        activeRelation?.id === relation.id
                          ? 'انتخاب‌شده'
                          : getStatusLabel(relation.status)
                      }}
                    </AppStatusBadge>
                  </div>
                </template>
              </AppListItem>
            </div>
          </div>
        </WorkspaceSection>
      </div>

      <div id="customer-workspace-overlay-host" class="ui-v2-workspace-overlay-host" />

      <AppConfirmDialog
        class="ui-v2-workspace-confirm-backdrop"
        :open="isConfirmDialogOpen"
        :title="confirmTitle"
        :message="confirmMessage"
        :confirm-label="
          confirmAction === 'terminate-session'
            ? 'پایان همین نشست'
            : confirmAction === 'close-relation'
              ? 'بستن همین رابطه'
              : 'لغو رابطه و دعوت'
        "
        :tone="confirmAction === 'terminate-session' ? 'warning' : 'danger'"
        :busy="isConfirmBusy"
        :error="confirmError"
        :confirm-disabled="isConfirmBusy"
        @cancel="closeConfirmDialog"
        @confirm="handleConfirmAction"
      />

      <WorkspaceAccountDeletionDialog
        :open="isAccountDeletionDialogOpen"
        :subject-name="confirmRelation ? getRelationTitle(confirmRelation) : ''"
        :busy="isConfirmBusy"
        :error="confirmError"
        @cancel="closeAccountDeletionDialog"
        @confirm="handleConfirmAction"
      />
    </WorkspaceShell>

    <component
      :is="isMobile ? AppBottomSheet : AppResponsiveDialog"
      :open="isCreatePanelOpen"
      title="افزودن مشتری"
      description="اطلاعات اولیه مشتری و محدودیت‌های پایه را ثبت کنید."
      :show-close="!isCreateSubmitting"
      :close-on-backdrop="!isCreateSubmitting"
      :close-on-escape="!isCreateSubmitting"
      teleport-to="#customer-workspace-overlay-host"
      backdrop-class="ui-v2-workspace-overlay-backdrop"
      panel-class="ui-v2-workspace-overlay-panel"
      body-class="ui-v2-workspace-overlay-body"
      actions-class="ui-v2-workspace-overlay-actions"
      @close="closeCreatePanel"
    >
      <div class="customer-create-panel ui-v2-workspace-customer-create-panel">
        <fieldset
          class="ui-v2-workspace-customer-create-fieldset"
          :disabled="isCreateSubmitting"
          :aria-busy="isCreateSubmitting ? 'true' : undefined"
        >
          <legend>اطلاعات دعوت مشتری</legend>
          <AppFormField label="نام مدیریتی" hint="نامی که در فضای کاری خودتان می‌بینید.">
            <template #default="{ id }">
              <AppInput
                :id="id"
                v-model="customerState.createForm.management_name"
                placeholder="مثلاً حسن رضایی"
              />
            </template>
          </AppFormField>

          <AppFormField label="شماره موبایل" hint="برای ساخت حساب دعوتی و ثبت لینک استفاده می‌شود.">
            <template #default="{ id }">
              <AppInput
                :id="id"
                v-model="customerState.createForm.mobile_number"
                placeholder="0912xxxxxxx"
              />
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

          <div class="customer-edit-form-grid ui-v2-workspace-customer-form-grid">
            <AppFormField label="حداقل مقدار معامله" hint="خالی بماند یعنی بدون محدودیت.">
              <template #default="{ id }">
                <AppInput
                  :id="id"
                  v-model="customerState.createForm.min_trade_quantity"
                  placeholder="اختیاری"
                />
              </template>
            </AppFormField>
            <AppFormField label="حداکثر مقدار معامله" hint="خالی بماند یعنی بدون محدودیت.">
              <template #default="{ id }">
                <AppInput
                  :id="id"
                  v-model="customerState.createForm.max_trade_quantity"
                  placeholder="اختیاری"
                />
              </template>
            </AppFormField>
            <AppFormField label="حداکثر تعداد روزانه" hint="خالی بماند یعنی بدون محدودیت.">
              <template #default="{ id }">
                <AppInput
                  :id="id"
                  v-model="customerState.createForm.max_daily_trades"
                  placeholder="اختیاری"
                />
              </template>
            </AppFormField>
            <AppFormField label="حداکثر حجم روزانه" hint="خالی بماند یعنی بدون محدودیت.">
              <template #default="{ id }">
                <AppInput
                  :id="id"
                  v-model="customerState.createForm.max_daily_commodity_volume"
                  placeholder="اختیاری"
                />
              </template>
            </AppFormField>
          </div>

          <AppCard
            v-if="generatedCreateAccountName"
            class="customer-generated-account ui-v2-workspace-customer-generated-account"
          >
            <span class="customer-meta-label ui-v2-workspace-customer-meta-label">دعوت مشتری</span>
            <strong>آماده ثبت</strong>
          </AppCard>
        </fieldset>

        <WorkspaceNotice
          v-if="createError"
          v2-scope
          tone="danger"
          title="ثبت دعوت ناموفق بود"
          :message="createError"
        />
        <WorkspaceNotice
          v-else-if="createNotice"
          v2-scope
          tone="success"
          title="دعوت ثبت شد"
          :message="createNotice"
        />
      </div>

      <template #actions>
        <AppButton variant="secondary" :disabled="isCreateSubmitting" @click="closeCreatePanel">
          انصراف
        </AppButton>
        <AppButton variant="primary" :loading="isCreateSubmitting" @click="createRelation">
          ثبت دعوت مشتری
        </AppButton>
      </template>
    </component>
  </div>
</template>
