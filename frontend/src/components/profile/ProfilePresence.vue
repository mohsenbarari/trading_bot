<script setup lang="ts">
withDefaults(defineProps<{
  status?: string | null
  online?: boolean
  own?: boolean
}>(), {
  status: null,
  online: false,
  own: false,
})
</script>

<template>
  <p
    v-if="status"
    class="profile-presence-status"
    :class="{
      'profile-presence-status--own': own,
      online,
    }"
    data-test="profile-presence"
  >
    {{ status }}
  </p>
</template>

<style scoped>
.profile-presence-status {
  margin: 6px 0 0;
  font-size: 0.84rem;
  font-weight: 700;
  color: var(--ds-text-secondary);
}

.profile-presence-status--own {
  position: absolute;
  top: 68px;
  left: 0;
  right: 0;
  margin: 0;
  min-height: 1.1rem;
  font-size: 0.76rem;
  line-height: 1.45;
  text-align: center;
}

.profile-presence-status.online {
  color: var(--ui-v2-color-text-warning, #b45309);
}

@media (prefers-reduced-motion: reduce) {
  .profile-presence-status {
    transition: none;
  }
}
</style>
