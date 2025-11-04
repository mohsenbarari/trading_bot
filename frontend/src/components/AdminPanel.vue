<script setup lang="ts">
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

    <div class="card management-card">
      <div class="button-group">
        <button class="management-button" @click="emit('navigate', 'manage_commodities')">
          📦 مدیریت کالاها
        </button>
        <button class="management-button" @click="emit('navigate', 'settings')">
          ⚙️ تنظیمات مدیریت
        </button>
      </div>
    </div>

  </div>
</template>

<style scoped>
.admin-panel-container {
  display: flex;
  flex-direction: column;
  gap: 16px; 
}
.card.management-card {
  background-color: var(--card-bg);
  border-radius: 12px;
  padding: 15px; /* کمی پدینگ کمتر */
  box-shadow: 0 4px 12px rgba(0,0,0,0.06); /* سایه کمتر */
}
.button-group {
    display: grid;
    /* دو ستون مساوی */
    grid-template-columns: repeat(2, 1fr); 
    gap: 12px; /* فاصله بین دکمه‌ها */
}
.management-button { /* تغییر نام از settings-button */
  width: 100%;
  padding: 12px 10px; /* کمی پدینگ افقی کمتر */
  font-size: 14px; /* کمی فونت کوچکتر */
  font-weight: 500; /* وزن معمولی‌تر */
  background-color: #f9fafb; /* پس‌زمینه کمی متفاوت */
  color: var(--text-color);
  border: 1px solid var(--border-color);
  border-radius: 10px; 
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: all 0.2s ease-in-out;
}
.management-button:hover {
  border-color: var(--primary-color);
  color: var(--primary-color);
  background-color: #f0f9ff; /* هاور با رنگ آبی کم‌رنگ */
}
.management-button:active {
  background-color: #e0f2fe; /* فعال شدن با رنگ آبی پررنگ‌تر */
  transform: translateY(1px);
}
</style>