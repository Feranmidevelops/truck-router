import { LngLatBounds, type Map as MapLibreMap } from 'maplibre-gl'
import type { Plan } from '../api/types'
import { checkpointKey, type Channel, type CheckpointRun } from '../lib/channel'

// Close enough to see the towns along a stretch
const STRETCH_MAX_ZOOM = 7

interface Options {
  source: string // the checkpoints keyed one by one, as checkpointKey() names them
  getPlan: () => Plan | null // the plan on the map now
  fit: (bounds: LngLatBounds, maxZoom?: number) => void // frames bounds clear of the panels
}

// A stretch picked in the journey report: its checkpoints get a ring (feature-state "focus")
// and the map frames them. Letting go of it frames the whole plan again, unless a new plan
// has replaced the one it was on (that plan frames itself). Returns the unsubscribe.
export function bindStretchFocus(map: MapLibreMap, channel: Channel<CheckpointRun | null>, opts: Options) {
  let shown: { plan: Plan; keys: string[] } | null = null
  const ring = (keys: string[], on: boolean) =>
    keys.forEach((id) => map.setFeatureState({ source: opts.source, id }, { focus: on }))

  const focus = (run: CheckpointRun | null) => {
    const plan = opts.getPlan()
    const before = shown
    if (before) ring(before.keys, false)
    shown = null
    const route = run && plan?.routes.find((r) => r.id === run.routeId)
    if (run && plan && route) {
      const points = route.checkpoints.slice(run.first, run.last + 1)
      shown = { plan, keys: points.map((c) => checkpointKey(route.id, c.index)) }
      ring(shown.keys, true)
      const bounds = new LngLatBounds()
      points.forEach((c) => bounds.extend([c.lon, c.lat]))
      opts.fit(bounds, STRETCH_MAX_ZOOM)
    } else if (before && before.plan === plan) {
      opts.fit(planBounds(plan))
    }
  }

  focus(channel.get())
  const unsubscribe = channel.subscribe(focus)
  return () => {
    unsubscribe()
    if (shown) ring(shown.keys, false)
  }
}

// The box round every route in the plan
export function planBounds(plan: Plan) {
  const bounds = new LngLatBounds()
  plan.routes.forEach((r) => r.geometry.forEach((c) => bounds.extend(c)))
  return bounds
}
