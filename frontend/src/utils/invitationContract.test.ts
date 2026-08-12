import { describe, expect, it } from 'vitest'
import {
  invitationRelationLink,
  invitationSmsStatusMessage,
  invitationTerminalMessage,
  normalizeInvitationContract,
} from './invitationContract'

describe('invitationContract', () => {
  it('normalizes bounded lookup aliases while preferring explicit v2 fields', () => {
    expect(
      normalizeInvitationContract({
        token: 'legacy',
        link: 'https://t.me/test_bot?start=INV-legacy',
        short_link: '/i/LEGACY01',
      }),
    ).toMatchObject({
      token: 'legacy',
      created: null,
      botLink: 'https://t.me/test_bot?start=INV-legacy',
      webLink: '/i/LEGACY01',
      botAvailable: true,
      webAvailable: true,
      state: 'pending',
    })

    expect(
      normalizeInvitationContract({
        token: 'v2',
        bot_link: 'https://t.me/test_bot?start=INV-v2',
        web_short_link: 'https://example.test/i/V2CODE01',
        link: 'https://t.me/legacy_bot?start=INV-old',
        short_link: 'web-old',
        bot_available: false,
        web_available: true,
        state: 'pending',
        sms_status: 'disabled',
      }),
    ).toMatchObject({
      created: null,
      botLink: '',
      webLink: 'https://example.test/i/V2CODE01',
      botAvailable: false,
      webAvailable: true,
      smsStatus: 'disabled',
    })
  })

  it('preserves the server-owned created versus recovered result without inventing it for queue payloads', () => {
    expect(
      normalizeInvitationContract({
        created: true,
        state: 'pending',
        web_short_link: '/i/CREATE01',
      }),
    ).toMatchObject({ created: true })

    expect(
      normalizeInvitationContract({
        created: false,
        state: 'pending',
        web_short_link: '/i/REUSED01',
      }),
    ).toMatchObject({ created: false })

    expect(
      normalizeInvitationContract({
        created: 'false' as unknown as boolean,
        state: 'pending',
        web_short_link: '/i/UNKNOWN1',
      }),
    ).toMatchObject({ created: null })
  })

  it('removes all actions and tokens from terminal UI state', () => {
    expect(
      normalizeInvitationContract({
        token: 'must-not-use',
        bot_link: 'bot',
        web_link: 'web',
        state: 'completed',
      }),
    ).toMatchObject({
      token: '',
      botLink: '',
      webLink: '',
      botAvailable: false,
      webAvailable: false,
    })
  })

  it('preserves lookup availability flags without inventing copyable links', () => {
    expect(
      normalizeInvitationContract({
        token: 'INV-lookup-only',
        bot_available: true,
        web_available: true,
        state: 'pending',
      }),
    ).toMatchObject({
      token: 'INV-lookup-only',
      botLink: '',
      webLink: '',
      botAvailable: true,
      webAvailable: true,
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
      registration_link: 'https://example.test/register?token=raw-legacy-bearer',
      bot_registration_link: 'https://t.me/test_bot?start=INV-v2',
      web_registration_link: 'https://example.test/i/OLDER001',
      web_short_link: 'https://example.test/i/CANON001',
    }
    expect(invitationRelationLink(relation, 'bot')).toBe('https://t.me/test_bot?start=INV-v2')
    expect(invitationRelationLink(relation, 'web')).toBe('https://example.test/i/CANON001')
  })

  it('fails closed instead of exposing a raw bearer Web URL', () => {
    expect(
      invitationRelationLink(
        {
          registration_link: 'https://example.test/register?token=INV-raw',
          web_registration_link: '/register?registration_token=REG-raw',
        },
        'web',
      ),
    ).toBe('')
    expect(
      invitationRelationLink({ web_short_link: 'https://example.test/i/ACCT-raw-bearer' }, 'web'),
    ).toBe('')
    expect(
      invitationRelationLink(
        { bot_registration_link: 'https://evil.example/?start=INV-raw' },
        'bot',
      ),
    ).toBe('')
  })
})
