<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description?: string
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger'
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <section class="ds-workspace-section" :class="`ds-workspace-section--${tone}`">
    <header class="ds-workspace-section-header">
      <div class="ds-workspace-section-copy">
        <h2>{{ title }}</h2>
        <p v-if="description">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="ds-workspace-section-actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="ds-workspace-section-body">
      <slot />
    </div>
  </section>
</template>
