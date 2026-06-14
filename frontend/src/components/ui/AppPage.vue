<script setup lang="ts">
withDefaults(defineProps<{
  narrow?: boolean
}>(), {
  narrow: false,
})
</script>

<template>
  <main class="ui-page" :class="{ 'ui-page--narrow': narrow }">
    <slot />
  </main>
</template>
