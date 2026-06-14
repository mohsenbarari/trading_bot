<script setup lang="ts">
withDefaults(defineProps<{
  modelValue?: string | number
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
  <input
    class="ui-input"
    :class="{ 'is-invalid': invalid }"
    :value="modelValue"
    :aria-invalid="invalid || undefined"
    @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  >
</template>
