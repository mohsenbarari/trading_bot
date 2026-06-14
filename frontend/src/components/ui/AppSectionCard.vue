<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  description?: string
  tone?: 'neutral' | 'primary' | 'success' | 'warning' | 'danger' | 'info'
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <section class="ui-section-card" :class="`ui-section-card--${tone}`">
    <header class="ui-section-card__header">
      <div class="ui-section-card__copy">
        <h2>{{ title }}</h2>
        <p v-if="description">{{ description }}</p>
      </div>
      <div v-if="$slots.actions" class="ui-section-card__actions">
        <slot name="actions" />
      </div>
    </header>
    <div class="ui-section-card__body">
      <slot />
    </div>
  </section>
</template>
