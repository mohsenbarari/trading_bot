<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  mode?: 'list' | 'chat'
  title?: string
  subtitle?: string
}>(), {
  mode: 'list',
  title: 'در حال بارگذاری پیام‌رسان',
  subtitle: 'چند لحظه صبر کنید...'
})

const listRows = ['84%', '62%', '76%', '58%']
const chatRows = [
  { side: 'received', width: '72%' },
  { side: 'sent', width: '54%' },
  { side: 'received', width: '66%' },
  { side: 'sent', width: '46%' },
  { side: 'received', width: '61%' },
]

const badgeLabel = computed(() => {
  return props.mode === 'chat' ? 'Loading Chat' : 'Loading Messenger'
})
</script>

<template>
  <div class="messenger-loader" :class="`mode-${props.mode}`">
    <div class="loader-ambient ambient-gold"></div>
    <div class="loader-ambient ambient-sky"></div>

    <section class="loader-card">
      <div class="loader-badge">
        <span class="badge-ping"></span>
        <span class="badge-text">{{ badgeLabel }}</span>
      </div>

      <div class="loader-copy">
        <h2>{{ props.title }}</h2>
        <p>{{ props.subtitle }}</p>
      </div>

      <div v-if="props.mode === 'list'" class="loader-list-preview">
        <div v-for="(row, index) in listRows" :key="row" class="list-item-ghost">
          <div class="avatar-ghost shimmer"></div>
          <div class="list-copy-ghost">
            <div class="line-ghost shimmer" :style="{ width: row }"></div>
            <div class="line-ghost small shimmer" :style="{ width: listRows[(index + 1) % listRows.length] }"></div>
          </div>
        </div>
      </div>

      <div v-else class="loader-chat-preview">
        <div class="chat-header-ghost shimmer"></div>
        <div class="chat-bubbles-ghost">
          <div
            v-for="bubble in chatRows"
            :key="`${bubble.side}-${bubble.width}`"
            class="bubble-row-ghost"
            :class="bubble.side"
          >
            <div class="bubble-ghost shimmer" :style="{ width: bubble.width }"></div>
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
    radial-gradient(circle at top left, rgba(255, 255, 255, 0.72), transparent 34%),
    linear-gradient(180deg, #eef4f7 0%, #e1eaef 100%);
}

.loader-ambient {
  position: absolute;
  border-radius: 999px;
  filter: blur(18px);
  opacity: 0.7;
  animation: drift 8s ease-in-out infinite;
}

.ambient-gold {
  width: 220px;
  height: 220px;
  top: -72px;
  right: -44px;
  background: radial-gradient(circle, rgba(245, 158, 11, 0.34), rgba(245, 158, 11, 0));
}

.ambient-sky {
  width: 240px;
  height: 240px;
  bottom: -96px;
  left: -72px;
  background: radial-gradient(circle, rgba(51, 144, 236, 0.22), rgba(51, 144, 236, 0));
  animation-delay: -3s;
}

.loader-card {
  position: relative;
  z-index: 1;
  width: min(520px, calc(100% - 28px));
  padding: 24px;
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.76);
  border: 1px solid rgba(255, 255, 255, 0.78);
  box-shadow: 0 22px 46px rgba(61, 78, 92, 0.14);
  backdrop-filter: blur(12px);
}

.loader-badge {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(17, 24, 39, 0.05);
  color: #5b6470;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.badge-ping {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f59e0b, #3390ec);
  box-shadow: 0 0 0 0 rgba(51, 144, 236, 0.28);
  animation: ping 1.8s infinite;
}

.loader-copy {
  margin-top: 18px;
}

.loader-copy h2 {
  margin: 0;
  color: #16202a;
  font-size: clamp(20px, 3.8vw, 28px);
  font-weight: 800;
}

.loader-copy p {
  margin: 10px 0 0;
  color: #61707f;
  font-size: 14px;
  line-height: 1.8;
}

.loader-list-preview,
.loader-chat-preview {
  margin-top: 22px;
}

.loader-list-preview {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.list-item-ghost {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.62);
}

.avatar-ghost {
  width: 52px;
  height: 52px;
  min-width: 52px;
  border-radius: 50%;
  background-color: #e3e9ed;
}

.list-copy-ghost {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.line-ghost {
  height: 12px;
  border-radius: 999px;
  background-color: #e3e9ed;
}

.line-ghost.small {
  height: 10px;
  opacity: 0.86;
}

.chat-header-ghost {
  width: 42%;
  height: 16px;
  border-radius: 999px;
  background-color: #e3e9ed;
  margin-bottom: 18px;
}

.chat-bubbles-ghost {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.bubble-row-ghost {
  display: flex;
}

.bubble-row-ghost.sent {
  justify-content: flex-start;
}

.bubble-row-ghost.received {
  justify-content: flex-end;
}

.bubble-ghost {
  height: 52px;
  border-radius: 18px;
  background-color: #e3e9ed;
}

.bubble-row-ghost.sent .bubble-ghost {
  border-bottom-left-radius: 8px;
}

.bubble-row-ghost.received .bubble-ghost {
  border-bottom-right-radius: 8px;
}

.shimmer {
  background-image: linear-gradient(110deg, rgba(224, 231, 235, 0.92) 8%, rgba(255, 255, 255, 0.98) 18%, rgba(224, 231, 235, 0.92) 33%);
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
}

@keyframes shimmer {
  to {
    background-position-x: -200%;
  }
}

@keyframes ping {
  0% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.28);
  }
  70% {
    transform: scale(1);
    box-shadow: 0 0 0 12px rgba(245, 158, 11, 0);
  }
  100% {
    transform: scale(0.95);
    box-shadow: 0 0 0 0 rgba(245, 158, 11, 0);
  }
}

@keyframes drift {
  0%,
  100% {
    transform: translate3d(0, 0, 0) scale(1);
  }
  50% {
    transform: translate3d(0, 12px, 0) scale(1.05);
  }
}

@media (max-width: 640px) {
  .loader-card {
    width: calc(100% - 20px);
    padding: 18px;
    border-radius: 24px;
  }

  .list-item-ghost {
    padding: 12px;
  }

  .bubble-ghost {
    height: 46px;
  }
}
</style>
