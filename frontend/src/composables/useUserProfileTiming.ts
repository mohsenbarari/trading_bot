import { onUnmounted, ref, watchEffect, type Ref } from 'vue'
import moment from 'moment-jalaali'

type UserTimingSource = {
  trading_restricted_until?: string | null
  limitations_expire_at?: string | null
}

export function useUserProfileTiming(user: Ref<UserTimingSource | null | undefined>) {
  const countdownRestriction = ref('')
  const countdownLimitation = ref('')
  let countdownInterval: ReturnType<typeof setInterval> | null = null

  function formatCountdown(seconds: number): string {
    if (seconds <= 0) return 'منقضی شده'

    const days = Math.floor(seconds / 86400)
    const hours = Math.floor((seconds % 86400) / 3600)
    const minutes = Math.floor((seconds % 3600) / 60)
    const secs = Math.floor(seconds % 60)

    if (days > 0) {
      return `${days} روز ${hours} ساعت ${minutes} دقیقه`
    }

    if (hours > 0) {
      return `${hours} ساعت ${minutes} دقیقه ${secs} ثانیه`
    }

    if (minutes > 0) {
      return `${minutes} دقیقه ${secs} ثانیه`
    }

    return `${secs} ثانیه`
  }

  function stopCountdownInterval() {
    if (countdownInterval) {
      clearInterval(countdownInterval)
      countdownInterval = null
    }
  }

  function updateCountdowns() {
    const currentUser = user.value
    const now = moment.utc()

    if (currentUser?.trading_restricted_until) {
      const restrictionTime = moment.utc(currentUser.trading_restricted_until)
      if (restrictionTime.isValid() && restrictionTime.year() <= 2100) {
        const diffSeconds = restrictionTime.diff(now, 'seconds')
        countdownRestriction.value = formatCountdown(diffSeconds)
      } else if (restrictionTime.year() > 2100) {
        countdownRestriction.value = 'دائمی'
      } else {
        countdownRestriction.value = ''
      }
    } else {
      countdownRestriction.value = ''
    }

    if (currentUser?.limitations_expire_at) {
      const limitTime = moment.utc(currentUser.limitations_expire_at)
      if (limitTime.isValid()) {
        const diffSeconds = limitTime.diff(now, 'seconds')
        countdownLimitation.value = formatCountdown(diffSeconds)
      } else {
        countdownLimitation.value = ''
      }
    } else {
      countdownLimitation.value = ''
    }
  }

  const toEnglishDigits = (str: string) => {
    if (!str) return str
    return str.replace(/[۰-۹]/g, (digit) => '۰۱۲۳۴۵۶۷۸۹'.indexOf(digit).toString())
  }

  const parseJalaliToIranISO = (jalaliStr: string) => {
    const normalized = toEnglishDigits(jalaliStr)
    const parsedDate = moment(normalized, 'jYYYY/jMM/jDD HH:mm')
    if (!parsedDate.isValid()) return null

    parsedDate.utcOffset(210, true)
    return parsedDate.toISOString()
  }

  watchEffect(() => {
    stopCountdownInterval()

    const currentUser = user.value
    if (currentUser?.trading_restricted_until || currentUser?.limitations_expire_at) {
      updateCountdowns()
      countdownInterval = setInterval(updateCountdowns, 1000)
    } else {
      countdownRestriction.value = ''
      countdownLimitation.value = ''
    }
  })

  onUnmounted(() => {
    stopCountdownInterval()
  })

  return {
    countdownRestriction,
    countdownLimitation,
    parseJalaliToIranISO,
    toEnglishDigits,
  }
}