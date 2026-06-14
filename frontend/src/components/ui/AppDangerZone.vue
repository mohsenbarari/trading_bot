<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <section class="ui-danger-zone">
    <header class="ui-danger-zone__header">
      <h2>{{ title }}</h2>
      <p v-if="description">{{ description }}</p>
    </header>
    <div class="ui-danger-zone__body">
      <slot />
    </div>
  </section>
</template>
