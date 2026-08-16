<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BriefcaseBusiness, Clock, Copy, ShieldAlert, UserPlus } from 'lucide-vue-next'
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
  AppErrorState,
  AppFilterChips,
  AppFormField,
  AppInput,
  AppListItem,
  AppLoadingState,
  AppResponsiveDialog,
  AppSearchField,
  AppStatusBadge,
  AppTabs,
  AppTextarea,
} from '../components/ui'
import {
  createOwnerAccountantRelation,
  deleteOwnerAccountantRelation,
  fetchOwnerAccountantRelation,
  fetchOwnerAccountantRelations,
  fetchOwnerAccountantSessions,
  normalizeDutyDescription,
  terminateOwnerAccountantSession,
  updateOwnerAccountantRelation,
  useOwnerAccountants,
  type AccountantRelation,
  type AccountantSessionSummary,
} from '../composables/useOwnerAccountants'
import { invitationRelationLink, invitationSmsStatusMessage } from '../utils/invitationContract'

const route = useRoute()
const router = useRouter()
const accountantState = useOwnerAccountants()
const isLoading = ref(false)
const isRefreshingRelations = ref(false)
const hasLoadedRelations = ref(false)
const isMobile = ref(typeof window !== 'undefined' ? window.innerWidth < 900 : false)
const error = ref('')
const isCreatePanelOpen = ref(false)
const isCreateSubmitting = ref(false)
const createError = ref('')
const createNotice = ref('')
const isSavingDuty = ref(false)
const dutyError = ref('')
const dutyNotice = ref('')
const copiedRelationId = ref<number | null>(null)
const relationFeedback = ref<Record<number, { tone: 'success' | 'danger'; message: string }>>({})
const listActionNotice = ref('')
const sessionActionNotice = ref('')
const isConfirmDialogOpen = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
type ConfirmAction =
  | 'terminate-session'
  | 'cancel-invitation'
  | 'delete-relation'
  | 'delete-account'
const confirmAction = ref<ConfirmAction | null>(null)
const confirmRelation = ref<AccountantRelation | null>(null)
const confirmSession = ref<AccountantSessionSummary | null>(null)
const isConfirmBusy = ref(false)
const confirmError = ref('')
const searchQuery = ref(normalizeSearchQuery(routeQueryValue('q')))
const relationFilter = ref(relationFilterOptionsValue(routeQueryValue('filter')))
const listScrollTop = ref(
  parseListScroll(routeQueryValue('scroll') ?? routeQueryValue('listScroll')),
)
const detailSessions = ref<AccountantSessionSummary[]>([])
const detailSessionsLoading = ref(false)
const detailSessionsError = ref('')
const detailSessionsLoadedKey = ref<string | null>(null)
let relationsRequestController: AbortController | null = null
let relationsRequestGeneration = 0
let relationsMutationRevision = 0
let detailSessionsController: AbortController | null = null
let detailSessionsRequestKey: string | null = null
let detailSessionsRequestGeneration = 0
let createRequestGeneration = 0
let createDraftRevision = 0
let dutyRequestGeneration = 0
let dutyDraftRevision = 0
let confirmRequestGeneration = 0
let confirmRouteGeneration = 0
let isApplyingRouteContext = false
let routeScrollTarget: HTMLElement | Window | null = null
let isDeleteNavigationPending = false

const relationFilterOptions = [
  { key: 'all', label: 'همه' },
  { key: 'active', label: 'فعال' },
  { key: 'pending', label: 'دعوت‌ها' },
  { key: 'inactive', label: 'غیرفعال' },
]

function routeQueryValue(key: string) {
  const value = route.query[key]
  return Array.isArray(value) ? (value[0] ?? null) : (value ?? null)
}

function normalizeSearchQuery(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function relationFilterOptionsValue(value: unknown) {
  const normalized = typeof value === 'string' ? value : ''
  return ['all', 'active', 'pending', 'inactive'].includes(normalized) ? normalized : 'all'
}

function parseListScroll(value: unknown) {
  const normalized = Number(value)
  return Number.isFinite(normalized) && normalized > 0 ? Math.floor(normalized) : 0
}

const detailTabOptions = [
  { key: 'profile', label: 'مشخصات' },
  { key: 'duty', label: 'شرح وظیفه' },
  { key: 'sessions', label: 'نشست‌ها' },
  { key: 'danger', label: 'حساس' },
]

const relationId = computed(() => {
  const value = route.params.relationId
  return Array.isArray(value) ? (value[0] ?? null) : (value ?? null)
})

const relationIdNumber = computed(() => {
  if (relationId.value == null || relationId.value === '') return null
  const normalized = Number(relationId.value)
  return Number.isInteger(normalized) && normalized > 0 ? normalized : null
})

const hasDetailRoute = computed(() => relationId.value != null && relationId.value !== '')

const legacyRouteValues = computed(() =>
  [routeQueryValue('panel'), routeQueryValue('section')].filter(
    (value): value is string => typeof value === 'string' && Boolean(value),
  ),
)

const activeRelation = computed(() => {
  const id = relationIdNumber.value
  if (id == null) return null
  return accountantState.relations.value.find((relation) => relation.id === id) ?? null
})
const activeRelationId = computed(() => activeRelation.value?.id ?? null)
const isActiveRelation = computed(() => activeRelation.value?.status === 'active')
const isPendingRelation = computed(() => activeRelation.value?.status === 'pending')
const activeAccountantUserId = computed(() => getLiveAccountantUserId(activeRelation.value))
const hasLiveAccount = computed(
  () => isActiveRelation.value && activeAccountantUserId.value != null,
)
const isOrphanActiveRelation = computed(() => isActiveRelation.value && !hasLiveAccount.value)
const isTerminalRelation = computed(
  () => Boolean(activeRelation.value) && !isActiveRelation.value && !isPendingRelation.value,
)
const availableDetailTabOptions = computed(() => {
  if (!hasLoadedRelations.value || !activeRelation.value) return detailTabOptions
  if (hasLiveAccount.value) return detailTabOptions
  if (isOrphanActiveRelation.value) {
    return detailTabOptions.filter((option) => option.key !== 'sessions')
  }
  if (isPendingRelation.value) {
    return detailTabOptions.filter((option) => option.key === 'profile' || option.key === 'danger')
  }
  return detailTabOptions.filter((option) => option.key === 'profile')
})

function getLiveAccountantUserId(relation: AccountantRelation | null | undefined) {
  const id = Number(relation?.accountant_user_id)
  return Number.isInteger(id) && id > 0 ? id : null
}

function requestedDetailTabFromRoute() {
  const canonicalTab = routeQueryValue('tab')
  if (detailTabOptions.some((option) => option.key === canonicalTab)) return canonicalTab
  const legacyTab = legacyRouteValues.value.find((value) =>
    detailTabOptions.some((option) => option.key === value),
  )
  return legacyTab || canonicalTab
}

function normalizedDetailTab(value: unknown) {
  const normalized = Array.isArray(value) ? value[0] : value
  if (typeof normalized !== 'string') return 'profile'
  if (!detailTabOptions.some((option) => option.key === normalized)) return 'profile'
  if (
    hasLoadedRelations.value &&
    activeRelation.value &&
    !availableDetailTabOptions.value.some((option) => option.key === normalized)
  ) {
    return 'profile'
  }
  if (hasLoadedRelations.value && hasDetailRoute.value && !activeRelation.value) return 'profile'
  return normalized
}

const detailTab = computed({
  get() {
    return normalizedDetailTab(requestedDetailTabFromRoute())
  },
  set(tab: string) {
    if (!availableDetailTabOptions.value.some((option) => option.key === tab)) return
    router.push({
      name: relationId.value ? 'operations-accountants-detail' : 'operations-accountants',
      params: relationId.value ? { relationId: String(relationId.value) } : {},
      query: buildCanonicalQuery({ tab }),
    })
  },
})

const filteredRelations = computed(() => {
  const query = searchQuery.value.trim().toLocaleLowerCase('fa-IR')
  return accountantState.orderedRelations.value.filter((relation) => {
    const filter = relationFilter.value
    if (filter === 'active' && relation.status !== 'active') return false
    if (filter === 'pending' && relation.status !== 'pending') return false
    if (filter === 'inactive' && (relation.status === 'active' || relation.status === 'pending'))
      return false
    if (!query) return true
    const haystack = [
      relation.relation_display_name,
      relation.accountant_account_name,
      relation.global_account_name,
      relation.mobile_number,
      relation.duty_description,
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

const generatedGlobalAccountName = computed(() => accountantState.createForm.account_name.trim())

function abortRelationsRequest() {
  relationsRequestGeneration += 1
  relationsRequestController?.abort()
  relationsRequestController = null
  isLoading.value = false
  isRefreshingRelations.value = false
}

function invalidateRelationsSnapshot() {
  relationsMutationRevision += 1
  abortRelationsRequest()
}

function isNotFoundError(error: unknown) {
  return Boolean(error && typeof error === 'object' && 'status' in error && error.status === 404)
}

async function loadRelations(force = false) {
  if ((isLoading.value || isRefreshingRelations.value) && !force) return
  const isInitialLoad = !hasLoadedRelations.value
  abortRelationsRequest()
  const requestGeneration = relationsRequestGeneration
  const capturedMutationRevision = relationsMutationRevision
  const capturedDetailId = relationIdNumber.value
  const controller = new AbortController()
  relationsRequestController = controller
  if (isInitialLoad) isLoading.value = true
  else isRefreshingRelations.value = true
  error.value = ''
  try {
    const relations = await fetchOwnerAccountantRelations({ signal: controller.signal })
    if (
      requestGeneration !== relationsRequestGeneration ||
      capturedMutationRevision !== relationsMutationRevision ||
      capturedDetailId !== relationIdNumber.value
    )
      return
    if (capturedDetailId && !relations.some((relation) => relation.id === capturedDetailId)) {
      try {
        const detailRelation = await fetchOwnerAccountantRelation(capturedDetailId, {
          signal: controller.signal,
        })
        if (
          requestGeneration !== relationsRequestGeneration ||
          capturedMutationRevision !== relationsMutationRevision ||
          capturedDetailId !== relationIdNumber.value
        )
          return
        if (detailRelation.id !== capturedDetailId) {
          throw new Error('پاسخ پرونده حسابدار معتبر نبود.')
        }
        relations.unshift(detailRelation)
      } catch (err: unknown) {
        if (
          isAbortError(err) ||
          requestGeneration !== relationsRequestGeneration ||
          capturedMutationRevision !== relationsMutationRevision ||
          capturedDetailId !== relationIdNumber.value
        )
          return
        if (!isNotFoundError(err)) throw err
      }
    }
    accountantState.relations.value = relations
    hasLoadedRelations.value = true
  } catch (err: unknown) {
    if (
      isAbortError(err) ||
      requestGeneration !== relationsRequestGeneration ||
      capturedMutationRevision !== relationsMutationRevision ||
      capturedDetailId !== relationIdNumber.value
    )
      return
    error.value =
      err instanceof Error && err.message ? err.message : 'دریافت لیست حسابداران ناموفق بود.'
  } finally {
    if (requestGeneration === relationsRequestGeneration) {
      isLoading.value = false
      isRefreshingRelations.value = false
      if (relationsRequestController === controller) relationsRequestController = null
    }
  }
}

function goToOperations() {
  router.push({ name: 'operations' })
}

function buildCanonicalQuery(options: { tab?: string; includeTab?: boolean } = {}) {
  const query: Record<string, string> = {}
  const normalizedSearch = searchQuery.value.trim()
  if (normalizedSearch) query.q = normalizedSearch
  if (relationFilter.value !== 'all') query.filter = relationFilter.value
  if (listScrollTop.value > 0) query.scroll = String(listScrollTop.value)
  const shouldIncludeTab = options.includeTab ?? hasDetailRoute.value
  const tab = options.tab ?? detailTab.value
  if (shouldIncludeTab && tab !== 'profile') query.tab = tab
  return query
}

function currentQueryIsCanonical(query: Record<string, string>) {
  const currentEntries = Object.entries(route.query)
  const expectedEntries = Object.entries(query)
  if (currentEntries.length !== expectedEntries.length) return false
  if (currentEntries.some(([, value]) => Array.isArray(value))) return false
  return expectedEntries.every(([key, value]) => routeQueryValue(key) === value)
}

function replaceCanonicalRouteQuery() {
  if (isDeleteNavigationPending) return
  const query = buildCanonicalQuery()
  if (currentQueryIsCanonical(query)) return
  const name = hasDetailRoute.value ? 'operations-accountants-detail' : 'operations-accountants'
  void router.replace({
    name,
    params: hasDetailRoute.value ? { relationId: String(relationId.value) } : {},
    query,
  })
}

function syncRouteContextFromQuery() {
  const nextSearch = normalizeSearchQuery(routeQueryValue('q'))
  const canonicalFilter = routeQueryValue('filter')
  const hasValidCanonicalFilter =
    typeof canonicalFilter === 'string' &&
    ['all', 'active', 'pending', 'inactive'].includes(canonicalFilter)
  const nextFilter =
    !hasValidCanonicalFilter && legacyRouteValues.value.includes('pending')
      ? 'pending'
      : relationFilterOptionsValue(canonicalFilter)
  const nextScroll = parseListScroll(routeQueryValue('scroll') ?? routeQueryValue('listScroll'))
  isApplyingRouteContext = true
  searchQuery.value = nextSearch
  relationFilter.value = nextFilter
  listScrollTop.value = nextScroll
  if (legacyRouteValues.value.includes('create')) openCreatePanel()
  void nextTick(() => {
    isApplyingRouteContext = false
  })
  void restorePageScroll()
  replaceCanonicalRouteQuery()
}

function capturePageScroll() {
  if (typeof window === 'undefined') return
  if (hasDetailRoute.value) return
  const scrollTop =
    routeScrollTarget instanceof HTMLElement
      ? routeScrollTarget.scrollTop
      : window.scrollY || document.documentElement.scrollTop || document.body.scrollTop || 0
  listScrollTop.value = Math.max(0, Math.floor(scrollTop))
}

async function restorePageScroll() {
  if (hasDetailRoute.value && isMobile.value) return
  await nextTick()
  if (routeScrollTarget instanceof HTMLElement) {
    routeScrollTarget.scrollTop = listScrollTop.value
    return
  }
  if (typeof window !== 'undefined') window.scrollTo(0, listScrollTop.value)
}

function resolveRouteScrollTarget() {
  if (typeof document === 'undefined') return null
  return document.querySelector<HTMLElement>('.app-route-scroll') || window
}

function openRelation(relationId: number) {
  capturePageScroll()
  router.push({
    name: 'operations-accountants-detail',
    params: { relationId: String(relationId) },
    query: buildCanonicalQuery({ includeTab: false }),
  })
}

async function backToList() {
  await router.push({
    name: 'operations-accountants',
    query: buildCanonicalQuery({ includeTab: false }),
  })
  if (routeScrollTarget instanceof HTMLElement) {
    routeScrollTarget.scrollTop = listScrollTop.value
  } else if (typeof window !== 'undefined') {
    window.scrollTo(0, listScrollTop.value)
  }
}

function handleBack() {
  if (relationId.value) {
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
  if (isCreateSubmitting.value) return
  isCreatePanelOpen.value = true
  createError.value = ''
  createNotice.value = ''
}

function closeCreatePanel() {
  if (isCreateSubmitting.value) return
  isCreatePanelOpen.value = false
}

function completeCreatePanel() {
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

function seedEditForm(
  relation: AccountantRelation | null,
  options: { resetFeedback?: boolean } = {},
) {
  const { resetFeedback = true } = options
  accountantState.editForm.duty_description = relation?.duty_description || ''
  if (resetFeedback) {
    dutyError.value = ''
    dutyNotice.value = ''
  }
}

function isAbortError(error: unknown) {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : Boolean(error && typeof error === 'object' && 'name' in error && error.name === 'AbortError')
}

function abortDetailSessionsRequest() {
  detailSessionsRequestGeneration += 1
  detailSessionsController?.abort()
  detailSessionsController = null
  detailSessionsRequestKey = null
  detailSessionsLoading.value = false
}

function isCurrentActiveRelation(relationId: number) {
  return activeRelation.value?.id === relationId && activeRelation.value.status === 'active'
}

function isCurrentLiveRelation(relationId: number, accountantUserId: number) {
  return isCurrentActiveRelation(relationId) && activeAccountantUserId.value === accountantUserId
}

function sessionRelationKey(relationId: number, accountantUserId: number) {
  return `${relationId}:${accountantUserId}`
}

async function loadDetailSessions(force = false) {
  const relation = activeRelation.value
  const accountantUserId = getLiveAccountantUserId(relation)
  if (!relation || relation.status !== 'active' || accountantUserId == null) {
    abortDetailSessionsRequest()
    detailSessions.value = []
    detailSessionsLoadedKey.value = null
    detailSessionsError.value = ''
    return
  }
  const capturedRelationId = relation.id
  const capturedAccountantUserId = accountantUserId
  const requestKey = sessionRelationKey(capturedRelationId, capturedAccountantUserId)
  if (!force && detailSessionsLoadedKey.value === requestKey) return
  if (!force && detailSessionsLoading.value && detailSessionsRequestKey === requestKey) return
  abortDetailSessionsRequest()
  const requestGeneration = detailSessionsRequestGeneration
  const controller = new AbortController()
  detailSessionsController = controller
  detailSessionsRequestKey = requestKey
  detailSessionsLoading.value = true
  detailSessionsError.value = ''
  try {
    const sessions = await fetchOwnerAccountantSessions(capturedRelationId, {
      signal: controller.signal,
    })
    if (
      requestGeneration !== detailSessionsRequestGeneration ||
      !isCurrentLiveRelation(capturedRelationId, capturedAccountantUserId)
    )
      return
    detailSessions.value = sessions
    detailSessionsLoadedKey.value = requestKey
  } catch (error: unknown) {
    if (
      isAbortError(error) ||
      requestGeneration !== detailSessionsRequestGeneration ||
      !isCurrentLiveRelation(capturedRelationId, capturedAccountantUserId)
    )
      return
    detailSessionsError.value =
      error instanceof Error && error.message
        ? error.message
        : 'دریافت نشست‌های حسابدار ناموفق بود.'
  } finally {
    if (
      requestGeneration === detailSessionsRequestGeneration &&
      isCurrentLiveRelation(capturedRelationId, capturedAccountantUserId)
    ) {
      detailSessionsLoading.value = false
      if (detailSessionsController === controller) {
        detailSessionsController = null
        detailSessionsRequestKey = null
      }
    }
  }
}

function refreshCurrentDetailTab() {
  if (detailTab.value === 'sessions') {
    void loadDetailSessions(false)
    return
  }
  abortDetailSessionsRequest()
}

function createDraftPayload() {
  return {
    account_name: normalizeIdentityText(accountantState.createForm.account_name),
    relation_display_name: accountantState.createForm.relation_display_name.trim(),
    mobile_number: normalizeIdentityText(accountantState.createForm.mobile_number),
    duty_description: normalizeDutyDescription(accountantState.createForm.duty_description),
  }
}

function normalizeIdentityText(value: unknown) {
  if (typeof value !== 'string') return ''
  const persianDigits = '۰۱۲۳۴۵۶۷۸۹'
  const arabicDigits = '٠١٢٣٤٥٦٧٨٩'
  return value
    .replace(/[۰-۹]/g, (digit) => String(persianDigits.indexOf(digit)))
    .replace(/[٠-٩]/g, (digit) => String(arabicDigits.indexOf(digit)))
    .trim()
    .toLocaleLowerCase('en-US')
}

function isValidCreateReceipt(
  created: AccountantRelation | null | undefined,
  payload: ReturnType<typeof createDraftPayload>,
) {
  if (!created || !Number.isInteger(created.id) || created.id <= 0 || created.status !== 'pending')
    return false
  const receiptAccountName = created.global_account_name || created.accountant_account_name
  if (normalizeIdentityText(receiptAccountName) !== normalizeIdentityText(payload.account_name))
    return false
  if (
    normalizeIdentityText(created.relation_display_name) !==
    normalizeIdentityText(payload.relation_display_name)
  )
    return false
  if (normalizeIdentityText(created.mobile_number) !== normalizeIdentityText(payload.mobile_number))
    return false
  return normalizeDutyDescription(created.duty_description || '') === payload.duty_description
}

async function createRelation() {
  if (isCreateSubmitting.value) return
  const payload = createDraftPayload()
  const capturedDraftRevision = createDraftRevision
  const requestGeneration = ++createRequestGeneration
  isCreateSubmitting.value = true
  createError.value = ''
  createNotice.value = ''
  try {
    const created = await createOwnerAccountantRelation(payload)
    if (requestGeneration !== createRequestGeneration) return
    if (!isValidCreateReceipt(created, payload)) {
      throw new Error('پاسخ ایجاد حسابدار معتبر نبود.')
    }
    invalidateRelationsSnapshot()
    accountantState.relations.value = [
      created,
      ...accountantState.relations.value.filter((item) => item.id !== created.id),
    ]
    hasLoadedRelations.value = true
    error.value = ''
    createNotice.value =
      invitationSmsStatusMessage(created.sms_status) || 'دعوت حسابدار با موفقیت ثبت شد.'
    if (capturedDraftRevision === createDraftRevision) {
      resetCreateForm()
      completeCreatePanel()
    }
  } catch (err: unknown) {
    if (
      requestGeneration !== createRequestGeneration ||
      capturedDraftRevision !== createDraftRevision
    )
      return
    createError.value =
      err instanceof Error && err.message ? err.message : 'ایجاد حسابدار ناموفق بود.'
  } finally {
    if (requestGeneration === createRequestGeneration) isCreateSubmitting.value = false
  }
}

async function saveDuty() {
  const relation = activeRelation.value
  if (!relation || relation.status !== 'active' || isSavingDuty.value) return
  const capturedRelationId = relation.id
  const capturedDraftRevision = dutyDraftRevision
  const requestGeneration = ++dutyRequestGeneration
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
    if (requestGeneration !== dutyRequestGeneration || !isCurrentActiveRelation(capturedRelationId))
      return
    if (
      !updated ||
      updated.id !== relation.id ||
      normalizeDutyDescription(updated.duty_description || '') !== normalizedDuty
    ) {
      throw new Error('پاسخ ویرایش حسابدار معتبر نبود.')
    }
    invalidateRelationsSnapshot()
    accountantState.relations.value = accountantState.relations.value.map((item) =>
      item.id === updated.id ? updated : item,
    )
    if (capturedDraftRevision === dutyDraftRevision) {
      seedEditForm(updated, { resetFeedback: false })
      dutyNotice.value = 'شرح وظیفه ذخیره شد.'
    } else {
      dutyNotice.value = 'تغییر قبلی ذخیره شد؛ پیش‌نویس جدید هنوز ذخیره نشده است.'
    }
  } catch (error: unknown) {
    if (requestGeneration !== dutyRequestGeneration || !isCurrentActiveRelation(capturedRelationId))
      return
    dutyError.value =
      error instanceof Error && error.message ? error.message : 'ذخیره شرح وظیفه ناموفق بود.'
  } finally {
    if (
      requestGeneration === dutyRequestGeneration &&
      isCurrentActiveRelation(capturedRelationId)
    ) {
      isSavingDuty.value = false
    }
  }
}

async function copyRegistrationLink(relation: AccountantRelation) {
  const link = invitationRelationLink(relation, 'web')
  if (!link) return
  try {
    await navigator.clipboard.writeText(link)
    copiedRelationId.value = relation.id
    relationFeedback.value = {
      ...relationFeedback.value,
      [relation.id]: { tone: 'success', message: 'لینک دعوت کپی شد.' },
    }
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        if (copiedRelationId.value === relation.id) copiedRelationId.value = null
      }, 1800)
    }
  } catch {
    relationFeedback.value = {
      ...relationFeedback.value,
      [relation.id]: { tone: 'danger', message: 'کپی لینک دعوت ممکن نشد؛ دوباره تلاش کنید.' },
    }
  }
}

function openConfirmDialog(
  kind: ConfirmAction,
  relation: AccountantRelation,
  session: AccountantSessionSummary | null = null,
) {
  if (isConfirmBusy.value) return
  if (kind === 'cancel-invitation' && relation.status !== 'pending') return
  const relationAccountantUserId = getLiveAccountantUserId(relation)
  if (
    (kind === 'terminate-session' || kind === 'delete-account') &&
    (relationAccountantUserId == null ||
      !isCurrentLiveRelation(relation.id, relationAccountantUserId))
  )
    return
  if (
    kind === 'delete-relation' &&
    (!isCurrentActiveRelation(relation.id) || relationAccountantUserId != null)
  )
    return
  confirmAction.value = kind
  confirmRelation.value = relation
  confirmSession.value = session
  confirmError.value = ''
  confirmTitle.value =
    kind === 'terminate-session'
      ? 'پایان نشست'
      : kind === 'cancel-invitation'
        ? 'لغو رابطه و دعوت حسابدار'
        : kind === 'delete-relation'
          ? `حذف رابطه ${getRelationTitle(relation)}`
          : `حذف حساب ${getRelationTitle(relation)}`
  confirmMessage.value =
    kind === 'terminate-session'
      ? `نشست «${session?.device_name || 'دستگاه حسابدار'}» پایان یابد؟ فقط دسترسی همین نشست قطع می‌شود؛ نشست‌های دیگر باقی می‌مانند و در صورت نیاز قدیمی‌ترین نشست فعال به‌عنوان نشست اصلی انتخاب می‌شود.`
      : kind === 'cancel-invitation'
        ? `رابطه و دعوت در انتظار «${getRelationTitle(relation)}» لغو شود؟ لینک ثبت‌نام بی‌اعتبار و رزرو هویت و نام کاربری آزاد می‌شود. چون حسابی فعال نشده، حذف زنجیره‌ای حساب، نشست، آفر یا روابط فعال اجرا نمی‌شود.`
        : kind === 'delete-relation'
          ? `رابطه «${getRelationTitle(relation)}» حذف شود؟ این رابطه به حساب کاربری فعالی متصل نیست؛ فقط همین رابطه حذف می‌شود و حذف زنجیره‌ای حساب، نشست، آفر، دعوت یا سایر روابط اجرا نمی‌شود.`
          : `با حذف حساب «${getRelationTitle(relation)}»، دسترسی وب و ربات پایان می‌یابد؛ همه نشست‌های فعال بسته و آفرهای فعال منقضی می‌شوند؛ دعوت‌های در انتظار مرتبط لغو و همه روابط باز مشتری یا حسابدارِ متعلق یا متصل بسته می‌شوند؛ حساب‌های وابسته فعالِ متعلق به این کاربر ممکن است به‌صورت بازگشتی حذف شوند. سابقه معاملات حفظ می‌شود.`
  isConfirmDialogOpen.value = true
}

function isCurrentConfirmOperation(
  generation: number,
  action: ConfirmAction,
  relationId: number,
  accountantUserId: number | null,
) {
  if (generation !== confirmRequestGeneration) return false
  if (confirmAction.value !== action || confirmRelation.value?.id !== relationId) return false
  if (action === 'cancel-invitation') {
    return accountantState.relations.value.some(
      (relation) => relation.id === relationId && relation.status === 'pending',
    )
  }
  if (action === 'delete-relation') {
    return isCurrentActiveRelation(relationId) && activeAccountantUserId.value == null
  }
  return accountantUserId != null && isCurrentLiveRelation(relationId, accountantUserId)
}

function closeConfirmDialog() {
  if (isConfirmBusy.value) return
  resetConfirmDialog()
}

function getSafeAccountDeletionError() {
  return 'حذف حساب تأیید نشد. اطلاعات رابطه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
}

function getSafeSessionTerminationError() {
  return 'پایان نشست تأیید نشد. اطلاعات نمایش‌داده‌شدهٔ نشست در این صفحه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
}

function getSafeRelationConfirmationError(action: 'cancel-invitation' | 'delete-relation') {
  return action === 'cancel-invitation'
    ? 'لغو رابطه و دعوت تأیید نشد. اطلاعات نمایش‌داده‌شدهٔ رابطه در این صفحه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
    : 'حذف رابطه تأیید نشد. اطلاعات نمایش‌داده‌شدهٔ رابطه در این صفحه بدون تغییر باقی ماند؛ وضعیت را دوباره بررسی کنید.'
}

function resetConfirmDialog() {
  isConfirmDialogOpen.value = false
  confirmAction.value = null
  confirmRelation.value = null
  confirmSession.value = null
  confirmError.value = ''
}

async function handleConfirmAction() {
  const relation = confirmRelation.value
  if (!relation || !confirmAction.value || isConfirmBusy.value) return
  const action = confirmAction.value
  const session = confirmSession.value
  const capturedRelationId = relation.id
  const capturedAccountantUserId = getLiveAccountantUserId(relation)
  const capturedConfirmRouteGeneration = confirmRouteGeneration
  const capturedRouteRelationId = relationIdNumber.value
  const shouldReturnToList = capturedRouteRelationId === capturedRelationId
  const requestGeneration = ++confirmRequestGeneration
  const shouldHoldCanonicalSync = action !== 'terminate-session' && shouldReturnToList
  if (shouldHoldCanonicalSync) isDeleteNavigationPending = true

  isConfirmBusy.value = true
  confirmError.value = ''

  try {
    if (action === 'terminate-session') {
      if (!session) throw new Error(getSafeSessionTerminationError())
      const receipt = await terminateOwnerAccountantSession(relation.id, session.id)
      if (
        !isCurrentConfirmOperation(
          requestGeneration,
          action,
          capturedRelationId,
          capturedAccountantUserId,
        )
      )
        return
      if (!receipt || receipt.terminated_session_id !== session.id) {
        throw new Error(getSafeSessionTerminationError())
      }
      detailSessions.value = detailSessions.value
        .filter((item) => item.id !== receipt.terminated_session_id)
        .map((item) => ({
          ...item,
          is_primary: receipt.promoted_primary_session_id === item.id ? true : item.is_primary,
        }))
      detailSessionsLoadedKey.value = sessionRelationKey(
        capturedRelationId,
        capturedAccountantUserId!,
      )
      sessionActionNotice.value = `نشست «${session.device_name || 'دستگاه حسابدار'}» پایان یافت.`
      isConfirmBusy.value = false
      resetConfirmDialog()
      return
    }

    const expectedAction =
      action === 'cancel-invitation'
        ? 'cancel-pending'
        : action === 'delete-relation'
          ? 'delete-relation'
          : 'delete-account'
    const receipt = await deleteOwnerAccountantRelation(
      relation.id,
      expectedAction,
      action === 'cancel-invitation'
        ? 'لغو رابطه و دعوت حسابدار ناموفق بود.'
        : action === 'delete-relation'
          ? 'حذف رابطه حسابدار ناموفق بود.'
          : 'حذف حساب حسابدار ناموفق بود.',
    )
    const expectedStatus = action === 'cancel-invitation' ? 'revoked' : 'deleted'
    if (!receipt || receipt.id !== relation.id || receipt.status !== expectedStatus) {
      throw new Error(
        action === 'cancel-invitation'
          ? 'پاسخ لغو رابطه و دعوت حسابدار معتبر نبود.'
          : action === 'delete-relation'
            ? 'پاسخ حذف رابطه حسابدار معتبر نبود.'
            : 'پاسخ حذف حساب حسابدار معتبر نبود.',
      )
    }
    const shouldApplyContext =
      capturedConfirmRouteGeneration === confirmRouteGeneration &&
      relationIdNumber.value === capturedRouteRelationId
    invalidateRelationsSnapshot()
    accountantState.relations.value = accountantState.relations.value.filter(
      (item) => item.id !== relation.id,
    )
    if (!shouldApplyContext) return
    listActionNotice.value =
      action === 'cancel-invitation'
        ? `رابطه و دعوت «${getRelationTitle(relation)}» لغو و رزرو هویت آزاد شد.`
        : action === 'delete-relation'
          ? `رابطه «${getRelationTitle(relation)}» حذف شد.`
          : `حساب «${getRelationTitle(relation)}» حذف شد.`
    isConfirmBusy.value = false
    resetConfirmDialog()
    if (shouldReturnToList) {
      await backToList()
    }
  } catch {
    if (
      !isCurrentConfirmOperation(
        requestGeneration,
        action,
        capturedRelationId,
        capturedAccountantUserId,
      )
    )
      return
    if (action === 'delete-account') {
      confirmError.value = getSafeAccountDeletionError()
    } else if (action === 'terminate-session') {
      confirmError.value = getSafeSessionTerminationError()
    } else {
      confirmError.value = getSafeRelationConfirmationError(action)
    }
  } finally {
    if (shouldHoldCanonicalSync) isDeleteNavigationPending = false
    if (requestGeneration === confirmRequestGeneration) isConfirmBusy.value = false
  }
}

function getRelationTitle(relation: AccountantRelation) {
  return (
    relation.relation_display_name ||
    relation.accountant_account_name ||
    relation.global_account_name ||
    'حسابدار'
  )
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

function invitationDeadlineText(relation: AccountantRelation) {
  return relation.expires_at
    ? `مهلت استفاده تا ${formatDate(relation.expires_at)}`
    : 'مهلت استفاده برای این دعوت ثبت نشده است.'
}

function sessionPlatformLabel(platform: string | null | undefined) {
  if (platform === 'web') return 'وب'
  if (platform === 'telegram') return 'ربات'
  if (platform === 'mobile') return 'موبایل'
  return 'دستگاه حسابدار'
}

function clearListContext() {
  searchQuery.value = ''
  relationFilter.value = 'all'
  listScrollTop.value = 0
  if (routeScrollTarget instanceof HTMLElement) {
    routeScrollTarget.scrollTop = 0
  } else if (typeof window !== 'undefined') {
    window.scrollTo(0, 0)
  }
}

watch(
  [
    relationId,
    activeRelationId,
    () => activeRelation.value?.status ?? null,
    activeAccountantUserId,
  ],
  (
    [nextRawId, nextRelationId, nextStatus, nextUserId],
    [previousRawId, previousRelationId, previousStatus, previousUserId],
  ) => {
    if (
      nextRawId === previousRawId &&
      nextRelationId === previousRelationId &&
      nextStatus === previousStatus &&
      nextUserId === previousUserId
    )
      return
    abortDetailSessionsRequest()
    detailSessions.value = []
    detailSessionsLoadedKey.value = null
    detailSessionsError.value = ''
    sessionActionNotice.value = ''
    dutyRequestGeneration += 1
    isSavingDuty.value = false
    confirmRequestGeneration += 1
    isConfirmBusy.value = false
    if (isConfirmDialogOpen.value) resetConfirmDialog()
    refreshCurrentDetailTab()
  },
  { flush: 'post' },
)

watch(
  detailTab,
  () => {
    refreshCurrentDetailTab()
  },
  { flush: 'post' },
)

watch(relationIdNumber, (nextRelationId, previousRelationId) => {
  if (nextRelationId === previousRelationId) return
  abortRelationsRequest()
  if (!nextRelationId) return
  if (accountantState.relations.value.some((relation) => relation.id === nextRelationId)) return
  void loadRelations(true)
})

watch(relationId, (nextRelationId, previousRelationId) => {
  if (nextRelationId !== previousRelationId) confirmRouteGeneration += 1
})

watch(
  [
    relationId,
    () => route.query.q,
    () => route.query.filter,
    () => route.query.scroll,
    () => route.query.listScroll,
    () => route.query.tab,
    () => route.query.panel,
    () => route.query.section,
    hasLoadedRelations,
    () => activeRelation.value?.status ?? null,
    activeAccountantUserId,
  ],
  () => syncRouteContextFromQuery(),
  { immediate: true },
)

watch([searchQuery, relationFilter, listScrollTop], () => {
  if (!isApplyingRouteContext) replaceCanonicalRouteQuery()
})

watch(hasLoadedRelations, (loaded) => {
  if (loaded) void restorePageScroll()
})

watch(
  () => [
    accountantState.createForm.account_name,
    accountantState.createForm.relation_display_name,
    accountantState.createForm.mobile_number,
    accountantState.createForm.duty_description,
  ],
  () => {
    createDraftRevision += 1
  },
  { flush: 'sync' },
)

watch(
  () => accountantState.editForm.duty_description,
  () => {
    dutyDraftRevision += 1
  },
  { flush: 'sync' },
)

watch(
  activeRelation,
  (relation, previousRelation) => {
    if (relation?.id === previousRelation?.id) return
    seedEditForm(relation, {
      resetFeedback: relation?.id !== previousRelation?.id,
    })
  },
  { immediate: true },
)

onMounted(() => {
  updateIsMobile()
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', updateIsMobile)
    routeScrollTarget = resolveRouteScrollTarget()
    routeScrollTarget?.addEventListener('scroll', capturePageScroll, { passive: true })
  }
  void loadRelations()
  void restorePageScroll()
})

onBeforeUnmount(() => {
  abortRelationsRequest()
  abortDetailSessionsRequest()
  createRequestGeneration += 1
  dutyRequestGeneration += 1
  confirmRequestGeneration += 1
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
  <div class="ds-page accountant-workspace-view">
    <WorkspaceShell
      class="ui-v2-workspace-accountant-root"
      title="حسابداران"
      eyebrow="عملیات"
      description="افزودن، مرور و تنظیم روابط حسابداران در یک فضای کاری یکپارچه."
      layout="split"
      v2-scope
      show-back
      back-label="بازگشت"
      @back="handleBack"
    >
      <template #actions>
        <AppButton variant="primary" class="accountant-workspace-create" @click="openCreatePanel">
          <template #icon>
            <UserPlus :size="16" />
          </template>
          افزودن حسابدار
        </AppButton>
      </template>

      <WorkspaceNotice
        v-if="createNotice"
        class="accountant-global-create-notice"
        v2-scope
        tone="success"
        title="دعوت حسابدار ثبت شد"
        :message="createNotice"
      />

      <WorkspaceNotice
        v-if="error && hasLoadedRelations"
        class="accountant-global-refresh-error"
        v2-scope
        tone="danger"
        role="alert"
        title="به‌روزرسانی حسابداران انجام نشد"
        :message="error"
      >
        <AppButton
          class="accountant-list-retry"
          size="sm"
          variant="secondary"
          :loading="isRefreshingRelations"
          @click="loadRelations"
        >
          تلاش دوباره
        </AppButton>
      </WorkspaceNotice>

      <div class="accountant-stage5-layout ui-v2-workspace-accountant-layout">
        <WorkspaceSection
          v-if="!isMobile || hasDetailRoute"
          class="accountant-detail-section ui-v2-workspace-accountant-detail-section"
          title="پرونده حسابدار"
          description="مشخصات، شرح وظیفه، نشست‌ها و اقدامات حساس در یک نمای یکپارچه."
          v2-scope
        >
          <AppEmptyState
            v-if="!hasDetailRoute"
            title="حسابداری انتخاب نشده است"
            message="برای دیدن پرونده و تنظیمات، یکی از حسابداران را از فهرست انتخاب کنید."
            role="status"
          />
          <AppErrorState
            v-else-if="!hasLoadedRelations && error"
            title="دریافت پرونده حسابدار ممکن نشد"
            :message="error"
          >
            <template #actions>
              <AppButton
                class="accountant-detail-retry"
                size="sm"
                variant="secondary"
                :loading="isLoading"
                @click="loadRelations"
              >
                تلاش دوباره
              </AppButton>
              <AppButton size="sm" variant="secondary" @click="backToList">
                بازگشت به فهرست
              </AppButton>
            </template>
          </AppErrorState>
          <AppLoadingState
            v-else-if="!hasLoadedRelations && isLoading"
            class="accountant-detail-loading"
            label="در حال ساخت پرونده حسابدار"
          />
          <AppEmptyState
            v-else-if="!activeRelation && hasLoadedRelations"
            title="حسابدار پیدا نشد"
            message="این رابطه دیگر در دسترس نیست. به فهرست برگردید و یک حسابدار معتبر انتخاب کنید."
            tone="warning"
            role="status"
          >
            <template #actions>
              <AppButton size="sm" variant="secondary" @click="backToList"
                >بازگشت به فهرست</AppButton
              >
            </template>
          </AppEmptyState>
          <div
            v-else-if="activeRelation"
            class="accountant-detail-shell ui-v2-workspace-accountant-detail-shell"
          >
            <header class="accountant-detail-header ui-v2-workspace-accountant-detail-header">
              <div>
                <h2>{{ getRelationTitle(activeRelation) }}</h2>
                <p>{{ getRelationDescription(activeRelation) }}</p>
              </div>
              <div class="accountant-detail-badges ui-v2-workspace-accountant-detail-badges">
                <AppStatusBadge :tone="getStatusTone(activeRelation.status)">
                  {{ getStatusLabel(activeRelation.status) }}
                </AppStatusBadge>
              </div>
            </header>

            <WorkspaceNotice
              v-if="isTerminalRelation"
              v2-scope
              tone="info"
              title="این رابطه پایان یافته است"
              message="اطلاعات برای مراجعه نمایش داده می‌شود و اقدام مدیریتی دیگری روی این رابطه ممکن نیست."
            />
            <WorkspaceNotice
              v-else-if="isPendingRelation"
              v2-scope
              tone="warning"
              title="دعوت هنوز فعال نشده است"
              message="تا پیش از ثبت‌نام فقط مشخصات دعوت و امکان لغو آن در دسترس است."
            />
            <WorkspaceNotice
              v-else-if="isOrphanActiveRelation"
              v2-scope
              tone="warning"
              title="حساب کاربری متصل در دسترس نیست"
              message="این رابطه فعال ثبت شده اما به حساب کاربری زنده‌ای متصل نیست؛ نشست‌ها و حذف زنجیره‌ای حساب در دسترس نیستند."
            />

            <AppTabs
              class="ui-v2-workspace-accountant-detail-tabs"
              v-model="detailTab"
              label="بخش‌های پرونده حسابدار"
              :options="availableDetailTabOptions"
              reveal-selection-on-keyboard
            />

            <div
              v-if="detailTab === 'profile'"
              class="accountant-detail-grid ui-v2-workspace-accountant-detail-grid"
            >
              <AppCard>
                <span class="accountant-meta-label ui-v2-workspace-accountant-meta-label"
                  >نام نمایشی</span
                >
                <strong>{{ activeRelation.relation_display_name || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-meta-label ui-v2-workspace-accountant-meta-label"
                  >شماره موبایل</span
                >
                <strong>{{ activeRelation.mobile_number || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-meta-label ui-v2-workspace-accountant-meta-label"
                  >حساب کاربری</span
                >
                <strong>{{
                  activeRelation.accountant_account_name ||
                  activeRelation.global_account_name ||
                  'در انتظار ثبت‌نام'
                }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-meta-label ui-v2-workspace-accountant-meta-label"
                  >نام کاربری جهانی</span
                >
                <strong>@{{ activeRelation.global_account_name || 'ثبت نشده' }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-meta-label ui-v2-workspace-accountant-meta-label"
                  >فعال‌سازی</span
                >
                <strong>{{ formatDate(activeRelation.activated_at) }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-meta-label ui-v2-workspace-accountant-meta-label"
                  >ایجاد رابطه</span
                >
                <strong>{{ formatDate(activeRelation.created_at) }}</strong>
              </AppCard>
            </div>

            <div
              v-else-if="detailTab === 'duty' && isActiveRelation"
              class="accountant-detail-list ui-v2-workspace-accountant-detail-list"
            >
              <AppCard class="accountant-edit-form-card ui-v2-workspace-accountant-edit-form-card">
                <AppFormField
                  label="شرح وظیفه"
                  hint="یک توضیح کوتاه برای تفکیک مسئولیت این حسابدار بنویسید."
                >
                  <template #default="{ id }">
                    <AppTextarea
                      :id="id"
                      v-model="accountantState.editForm.duty_description"
                      rows="4"
                      :disabled="isSavingDuty"
                      :placeholder="
                        activeRelation.duty_description ||
                        'مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه'
                      "
                    />
                  </template>
                </AppFormField>

                <WorkspaceNotice
                  v-if="dutyError"
                  v2-scope
                  tone="danger"
                  title="ذخیره شرح وظیفه ناموفق بود"
                  :message="dutyError"
                />
                <WorkspaceNotice
                  v-else-if="dutyNotice"
                  v2-scope
                  tone="success"
                  title="وضعیت شرح وظیفه"
                  :message="dutyNotice"
                />

                <div class="accountant-inline-actions ui-v2-workspace-accountant-inline-actions">
                  <AppButton
                    variant="secondary"
                    :disabled="isSavingDuty"
                    @click="
                      accountantState.editForm.duty_description =
                        activeRelation.duty_description || ''
                    "
                  >
                    بازنشانی
                  </AppButton>
                  <AppButton
                    variant="primary"
                    :loading="isSavingDuty"
                    :disabled="isSavingDuty"
                    @click="saveDuty"
                  >
                    ذخیره تغییرات
                  </AppButton>
                </div>
              </AppCard>
            </div>

            <div
              v-else-if="detailTab === 'sessions' && hasLiveAccount"
              class="accountant-detail-list ui-v2-workspace-accountant-detail-list"
            >
              <div class="accountant-detail-toolbar ui-v2-workspace-accountant-detail-toolbar">
                <strong>نشست‌های فعال حسابدار</strong>
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
                v-if="sessionActionNotice"
                v2-scope
                tone="success"
                title="نشست پایان یافت"
                :message="sessionActionNotice"
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
                label="در حال ساخت فهرست نشست‌ها"
              />
              <AppEmptyState
                v-else-if="!detailSessionsError && !detailSessions.length"
                title="نشست فعالی وجود ندارد"
                message="برای این حسابدار نشست فعالی ثبت نشده است."
                role="status"
              />
              <template v-if="detailSessions.length">
                <AppListItem
                  v-for="session in detailSessions"
                  :key="session.id"
                  :title="session.device_name || session.platform || 'دستگاه بدون نام'"
                  :description="`${sessionPlatformLabel(session.platform)} · آخرین فعالیت ${formatDate(session.last_active_at)}`"
                >
                  <template #leading>
                    <Clock :size="18" />
                  </template>
                  <template #trailing>
                    <div
                      class="accountant-session-actions ui-v2-workspace-accountant-session-actions"
                    >
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
              v-else-if="detailTab === 'danger' && (isActiveRelation || isPendingRelation)"
              class="accountant-detail-list ui-v2-workspace-accountant-detail-list"
            >
              <AppDangerZone
                :title="
                  isPendingRelation
                    ? 'لغو رابطه و دعوت حسابدار'
                    : isOrphanActiveRelation
                      ? 'حذف رابطه حسابدار'
                      : 'حذف حساب حسابدار'
                "
                :description="
                  activeRelation.status === 'pending'
                    ? 'رابطه و دعوت در انتظار را لغو و رزرو هویت را آزاد کنید.'
                    : isOrphanActiveRelation
                      ? 'این رابطه حساب کاربری زنده‌ای ندارد و بدون حذف زنجیره‌ای حساب برداشته می‌شود.'
                      : 'حذف حساب یک اقدام بازگشت‌ناپذیر با پیامدهای امنیتی و تجاری است.'
                "
              >
                <div class="accountant-danger-card ui-v2-workspace-accountant-danger-card">
                  <ShieldAlert :size="22" />
                  <div>
                    <strong>
                      {{
                        activeRelation.status === 'pending'
                          ? 'لغو رابطه و دعوت حسابدار'
                          : isOrphanActiveRelation
                            ? `حذف رابطه ${getRelationTitle(activeRelation)}`
                            : `حذف حساب ${getRelationTitle(activeRelation)}`
                      }}
                    </strong>
                    <p>
                      {{
                        activeRelation.status === 'pending'
                          ? 'رابطه و دعوت در انتظار لغو، لینک ثبت‌نام بی‌اعتبار و رزرو هویت و نام کاربری آزاد می‌شود. چون حسابی فعال نشده، حذف زنجیره‌ای حساب، نشست، آفر یا روابط فعال اجرا نمی‌شود.'
                          : isOrphanActiveRelation
                            ? 'فقط همین رابطه حذف می‌شود؛ حساب، نشست، آفر، دعوت یا روابط دیگری به‌صورت زنجیره‌ای حذف نمی‌شوند.'
                            : 'دسترسی وب و ربات پایان می‌یابد؛ همه نشست‌های فعال پایان می‌یابند؛ آفرهای فعال منقضی و دعوت‌های در انتظار مرتبط لغو می‌شوند؛ همه روابط باز مشتری یا حسابدارِ متعلق یا متصل بسته می‌شوند؛ حساب‌های وابسته فعالِ متعلق ممکن است به‌صورت بازگشتی حذف شوند و سابقه معاملات حفظ می‌شود.'
                      }}
                    </p>
                  </div>
                </div>
                <div class="accountant-inline-actions ui-v2-workspace-accountant-inline-actions">
                  <AppButton
                    variant="danger"
                    @click="
                      openConfirmDialog(
                        activeRelation.status === 'pending'
                          ? 'cancel-invitation'
                          : isOrphanActiveRelation
                            ? 'delete-relation'
                            : 'delete-account',
                        activeRelation,
                      )
                    "
                  >
                    {{
                      activeRelation.status === 'pending'
                        ? 'لغو رابطه و دعوت'
                        : isOrphanActiveRelation
                          ? 'حذف رابطه'
                          : 'حذف حساب'
                    }}
                  </AppButton>
                </div>
              </AppDangerZone>
            </div>
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          v-if="!isMobile || !hasDetailRoute"
          class="accountant-list-section ui-v2-workspace-accountant-list-section"
          title="لیست حسابداران"
          description="جستجو، فیلتر و انتخاب حسابدار با دسترسی مستقیم به دعوت‌ها و پرونده‌های فعال."
          v2-scope
        >
          <template #actions>
            <div
              v-if="hasLoadedRelations && accountantState.pendingInvitationRelations.value.length"
              class="workspace-summary-badges ui-v2-workspace-accountant-summary-badges"
            >
              <AppStatusBadge tone="warning">
                {{
                  accountantState.pendingInvitationRelations.value.length.toLocaleString('fa-IR')
                }}
                دعوت نیازمند اقدام
              </AppStatusBadge>
            </div>
          </template>
          <div class="accountant-list-controls ui-v2-workspace-accountant-list-controls">
            <AppSearchField
              v-model="searchQuery"
              label="جستجوی حسابدار"
              placeholder="نام، شماره موبایل، حساب یا شرح وظیفه را جستجو کنید."
            />
            <AppFilterChips
              class="ui-v2-workspace-accountant-filter-chips"
              v-model="relationFilter"
              label="فیلتر حسابداران"
              focus-selection-on-keyboard
              :options="relationFilterOptions"
            />
          </div>

          <WorkspaceNotice
            v-if="listActionNotice"
            v2-scope
            tone="success"
            title="اقدام حسابدار انجام شد"
            :message="listActionNotice"
          />

          <AppErrorState
            v-if="error && !hasLoadedRelations"
            title="دریافت حسابداران ممکن نشد"
            :message="error"
          >
            <template #actions>
              <AppButton
                class="accountant-list-retry"
                size="sm"
                variant="secondary"
                :loading="isLoading"
                @click="loadRelations"
              >
                تلاش دوباره
              </AppButton>
            </template>
          </AppErrorState>
          <AppLoadingState
            v-if="isLoading && !hasLoadedRelations"
            class="accountant-list-loading"
            label="در حال ساخت فهرست حسابداران"
          />
          <AppEmptyState
            v-if="hasLoadedRelations && !accountantState.orderedRelations.value.length"
            title="هنوز حسابداری ثبت نشده است"
            message="برای شروع، از دکمه افزودن حسابدار استفاده کنید."
            role="status"
          >
            <template #actions>
              <AppButton size="sm" variant="primary" @click="openCreatePanel"
                >افزودن حسابدار</AppButton
              >
            </template>
          </AppEmptyState>
          <AppEmptyState
            v-else-if="hasLoadedRelations && !filteredRelations.length"
            title="نتیجه‌ای پیدا نشد"
            message="فیلتر یا عبارت جستجو را پاک کنید تا فهرست کامل برگردد."
            role="status"
          >
            <template #actions>
              <AppButton size="sm" variant="secondary" @click="clearListContext"
                >پاک‌کردن جستجو و فیلتر</AppButton
              >
            </template>
          </AppEmptyState>
          <div
            v-if="hasLoadedRelations && filteredRelations.length"
            class="workspace-relation-list accountant-scroll-restoration-region ui-v2-workspace-accountant-relation-list ui-v2-workspace-accountant-scroll-region"
          >
            <div
              v-if="visiblePendingRelations.length"
              class="accountant-list-group ui-v2-workspace-accountant-list-group"
            >
              <h3>دعوت‌های در انتظار</h3>
              <AppCard
                v-for="relation in visiblePendingRelations"
                :key="relation.id"
                tone="warning"
                class="accountant-pending-card"
              >
                <div
                  class="accountant-pending-card__header ui-v2-workspace-accountant-pending-card-header"
                >
                  <div>
                    <strong>{{ getRelationTitle(relation) }}</strong>
                    <p>{{ getRelationDescription(relation) }}</p>
                  </div>
                  <AppStatusBadge tone="warning">دعوت</AppStatusBadge>
                </div>
                <p class="accountant-pending-deadline ui-v2-workspace-accountant-pending-deadline">
                  {{ invitationDeadlineText(relation) }}
                </p>
                <div class="accountant-inline-actions ui-v2-workspace-accountant-inline-actions">
                  <AppButton
                    v-if="invitationRelationLink(relation, 'web')"
                    size="sm"
                    variant="secondary"
                    @click="copyRegistrationLink(relation)"
                  >
                    <template #icon>
                      <Copy :size="16" />
                    </template>
                    {{ copiedRelationId === relation.id ? 'کپی شد' : 'کپی لینک وب' }}
                  </AppButton>
                  <AppButton
                    size="sm"
                    variant="danger"
                    @click="openConfirmDialog('cancel-invitation', relation)"
                  >
                    لغو دعوت
                  </AppButton>
                </div>
                <p
                  v-if="invitationSmsStatusMessage(relation.sms_status)"
                  class="accountant-pending-sms-status ui-v2-workspace-accountant-pending-sms-status"
                >
                  {{ invitationSmsStatusMessage(relation.sms_status) }}
                </p>
                <WorkspaceNotice
                  v-if="relationFeedback[relation.id]"
                  v2-scope
                  :tone="relationFeedback[relation.id]?.tone"
                  :title="
                    relationFeedback[relation.id]?.tone === 'success' ? 'انجام شد' : 'کپی انجام نشد'
                  "
                  :message="relationFeedback[relation.id]?.message"
                />
              </AppCard>
            </div>

            <div
              v-if="visibleManageableRelations.length"
              class="accountant-list-group ui-v2-workspace-accountant-list-group"
            >
              <h3>روابط ثبت‌شده</h3>
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
                  <div class="accountant-list-badges ui-v2-workspace-accountant-list-badges">
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

      <div id="accountant-workspace-overlay-host" class="ui-v2-workspace-overlay-host" />

      <AppConfirmDialog
        class="ui-v2-workspace-confirm-backdrop"
        :open="isConfirmDialogOpen && confirmAction !== 'delete-account'"
        :title="confirmTitle"
        :message="confirmMessage"
        :confirm-label="
          confirmAction === 'terminate-session'
            ? 'پایان نشست'
            : confirmAction === 'cancel-invitation'
              ? 'لغو رابطه و دعوت'
              : 'حذف رابطه'
        "
        :tone="confirmAction === 'terminate-session' ? 'warning' : 'danger'"
        :busy="isConfirmBusy"
        :error="confirmError"
        :confirm-disabled="isConfirmBusy"
        @cancel="closeConfirmDialog"
        @confirm="handleConfirmAction"
      />

      <WorkspaceAccountDeletionDialog
        :open="isConfirmDialogOpen && confirmAction === 'delete-account'"
        :subject-name="confirmRelation ? getRelationTitle(confirmRelation) : ''"
        :busy="isConfirmBusy"
        :error="confirmError"
        @cancel="closeConfirmDialog"
        @confirm="handleConfirmAction"
      />
    </WorkspaceShell>

    <component
      :is="isMobile ? AppBottomSheet : AppResponsiveDialog"
      :open="isCreatePanelOpen"
      title="افزودن حسابدار"
      description="اطلاعات اولیه حسابدار و شرح وظیفه را ثبت کنید."
      teleport-to="#accountant-workspace-overlay-host"
      backdrop-class="ui-v2-workspace-overlay-backdrop"
      panel-class="ui-v2-workspace-overlay-panel"
      body-class="ui-v2-workspace-overlay-body"
      :show-close="!isCreateSubmitting"
      :close-on-backdrop="!isCreateSubmitting"
      :close-on-escape="!isCreateSubmitting"
      @close="closeCreatePanel"
    >
      <div
        class="accountant-create-panel ui-v2-workspace-accountant-create-panel"
        :aria-busy="isCreateSubmitting ? 'true' : undefined"
      >
        <AppFormField
          label="نام کاربری جهانی"
          hint="این نام برای ساخت دعوت و ورود حسابدار استفاده می‌شود."
        >
          <template #default="{ id }">
            <AppInput
              :id="id"
              v-model="accountantState.createForm.account_name"
              :disabled="isCreateSubmitting"
              placeholder="مثلاً accountant_01"
            />
          </template>
        </AppFormField>

        <AppFormField label="نام نمایشی رابطه" hint="نامی که در فضای کاری خودتان می‌بینید.">
          <template #default="{ id }">
            <AppInput
              :id="id"
              v-model="accountantState.createForm.relation_display_name"
              :disabled="isCreateSubmitting"
              placeholder="مثلاً حسابدار فروش"
            />
          </template>
        </AppFormField>

        <AppFormField
          label="شماره موبایل"
          hint="برای ثبت دعوت و ساخت لینک مخصوص حسابدار استفاده می‌شود."
        >
          <template #default="{ id }">
            <AppInput
              :id="id"
              v-model="accountantState.createForm.mobile_number"
              :disabled="isCreateSubmitting"
              placeholder="0912xxxxxxx"
            />
          </template>
        </AppFormField>

        <AppFormField label="شرح وظیفه" hint="اختیاری، برای تفکیک نقش حسابدار در گروه کاری.">
          <template #default="{ id }">
            <AppTextarea
              :id="id"
              v-model="accountantState.createForm.duty_description"
              rows="4"
              :disabled="isCreateSubmitting"
              placeholder="مثلاً پیگیری پیشنهادها و ثبت معاملات روزانه"
            />
          </template>
        </AppFormField>

        <AppCard
          v-if="generatedGlobalAccountName"
          class="accountant-generated-account ui-v2-workspace-accountant-generated-account"
        >
          <span class="accountant-meta-label ui-v2-workspace-accountant-meta-label"
            >نام کاربری دعوتی</span
          >
          <strong>@{{ generatedGlobalAccountName }}</strong>
        </AppCard>

        <WorkspaceNotice
          v-if="createError"
          v2-scope
          tone="danger"
          title="ثبت دعوت ناموفق بود"
          :message="createError"
        />

        <div
          class="ui-v2-workspace-inline-form-actions ui-v2-workspace-accountant-create-actions"
        >
          <AppButton variant="secondary" :disabled="isCreateSubmitting" @click="closeCreatePanel">
            انصراف
          </AppButton>
          <AppButton
            variant="primary"
            :loading="isCreateSubmitting"
            :disabled="isCreateSubmitting"
            @click="createRelation"
          >
            ثبت دعوت حسابدار
          </AppButton>
        </div>
      </div>
    </component>
  </div>
</template>
