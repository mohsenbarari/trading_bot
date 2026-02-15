<script setup lang="ts">
defineEmits(['navigate']);
</script>

<template>
  <div class="admin-panel-container">
    
    <div class="card management-card">
      <h2>پنل مدیریت</h2>
      <p>لطفاً بخش مورد نظر خود را انتخاب کنید:</p>
      
      <div class="button-group">
        <button class="admin-action-btn primary" @click="$emit('navigate', 'create_invitation')">
          ➕ ارسال لینک دعوت
        </button>
        
        <button class="admin-action-btn secondary" @click="$emit('navigate', 'manage_commodities')">
          📦 مدیریت کالاها
        </button>

        <button class="admin-action-btn secondary" @click="$emit('navigate', 'manage_users')">
          👥 مدیریت کاربران
        </button>
        
        <button class="admin-action-btn secondary" @click="$emit('navigate', 'settings')">
          ⚙️ تنظیمات سیستم
        </button>
      </div>
      <div class="version-tag">UI v1.3</div>
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
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(245, 158, 11, 0.1);
  border-radius: 1.25rem;
  padding: 1.5rem;
  box-shadow: 0 4px 16px rgba(0,0,0,0.04);
}
h2 {
  margin-top: 0;
  margin-bottom: 0.5rem;
  font-weight: 800;
  font-size: 1.1rem;
  color: #1f2937;
}
p {
  font-size: 0.8rem;
  color: #9ca3af;
  margin-bottom: 1.5rem;
}
.button-group {
    display: grid;
    grid-template-columns: 1fr; 
    gap: 0.75rem;
}
.admin-action-btn {
  width: 100%;
  padding: 1rem 1.25rem;
  font-size: 0.9rem; /* Standardized size */
  font-weight: 600; /* Standardized weight */
  background: white;
  color: #1f2937;
  border: 1px solid rgba(245, 158, 11, 0.15);
  border-radius: 1rem; 
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  direction: rtl; 
  gap: 0.75rem;
  transition: all 0.2s ease-in-out;
  box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}
.admin-action-btn:hover {
  border-color: #f59e0b;
  color: #b45309;
  background: #fffbeb;
}
.admin-action-btn:active {
  background: #fef3c7;
  transform: scale(0.98);
}
.admin-action-btn.primary {
  background: linear-gradient(135deg, #f59e0b, #d97706) !important;
  color: white !important;
  border-color: transparent !important;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
}
.admin-action-btn.primary:hover {
  background: linear-gradient(135deg, #d97706, #b45309) !important;
  color: white !important;
}
.admin-action-btn.secondary {
  background: linear-gradient(135deg, #ffffff, #f9fafb) !important;
  color: #1f2937 !important; /* Dark Gray */
  border: 1px solid rgba(245, 158, 11, 0.3) !important;
  box-shadow: 0 2px 4px rgba(0,0,0,0.05);
}
.admin-action-btn.secondary:hover {
  background: #fffbeb !important; /* Very light amber */
  border-color: #f59e0b !important;
  color: #b45309 !important;
}
.version-tag {
    text-align: center;
    font-size: 0.7rem;
    color: #9ca3af;
    margin-top: 1rem;
    opacity: 0.5;
}
</style>