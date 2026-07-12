import { describe, expect, it } from 'vitest'
import {
  invitationRelationLink,
  invitationSmsStatusMessage,
  invitationTerminalMessage,
  normalizeInvitationContract,
} from './invitationContract'

describe('invitationContract', () => {
  it('keeps legacy aliases available while preferring explicit v2 fields', () => {
    expect(normalizeInvitationContract({ token: 'legacy', link: 'bot-old', short_link: 'web-old' })).toMatchObject({
      token: 'legacy',
      botLink: 'bot-old',
      webLink: 'web-old',
      botAvailable: true,
      webAvailable: true,
      state: 'pending',
    })

    expect(normalizeInvitationContract({
      token: 'v2',
      bot_link: 'bot-v2',
      web_short_link: 'web-v2',
      link: 'bot-old',
      short_link: 'web-old',
      bot_available: false,
      web_available: true,
      state: 'pending',
      sms_status: 'disabled',
    })).toMatchObject({
      botLink: '',
      webLink: 'web-v2',
      botAvailable: false,
      webAvailable: true,
      smsStatus: 'disabled',
    })
  })

  it('removes all actions and tokens from terminal UI state', () => {
    expect(normalizeInvitationContract({
      token: 'must-not-use',
      bot_link: 'bot',
      web_link: 'web',
      state: 'completed',
    })).toMatchObject({
      token: '',
      botLink: '',
      webLink: '',
      botAvailable: false,
      webAvailable: false,
    })
  })

  it('maps every bounded SMS and terminal state to truthful Persian copy', () => {
    expect(invitationSmsStatusMessage('disabled')).toContain('ارسال نشد')
    expect(invitationSmsStatusMessage('pending')).toContain('در حال بررسی')
    expect(invitationSmsStatusMessage('accepted')).toContain('ارسال شد')
    expect(invitationSmsStatusMessage('failed')).toContain('ناموفق')
    expect(invitationSmsStatusMessage('ambiguous')).toContain('مشخص نیست')
    expect(invitationSmsStatusMessage(null)).toBe('')
    expect(invitationTerminalMessage('expired')).toContain('پایان یافته')
    expect(invitationTerminalMessage('revoked')).toContain('دیگر معتبر نیست')
  })

  it('selects explicit relation links per surface', () => {
    const relation = {
      registration_link: 'legacy-web',
      bot_registration_link: 'bot-v2',
      web_registration_link: 'web-v2',
    }
    expect(invitationRelationLink(relation, 'bot')).toBe('bot-v2')
    expect(invitationRelationLink(relation, 'web')).toBe('web-v2')
  })
})
