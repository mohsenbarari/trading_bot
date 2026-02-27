<script setup lang="ts">
// Simple stateless component for the empty selection view
</script>

<template>
  <div class="no-selection-placeholder">
    <p>لطفاً یک گفتگو را انتخاب کنید</p>
  </div>
</template>

<style scoped>
.no-selection-placeholder {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #f5f5f5; /* Telegram empty grey */
  background-image: url('data:image/svg+xml,%3Csvg width="60" height="60" viewBox="0 0 60 60" xmlns="http://www.w3.org/2000/svg"%3E%3Cg fill="none" fill-rule="evenodd"%3E%3Cg fill="%23e0e0e0" fill-opacity="0.4"%3E%3Cpath d="M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z"/%3E%3C/g%3E%3C/g%3E%3C/svg%3E');
}

.no-selection-placeholder p {
  background: rgba(255, 255, 255, 0.8);
  padding: 8px 16px;
  border-radius: 16px;
  color: #79828b;
  font-size: 14px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}
</style>
