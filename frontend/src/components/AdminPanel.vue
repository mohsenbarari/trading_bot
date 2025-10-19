<template>
  <div class="card admin-panel">
    <h2 style="margin-top:0">ایجاد لینک دعوت</h2>
    <InvitationForm @invite-created="handleInviteCreated" />
  </div>
</template>

<script setup lang="ts">
import InvitationForm from './InvitationForm.vue'

const emit = defineEmits(['invite-created'])
function handleInviteCreated(link: string) {
  emit('invite-created', link)
}
</script>
