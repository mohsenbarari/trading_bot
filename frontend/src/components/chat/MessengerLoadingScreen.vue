<script setup lang="ts">
const props = withDefaults(defineProps<{
  mode?: 'list' | 'chat'
  title?: string
  subtitle?: string
}>(), {
  mode: 'list',
  title: undefined,
  subtitle: undefined,
})

const listRows = ['78%', '56%', '70%', '48%']
const chatRows = [
  { side: 'received', width: '68%' },
  { side: 'sent', width: '48%' },
  { side: 'received', width: '60%' },
  { side: 'sent', width: '40%' },
]
</script>

<template>
  <div class="messenger-loader" :class="`mode-${props.mode}`">
    <div class="loader-ambient ambient-sand"></div>
    <div class="loader-ambient ambient-blue"></div>

    <section class="loader-shell">
      <div class="loader-core" aria-hidden="true">
        <span class="core-ring core-ring-outer"></span>
        <span class="core-ring core-ring-inner"></span>
        <span class="core-dot"></span>
      </div>

      <div v-if="props.mode === 'list'" class="preview-panel preview-list">
        <div v-for="(row, index) in listRows" :key="row" class="list-item-ghost">
          <div class="avatar-ghost skeleton-wave"></div>
          <div class="list-copy-ghost">
            <div class="line-ghost skeleton-wave" :style="{ width: row }"></div>
            <div class="line-ghost small skeleton-wave" :style="{ width: listRows[(index + 1) % listRows.length] }"></div>
          </div>
        </div>
      </div>

      <div v-else class="preview-panel preview-chat">
        <div class="chat-bubbles-ghost">
          <div
            v-for="bubble in chatRows"
            :key="`${bubble.side}-${bubble.width}`"
            class="bubble-row-ghost"
            :class="bubble.side"
          >
            <div class="bubble-ghost skeleton-wave" :style="{ width: bubble.width }"></div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.messenger-loader {
  position: relative;
  min-height: 100%;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background:
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.78), transparent 34%),
    linear-gradient(180deg, #edf3f5 0%, #e4ecef 100%);
}

.loader-ambient {
  position: absolute;
  border-radius: 999px;
  filter: blur(24px);
  opacity: 0.62;
  animation: drift 9s ease-in-out infinite;
}

.ambient-sand {
  width: 240px;
  height: 240px;
  top: -84px;
  right: -52px;
  background: radial-gradient(circle, rgba(212, 167, 98, 0.28), rgba(212, 167, 98, 0));
}

.ambient-blue {
  width: 260px;
  height: 260px;
  bottom: -110px;
  left: -78px;
  background: radial-gradient(circle, rgba(51, 144, 236, 0.18), rgba(51, 144, 236, 0));
  animation-delay: -4s;
}

.loader-shell {
  position: relative;
  z-index: 1;
  width: min(440px, calc(100% - 28px));
  padding: 26px 22px 20px;
  border-radius: 30px;
  background: rgba(255, 255, 255, 0.56);
  border: 1px solid rgba(255, 255, 255, 0.7);
  box-shadow: 0 24px 48px rgba(52, 70, 82, 0.11);
  backdrop-filter: blur(14px);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 22px;
}

.loader-core {
  position: relative;
  width: 78px;
  height: 78px;
  display: grid;
  place-items: center;
}

.core-ring {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  border: 1px solid rgba(22, 32, 42, 0.08);
}

.core-ring-outer {
  background: radial-gradient(circle at center, rgba(255, 255, 255, 0.62), rgba(255, 255, 255, 0.08));
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.44);
  animation: slow-spin 8s linear infinite;
}

.core-ring-outer::after {
  content: '';
  position: absolute;
  top: -3px;
  left: 50%;
  width: 10px;
  height: 10px;
  margin-left: -5px;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(28, 118, 204, 0.95), rgba(97, 178, 252, 0.85));
  box-shadow: 0 0 14px rgba(51, 144, 236, 0.26);
}

.core-ring-inner {
  inset: 12px;
  border-color: rgba(18, 84, 145, 0.08);
  background: rgba(255, 255, 255, 0.42);
  animation: reverse-spin 6s linear infinite;
}

.core-dot {
  position: relative;
  z-index: 1;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: linear-gradient(135deg, #c8913b, #3390ec);
  box-shadow: 0 0 22px rgba(51, 144, 236, 0.2);
  animation: breathe 1.8s ease-in-out infinite;
}

.preview-panel {
  width: 100%;
  padding: 16px;
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.46);
  border: 1px solid rgba(255, 255, 255, 0.62);
}

.preview-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.preview-chat {
  padding-block: 18px;
}

.list-item-ghost {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
}

.avatar-ghost {
  width: 42px;
  height: 42px;
  min-width: 42px;
  border-radius: 50%;
}

.list-copy-ghost {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.line-ghost {
  height: 10px;
  border-radius: 999px;
}

.line-ghost.small {
  height: 8px;
  opacity: 0.82;
}

.chat-bubbles-ghost {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.bubble-row-ghost {
  display: flex;
}

.bubble-row-ghost.sent {
  justify-content: flex-end;
}

.bubble-row-ghost.received {
  justify-content: flex-start;
}

.bubble-ghost {
  height: 42px;
  border-radius: 18px;
}

.bubble-row-ghost.sent .bubble-ghost {
  border-bottom-right-radius: 8px;
}

.bubble-row-ghost.received .bubble-ghost {
  border-bottom-left-radius: 8px;
}

.skeleton-wave {
  background-color: rgba(202, 214, 221, 0.82);
  background-image: linear-gradient(110deg, rgba(210, 220, 226, 0.86) 8%, rgba(255, 255, 255, 0.98) 18%, rgba(210, 220, 226, 0.86) 33%);
  background-size: 200% 100%;
  animation: shimmer 1.6s linear infinite;
}

@keyframes shimmer {
  to {
    background-position-x: -200%;
  }
}

@keyframes drift {
  0%,
  100% {
    transform: translate3d(0, 0, 0) scale(1);
  }
  50% {
    transform: translate3d(0, 14px, 0) scale(1.06);
  }
}

@keyframes slow-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes reverse-spin {
  to {
    transform: rotate(-360deg);
  }
}

@keyframes breathe {
  0%,
  100% {
    transform: scale(0.86);
    opacity: 0.72;
  }
  50% {
    transform: scale(1);
    opacity: 1;
  }
}

@media (max-width: 640px) {
  .loader-shell {
    width: calc(100% - 20px);
    padding: 22px 16px 16px;
    border-radius: 24px;
    gap: 18px;
  }

  .preview-panel {
    padding: 14px;
  }

  .bubble-ghost {
    height: 38px;
  }
}
</style>
