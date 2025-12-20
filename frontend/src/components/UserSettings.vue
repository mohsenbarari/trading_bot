<script setup lang="ts">
defineEmits(['navigate'])
</script>

<template>
  <div class="user-settings">
    <div class="settings-content">
      <div class="coming-soon">
        <span class="icon">🚧</span>
        <h2>در حال ساخت</h2>
        <p>تنظیمات پنل کاربری به زودی اضافه خواهد شد.</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.user-settings {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 50vh;
  padding: 20px;
}

.settings-content {
  width: 100%;
  max-width: 400px;
}

.coming-soon {
  text-align: center;
  padding: 40px 20px;
  background: linear-gradient(135deg, #f5f5f5, #e8e8e8);
  border-radius: 16px;
  box-shadow: 0 4px 15px rgba(0, 0, 0, 0.1);
}

.coming-soon .icon {
  font-size: 48px;
  display: block;
  margin-bottom: 16px;
}

.coming-soon h2 {
  color: #333;
  margin: 0 0 12px 0;
  font-size: 20px;
}

.coming-soon p {
  color: #666;
  margin: 0;
  font-size: 14px;
}
</style>
