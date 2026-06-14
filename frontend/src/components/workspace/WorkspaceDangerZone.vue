<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <section class="ds-danger-zone">
    <header class="ds-danger-zone-header">
      <div>
        <h2>{{ title }}</h2>
        <p v-if="description">{{ description }}</p>
      </div>
    </header>
    <div class="ds-danger-zone-body">
      <slot />
    </div>
  </section>
</template>
