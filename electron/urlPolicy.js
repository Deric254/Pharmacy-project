'use strict'

/**
 * Which URLs the main process is willing to act on. Everything here is
 * pure (no Electron imports) so it can be unit-tested with plain Node.
 *
 * The renderer is untrusted input as far as the main process is
 * concerned: anything that manages to run script in it (a bug, a
 * compromised dependency) can call the exposed IPC functions and open
 * windows. These checks are what keep that from turning into "download
 * and launch an attacker's installer" or "hand an arbitrary URL scheme
 * to the operating system".
 */

// Keep in step with REPO in frontend/src/lib/updateCheck.ts.
const RELEASE_REPO = 'Deric254/Pharmacy-project'
const INSTALLER_FILENAME = /^Pharmacy-ERP-Setup-.+\.exe$/i

function parse(value) {
  try {
    return new URL(value)
  } catch {
    return null
  }
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value)
  } catch {
    return ''
  }
}

/**
 * A release asset of this project's own GitHub repository, named like the
 * installer: https://github.com/<repo>/releases/download/<tag>/Pharmacy-ERP-Setup-*.exe
 *
 * allowLoopback is only ever passed for an unpackaged (development / e2e)
 * run, where the update source is a fake server on 127.0.0.1.
 */
function isTrustedInstallerUrl(value, { allowLoopback = false } = {}) {
  const url = parse(value)
  if (url === null || url.username !== '' || url.password !== '') return false

  if (allowLoopback && url.protocol === 'http:' && url.hostname === '127.0.0.1') return true

  if (url.protocol !== 'https:' || url.hostname !== 'github.com' || url.port !== '') return false

  const prefix = `/${RELEASE_REPO}/releases/download/`.toLowerCase()
  if (!url.pathname.toLowerCase().startsWith(prefix)) return false

  const [tag, file, ...rest] = url.pathname.slice(prefix.length).split('/')
  return tag !== '' && rest.length === 0 && INSTALLER_FILENAME.test(safeDecode(file ?? ''))
}

/** Links that may be handed to the operating system's default handler. */
function isSafeExternalUrl(value) {
  const url = parse(value)
  return url !== null && ['https:', 'http:', 'mailto:'].includes(url.protocol)
}

// The only places the licence pay window opens (see frontend/src/lib/payment.ts): the DericBI
// renewal page, PayPal, Gmail's compose window and an email to the owner.
const PAYMENT_LINK_PREFIXES = [
  'https://dericbi.vercel.app/renew.html?',
  'https://www.paypal.com/',
  'https://mail.google.com/mail/?view=cm',
  'mailto:dericmarangu@gmail.com?',
]

function isPaymentLink(value) {
  return (
    typeof value === 'string' &&
    value.length <= 4000 &&
    PAYMENT_LINK_PREFIXES.some((prefix) => value.startsWith(prefix))
  )
}

function isSameOrigin(value, baseUrl) {
  const url = parse(value)
  const base = parse(baseUrl)
  return url !== null && base !== null && url.origin === base.origin
}

module.exports = {
  INSTALLER_FILENAME,
  PAYMENT_LINK_PREFIXES,
  isTrustedInstallerUrl,
  isSafeExternalUrl,
  isPaymentLink,
  isSameOrigin,
}
