<script setup lang="ts">
import { computed, useId } from 'vue'

const props = defineProps<{
  label: string
  hint?: string
  error?: string
  id?: string
}>()

const generatedId = useId()
const fieldId = computed(() => props.id || generatedId)
const hintId = computed(() => `${fieldId.value}-hint`)
const errorId = computed(() => `${fieldId.value}-error`)
</script>

<template>
  <label class="ui-form-field" :for="fieldId">
    <span class="ui-form-field__label">{{ label }}</span>
    <slot :id="fieldId" :describedby="error ? errorId : hint ? hintId : undefined" :invalid="Boolean(error)" />
    <span v-if="error" :id="errorId" class="ui-form-field__error" role="alert">{{ error }}</span>
    <span v-else-if="hint" :id="hintId" class="ui-form-field__hint">{{ hint }}</span>
  </label>
</template>
