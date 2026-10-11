// Guards the fix for the blank-window report: hardware acceleration must be
// switched off at the top level of main.js, before the app is ready (Electron
// ignores the call afterwards), and GPU/helper process crashes must be logged.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'main.js'), 'utf8')

test('hardware acceleration is disabled before the app can become ready', () => {
  const disable = source.indexOf('\napp.disableHardwareAcceleration()')
  const ready = source.indexOf('app.whenReady()')
  assert.ok(disable > -1, 'app.disableHardwareAcceleration() must be called at top level')
  assert.ok(ready > -1)
  assert.ok(disable < ready, 'it must run before app.whenReady()')
})

test('helper process crashes are written to desktop.log', () => {
  assert.match(source, /app\.on\('child-process-gone'/)
})
