<script setup lang="ts">
import AppSectionCard from '../ui/AppSectionCard.vue'

type WorkspaceTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

withDefaults(defineProps<{
  title: string
  description?: string
  tone?: WorkspaceTone
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <AppSectionCard
    class="ds-workspace-section"
    :class="`ds-workspace-section--${tone}`"
    :title="title"
    :description="description"
    :tone="tone"
  >
    <template v-if="$slots.actions" #actions>
      <div class="ds-workspace-section-actions">
        <slot name="actions" />
      </div>
    </template>
    <slot />
  </AppSectionCard>
</template>
