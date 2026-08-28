<script setup lang="ts">
import { Search } from 'lucide-vue-next'

withDefaults(defineProps<{
  modelValue?: string
  placeholder?: string
  label?: string
  disabled?: boolean
}>(), {
  modelValue: '',
  placeholder: 'جستجو',
  label: 'جستجو',
  disabled: false,
})

defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>

<template>
  <label class="ui-search-field">
    <span class="sr-only">{{ label }}</span>
    <Search :size="18" aria-hidden="true" />
    <input
      type="search"
      :aria-label="label"
      :value="modelValue"
      :placeholder="placeholder"
      :disabled="disabled"
      @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    >
  </label>
</template>
