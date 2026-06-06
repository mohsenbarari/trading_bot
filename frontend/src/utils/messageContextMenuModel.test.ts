import { describe, expect, it } from 'vitest'

import {
  buildMessengerContextMenuModel,
  getMessengerContextMenuStyle,
} from './messageContextMenuModel'

describe('messageContextMenuModel', () => {
  it('builds the expected text-message sections', () => {
    const model = buildMessengerContextMenuModel({
      messageType: 'text',
      isAlbumSelection: false,
      supportsFileShare: true,
      canEdit: true,
      canDelete: true,
      canPin: true,
      isPinnedMessage: false,
      showReactionRow: true,
      hasOverflowReactions: false,
      isReactionPickerExpanded: false,
    })

    expect(model.sections.map((section) => section.key)).toEqual([
      'primary',
      'communication',
      'danger',
    ])
    expect(model.sections[1]?.items.map((item) => item.key)).toEqual([
      'copy',
      'edit',
      'pin-message',
    ])
    expect(model.menuWidth).toBe(296)
    expect(model.menuHeight).toBeGreaterThan(0)
  })

  it('adds album media actions only when available', () => {
    const albumInput = {
      messageType: 'image',
      isAlbumSelection: true,
      supportsFileShare: true,
      canEdit: false,
      canDelete: false,
      canPin: false,
      canViewSeenList: true,
      isPinnedMessage: false,
      showReactionRow: false,
      hasOverflowReactions: false,
      isReactionPickerExpanded: false,
    } as const

    const withShare = buildMessengerContextMenuModel(albumInput)
    const withoutShare = buildMessengerContextMenuModel({
      ...albumInput,
      supportsFileShare: false,
    })

    expect(withShare.sections[1]?.items.map((item) => item.key)).toEqual([
      'save-album',
      'share-album',
    ])
    expect(withShare.sections[2]?.items.map((item) => item.key)).toEqual([
      'seen-list',
    ])
    expect(withoutShare.sections[1]?.items.map((item) => item.key)).toEqual([
      'save-album',
    ])
    expect(withoutShare.sections[2]?.items.map((item) => item.key)).toEqual([
      'seen-list',
    ])
  })

  it('uses expanded reaction height when overflow is open', () => {
    const baseInput = {
      messageType: 'image',
      isAlbumSelection: false,
      supportsFileShare: true,
      canEdit: false,
      canDelete: false,
      canPin: false,
      isPinnedMessage: false,
      showReactionRow: true,
      hasOverflowReactions: true,
      isReactionPickerExpanded: false,
    } as const

    const collapsed = buildMessengerContextMenuModel(baseInput)
    const expanded = buildMessengerContextMenuModel({
      ...baseInput,
      isReactionPickerExpanded: true,
    })

    expect(expanded.menuHeight).toBeGreaterThan(collapsed.menuHeight)
  })

  it('clamps menu coordinates within the viewport', () => {
    const style = getMessengerContextMenuStyle({
      x: 390,
      y: 770,
      menuWidth: 296,
      menuHeight: 240,
      viewportWidth: 400,
      viewportHeight: 800,
    })

    expect(style.left).toBe('96px')
    expect(style.top).toBe('552px')
    expect(style.width).toBe('296px')
  })
})
