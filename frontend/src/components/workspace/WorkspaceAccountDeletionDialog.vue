<script setup lang="ts">
import { computed, ref, toRef, useId, watch } from 'vue'
import AppButton from '../ui/AppButton.vue'
import AppCheckbox from '../ui/AppCheckbox.vue'
import AppFormField from '../ui/AppFormField.vue'
import AppInput from '../ui/AppInput.vue'
import { useOverlayA11y } from '../ui/useOverlayA11y'

const props = withDefaults(
  defineProps<{
    open: boolean
    subjectName: string
    busy?: boolean
    error?: string
  }>(),
  {
    busy: false,
    error: '',
  },
)

const emit = defineEmits<{
  cancel: []
  confirm: []
}>()

const containerRef = ref<HTMLElement | null>(null)
const confirmationName = ref('')
const acknowledged = ref(false)
const submissionLocked = ref(false)
const description = computed(() => 'این اقدام فقط قطع یک رابطه نیست و بازگشت خودکار ندارد.')
const hasValidSubjectName = computed(() => props.subjectName.trim().length > 0)
const confirmationMatches = computed(
  () => hasValidSubjectName.value && confirmationName.value === props.subjectName,
)
const canConfirm = computed(
  () => confirmationMatches.value && acknowledged.value && !props.busy && !submissionLocked.value,
)
const consequencesId = useId()

const { titleId, descriptionId } = useOverlayA11y({
  open: toRef(props, 'open'),
  description,
  containerRef,
  close: () => {
    if (!props.busy) emit('cancel')
  },
})

watch([() => props.open, () => props.subjectName], ([open]) => {
  if (!open) return
  confirmationName.value = ''
  acknowledged.value = false
  submissionLocked.value = false
})

watch([() => props.busy, () => props.error], ([busy, error], [wasBusy, previousError]) => {
  if (!busy && error && (wasBusy || error !== previousError)) {
    submissionLocked.value = false
  }
})

function confirmDeletion() {
  if (!canConfirm.value) return
  submissionLocked.value = true
  emit('confirm')
}
</script>

<template>
  <div v-if="open" class="ui-v2-workspace-account-deletion-backdrop">
    <section
      ref="containerRef"
      class="ui-v2-workspace-account-deletion-dialog"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      :aria-describedby="`${descriptionId} ${consequencesId}`"
      :aria-busy="busy ? 'true' : undefined"
      tabindex="-1"
    >
      <header class="ui-v2-workspace-account-deletion-dialog__header">
        <h2 :id="titleId">حذف حساب {{ subjectName }}</h2>
        <p :id="descriptionId">{{ description }}</p>
      </header>

      <div class="ui-v2-workspace-account-deletion-dialog__body">
        <ul :id="consequencesId" class="ui-v2-workspace-account-deletion-dialog__consequences">
          <li>دسترسی وب‌اپ و ربات قطع می‌شود.</li>
          <li>همه نشست‌های فعال پایان می‌یابند.</li>
          <li>آفرهای فعال منقضی می‌شوند.</li>
          <li>دعوت‌های در انتظار مرتبط لغو می‌شوند.</li>
          <li>همه روابط باز مشتری و حسابدارِ متعلق یا لینک‌شده بسته می‌شوند.</li>
          <li>
            حساب‌های فعال وابسته‌ای که این کاربر مالک آن‌هاست ممکن است به‌صورت بازگشتی حذف شوند.
          </li>
          <li>سوابق معاملات حذف نمی‌شوند.</li>
        </ul>

        <AppFormField
          :label="`برای تأیید، نام نمایش‌داده‌شده «${subjectName}» را وارد کنید.`"
          :error="
            confirmationName && !confirmationMatches
              ? 'نام واردشده دقیقاً با نام نمایش‌داده‌شده یکسان نیست.'
              : undefined
          "
        >
          <template #default="{ id, describedby, invalid }">
            <AppInput
              :id="id"
              v-model="confirmationName"
              :aria-describedby="describedby"
              :invalid="invalid"
              :disabled="busy"
              autocomplete="off"
              spellcheck="false"
            />
          </template>
        </AppFormField>

        <label class="ui-v2-workspace-account-deletion-dialog__acknowledgement">
          <AppCheckbox v-model="acknowledged" :disabled="busy" />
          <span>پیامدهای بالا را خواندم و تأیید می‌کنم.</span>
        </label>

        <p
          v-if="busy"
          class="ui-v2-workspace-account-deletion-dialog__status"
          role="status"
          aria-live="polite"
        >
          حذف حساب در حال انجام است؛ تا اعلام نتیجه منتظر بمانید.
        </p>

        <p v-if="error" class="ui-v2-workspace-account-deletion-dialog__error" role="alert">
          {{ error }}
        </p>
      </div>

      <footer class="ui-v2-workspace-account-deletion-dialog__actions">
        <AppButton variant="secondary" block :disabled="busy" @click="$emit('cancel')">
          بازگشت امن
        </AppButton>
        <AppButton
          variant="danger"
          block
          :loading="busy"
          :disabled="!canConfirm"
          :aria-busy="busy ? 'true' : undefined"
          @click="confirmDeletion"
        >
          حذف حساب و قطع ارتباط
        </AppButton>
      </footer>
    </section>
  </div>
</template>
