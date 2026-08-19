<script setup lang="ts">
import { AppSectionCard } from '../ui'

withDefaults(defineProps<{
  title: string
  description?: string
}>(), {
  description: '',
})
</script>

<template>
  <section class="profile-section" data-test="profile-trade-history">
    <AppSectionCard
      class="profile-section-card card-with-help history-section-card"
      :title="title"
      :description="description || undefined"
    >
      <template v-if="$slots.actions" #actions>
        <slot name="actions" />
      </template>
      <div class="profile-section-card__body">
        <slot />
      </div>
    </AppSectionCard>
  </section>
</template>
