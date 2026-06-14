<script setup lang="ts">
import { Minus, Plus } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  modelValue?: number
  min?: number
  max?: number
  step?: number
  label?: string
  invalid?: boolean
}>(), {
  modelValue: 0,
  step: 1,
  label: 'مقدار عددی',
  invalid: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: number]
}>()

function normalize(value: number) {
  let next = Number.isFinite(value) ? value : 0
  if (props.min != null) next = Math.max(props.min, next)
  if (props.max != null) next = Math.min(props.max, next)
  return Number(next.toFixed(4))
}

function updateFromInput(value: string) {
  emit('update:modelValue', normalize(Number(value)))
}

function stepBy(direction: 1 | -1) {
  emit('update:modelValue', normalize(Number(props.modelValue || 0) + direction * props.step))
}
</script>

<template>
  <div class="ui-number-stepper" :class="{ 'is-invalid': invalid }">
    <button type="button" :aria-label="`کاهش ${label}`" @click="stepBy(-1)">
      <Minus :size="16" aria-hidden="true" />
    </button>
    <input
      type="number"
      inputmode="decimal"
      :aria-label="label"
      :value="modelValue"
      :min="min"
      :max="max"
      :step="step"
      :aria-invalid="invalid || undefined"
      @input="updateFromInput(($event.target as HTMLInputElement).value)"
    >
    <button type="button" :aria-label="`افزایش ${label}`" @click="stepBy(1)">
      <Plus :size="16" aria-hidden="true" />
    </button>
  </div>
</template>
