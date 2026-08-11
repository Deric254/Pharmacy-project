import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { App } from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary.tsx'

// React's Error Boundary only catches errors during rendering -- an
// error thrown in an event handler or a rejected promise that nobody
// awaited would otherwise fail completely silently, with no record
// anywhere. This at least guarantees it's visible in the console for
// anyone who reports "something felt off" even without a full crash.
window.addEventListener('error', (event) => {
  console.error('Uncaught error:', event.error)
})
window.addEventListener('unhandledrejection', (event) => {
  console.error('Unhandled promise rejection:', event.reason)
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
