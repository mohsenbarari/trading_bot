<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { X, Check, Smartphone, ShieldAlert, Image } from 'lucide-vue-next'
import { setSessionApprovalBlocking } from '../composables/authenticatedOverlayPriority'
import { useSessionApprovalRuntime } from '../composables/useSessionApprovalRuntime'
import { setSecurityLayerActive } from '../utils/securityLayerState'
import { UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE } from './ui/uiDesignSystemScope'

const props = withDefaults(defineProps<{ v2Portal?: boolean }>(), { v2Portal: false })
const dialogInstanceId = useId()
const dialogTitleId = `${dialogInstanceId}-session-approval-title`
const dialogDescriptionId = `${dialogInstanceId}-session-approval-description`
const dialogInstructionId = `${dialogInstanceId}-session-approval-instruction`
const dialogRef = ref<HTMLElement | null>(null)
const portalScopeValue = computed(() =>
  props.v2Portal ? UI_DESIGN_SYSTEM_PORTAL_SCOPE_VALUE : undefined,
)
const transitionName = computed(() => (props.v2Portal ? 'ui-v2-session-fade' : 'fade'))
const portalMotionStyle = computed(() =>
  props.v2Portal
    ? {
        transitionDuration: 'var(--ui-v2-motion-state)',
        transitionProperty: 'opacity',
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
const dialogInstruction = computed(() =>
  isIdentitySubmittedPrompt.value
    ? 'برای ادامه، تصویر کارت شناسایی را بررسی کنید.'
    : 'برای ادامه، یکی از گزینه‌های امنیتی را انتخاب کنید. این پنجره بدون انتخاب بسته نمی‌شود.',
)

const FOCUSABLE_SELECTOR = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

let previouslyFocusedElement: HTMLElement | null = null

const getFocusableElements = () =>
  dialogRef.value
    ? Array.from(dialogRef.value.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (element) =>
          !element.hasAttribute('hidden') && element.getAttribute('aria-hidden') !== 'true',
      )
    : []

const focusDialog = () => {
  dialogRef.value?.focus({ preventScroll: true })
}

const restorePreviousFocus = () => {
  const focusTarget = previouslyFocusedElement
  previouslyFocusedElement = null
  if (focusTarget?.isConnected) focusTarget.focus({ preventScroll: true })
}

const keepProgrammaticFocusInside = (event: FocusEvent) => {
  if (!props.v2Portal || !showModal.value || !dialogRef.value) return
  if (event.target instanceof Node && !dialogRef.value.contains(event.target)) focusDialog()
}

const attachFocusBoundary = () => {
  document.addEventListener('focusin', keepProgrammaticFocusInside, true)
}

const detachFocusBoundary = () => {
  document.removeEventListener('focusin', keepProgrammaticFocusInside, true)
}

const handleDialogKeydown = (event: KeyboardEvent) => {
  if (!props.v2Portal) return

  if (event.key === 'Escape') {
    // This prompt requires an explicit security decision. Escape must never
    // imply approval, rejection, or dismissal.
    event.preventDefault()
    event.stopPropagation()
    return
  }

  if (event.key !== 'Tab') return

  const focusableElements = getFocusableElements()
  if (focusableElements.length === 0) {
    event.preventDefault()
    focusDialog()
    return
  }

  const firstElement = focusableElements[0]!
  const lastElement = focusableElements[focusableElements.length - 1]!
  const activeElement = document.activeElement

  if (!dialogRef.value?.contains(activeElement)) {
    event.preventDefault()
    firstElement.focus()
  } else if (
    event.shiftKey &&
    (activeElement === firstElement || activeElement === dialogRef.value)
  ) {
    event.preventDefault()
    lastElement.focus()
  } else if (!event.shiftKey && activeElement === lastElement) {
    event.preventDefault()
    firstElement.focus()
  }
}

const SECURITY_LAYER_ID = 'session-approval'
watch(
  [showModal, () => props.v2Portal],
  async ([active, isV2Portal]) => {
    setSessionApprovalBlocking(Boolean(active))
    const shouldActivateV2Layer = active && isV2Portal
    setSecurityLayerActive(SECURITY_LAYER_ID, shouldActivateV2Layer)

    if (shouldActivateV2Layer) {
      if (
        document.activeElement instanceof HTMLElement &&
        document.activeElement !== document.body
      ) {
        previouslyFocusedElement = document.activeElement
      }
      attachFocusBoundary()
      await nextTick()
      if (showModal.value && props.v2Portal) focusDialog()
      return
    }

    detachFocusBoundary()
    restorePreviousFocus()
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  setSessionApprovalBlocking(false)
  setSecurityLayerActive(SECURITY_LAYER_ID, false)
  detachFocusBoundary()
  restorePreviousFocus()
})
</script>

<template>
  <Teleport to="body">
    <transition :name="transitionName">
      <div
        v-if="showModal"
        :class="
          v2Portal
            ? 'ui-v2-session-layer fixed inset-0 z-[10000] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm'
            : 'fixed inset-0 z-[9999] flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm'
        "
        :data-ui-system="portalScopeValue"
        :data-ui-v2-motion="v2Portal ? 'essential' : undefined"
        :style="portalMotionStyle"
        @click.self="() => {}"
      >
        <div
          ref="dialogRef"
          :class="
            v2Portal
              ? 'ui-v2-session-card bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden'
              : 'bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden animate-scale-in'
          "
          :role="v2Portal ? 'dialog' : undefined"
          :aria-modal="v2Portal ? 'true' : undefined"
          :aria-labelledby="v2Portal ? dialogTitleId : undefined"
          :aria-describedby="v2Portal ? `${dialogDescriptionId} ${dialogInstructionId}` : undefined"
          :aria-busy="v2Portal ? (loading ? 'true' : 'false') : undefined"
          :tabindex="v2Portal ? -1 : undefined"
          @keydown="handleDialogKeydown"
        >
          <!-- Header -->
          <div
            class="bg-gradient-to-r from-amber-500 to-amber-600 p-5 text-center"
            :class="{ 'ui-v2-session-header': v2Portal }"
          >
            <div
              class="w-14 h-14 bg-white/20 rounded-full mx-auto flex items-center justify-center mb-3"
            >
              <Image
                v-if="isIdentitySubmittedPrompt"
                class="w-7 h-7 text-white"
                :class="{ 'ui-v2-session-icon': v2Portal }"
              />
              <ShieldAlert
                v-else-if="isRecoveryPrompt"
                class="w-7 h-7 text-white"
                :class="{ 'ui-v2-session-icon': v2Portal }"
              />
              <Smartphone
                v-else
                class="w-7 h-7 text-white"
                :class="{ 'ui-v2-session-icon': v2Portal }"
              />
            </div>
            <h2 v-if="v2Portal" :id="dialogTitleId" class="text-white font-bold text-lg">
              {{ dialogTitle }}
            </h2>
            <h3 v-else class="text-white font-bold text-lg">{{ dialogTitle }}</h3>
          </div>

          <!-- Body -->
          <div class="p-5 space-y-4" :class="{ 'ui-v2-session-body': v2Portal }">
            <div
              class="bg-gray-50 rounded-xl p-4 space-y-2 text-sm text-right"
              :class="{ 'ui-v2-session-summary': v2Portal }"
            >
              <div class="flex justify-between items-center">
                <span class="font-mono text-xs text-gray-500 dir-ltr">{{
                  pendingRecovery?.requester_ip || pendingRequest?.device_ip || '—'
                }}</span>
                <span class="text-gray-600 font-medium">آی‌پی</span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-gray-700">{{
                  pendingRecovery?.requester_device_name ||
                  pendingRequest?.device_name ||
                  'دستگاه ناشناس'
                }}</span>
                <span class="text-gray-600 font-medium">دستگاه</span>
              </div>
              <div v-if="pendingRecovery?.user_name" class="flex justify-between items-center">
                <span class="text-gray-700">{{ pendingRecovery.user_name }}</span>
                <span class="text-gray-600 font-medium">کاربر</span>
              </div>
            </div>

            <p
              :id="v2Portal ? dialogDescriptionId : undefined"
              class="text-xs text-gray-500 text-center leading-relaxed"
            >
              {{ dialogDescription }}
            </p>

            <p
              v-if="v2Portal"
              :id="dialogInstructionId"
              class="text-xs text-gray-500 text-center leading-relaxed"
            >
              {{ dialogInstruction }}
            </p>

            <div v-if="countdown > 0" class="text-center text-xs text-gray-400 font-mono">
              {{
                Math.floor(countdown / 60)
                  .toString()
                  .padStart(2, '0')
              }}:{{ (countdown % 60).toString().padStart(2, '0') }}
            </div>

            <!-- Actions -->
            <div v-if="isIdentitySubmittedPrompt" class="flex gap-3">
              <button
                :type="v2Portal ? 'button' : undefined"
                @click="openRecoveryThread"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl bg-amber-500 text-white font-bold text-sm hover:bg-amber-600 transition-colors disabled:opacity-50"
                :class="{
                  'ui-v2-session-action ui-v2-session-action--primary': v2Portal,
                }"
              >
                <Image class="w-4 h-4 inline-block ml-1" />
                مشاهده تصویر کارت شناسایی
              </button>
            </div>
            <div v-else-if="isRecoveryPrompt" class="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <button
                :type="v2Portal ? 'button' : undefined"
                @click="rejectRecovery"
                :disabled="loading"
                class="py-3 rounded-xl border border-red-200 text-red-600 font-bold text-sm hover:bg-red-50 transition-colors disabled:opacity-50"
                :class="{
                  'ui-v2-session-action ui-v2-session-action--danger': v2Portal,
                }"
              >
                <X class="w-4 h-4 inline-block ml-1" />
                رد
              </button>
              <button
                :type="v2Portal ? 'button' : undefined"
                @click="requestRecoveryIdentity"
                :disabled="loading"
                class="py-3 rounded-xl border border-amber-200 text-amber-700 font-bold text-sm hover:bg-amber-50 transition-colors disabled:opacity-50"
                :class="{
                  'ui-v2-session-action ui-v2-session-action--secondary': v2Portal,
                }"
              >
                <Image class="w-4 h-4 inline-block ml-1" />
                درخواست مدرک
              </button>
              <button
                :type="v2Portal ? 'button' : undefined"
                @click="approveRecovery"
                :disabled="loading"
                class="py-3 rounded-xl bg-emerald-500 text-white font-bold text-sm hover:bg-emerald-600 transition-colors disabled:opacity-50"
                :class="{
                  'ui-v2-session-action ui-v2-session-action--success': v2Portal,
                }"
              >
                <Check class="w-4 h-4 inline-block ml-1" />
                تایید
              </button>
            </div>
            <div v-else class="flex gap-3">
              <button
                :type="v2Portal ? 'button' : undefined"
                @click="reject"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl border border-red-200 text-red-600 font-bold text-sm hover:bg-red-50 transition-colors disabled:opacity-50"
                :class="{
                  'ui-v2-session-action ui-v2-session-action--danger': v2Portal,
                }"
              >
                <X class="w-4 h-4 inline-block ml-1" />
                رد
              </button>
              <button
                :type="v2Portal ? 'button' : undefined"
                @click="approve"
                :disabled="loading"
                class="flex-1 py-3 rounded-xl bg-emerald-500 text-white font-bold text-sm hover:bg-emerald-600 transition-colors disabled:opacity-50"
                :class="{
                  'ui-v2-session-action ui-v2-session-action--success': v2Portal,
                }"
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
</style>
