<script setup lang="ts">
import CustomerNameWithBadge from '../CustomerNameWithBadge.vue'

defineProps<{
  managementName?: string | null
  tierLabel?: string | null
}>()
</script>

<template>
  <div class="customer-context-row" data-test="customer-context-row">
    <CustomerNameWithBadge
      v-if="managementName"
      :name="managementName"
      compact
      data-test="customer-context-name-badge"
    />
    <span v-else class="customer-context-badge" data-test="customer-context-badge">مشتری</span>
    <span v-if="tierLabel" class="customer-context-tier" data-test="customer-context-tier">{{ tierLabel }}</span>
  </div>
</template>

<style scoped>
.customer-context-row {
  display: flex;
  align-items: center;
  gap: 5px;
  margin-top: 5px;
  flex-wrap: wrap;
}

.customer-context-badge,
.customer-context-tier {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  padding: 2px 7px;
  font-size: 10.5px;
  font-weight: 800;
  line-height: 1;
}

.customer-context-badge {
  color: #92400e;
  background: rgba(251, 191, 36, 0.2);
  border: 1px solid rgba(245, 158, 11, 0.35);
}

.customer-context-tier {
  color: #1d4ed8;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.22);
}
</style>
