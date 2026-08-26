<script setup lang="ts">
import { AppInsetGroup, AppListItem } from '../ui'
import type { ProfileCustomerContext, ProfileStatItem } from './types'

withDefaults(defineProps<{
  stats?: ProfileStatItem[]
  customerContext?: ProfileCustomerContext | null
  customerTierLabel?: string
}>(), {
  stats: () => [],
  customerContext: null,
  customerTierLabel: '',
})
</script>

<template>
  <section class="profile-section shared-profile-section" data-test="profile-summary">
    <AppInsetGroup v-if="customerContext" class="customer-context-group">
      <div class="customer-context-banner">
        <div class="customer-context-title">پروفایل مشتری</div>
        <p class="customer-context-copy">
          <slot name="customer-name">{{ customerContext.managementName }}</slot>
          <span v-if="customerContext.ownerAccountName"> | سرگروه: {{ customerContext.ownerAccountName }}</span>
          <span v-if="customerContext.showTier && customerTierLabel"> | {{ customerTierLabel }}</span>
        </p>
      </div>
    </AppInsetGroup>

    <AppInsetGroup v-if="stats.length > 0">
      <section class="profile-stats-grid" aria-label="خلاصه وضعیت پروفایل">
        <AppListItem
          v-for="stat in stats"
          :key="stat.key"
          :title="stat.label"
          :meta="stat.value"
        />
      </section>
    </AppInsetGroup>

    <slot />
  </section>
</template>

<style scoped>
.shared-profile-section {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  width: 100%;
  min-width: 0;
}

.profile-stats-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
  width: 100%;
  min-width: 0;
}

.customer-context-banner {
  width: 100%;
  margin: 0;
  padding: 0.85rem 1rem;
  border: 0;
  border-radius: 0;
  background: transparent;
  text-align: right;
}

.customer-context-title {
  margin-bottom: 6px;
  font-size: 0.94rem;
  font-weight: 800;
  color: var(--ds-text-primary);
}

.customer-context-copy {
  margin: 0;
  overflow-wrap: anywhere;
}
</style>
