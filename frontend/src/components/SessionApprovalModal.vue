<script setup lang="ts">
import { computed, onBeforeUnmount, useId, watch } from 'vue'
import { X, Check, Smartphone, ShieldAlert, Image } from 'lucide-vue-next'
import { setSessionApprovalBlocking } from '../composables/authenticatedOverlayPriority'
import { useSessionApprovalRuntime } from '../composables/useSessionApprovalRuntime'
import { setSecurityLayerActive } from '../utils/securityLayerState'
import { UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE } from './ui/uiDesignSystemScope'
import AppBottomSheet from './ui/AppBottomSheet.vue'
import AppButton from './ui/AppButton.vue'

const props = withDefaults(defineProps<{ v2Portal?: boolean }>(), { v2Portal: false })
const dialogInstanceId = useId()
const dialogInstructionId = `${dialogInstanceId}-session-approval-instruction`
const portalScopeValue = computed(() =>
  props.v2Portal ? UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE : undefined,
)
const portalMotionStyle = computed(() =>
  props.v2Portal
    ? {
        transitionDuration: 'var(--ui-v2-motion-state)',
        transitionProperty: 'opacity',
      }
    : undefined,
)
const sessionBackdropAttrs = computed(() =>
  props.v2Portal
    ? {
        'data-ui-system': portalScopeValue.value,
        'data-ui-v2-motion': 'essential',
      }
    : undefined,
)

const {
  approve,
  approveRecovery,
  countdown,
  loading,
  openRecoveryThread,
  pendingRecovery,
  pendingRequest,
  reject,
  rejectRecovery,
  requestRecoveryIdentity,
  showModal,
} = useSessionApprovalRuntime()

const isRecoveryPrompt = computed(() => Boolean(pendingRecovery.value))
const isIdentitySubmittedPrompt = computed(
  () => pendingRecovery.value?.prompt_type === 'identity_submitted',
)
const dialogTitle = computed(() => {
  if (isIdentitySubmittedPrompt.value) return 'مدرک هویتی دریافت شد'
  if (isRecoveryPrompt.value) return 'درخواست بازیابی نشست'
  return 'درخواست ورود جدید'
})
const dialogDescription = computed(
  () => pendingRecovery.value?.message || 'آیا اجازه ورود از این دستگاه را می‌دهید؟',
)
const dialogInstruction =
  'برای ادامه، یکی از گزینه‌های امنیتی را انتخاب کنید. این پنجره بدون انتخاب بسته نمی‌شود.'
const requesterIp = computed(
  () => pendingRecovery.value?.requester_ip || pendingRequest.value?.device_ip || '—',
)
const requesterDevice = computed(
  () =>
    pendingRecovery.value?.requester_device_name ||
    pendingRequest.value?.device_name ||
    'دستگاه ناشناس',
)
const countdownLabel = computed(() => {
  if (countdown.value <= 0) return ''
  return `${Math.floor(countdown.value / 60)
    .toString()
    .padStart(2, '0')}:${(countdown.value % 60).toString().padStart(2, '0')}`
})
const sheetOpen = computed(() => Boolean(props.v2Portal && showModal.value))
const actionLayoutClass = computed(() =>
  isIdentitySubmittedPrompt.value
    ? 'session-approval-actions'
    : isRecoveryPrompt.value
      ? 'session-approval-actions session-approval-actions--triple'
      : 'session-approval-actions session-approval-actions--split',
)

const SECURITY_LAYER_ID = 'session-approval'
watch(
  [showModal, () => props.v2Portal],
  ([active, isV2Portal]) => {
    setSessionApprovalBlocking(Boolean(active))
    setSecurityLayerActive(SECURITY_LAYER_ID, Boolean(active && isV2Portal))
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  setSessionApprovalBlocking(false)
  setSecurityLayerActive(SECURITY_LAYER_ID, false)
})
</script>

<template>
  <AppBottomSheet
    :open="sheetOpen"
    :title="dialogTitle"
    :description="dialogDescription"
    :show-close="false"
    :close-on-backdrop="false"
    :close-on-escape="false"
    :busy="loading"
    initial-focus="container"
    trap-programmatic-focus
    :described-by-extra="dialogInstructionId"
    title-class="text-white"
    header-class="ui-v2-session-header session-approval-header"
    backdrop-class="ui-v2-session-layer z-[10000] session-approval-layer"
    :backdrop-style="portalMotionStyle"
    :backdrop-attrs="sessionBackdropAttrs"
    panel-class="ui-v2-session-card session-approval-sheet"
    body-class="ui-v2-session-body session-approval-body"
    :actions-class="actionLayoutClass"
  >
    <div class="session-approval-icon" aria-hidden="true">
      <Image
        v-if="isIdentitySubmittedPrompt"
        class="w-7 h-7 ui-v2-session-icon session-approval-icon__glyph"
      />
      <ShieldAlert
        v-else-if="isRecoveryPrompt"
        class="w-7 h-7 ui-v2-session-icon session-approval-icon__glyph"
      />
      <Smartphone v-else class="w-7 h-7 ui-v2-session-icon session-approval-icon__glyph" />
    </div>

    <div class="ui-v2-session-summary session-approval-summary">
      <div class="session-approval-summary__row">
        <span class="session-approval-summary__value dir-ltr">{{ requesterIp }}</span>
        <span class="session-approval-summary__label">آی‌پی</span>
      </div>
      <div class="session-approval-summary__row">
        <span class="session-approval-summary__value">{{ requesterDevice }}</span>
        <span class="session-approval-summary__label">دستگاه</span>
      </div>
      <div v-if="pendingRecovery?.user_name" class="session-approval-summary__row">
        <span class="session-approval-summary__value">{{ pendingRecovery.user_name }}</span>
        <span class="session-approval-summary__label">کاربر</span>
      </div>
    </div>

    <p :id="dialogInstructionId" class="session-approval-instruction">
      {{ dialogInstruction }}
    </p>
    <div v-if="countdownLabel" class="session-approval-countdown">{{ countdownLabel }}</div>

    <template #actions>
      <AppButton
        v-if="isIdentitySubmittedPrompt"
        type="button"
        class="ui-v2-session-action ui-v2-session-action--primary"
        :disabled="loading"
        :loading="loading"
        @click="openRecoveryThread"
      >
        <template #icon>
          <Image class="w-4 h-4" />
        </template>
        مشاهده تصویر کارت شناسایی
      </AppButton>
      <template v-else-if="isRecoveryPrompt">
        <AppButton
          type="button"
          variant="danger"
          class="ui-v2-session-action ui-v2-session-action--danger"
          :disabled="loading"
          @click="rejectRecovery"
        >
          <template #icon>
            <X class="w-4 h-4" />
          </template>
          رد
        </AppButton>
        <AppButton
          type="button"
          variant="secondary"
          class="ui-v2-session-action ui-v2-session-action--secondary"
          :disabled="loading"
          @click="requestRecoveryIdentity"
        >
          <template #icon>
            <Image class="w-4 h-4" />
          </template>
          درخواست مدرک
        </AppButton>
        <AppButton
          type="button"
          class="ui-v2-session-action ui-v2-session-action--success"
          :disabled="loading"
          :loading="loading"
          @click="approveRecovery"
        >
          <template #icon>
            <Check class="w-4 h-4" />
          </template>
          تایید
        </AppButton>
      </template>
      <template v-else>
        <AppButton
          type="button"
          variant="danger"
          class="ui-v2-session-action ui-v2-session-action--danger"
          :disabled="loading"
          @click="reject"
        >
          <template #icon>
            <X class="w-4 h-4" />
          </template>
          رد
        </AppButton>
        <AppButton
          type="button"
          class="ui-v2-session-action ui-v2-session-action--success"
          :disabled="loading"
          :loading="loading"
          @click="approve"
        >
          <template #icon>
            <Check class="w-4 h-4" />
          </template>
          تایید
        </AppButton>
      </template>
    </template>
  </AppBottomSheet>

  <Teleport v-if="!v2Portal" to="body">
    <transition name="fade">
      <div
        v-if="showModal"
        class="fixed inset-0 z-[9999] flex items-end justify-center p-0 bg-black/40 backdrop-blur-sm"
        @click.self="() => {}"
      >
        <div class="bg-white w-full max-w-md overflow-hidden rounded-t-2xl animate-scale-in">
          <div class="bg-gradient-to-r from-amber-500 to-amber-600 p-5 text-center">
            <div
              class="w-14 h-14 bg-white/20 rounded-full mx-auto flex items-center justify-center mb-3"
            >
              <Image v-if="isIdentitySubmittedPrompt" class="w-7 h-7 text-white" />
              <ShieldAlert v-else-if="isRecoveryPrompt" class="w-7 h-7 text-white" />
              <Smartphone v-else class="w-7 h-7 text-white" />
            </div>
            <h3 class="text-white font-bold text-lg">{{ dialogTitle }}</h3>
          </div>

          <div class="p-5 space-y-4">
            <div class="bg-gray-50 rounded-xl p-4 space-y-2 text-sm text-right">
              <div class="flex justify-between items-center">
                <span class="font-mono text-xs text-gray-500 dir-ltr">{{ requesterIp }}</span>
                <span class="text-gray-600 font-medium">آی‌پی</span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-gray-700">{{ requesterDevice }}</span>
                <span class="text-gray-600 font-medium">دستگاه</span>
              </div>
              <div v-if="pendingRecovery?.user_name" class="flex justify-between items-center">
                <span class="text-gray-700">{{ pendingRecovery.user_name }}</span>
                <span class="text-gray-600 font-medium">کاربر</span>
              </div>
            </div>

            <p class="text-xs text-gray-500 text-center leading-relaxed">
              {{ dialogDescription }}
            </p>

            <div v-if="countdownLabel" class="text-center text-xs text-gray-400 font-mono">
              {{ countdownLabel }}
            </div>

            <div v-if="isIdentitySubmittedPrompt" class="flex gap-3">
              <button
                @click="openRecoveryThread"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl bg-amber-500 text-white font-bold text-sm hover:bg-amber-600 transition-colors disabled:opacity-50"
              >
                <Image class="w-4 h-4 inline-block ml-1" />
                مشاهده تصویر کارت شناسایی
              </button>
            </div>
            <div v-else-if="isRecoveryPrompt" class="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <button
                @click="rejectRecovery"
                :disabled="loading"
                class="py-3 rounded-xl border border-red-200 text-red-600 font-bold text-sm hover:bg-red-50 transition-colors disabled:opacity-50"
              >
                <X class="w-4 h-4 inline-block ml-1" />
                رد
              </button>
              <button
                @click="requestRecoveryIdentity"
                :disabled="loading"
                class="py-3 rounded-xl border border-amber-200 text-amber-700 font-bold text-sm hover:bg-amber-50 transition-colors disabled:opacity-50"
              >
                <Image class="w-4 h-4 inline-block ml-1" />
                درخواست مدرک
              </button>
              <button
                @click="approveRecovery"
                :disabled="loading"
                class="py-3 rounded-xl bg-emerald-500 text-white font-bold text-sm hover:bg-emerald-600 transition-colors disabled:opacity-50"
              >
                <Check class="w-4 h-4 inline-block ml-1" />
                تایید
              </button>
            </div>
            <div v-else class="flex gap-3">
              <button
                @click="reject"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl border border-red-200 text-red-600 font-bold text-sm hover:bg-red-50 transition-colors disabled:opacity-50"
              >
                <X class="w-4 h-4 inline-block ml-1" />
                رد
              </button>
              <button
                @click="approve"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl bg-emerald-500 text-white font-bold text-sm hover:bg-emerald-600 transition-colors disabled:opacity-50"
              >
                <Check class="w-4 h-4 inline-block ml-1" />
                تایید
              </button>
            </div>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.session-approval-layer {
  z-index: 10030;
}

.session-approval-sheet {
  background: var(--ds-bg-card, #ffffff);
}

.session-approval-header :deep(h2) {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-lg);
  font-weight: 800;
}

.session-approval-icon {
  width: 3.5rem;
  height: 3.5rem;
  margin: 0 auto 0.75rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  background: var(--ds-primary-50, #fffbeb);
  color: var(--ds-primary-700, #b45309);
}

.session-approval-summary {
  display: grid;
  gap: 0.5rem;
  padding: 0.85rem;
  border-radius: 12px;
  background: var(--ds-bg-inset, #f8fafc);
}

.session-approval-summary__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  min-height: 1.5rem;
}

.session-approval-summary__label {
  color: var(--ds-text-secondary);
  font-weight: 700;
}

.session-approval-summary__value {
  color: var(--ds-text-primary);
  font-size: var(--ds-font-sm);
}

.session-approval-instruction,
.session-approval-countdown {
  margin: 0.85rem 0 0;
  text-align: center;
  color: var(--ds-text-muted);
  font-size: var(--ds-font-sm);
  line-height: 1.7;
}

.session-approval-countdown {
  font-family: ui-monospace, monospace;
}

.session-approval-actions {
  display: grid;
  gap: 0.75rem;
}

.session-approval-actions--split {
  grid-template-columns: 1fr 1fr;
}

.session-approval-actions--triple {
  grid-template-columns: 1fr;
}

@media (min-width: 640px) {
  .session-approval-actions--triple {
    grid-template-columns: 1fr 1fr 1fr;
  }
}

.animate-scale-in {
  animation: scaleIn 0.3s ease-out;
}

@keyframes scaleIn {
  from {
    transform: scale(0.9);
    opacity: 0;
  }
  to {
    transform: scale(1);
    opacity: 1;
  }
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .animate-scale-in,
  .fade-enter-active,
  .fade-leave-active {
    animation: none;
    transition: none;
  }
}
</style>
