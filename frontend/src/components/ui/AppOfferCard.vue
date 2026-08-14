<script setup lang="ts">
import type { StyleValue } from 'vue'

withDefaults(defineProps<{
  timerCritical?: boolean
  hasTimer?: boolean
  timerOvertime?: boolean
  history?: boolean
  expired?: boolean
  traded?: boolean
  decisionFocus?: boolean
  timerStyle?: StyleValue
}>(), {
  timerCritical: false,
  hasTimer: false,
  timerOvertime: false,
  history: false,
  expired: false,
  traded: false,
  decisionFocus: false,
})
</script>

<template>
  <div
    class="offer-card-wrap"
    :class="{
      'timer-critical': timerCritical,
      'has-timer': hasTimer,
      'timer-overtime': timerOvertime,
      'is-history': history,
      'is-expired': expired,
      'is-traded': traded,
      'is-decision-focus': decisionFocus,
    }"
    :style="timerStyle"
    data-test="offer-card"
    :data-decision-focus="decisionFocus ? 'true' : 'false'"
  >
    <slot />
  </div>
</template>
