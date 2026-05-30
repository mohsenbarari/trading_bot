<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { markMessengerPerformance } from '../../utils/messengerRefactor'

const props = defineProps<{
  apiBaseUrl: string
  jwtToken: string | null
  currentUserId: number
  currentUserRole?: string | null
  currentUserIsAccountant?: boolean
  currentUserIsCustomer?: boolean
  targetUserId?: number
  targetUserName?: string
}>()

defineEmits<{
  (e: 'navigate', view: string, payload?: any): void
  (e: 'back'): void
}>()

const activeTargetLabel = computed(() => {
  if (!props.targetUserId) {
    return 'لیست گفتگوها'
  }

  return props.targetUserName || `گفتگو ${props.targetUserId}`
})

onMounted(() => {
  markMessengerPerformance('refactor-shell-mounted')
})
</script>

<template>
  <section class="messenger-refactor-shell" data-testid="messenger-refactor-shell" dir="rtl">
    <div class="shell-panel">
      <span class="shell-kicker">Messenger Refactor</span>
      <h1>مسیر امن بازسازی پیامرسان فعال است</h1>
      <p>
        این پوسته فقط برای مرحله اول است و مسیر فعلی پیامرسان را جایگزین نکرده است.
        با خاموش کردن feature flag، نسخه legacy فوراً برمی‌گردد.
      </p>
      <dl class="shell-facts">
        <div>
          <dt>کاربر</dt>
          <dd>{{ currentUserId }}</dd>
        </div>
        <div>
          <dt>نقش</dt>
          <dd>{{ currentUserRole || 'standard' }}</dd>
        </div>
        <div>
          <dt>مسیر فعلی</dt>
          <dd>{{ activeTargetLabel }}</dd>
        </div>
      </dl>
      <button type="button" class="shell-back" @click="$emit('back')">
        بازگشت
      </button>
    </div>
  </section>
</template>

<style scoped>
.messenger-refactor-shell {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(circle at top right, rgba(245, 158, 11, 0.24), transparent 34%),
    linear-gradient(135deg, #fff7ed 0%, #f8fafc 50%, #eef2ff 100%);
  color: #1f2937;
}

.shell-panel {
  width: min(100%, 520px);
  padding: 24px;
  border: 1px solid rgba(148, 163, 184, 0.32);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.12);
}

.shell-kicker {
  display: inline-flex;
  margin-bottom: 12px;
  color: #b45309;
  font-size: 0.78rem;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1 {
  margin: 0 0 10px;
  font-size: 1.35rem;
  line-height: 1.6;
  font-weight: 900;
}

p {
  margin: 0;
  color: #475569;
  line-height: 1.9;
}

.shell-facts {
  display: grid;
  gap: 10px;
  margin: 20px 0;
}

.shell-facts div {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #f8fafc;
}

dt {
  color: #64748b;
  font-size: 0.84rem;
}

dd {
  margin: 0;
  font-weight: 800;
}

.shell-back {
  width: 100%;
  min-height: 44px;
  border: 0;
  border-radius: 8px;
  background: #f59e0b;
  color: #111827;
  font-weight: 900;
  cursor: pointer;
}
</style>