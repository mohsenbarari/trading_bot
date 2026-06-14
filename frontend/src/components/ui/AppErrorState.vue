<script setup lang="ts">
defineProps<{
  title: string
  message?: string
}>()
</script>

<template>
  <section class="ui-empty-state ui-empty-state--danger" role="alert">
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
