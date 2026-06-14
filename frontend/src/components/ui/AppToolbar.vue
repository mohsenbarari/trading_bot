<script setup lang="ts">
withDefaults(defineProps<{
  stacked?: boolean
}>(), {
  stacked: false,
})
</script>

<template>
  <div class="ui-toolbar" :class="{ 'ui-toolbar--stacked': stacked }">
    <div v-if="$slots.leading" class="ui-toolbar__leading">
      <slot name="leading" />
    </div>
    <div v-if="$slots.default" class="ui-toolbar__content">
      <slot />
    </div>
    <div v-if="$slots.actions" class="ui-toolbar__actions">
      <slot name="actions" />
    </div>
  </div>
</template>
