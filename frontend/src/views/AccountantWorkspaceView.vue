<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { BriefcaseBusiness, Clock, SlidersHorizontal, UserPlus } from 'lucide-vue-next'
import OwnerAccountantManagerModal from '../components/OwnerAccountantManagerModal.vue'
import { WorkspaceNotice, WorkspaceSection, WorkspaceShell } from '../components/workspace'
import { AppActionCard, AppButton, AppListItem, AppMetricCard, AppStatusBadge } from '../components/ui'
import {
  fetchOwnerAccountantRelations,
  useOwnerAccountants,
  type AccountantRelation,
} from '../composables/useOwnerAccountants'

const route = useRoute()
const router = useRouter()
const accountantState = useOwnerAccountants()
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
  return accountantState.relations.value.find((relation) => relation.id === id) ?? null
})

const activeCount = computed(() => accountantState.relations.value.filter((relation) => relation.status === 'active').length)

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
          description="این نمای جدید جایگزین wrapper مستقیم شده و مدیریت کامل تا زمان Stage 4 از مسیر سازگار در دسترس است."
          tone="primary"
        >
          <div class="accountant-summary-grid">
            <AppMetricCard label="کل روابط" :value="accountantState.relations.value.length" />
            <AppMetricCard label="فعال" :value="activeCount" tone="success" />
            <AppMetricCard label="دعوت‌ها" :value="accountantState.pendingInvitationRelations.value.length" tone="warning" />
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          v-if="relationIdNumber"
          title="جزئیات حسابدار"
          description="جزئیات کامل در Stage 4 به tabs route-native منتقل می‌شود؛ فعلاً مدیریت کامل از مسیر سازگار باز می‌شود."
        >
          <WorkspaceNotice
            v-if="!activeRelation && !isLoading"
            tone="warning"
            title="حسابدار پیدا نشد"
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
              مدیریت کامل حسابدار
            </AppButton>
          </div>
        </WorkspaceSection>

        <WorkspaceSection
          title="لیست حسابداران"
          description="انتخاب هر حسابدار مسیر detail را باز می‌کند؛ مدیریت کامل بدون حذف قابلیت‌های فعلی باقی مانده است."
        >
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
          <div v-else class="workspace-relation-list">
            <AppListItem
              v-for="relation in accountantState.orderedRelations.value"
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
  grid-template-columns: repeat(3, minmax(0, 1fr));
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
  .accountant-summary-grid {
    grid-template-columns: 1fr;
  }
}
</style>
