<script setup lang="ts">
defineProps<{
  title: string;
}>();
</script>

<template>
  <div class="placeholder-container">
    <div class="icon">🚧</div>
    <h2>{{ title }}</h2>
    <p>این بخش در حال توسعه است و به زودی در دسترس خواهد بود.</p>
  </div>
</template>

<style scoped>
.placeholder-container {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  height: 100%;
  color: var(--text-secondary);
}
.icon {
  font-size: 48px;
  margin-bottom: 16px;
}
h2 {
  font-size: 22px;
  color: var(--text-color);
  margin: 0 0 8px 0;
}
p {
  margin: 0;
  max-width: 250px;
}
</style>

