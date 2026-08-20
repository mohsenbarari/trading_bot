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
  <Teleport to="body">
    <transition name="fade">
      <div
        v-if="ownerVisible"
        class="overtime-owner-overlay"
        role="dialog"
        aria-modal="true"
        :aria-label="M35_OWNER_TITLE"
        @click.self="() => {}"
      >
        <div class="overtime-owner-card">
          <div class="overtime-owner-header">
            <div class="overtime-owner-icon" aria-hidden="true">
              <Hourglass class="w-7 h-7" />
            </div>
            <h3>{{ M35_OWNER_TITLE }}</h3>
          </div>

          <div class="overtime-owner-body">
            <pre v-if="ownerOfferText" class="overtime-offer-text">{{ ownerOfferText }}</pre>
            <p v-if="ownerQuantityLine" class="overtime-quantity">{{ ownerQuantityLine }}</p>
            <div class="overtime-countdown" aria-live="polite">{{ ownerCountdownLabel }}</div>
            <p v-if="ownerError" class="overtime-error" role="alert">{{ ownerError }}</p>
            <div class="overtime-owner-actions">
              <button
                type="button"
                class="overtime-btn overtime-btn--reject"
                :disabled="loading"
                @click="reject"
              >
                {{ M36_OWNER_REJECT }}
              </button>
              <button
                type="button"
                class="overtime-btn overtime-btn--approve"
                :disabled="loading"
                @click="approve"
              >
                {{ M36_OWNER_APPROVE }}
              </button>
            </div>
          </div>
        </div>
      </div>
    </transition>

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
              <span
                v-else
                class="overtime-requester-countdown"
              >
                {{ requesterCountdownLabel || M21_REQUESTER_QUEUED }}
              </span>
            </p>
            <button
              type="button"
              class="overtime-btn overtime-btn--cancel"
              :disabled="loading"
              @click="cancel"
            >
              {{ M12_CANCEL_BUTTON }}
            </button>
          </template>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.overtime-owner-overlay {
  position: fixed;
  inset: 0;
  z-index: 9997;
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 0;
  background: rgba(0, 0, 0, 0.4);
}

.overtime-owner-card {
  width: 100%;
  max-width: 28rem;
  overflow: hidden;
  border-radius: 1rem 1rem 0 0;
  background: var(--ds-bg-card, #ffffff);
  box-shadow: none;
  padding-bottom: env(safe-area-inset-bottom, 0px);
}

.overtime-owner-header {
  padding: 1.25rem;
  text-align: center;
  background: var(--ds-primary-50, #fffbeb);
}

.overtime-owner-icon {
  width: 3.5rem;
  height: 3.5rem;
  margin: 0 auto 0.75rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 9999px;
  background: var(--ds-primary-100, #fef3c7);
  color: var(--ds-primary-700, #b45309);
}

.overtime-owner-header h3 {
  margin: 0;
  color: var(--ds-text-primary, #111827);
  font-size: 1.05rem;
  font-weight: 700;
}

.overtime-owner-body {
  padding: 1.25rem;
  display: grid;
  gap: 0.85rem;
}

.overtime-offer-text {
  margin: 0;
  padding: 0.85rem;
  border-radius: 0.75rem;
  background: #f8fafc;
  color: #334155;
  font-size: 0.85rem;
  line-height: 1.6;
  white-space: pre-wrap;
  text-align: right;
  font-family: inherit;
}

.overtime-quantity {
  margin: 0;
  text-align: center;
  font-size: 0.9rem;
  color: var(--ds-primary-700, #b45309);
  font-weight: 600;
}

.overtime-countdown {
  text-align: center;
  font-family: ui-monospace, monospace;
  font-size: 0.95rem;
  color: #64748b;
}

.overtime-error {
  margin: 0;
  text-align: center;
  font-size: 0.8rem;
  color: #b91c1c;
}

.overtime-owner-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}

.overtime-btn {
  padding: 0.75rem 0.5rem;
  border-radius: 0.75rem;
  font-size: 0.875rem;
  font-weight: 700;
  transition: opacity 0.15s ease;
}

.overtime-btn:disabled {
  opacity: 0.5;
}

.overtime-btn--approve {
  background: var(--ds-primary-500, #f59e0b);
  color: var(--ds-on-primary, #111827);
}

.overtime-btn--reject {
  border: 1px solid #fecaca;
  color: #dc2626;
  background: #fff;
}

.overtime-btn--cancel {
  border: 1px solid #cbd5e1;
  background: #fff;
  color: #334155;
  padding: 0.55rem 0.85rem;
}

.overtime-requester-banner {
  position: fixed;
  left: 0;
  right: 0;
  bottom: calc(4.5rem + env(safe-area-inset-bottom, 0px));
  z-index: 9996;
  display: flex;
  justify-content: center;
  padding: 0 0.75rem;
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
  border-radius: 0.9rem;
  background: #0f172a;
  color: #f8fafc;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.28);
}

.overtime-requester-card p {
  margin: 0;
  font-size: 0.85rem;
  line-height: 1.5;
}

.overtime-requester-countdown {
  display: inline-block;
  margin-inline-start: 0.4rem;
  font-family: ui-monospace, monospace;
  color: #99f6e4;
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
  .fade-leave-active,
  .overtime-btn {
    transition: none;
  }
}
</style>
