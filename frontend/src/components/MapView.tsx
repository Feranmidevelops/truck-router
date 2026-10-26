import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'
import {
  LngLatBounds,
  Map as MapLibreMap,
  NavigationControl,
  Popup,
  type GeoJSONSource,
  type MapGeoJSONFeature,
  type PointLike,
} from 'maplibre-gl'
import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'
import 'maplibre-gl/dist/maplibre-gl.css'
import '../lib/maplibre'
import './MapView.css'
import type { FeatureCollection, LineString, Point } from 'geojson'
import type { Plan } from '../api/types'
import { checkpointKey, type Channel } from '../lib/channel'
import { NARROW_SCREEN } from '../lib/layout'
import type { MapWeather } from '../lib/liveWeather'
import { levelColorExpression } from '../lib/risk'
import { checkpointPopup } from './CheckpointPopup'
import { HeatmapSlider, type HeatmapState } from './HeatmapSlider'
import { HEAT_LAYER, addHeatmapLayer, setHeatmapData, showHeatmapHour } from './heatmapLayer'
import { LiveWeather } from './LiveWeather'
import { isLiveWeatherSource, useLiveWeather } from './liveWeatherLayer'

// OpenFreeMap is free and needs no key. Override with VITE_MAP_STYLE_URL if it's ever down.
const STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL || 'https://tiles.openfreemap.org/styles/liberty'
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }
// After a resize, wait this long for it to settle before fitting the routes again
const REFIT_DELAY_MS = 150
// Black and greys like the rest of the page, so the risk colours on the dots stand out
const ROUTE_COLOR = '#8b8d94'
const SELECTED_ROUTE_COLOR = '#111113'
const CASING_COLOR = '#ffffff'

// Selection lives in feature-state, so changing it restyles on the GPU without
// sending any geometry back to the map's worker.
const SELECTED: ExpressionSpecification = ['boolean', ['feature-state', 'selected'], false]
const HOVER_SOURCE = 'checkpoint-keys'
// Checkpoint dots shrink when zoomed out so they don't bury the route line.
const byZoom = (zoomedOut: Radius, zoomedIn: Radius): ExpressionSpecification => [
  'interpolate',
  ['linear'],
  ['zoom'],
  4,
  zoomedOut,
  7,
  zoomedIn,
]
type Radius = number | ExpressionSpecification

interface Props {
  plan: Plan | null
  loading: boolean // a new plan is on its way; any plan on the map is out of date
  selectedRouteId: string | null
  onSelectRoute: (id: string) => void
  heatmap: HeatmapState
  coverRef?: RefObject<HTMLElement | null> // the results panel or sheet floating over the map
  hoverChannel?: Channel<string | null> // the checkpoint hovered in the list, as checkpointKey()
}

export function MapView({ plan, loading, selectedRouteId, onSelectRoute, heatmap, coverRef, hoverChannel }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const onSelectRef = useRef(onSelectRoute)
  // The map's event handlers are bound once, so they read the latest props from refs.
  const planRef = useRef(plan)
  const selectedRef = useRef(selectedRouteId)
  const loadingRef = useRef(loading)
  const popupRef = useRef<Popup | null>(null)
  const panelRef = useRef<HTMLElement>(null)
  const stackRef = useRef<HTMLDivElement>(null)
  const [ready, setReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const errorDismissed = useRef(false)
  const [showHeat, setShowHeat] = useState(true)
  const heatData = heatmap.status === 'ready' ? heatmap.data : null
  const stale = loading && plan !== null

  // Map weather: live satellite while exploring; a plan's route risk once it lands. Never both:
  // radar green is light rain, risk green is Low.
  const planId = plan?.plan_id ?? null
  const [mapWeather, setMapWeather] = useState<MapWeather>(planId ? 'off' : 'satellite')
  const [weatherFor, setWeatherFor] = useState(planId)
  if (weatherFor !== planId) {
    setWeatherFor(planId)
    setMapWeather(planId ? 'off' : 'satellite')
  }
  const riskShown = mapWeather === 'off'
  const [liveRetry, setLiveRetry] = useState(0)
  // The live images load once the map is first on screen, not while it waits below the hero
  const [seen, setSeen] = useState(false)
  const liveStatus = useLiveWeather(mapRef, ready && seen, riskShown ? null : mapWeather, liveRetry)

  useEffect(() => {
    onSelectRef.current = onSelectRoute
    planRef.current = plan
    selectedRef.current = selectedRouteId
    loadingRef.current = loading
  }, [onSelectRoute, plan, selectedRouteId, loading])

  // Like the sidebar, the old plan stays on screen dimmed until the new one lands. Its routes
  // stop answering hovers and clicks (see bindCheckpointPopup); panning and zooming still work.
  useEffect(() => {
    if (loading) popupRef.current?.remove()
  }, [loading])

  // Create the map once. Sources and layers are added once; later updates only call setData.
  useEffect(() => {
    if (!containerRef.current) return
    // On narrow screens the map sits below the form in a scrolling page. Cooperative
    // gestures let one finger (or a plain mouse wheel) scroll the page past the map;
    // two fingers (or Ctrl + wheel) move the map.
    const narrow = window.matchMedia(NARROW_SCREEN)
    const map = new MapLibreMap({
      container: containerRef.current,
      style: STYLE_URL,
      center: [-98.5, 39.5],
      zoom: 3.6,
      cooperativeGestures: narrow.matches,
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    const onWidthChange = (e: MediaQueryListEvent) =>
      e.matches ? map.cooperativeGestures.enable() : map.cooperativeGestures.disable()
    narrow.addEventListener('change', onWidthChange)

    // focusAfterOpen off: the popup only shows information, so focus stays where it was.
    const popup = new Popup({
      className: 'checkpoint-popup',
      closeButton: false,
      closeOnClick: false,
      focusAfterOpen: false,
      maxWidth: '260px',
      offset: 12,
    })
    popupRef.current = popup

    // Without a listener MapLibre only logs to the console and the map stays blank.
    map.on('error', (e) => {
      console.error(e.error)
      // The live weather layers report their own failures, in their badge
      if (errorDismissed.current || ('sourceId' in e && isLiveWeatherSource(e.sourceId))) return
      setMapError(
        'sourceId' in e
          ? "Some map tiles didn't load. Check your connection."
          : "The map didn't load, but the route list still works.",
      )
    })

    map.on('load', () => {
      // promoteId lets setFeatureState address features by route id. Checkpoints
      // share their route's id, so one call restyles every checkpoint on a route.
      map.addSource('routes', { type: 'geojson', data: EMPTY, promoteId: 'id' })
      map.addSource('checkpoints', { type: 'geojson', data: EMPTY, promoteId: 'routeId' })
      // The same points keyed one by one ("r1:12"), for the ring round the one hovered in
      // the list. Selection keeps its one-call-per-route ids on the source above.
      map.addSource(HOVER_SOURCE, { type: 'geojson', data: EMPTY, promoteId: 'key' })

      addHeatmapLayer(map)
      // Invisible and wide, so a route is easy to hover and tap.
      map.addLayer({
        id: 'routes-hit',
        type: 'line',
        source: 'routes',
        paint: { 'line-color': '#000000', 'line-width': 18, 'line-opacity': 0 },
      })
      map.addLayer({
        id: 'routes-casing',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': CASING_COLOR, 'line-width': 7 },
      })
      map.addLayer({
        id: 'routes-line',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': ROUTE_COLOR, 'line-width': 4 },
      })
      // The selected route is drawn a second time, above the others, so it stays on
      // top where routes share a road. Only the selected one is visible here.
      map.addLayer({
        id: 'routes-selected-casing',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': CASING_COLOR, 'line-width': 10, 'line-opacity': ['case', SELECTED, 1, 0] },
      })
      map.addLayer({
        id: 'routes-selected',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': SELECTED_ROUTE_COLOR, 'line-width': 6, 'line-opacity': ['case', SELECTED, 1, 0] },
      })
      // Same idea for checkpoints: faded ones for the other routes, then the selected
      // route's on top.
      map.addLayer({
        id: 'checkpoints',
        type: 'circle',
        source: 'checkpoints',
        paint: {
          'circle-color': levelColorExpression,
          'circle-radius': byZoom(['case', SELECTED, 4, 3], ['case', SELECTED, 7, 5]),
          'circle-opacity': ['case', SELECTED, 0, 0.55],
          'circle-stroke-opacity': ['case', SELECTED, 0, 0.55],
          'circle-stroke-color': CASING_COLOR,
          'circle-stroke-width': 1.5,
        },
      })
      map.addLayer({
        id: 'checkpoints-selected',
        type: 'circle',
        source: 'checkpoints',
        paint: {
          'circle-color': levelColorExpression,
          'circle-radius': byZoom(4, 7),
          'circle-opacity': ['case', SELECTED, 1, 0],
          'circle-stroke-opacity': ['case', SELECTED, 1, 0],
          'circle-stroke-color': CASING_COLOR,
          'circle-stroke-width': 1.5,
        },
      })
      map.addLayer({
        id: 'checkpoints-hover',
        type: 'circle',
        source: HOVER_SOURCE,
        paint: {
          'circle-color': 'rgba(0, 0, 0, 0)',
          'circle-radius': byZoom(9, 13),
          'circle-stroke-color': SELECTED_ROUTE_COLOR,
          'circle-stroke-width': 3,
          'circle-stroke-opacity': ['case', ['boolean', ['feature-state', 'hover'], false], 1, 0],
        },
      })

      bindCheckpointPopup(map, popup, planRef, selectedRef, onSelectRef, loadingRef)
      setReady(true)
    })

    mapRef.current = map
    return () => {
      narrow.removeEventListener('change', onWidthChange)
      popup.remove()
      map.remove()
      mapRef.current = null
      popupRef.current = null
      setReady(false)
    }
  }, [])

  // Geometry is uploaded only when the plan changes, never on selection.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    popupRef.current?.remove()
    const checkpoints = checkpointsGeoJSON(plan)
    ;(map.getSource('routes') as GeoJSONSource).setData(routesGeoJSON(plan))
    ;(map.getSource('checkpoints') as GeoJSONSource).setData(checkpoints)
    ;(map.getSource(HOVER_SOURCE) as GeoJSONSource).setData(checkpoints)
  }, [plan, ready])

  // A checkpoint hovered or focused in the list gets a ring. The list talks to the map
  // through the channel, so a hover is one setFeatureState here and no React render.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !hoverChannel) return
    let ringed: string | null = null
    const ring = (key: string | null) => {
      if (ringed) map.setFeatureState({ source: HOVER_SOURCE, id: ringed }, { hover: false })
      if (key) map.setFeatureState({ source: HOVER_SOURCE, id: key }, { hover: true })
      ringed = key
    }
    ring(hoverChannel.get())
    const unsubscribe = hoverChannel.subscribe(ring)
    return () => {
      unsubscribe()
      ring(null)
    }
  }, [hoverChannel, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !plan) return
    for (const r of plan.routes) {
      const state = { selected: r.id === selectedRouteId }
      map.setFeatureState({ source: 'routes', id: r.id }, state)
      map.setFeatureState({ source: 'checkpoints', id: r.id }, state)
    }
  }, [plan, selectedRouteId, ready])

  useEffect(() => {
    const map = mapRef.current
    if (map && ready) setHeatmapData(map, heatData)
  }, [heatData, ready])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    map.setLayoutProperty(HEAT_LAYER, 'visibility', showHeat && riskShown ? 'visible' : 'none')
  }, [showHeat, riskShown, ready])

  useEffect(() => {
    const el = containerRef.current
    if (seen || !el) return
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setSeen(true)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [seen])

  // Stable, so slider ticks never re-render the map component.
  const onHourChange = useCallback((hour: number) => {
    const map = mapRef.current
    if (map?.getLayer(HEAT_LAYER)) showHeatmapHour(map, hour)
  }, [])
  const toggleHeat = useCallback(() => setShowHeat((v) => !v), [])
  const chooseWeather = useCallback((choice: MapWeather) => {
    setMapWeather(choice)
    if (choice === 'off') setShowHeat(true) // "Route risk" brings the shading back
  }, [])
  const retryLive = useCallback(() => setLiveRetry((n) => n + 1), [])

  // Fit the routes into the part of the map nothing floats over, and again whenever the map
  // changes size (a window resize, a phone turned sideways)
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready || !plan) return
    const bounds = new LngLatBounds()
    plan.routes.forEach((r) => r.geometry.forEach((c) => bounds.extend(c)))
    const fit = (duration: number) =>
      map.fitBounds(bounds, {
        padding: fitPadding(map, [panelRef.current, stackRef.current, coverRef?.current]),
        duration,
      })
    fit(600)

    let timer: ReturnType<typeof setTimeout> | undefined
    let first = true // the observer reports the current size straight away; that's not a resize
    const observer = new ResizeObserver(() => {
      if (first) first = false
      else {
        clearTimeout(timer)
        timer = setTimeout(() => fit(0), REFIT_DELAY_MS)
      }
    })
    observer.observe(map.getContainer())
    return () => {
      observer.disconnect()
      clearTimeout(timer)
    }
  }, [plan, ready, coverRef])

  return (
    <div className={stale ? 'map-wrap stale' : 'map-wrap'}>
      <div ref={containerRef} className="map" />
      <div ref={stackRef} className="map-stack">
        <LiveWeather
          choice={mapWeather}
          onChoose={chooseWeather}
          hasPlan={plan !== null}
          status={liveStatus}
          timeZone={plan?.input.timezone}
          onRetry={retryLive}
        />
        {mapError && (
          <div className="map-notice" role="status">
            <span>{mapError}</span>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => {
                errorDismissed.current = true
                setMapError(null)
              }}
            >
              ×
            </button>
          </div>
        )}
      </div>
      {/* Hidden, not removed, under a live layer, so the chosen forecast hour survives */}
      <div className="heat-holder" hidden={!riskShown}>
        <HeatmapSlider
          ref={panelRef}
          state={heatmap}
          timeZone={plan?.input.timezone}
          inert={stale}
          visible={showHeat}
          onToggle={toggleHeat}
          onHourChange={onHourChange}
        />
      </div>
    </div>
  )
}

type Edge = 'top' | 'right' | 'bottom' | 'left'
const FIT_MARGIN = 40 // px between the routes and the map's edge, or a panel's
const MIN_FIT_PX = 120 // the routes always get at least this much width and height

// Padding that keeps the routes clear of the panels floating over the map. Each panel counts
// against one edge: a tall one against the left or right, a wide one against the top or
// bottom, whichever side it reaches in from the least.
function fitPadding(map: MapLibreMap, covers: Array<HTMLElement | null | undefined>) {
  const box = map.getContainer().getBoundingClientRect()
  const pad: Record<Edge, number> = { top: FIT_MARGIN, right: FIT_MARGIN, bottom: FIT_MARGIN, left: FIT_MARGIN }
  for (const el of covers) {
    const r = el?.getBoundingClientRect()
    if (!r || !r.width || !r.height) continue
    const reach: Record<Edge, number> = {
      top: r.bottom - box.top,
      bottom: box.bottom - r.top,
      left: r.right - box.left,
      right: box.right - r.left,
    }
    const [a, b]: Edge[] = r.height > r.width ? ['left', 'right'] : ['top', 'bottom']
    const edge = reach[a] <= reach[b] ? a : b
    pad[edge] = Math.max(pad[edge], reach[edge] + FIT_MARGIN)
  }
  // On a small map the panels can leave no room at all; fitBounds would refuse to fit
  shrinkToFit(pad, 'left', 'right', box.width)
  shrinkToFit(pad, 'top', 'bottom', box.height)
  return pad
}

function shrinkToFit(pad: Record<Edge, number>, a: Edge, b: Edge, size: number) {
  const room = size - MIN_FIT_PX
  const total = pad[a] + pad[b]
  if (total <= room) return
  const scale = Math.max(0, room) / total
  pad[a] *= scale
  pad[b] *= scale
}

// Hover shows a checkpoint's details; a click or tap pins them and selects its route.
// A click on a bare route line selects it; a click on empty map closes the popup.
function bindCheckpointPopup(
  map: MapLibreMap,
  popup: Popup,
  planRef: { current: Plan | null },
  selectedRef: { current: string | null },
  onSelectRef: { current: (id: string) => void },
  loadingRef: { current: boolean },
) {
  let shown: string | null = null
  let pinned = false
  popup.on('close', () => {
    shown = null
    pinned = false
  })

  // Nothing to hit while a new plan loads: the routes on screen are the old plan's
  const hitsAt = (point: PointLike) =>
    loadingRef.current ? [] : map.queryRenderedFeatures(point, { layers: ['checkpoints', 'routes-hit'] })

  // Where checkpoints overlap, prefer the selected route's, since that's the one drawn on top.
  const checkpointIn = (hits: MapGeoJSONFeature[]) => {
    const dots = hits.filter((f) => f.layer.id === 'checkpoints')
    return dots.find((f) => f.properties.routeId === selectedRef.current) ?? dots[0]
  }

  const show = (feature: MapGeoJSONFeature, pin: boolean) => {
    const { routeId, index } = feature.properties
    const key = `${routeId}:${index}`
    pinned = pin
    if (key === shown && popup.isOpen()) return
    const plan = planRef.current
    const route = plan?.routes.find((r) => r.id === routeId)
    const cp = route?.checkpoints.find((c) => c.index === index)
    if (!plan || !route || !cp) return
    popup.setLngLat([cp.lon, cp.lat]).setDOMContent(checkpointPopup(route, cp, plan.input.timezone))
    if (!popup.isOpen()) popup.addTo(map)
    shown = key
  }

  map.on('mousemove', (e) => {
    const hits = hitsAt(e.point)
    map.getCanvas().style.cursor = hits.length ? 'pointer' : ''
    const dot = checkpointIn(hits)
    if (dot) show(dot, pinned && shown === `${dot.properties.routeId}:${dot.properties.index}`)
    else if (!pinned) popup.remove()
  })
  map.on('mouseout', () => {
    if (!pinned) popup.remove()
  })
  map.on('click', (e) => {
    const hits = hitsAt(e.point)
    const dot = checkpointIn(hits)
    if (dot) show(dot, true)
    else popup.remove()
    // On a shared stretch of road the selected route is the one on top, so keep it.
    const lines: unknown[] = hits.filter((f) => f.layer.id === 'routes-hit').map((f) => f.properties.id)
    const routeId = dot ? dot.properties.routeId : lines.includes(selectedRef.current) ? null : lines[0]
    if (typeof routeId === 'string' && routeId !== selectedRef.current) onSelectRef.current(routeId)
  })
}

function routesGeoJSON(plan: Plan | null): FeatureCollection<LineString> {
  if (!plan) return { type: 'FeatureCollection', features: [] }
  return {
    type: 'FeatureCollection',
    features: plan.routes.map((r) => ({
      type: 'Feature',
      properties: { id: r.id },
      geometry: { type: 'LineString', coordinates: r.geometry },
    })),
  }
}

function checkpointsGeoJSON(plan: Plan | null): FeatureCollection<Point> {
  if (!plan) return { type: 'FeatureCollection', features: [] }
  return {
    type: 'FeatureCollection',
    features: plan.routes.flatMap((r) =>
      r.checkpoints.map((c) => ({
        type: 'Feature' as const,
        properties: { routeId: r.id, index: c.index, level: c.risk.level, key: checkpointKey(r.id, c.index) },
        geometry: { type: 'Point' as const, coordinates: [c.lon, c.lat] },
      })),
    ),
  }
}
