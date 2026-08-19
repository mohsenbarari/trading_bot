<script setup lang="ts">
import { AppSectionCard } from '../ui'

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
    <AppSectionCard class="profile-section-card card-with-help" :title="title" :description="description || undefined">
      <template v-if="$slots.actions" #actions>
        <slot name="actions" />
      </template>
      <div class="profile-section-card__body">
        <slot />
      </div>
    </AppSectionCard>
  </section>
</template>
