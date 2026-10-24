// The map (MapLibre, about 290 kB gzipped) is its own chunk, kept off the first screen's path.
// The module system keeps the one promise, so calling this again costs nothing.
export const loadMapView = () => import('../components/MapView')

// Runs callback once the first screen has settled: after the load event (photo and font in),
// when the browser is next idle. Returns a function that cancels it.
export function afterLoadWhenIdle(callback: () => void) {
  let cancelIdle = () => {}
  const start = () => {
    if ('requestIdleCallback' in window) {
      const id = requestIdleCallback(callback, { timeout: 3000 })
      cancelIdle = () => cancelIdleCallback(id)
    } else {
      const id = setTimeout(callback, 300) // Safari has no requestIdleCallback
      cancelIdle = () => clearTimeout(id)
    }
  }
  if (document.readyState === 'complete') start()
  else window.addEventListener('load', start, { once: true })
  return () => {
    window.removeEventListener('load', start)
    cancelIdle()
  }
}
