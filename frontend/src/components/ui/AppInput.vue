<script setup lang="ts">
const props = withDefaults(defineProps<{
  modelValue?: string | number
  invalid?: boolean
  modelModifiers?: {
    number?: boolean
    trim?: boolean
  }
}>(), {
  modelValue: '',
  invalid: false,
  modelModifiers: () => ({}),
})

const emit = defineEmits<{
  'update:modelValue': [value: string | number]
}>()

function normalizeInputValue(rawValue: string) {
  const value = props.modelModifiers.trim ? rawValue.trim() : rawValue
  if (!props.modelModifiers.number) {
    return value
  }
  const parsed = Number.parseFloat(value)
  return Number.isNaN(parsed) ? value : parsed
}

function handleInput(event: Event) {
  emit('update:modelValue', normalizeInputValue((event.target as HTMLInputElement).value))
}
</script>

<template>
  <input
    class="ui-input"
    :class="{ 'is-invalid': invalid }"
    :value="modelValue"
    :aria-invalid="invalid || undefined"
    @input="handleInput"
  >
</template>
