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
