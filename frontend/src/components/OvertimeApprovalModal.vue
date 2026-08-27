<script setup lang="ts">
import { Hourglass } from 'lucide-vue-next'
import { useOvertimeApprovalRuntime } from '../composables/useOvertimeApprovalRuntime'
import {
  M12_CANCEL_BUTTON,
  M15_CANCELLED,
  M21_REQUESTER_QUEUED,
  M35_OWNER_TITLE,
  M36_OWNER_APPROVE,
  M36_OWNER_REJECT,
} from '../constants/offerOvertimeCopy'
import AppBottomSheet from './ui/AppBottomSheet.vue'
import AppButton from './ui/AppButton.vue'

const {
  approve,
  cancel,
  loading,
  ownerCountdownLabel,
  ownerError,
  ownerOfferText,
  ownerQuantityLine,
  ownerVisible,
  reject,
  requesterCountdownLabel,
  requesterMessage,
  requesterTerminalNotice,
  requesterVisible,
} = useOvertimeApprovalRuntime()
</script>

<template>
  <AppBottomSheet
    :open="ownerVisible"
    :title="M35_OWNER_TITLE"
    :show-close="false"
    :close-on-backdrop="false"
    :close-on-escape="false"
    :busy="loading"
    initial-focus="container"
    trap-programmatic-focus
    backdrop-class="overtime-owner-layer"
    panel-class="overtime-owner-sheet"
    body-class="overtime-owner-body"
    actions-class="overtime-owner-actions"
  >
    <div class="overtime-owner-icon" aria-hidden="true">
      <Hourglass class="w-7 h-7" />
    </div>
    <pre v-if="ownerOfferText" class="overtime-offer-text">{{ ownerOfferText }}</pre>
    <p v-if="ownerQuantityLine" class="overtime-quantity">{{ ownerQuantityLine }}</p>
    <div class="overtime-countdown" aria-live="polite">{{ ownerCountdownLabel }}</div>
    <p v-if="ownerError" class="overtime-error" role="alert">{{ ownerError }}</p>
    <template #actions>
      <AppButton type="button" variant="danger" :disabled="loading" @click="reject">
        {{ M36_OWNER_REJECT }}
      </AppButton>
      <AppButton type="button" :disabled="loading" :loading="loading" @click="approve">
        {{ M36_OWNER_APPROVE }}
      </AppButton>
    </template>
  </AppBottomSheet>

  <Teleport to="body">
    <transition name="fade">
      <div
        v-if="requesterVisible"
        class="overtime-requester-banner"
        role="status"
        aria-live="polite"
      >
        <div class="overtime-requester-card">
          <template v-if="requesterTerminalNotice">
            <p>{{ requesterTerminalNotice || M15_CANCELLED }}</p>
          </template>
          <template v-else>
            <p>
              <template v-if="requesterMessage">{{ requesterMessage }}</template>
              <span v-else class="overtime-requester-countdown">
                {{ requesterCountdownLabel || M21_REQUESTER_QUEUED }}
              </span>
            </p>
            <AppButton type="button" variant="secondary" :disabled="loading" @click="cancel">
              {{ M12_CANCEL_BUTTON }}
            </AppButton>
          </template>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.overtime-owner-layer {
  z-index: 9997;
}

.overtime-owner-icon {
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

.overtime-offer-text {
  margin: 0;
  padding: 0.85rem;
  border-radius: 12px;
  background: var(--ds-bg-inset, #f8fafc);
  color: var(--ds-text-secondary);
  font-size: var(--ds-font-sm);
  line-height: 1.6;
  white-space: pre-wrap;
  text-align: right;
  font-family: inherit;
}

.overtime-quantity {
  margin: 0.75rem 0 0;
  text-align: center;
  font-size: var(--ds-font-sm);
  color: var(--ds-primary-700, #b45309);
  font-weight: 700;
}

.overtime-countdown {
  margin-top: 0.75rem;
  text-align: center;
  font-family: ui-monospace, monospace;
  font-size: var(--ds-font-sm);
  color: var(--ds-text-muted);
}

.overtime-error {
  margin: 0.75rem 0 0;
  text-align: center;
  font-size: var(--ds-font-sm);
  color: var(--ds-danger-700, #b91c1c);
}

.overtime-owner-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.overtime-requester-banner {
  position: fixed;
  left: 0;
  right: 0;
  bottom: calc(4.5rem + env(safe-area-inset-bottom, 0px));
  z-index: 9996;
  display: flex;
  justify-content: center;
  padding: 0 16px;
  pointer-events: none;
}

.overtime-requester-card {
  width: 100%;
  max-width: 28rem;
  pointer-events: auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.85rem 1rem;
  border-radius: 12px;
  background: var(--ds-text-primary, #111827);
  color: var(--ds-bg-card, #ffffff);
}

.overtime-requester-card p {
  margin: 0;
  font-size: var(--ds-font-sm);
  line-height: 1.5;
}

.overtime-requester-countdown {
  display: inline-block;
  margin-inline-start: 0.4rem;
  font-family: ui-monospace, monospace;
  color: var(--ds-success-100, #d1fae5);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.18s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .fade-enter-active,
  .fade-leave-active {
    transition: none;
  }
}
</style>
