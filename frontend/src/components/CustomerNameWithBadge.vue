<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  name?: string | null
  fallback?: string
  badgeLabel?: string
  compact?: boolean
}>(), {
  fallback: 'مشتری',
  badgeLabel: 'مشتری',
  compact: false,
})

const displayName = computed(() => {
  const name = (props.name || '').trim()
  return name || props.fallback
})
</script>

<template>
  <span class="customer-name-with-badge" :class="{ compact }">
    <span class="customer-name-with-badge__name">{{ displayName }}</span>
    <span class="customer-name-with-badge__badge">{{ badgeLabel }}</span>
  </span>
</template>

<style scoped>
.customer-name-with-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  vertical-align: middle;
}

.customer-name-with-badge__name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.customer-name-with-badge__badge {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 20px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid rgba(15, 118, 110, 0.22);
  background: rgba(15, 118, 110, 0.1);
  color: #0f766e;
  font-size: 0.66rem;
  font-weight: 900;
  line-height: 1;
  white-space: nowrap;
}

.customer-name-with-badge.compact {
  gap: 5px;
}

.customer-name-with-badge.compact .customer-name-with-badge__badge {
  min-height: 18px;
  padding: 1px 7px;
  font-size: 0.6rem;
}
</style>
