import { describe, expect, it } from 'vitest'

import { resolveConversationProfileTarget, resolveForwardedProfileTarget } from './accountantChatIdentity'

describe('accountantChatIdentity', () => {
  it('resolves conversation profile targets with additive owner metadata first', () => {
    expect(
      resolveConversationProfileTarget({
        other_user_id: 11,
        other_user_name: 'raw-accountant',
        profile_user_id: 77,
        profile_account_name: 'owner-77',
        highlight_accountant_user_id: 11,
        highlight_accountant_relation_display_name: 'حسابدار فروش',
      }),
    ).toEqual({
      id: 77,
      account_name: 'owner-77',
      highlight_accountant_user_id: 11,
      highlight_accountant_relation_display_name: 'حسابدار فروش',
    })
  })

  it('falls back to the direct conversation identity and rejects invalid payloads', () => {
    expect(
      resolveConversationProfileTarget({
        other_user_id: 11,
        other_user_name: 'raw-user',
        profile_user_id: null,
        profile_account_name: null,
        highlight_accountant_user_id: null,
        highlight_accountant_relation_display_name: null,
      }),
    ).toEqual({
      id: 11,
      account_name: 'raw-user',
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
    })

    expect(
      resolveConversationProfileTarget({
        other_user_id: 0,
        other_user_name: '',
        profile_user_id: null,
        profile_account_name: null,
        highlight_accountant_user_id: null,
        highlight_accountant_relation_display_name: null,
      }),
    ).toBeNull()
    expect(resolveConversationProfileTarget(null)).toBeNull()
  })

  it('resolves forwarded profile targets with additive owner metadata first', () => {
    expect(
      resolveForwardedProfileTarget({
        forwarded_from_id: 12,
        forwarded_from_name: 'raw-forwarded',
        forwarded_from_profile_user_id: 88,
        forwarded_from_profile_account_name: 'owner-88',
        forwarded_from_highlight_accountant_user_id: 12,
        forwarded_from_highlight_accountant_relation_display_name: 'حسابدار دوم',
      }),
    ).toEqual({
      id: 88,
      account_name: 'owner-88',
      highlight_accountant_user_id: 12,
      highlight_accountant_relation_display_name: 'حسابدار دوم',
    })
  })

  it('falls back to raw forwarded identity and rejects invalid payloads', () => {
    expect(
      resolveForwardedProfileTarget({
        forwarded_from_id: 12,
        forwarded_from_name: 'raw-forwarded',
        forwarded_from_profile_user_id: null,
        forwarded_from_profile_account_name: null,
        forwarded_from_highlight_accountant_user_id: null,
        forwarded_from_highlight_accountant_relation_display_name: null,
      }),
    ).toEqual({
      id: 12,
      account_name: 'raw-forwarded',
      highlight_accountant_user_id: null,
      highlight_accountant_relation_display_name: null,
    })

    expect(
      resolveForwardedProfileTarget({
        forwarded_from_id: 0,
        forwarded_from_name: '',
        forwarded_from_profile_user_id: null,
        forwarded_from_profile_account_name: null,
        forwarded_from_highlight_accountant_user_id: null,
        forwarded_from_highlight_accountant_relation_display_name: null,
      }),
    ).toBeNull()
    expect(resolveForwardedProfileTarget(undefined)).toBeNull()
  })
})