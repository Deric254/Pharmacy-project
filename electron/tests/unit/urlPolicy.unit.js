'use strict'

const test = require('node:test')
const assert = require('node:assert/strict')
const {
  INSTALLER_FILENAME,
  isTrustedInstallerUrl,
  isSafeExternalUrl,
  isSameOrigin,
} = require('../../urlPolicy')

const RELEASE = 'https://github.com/Deric254/Pharmacy-project/releases/download/v1.2.3'

test('a release installer from this repository is trusted', () => {
  assert.equal(isTrustedInstallerUrl(`${RELEASE}/Pharmacy-ERP-Setup-1.2.3.exe`), true)
})

test('anything else is refused: other hosts, repos, schemes, files and tricks', () => {
  const refused = [
    'https://evil.example/Deric254/Pharmacy-project/releases/download/v1/Pharmacy-ERP-Setup-1.exe',
    'https://github.com.evil.example/Deric254/Pharmacy-project/releases/download/v1/Pharmacy-ERP-Setup-1.exe',
    'https://github.com/attacker/Pharmacy-project/releases/download/v1/Pharmacy-ERP-Setup-1.exe',
    'http://github.com/Deric254/Pharmacy-project/releases/download/v1/Pharmacy-ERP-Setup-1.exe',
    'https://github.com:8443/Deric254/Pharmacy-project/releases/download/v1/Pharmacy-ERP-Setup-1.exe',
    'https://user:pass@github.com/Deric254/Pharmacy-project/releases/download/v1/Pharmacy-ERP-Setup-1.exe',
    `${RELEASE}/malware.exe`,
    `${RELEASE}/Pharmacy-ERP-Setup-1.exe.zip`,
    `${RELEASE}/nested/Pharmacy-ERP-Setup-1.exe`,
    'https://github.com/Deric254/Pharmacy-project/releases/latest',
    'https://github.com/Deric254/Pharmacy-project/archive/Pharmacy-ERP-Setup-1.exe',
    'file:///C:/Windows/System32/cmd.exe',
    'javascript:alert(1)',
    'not a url',
    '',
    undefined,
    null,
    42,
  ]
  for (const value of refused) {
    assert.equal(isTrustedInstallerUrl(value), false, `should refuse ${String(value)}`)
  }
})

test('a loopback update server is accepted only when explicitly allowed', () => {
  const local = 'http://127.0.0.1:5000/Pharmacy-ERP-Setup-9.9.9.exe'
  assert.equal(isTrustedInstallerUrl(local), false)
  assert.equal(isTrustedInstallerUrl(local, { allowLoopback: true }), true)
  assert.equal(
    isTrustedInstallerUrl('http://evil.example/Pharmacy-ERP-Setup-9.exe', { allowLoopback: true }),
    false,
  )
})

test('only web and mail links may be opened outside the app', () => {
  for (const ok of ['https://example.com/a', 'http://example.com', 'mailto:someone@example.com']) {
    assert.equal(isSafeExternalUrl(ok), true, ok)
  }
  for (const bad of [
    'file:///C:/Windows/System32/calc.exe',
    'ms-msdt:/id PCWDiagnostic',
    'search-ms:query=x',
    'javascript:alert(1)',
    'data:text/html,hi',
    'not a url',
    '',
  ]) {
    assert.equal(isSafeExternalUrl(bad), false, bad)
  }
})

test('same-origin comparison compares scheme, host and port', () => {
  const app = 'http://127.0.0.1:51234'
  assert.equal(isSameOrigin('http://127.0.0.1:51234/sales?x=1', app), true)
  assert.equal(isSameOrigin('http://127.0.0.1:9999/', app), false)
  assert.equal(isSameOrigin('https://127.0.0.1:51234/', app), false)
  assert.equal(isSameOrigin('http://evil.example:51234/', app), false)
  assert.equal(isSameOrigin('garbage', app), false)
})

test('the installer filename pattern matches only the installer', () => {
  assert.equal(INSTALLER_FILENAME.test('Pharmacy-ERP-Setup-1.2.3.exe'), true)
  assert.equal(INSTALLER_FILENAME.test('sales-report.xlsx'), false)
  assert.equal(INSTALLER_FILENAME.test('Pharmacy-ERP-Setup-1.2.3.exe.zip'), false)
})
