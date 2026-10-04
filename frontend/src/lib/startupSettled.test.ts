import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { runAfterStartup } from './startupSettled'

const GRACE_MS = 15_000

function setReadyState(state: DocumentReadyState) {
  Object.defineProperty(document, 'readyState', { configurable: true, get: () => state })
}

describe('runAfterStartup', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    Reflect.deleteProperty(document, 'readyState')
  })

  it('waits out the grace period after the page has already loaded', () => {
    setReadyState('complete')
    const task = vi.fn()
    runAfterStartup(task)

    vi.advanceTimersByTime(GRACE_MS - 1)
    expect(task).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(task).toHaveBeenCalledTimes(1)
  })

  it('does not start the grace period until the load event fires', () => {
    setReadyState('loading')
    const task = vi.fn()
    runAfterStartup(task)

    vi.advanceTimersByTime(GRACE_MS * 2)
    expect(task).not.toHaveBeenCalled()

    window.dispatchEvent(new Event('load'))
    vi.advanceTimersByTime(GRACE_MS)
    expect(task).toHaveBeenCalledTimes(1)
  })

  it('never runs the task once cancelled', () => {
    setReadyState('complete')
    const task = vi.fn()
    const cancel = runAfterStartup(task)

    cancel()
    vi.advanceTimersByTime(GRACE_MS * 2)
    expect(task).not.toHaveBeenCalled()
  })

  it('never runs the task if cancelled before the load event', () => {
    setReadyState('loading')
    const task = vi.fn()
    const cancel = runAfterStartup(task)

    cancel()
    window.dispatchEvent(new Event('load'))
    vi.advanceTimersByTime(GRACE_MS * 2)
    expect(task).not.toHaveBeenCalled()
  })
})
