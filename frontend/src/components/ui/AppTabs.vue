<script setup lang="ts">
interface AppTabOption {
  key: string
  label: string
  disabled?: boolean
}

const props = defineProps<{
  modelValue: string
  options: AppTabOption[]
  label: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function selectOption(key: string, disabled?: boolean) {
  if (disabled) return
  emit('update:modelValue', key)
}

function handleKeydown(event: KeyboardEvent, index: number) {
  const enabledOptions = props.options
    .map((option, optionIndex) => ({ ...option, optionIndex }))
    .filter(option => !option.disabled)
  if (!enabledOptions.length) return

  const currentIndex = enabledOptions.findIndex(option => option.optionIndex === index)
  let nextIndex = currentIndex

  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
    nextIndex = currentIndex <= 0 ? enabledOptions.length - 1 : currentIndex - 1
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    nextIndex = currentIndex >= enabledOptions.length - 1 ? 0 : currentIndex + 1
  } else if (event.key === 'Home') {
    nextIndex = 0
  } else if (event.key === 'End') {
    nextIndex = enabledOptions.length - 1
  } else {
    return
  }

  event.preventDefault()
  emit('update:modelValue', enabledOptions[nextIndex]!.key)
}
</script>

<template>
  <div class="ui-tabs" role="tablist" :aria-label="label">
    <button
      v-for="(option, index) in options"
      :key="option.key"
      type="button"
      class="ui-tabs__tab"
      :class="{ 'is-active': modelValue === option.key }"
      role="tab"
      :aria-selected="modelValue === option.key"
      :tabindex="modelValue === option.key ? 0 : -1"
      :disabled="option.disabled"
      @click="selectOption(option.key, option.disabled)"
      @keydown="handleKeydown($event, index)"
    >
      {{ option.label }}
    </button>
  </div>
</template>
