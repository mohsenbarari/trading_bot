<script setup lang="ts">
withDefaults(defineProps<{
  modelValue?: string
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
  <textarea
    class="ui-input ui-textarea"
    :class="{ 'is-invalid': invalid }"
    :value="modelValue"
    :aria-invalid="invalid || undefined"
    @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
  ></textarea>
</template>
