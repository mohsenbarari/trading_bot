<template>
  <div class="skeleton-loader">
    <div 
      v-for="i in count" 
      :key="i" 
      class="skeleton-item"
      :style="{ height: height + 'px', marginBottom: gap + 'px' }"
    ></div>
  </div>
</template>

<script setup lang="ts">
defineProps({
  count: {
    type: Number,
    default: 3
  },
  height: {
    type: Number,
    default: 80
  },
  gap: {
    type: Number,
    default: 12
  }
});
</script>

<style scoped>
.skeleton-loader {
  width: 100%;
}

.skeleton-item {
  width: 100%;
  background: #f0f2f5;
  background: linear-gradient(90deg, #f0f2f5 25%, #e6e8eb 50%, #f0f2f5 75%);
  background-size: 200% 100%;
  border-radius: 12px;
  animation: loading 1.5s infinite;
}

@keyframes loading {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
