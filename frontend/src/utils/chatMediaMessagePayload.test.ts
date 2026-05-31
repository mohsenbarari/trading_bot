import { describe, expect, it } from 'vitest'

import { buildChatMediaMessagePayload, serializeChatMediaMessagePayload } from './chatMediaMessagePayload'

describe('chatMediaMessagePayload', () => {
  it('builds image and video preview payloads with album, caption, and explicit empty thumbnails', () => {
    expect(buildChatMediaMessagePayload({
      phase: 'preview',
      msgType: 'video',
      thumbnail: '',
      width: 1280,
      height: 720,
      durationMs: 2450,
      albumId: 'album-7',
      albumIndex: 3,
      caption: 'preview caption',
    })).toEqual({
      placeholder: true,
      thumbnail: '',
      width: 1280,
      height: 720,
      album_id: 'album-7',
      album_index: 3,
      caption: 'preview caption',
      durationMs: 2450,
    })
  })

  it('strips non-voice fields from voice previews while preserving duration', () => {
    expect(JSON.parse(serializeChatMediaMessagePayload({
      phase: 'preview',
      msgType: 'voice',
      thumbnail: 'ignored',
      width: 500,
      height: 250,
      durationMs: 1900,
      albumId: 'album-voice',
      albumIndex: 9,
      caption: 'ignored caption',
    }))).toEqual({
      placeholder: true,
      durationMs: 1900,
    })
  })

  it('builds final document payloads without media-only metadata', () => {
    expect(buildChatMediaMessagePayload({
      phase: 'final',
      msgType: 'document',
      fileId: 'file-22',
      fileName: 'report.pdf',
      mimeType: 'application/pdf',
      fileSize: 4096,
      thumbnail: 'ignored',
      durationMs: 1200,
      albumId: 'album-doc',
      caption: 'ignored caption',
    })).toEqual({
      file_id: 'file-22',
      file_name: 'report.pdf',
      mime_type: 'application/pdf',
      size: 4096,
    })
  })

  it('prefers the server thumbnail for final video payloads', () => {
    expect(buildChatMediaMessagePayload({
      phase: 'final',
      msgType: 'video',
      fileId: 'video-9',
      thumbnail: 'local-thumb',
      serverThumbnail: 'server-thumb',
      width: 1920,
      height: 1080,
      durationMs: 4000,
    })).toEqual({
      file_id: 'video-9',
      thumbnail: 'server-thumb',
      width: 1920,
      height: 1080,
      durationMs: 4000,
    })
  })
})