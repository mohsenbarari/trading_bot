<script setup lang="ts">
withDefaults(defineProps<{
  label?: string
}>(), {
  label: 'در حال دریافت اطلاعات',
})
</script>

<template>
  <section class="ui-loading-state" role="status" aria-live="polite">
    <span class="ui-loading-state__spinner" aria-hidden="true"></span>
    <span>{{ label }}</span>
  </section>
</template>
