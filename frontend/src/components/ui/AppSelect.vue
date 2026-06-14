<script setup lang="ts">
interface AppSelectOption {
  value: string
  label: string
}

withDefaults(defineProps<{
  modelValue?: string
  options: AppSelectOption[]
  invalid?: boolean
}>(), {
  modelValue: '',
  invalid: false,
})

defineEmits<{
  'update:modelValue': [value: string]
}>()
</script>

<template>
  <select
    class="ui-input ui-select"
    :class="{ 'is-invalid': invalid }"
    :value="modelValue"
    :aria-invalid="invalid || undefined"
    @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <option v-for="option in options" :key="option.value" :value="option.value">
      {{ option.label }}
    </option>
  </select>
</template>
