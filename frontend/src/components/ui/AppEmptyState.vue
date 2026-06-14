<script setup lang="ts">
withDefaults(defineProps<{
  title: string
  message?: string
  tone?: 'neutral' | 'info' | 'warning' | 'danger'
}>(), {
  tone: 'neutral',
})
</script>

<template>
  <section class="ui-empty-state" :class="`ui-empty-state--${tone}`">
    <div v-if="$slots.icon" class="ui-empty-state__icon" aria-hidden="true">
      <slot name="icon" />
    </div>
    <h2>{{ title }}</h2>
    <p v-if="message">{{ message }}</p>
    <div v-if="$slots.actions" class="ui-empty-state__actions">
      <slot name="actions" />
    </div>
  </section>
</template>
