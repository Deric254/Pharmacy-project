import { describe, expect, it } from 'vitest'

import { arrayBufferToBase64 } from './base64'

function toBuffer(bytes: number[]): ArrayBuffer {
  return new Uint8Array(bytes).buffer
}

describe('arrayBufferToBase64', () => {
  it('encodes an empty buffer as an empty string', () => {
    expect(arrayBufferToBase64(toBuffer([]))).toBe('')
  })

  it('matches a known base64 vector ("Man" -> "TWFu")', () => {
    // The textbook base64 test vector: three bytes encode to exactly
    // four base64 characters with no padding, which makes it a good
    // sanity check that byte order and grouping are correct, not just
    // that "some" base64 comes out.
    const bytes = [0x4d, 0x61, 0x6e] // 'M', 'a', 'n'
    expect(arrayBufferToBase64(toBuffer(bytes))).toBe('TWFu')
  })

  it('round-trips arbitrary binary data through atob', () => {
    const bytes = Array.from({ length: 1000 }, (_, i) => i % 256)
    const encoded = arrayBufferToBase64(toBuffer(bytes))
    const decoded = Array.from(atob(encoded), (c) => c.charCodeAt(0))
    expect(decoded).toEqual(bytes)
  })

  it('round-trips a buffer larger than one internal chunk (exercises the chunking loop itself, not just small inputs)', () => {
    // CHUNK_SIZE inside the implementation is 0x8000 (32768) -- this
    // buffer spans three full chunks plus a partial one, so a bug in
    // the chunk-boundary math (an off-by-one, a dropped tail chunk)
    // would show up here even though it wouldn't in a small buffer.
    const length = 0x8000 * 3 + 123
    const bytes = Array.from({ length }, (_, i) => (i * 7) % 256)
    const encoded = arrayBufferToBase64(toBuffer(bytes))
    const decoded = Array.from(atob(encoded), (c) => c.charCodeAt(0))
    expect(decoded).toEqual(bytes)
  })

  it('matches the output of the original byte-at-a-time implementation', () => {
    // Guards against the fast version being merely fast rather than
    // equivalent -- compares against the exact old algorithm this
    // replaces, not just against atob() round-tripping.
    const bytes = Array.from({ length: 5000 }, (_, i) => (i * 31) % 256)
    const buffer = toBuffer(bytes)
    const slow = btoa(
      new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), ''),
    )
    expect(arrayBufferToBase64(buffer)).toBe(slow)
  })
})
