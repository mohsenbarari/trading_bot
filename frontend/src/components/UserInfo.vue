<template>
  <div class="card user-info">
    <p v-if="user">👋 خوش آمدید <strong>{{ user.full_name }}</strong></p>
    <p v-if="user?.role" class="meta">نقش شما: <strong>{{ user.role }}</strong></p>
    <p v-else>در حال بارگذاری اطلاعات کاربر...</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{ user: any }>()
</script>

