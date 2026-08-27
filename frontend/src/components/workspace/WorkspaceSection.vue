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
    class="ds-workspace-section ds-workspace-section--plain"
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

<style scoped>
.ds-workspace-section.ui-section-card,
.ds-workspace-section--primary,
.ds-workspace-section--success,
.ds-workspace-section--warning,
.ds-workspace-section--danger {
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
}

.ds-workspace-section :deep(.ui-section-card__header) {
  padding: 0 1rem 0.4rem;
  border: 0;
}

.ds-workspace-section :deep(.ui-section-card__body) {
  padding: 0;
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  overflow: visible;
}
</style>
