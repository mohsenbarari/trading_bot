<script setup lang="ts">
withDefaults(defineProps<{
  error?: string | null
}>(), {
  error: null,
})
</script>

<template>
  <section class="profile-section owner-profile-section" data-test="profile-admin-controls">
    <p v-if="error" class="admin-user-error" role="alert">{{ error }}</p>
    <slot />
  </section>
</template>

<style scoped>
.admin-user-error {
  margin: 0 0 0.75rem;
  color: var(--ds-danger-700);
}
</style>
