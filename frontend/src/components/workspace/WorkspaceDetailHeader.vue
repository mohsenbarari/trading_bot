<script setup lang="ts">
withDefaults(defineProps<{
  title?: string
  description?: string
  headerClass?: string
  badgesClass?: string
}>(), {
  title: '',
  description: '',
  headerClass: '',
  badgesClass: '',
})
</script>

<template>
  <header
    class="workspace-detail-header"
    :class="headerClass"
    data-test="workspace-detail-header"
  >
    <div class="workspace-detail-header__copy">
      <h2>
        <slot name="title">{{ title }}</slot>
      </h2>
      <p v-if="description || $slots.description">
        <slot name="description">{{ description }}</slot>
      </p>
    </div>
    <div v-if="$slots.default" class="workspace-detail-header__badges" :class="badgesClass">
      <slot />
    </div>
  </header>
</template>

<style scoped>
.workspace-detail-header {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--ds-section-gap);
  min-width: 0;
}

.workspace-detail-header__copy {
  min-width: 0;
  flex: 1 1 12rem;
}

.workspace-detail-header__copy h2 {
  margin: 0;
  overflow-wrap: anywhere;
}

.workspace-detail-header__copy p {
  margin: 0.25rem 0 0;
  overflow-wrap: anywhere;
}

.workspace-detail-header__badges {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
</style>
