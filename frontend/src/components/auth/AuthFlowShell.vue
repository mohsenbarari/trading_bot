<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    title: string
    description?: string
    currentStep?: number
    totalSteps?: number
    focused?: boolean
    fillViewport?: boolean
  }>(),
  {
    description: '',
    currentStep: undefined,
    totalSteps: undefined,
    focused: false,
    fillViewport: false,
  },
)

const hasProgress = computed(
  () =>
    Number.isInteger(props.currentStep) &&
    Number.isInteger(props.totalSteps) &&
    Number(props.currentStep) > 0 &&
    Number(props.totalSteps) > 0 &&
    Number(props.currentStep) <= Number(props.totalSteps),
)

const progressSegments = computed(() =>
  hasProgress.value
    ? Array.from({ length: Number(props.totalSteps) }, (_, index) => index + 1)
    : [],
)

const persianNumber = new Intl.NumberFormat('fa-IR', { useGrouping: false })
const progressLabel = computed(() =>
  hasProgress.value
    ? `مرحله ${persianNumber.format(Number(props.currentStep))} از ${persianNumber.format(Number(props.totalSteps))}`
    : '',
)
</script>

<template>
  <main
    class="auth-shell ui-v2-auth-flow"
    :class="{
      'auth-shell--fill': fillViewport,
      'ui-v2-auth-flow--focused': focused,
      'ui-v2-auth-flow--viewport-fill': fillViewport,
    }"
  >
    <header class="auth-shell__brand ui-v2-public-header">
      <strong>سامانه معاملات</strong>
      <img src="/uiux-v2-brand-mark.svg" width="28" height="28" alt="" aria-hidden="true" />
    </header>

    <div class="auth-shell__content ui-v2-auth-flow__content">
      <div v-if="hasProgress" class="ui-v2-auth-progress" aria-label="پیشرفت فرایند">
        <span>{{ progressLabel }}</span>
        <span class="ui-v2-auth-progress__bars" aria-hidden="true">
          <span
            v-for="segment in progressSegments"
            :key="segment"
            :class="{ 'is-current': segment <= Number(currentStep) }"
          />
        </span>
      </div>

      <div class="auth-shell__heading ui-v2-auth-flow__heading">
        <h1>{{ title }}</h1>
        <p v-if="description">{{ description }}</p>
      </div>

      <slot />
    </div>
  </main>
</template>

<style scoped>
.auth-shell {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  min-height: 100%;
  overflow-x: clip;
  padding-top: var(--ds-safe-area-top, env(safe-area-inset-top, 0px));
  background: var(--ds-native-grouped-bg, #f2f2f7);
  color: var(--ds-text-primary);
}

.auth-shell--fill {
  min-height: 100dvh;
  display: flex;
  flex-direction: column;
}

.auth-shell__brand {
  min-height: var(--ds-native-row-min-height, 48px);
  padding-inline: var(--ds-page-padding, 16px);
  background: transparent;
}

.auth-shell__brand strong {
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-xs);
}

.auth-shell__content {
  flex: 1 1 auto;
  padding-inline: var(--ds-page-padding, 16px);
  padding-bottom: calc(24px + var(--ds-safe-area-bottom, env(safe-area-inset-bottom, 0px)));
}

.auth-shell__heading h1 {
  margin: 0;
  color: var(--ds-text-primary);
  font-size: var(--ds-native-title-size, 1.7rem);
  font-weight: 800;
  line-height: 1.25;
}

.auth-shell__heading p {
  margin: 8px 0 0;
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.6;
}
</style>
