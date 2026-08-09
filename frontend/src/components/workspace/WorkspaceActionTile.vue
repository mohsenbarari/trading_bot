<script setup lang="ts">
import { computed } from 'vue'
import AppActionCard from '../ui/AppActionCard.vue'
import { getUiDesignSystemScopeAttributes } from '../ui/uiDesignSystemScope'

type WorkspaceTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const props = withDefaults(defineProps<{
  title: string
  description?: string
  badge?: string
  disabled?: boolean
  active?: boolean
  tone?: WorkspaceTone
  v2Scope?: boolean
}>(), {
  disabled: false,
  active: false,
  tone: 'neutral',
  v2Scope: false,
})

defineEmits<{
  select: []
}>()

const scopeAttributes = computed(() => (
  props.v2Scope ? getUiDesignSystemScopeAttributes() : {}
))
</script>

<template>
  <AppActionCard
    v-bind="scopeAttributes"
    class="ds-action-tile"
    :class="[
      `ds-action-tile--${tone}`,
      {
        'is-active': active,
        'ui-v2-scope': v2Scope,
        'ui-v2-workspace-action-adapter': v2Scope,
      },
    ]"
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
