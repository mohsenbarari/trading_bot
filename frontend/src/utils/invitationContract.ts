export type InvitationState = 'pending' | 'completed' | 'expired' | 'revoked' | string
export type InvitationSmsStatus = 'disabled' | 'pending' | 'accepted' | 'failed' | 'ambiguous' | string

export interface InvitationContractPayload {
  token?: string | null
  valid?: boolean
  bot_link?: string | null
  web_link?: string | null
  web_short_link?: string | null
  link?: string | null
  short_link?: string | null
  bot_available?: boolean
  web_available?: boolean
  state?: InvitationState | null
  sms_status?: InvitationSmsStatus | null
  expires_at?: string | null
}

export interface NormalizedInvitationContract {
  token: string
  botLink: string
  webLink: string
  botAvailable: boolean
  webAvailable: boolean
  state: InvitationState
  smsStatus: InvitationSmsStatus | null
  expiresAt: string
}

export function normalizeInvitationContract(
  payload: InvitationContractPayload,
): NormalizedInvitationContract {
  const state = payload.state || 'pending'
  const pending = state === 'pending'
  const botLink = payload.bot_link ?? payload.link ?? ''
  const webLink = payload.web_short_link ?? payload.short_link ?? payload.web_link ?? ''

  return {
    token: pending ? payload.token || '' : '',
    botLink: pending && payload.bot_available !== false ? botLink : '',
    webLink: pending && payload.web_available !== false ? webLink : '',
    botAvailable: pending && payload.bot_available !== false,
    webAvailable: pending && payload.web_available !== false,
    state,
    smsStatus: payload.sms_status || null,
    expiresAt: payload.expires_at || '',
  }
}

export function invitationSmsStatusMessage(status: InvitationSmsStatus | null | undefined): string {
  if (status === 'disabled') return 'پیامک دعوت ارسال نشد؛ لینک را دستی ارسال کنید.'
  if (status === 'pending') return 'وضعیت ارسال پیامک در حال بررسی است.'
  if (status === 'accepted') return 'پیامک دعوت ارسال شد.'
  if (status === 'failed') return 'ارسال پیامک دعوت ناموفق بود؛ لینک را دستی ارسال کنید.'
  if (status === 'ambiguous') return 'نتیجه ارسال پیامک مشخص نیست؛ پیش از ارسال دوباره وضعیت را بررسی کنید.'
  return ''
}

export function invitationTerminalMessage(state: InvitationState): string {
  if (state === 'expired') return 'مهلت ثبت‌نام پایان یافته است. لطفاً دعوت‌نامه جدید دریافت کنید.'
  if (state === 'revoked') return 'این دعوت‌نامه دیگر معتبر نیست.'
  return 'دعوت‌نامه نامعتبر یا منقضی شده است.'
}

export function invitationRelationLink(
  relation: {
    registration_link?: string | null
    bot_registration_link?: string | null
    web_registration_link?: string | null
  },
  surface: 'bot' | 'web',
): string {
  if (surface === 'bot') return relation.bot_registration_link || ''
  return relation.web_registration_link || relation.registration_link || ''
}
