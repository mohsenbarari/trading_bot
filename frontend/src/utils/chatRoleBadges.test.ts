import { describe, expect, it } from 'vitest'

import { getChatRoleBadge } from './chatRoleBadges'

describe('chatRoleBadges', () => {
  it('uses a default customer badge even when the backend role label is empty', () => {
    expect(getChatRoleBadge({ chat_role_kind: 'customer', chat_role_label: null })).toEqual({
      label: 'مشتری',
      tone: 'creator',
    })
  })

  it('keeps accountant badges dependent on an explicit label', () => {
    expect(getChatRoleBadge({ chat_role_kind: 'accountant', chat_role_label: null })).toBeNull()
  })
})
