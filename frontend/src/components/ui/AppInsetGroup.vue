<script setup lang="ts">
defineProps<{
  title?: string
}>()
</script>

<template>
  <section class="ui-inset-group">
    <p v-if="title" class="ui-inset-group__title">{{ title }}</p>
    <div class="ui-inset-group__body">
      <slot />
    </div>
  </section>
</template>
