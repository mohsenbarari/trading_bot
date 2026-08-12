export type InvitationState = 'pending' | 'completed' | 'expired' | 'revoked' | string
export type InvitationSmsStatus =
  | 'disabled'
  | 'pending'
  | 'accepted'
  | 'failed'
  | 'ambiguous'
  | string

export interface InvitationContractPayload {
  token?: string | null
  /** Present only on the standard-invitation create response. */
  created?: boolean | null
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
  /**
   * `true` means the server created a new invitation, `false` means it
   * recovered the still-pending canonical invitation, and `null` means the
   * payload did not make that claim (for example a queue/lookup response).
   */
  created: boolean | null
  botLink: string
  webLink: string
  botAvailable: boolean
  webAvailable: boolean
  state: InvitationState
  smsStatus: InvitationSmsStatus | null
  expiresAt: string
}

function canonicalInvitationWebLink(...candidates: Array<string | null | undefined>): string {
  for (const candidate of candidates) {
    if (typeof candidate !== 'string' || !candidate.trim()) continue
    const value = candidate.trim()
    try {
      const url = new URL(value, 'https://invitation.invalid')
      if (
        /^\/i\/[A-Za-z0-9]{8}$/u.test(url.pathname) &&
        !url.search &&
        !url.hash &&
        !url.username &&
        !url.password &&
        (url.origin === 'https://invitation.invalid' || /^https?:$/u.test(url.protocol))
      ) {
        return value
      }
    } catch {
      // Ignore malformed or non-canonical legacy links.
    }
  }
  return ''
}

function canonicalInvitationBotLink(...candidates: Array<string | null | undefined>): string {
  for (const candidate of candidates) {
    if (typeof candidate !== 'string' || !candidate.trim()) continue
    const value = candidate.trim()
    try {
      const url = new URL(value)
      const queryKeys = [...url.searchParams.keys()]
      if (
        url.protocol === 'https:' &&
        url.hostname === 't.me' &&
        !url.port &&
        !url.username &&
        !url.password &&
        /^\/[A-Za-z0-9_]+$/u.test(url.pathname) &&
        queryKeys.length === 1 &&
        queryKeys[0] === 'start' &&
        Boolean(url.searchParams.get('start')) &&
        !url.hash
      ) {
        return value
      }
    } catch {
      // Ignore malformed or non-Telegram legacy links.
    }
  }
  return ''
}

export function normalizeInvitationContract(
  payload: InvitationContractPayload,
): NormalizedInvitationContract {
  const state = payload.state || 'pending'
  const pending = state === 'pending'
  const botLink = canonicalInvitationBotLink(payload.bot_link, payload.link)
  const webLink = canonicalInvitationWebLink(
    payload.web_short_link,
    payload.short_link,
    payload.web_link,
  )

  return {
    token: pending ? payload.token || '' : '',
    created: typeof payload.created === 'boolean' ? payload.created : null,
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
  if (status === 'ambiguous')
    return 'نتیجه ارسال پیامک مشخص نیست؛ پیش از ارسال دوباره وضعیت را بررسی کنید.'
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
    web_short_link?: string | null
    web_registration_link?: string | null
  },
  surface: 'bot' | 'web',
): string {
  if (surface === 'bot') return canonicalInvitationBotLink(relation.bot_registration_link)
  return canonicalInvitationWebLink(
    relation.web_short_link,
    relation.web_registration_link,
    relation.registration_link,
  )
}
