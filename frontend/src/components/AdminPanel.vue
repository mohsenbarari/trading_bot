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
      <div class="version-tag">UI v1.4</div>
    </div>

  </div>
</template>

<style scoped>
.admin-panel-container {
  display: flex;
  flex-direction: column;
  gap: var(--ds-page-padding); 
}
.card.management-card {
  background: rgba(255, 255, 255, 0.7);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-xl);
  padding: 1.5rem;
  box-shadow: var(--ds-shadow-md);
}
h2 {
  margin-top: 0;
  margin-bottom: 0.5rem;
  font-weight: 800;
  font-size: var(--ds-font-xl);
  color: var(--ds-text-primary);
}
p {
  font-size: 0.8rem;
  color: var(--ds-text-placeholder);
  margin-bottom: 1.5rem;
}
.button-group {
    display: grid;
    grid-template-columns: 1fr; 
    gap: var(--ds-section-gap);
}
.admin-action-btn {
  width: 100%;
  padding: 1rem 1.25rem;
  font-size: var(--ds-font-md);
  font-weight: 600;
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  border: 1px solid var(--ds-border-accent);
  border-radius: var(--ds-radius-lg);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  direction: rtl; 
  gap: 0.75rem;
  transition: all 0.2s ease-in-out;
  box-shadow: var(--ds-shadow-xs);
  font-family: inherit;
}
.admin-action-btn:hover {
  border-color: var(--ds-primary-500);
  color: var(--ds-primary-700);
  background: var(--ds-primary-50);
}
.admin-action-btn:active {
  background: var(--ds-primary-100);
  transform: scale(0.98);
}
.admin-action-btn.primary {
  background: var(--ds-gradient-primary);
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(245, 158, 11, 0.3);
}
.admin-action-btn.primary:hover {
  background: linear-gradient(135deg, var(--ds-primary-600), var(--ds-primary-700));
  color: white;
}
.admin-action-btn.primary-alt {
  background: linear-gradient(135deg, #0f766e, var(--ds-success-500));
  color: white;
  border-color: transparent;
  box-shadow: 0 4px 12px rgba(16, 185, 129, 0.24);
}
.admin-action-btn.primary-alt:hover {
  background: linear-gradient(135deg, #0b5f59, var(--ds-success-600));
  color: white;
}
.admin-action-btn.secondary {
  background: var(--ds-bg-card);
  color: var(--ds-text-primary);
  border: 1px solid rgba(245, 158, 11, 0.2);
  box-shadow: var(--ds-shadow-sm);
}
.admin-action-btn.secondary:hover {
  background: var(--ds-primary-50);
  border-color: var(--ds-primary-500);
  color: var(--ds-primary-700);
}
.version-tag {
    text-align: center;
    font-size: var(--ds-font-xs);
    color: var(--ds-text-placeholder);
    margin-top: 1rem;
    opacity: 0.5;
}
</style>
