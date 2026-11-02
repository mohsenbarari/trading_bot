<script setup lang="ts">
import { AppInsetGroup } from '../ui'

withDefaults(defineProps<{
  title: string
  description?: string
}>(), {
  description: '',
})
</script>

<template>
  <section class="profile-section" data-test="profile-trade-history">
    <AppInsetGroup class="history-section-card" :title="title">
      <slot />
    </AppInsetGroup>
  </section>
</template>

<style scoped>
/* History date popovers extend beyond the inset group's rounded body. */
:deep(.history-section-card > .ui-inset-group__body) {
  overflow: visible;
}
</style>
