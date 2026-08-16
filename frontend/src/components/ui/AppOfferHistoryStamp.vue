<script setup lang="ts">
import { CircleCheckBig, Clock3 } from 'lucide-vue-next'

defineProps<{
  label: string
  traded?: boolean
}>()
</script>

<template>
  <span
    class="history-ribbon"
    :class="traded ? 'traded-ribbon' : 'expired-ribbon'"
    data-test="history-stamp"
  >
    <CircleCheckBig
      v-if="traded"
      class="history-ribbon__icon"
      :size="14"
      :stroke-width="2.25"
      aria-hidden="true"
    />
    <Clock3
      v-else
      class="history-ribbon__icon"
      :size="14"
      :stroke-width="2.25"
      aria-hidden="true"
    />
    <span>{{ label }}</span>
  </span>
</template>
