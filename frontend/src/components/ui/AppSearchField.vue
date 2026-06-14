<script setup lang="ts">
import { Search } from 'lucide-vue-next'

withDefaults(defineProps<{
  modelValue?: string
  placeholder?: string
  label?: string
}>(), {
  modelValue: '',
  placeholder: 'جستجو',
  label: 'جستجو',
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
      :value="modelValue"
      :placeholder="placeholder"
      @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
    >
  </label>
</template>
