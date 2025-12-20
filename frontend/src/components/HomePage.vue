<script setup lang="ts">
const props = defineProps<{
  userRole: string;
}>()
const emit = defineEmits(['navigate'])
</script>

<template>
  <div class="home-page">
    <!-- صفحه خالی - دکمه‌ها در منوی پایین هستند -->
  </div>
</template>

<style scoped>
.home-page {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  padding: 20px;
}

.home-content {
  width: 100%;
  max-width: 400px;
}

.main-buttons {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.main-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 20px 24px;
  border: none;
  border-radius: 16px;
  cursor: pointer;
  font-size: 18px;
  font-weight: 700;
  transition: all 0.2s ease;
  box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
}

.main-btn:hover {
  transform: translateY(-3px);
  box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15);
}

.main-btn:active {
  transform: translateY(1px);
}

.btn-icon {
  font-size: 24px;
}

.btn-text {
  flex: 1;
  text-align: center;
}

/* دکمه معامله */
.trade-btn {
  background: linear-gradient(135deg, #007aff, #0056b3);
  color: white;
  box-shadow: 0 4px 15px rgba(0, 122, 255, 0.3);
}

.trade-btn:hover {
  box-shadow: 0 6px 20px rgba(0, 122, 255, 0.4);
}

/* دکمه پنل کاربری */
.profile-btn {
  background: linear-gradient(135deg, #34c759, #28a745);
  color: white;
  box-shadow: 0 4px 15px rgba(52, 199, 89, 0.3);
}

.profile-btn:hover {
  box-shadow: 0 6px 20px rgba(52, 199, 89, 0.4);
}

/* دکمه پنل مدیریت */
.admin-btn {
  background: linear-gradient(135deg, #ff9500, #e67e00);
  color: white;
  box-shadow: 0 4px 15px rgba(255, 149, 0, 0.3);
}

.admin-btn:hover {
  box-shadow: 0 6px 20px rgba(255, 149, 0, 0.4);
}
</style>
