import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'

// Without this, a component left mounted by one test's render() would
// still be in the DOM when the next test's render() runs, letting
// document.querySelector-style lookups in one test accidentally match
// an element left over from a previous one.
afterEach(() => {
  cleanup()
})
