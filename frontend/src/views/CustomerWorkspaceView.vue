<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BarChart3, Clock, ReceiptText, Search, ShieldAlert, SlidersHorizontal, UserPlus, Users } from 'lucide-vue-next'
import OwnerCustomerManagerModal from '../components/OwnerCustomerManagerModal.vue'
import { WorkspaceNotice, WorkspaceSection, WorkspaceShell } from '../components/workspace'
import { AppActionCard, AppButton, AppCard, AppFormField, AppInput, AppListItem, AppMetricCard, AppStatusBadge, AppTabs } from '../components/ui'
import {
  fetchOwnerCustomerRelations,
  fetchOwnerCustomerSessions,
  fetchOwnerCustomerTradeStats,
  fetchOwnerCustomerTrades,
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
const error = ref('')
const isCompatibilityManagerOpen = ref(false)
const compatibilityPanel = ref<string | null>(null)
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
  if (panel === 'create' || panel === 'pending' || panel === 'manage' || panel === 'relations') {
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

watch(statsPeriodDays, () => {
  if (detailTab.value === 'stats') {
    void loadDetailStats(true)
  }
})

onMounted(() => {
  void loadRelations()
})
</script>

<template>
  <div class="ds-page customer-workspace-view">
    <WorkspaceShell
      title="مشتریان"
      eyebrow="عملیات"
      description="نمای route-native برای مرور مشتریان، وضعیت دعوت‌ها و ورود به مدیریت کامل."
      layout="split"
      show-back
      back-label="بازگشت"
      @back="handleBack"
    >
      <template #actions>
        <AppButton variant="secondary" class="customer-workspace-action" @click="goToOperations">
          عملیات
        </AppButton>
        <AppButton variant="primary" class="customer-workspace-create" @click="openCompatibilityManager('create')">
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
          description="مشخصات، محدودیت‌ها، معاملات، آمار، نشست‌ها و اقدامات حساس در یک نمای tabدار."
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
              <AppListItem
                v-for="item in activeRelationLimits"
                :key="item.label"
                :title="item.label"
                :description="item.description"
                :meta="item.value"
              />
              <AppButton variant="secondary" @click="openCompatibilityManager('manage')">
                ویرایش سطح و محدودیت‌ها
              </AppButton>
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

            <div v-else class="customer-detail-list">
              <AppCard tone="danger">
                <div class="customer-danger-card">
                  <ShieldAlert :size="22" />
                  <div>
                    <strong>اقدامات حساس مشتری</strong>
                    <p>لغو دعوت یا قطع رابطه باید از مسیر مدیریت کامل انجام شود تا confirmation و permissionهای قبلی حفظ شوند.</p>
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
          title="لیست مشتریان"
          description="جستجو، فیلتر و انتخاب مشتری بدون accordionهای تو در تو."
        >
          <div class="customer-list-controls">
            <AppFormField label="جستجوی مشتری" hint="نام، شماره موبایل، نام حساب یا وضعیت را جستجو کنید.">
              <template #default="{ id }">
                <div class="customer-search-field">
                  <Search :size="16" />
                  <AppInput :id="id" v-model="searchQuery" placeholder="مثلاً حسن یا 0912" />
                </div>
              </template>
            </AppFormField>
            <AppTabs v-model="relationFilter" label="فیلتر مشتریان" :options="relationFilterOptions" />
          </div>

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
          <div v-else class="customer-master-detail-grid">
            <div class="workspace-relation-list">
              <div v-if="visiblePendingRelations.length" class="customer-list-group">
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
          </div>
        </WorkspaceSection>

      </template>

      <template #aside>
        <WorkspaceSection
          v-if="!isCompatibilityManagerOpen"
          title="دسترسی‌های باقی‌مانده"
          description="تا پایان Stage 3 هیچ action فعلی حذف نمی‌شود."
        >
          <div class="workspace-side-actions">
            <AppActionCard
              title="دعوت‌های در انتظار"
              :description="`${customerState.pendingInvitationRelations.value.length.toLocaleString('fa-IR')} دعوت در وضعیت انتظار`"
              tone="warning"
              @select="openCompatibilityManager('pending')"
            >
              <template #icon>
                <Clock :size="18" />
              </template>
            </AppActionCard>
            <AppActionCard
              title="مدیریت کامل"
              description="ویرایش محدودیت‌ها، معاملات، آمار، نشست‌ها و اقدامات حساس"
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

.customer-search-field {
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

.customer-search-field :deep(.ui-input) {
  border: 0;
  background: transparent;
  box-shadow: none;
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

.workspace-detail-card {
  display: grid;
  gap: 0.75rem;
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
  .customer-summary-grid,
  .customer-detail-grid,
  .customer-stats-grid {
    grid-template-columns: 1fr;
  }

  .customer-detail-header {
    grid-template-columns: 1fr;
  }

  .customer-detail-badges,
  .customer-list-badges {
    justify-content: flex-start;
  }
}
</style>
