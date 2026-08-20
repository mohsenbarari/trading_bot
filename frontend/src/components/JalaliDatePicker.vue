<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import moment from 'moment-jalaali'
import { CalendarDays, ChevronDown, ChevronLeft, ChevronRight } from 'lucide-vue-next'

type CalendarValueType = 'gregorian' | 'jalali'
type JalaliMoment = ReturnType<typeof moment>

const props = withDefaults(defineProps<{
  modelValue?: string | null
  valueType?: CalendarValueType
  placeholder?: string
  disabled?: boolean
  clearable?: boolean
  inline?: boolean
  autoClose?: boolean
  triggerTestId?: string
  arrowKeyNavigation?: boolean
}>(), {
  modelValue: '',
  valueType: 'gregorian',
  placeholder: 'انتخاب تاریخ',
  disabled: false,
  clearable: true,
  inline: false,
  autoClose: true,
  triggerTestId: undefined,
  arrowKeyNavigation: false,
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void
  (event: 'change', value: string): void
}>()

const monthNames = [
  'فروردین',
  'اردیبهشت',
  'خرداد',
  'تیر',
  'مرداد',
  'شهریور',
  'مهر',
  'آبان',
  'آذر',
  'دی',
  'بهمن',
  'اسفند',
]
const weekdayNames = ['ش', 'ی', 'د', 'س', 'چ', 'پ', 'ج']
const persianDigits = ['۰', '۱', '۲', '۳', '۴', '۵', '۶', '۷', '۸', '۹']

const rootRef = ref<HTMLElement | null>(null)
const isOpen = ref(false)
const visibleMonth = ref(moment())

function toEnglishDigits(value: string) {
  return value.replace(/[۰-۹]/g, (digit) => String('۰۱۲۳۴۵۶۷۸۹'.indexOf(digit)))
}

function toPersianDigits(value: string | number) {
  return String(value).replace(/\d/g, (digit) => persianDigits[Number(digit)] ?? digit)
}

function parseModelValue(value: string | null | undefined, valueType: CalendarValueType) {
  if (!value) return null
  const normalized = toEnglishDigits(value.trim())
  const parsed = valueType === 'jalali'
    ? moment(normalized, ['jYYYY/jMM/jDD', 'jYYYY/jM/jD'], true)
    : moment(normalized, ['YYYY-MM-DD', 'YYYY-M-D'], true)
  return parsed.isValid() ? parsed : null
}

function createVisibleJalaliDate(year: number, monthIndex: number, preferredDay: number) {
  const monthStart = moment(`${year}/${monthIndex + 1}/1`, 'jYYYY/jM/jD', true)
  if (!monthStart.isValid()) return visibleMonth.value.clone()
  const day = Math.min(preferredDay, monthStart.clone().endOf('jMonth').jDate())
  return monthStart.jDate(day)
}

const selectedDate = computed(() => parseModelValue(props.modelValue, props.valueType))

const visibleMonthTitle = computed(() => {
  const monthIndex = visibleMonth.value.jMonth()
  return `${monthNames[monthIndex]} ${toPersianDigits(visibleMonth.value.jYear())}`
})

const displayValue = computed(() => {
  if (!selectedDate.value) return ''
  const day = toPersianDigits(selectedDate.value.jDate())
  const month = monthNames[selectedDate.value.jMonth()]
  const year = toPersianDigits(selectedDate.value.jYear())
  return `${day} ${month} ${year}`
})

const selectedKey = computed(() => selectedDate.value?.format('jYYYY/jMM/jDD') ?? '')
const todayKey = computed(() => moment().format('jYYYY/jMM/jDD'))

const monthOptions = monthNames.map((label, index) => ({ value: String(index), label }))
const yearOptions = computed(() => {
  const currentYear = moment().jYear()
  const visibleYear = visibleMonth.value.jYear()
  const selectedYear = selectedDate.value?.jYear() ?? visibleYear
  const minYear = Math.min(currentYear, visibleYear, selectedYear) - 20
  const maxYear = Math.max(currentYear, visibleYear, selectedYear) + 20
  const options: Array<{ value: string; label: string }> = []
  for (let year = minYear; year <= maxYear; year += 1) {
    options.push({ value: String(year), label: toPersianDigits(year) })
  }
  return options
})

type CalendarCell = {
  key: string
  label: string
  date: JalaliMoment | null
  isSelected: boolean
  isToday: boolean
}

const calendarCells = computed<CalendarCell[]>(() => {
  const monthStart = visibleMonth.value.clone().startOf('jMonth')
  const daysInMonth = visibleMonth.value.clone().endOf('jMonth').jDate()
  const leadingBlanks = (monthStart.day() + 1) % 7
  const cells: CalendarCell[] = []

  for (let index = 0; index < leadingBlanks; index += 1) {
    cells.push({ key: `blank-${index}`, label: '', date: null, isSelected: false, isToday: false })
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    const date = monthStart.clone().jDate(day)
    const key = date.format('jYYYY/jMM/jDD')
    cells.push({
      key,
      label: toPersianDigits(day),
      date,
      isSelected: key === selectedKey.value,
      isToday: key === todayKey.value,
    })
  }

  return cells
})

watch(
  () => props.modelValue,
  () => {
    const parsed = selectedDate.value
    if (parsed) {
      visibleMonth.value = parsed.clone()
    }
  },
  { immediate: true },
)

function emitValue(date: JalaliMoment) {
  const value = props.valueType === 'jalali'
    ? date.format('jYYYY/jMM/jDD')
    : date.format('YYYY-MM-DD')
  emit('update:modelValue', value)
  emit('change', value)
}

function selectDate(date: JalaliMoment | null) {
  if (!date || props.disabled) return
  emitValue(date)
  if (!props.inline && props.autoClose) {
    isOpen.value = false
  }
}

function clearValue() {
  if (props.disabled) return
  emit('update:modelValue', '')
  emit('change', '')
}

function openPicker() {
  if (props.disabled || props.inline) return
  const parsed = selectedDate.value
  visibleMonth.value = parsed?.clone() ?? moment()
  isOpen.value = true
}

function togglePicker() {
  if (isOpen.value) {
    isOpen.value = false
    return
  }
  openPicker()
}

function goToPreviousMonth() {
  visibleMonth.value = visibleMonth.value.clone().subtract(1, 'jMonth')
}

function goToNextMonth() {
  visibleMonth.value = visibleMonth.value.clone().add(1, 'jMonth')
}

function updateVisibleMonth(event: Event) {
  const value = Number((event.target as HTMLSelectElement | null)?.value)
  if (!Number.isInteger(value) || value < 0 || value > 11) return
  visibleMonth.value = createVisibleJalaliDate(visibleMonth.value.jYear(), value, visibleMonth.value.jDate())
}

function updateVisibleYear(event: Event) {
  const value = Number((event.target as HTMLSelectElement | null)?.value)
  if (!Number.isInteger(value)) return
  visibleMonth.value = createVisibleJalaliDate(value, visibleMonth.value.jMonth(), visibleMonth.value.jDate())
}

function goToToday() {
  visibleMonth.value = moment()
  emitValue(moment())
  if (!props.inline && props.autoClose) {
    isOpen.value = false
  }
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (props.inline || !isOpen.value) return
  const target = event.target
  if (target instanceof Node && rootRef.value?.contains(target)) return
  isOpen.value = false
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    isOpen.value = false
  }
}

function focusCalendarDay(date: JalaliMoment) {
  const key = date.format('jYYYY/jMM/jDD')
  void nextTick(() => {
    rootRef.value
      ?.querySelector<HTMLButtonElement>(`.jalali-calendar-day[data-day-key="${key}"]`)
      ?.focus()
  })
}

function handleDayKeydown(event: KeyboardEvent, date: JalaliMoment | null) {
  if (!props.arrowKeyNavigation || !date || props.disabled) return

  let delta = 0
  if (event.key === 'ArrowRight') delta = -1
  else if (event.key === 'ArrowLeft') delta = 1
  else if (event.key === 'ArrowUp') delta = -7
  else if (event.key === 'ArrowDown') delta = 7
  else return

  event.preventDefault()
  const nextDate = date.clone().add(delta, 'day')
  if (
    nextDate.jMonth() !== visibleMonth.value.jMonth() ||
    nextDate.jYear() !== visibleMonth.value.jYear()
  ) {
    visibleMonth.value = nextDate.clone()
  }
  focusCalendarDay(nextDate)
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
})

watch(isOpen, async (opened) => {
  if (!opened) return
  await nextTick()
  rootRef.value?.querySelector<HTMLButtonElement>('.jalali-calendar-day.is-selected, .jalali-calendar-day.is-today')?.focus()
})
</script>

<template>
  <div ref="rootRef" class="jalali-date-picker" :class="{ 'is-inline': inline, 'is-disabled': disabled }" @keydown="handleKeydown">
    <button
      v-if="!inline"
      type="button"
      class="jalali-date-trigger"
      :class="{ 'has-value': Boolean(displayValue) }"
      :disabled="disabled"
      :data-testid="triggerTestId"
      @click="togglePicker"
    >
      <span class="jalali-date-trigger__icon" aria-hidden="true">
        <CalendarDays :size="16" />
      </span>
      <span class="jalali-date-trigger__text">{{ displayValue || placeholder }}</span>
      <span class="jalali-date-trigger__chevron" aria-hidden="true">
        <ChevronDown :size="18" />
      </span>
    </button>

    <div v-if="inline || isOpen" class="jalali-calendar-panel" :class="{ 'is-popover': !inline }" role="dialog" aria-label="تقویم جلالی">
      <div class="jalali-calendar-header">
        <button type="button" class="jalali-calendar-nav" aria-label="ماه قبل" @click="goToPreviousMonth">
          <ChevronRight :size="20" />
        </button>
        <div class="jalali-calendar-title" :aria-label="visibleMonthTitle">
          <select class="jalali-calendar-select" aria-label="انتخاب ماه" :value="visibleMonth.jMonth()" @change="updateVisibleMonth">
            <option v-for="option in monthOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
          <select class="jalali-calendar-select year-select" aria-label="انتخاب سال" :value="visibleMonth.jYear()" @change="updateVisibleYear">
            <option v-for="option in yearOptions" :key="option.value" :value="option.value">
              {{ option.label }}
            </option>
          </select>
        </div>
        <button type="button" class="jalali-calendar-nav" aria-label="ماه بعد" @click="goToNextMonth">
          <ChevronLeft :size="20" />
        </button>
      </div>

      <div class="jalali-calendar-weekdays" aria-hidden="true">
        <span v-for="weekday in weekdayNames" :key="weekday">{{ weekday }}</span>
      </div>

      <div class="jalali-calendar-grid">
        <button
          v-for="cell in calendarCells"
          :key="cell.key"
          type="button"
          class="jalali-calendar-day"
          :class="{ 'is-empty': !cell.date, 'is-selected': cell.isSelected, 'is-today': cell.isToday }"
          :data-day-key="cell.date ? cell.key : undefined"
          :disabled="!cell.date"
          @click="selectDate(cell.date)"
          @keydown="handleDayKeydown($event, cell.date)"
        >
          {{ cell.label }}
        </button>
      </div>

      <div class="jalali-calendar-footer">
        <button type="button" class="jalali-calendar-link" @click="goToToday">امروز</button>
        <button v-if="clearable" type="button" class="jalali-calendar-link muted" @click="clearValue">پاک کردن</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.jalali-date-picker {
  position: relative;
  width: 100%;
  direction: rtl;
  font-family: inherit;
}

.jalali-date-trigger {
  width: 100%;
  min-height: var(--ds-control-min-height, 48px);
  display: grid;
  grid-template-columns: 28px minmax(0, 1fr) 24px;
  align-items: center;
  gap: 8px;
  padding: 9px var(--ds-control-padding-inline, 0.875rem);
  border: 1.5px solid var(--ds-control-border, #e5e7eb);
  border-radius: var(--ds-control-radius, 12px);
  background: var(--ds-control-bg, #f8fafc);
  color: var(--ds-text-primary, #213044);
  font-size: 14px;
  text-align: right;
  box-shadow: none;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

.jalali-date-trigger:not(:disabled):hover,
.jalali-date-trigger:not(:disabled):focus-visible {
  border-color: var(--ds-primary-500, #f59e0b);
  box-shadow: var(--ds-focus-ring, 0 0 0 4px rgba(245, 158, 11, 0.18));
  outline: none;
}

.jalali-date-trigger:not(:disabled):active {
  transform: translateY(1px);
}

.jalali-date-trigger:disabled {
  cursor: not-allowed;
  opacity: 0.58;
}

.jalali-date-trigger__icon {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--ds-primary-100, #fef3c7);
  color: var(--ds-primary-700, #b45309);
  font-size: 15px;
}

.jalali-date-trigger__text {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: #64748b;
}

.jalali-date-trigger.has-value .jalali-date-trigger__text {
  color: #172033;
  font-weight: 700;
}

.jalali-date-trigger__chevron {
  color: #8290a5;
  text-align: center;
  font-size: 18px;
  line-height: 1;
}

.jalali-calendar-panel {
  width: min(100%, 360px);
  border: 1px solid var(--ds-native-hairline, rgba(60, 60, 67, 0.14));
  border-radius: 12px;
  background: var(--ds-bg-card, #ffffff);
  box-shadow: none;
  padding: 12px;
  color: var(--ds-text-primary, #172033);
  z-index: 50;
}

.jalali-calendar-panel.is-popover {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
}

.jalali-date-picker.is-inline .jalali-calendar-panel {
  width: 100%;
  max-width: 328px;
  margin: 0 auto;
  box-shadow: none;
}

.jalali-calendar-header {
  display: grid;
  grid-template-columns: var(--ds-native-row-min-height, 48px) minmax(0, 1fr) var(--ds-native-row-min-height, 48px);
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.jalali-calendar-title {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(82px, 0.72fr);
  align-items: center;
  gap: 7px;
}

.jalali-calendar-select {
  min-width: 0;
  width: 100%;
  min-height: var(--ds-control-min-height, 48px);
  height: var(--ds-control-min-height, 48px);
  border: 1.5px solid var(--ds-control-border, #e5e7eb);
  border-radius: var(--ds-control-radius, 12px);
  background: var(--ds-control-bg, #f8fafc);
  color: var(--ds-text-primary, #123f46);
  font: inherit;
  font-weight: 700;
  font-size: 13px;
  text-align: center;
  text-align-last: center;
  padding: 0 8px;
  box-shadow: none;
}

.jalali-calendar-select:hover,
.jalali-calendar-select:focus-visible {
  border-color: var(--ds-primary-500, #f59e0b);
  outline: none;
  box-shadow: var(--ds-focus-ring, 0 0 0 4px rgba(245, 158, 11, 0.18));
}

.jalali-calendar-select.year-select {
  direction: ltr;
}

.jalali-calendar-nav {
  width: var(--ds-native-row-min-height, 48px);
  height: var(--ds-native-row-min-height, 48px);
  border: 0;
  border-radius: var(--ds-radius-full, 999px);
  background: var(--ds-bg-inset, #f8fafc);
  color: var(--ds-text-primary, #113f45);
  font-size: 24px;
  line-height: 1;
}

.jalali-calendar-nav:hover,
.jalali-calendar-nav:focus-visible {
  background: var(--ds-primary-50, #fffbeb);
  color: var(--ds-primary-700, #b45309);
  outline: none;
}

.jalali-calendar-weekdays,
.jalali-calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 5px;
}

.jalali-calendar-weekdays {
  margin-bottom: 6px;
  color: #79869a;
  font-weight: 700;
  font-size: 12px;
  text-align: center;
}

.jalali-calendar-day {
  aspect-ratio: 1;
  min-width: 0;
  min-height: 40px;
  border: 0;
  border-radius: 8px;
  background: var(--ds-bg-inset, #f8fafc);
  color: var(--ds-text-primary, #243447);
  font-weight: 700;
  font-size: 13px;
  box-shadow: none;
}

.jalali-calendar-day:not(:disabled):hover,
.jalali-calendar-day:not(:disabled):focus-visible {
  background: var(--ds-primary-50, #fffbeb);
  color: var(--ds-primary-800, #92400e);
  outline: none;
}

.jalali-calendar-day.is-today {
  box-shadow: inset 0 0 0 2px var(--ds-primary-500, #f59e0b);
}

.jalali-calendar-day.is-selected {
  background: var(--ds-primary-500, #f59e0b);
  color: #ffffff;
  box-shadow: none;
}

.jalali-calendar-day.is-empty {
  background: transparent;
  box-shadow: none;
}

.jalali-calendar-footer {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(115, 130, 154, 0.14);
}

.jalali-calendar-link {
  border: 0;
  border-radius: 12px;
  background: var(--ds-bg-inset, #f8fafc);
  color: var(--ds-text-primary, #113f45);
  min-height: var(--ds-native-row-min-height, 48px);
  padding: 8px 14px;
  font-weight: 800;
  font-size: 13px;
}

.jalali-calendar-link.muted {
  background: var(--ds-bg-hover, #f3f4f6);
  color: var(--ds-text-muted, #64748b);
}

.jalali-calendar-link:hover,
.jalali-calendar-link:focus-visible {
  outline: none;
  background: var(--ds-primary-50, #fffbeb);
  color: var(--ds-primary-700, #b45309);
}

@media (max-width: 420px) {
  .jalali-calendar-panel {
    width: min(100%, calc(100vw - 32px));
  }
}
</style>
