<script setup lang="ts">
import { computed, useId } from 'vue'

const props = withDefaults(defineProps<{
  title: string
  eyebrow?: string
  description?: string
  layout?: 'stack' | 'split'
  showBack?: boolean
  backLabel?: string
}>(), {
  layout: 'stack',
  showBack: false,
  backLabel: 'بازگشت',
})

defineEmits<{
  back: []
}>()

const headingId = useId()
const workspaceClasses = computed(() => [
  'ds-workspace',
  `ds-workspace--${props.layout}`,
])
</script>

<template>
  <section :class="workspaceClasses" :aria-labelledby="headingId">
    <header class="ds-workspace-header">
      <button
        v-if="showBack"
        type="button"
        class="ds-workspace-back"
        :aria-label="backLabel"
        @click="$emit('back')"
      >
        <span aria-hidden="true">‹</span>
      </button>

      <div class="ds-workspace-heading">
        <p v-if="eyebrow" class="ds-workspace-eyebrow">{{ eyebrow }}</p>
        <h1 :id="headingId">{{ title }}</h1>
        <p v-if="description" class="ds-workspace-description">{{ description }}</p>
      </div>

      <div v-if="$slots.actions" class="ds-workspace-actions">
        <slot name="actions" />
      </div>
    </header>

    <div v-if="$slots.toolbar" class="ds-workspace-toolbar">
      <slot name="toolbar" />
    </div>

    <div class="ds-workspace-body">
      <main class="ds-workspace-main">
        <slot />
      </main>
      <aside v-if="$slots.aside" class="ds-workspace-aside">
        <slot name="aside" />
      </aside>
    </div>
  </section>
</template>
