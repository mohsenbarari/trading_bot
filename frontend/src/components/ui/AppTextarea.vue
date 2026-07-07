<script setup lang="ts">
import { ref } from 'vue'

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

const textareaRef = ref<HTMLTextAreaElement | null>(null)

function focus(options?: FocusOptions) {
  textareaRef.value?.focus(options)
}

function scrollIntoView(arg?: boolean | ScrollIntoViewOptions) {
  textareaRef.value?.scrollIntoView(arg)
}

defineExpose({
  focus,
  scrollIntoView,
})
</script>

<template>
  <textarea
    ref="textareaRef"
    class="ui-input ui-textarea"
    :class="{ 'is-invalid': invalid }"
    :value="modelValue"
    :aria-invalid="invalid || undefined"
    @input="$emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
  ></textarea>
</template>
