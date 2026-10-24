// A value shared between parts of the page without React state: set() calls the listeners
// directly, so nothing re-renders. The checkpoint under the pointer in the list goes through
// one, and the map rings it with a single setFeatureState call.
export interface Channel<T> {
  get: () => T
  set: (value: T) => void
  subscribe: (listener: (value: T) => void) => () => void
}

export function createChannel<T>(initial: T): Channel<T> {
  let value = initial
  const listeners = new Set<(value: T) => void>()
  return {
    get: () => value,
    set(next) {
      if (Object.is(next, value)) return
      value = next
      listeners.forEach((listener) => listener(next))
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

// Names one checkpoint across all routes, as the map's hover ring knows it: "r1:12"
export function checkpointKey(routeId: string, index: number) {
  return `${routeId}:${index}`
}
