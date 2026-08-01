import { create } from 'zustand'

interface ViewedRangeState {
  range: { start: string; end: string } | null
  setViewedRange: (range: { start: string; end: string } | null) => void
}

export const useViewedRangeStore = create<ViewedRangeState>((set) => ({
  range: null,
  setViewedRange: (range) => set({ range }),
}))
