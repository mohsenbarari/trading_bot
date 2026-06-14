<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import OwnerCustomerManagerModal from '../components/OwnerCustomerManagerModal.vue'
import { WorkspaceShell } from '../components/workspace'

const route = useRoute()
const router = useRouter()

const relationId = computed(() => {
  const value = route.params.relationId
  return Array.isArray(value) ? value[0] ?? null : value ?? null
})

const initialPanel = computed(() => {
  const value = route.query.panel
  return Array.isArray(value) ? value[0] ?? null : value ?? null
})

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
  if (relationId.value) {
    backToList()
    return
  }
  goToOperations()
}
</script>

<template>
  <div class="ds-page customer-workspace-view">
    <WorkspaceShell
      title="مشتریان"
      eyebrow="عملیات"
      description="دعوت، مدیریت، محدودیت، آمار، نشست و قطع رابطه مشتریان در یک مسیر مستقل."
      show-back
      back-label="بازگشت"
      @back="handleBack"
    >
      <template #actions>
        <button type="button" class="customer-workspace-action" @click="goToOperations">
          عملیات
        </button>
      </template>

      <OwnerCustomerManagerModal
        presentation="workspace"
        :initial-relation-id="relationId"
        :initial-panel="initialPanel"
        @close="handleBack"
        @open-relation="openRelation"
        @back-to-list="backToList"
      />
    </WorkspaceShell>
  </div>
</template>

<style scoped>
.customer-workspace-view {
  min-height: 100%;
}

.customer-workspace-action {
  min-height: 2.5rem;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 0.875rem;
  background: rgba(255, 255, 255, 0.92);
  color: #334155;
  font: inherit;
  font-size: 0.82rem;
  font-weight: 850;
  padding: 0 0.95rem;
  cursor: pointer;
}
</style>
