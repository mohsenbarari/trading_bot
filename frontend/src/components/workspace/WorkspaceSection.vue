<script setup lang="ts">
import { computed } from 'vue'
import AppSectionCard from '../ui/AppSectionCard.vue'
import { getUiDesignSystemScopeAttributes } from '../ui/uiDesignSystemScope'

type WorkspaceTone = 'neutral' | 'primary' | 'success' | 'warning' | 'danger'

const props = withDefaults(defineProps<{
  title: string
  description?: string
  tone?: WorkspaceTone
  v2Scope?: boolean
}>(), {
  tone: 'neutral',
  v2Scope: false,
})

const scopeAttributes = computed(() => (
  props.v2Scope ? getUiDesignSystemScopeAttributes() : {}
))
</script>

<template>
  <AppSectionCard
    v-bind="scopeAttributes"
    class="ds-workspace-section"
    :class="[
      `ds-workspace-section--${tone}`,
      {
        'ui-v2-scope': v2Scope,
        'ui-v2-workspace-section-adapter': v2Scope,
      },
    ]"
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
