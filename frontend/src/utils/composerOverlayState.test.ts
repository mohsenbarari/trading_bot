import { describe, expect, it } from 'vitest'
import {
  getMessengerComposerSurface,
  reduceMessengerOverlayState,
  type MessengerComposerSurfaceInput,
  type MessengerOverlayState,
} from './composerOverlayState'

function composerInput(overrides: Partial<MessengerComposerSurfaceInput> = {}): MessengerComposerSurfaceInput {
  return {
    text: '',
    isSelectionMode: false,
    selectedMessagesCount: 0,
    canDeleteSelected: false,
    canCopySelected: false,
    isDeleted: false,
    isReadOnly: false,
    readOnlyBannerText: undefined,
    isRecording: false,
    isEditing: false,
    disableRichComposer: false,
    allowVoiceRecording: true,
    ...overrides,
  }
}

function overlayState(overrides: Partial<MessengerOverlayState> = {}): MessengerOverlayState {
  return {
    attachmentOpen: false,
    stickerOpen: false,
    forwardOpen: false,
    searchActive: false,
    inChatSearchList: false,
    ...overrides,
  }
}

describe('composerOverlayState', () => {
  it('resolves composer surface modes without component state', () => {
    expect(getMessengerComposerSurface(composerInput({
      isSelectionMode: true,
      selectedMessagesCount: 1,
      canDeleteSelected: true,
      canCopySelected: true,
    }))).toMatchObject({
      mode: 'selection',
      showDeleteAction: true,
      showReplyAction: true,
      showCopyAction: true,
      showForwardAction: true,
      showVoiceButton: false,
    })

    expect(getMessengerComposerSurface(composerInput({ isDeleted: true }))).toMatchObject({
      mode: 'disabled',
      disabledText: 'امکان ارسال پیام به این کاربر وجود ندارد.',
      showEmojiButton: false,
    })

    expect(getMessengerComposerSurface(composerInput({ isReadOnly: true, readOnlyBannerText: 'فقط مدیران' }))).toMatchObject({
      mode: 'disabled',
      disabledText: 'فقط مدیران',
    })

    expect(getMessengerComposerSurface(composerInput({ isRecording: true }))).toMatchObject({
      mode: 'recording',
      showAttachmentButton: false,
      showEmojiButton: false,
    })
  })

  it('keeps send, voice, attachment, and edit affordances explicit', () => {
    expect(getMessengerComposerSurface(composerInput())).toMatchObject({
      mode: 'input',
      canSubmit: false,
      showVoiceButton: true,
      showAttachmentButton: true,
      showSendButton: false,
      showEmojiButton: true,
    })

    expect(getMessengerComposerSurface(composerInput({ text: '  سلام  ' }))).toMatchObject({
      mode: 'input',
      canSubmit: true,
      showVoiceButton: false,
      showAttachmentButton: true,
      showSendButton: true,
    })

    expect(getMessengerComposerSurface(composerInput({ isEditing: true }))).toMatchObject({
      mode: 'editing',
      canSubmit: false,
      showVoiceButton: false,
      showAttachmentButton: false,
      showSendButton: true,
    })

    expect(getMessengerComposerSurface(composerInput({ disableRichComposer: true, allowVoiceRecording: false }))).toMatchObject({
      showVoiceButton: false,
      showAttachmentButton: false,
      showEmojiButton: true,
    })
  })

  it('arbitrates mutually exclusive composer overlays', () => {
    expect(reduceMessengerOverlayState(overlayState({ stickerOpen: true }), { type: 'open_attachment', canOpen: true })).toMatchObject({
      attachmentOpen: true,
      stickerOpen: false,
    })

    expect(reduceMessengerOverlayState(overlayState({ attachmentOpen: true }), { type: 'open_sticker' })).toMatchObject({
      attachmentOpen: false,
      stickerOpen: true,
    })

    expect(reduceMessengerOverlayState(overlayState({ attachmentOpen: true, stickerOpen: true, forwardOpen: true }), { type: 'enter_selection' })).toMatchObject({
      attachmentOpen: false,
      stickerOpen: false,
      forwardOpen: false,
    })

    expect(reduceMessengerOverlayState(overlayState({ attachmentOpen: true, stickerOpen: true, forwardOpen: true, searchActive: true, inChatSearchList: true }), { type: 'enter_reply' })).toMatchObject({
      attachmentOpen: false,
      stickerOpen: false,
      forwardOpen: false,
      searchActive: true,
      inChatSearchList: true,
    })

    expect(reduceMessengerOverlayState(overlayState({ attachmentOpen: true, stickerOpen: true, forwardOpen: true }), { type: 'enter_editing' })).toMatchObject({
      attachmentOpen: false,
      stickerOpen: false,
      forwardOpen: false,
    })

    expect(reduceMessengerOverlayState(overlayState({ attachmentOpen: true, stickerOpen: true, forwardOpen: true, searchActive: true, inChatSearchList: true }), { type: 'enter_conversation' })).toMatchObject({
      attachmentOpen: false,
      stickerOpen: false,
      forwardOpen: false,
    })

    expect(reduceMessengerOverlayState(overlayState({ attachmentOpen: true, stickerOpen: true, inChatSearchList: true }), { type: 'enter_search' })).toMatchObject({
      attachmentOpen: false,
      stickerOpen: false,
      searchActive: true,
      inChatSearchList: false,
    })

    expect(reduceMessengerOverlayState(overlayState({ attachmentOpen: true, stickerOpen: true, forwardOpen: true }), { type: 'close_composer_overlays' })).toMatchObject({
      attachmentOpen: false,
      stickerOpen: false,
      forwardOpen: false,
    })

    const blocked = overlayState({ stickerOpen: true })
    expect(reduceMessengerOverlayState(blocked, { type: 'toggle_attachment', canOpen: false })).toEqual(blocked)
  })
})