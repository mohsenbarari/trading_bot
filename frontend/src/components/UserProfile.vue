<script setup lang="ts">
defineProps<{
  user: any;
}>();
</script>

<template>
  <div class="card">
    <h2>پروفایل کاربری</h2>
    <div class="profile-details">
      <div class="detail-item">
        <span class="label">نام کامل</span>
        <span class="value">{{ user.full_name }}</span>
      </div>
      <div class="detail-item">
        <span class="label">نام کاربری حساب</span>
        <span class="value">{{ user.account_name }}</span>
      </div>
      <div class="detail-item">
        <span class="label">شماره موبایل</span>
        <span class="value">{{ user.mobile_number }}</span>
      </div>
      <div class="detail-item">
        <span class="label">نقش</span>
        <span class="value">{{ user.role }}</span>
      </div>
      <div class="detail-item">
        <span class="label">آیدی تلگرام</span>
        <span class="value code">{{ user.telegram_id }}</span>
      </div>
    </div>
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
</style>

