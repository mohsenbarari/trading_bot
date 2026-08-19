<script setup lang="ts">
import { AppDangerZone } from '../ui'

withDefaults(defineProps<{
  title?: string
  description?: string
}>(), {
  title: 'منطقه خطر',
  description: '',
})
</script>

<template>
  <AppDangerZone :title="title" :description="description || undefined" data-test="profile-danger-zone">
    <slot />
  </AppDangerZone>
</template>
