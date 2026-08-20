<script setup lang="ts">
import { computed } from 'vue'
import { ChevronLeft } from 'lucide-vue-next'
import AppListItem from '../ui/AppListItem.vue'
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

const emit = defineEmits<{
  select: []
}>()

const scopeAttributes = computed(() => (
  props.v2Scope ? getUiDesignSystemScopeAttributes() : {}
))

function handleSelect() {
  if (props.disabled) return
  emit('select')
}
</script>

<template>
  <AppListItem
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
    interactive
    :title="title"
    :description="description"
    :disabled="disabled"
    @select="handleSelect"
  >
    <template v-if="$slots.icon" #leading>
      <span class="ds-action-tile-icon">
        <slot name="icon" />
      </span>
    </template>
    <template #trailing>
      <span v-if="badge" class="ds-action-tile-badge">{{ badge }}</span>
      <ChevronLeft :size="18" aria-hidden="true" />
    </template>
  </AppListItem>
</template>

<style scoped>
.ds-action-tile {
  width: 100%;
  min-height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: 0;
  background: var(--ds-bg-card);
  box-shadow: inset 0 -1px 0 var(--ds-native-hairline);
}

.ds-action-tile.is-active {
  background: var(--ds-primary-50, #fffbeb);
}

.ds-action-tile :deep(.ui-list-item__trailing) {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  color: var(--ds-text-muted);
}

.ds-action-tile-badge {
  color: var(--ds-text-secondary);
  font-size: 0.78rem;
}
</style>
