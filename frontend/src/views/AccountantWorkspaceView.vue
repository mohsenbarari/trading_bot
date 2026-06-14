<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BriefcaseBusiness, Clock, Search, ShieldAlert, SlidersHorizontal, UserPlus } from 'lucide-vue-next'
import OwnerAccountantManagerModal from '../components/OwnerAccountantManagerModal.vue'
import { WorkspaceNotice, WorkspaceSection, WorkspaceShell } from '../components/workspace'
import { AppActionCard, AppButton, AppCard, AppFormField, AppInput, AppListItem, AppMetricCard, AppStatusBadge, AppTabs } from '../components/ui'
import {
  fetchOwnerAccountantSessions,
  fetchOwnerAccountantRelations,
  useOwnerAccountants,
  type AccountantRelation,
  type AccountantSessionSummary,
} from '../composables/useOwnerAccountants'

const route = useRoute()
const router = useRouter()
const accountantState = useOwnerAccountants()
const isLoading = ref(true)
const error = ref('')
const isCompatibilityManagerOpen = ref(false)
const compatibilityPanel = ref<string | null>(null)
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
    if (relationFilter.value === 'active' && relation.status !== 'active') return false
    if (relationFilter.value === 'pending' && relation.status !== 'pending') return false
    if (relationFilter.value === 'inactive' && (relation.status === 'active' || relation.status === 'pending')) return false
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

function getRelationTitle(relation: AccountantRelation) {
  return relation.relation_display_name || relation.accountant_account_name || relation.global_account_name || 'حسابدار'
}

function getRelationDescription(relation: AccountantRelation) {
  return `${relation.mobile_number || 'بدون شماره'}${relation.duty_description ? ` - ${relation.duty_description}` : ''}`
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
  if (panel === 'create' || panel === 'pending' || panel === 'manage' || panel === 'relations') {
    openCompatibilityManager(panel)
  }
}, { immediate: true })

watch([activeRelation, detailTab], () => {
  detailSessions.value = []
  detailSessionsError.value = ''
  refreshCurrentDetailTab()
}, { flush: 'post' })

onMounted(() => {
  void loadRelations()
})
</script>

<template>
  <div class="ds-page accountant-workspace-view">
    <WorkspaceShell
      title="حسابداران"
      eyebrow="عملیات"
      description="نمای route-native برای مرور حسابداران و ورود به مدیریت کامل."
      layout="split"
      show-back
      back-label="بازگشت"
      @back="handleBack"
    >
      <template #actions>
        <AppButton variant="secondary" class="accountant-workspace-action" @click="goToOperations">
          عملیات
        </AppButton>
        <AppButton variant="primary" class="accountant-workspace-create" @click="openCompatibilityManager('create')">
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
          description="مشخصات، شرح وظیفه، نشست‌ها و اقدامات حساس در یک نمای tabدار."
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
                <span class="accountant-field-label">فعال‌سازی</span>
                <strong>{{ formatDate(activeRelation.activated_at) }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-field-label">ایجاد رابطه</span>
                <strong>{{ formatDate(activeRelation.created_at) }}</strong>
              </AppCard>
              <AppCard>
                <span class="accountant-field-label">انقضا دعوت</span>
                <strong>{{ formatDate(activeRelation.expires_at) }}</strong>
              </AppCard>
            </div>

            <div v-else-if="detailTab === 'duty'" class="accountant-detail-list">
              <AppCard>
                <span class="accountant-field-label">شرح وظیفه فعلی</span>
                <strong>{{ getDutyText(activeRelation) }}</strong>
              </AppCard>
              <AppButton variant="secondary" @click="openCompatibilityManager('manage')">
                ویرایش شرح وظیفه
              </AppButton>
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
                  :meta="session.is_primary ? 'اصلی' : 'فرعی'"
                >
                  <template #leading>
                    <Clock :size="18" />
                  </template>
                </AppListItem>
              </template>
              <AppButton variant="secondary" @click="openCompatibilityManager('manage')">
                مدیریت نشست‌ها
              </AppButton>
            </div>

            <div v-else class="accountant-detail-list">
              <AppCard tone="danger">
                <div class="accountant-danger-card">
                  <ShieldAlert :size="22" />
                  <div>
                    <strong>اقدامات حساس حسابدار</strong>
                    <p>قطع رابطه یا لغو دعوت باید از مسیر مدیریت کامل انجام شود تا confirmation و permissionهای قبلی حفظ شوند.</p>
                  </div>
                </div>
              </AppCard>
              <AppButton variant="danger" @click="openCompatibilityManager('manage')">
                ورود به اقدامات حساس
              </AppButton>
            </div>
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          title="لیست حسابداران"
          description="جستجو، فیلتر و انتخاب حسابدار بدون accordionهای تو در تو."
        >
          <div class="accountant-list-controls">
            <AppFormField label="جستجوی حسابدار" hint="نام، شماره موبایل، حساب یا شرح وظیفه را جستجو کنید.">
              <template #default="{ id }">
                <div class="accountant-search-field">
                  <Search :size="16" />
                  <AppInput :id="id" v-model="searchQuery" placeholder="مثلاً نرگس یا ثبت معاملات" />
                </div>
              </template>
            </AppFormField>
            <AppTabs v-model="relationFilter" label="فیلتر حسابداران" :options="relationFilterOptions" />
          </div>

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
          <div v-else class="accountant-master-detail-grid">
            <div class="workspace-relation-list">
              <div v-if="visiblePendingRelations.length" class="accountant-list-group">
                <h3>دعوت‌های در انتظار</h3>
                <AppListItem
                  v-for="relation in visiblePendingRelations"
                  :key="relation.id"
                  :title="getRelationTitle(relation)"
                  :description="getRelationDescription(relation)"
                  interactive
                  @select="openRelation(relation.id)"
                >
                  <template #leading>
                    <Clock :size="18" />
                  </template>
                  <template #trailing>
                    <AppStatusBadge tone="warning">
                      دعوت
                    </AppStatusBadge>
                  </template>
                </AppListItem>
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
          </div>
        </WorkspaceSection>

      </template>

      <template #aside>
        <WorkspaceSection
          v-if="!isCompatibilityManagerOpen"
          title="دسترسی‌های باقی‌مانده"
          description="تا پایان Stage 4 هیچ action فعلی حذف نمی‌شود."
        >
          <div class="workspace-side-actions">
            <AppActionCard
              title="دعوت‌های در انتظار"
              :description="`${accountantState.pendingInvitationRelations.value.length.toLocaleString('fa-IR')} دعوت در وضعیت انتظار`"
              tone="warning"
              @select="openCompatibilityManager('pending')"
            >
              <template #icon>
                <Clock :size="18" />
              </template>
            </AppActionCard>
            <AppActionCard
              title="مدیریت کامل"
              description="ویرایش شرح وظیفه، نشست‌ها و اقدامات حساس"
              tone="primary"
              @select="openCompatibilityManager('manage')"
            >
              <template #icon>
                <SlidersHorizontal :size="18" />
              </template>
            </AppActionCard>
          </div>
        </WorkspaceSection>
      </template>
    </WorkspaceShell>
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

.workspace-detail-card {
  display: grid;
  gap: 0.75rem;
}

.accountant-list-controls,
.accountant-detail-shell,
.accountant-detail-list {
  display: grid;
  gap: 0.85rem;
}

.accountant-search-field {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 0.55rem;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-md);
  background: var(--ds-surface);
  color: var(--ds-text-muted);
  padding-inline: 0.75rem 0;
}

.accountant-search-field :deep(.ui-input) {
  border: 0;
  background: transparent;
  box-shadow: none;
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

.workspace-detail-card h2,
.workspace-detail-card p {
  margin: 0;
}

.workspace-detail-card h2 {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-lg);
  font-weight: 900;
}

.workspace-detail-card p {
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.8;
}

.workspace-compatibility-panel {
  min-width: 0;
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
</style>
