const STARTUP_GRACE_MS = 15_000

export function runAfterStartup(task: () => void): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined
  let cancelled = false

  const schedule = () => {
    timer = setTimeout(() => {
      if (!cancelled) task()
    }, STARTUP_GRACE_MS)
  }

  if (document.readyState === 'complete') {
    schedule()
  } else {
    window.addEventListener('load', schedule, { once: true })
  }

  return () => {
    cancelled = true
    window.removeEventListener('load', schedule)
    if (timer !== undefined) clearTimeout(timer)
  }
}
