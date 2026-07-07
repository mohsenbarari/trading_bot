<script setup lang="ts">
import AppActionCard from '../ui/AppActionCard.vue'

type WorkspaceTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

withDefaults(defineProps<{
  title: string
  description?: string
  badge?: string
  disabled?: boolean
  active?: boolean
  tone?: WorkspaceTone
}>(), {
  disabled: false,
  active: false,
  tone: 'neutral',
})

defineEmits<{
  select: []
}>()
</script>

<template>
  <AppActionCard
    class="ds-action-tile"
    :class="[`ds-action-tile--${tone}`, { 'is-active': active }]"
    :title="title"
    :description="description"
    :badge="badge"
    :disabled="disabled"
    :active="active"
    :tone="tone"
    @select="$emit('select')"
  >
    <template v-if="$slots.icon" #icon>
      <span class="ds-action-tile-icon">
        <slot name="icon" />
      </span>
    </template>
  </AppActionCard>
</template>
