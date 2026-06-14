<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { UserPlus, Users, Clock, SlidersHorizontal } from 'lucide-vue-next'
import OwnerCustomerManagerModal from '../components/OwnerCustomerManagerModal.vue'
import { WorkspaceNotice, WorkspaceSection, WorkspaceShell } from '../components/workspace'
import { AppActionCard, AppButton, AppListItem, AppMetricCard, AppStatusBadge } from '../components/ui'
import {
  fetchOwnerCustomerRelations,
  useOwnerCustomers,
  type CustomerRelation,
} from '../composables/useOwnerCustomers'

const route = useRoute()
const router = useRouter()
const customerState = useOwnerCustomers()
const isLoading = ref(true)
const error = ref('')
const isCompatibilityManagerOpen = ref(false)
const compatibilityPanel = ref<string | null>(null)

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

watch(initialPanel, (panel) => {
  if (panel === 'create' || panel === 'pending' || panel === 'manage' || panel === 'relations') {
    openCompatibilityManager(panel)
  }
}, { immediate: true })

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
          description="این نمای جدید جایگزین wrapper مستقیم شده و مدیریت کامل تا زمان Stage 3 از مسیر سازگار در دسترس است."
          tone="primary"
        >
          <div class="customer-summary-grid">
            <AppMetricCard label="کل روابط" :value="customerState.relations.value.length" />
            <AppMetricCard label="فعال" :value="activeCount" tone="success" />
            <AppMetricCard label="دعوت‌ها" :value="customerState.pendingInvitationRelations.value.length" tone="warning" />
            <AppMetricCard label="سطح ۲" :value="tier2Count" tone="primary" />
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          v-if="relationIdNumber"
          title="جزئیات مشتری"
          description="جزئیات کامل در Stage 3 به tabs route-native منتقل می‌شود؛ فعلاً مدیریت کامل از مسیر سازگار باز می‌شود."
        >
          <WorkspaceNotice
            v-if="!activeRelation && !isLoading"
            tone="warning"
            title="مشتری پیدا نشد"
            message="این رابطه در لیست فعلی وجود ندارد یا هنوز همگام‌سازی نشده است."
          />
          <div v-else-if="activeRelation" class="workspace-detail-card">
            <div>
              <h2>{{ getRelationTitle(activeRelation) }}</h2>
              <p>{{ getRelationDescription(activeRelation) }}</p>
            </div>
            <AppStatusBadge :tone="getStatusTone(activeRelation.status)">
              {{ getStatusLabel(activeRelation.status) }}
            </AppStatusBadge>
            <AppButton block variant="primary" @click="openCompatibilityManager('manage')">
              مدیریت کامل مشتری
            </AppButton>
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          title="لیست مشتریان"
          description="انتخاب هر مشتری مسیر detail را باز می‌کند؛ مدیریت کامل بدون حذف قابلیت‌های فعلی باقی مانده است."
        >
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
          <div v-else class="workspace-relation-list">
            <AppListItem
              v-for="relation in customerState.orderedRelations.value"
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
                <AppStatusBadge :tone="getStatusTone(relation.status)">
                  {{ getStatusLabel(relation.status) }}
                </AppStatusBadge>
              </template>
            </AppListItem>
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
  grid-template-columns: repeat(2, minmax(0, 1fr));
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
  .customer-summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
