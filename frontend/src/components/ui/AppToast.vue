<script setup lang="ts">
withDefaults(defineProps<{
  title?: string
  message: string
  tone?: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
}>(), {
  title: '',
  tone: 'neutral',
})
</script>

<template>
  <div class="ui-toast" :class="`ui-toast--${tone}`" role="status" aria-live="polite">
    <strong v-if="title">{{ title }}</strong>
    <span>{{ message }}</span>
  </div>
</template>
