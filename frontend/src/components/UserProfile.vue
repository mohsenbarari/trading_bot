<script setup lang="ts">
defineProps<{
  user: any;
}>();
const emit = defineEmits(['navigate'])
</script>

<template>
  <div class="card">
    <h2>پروفایل کاربری</h2>
    <div class="profile-details">
      <div class="detail-item">
        <span class="label">آیدی تلگرام</span>
        <span class="value code">{{ user.telegram_id }}</span>
      </div>
    </div>

    <button class="menu-button notification-btn" @click="emit('navigate', 'notifications')">
      🔔 صندوق پیام‌ها
    </button>

    <button class="menu-button settings-btn" @click="emit('navigate', 'settings')">
      ⚙️ تنظیمات
    </button>
  </div>
</template>

<style scoped>
.card {
  background-color: var(--card-bg);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}
h2 {
  margin-top: 0;
  margin-bottom: 24px;
  font-size: 20px;
  color: var(--text-color);
}
.profile-details {
  display: grid;
  gap: 16px;
  margin-bottom: 24px;
}
.detail-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  background-color: #f7f7f7;
  border-radius: 8px;
}
.label {
  font-weight: 500;
  color: var(--text-secondary);
  font-size: 14px;
}
.value {
  font-weight: 500;
  color: var(--text-color);
  font-size: 15px;
}
.value.code {
  direction: ltr;
  font-family: monospace;
}

.menu-button {
  width: 100%;
  padding: 14px;
  font-size: 15px;
  font-weight: 600;
  background-color: var(--card-bg);
  color: var(--text-color);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease-in-out;
  margin-bottom: 12px;
}
.menu-button:hover {
  border-color: var(--primary-color);
  color: var(--primary-color);
  background-color: #f0f9ff;
}
.menu-button:active {
  background-color: #e0f2fe;
  transform: translateY(1px);
}
/* حذف فاصله از آخرین دکمه */
.menu-button:last-child {
  margin-bottom: 0;
}
</style>