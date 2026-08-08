<script setup lang="ts">
import { computed, ref, toRef } from 'vue'
import AppButton from './AppButton.vue'
import { useOverlayA11y } from './useOverlayA11y'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  message?: string
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'warning' | 'danger'
  busy?: boolean
  error?: string
  confirmDisabled?: boolean
}>(), {
  confirmLabel: 'تأیید',
  cancelLabel: 'انصراف',
  tone: 'warning',
  busy: false,
  confirmDisabled: false,
})

const toneLabel = computed(() => (props.tone === 'danger' ? 'اقدام حساس' : 'نیازمند تایید'))

const emit = defineEmits<{
  confirm: []
  cancel: []
}>()

const containerRef = ref<HTMLElement | null>(null)

const { titleId, descriptionId, ariaDescriptionId } = useOverlayA11y({
  open: toRef(props, 'open'),
  description: computed(() => props.message || props.error || undefined),
  containerRef,
  close: () => {
    if (!props.busy) emit('cancel')
  },
})
</script>

<template>
  <div v-if="open" class="ui-dialog-backdrop">
    <section
      ref="containerRef"
      class="ui-confirm-dialog"
      :class="`ui-confirm-dialog--${tone}`"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="ariaDescriptionId"
      tabindex="-1"
    >
      <header class="ui-confirm-dialog__header">
        <p class="ui-confirm-dialog__eyebrow">{{ toneLabel }}</p>
        <h2 :id="titleId">{{ title }}</h2>
        <p v-if="message" :id="descriptionId">{{ message }}</p>
        <p
          v-if="error"
          :id="message ? undefined : descriptionId"
          class="ui-form-field__error"
          role="alert"
        >
          {{ error }}
        </p>
      </header>
      <footer class="ui-confirm-dialog__actions">
        <AppButton variant="secondary" :disabled="busy" @click="$emit('cancel')">
          {{ cancelLabel }}
        </AppButton>
        <AppButton
          :variant="tone === 'danger' ? 'danger' : 'primary'"
          :loading="busy"
          :disabled="confirmDisabled"
          :aria-busy="busy ? 'true' : undefined"
          @click="$emit('confirm')"
        >
          {{ confirmLabel }}
        </AppButton>
      </footer>
    </section>
  </div>
</template>
