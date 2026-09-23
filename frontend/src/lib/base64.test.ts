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
    const bytes = [0x4d, 0x61, 0x6e] 
    expect(arrayBufferToBase64(toBuffer(bytes))).toBe('TWFu')
  })

  it('round-trips arbitrary binary data through atob', () => {
    const bytes = Array.from({ length: 1000 }, (_, i) => i % 256)
    const encoded = arrayBufferToBase64(toBuffer(bytes))
    const decoded = Array.from(atob(encoded), (c) => c.charCodeAt(0))
    expect(decoded).toEqual(bytes)
  })

  it('round-trips a buffer larger than one internal chunk (exercises the chunking loop itself, not just small inputs)', () => {
    const length = 0x8000 * 3 + 123
    const bytes = Array.from({ length }, (_, i) => (i * 7) % 256)
    const encoded = arrayBufferToBase64(toBuffer(bytes))
    const decoded = Array.from(atob(encoded), (c) => c.charCodeAt(0))
    expect(decoded).toEqual(bytes)
  })

  it('matches the output of the original byte-at-a-time implementation', () => {
    const bytes = Array.from({ length: 5000 }, (_, i) => (i * 31) % 256)
    const buffer = toBuffer(bytes)
    const slow = btoa(
      new Uint8Array(buffer).reduce((data, byte) => data + String.fromCharCode(byte), ''),
    )
    expect(arrayBufferToBase64(buffer)).toBe(slow)
  })
})
