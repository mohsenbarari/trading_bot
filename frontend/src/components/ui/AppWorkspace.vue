<script setup lang="ts">
withDefaults(defineProps<{
  narrow?: boolean
}>(), {
  narrow: false,
})
</script>

<template>
  <main class="ui-workspace" :class="{ 'ui-workspace--narrow': narrow }">
    <slot />
  </main>
</template>
