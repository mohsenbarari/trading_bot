<script setup lang="ts">
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
    {{ label }}
  </span>
</template>
