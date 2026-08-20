<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import {
  ArrowRight,
  Package,
  PencilLine,
  Plus,
  Tag,
  Trash2,
} from 'lucide-vue-next'
import { routeRequest, routeRequestJson } from '../utils/routeRequest'
import { useActionState } from '../composables/useActionState'
import AppBackButton from './ui/AppBackButton.vue'
import AppButton from './ui/AppButton.vue'
import AppConfirmDialog from './ui/AppConfirmDialog.vue'
import AppEmptyState from './ui/AppEmptyState.vue'
import AppFormField from './ui/AppFormField.vue'
import AppIconButton from './ui/AppIconButton.vue'
import AppInput from './ui/AppInput.vue'
import AppListItem from './ui/AppListItem.vue'
import AppLoadingState from './ui/AppLoadingState.vue'
import AppSectionCard from './ui/AppSectionCard.vue'
import AppStatusBadge from './ui/AppStatusBadge.vue'

defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
}>()

defineEmits(['navigate'])

interface CommodityAlias {
  id: number
  alias: string
  commodity_id: number
}

interface Commodity {
  id: number
  name: string
  aliases: CommodityAlias[]
}

interface FormState {
  name: string
  aliasesText: string
}

const LOCKED_IMAM_COMMODITY_NAME = 'امام'

type ViewMode =
  | 'list'
  | 'aliases'
  | 'add_commodity'
  | 'edit_commodity_name'
  | 'add_alias'
  | 'edit_alias'

type DeleteConfirmation =
  | { kind: 'commodity'; commodity: Commodity }
  | { kind: 'alias'; commodity: Commodity; alias: CommodityAlias }

const viewMode = ref<ViewMode>('list')
const listLoading = ref(false)
const hasLoadedList = ref(false)
const actionBusyKey = ref<string | null>(null)
const errorMessage = ref('')
const successMessage = ref('')
const commodities = ref<Commodity[]>([])
const selectedCommodity = ref<Commodity | null>(null)
const selectedAlias = ref<CommodityAlias | null>(null)
const pendingDelete = ref<DeleteConfirmation | null>(null)
const deleteConfirmationError = ref('')
const form = reactive<FormState>({ name: '', aliasesText: '' })
const selectedCommodityIsLockedImam = computed(() => selectedCommodity.value?.name === LOCKED_IMAM_COMMODITY_NAME)
const selectedCommodityAliasCount = computed(() => selectedCommodity.value?.aliases.length ?? 0)
const isActionBusy = computed(() => actionBusyKey.value !== null)
const mutationActions = useActionState<{ kind: string; entityId?: number }, unknown>()
const deleteConfirmationTitle = computed(() => (
  pendingDelete.value?.kind === 'alias' ? 'حذف نام مستعار' : 'حذف کامل کالا'
))
const deleteConfirmationMessage = computed(() => (
  pendingDelete.value?.kind === 'alias'
    ? `آیا از حذف نام مستعار «${pendingDelete.value.alias.alias}» مطمئن هستید؟ حذف فقط پس از تأیید دقیق پاسخ سرور انجام می‌شود.`
    : pendingDelete.value
      ? `آیا از حذف کامل کالای «${pendingDelete.value.commodity.name}» مطمئن هستید؟ کالا و نام‌های مستعار آن فقط پس از تأیید دقیق پاسخ سرور حذف می‌شوند.`
      : ''
))
const deleteConfirmationLabel = computed(() => (
  pendingDelete.value?.kind === 'alias' ? 'بله، حذف شود' : 'بله، حذف کامل'
))
let listRequestSequence = 0
let listAbortController: AbortController | null = null
let detailRequestSequence = 0
let detailAbortController: AbortController | null = null

function resetMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function resetForm() {
  form.name = ''
  form.aliasesText = ''
}

function invalidateDetailRequest() {
  detailRequestSequence += 1
  detailAbortController?.abort()
  detailAbortController = null
  listLoading.value = false
}

function isCurrentDetailRequest(requestSequence: number, controller: AbortController) {
  return requestSequence === detailRequestSequence && detailAbortController === controller
}

function aliasCountLabel(count: number) {
  if (count <= 0) return 'بدون نام مستعار'
  return `${count.toLocaleString('fa-IR')} نام مستعار`
}

function isCommodity(value: unknown): value is Commodity {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<Commodity>
  return Number.isInteger(candidate.id)
    && typeof candidate.name === 'string'
    && Array.isArray(candidate.aliases)
    && candidate.aliases.every((alias) => (
      isCommodityAlias(alias) && alias.commodity_id === candidate.id
    ))
}

function isCommodityAlias(value: unknown): value is CommodityAlias {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<CommodityAlias>
  return Number.isInteger(candidate.id)
    && Number.isInteger(candidate.commodity_id)
    && typeof candidate.alias === 'string'
}

function replaceCommodity(nextCommodity: Commodity) {
  const index = commodities.value.findIndex((commodity) => commodity.id === nextCommodity.id)
  if (index === -1) {
    commodities.value = [...commodities.value, nextCommodity]
    return
  }
  commodities.value = commodities.value.map((commodity) => (
    commodity.id === nextCommodity.id ? nextCommodity : commodity
  ))
}

async function runMutation(input: {
  key: string
  context: { kind: string; entityId?: number }
  url: string
  method: 'POST' | 'PUT' | 'DELETE'
  body?: string
  operation: 'submit' | 'update' | 'delete'
  fallbackMessage: string
  validate: (receipt: unknown, response: Response) => boolean
}) {
  if (actionBusyKey.value !== null || mutationActions.isBusy(input.key)) return null
  actionBusyKey.value = input.key
  try {
    return await mutationActions.run({
      key: input.key,
      context: input.context,
      action: async () => {
        const response = await routeRequest(input.url, {
          method: input.method,
          ...(input.body ? { body: input.body } : {}),
          errorContext: {
            surface: 'admin',
            scope: 'action',
            operation: input.operation,
            userInitiated: true,
            fallbackMessage: input.fallbackMessage,
          },
        })
        const receipt = response.status === 204 ? null : await response.json()
        return { response, receipt }
      },
      validateReceipt: (receipt, _context, response) => input.validate(receipt, response),
    })
  } finally {
    if (actionBusyKey.value === input.key) actionBusyKey.value = null
  }
}

async function fetchCommodities(options: { preserveMessages?: boolean } = {}) {
  invalidateDetailRequest()
  const requestSequence = ++listRequestSequence
  listAbortController?.abort()
  listAbortController = new AbortController()
  viewMode.value = 'list'
  listLoading.value = true
  if (!options.preserveMessages) resetMessages()
  else errorMessage.value = ''
  try {
    const payload = await routeRequestJson<unknown>('/api/commodities/', {
      signal: listAbortController.signal,
      errorContext: {
        surface: 'admin',
        scope: 'list',
        operation: hasLoadedList.value ? 'background-refresh' : 'load-list',
        preserveExistingData: hasLoadedList.value,
        fallbackMessage: 'دریافت فهرست کالاها ممکن نشد.',
      },
    })
    if (requestSequence !== listRequestSequence) return
    if (!Array.isArray(payload) || !payload.every(isCommodity)) throw new Error('invalid_commodities_payload')
    commodities.value = payload
    hasLoadedList.value = true
  } catch (error) {
    if (requestSequence !== listRequestSequence) return
    if (error instanceof DOMException && error.name === 'AbortError') return
    errorMessage.value = 'دریافت فهرست کالاها ممکن نشد. اطلاعات فعلی حفظ شده است.'
  } finally {
    if (requestSequence === listRequestSequence) listLoading.value = false
  }
}

async function onManageAliases(commodity: Commodity, preserveMessages = false) {
  const requestSequence = ++detailRequestSequence
  detailAbortController?.abort()
  const controller = new AbortController()
  detailAbortController = controller
  listLoading.value = true
  if (!preserveMessages) {
    resetMessages()
  }
  try {
    const payload = await routeRequestJson<unknown>(`/api/commodities/${commodity.id}`, {
      signal: controller.signal,
      errorContext: {
        surface: 'admin',
        scope: 'panel',
        operation: selectedCommodity.value?.id === commodity.id ? 'background-refresh' : 'load-detail',
        preserveExistingData: selectedCommodity.value?.id === commodity.id,
        fallbackMessage: 'دریافت اطلاعات کالا ممکن نشد.',
      },
    })
    if (!isCurrentDetailRequest(requestSequence, controller)) return
    if (!isCommodity(payload) || payload.id !== commodity.id) {
      throw new Error('invalid_commodity_payload')
    }
    selectedCommodity.value = payload
    replaceCommodity(payload)
    viewMode.value = 'aliases'
  } catch (error) {
    if (!isCurrentDetailRequest(requestSequence, controller)) return
    if (error instanceof DOMException && error.name === 'AbortError') return
    errorMessage.value = 'دریافت اطلاعات کالا ممکن نشد. اطلاعات فعلی حفظ شده است.'
    if (selectedCommodity.value?.id !== commodity.id) viewMode.value = 'list'
  } finally {
    if (isCurrentDetailRequest(requestSequence, controller)) {
      listLoading.value = false
      detailAbortController = null
    }
  }
}

function returnToList() {
  if (isActionBusy.value) return
  invalidateDetailRequest()
  resetMessages()
  viewMode.value = 'list'
}

function returnToAliases() {
  if (!selectedCommodity.value || isActionBusy.value) return
  invalidateDetailRequest()
  resetMessages()
  viewMode.value = 'aliases'
}

function onAddCommodityStart() {
  invalidateDetailRequest()
  resetMessages()
  resetForm()
  viewMode.value = 'add_commodity'
}

async function onAddCommoditySubmit() {
  if (isActionBusy.value) return
  resetMessages()
  const aliasList = form.aliasesText.split(/[،-]/)
    .map((alias) => alias.trim())
    .filter((alias) => alias.length > 0)
  const commodityName = form.name.trim()
  if (!commodityName) {
    errorMessage.value = 'نام اصلی کالا را وارد کنید.'
    return
  }
  if (!aliasList.includes(commodityName)) aliasList.unshift(commodityName)

  const result = await runMutation({
    key: 'create-commodity',
    context: { kind: 'create-commodity' },
    url: '/api/commodities/',
    method: 'POST',
    body: JSON.stringify({ commodity_data: { name: commodityName }, aliases: aliasList }),
    operation: 'submit',
    fallbackMessage: 'افزودن کالا انجام نشد.',
    validate: (receipt, response) => (
      response.status === 201
      && isCommodity(receipt)
      && receipt.name === commodityName
    ),
  })
  if (!result || result.outcome === 'duplicate') return
  if (result.outcome === 'error') {
    errorMessage.value = 'افزودن کالا انجام نشد. اطلاعات واردشده حفظ شده است.'
    viewMode.value = 'add_commodity'
    return
  }

  const data = result.receipt as Commodity
  replaceCommodity(data)
  successMessage.value = `کالا «${data.name}» با موفقیت افزوده شد.`
  viewMode.value = 'list'
  await fetchCommodities({ preserveMessages: true })
}

function onEditCommodityNameStart() {
  if (!selectedCommodity.value) return
  if (selectedCommodityIsLockedImam.value) {
    resetMessages()
    errorMessage.value = 'نام کالای پیش فرض امام قابل ویرایش نیست. فقط نام های مستعار را مدیریت کنید.'
    return
  }
  invalidateDetailRequest()
  resetMessages()
  form.name = selectedCommodity.value.name
  viewMode.value = 'edit_commodity_name'
}

async function onEditCommodityNameSubmit() {
  if (!selectedCommodity.value || isActionBusy.value) return
  const commodity = selectedCommodity.value
  resetMessages()
  const result = await runMutation({
    key: `update-commodity:${commodity.id}`,
    context: { kind: 'update-commodity', entityId: commodity.id },
    url: `/api/commodities/${commodity.id}`,
    method: 'PUT',
    body: JSON.stringify({ name: form.name.trim() }),
    operation: 'update',
    fallbackMessage: 'ویرایش نام کالا انجام نشد.',
    validate: (receipt, response) => (
      response.status === 200
      && isCommodity(receipt)
      && receipt.id === commodity.id
    ),
  })
  if (!result || result.outcome === 'duplicate') return
  if (result.outcome === 'error') {
    errorMessage.value = 'ویرایش نام کالا انجام نشد. اطلاعات واردشده حفظ شده است.'
    viewMode.value = 'edit_commodity_name'
    return
  }

  const data = result.receipt as Commodity
  selectedCommodity.value = data
  replaceCommodity(data)
  successMessage.value = `نام کالا با موفقیت به «${data.name}» تغییر یافت.`
  viewMode.value = 'aliases'
  await onManageAliases(data, true)
}

function onAddAliasStart() {
  if (!selectedCommodity.value) return
  invalidateDetailRequest()
  resetMessages()
  resetForm()
  viewMode.value = 'add_alias'
}

async function onAddAliasSubmit() {
  if (!selectedCommodity.value || isActionBusy.value) return
  const commodity = selectedCommodity.value
  resetMessages()
  const actionKey = `create-aliases:${commodity.id}`
  actionBusyKey.value = actionKey
  try {
    const aliasList = form.name.split(/[،\-]/)
      .map((alias) => alias.trim())
      .filter((alias) => alias.length > 0)

    if (aliasList.length === 0) {
      errorMessage.value = 'لطفاً حداقل یک نام مستعار وارد کنید.'
      return
    }

    const addedAliases: string[] = []
    const failedAliases: string[] = []

    for (const aliasName of aliasList) {
      try {
        const response = await routeRequest(`/api/commodities/${commodity.id}/aliases`, {
          method: 'POST',
          body: JSON.stringify({ alias: aliasName }),
          errorContext: {
            surface: 'admin',
            scope: 'action',
            operation: 'submit',
            userInitiated: true,
            fallbackMessage: 'افزودن نام مستعار انجام نشد.',
          },
        })
        const data = await response.json() as unknown
        if (
          response.status !== 201
          || !isCommodityAlias(data)
          || data.commodity_id !== commodity.id
          || data.alias !== aliasName
        ) {
          throw new Error('invalid_alias_receipt')
        }
        addedAliases.push(data.alias)
        commodity.aliases = [...commodity.aliases.filter((alias) => alias.id !== data.id), data]
      } catch {
        failedAliases.push(aliasName)
      }
    }

    if (addedAliases.length > 0) {
      successMessage.value = `نام‌های مستعار «${addedAliases.join('، ')}» با موفقیت افزوده شدند.`
    }
    if (failedAliases.length > 0) {
      errorMessage.value = `ثبت نام‌های «${failedAliases.join('، ')}» انجام نشد.`
    }

    replaceCommodity(commodity)
    if (addedAliases.length > 0) {
      viewMode.value = 'aliases'
      await onManageAliases(commodity, true)
    } else {
      viewMode.value = 'add_alias'
    }
  } finally {
    if (actionBusyKey.value === actionKey) actionBusyKey.value = null
  }
}

function onEditAliasStart(alias: CommodityAlias) {
  if (!selectedCommodity.value) return
  invalidateDetailRequest()
  resetMessages()
  selectedAlias.value = alias
  form.name = alias.alias
  viewMode.value = 'edit_alias'
}

async function onEditAliasSubmit() {
  if (!selectedCommodity.value || !selectedAlias.value || isActionBusy.value) return
  const commodity = selectedCommodity.value
  const alias = selectedAlias.value
  resetMessages()
  const result = await runMutation({
    key: `update-alias:${alias.id}`,
    context: { kind: 'update-alias', entityId: alias.id },
    url: `/api/commodities/aliases/${alias.id}`,
    method: 'PUT',
    body: JSON.stringify({ alias: form.name.trim() }),
    operation: 'update',
    fallbackMessage: 'ویرایش نام مستعار انجام نشد.',
    validate: (receipt, response) => (
      response.status === 200
      && isCommodityAlias(receipt)
      && receipt.id === alias.id
      && receipt.commodity_id === commodity.id
    ),
  })
  if (!result || result.outcome === 'duplicate') return
  if (result.outcome === 'error') {
    errorMessage.value = 'ویرایش نام مستعار انجام نشد. اطلاعات واردشده حفظ شده است.'
    viewMode.value = 'edit_alias'
    return
  }

  const data = result.receipt as CommodityAlias
  commodity.aliases = commodity.aliases.map((entry) => entry.id === data.id ? data : entry)
  selectedCommodity.value = commodity
  selectedAlias.value = data
  replaceCommodity(commodity)
  successMessage.value = `نام مستعار با موفقیت به «${data.alias}» تغییر یافت.`
  viewMode.value = 'aliases'
  await onManageAliases(commodity, true)
}

function onDeleteCommodityStart() {
  if (!selectedCommodity.value || pendingDelete.value || isActionBusy.value) return
  if (selectedCommodityIsLockedImam.value) {
    resetMessages()
    errorMessage.value = 'کالای پیش فرض امام قابل حذف نیست. فقط نام های مستعار را مدیریت کنید.'
    return
  }
  invalidateDetailRequest()
  resetMessages()
  deleteConfirmationError.value = ''
  pendingDelete.value = { kind: 'commodity', commodity: selectedCommodity.value }
}

async function onDeleteCommodityConfirm() {
  const candidate = pendingDelete.value
  if (!candidate || candidate.kind !== 'commodity' || isActionBusy.value) return
  const commodity = candidate.commodity
  deleteConfirmationError.value = ''
  const result = await runMutation({
    key: `delete-commodity:${commodity.id}`,
    context: { kind: 'delete-commodity', entityId: commodity.id },
    url: `/api/commodities/${commodity.id}`,
    method: 'DELETE',
    operation: 'delete',
    fallbackMessage: 'حذف کالا انجام نشد.',
    validate: (receipt, response) => response.status === 204 && receipt === null,
  })
  if (!result || result.outcome === 'duplicate') return
  if (result.outcome === 'error') {
    deleteConfirmationError.value = 'حذف کالا تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.'
    return
  }

  invalidateDetailRequest()
  commodities.value = commodities.value.filter((entry) => entry.id !== commodity.id)
  selectedCommodity.value = null
  selectedAlias.value = null
  pendingDelete.value = null
  deleteConfirmationError.value = ''
  successMessage.value = `کالا «${commodity.name}» با موفقیت حذف شد.`
  viewMode.value = 'list'
  await fetchCommodities({ preserveMessages: true })
}

function onDeleteAliasStart(alias: CommodityAlias) {
  if (!selectedCommodity.value || pendingDelete.value || isActionBusy.value) return
  invalidateDetailRequest()
  resetMessages()
  deleteConfirmationError.value = ''
  pendingDelete.value = {
    kind: 'alias',
    commodity: selectedCommodity.value,
    alias,
  }
}

async function onDeleteAliasConfirm() {
  const candidate = pendingDelete.value
  if (!candidate || candidate.kind !== 'alias' || isActionBusy.value) return
  const { commodity, alias } = candidate
  deleteConfirmationError.value = ''
  const result = await runMutation({
    key: `delete-alias:${alias.id}`,
    context: { kind: 'delete-alias', entityId: alias.id },
    url: `/api/commodities/aliases/${alias.id}`,
    method: 'DELETE',
    operation: 'delete',
    fallbackMessage: 'حذف نام مستعار انجام نشد.',
    validate: (receipt, response) => response.status === 204 && receipt === null,
  })
  if (!result || result.outcome === 'duplicate') return
  if (result.outcome === 'error') {
    deleteConfirmationError.value = 'حذف نام مستعار تأیید نشد. اطلاعات نمایش‌داده‌شده تغییری نکرده است؛ وضعیت را دوباره بررسی کنید.'
    return
  }

  commodity.aliases = commodity.aliases.filter((entry) => entry.id !== alias.id)
  selectedCommodity.value = commodity
  selectedAlias.value = null
  replaceCommodity(commodity)
  pendingDelete.value = null
  deleteConfirmationError.value = ''
  successMessage.value = `نام مستعار «${alias.alias}» با موفقیت حذف شد.`
  viewMode.value = 'aliases'
  await onManageAliases(commodity, true)
}

function cancelDeleteConfirmation() {
  if (isActionBusy.value) return
  invalidateDetailRequest()
  pendingDelete.value = null
  deleteConfirmationError.value = ''
}

async function confirmPendingDelete() {
  if (pendingDelete.value?.kind === 'commodity') {
    await onDeleteCommodityConfirm()
    return
  }
  if (pendingDelete.value?.kind === 'alias') {
    await onDeleteAliasConfirm()
  }
}

onMounted(fetchCommodities)
onUnmounted(() => {
  listRequestSequence += 1
  listAbortController?.abort()
  invalidateDetailRequest()
})
</script>

<template>
  <div class="commodity-manager ds-page-content">
    <div v-if="successMessage" class="commodity-feedback commodity-feedback--success" role="status" aria-live="polite">
      <strong>ذخیره شد</strong>
      <span>{{ successMessage }}</span>
    </div>

    <div v-if="errorMessage" class="commodity-feedback commodity-feedback--error" role="alert" aria-live="assertive">
      <strong>{{ viewMode === 'list' && !hasLoadedList ? 'دریافت اطلاعات انجام نشد' : 'ثبت اطلاعات انجام نشد' }}</strong>
      <pre class="error-pre">{{ errorMessage }}</pre>
      <AppButton
        v-if="viewMode === 'list' && !hasLoadedList"
        type="button"
        class="commodity-list-retry"
        variant="secondary"
        :loading="listLoading"
        @click="fetchCommodities"
      >
        تلاش مجدد
      </AppButton>
    </div>

    <AppSectionCard
      v-if="viewMode === 'list'"
      title="فهرست کالاها"
      description="کالاهای قابل معامله و نام‌های مستعار هر مورد را از این بخش مدیریت کنید."
    >
      <template #actions>
        <AppButton class="commodity-action primary-soft" variant="primary" @click="onAddCommodityStart">
          <template #icon>
            <Plus :size="16" />
          </template>
          افزودن کالا
        </AppButton>
      </template>

      <AppLoadingState v-if="listLoading && !hasLoadedList" label="در حال دریافت کالاها" />

      <AppEmptyState
        v-else-if="hasLoadedList && commodities.length === 0"
        title="هنوز کالایی ثبت نشده است"
        message="ابتدا کالای اصلی را ثبت کنید و سپس نام‌های مستعار آن را مدیریت کنید."
        role="status"
      >
        <template #icon>
          <Package :size="18" />
        </template>
        <template #actions>
          <AppButton variant="primary" @click="onAddCommodityStart">افزودن کالای جدید</AppButton>
        </template>
      </AppEmptyState>

      <div v-else-if="hasLoadedList" class="list-group" :aria-busy="listLoading">
        <AppListItem
          v-for="comm in commodities"
          :key="comm.id"
          class="list-item-btn"
          :title="comm.name"
          :description="aliasCountLabel(comm.aliases.length)"
          interactive
          @select="onManageAliases(comm)"
        >
          <template #leading>
            <Package :size="18" />
          </template>
          <template #trailing>
            <span class="chevron">
              <ArrowRight :size="16" />
            </span>
          </template>
        </AppListItem>
      </div>
    </AppSectionCard>

    <template v-if="viewMode === 'aliases' && selectedCommodity">
      <div class="commodity-subview-nav">
        <AppBackButton
          class="commodity-back-control"
          label="بازگشت به فهرست کالاها"
          @click="returnToList"
        />
      </div>
      <AppSectionCard
        :title="selectedCommodity.name"
        description="لیست نام‌های مستعار این کالا و اقدامات مرتبط با آن را از اینجا مدیریت کنید."
      >
        <template #actions>
          <AppStatusBadge tone="info">{{ aliasCountLabel(selectedCommodityAliasCount) }}</AppStatusBadge>
        </template>

        <AppEmptyState
          v-if="selectedCommodity.aliases.length === 0"
          title="نام مستعاری برای این کالا ثبت نشده است"
          message="می‌توانید یک یا چند نام مستعار جدید به این کالا اضافه کنید."
          role="status"
        >
          <template #icon>
            <Tag :size="18" />
          </template>
          <template #actions>
            <AppButton class="commodity-action primary-soft" variant="primary" @click="onAddAliasStart">
              افزودن نام مستعار
            </AppButton>
          </template>
        </AppEmptyState>

        <div v-else class="alias-list">
          <div v-for="alias in selectedCommodity.aliases" :key="alias.id" class="alias-row">
            <AppListItem
              class="alias-item"
              :title="alias.alias"
              description="نام مستعار قابل استفاده در بازار"
            >
              <template #leading>
                <Tag :size="16" />
              </template>
              <template #trailing>
                <div class="alias-actions">
                  <AppIconButton @click="onEditAliasStart(alias)" class="commodity-icon-control edit" label="ویرایش نام مستعار" size="sm">
                    <PencilLine :size="15" />
                  </AppIconButton>
                  <AppIconButton @click="onDeleteAliasStart(alias)" class="commodity-icon-control delete" label="حذف نام مستعار" variant="danger" size="sm">
                    <Trash2 :size="15" />
                  </AppIconButton>
                </div>
              </template>
            </AppListItem>
          </div>
        </div>
      </AppSectionCard>

      <AppSectionCard
        title="اقدامات کالا"
        description="ثبت نام مستعار جدید، تغییر نام اصلی یا حذف کامل کالا از این بخش انجام می‌شود."
      >
        <div class="card-footer stacked">
          <AppButton class="commodity-action primary-soft" variant="primary" block @click="onAddAliasStart">
            <template #icon>
              <Plus :size="16" />
            </template>
            افزودن نام مستعار
          </AppButton>
          <p v-if="selectedCommodityIsLockedImam" class="locked-commodity-hint">
            کالای پیش‌فرض امام فقط از مسیر نام‌های مستعار قابل مدیریت است و حذف یا تغییر نام اصلی ندارد.
          </p>
          <AppButton
            v-if="!selectedCommodityIsLockedImam"
            class="commodity-action secondary-soft"
            variant="secondary"
            block
            @click="onEditCommodityNameStart"
          >
            <template #icon>
              <PencilLine :size="16" />
            </template>
            ویرایش نام اصلی
          </AppButton>
          <AppButton
            v-if="!selectedCommodityIsLockedImam"
            class="commodity-action danger-soft"
            variant="danger"
            block
            @click="onDeleteCommodityStart"
          >
            <template #icon>
              <Trash2 :size="16" />
            </template>
            حذف کامل کالا
          </AppButton>
        </div>
      </AppSectionCard>
    </template>

    <AppSectionCard
      v-if="viewMode === 'add_commodity'"
      title="افزودن کالای جدید"
      description="نام اصلی کالا و در صورت نیاز نام‌های مستعار اولیه را هم‌زمان ثبت کنید."
    >
      <form @submit.prevent="onAddCommoditySubmit" class="manager-form">
        <AppFormField label="نام اصلی کالا">
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              v-model="form.name"
              :aria-describedby="describedby"
              :invalid="invalid"
              type="text"
              placeholder="مثلاً سکه امامی"
              required
            />
          </template>
        </AppFormField>

        <AppFormField label="نام‌های مستعار" hint="نام‌ها را با «،» یا «-» از هم جدا کنید.">
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              v-model="form.aliasesText"
              :aria-describedby="describedby"
              :invalid="invalid"
              type="text"
              placeholder="مثال: سکه جدید ، امامی - سکه بانکی"
            />
          </template>
        </AppFormField>

        <div class="form-footer">
          <AppButton type="submit" variant="primary" :loading="isActionBusy">افزودن کالا</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isActionBusy" @click="returnToList">لغو</AppButton>
        </div>
      </form>
    </AppSectionCard>

    <AppSectionCard
      v-if="viewMode === 'edit_commodity_name' && selectedCommodity"
      title="ویرایش نام کالا"
      :description="`نام جدید برای «${selectedCommodity.name}» را ثبت کنید.`"
    >
      <form @submit.prevent="onEditCommodityNameSubmit" class="manager-form">
        <AppFormField :label="`نام جدید برای ${selectedCommodity.name}`">
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              v-model="form.name"
              :aria-describedby="describedby"
              :invalid="invalid"
              type="text"
              required
            />
          </template>
        </AppFormField>

        <div class="form-footer">
          <AppButton type="submit" variant="primary" :loading="isActionBusy">ذخیره نام</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isActionBusy" @click="returnToAliases">لغو</AppButton>
        </div>
      </form>
    </AppSectionCard>

    <AppSectionCard
      v-if="viewMode === 'add_alias' && selectedCommodity"
      title="افزودن نام مستعار"
      :description="`نام‌های مستعار جدید برای «${selectedCommodity.name}» را ثبت کنید.`"
    >
      <form @submit.prevent="onAddAliasSubmit" class="manager-form">
        <AppFormField label="نام‌های مستعار" hint="می‌توانید چند نام را با «،» یا «-» وارد کنید.">
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              v-model="form.name"
              :aria-describedby="describedby"
              :invalid="invalid"
              type="text"
              placeholder="مثال: نیم تاریخ پایین ، نیم ت.پ"
              required
            />
          </template>
        </AppFormField>

        <div class="form-footer">
          <AppButton type="submit" variant="primary" :loading="isActionBusy">افزودن</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isActionBusy" @click="returnToAliases">لغو</AppButton>
        </div>
      </form>
    </AppSectionCard>

    <AppSectionCard
      v-if="viewMode === 'edit_alias' && selectedCommodity && selectedAlias"
      title="ویرایش نام مستعار"
      :description="`نام جدید برای «${selectedAlias.alias}» را ثبت کنید.`"
    >
      <form @submit.prevent="onEditAliasSubmit" class="manager-form">
        <AppFormField :label="`نام جدید برای ${selectedAlias.alias}`">
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              v-model="form.name"
              :aria-describedby="describedby"
              :invalid="invalid"
              type="text"
              required
            />
          </template>
        </AppFormField>

        <div class="form-footer">
          <AppButton type="submit" variant="primary" :loading="isActionBusy">ذخیره</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isActionBusy" @click="returnToAliases">لغو</AppButton>
        </div>
      </form>
    </AppSectionCard>

    <AppConfirmDialog
      :open="Boolean(pendingDelete)"
      :title="deleteConfirmationTitle"
      :message="deleteConfirmationMessage"
      :confirm-label="deleteConfirmationLabel"
      cancel-label="انصراف"
      tone="danger"
      :busy="isActionBusy"
      :error="deleteConfirmationError || undefined"
      :confirm-disabled="!pendingDelete"
      @cancel="cancelDeleteConfirmation"
      @confirm="confirmPendingDelete"
    />
  </div>
</template>

<style scoped>
.commodity-manager {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
  font-family: Vazirmatn, Tahoma, Arial, sans-serif;
  font-synthesis: none;
}

.commodity-feedback {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 0.9rem 1rem;
  border: 1px solid transparent;
  border-radius: var(--ds-radius-lg);
  box-shadow: var(--ds-shadow-sm);
}

.commodity-feedback strong {
  font-size: var(--ds-font-sm);
  font-weight: 850;
  line-height: 1.5;
}

.commodity-feedback span,
.commodity-feedback pre {
  margin: 0;
  font-size: var(--ds-font-xs);
  line-height: 1.8;
  white-space: pre-wrap;
  font-family: inherit;
}

.commodity-feedback--success {
  background: var(--ds-success-50);
  border-color: var(--ds-success-100);
  color: var(--ds-success-700);
}

.commodity-feedback--error {
  background: var(--ds-danger-50);
  border-color: var(--ds-danger-100);
  color: var(--ds-danger-700);
}

.list-group,
.alias-list,
.manager-form,
.card-footer.stacked {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.list-item-btn,
.alias-item {
  width: 100%;
}

.chevron {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--ds-text-muted);
}

.commodity-subview-nav {
  display: flex;
  align-items: center;
  min-height: var(--ds-native-row-min-height, 48px);
  margin-bottom: 0.75rem;
}

.commodity-icon-control {
  width: 2.25rem;
  height: 2.25rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--ds-border-light);
  border-radius: var(--ds-radius-md);
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  cursor: pointer;
  transition: border-color 0.2s ease, background-color 0.2s ease, color 0.2s ease;
}

.commodity-icon-control:hover {
  border-color: var(--ds-primary-300);
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
}

.commodity-icon-control.delete:hover {
  border-color: var(--ds-danger-300);
  background: var(--ds-danger-50);
  color: var(--ds-danger-700);
}

.alias-row {
  display: flex;
  flex-direction: column;
}

.alias-actions {
  display: inline-flex;
  gap: 0.4rem;
}

.locked-commodity-hint {
  margin: 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.locked-commodity-hint {
  padding: 0.85rem 0.95rem;
  border: 1px solid var(--ds-warning-100);
  border-radius: var(--ds-radius-md);
  background: var(--ds-warning-50);
}

.form-footer {
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}

.form-footer :deep(.ui-button) {
  flex: 1 1 12rem;
}

@media (max-width: 640px) {
  .aliases-header-actions {
    width: 100%;
    justify-content: space-between;
  }

  .form-footer {
    flex-direction: column;
  }

  .form-footer :deep(.ui-button) {
    width: 100%;
    flex-basis: auto;
  }
}
</style>
