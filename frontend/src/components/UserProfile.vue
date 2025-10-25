<script setup lang="ts">
defineProps<{
  user: any;
}>();
// --- امیت را برای دکمه جدید اضافه کنید ---
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

    <button class="settings-button" @click="emit('navigate', 'settings')">
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
.settings-button {
  width: 100%;
  padding: 12px;
  font-size: 15px;
  font-weight: 600;
  background-color: var(--card-bg);
  color: var(--text-color);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease-in-out;
  margin-top: 20px; /* فاصله از بالا */
}
.settings-button:hover {
  border-color: var(--primary-color);
  color: var(--primary-color);
}
.settings-button:active {
  background-color: #f0f0f0;
  transform: translateY(1px);
}
</style>

