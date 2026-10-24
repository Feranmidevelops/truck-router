import { useSyncExternalStore } from 'react'

// Below this width the results sit in a bottom sheet over the map instead of a side panel,
// and the map takes two fingers to move. Same value as the max-width: 899px media queries.
export const NARROW_SCREEN = '(max-width: 899px)'

// Re-renders when the media query starts or stops matching
export function useMediaQuery(query: string) {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query)
      list.addEventListener('change', onChange)
      return () => list.removeEventListener('change', onChange)
    },
    () => window.matchMedia(query).matches,
  )
}

export function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

// Scrolls an element into view, to the top of the window or its middle: smoothly, unless the
// visitor asked for less motion
export function scrollToElement(el: Element | null, block: 'start' | 'center' = 'start') {
  el?.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block })
}
