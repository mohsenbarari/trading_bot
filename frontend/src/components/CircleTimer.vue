<template>
  <div class="timer-container" :title="timeText">
    <svg class="timer-svg" :width="size" :height="size" viewBox="0 0 36 36">
      <!-- Background Circle -->
      <path
        class="circle-bg"
        d="M18 2.0845
           a 15.9155 15.9155 0 0 1 0 31.831
           a 15.9155 15.9155 0 0 1 0 -31.831"
      />
      <!-- Progress Circle -->
      <path
        class="circle"
        :stroke-dasharray="dashArray"
        d="M18 2.0845
           a 15.9155 15.9155 0 0 1 0 31.831
           a 15.9155 15.9155 0 0 1 0 -31.831"
        :style="{ stroke: color }"
      />
    </svg>
    <div class="timer-text" v-if="showText">{{ timeText }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';

const props = defineProps({
  expiresAt: {
    type: Number,
    required: true
  },
  totalDuration: {
    type: Number,
    default: 120 // Default 2 minutes if not provided (though backend handles this)
  },
  size: {
    type: Number,
    default: 24
  },
  showText: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['expired']);

const now = ref(Math.floor(Date.now() / 1000));
const intervalId = ref(null);

const remaining = computed(() => {
  const r = props.expiresAt - now.value;
  return r > 0 ? r : 0;
});

const percentage = computed(() => {
  if (remaining.value <= 0) return 0;
  // Calculate relative to the *original* start time ideally, but simpler:
  // if we don't have start time, we assume max duration.
  // Ideally, we should pass createdAt too, but let's approximate or just use remaining/total.
  // Wait, correct logic: percentage = (remaining / totalDuration) * 100
  let p = (remaining.value / props.totalDuration) * 100;
  return Math.min(Math.max(p, 0), 100);
});

const dashArray = computed(() => {
  return `${percentage.value}, 100`;
});

const color = computed(() => {
  // HSL interpolation: 120 (green) -> 60 (yellow) -> 0 (red)
  // percentage 100% = hue 120 (green)
  // percentage 0% = hue 0 (red)
  const hue = Math.round((percentage.value / 100) * 120);
  return `hsl(${hue}, 80%, 45%)`;
});

const timeText = computed(() => {
  const m = Math.floor(remaining.value / 60);
  const s = remaining.value % 60;
  return `${m}:${s.toString().padStart(2, '0')}`;
});

onMounted(() => {
  intervalId.value = setInterval(() => {
    now.value = Math.floor(Date.now() / 1000);
    if (remaining.value <= 0) {
      emit('expired');
      clearInterval(intervalId.value);
    }
  }, 1000);
});

onUnmounted(() => {
  if (intervalId.value) clearInterval(intervalId.value);
});
</script>

<style scoped>
.timer-container {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.timer-svg {
  transform: rotate(-90deg); /* Start from top */
}

.circle-bg {
  fill: none;
  stroke: #e2e8f0;
  stroke-width: 3.8;
}

.circle {
  fill: none;
  stroke-width: 3.8;
  stroke-linecap: round;
  transition: stroke-dasharray 1s linear, stroke 0.3s ease;
}

.timer-text {
  position: absolute;
  font-size: 10px;
  font-weight: bold;
  color: #64748b;
}
</style>
