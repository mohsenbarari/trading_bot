<script setup lang="ts">
import { computed } from 'vue'
import {
  normalizeSettlementType,
  offerSettlementLabel,
  tradeSettlementLabel,
} from '../../utils/settlementType'

const props = defineProps<{
  settlementType?: unknown
  context?: 'offer' | 'trade'
}>()

const normalizedType = computed(() => normalizeSettlementType(props.settlementType))
const label = computed(() => (
  props.context === 'trade'
    ? tradeSettlementLabel(props.settlementType)
    : offerSettlementLabel(props.settlementType)
))
const ariaLabel = computed(() => `نوع تسویه: ${label.value}`)
</script>

<template>
  <span
    class="ui-settlement-badge"
    :class="`ui-settlement-badge--${normalizedType}`"
    :data-settlement-type="normalizedType"
    :aria-label="ariaLabel"
    data-test="settlement-badge"
  >
    <span class="ui-settlement-badge__caption">تسویه:</span>
    <span>{{ label }}</span>
  </span>
</template>

<style scoped>
.ui-settlement-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.28rem;
  min-height: 28px;
  padding: 0.2rem 0.55rem;
  border: 1px solid transparent;
  border-radius: 7px;
  font-size: 0.74rem;
  font-weight: 900;
  line-height: 1.25;
  white-space: nowrap;
}

.ui-settlement-badge__caption {
  font-size: 0.67rem;
  font-weight: 750;
  opacity: 0.76;
}

.ui-settlement-badge--cash {
  color: var(--ds-warning-700);
  background: var(--ds-warning-100);
  border-color: var(--ds-warning-500);
}

.ui-settlement-badge--tomorrow {
  color: var(--ds-info-700);
  background: var(--ds-info-50);
  border-color: var(--ds-info-500);
}
</style>
