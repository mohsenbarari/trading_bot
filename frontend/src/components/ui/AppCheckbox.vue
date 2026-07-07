<script setup lang="ts">
import { computed } from 'vue'

type CheckboxValue = string | number | boolean

const props = withDefaults(defineProps<{
  modelValue?: boolean | CheckboxValue[]
  value?: CheckboxValue
  trueValue?: CheckboxValue
  falseValue?: CheckboxValue
  disabled?: boolean
  invalid?: boolean
}>(), {
  modelValue: false,
  value: true,
  trueValue: true,
  falseValue: false,
  disabled: false,
  invalid: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean | CheckboxValue[] | CheckboxValue]
}>()

const isChecked = computed(() => {
  if (Array.isArray(props.modelValue)) {
    return props.modelValue.includes(props.value)
  }
  return props.modelValue === props.trueValue
})

function handleChange(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  if (Array.isArray(props.modelValue)) {
    const nextValues = [...props.modelValue]
    const index = nextValues.indexOf(props.value)
    if (checked && index === -1) {
      nextValues.push(props.value)
    } else if (!checked && index !== -1) {
      nextValues.splice(index, 1)
    }
    emit('update:modelValue', nextValues)
    return
  }
  emit('update:modelValue', checked ? props.trueValue : props.falseValue)
}
</script>

<template>
  <input
    type="checkbox"
    class="ui-checkbox"
    :checked="isChecked"
    :disabled="disabled"
    :aria-invalid="invalid || undefined"
    @change="handleChange"
  >
</template>
