<script setup lang="ts">
import { AppMetricCard } from '../ui'
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
    <div v-if="customerContext" class="customer-context-banner">
      <div class="customer-context-title">پروفایل مشتری</div>
      <p class="customer-context-copy">
        <slot name="customer-name">{{ customerContext.managementName }}</slot>
        <span v-if="customerContext.ownerAccountName"> | سرگروه: {{ customerContext.ownerAccountName }}</span>
        <span v-if="customerContext.showTier && customerTierLabel"> | {{ customerTierLabel }}</span>
      </p>
    </div>

    <section v-if="stats.length > 0" class="profile-stats-grid" aria-label="خلاصه وضعیت پروفایل">
      <AppMetricCard
        v-for="stat in stats"
        :key="stat.key"
        :label="stat.label"
        :value="stat.value"
        tone="neutral"
      />
    </section>

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
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
  width: 100%;
  max-width: var(--ds-page-max-width);
}

.customer-context-banner {
  width: 100%;
  max-width: min(100%, 520px);
  margin: 0 auto;
  padding: 12px 14px;
  border-radius: var(--ui-v2-radius-14, 16px);
  border: 1px solid var(--ds-primary-200);
  background: var(--ds-bg-card);
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
