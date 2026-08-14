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
    <svg
      v-if="hasTimer"
      class="offer-deadline-perimeter"
      data-test="offer-deadline-perimeter"
      :data-phase="timerOvertime ? 'overtime' : timerCritical ? 'critical' : 'normal'"
      :data-critical="timerCritical ? 'true' : 'false'"
      aria-hidden="true"
      focusable="false"
    >
      <rect
        class="offer-deadline-perimeter__track"
        x="1.5"
        y="1.5"
        width="calc(100% - 3px)"
        height="calc(100% - 3px)"
        rx="12"
        pathLength="100"
      />
      <rect
        class="offer-deadline-perimeter__value"
        x="1.5"
        y="1.5"
        width="calc(100% - 3px)"
        height="calc(100% - 3px)"
        rx="12"
        pathLength="100"
      />
    </svg>
    <slot />
  </div>
</template>

<style scoped>
.offer-deadline-perimeter {
  position: absolute;
  inset: 0;
  z-index: 4;
  width: 100%;
  height: 100%;
  overflow: visible;
  pointer-events: none;
}

.offer-deadline-perimeter__track,
.offer-deadline-perimeter__value {
  fill: none;
  vector-effect: non-scaling-stroke;
}

.offer-deadline-perimeter__track {
  stroke: color-mix(in srgb, var(--ds-border-medium) 72%, transparent);
  stroke-width: 2px;
}

.offer-deadline-perimeter__value {
  stroke: var(--ds-primary-600);
  stroke-width: 2.5px;
  stroke-linecap: round;
  stroke-dasharray: var(--t-pct, 100) 100;
  transition: stroke-dasharray 0.9s linear, stroke 0.2s ease;
}

.offer-card-wrap.timer-overtime .offer-deadline-perimeter__value {
  stroke: var(--ds-warning-600);
}

.offer-card-wrap.timer-critical .offer-deadline-perimeter__value {
  stroke: var(--ds-danger-600);
}

@media (prefers-reduced-motion: reduce) {
  .offer-deadline-perimeter__value {
    transition: none;
  }
}
</style>
