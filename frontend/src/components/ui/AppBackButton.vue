<script setup lang="ts">
import { ChevronRight } from 'lucide-vue-next'

withDefaults(defineProps<{
  label?: string
  text?: string
  disabled?: boolean
}>(), {
  label: 'بازگشت',
  text: '',
  disabled: false,
})
</script>

<template>
  <button
    type="button"
    class="ui-back-button"
    :aria-label="label"
    :disabled="disabled"
  >
    <ChevronRight :size="22" aria-hidden="true" />
    <span v-if="text" class="ui-back-button__text">{{ text }}</span>
  </button>
</template>
