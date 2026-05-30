export type MessengerComposerMode = 'selection' | 'disabled' | 'recording' | 'editing' | 'input'

export interface MessengerComposerSurfaceInput {
  text: string
  isSelectionMode: boolean
  selectedMessagesCount: number
  canDeleteSelected: boolean
  canCopySelected: boolean
  isDeleted?: boolean
  isReadOnly?: boolean
  readOnlyBannerText?: string
  isRecording: boolean
  isEditing: boolean
  disableRichComposer?: boolean
  allowVoiceRecording?: boolean
}

export interface MessengerComposerSurfaceState {
  mode: MessengerComposerMode
  canSubmit: boolean
  disabledText: string
  showDeleteAction: boolean
  showReplyAction: boolean
  showCopyAction: boolean
  showForwardAction: boolean
  showVoiceButton: boolean
  showAttachmentButton: boolean
  showSendButton: boolean
  showEmojiButton: boolean
}

export interface MessengerOverlayState {
  attachmentOpen: boolean
  stickerOpen: boolean
  forwardOpen: boolean
  searchActive: boolean
  inChatSearchList: boolean
}

export type MessengerOverlayAction =
  | { type: 'toggle_attachment'; canOpen: boolean }
  | { type: 'open_attachment'; canOpen: boolean }
  | { type: 'close_attachment' }
  | { type: 'open_sticker' }
  | { type: 'close_sticker' }
  | { type: 'open_forward' }
  | { type: 'close_forward' }
  | { type: 'enter_selection' }
  | { type: 'enter_search' }
  | { type: 'close_search' }
  | { type: 'close_composer_overlays' }

export function getMessengerComposerSurface(input: MessengerComposerSurfaceInput): MessengerComposerSurfaceState {
  const hasText = input.text.trim().length > 0
  const richComposerEnabled = input.disableRichComposer !== true
  const voiceRecordingAllowed = input.allowVoiceRecording !== false
  const isDisabled = input.isDeleted === true || input.isReadOnly === true
  const disabledText = input.isDeleted === true
    ? 'امکان ارسال پیام به این کاربر وجود ندارد.'
    : input.readOnlyBannerText || 'ارسال پیام در این فضا غیرفعال است.'

  if (input.isSelectionMode) {
    return {
      mode: 'selection',
      canSubmit: false,
      disabledText,
      showDeleteAction: input.canDeleteSelected,
      showReplyAction: input.selectedMessagesCount === 1,
      showCopyAction: input.canCopySelected,
      showForwardAction: true,
      showVoiceButton: false,
      showAttachmentButton: false,
      showSendButton: false,
      showEmojiButton: false,
    }
  }

  if (isDisabled) {
    return {
      mode: 'disabled',
      canSubmit: false,
      disabledText,
      showDeleteAction: false,
      showReplyAction: false,
      showCopyAction: false,
      showForwardAction: false,
      showVoiceButton: false,
      showAttachmentButton: false,
      showSendButton: false,
      showEmojiButton: false,
    }
  }

  if (input.isRecording) {
    return {
      mode: 'recording',
      canSubmit: false,
      disabledText,
      showDeleteAction: false,
      showReplyAction: false,
      showCopyAction: false,
      showForwardAction: false,
      showVoiceButton: false,
      showAttachmentButton: false,
      showSendButton: false,
      showEmojiButton: false,
    }
  }

  return {
    mode: input.isEditing ? 'editing' : 'input',
    canSubmit: hasText,
    disabledText,
    showDeleteAction: false,
    showReplyAction: false,
    showCopyAction: false,
    showForwardAction: false,
    showVoiceButton: !hasText && !input.isEditing && richComposerEnabled && voiceRecordingAllowed,
    showAttachmentButton: !input.isEditing && richComposerEnabled,
    showSendButton: hasText || input.isEditing,
    showEmojiButton: true,
  }
}

export function reduceMessengerOverlayState(
  state: MessengerOverlayState,
  action: MessengerOverlayAction,
): MessengerOverlayState {
  switch (action.type) {
    case 'toggle_attachment': {
      if (!action.canOpen) return state
      return {
        ...state,
        attachmentOpen: !state.attachmentOpen,
        stickerOpen: false,
      }
    }
    case 'open_attachment':
      if (!action.canOpen) return state
      return { ...state, attachmentOpen: true, stickerOpen: false }
    case 'close_attachment':
      return { ...state, attachmentOpen: false }
    case 'open_sticker':
      return { ...state, stickerOpen: true, attachmentOpen: false }
    case 'close_sticker':
      return { ...state, stickerOpen: false }
    case 'open_forward':
      return { ...state, forwardOpen: true, attachmentOpen: false, stickerOpen: false }
    case 'close_forward':
      return { ...state, forwardOpen: false }
    case 'enter_selection':
      return { ...state, attachmentOpen: false, stickerOpen: false, forwardOpen: false }
    case 'enter_search':
      return { ...state, searchActive: true, inChatSearchList: false, attachmentOpen: false, stickerOpen: false, forwardOpen: false }
    case 'close_search':
      return { ...state, searchActive: false, inChatSearchList: false }
    case 'close_composer_overlays':
      return { ...state, attachmentOpen: false, stickerOpen: false }
    default:
      return state
  }
}