<script setup lang="ts">
import { AppInsetGroup } from '../ui'

withDefaults(defineProps<{
  title: string
  description?: string
  sectionClass?: string
}>(), {
  description: '',
  sectionClass: '',
})
</script>

<template>
  <section class="profile-section" :class="sectionClass" data-test="profile-relationship-section">
    <AppInsetGroup :title="title">
      <slot />
    </AppInsetGroup>
  </section>
</template>
