<script setup lang="ts">
import AppDangerZone from '../ui/AppDangerZone.vue'

defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <AppDangerZone class="ds-danger-zone" :title="title" :description="description">
    <slot />
  </AppDangerZone>
</template>
