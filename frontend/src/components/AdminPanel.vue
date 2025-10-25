<script setup lang="ts">
// این کامپوننت دکمه ساخت دعوت را دیگر نمایش نمی‌دهد، بلکه یک صفحه جدید است
// ما باید کامپوننت ساخت دعوت را در اینجا import کنیم
import CreateInvitationView from './CreateInvitationView.vue';

defineProps<{
  apiBaseUrl: string;
  jwtToken: string | null;
}>();
        
const emit = defineEmits(['invite-created', 'navigate']);
</script>

<template>
  <div class="admin-panel-container">
    
    <CreateInvitationView 
      :api-base-url="apiBaseUrl"
      :jwt-token="jwtToken"
      @invite-created="(msg) => emit('invite-created', msg)"
    />

    <div class="card settings-card">
      <button class="settings-button" @click="emit('navigate', 'settings')">
        ⚙️ تنظیمات مدیریت
      </button>
    </div>

  </div>
</template>

<style scoped>
.admin-panel-container {
  display: flex;
  flex-direction: column;
  gap: 16px; /* فاصله بین کارت‌ها */
}
.card.settings-card {
  background-color: var(--card-bg);
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
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
