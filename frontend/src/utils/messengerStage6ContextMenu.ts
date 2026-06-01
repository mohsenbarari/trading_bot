export type MessengerContextMenuActionKey =
  | 'reply'
  | 'forward'
  | 'save-media'
  | 'save-album'
  | 'share'
  | 'share-album'
  | 'copy'
  | 'edit'
  | 'pin-message'
  | 'delete'

export type MessengerContextMenuTone = 'default' | 'warning' | 'danger'

export interface MessengerContextMenuActionDescriptor {
  key: MessengerContextMenuActionKey
  label: string
  tone: MessengerContextMenuTone
}

export interface MessengerContextMenuSectionDescriptor {
  key: 'primary' | 'media' | 'communication' | 'danger'
  label: string
  tone: MessengerContextMenuTone
  items: MessengerContextMenuActionDescriptor[]
}

export interface BuildMessengerContextMenuModelInput {
  messageType?: string | null
  isAlbumSelection: boolean
  supportsFileShare: boolean
  canEdit: boolean
  canDelete: boolean
  canPin: boolean
  isPinnedMessage: boolean
  showReactionRow: boolean
  hasOverflowReactions: boolean
  isReactionPickerExpanded: boolean
}

export interface MessengerContextMenuModel {
  sections: MessengerContextMenuSectionDescriptor[]
  actionCount: number
  sectionCount: number
  dividerCount: number
  menuWidth: number
  menuHeight: number
}

export interface MessengerContextMenuStyleInput {
  x: number
  y: number
  menuWidth: number
  menuHeight: number
  viewportWidth: number
  viewportHeight: number
  edgePadding?: number
}

function isShareableMessageType(messageType?: string | null) {
  return messageType === 'image' || messageType === 'video' || messageType === 'voice' || messageType === 'document'
}

export function buildMessengerContextMenuModel(
  input: BuildMessengerContextMenuModelInput,
): MessengerContextMenuModel {
  const sections: MessengerContextMenuSectionDescriptor[] = []

  sections.push({
    key: 'primary',
    label: 'اقدام اصلی',
    tone: 'default',
    items: [
      { key: 'reply', label: 'پاسخ', tone: 'default' },
      {
        key: 'forward',
        label: input.isAlbumSelection ? 'هدایت آلبوم' : 'هدایت پیام',
        tone: 'default',
      },
    ],
  })

  const mediaItems: MessengerContextMenuActionDescriptor[] = []
  if (input.isAlbumSelection) {
    mediaItems.push({ key: 'save-album', label: 'دانلود آلبوم', tone: 'default' })
    if (input.supportsFileShare) {
      mediaItems.push({ key: 'share-album', label: 'اشتراک‌گذاری آلبوم', tone: 'default' })
    }
  } else {
    if (input.messageType === 'image' || input.messageType === 'video') {
      mediaItems.push({ key: 'save-media', label: 'ذخیره در گالری', tone: 'default' })
    }
    if (input.supportsFileShare && isShareableMessageType(input.messageType)) {
      mediaItems.push({ key: 'share', label: 'اشتراک‌گذاری', tone: 'default' })
    }
  }
  if (mediaItems.length > 0) {
    sections.push({
      key: 'media',
      label: 'رسانه و فایل',
      tone: 'default',
      items: mediaItems,
    })
  }

  const communicationItems: MessengerContextMenuActionDescriptor[] = []
  if (input.messageType === 'text') {
    communicationItems.push({ key: 'copy', label: 'کپی کردن', tone: 'default' })
  }
  if (input.canEdit) {
    communicationItems.push({ key: 'edit', label: 'ویرایش', tone: 'default' })
  }
  if (input.canPin && !input.isAlbumSelection) {
    communicationItems.push({
      key: 'pin-message',
      label: input.isPinnedMessage ? 'برداشتن پیام سنجاق‌شده' : 'سنجاق کردن پیام',
      tone: 'warning',
    })
  }
  if (communicationItems.length > 0) {
    sections.push({
      key: 'communication',
      label: 'ارتباط و پیام',
      tone: 'default',
      items: communicationItems,
    })
  }

  if (input.canDelete) {
    sections.push({
      key: 'danger',
      label: 'اقدام حساس',
      tone: 'danger',
      items: [{ key: 'delete', label: 'حذف', tone: 'danger' }],
    })
  }

  const actionCount = sections.reduce((total, section) => total + section.items.length, 0)
  const sectionCount = sections.length
  const dividerCount = Math.max(sectionCount - 1, 0)
  const actionPanelHeight = actionCount * 44 + sectionCount * 22 + dividerCount * 9 + 16
  const reactionSectionHeight = input.showReactionRow
    ? input.hasOverflowReactions
      ? (input.isReactionPickerExpanded ? 232 : 102)
      : 72
    : 0

  return {
    sections,
    actionCount,
    sectionCount,
    dividerCount,
    menuWidth: input.showReactionRow ? 296 : 220,
    menuHeight: actionPanelHeight + (input.showReactionRow ? reactionSectionHeight + 6 : 0),
  }
}

export function getMessengerContextMenuStyle(input: MessengerContextMenuStyleInput) {
  const edgePadding = input.edgePadding ?? 8
  const boundedMenuWidth = Math.min(input.menuWidth, input.viewportWidth - edgePadding * 2)

  let x = input.x
  let y = input.y

  if (x + boundedMenuWidth > input.viewportWidth - edgePadding) {
    x = input.viewportWidth - boundedMenuWidth - edgePadding
  }
  if (x < edgePadding) {
    x = edgePadding
  }
  if (y + input.menuHeight > input.viewportHeight - edgePadding) {
    y = input.viewportHeight - input.menuHeight - edgePadding
  }
  if (y < edgePadding) {
    y = edgePadding
  }

  return {
    top: `${y}px`,
    left: `${x}px`,
    width: `${boundedMenuWidth}px`,
  }
}