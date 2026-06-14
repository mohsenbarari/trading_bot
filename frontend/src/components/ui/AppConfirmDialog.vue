<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'warning' | 'danger'
}>(), {
  confirmLabel: 'تأیید',
  cancelLabel: 'انصراف',
  tone: 'warning',
})

const toneLabel = computed(() => (props.tone === 'danger' ? 'اقدام حساس' : 'نیازمند تایید'))

defineEmits<{
  confirm: []
  cancel: []
}>()
</script>

<template>
  <div v-if="open" class="ui-dialog-backdrop">
    <section
      class="ui-confirm-dialog"
      :class="`ui-confirm-dialog--${tone}`"
      role="dialog"
      aria-modal="true"
      aria-labelledby="ui-confirm-dialog-title"
    >
      <header class="ui-confirm-dialog__header">
        <p class="ui-confirm-dialog__eyebrow">{{ toneLabel }}</p>
        <h2 id="ui-confirm-dialog-title">{{ title }}</h2>
        <p v-if="message">{{ message }}</p>
      </header>
      <footer class="ui-confirm-dialog__actions">
        <button type="button" class="ui-button ui-button--secondary ui-button--md" @click="$emit('cancel')">
          {{ cancelLabel }}
        </button>
        <button
          type="button"
          class="ui-button ui-button--md"
          :class="tone === 'danger' ? 'ui-button--danger' : 'ui-button--primary'"
          @click="$emit('confirm')"
        >
          {{ confirmLabel }}
        </button>
      </footer>
    </section>
  </div>
</template>
