<script setup lang="ts">
withDefaults(defineProps<{
  actionClass?: string
}>(), {
  actionClass: '',
})
</script>

<template>
  <div
    class="workspace-form-actions"
    :class="actionClass"
    data-test="workspace-form-actions"
  >
    <slot />
  </div>
</template>

<style scoped>
.workspace-form-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  position: static;
  inset: auto;
  width: 100%;
  padding-bottom: max(0.5rem, env(safe-area-inset-bottom, 0px));
}

@media (max-width: 430px) {
  .workspace-form-actions {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
