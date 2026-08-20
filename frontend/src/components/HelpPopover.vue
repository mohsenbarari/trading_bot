<script setup lang="ts">
import { Info } from 'lucide-vue-next'
import { onUnmounted, ref } from 'vue'

withDefaults(defineProps<{
  text: string
  label?: string
  buttonTest?: string
  noteTest?: string
  floating?: boolean
  comfortableTarget?: boolean
}>(), {
  label: 'توضیحات',
  buttonTest: undefined,
  noteTest: undefined,
  floating: false,
  comfortableTarget: false,
})

const isOpen = ref(false)
const timerId = ref<number | null>(null)

function clearTimer() {
  if (timerId.value !== null) {
    window.clearTimeout(timerId.value)
    timerId.value = null
  }
}

function showHelp() {
  isOpen.value = true
  clearTimer()
  timerId.value = window.setTimeout(() => {
    isOpen.value = false
    timerId.value = null
  }, 6000)
}

onUnmounted(clearTimer)
</script>

<template>
  <span
    class="help-popover"
    :class="{
      'help-popover--floating': floating,
      'help-popover--comfortable-target': comfortableTarget,
    }"
  >
    <button
      type="button"
      class="help-popover-trigger"
      :data-test="buttonTest"
      :aria-label="label"
      @click.stop="showHelp"
    >
      <Info :size="18" />
    </button>
    <span v-if="isOpen" class="help-popover-note" :data-test="noteTest">
      {{ text }}
    </span>
  </span>
</template>

<style scoped>
.help-popover {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
}

.help-popover--floating {
  position: absolute;
  top: 1rem;
  left: 1rem;
  z-index: 3;
}

.help-popover-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-sizing: border-box;
  width: 3rem;
  height: 3rem;
  min-width: 3rem;
  min-height: 3rem;
  border: 1px solid var(--ds-native-hairline, rgba(60, 60, 67, 0.14));
  border-radius: 999px;
  background: var(--ds-bg-inset, #f8fafc);
  color: var(--ds-text-secondary, #475569);
  cursor: pointer;
  transition: border-color 0.2s ease, color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
}

.help-popover--comfortable-target .help-popover-trigger {
  box-sizing: border-box;
  inline-size: 3rem;
  block-size: 3rem;
  min-inline-size: 3rem;
  min-block-size: 3rem;
}

.help-popover-trigger:hover,
.help-popover-trigger:focus-visible {
  color: var(--ds-primary-700, #b45309);
  border-color: var(--ds-primary-200, #fde68a);
  background: var(--ds-primary-50, #fffbeb);
  box-shadow: none;
  outline: none;
}

.help-popover-note {
  position: absolute;
  top: calc(100% + 0.45rem);
  left: 0;
  width: min(17rem, calc(100vw - 2rem));
  padding: 0.72rem 0.85rem;
  border-radius: 12px;
  border: 1px solid var(--ds-native-hairline, rgba(60, 60, 67, 0.14));
  background: var(--ds-bg-card, #ffffff);
  color: var(--ds-text-primary, #0f172a);
  font-size: 0.78rem;
  font-weight: 700;
  line-height: 1.8;
  text-align: right;
  box-shadow: none;
  white-space: normal;
  z-index: 20;
}

.help-popover-note::before {
  content: '';
  position: absolute;
  top: -6px;
  left: 12px;
  width: 12px;
  height: 12px;
  background: inherit;
  border-left: 1px solid var(--ds-native-hairline, rgba(60, 60, 67, 0.14));
  border-top: 1px solid var(--ds-native-hairline, rgba(60, 60, 67, 0.14));
  transform: rotate(45deg);
}
</style>
