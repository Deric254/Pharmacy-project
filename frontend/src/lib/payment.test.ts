import { describe, expect, it } from 'vitest'
import { PAY, payReference, proofEmail } from './payment'

// Keep in step with PAYMENT_LINK_PREFIXES in electron/urlPolicy.js (the desktop app opens nothing else).
const ALLOWED_PREFIXES = [
  'https://dericbi.vercel.app/renew.html?',
  'https://www.paypal.com/',
  'https://mail.google.com/mail/?view=cm',
  'mailto:dericmarangu@gmail.com?',
]

describe('licence payment', () => {
  it('pays the owner at the right account, tag and amounts', () => {
    expect(PAY.email).toBe('dericmarangu@gmail.com')
    expect(PAY.tag).toBe('PHA')
    expect(PAY.app).toBe('Pharmacy ERP')
    expect(PAY.plans.M).toEqual({ name: 'Monthly (30 days)', amount: 100 })
    expect(PAY.plans.L).toEqual({ name: 'Lifetime', amount: 1000 })
  })

  it('builds the same reference for the same app, computer and plan', () => {
    expect(payReference('abcd-efgh-jklm', 'M')).toBe('LIC-PHA-ABCDEFGHJKLM-M')
    expect(payReference('ABCD-EFGH-JKLM', 'L')).toBe('LIC-PHA-ABCDEFGHJKLM-L')
  })

  it('writes the proof email with everything needed to issue the licence', () => {
    const { mailto, gmail } = proofEmail('ABCD-EFGH-JKLM', 'M')
    const url = new URL(mailto)
    const body = url.searchParams.get('body') ?? ''
    expect(url.searchParams.get('subject')).toBe(
      'Payment proof - Pharmacy ERP licence Monthly (30 days) - LIC-PHA-ABCDEFGHJKLM-M',
    )
    for (const needed of [
      'Amount: $100 USD',
      'Reference: LIC-PHA-ABCDEFGHJKLM-M',
      'Activation code: ABCD-EFGH-JKLM',
      'PayPal transaction ID: ',
      'My name: ',
      '(Please attach your PayPal screenshot before sending.)',
    ]) {
      expect(body).toContain(needed)
    }
    expect(new URL(gmail).searchParams.get('to')).toBe(PAY.email)
    expect(new URL(gmail).searchParams.get('body')).toBe(body)
  })

  it('only builds links the desktop app is willing to open', () => {
    const { mailto, gmail } = proofEmail('ABCD-EFGH-JKLM', 'L')
    for (const link of [PAY.paypalUrl, mailto, gmail]) {
      expect(ALLOWED_PREFIXES.some((p) => link.startsWith(p))).toBe(true)
    }
  })
})
