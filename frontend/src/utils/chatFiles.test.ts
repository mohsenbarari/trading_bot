import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildChatFileUrl, getAvatarInitial, uploadAvatarImage } from './chatFiles'

describe('chatFiles utilities', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => {
    localStorage.clear()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('builds encoded chat file URLs only when both file id and auth token exist', () => {
    expect(buildChatFileUrl('', 'https://api.example.com')).toBe('')

    localStorage.setItem('auth_token', 'token with/slash')
    expect(buildChatFileUrl('folder/file id', 'https://api.example.com')).toBe(
      'https://api.example.com/api/chat/files/folder%2Ffile%20id?token=token%20with%2Fslash',
    )
  })

  it('returns an empty file URL when running without a browser window', async () => {
    vi.stubGlobal('window', undefined)
    expect(buildChatFileUrl('abc123', 'https://api.example.com')).toBe('')
  })

  it('derives avatar initials from trimmed names and falls back to a question mark', () => {
    expect(getAvatarInitial('  ali ')).toBe('A')
    expect(getAvatarInitial('')).toBe('?')
    expect(getAvatarInitial(null)).toBe('?')
  })

  it('uploads avatar images with the auth token and returns the uploaded file payload', async () => {
    const fetchMock = vi.mocked(fetch)
    localStorage.setItem('auth_token', 'token-123')
    const file = new File(['image'], 'avatar.png', { type: 'image/png' })

    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          file_id: 'file-1',
          file_name: 'avatar.png',
          mime_type: 'image/png',
          size: 5,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        },
      ),
    )

    const result = await uploadAvatarImage(file, 'https://api.example.com')

    expect(result).toEqual({
      file_id: 'file-1',
      file_name: 'avatar.png',
      mime_type: 'image/png',
      size: 5,
    })

    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('https://api.example.com/api/chat/upload-media')
    expect((init as RequestInit).headers).toEqual({ Authorization: 'Bearer token-123' })
    expect((init as RequestInit).method).toBe('POST')
    const formData = (init as RequestInit).body as FormData
    expect(formData.get('file')).toBe(file)
  })

  it('rejects invalid avatar uploads and surfaces server errors', async () => {
    const fetchMock = vi.mocked(fetch)
    const imageFile = new File(['image'], 'avatar.png', { type: 'image/png' })

    await expect(uploadAvatarImage(new File(['text'], 'avatar.txt', { type: 'text/plain' }))).rejects.toThrow(
      'فقط فایل تصویری برای آواتار قابل استفاده است.',
    )
    await expect(uploadAvatarImage(imageFile)).rejects.toThrow('نشست شما منقضی شده است. دوباره وارد شوید.')

    localStorage.setItem('auth_token', 'token-123')
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'bad upload' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await expect(uploadAvatarImage(imageFile)).rejects.toThrow('bad upload')

    fetchMock.mockResolvedValueOnce(new Response('not-json', { status: 500 }))
    await expect(uploadAvatarImage(imageFile)).rejects.toThrow('آپلود آواتار ناموفق بود.')
  })
})