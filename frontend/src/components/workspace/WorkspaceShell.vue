<script setup lang="ts">
import { computed, useId } from 'vue'
import AppDesignSystemScope from '../ui/AppDesignSystemScope.vue'
import AppWorkspace from '../ui/AppWorkspace.vue'

const props = withDefaults(defineProps<{
  title: string
  eyebrow?: string
  description?: string
  layout?: 'stack' | 'split'
  showBack?: boolean
  backLabel?: string
  v2Scope?: boolean
}>(), {
  layout: 'stack',
  showBack: false,
  backLabel: 'بازگشت',
  v2Scope: false,
})

defineEmits<{
  back: []
}>()

const headingId = useId()
const workspaceClasses = computed(() => [
  'ds-workspace',
  `ds-workspace--${props.layout}`,
  { 'ui-v2-workspace-adapter': props.v2Scope },
])
const workspaceRoot = computed(() => (props.v2Scope ? AppDesignSystemScope : 'section'))
const workspaceRootProps = computed(() => (
  props.v2Scope
    ? {
        as: AppWorkspace,
        narrow: props.layout === 'stack',
      }
    : {}
))
</script>

<template>
  <component
    :is="workspaceRoot"
    v-bind="workspaceRootProps"
    :class="workspaceClasses"
    :aria-labelledby="headingId"
  >
    <header
      class="ds-workspace-header"
      :class="{ 'ui-v2-workspace-adapter__header': v2Scope }"
    >
      <button
        v-if="showBack"
        type="button"
        class="ds-workspace-back"
        :class="{ 'ui-v2-workspace-adapter__back': v2Scope }"
        :aria-label="backLabel"
        @click="$emit('back')"
      >
        <span aria-hidden="true">‹</span>
      </button>

      <div
        class="ds-workspace-heading"
        :class="{ 'ui-v2-workspace-adapter__heading': v2Scope }"
      >
        <p v-if="eyebrow" class="ds-workspace-eyebrow">{{ eyebrow }}</p>
        <h1 :id="headingId">{{ title }}</h1>
        <p v-if="description" class="ds-workspace-description">{{ description }}</p>
      </div>

      <div
        v-if="$slots.actions"
        class="ds-workspace-actions"
        :class="{ 'ui-v2-workspace-adapter__actions': v2Scope }"
      >
        <slot name="actions" />
      </div>
    </header>

    <div
      v-if="$slots.toolbar"
      class="ds-workspace-toolbar"
      :class="{ 'ui-v2-workspace-adapter__toolbar': v2Scope }"
    >
      <slot name="toolbar" />
    </div>

    <div
      class="ds-workspace-body"
      :class="{ 'ui-v2-workspace-adapter__body': v2Scope }"
    >
      <component
        :is="v2Scope ? 'div' : 'main'"
        class="ds-workspace-main"
        :class="{ 'ui-v2-workspace-adapter__main': v2Scope }"
      >
        <slot />
      </component>
      <aside
        v-if="$slots.aside"
        class="ds-workspace-aside"
        :class="{ 'ui-v2-workspace-adapter__aside': v2Scope }"
      >
        <slot name="aside" />
      </aside>
    </div>
  </component>
</template>
