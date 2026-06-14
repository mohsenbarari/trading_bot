<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  ArrowRight,
  Package,
  PencilLine,
  Plus,
  Tag,
  Trash2,
} from 'lucide-vue-next'
import { apiFetch } from '../utils/auth'
import AppButton from './ui/AppButton.vue'
import AppDangerZone from './ui/AppDangerZone.vue'
import AppEmptyState from './ui/AppEmptyState.vue'
import AppFormField from './ui/AppFormField.vue'
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
  | 'delete_commodity'
  | 'delete_alias'

const viewMode = ref<ViewMode>('list')
const isLoading = ref(true)
const errorMessage = ref('')
const successMessage = ref('')
const commodities = ref<Commodity[]>([])
const selectedCommodity = ref<Commodity | null>(null)
const selectedAlias = ref<CommodityAlias | null>(null)
const form = reactive<FormState>({ name: '', aliasesText: '' })
const selectedCommodityIsLockedImam = computed(() => selectedCommodity.value?.name === LOCKED_IMAM_COMMODITY_NAME)
const selectedCommodityAliasCount = computed(() => selectedCommodity.value?.aliases.length ?? 0)

function resetMessages() {
  errorMessage.value = ''
  successMessage.value = ''
}

function resetForm() {
  form.name = ''
  form.aliasesText = ''
}

function getErrorDetail(error: any, defaultMsg: string): string {
  const detail = error.detail || error.message
  if (!detail) return defaultMsg

  if (typeof detail === 'object') {
    try {
      return JSON.stringify(detail, null, 2)
    } catch {
      return defaultMsg
    }
  }
  return detail
}

function aliasCountLabel(count: number) {
  if (count <= 0) return 'بدون نام مستعار'
  return `${count.toLocaleString('fa-IR')} نام مستعار`
}

async function fetchCommodities() {
  viewMode.value = 'list'
  isLoading.value = true
  resetMessages()
  try {
    const response = await apiFetch('/api/commodities/')
    if (!response.ok) throw new Error('خطا در بارگیری لیست کالاها')
    commodities.value = await response.json()
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
  } finally {
    isLoading.value = false
  }
}

async function onManageAliases(commodity: Commodity, preserveMessages = false) {
  isLoading.value = true
  if (!preserveMessages) {
    resetMessages()
  }
  try {
    const response = await apiFetch(`/api/commodities/${commodity.id}`)
    if (!response.ok) throw new Error('خطا در دریافت اطلاعات کالا')
    selectedCommodity.value = await response.json()
    viewMode.value = 'aliases'
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
    viewMode.value = 'list'
  } finally {
    isLoading.value = false
  }
}

function onAddCommodityStart() {
  resetMessages()
  resetForm()
  viewMode.value = 'add_commodity'
}

async function onAddCommoditySubmit() {
  isLoading.value = true
  resetMessages()
  try {
    const aliasList = form.aliasesText.split(/[،-]/)
      .map((alias) => alias.trim())
      .filter((alias) => alias.length > 0)

    const commodityName = form.name.trim()
    if (commodityName && !aliasList.includes(commodityName)) {
      aliasList.unshift(commodityName)
    }

    const payload = {
      commodity_data: { name: commodityName },
      aliases: aliasList,
    }

    const response = await apiFetch('/api/commodities/', {
      method: 'POST',
      body: JSON.stringify(payload),
    })

    const data = await response.json()

    if (!response.ok) {
      throw { detail: data.detail || 'خطا در افزودن کالا' }
    }

    successMessage.value = `کالا «${data.name}» با موفقیت افزوده شد.`
    await fetchCommodities()
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
    viewMode.value = 'add_commodity'
  } finally {
    isLoading.value = false
  }
}

function onEditCommodityNameStart() {
  if (!selectedCommodity.value) return
  if (selectedCommodityIsLockedImam.value) {
    resetMessages()
    errorMessage.value = 'نام کالای پیش فرض امام قابل ویرایش نیست. فقط نام های مستعار را مدیریت کنید.'
    return
  }
  resetMessages()
  form.name = selectedCommodity.value.name
  viewMode.value = 'edit_commodity_name'
}

async function onEditCommodityNameSubmit() {
  if (!selectedCommodity.value) return
  isLoading.value = true
  resetMessages()
  try {
    const response = await apiFetch(`/api/commodities/${selectedCommodity.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ name: form.name.trim() }),
    })
    const data = await response.json()
    if (!response.ok) {
      throw { detail: data.detail || 'خطا در ویرایش نام' }
    }

    successMessage.value = `نام کالا با موفقیت به «${data.name}» تغییر یافت.`
    await onManageAliases(data)
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
    viewMode.value = 'edit_commodity_name'
  } finally {
    isLoading.value = false
  }
}

function onAddAliasStart() {
  if (!selectedCommodity.value) return
  resetMessages()
  resetForm()
  viewMode.value = 'add_alias'
}

async function onAddAliasSubmit() {
  if (!selectedCommodity.value) return
  isLoading.value = true
  resetMessages()
  try {
    const aliasList = form.name.split(/[،\-]/)
      .map((alias) => alias.trim())
      .filter((alias) => alias.length > 0)

    if (aliasList.length === 0) {
      throw { detail: 'لطفاً حداقل یک نام مستعار وارد کنید.' }
    }

    const addedAliases: string[] = []
    const failedAliases: string[] = []

    for (const aliasName of aliasList) {
      const response = await apiFetch(`/api/commodities/${selectedCommodity.value.id}/aliases`, {
        method: 'POST',
        body: JSON.stringify({ alias: aliasName }),
      })
      const data = await response.json()
      if (response.ok) {
        addedAliases.push(data.alias)
      } else {
        failedAliases.push(`${aliasName}: ${data.detail || 'خطا'}`)
      }
    }

    if (addedAliases.length > 0) {
      successMessage.value = `نام‌های مستعار «${addedAliases.join('، ')}» با موفقیت افزوده شدند.`
    }
    if (failedAliases.length > 0) {
      errorMessage.value = failedAliases.join('\n')
    }

    await onManageAliases(selectedCommodity.value, addedAliases.length > 0 || failedAliases.length > 0)
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
    viewMode.value = 'add_alias'
  } finally {
    isLoading.value = false
  }
}

function onEditAliasStart(alias: CommodityAlias) {
  if (!selectedCommodity.value) return
  resetMessages()
  selectedAlias.value = alias
  form.name = alias.alias
  viewMode.value = 'edit_alias'
}

async function onEditAliasSubmit() {
  if (!selectedCommodity.value || !selectedAlias.value) return
  isLoading.value = true
  resetMessages()
  try {
    const response = await apiFetch(`/api/commodities/aliases/${selectedAlias.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({ alias: form.name.trim() }),
    })
    const data = await response.json()
    if (!response.ok) {
      throw { detail: data.detail || 'خطا در ویرایش نام مستعار' }
    }

    successMessage.value = `نام مستعار با موفقیت به «${data.alias}» تغییر یافت.`
    await onManageAliases(selectedCommodity.value)
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
    viewMode.value = 'edit_alias'
  } finally {
    isLoading.value = false
  }
}

function onDeleteCommodityStart() {
  if (!selectedCommodity.value) return
  if (selectedCommodityIsLockedImam.value) {
    resetMessages()
    errorMessage.value = 'کالای پیش فرض امام قابل حذف نیست. فقط نام های مستعار را مدیریت کنید.'
    return
  }
  resetMessages()
  viewMode.value = 'delete_commodity'
}

async function onDeleteCommodityConfirm() {
  if (!selectedCommodity.value) return
  isLoading.value = true
  resetMessages()
  try {
    const response = await apiFetch(`/api/commodities/${selectedCommodity.value.id}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const data = response.status !== 204 ? await response.json() : null
      if (data) {
        throw { detail: data.detail || 'خطا در حذف کالا' }
      }
    }

    successMessage.value = `کالا «${selectedCommodity.value.name}» با موفقیت حذف شد.`
    await fetchCommodities()
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
    await onManageAliases(selectedCommodity.value)
  } finally {
    isLoading.value = false
  }
}

function onDeleteAliasStart(alias: CommodityAlias) {
  if (!selectedCommodity.value) return
  resetMessages()
  selectedAlias.value = alias
  viewMode.value = 'delete_alias'
}

async function onDeleteAliasConfirm() {
  if (!selectedCommodity.value || !selectedAlias.value) return
  isLoading.value = true
  resetMessages()
  try {
    const response = await apiFetch(`/api/commodities/aliases/${selectedAlias.value.id}`, {
      method: 'DELETE',
    })
    if (!response.ok) {
      const data = response.status !== 204 ? await response.json() : null
      if (data) {
        throw { detail: data.detail || 'خطا در حذف نام مستعار' }
      }
    }

    successMessage.value = `نام مستعار «${selectedAlias.value.alias}» با موفقیت حذف شد.`
    await onManageAliases(selectedCommodity.value)
  } catch (e: any) {
    errorMessage.value = getErrorDetail(e, 'خطای ناشناخته')
    await onManageAliases(selectedCommodity.value)
  } finally {
    isLoading.value = false
  }
}

onMounted(fetchCommodities)
</script>

<template>
  <div class="commodity-manager ds-page-content">
    <div v-if="successMessage" class="flash-box flash-box--success" role="status" aria-live="polite">
      <strong>ذخیره شد</strong>
      <span>{{ successMessage }}</span>
    </div>

    <div v-if="errorMessage" class="flash-box flash-box--error" role="alert" aria-live="assertive">
      <strong>ثبت اطلاعات انجام نشد</strong>
      <pre class="error-pre">{{ errorMessage }}</pre>
    </div>

    <AppSectionCard
      v-if="viewMode === 'list'"
      title="فهرست کالاها"
      description="کالاهای قابل معامله و نام‌های مستعار هر مورد را از این بخش مدیریت کنید."
    >
      <template #actions>
        <AppButton class="action-btn primary-soft" variant="primary" @click="onAddCommodityStart">
          <template #icon>
            <Plus :size="16" />
          </template>
          افزودن کالا
        </AppButton>
      </template>

      <AppLoadingState v-if="isLoading" label="در حال دریافت کالاها" />

      <AppEmptyState
        v-else-if="commodities.length === 0"
        title="هنوز کالایی ثبت نشده است"
        message="ابتدا کالای اصلی را ثبت کنید و سپس نام‌های مستعار آن را مدیریت کنید."
      >
        <template #icon>
          <Package :size="18" />
        </template>
        <template #actions>
          <AppButton variant="primary" @click="onAddCommodityStart">افزودن کالای جدید</AppButton>
        </template>
      </AppEmptyState>

      <div v-else class="list-group">
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
      <AppSectionCard
        :title="selectedCommodity.name"
        description="لیست نام‌های مستعار این کالا و اقدامات مرتبط با آن را از اینجا مدیریت کنید."
      >
        <template #actions>
          <div class="aliases-header-actions">
            <AppStatusBadge tone="info">{{ aliasCountLabel(selectedCommodityAliasCount) }}</AppStatusBadge>
            <button @click="fetchCommodities" class="back-icon-btn" type="button" aria-label="بازگشت به فهرست کالاها">
              <ArrowRight :size="16" />
            </button>
          </div>
        </template>

        <AppLoadingState v-if="isLoading" label="در حال دریافت نام‌های مستعار" />

        <AppEmptyState
          v-else-if="selectedCommodity.aliases.length === 0"
          title="نام مستعاری برای این کالا ثبت نشده است"
          message="می‌توانید یک یا چند نام مستعار جدید به این کالا اضافه کنید."
        >
          <template #icon>
            <Tag :size="18" />
          </template>
          <template #actions>
            <AppButton class="action-btn primary-soft" variant="primary" @click="onAddAliasStart">
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
                  <button @click="onEditAliasStart(alias)" class="icon-btn edit" type="button" aria-label="ویرایش نام مستعار">
                    <PencilLine :size="15" />
                  </button>
                  <button @click="onDeleteAliasStart(alias)" class="icon-btn delete" type="button" aria-label="حذف نام مستعار">
                    <Trash2 :size="15" />
                  </button>
                </div>
              </template>
            </AppListItem>
            <span class="alias-text">{{ alias.alias }}</span>
          </div>
        </div>
      </AppSectionCard>

      <AppSectionCard
        title="اقدامات کالا"
        description="ثبت نام مستعار جدید، تغییر نام اصلی یا حذف کامل کالا از این بخش انجام می‌شود."
      >
        <div class="card-footer stacked">
          <AppButton class="action-btn primary-soft" variant="primary" block @click="onAddAliasStart">
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
            class="action-btn secondary-soft"
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
            class="action-btn danger-soft"
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
          <AppButton type="submit" variant="primary" :loading="isLoading">افزودن کالا</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isLoading" @click="fetchCommodities">لغو</AppButton>
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
          <AppButton type="submit" variant="primary" :loading="isLoading">ذخیره نام</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isLoading" @click="onManageAliases(selectedCommodity)">لغو</AppButton>
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
          <AppButton type="submit" variant="primary" :loading="isLoading">افزودن</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isLoading" @click="onManageAliases(selectedCommodity)">لغو</AppButton>
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
          <AppButton type="submit" variant="primary" :loading="isLoading">ذخیره</AppButton>
          <AppButton type="button" variant="secondary" :disabled="isLoading" @click="onManageAliases(selectedCommodity)">لغو</AppButton>
        </div>
      </form>
    </AppSectionCard>

    <AppDangerZone
      v-if="viewMode === 'delete_commodity' && selectedCommodity"
      title="حذف کامل کالا"
      description="این عملیات برگشت‌پذیر نیست و تمام نام‌های مستعار این کالا هم حذف می‌شوند."
    >
      <p class="confirm-text">آیا از حذف کامل «{{ selectedCommodity.name }}» مطمئن هستید؟</p>
      <div class="form-footer">
        <AppButton type="button" variant="danger" :loading="isLoading" @click="onDeleteCommodityConfirm">بله، حذف کامل</AppButton>
        <AppButton type="button" variant="secondary" :disabled="isLoading" @click="onManageAliases(selectedCommodity)">لغو</AppButton>
      </div>
    </AppDangerZone>

    <AppDangerZone
      v-if="viewMode === 'delete_alias' && selectedCommodity && selectedAlias"
      title="حذف نام مستعار"
      description="اگر این نام در بازار استفاده می‌شود، پس از حذف دیگر قابل جستجو نخواهد بود."
    >
      <p class="confirm-text">آیا از حذف نام مستعار «{{ selectedAlias.alias }}» مطمئن هستید؟</p>
      <div class="form-footer">
        <AppButton type="button" variant="danger" :loading="isLoading" @click="onDeleteAliasConfirm">بله، حذف شود</AppButton>
        <AppButton type="button" variant="secondary" :disabled="isLoading" @click="onManageAliases(selectedCommodity)">لغو</AppButton>
      </div>
    </AppDangerZone>
  </div>
</template>

<style scoped>
.commodity-manager {
  display: flex;
  flex-direction: column;
  gap: var(--ds-section-gap);
}

.flash-box {
  display: flex;
  flex-direction: column;
  gap: 0.3rem;
  padding: 0.9rem 1rem;
  border: 1px solid transparent;
  border-radius: var(--ds-radius-lg);
  box-shadow: var(--ds-shadow-sm);
}

.flash-box strong {
  font-size: var(--ds-font-sm);
  font-weight: 850;
  line-height: 1.5;
}

.flash-box span,
.flash-box pre {
  margin: 0;
  font-size: var(--ds-font-xs);
  line-height: 1.8;
  white-space: pre-wrap;
  font-family: inherit;
}

.flash-box--success {
  background: var(--ds-success-50);
  border-color: var(--ds-success-100);
  color: var(--ds-success-700);
}

.flash-box--error {
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

.aliases-header-actions {
  display: inline-flex;
  align-items: center;
  gap: 0.55rem;
}

.back-icon-btn,
.icon-btn {
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

.back-icon-btn:hover,
.icon-btn:hover {
  border-color: var(--ds-primary-300);
  background: var(--ds-primary-50);
  color: var(--ds-primary-700);
}

.icon-btn.delete:hover {
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

.locked-commodity-hint,
.confirm-text {
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
