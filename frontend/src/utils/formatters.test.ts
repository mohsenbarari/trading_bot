import { describe, expect, it } from 'vitest'
import { cleanDeletedSuffixes } from './formatters'

describe('cleanDeletedSuffixes', () => {
  it('cleans deleted suffixes inside nested arrays and leaves unrelated values untouched', () => {
    expect(cleanDeletedSuffixes([
      { account_name: 'ali_del_12', nested: { receiver_name: 'reza_del_2', note: 'keep' } },
      'plain',
    ])).toEqual([
      { account_name: 'ali', nested: { receiver_name: 'reza', note: 'keep' } },
      'plain',
    ])
  })
})