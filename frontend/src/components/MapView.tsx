import { useEffect, useRef, useState } from 'react'
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
import { LEVELS, LEVEL_COLOR, LEVEL_LABEL, levelColorExpression } from '../lib/risk'
import { checkpointPopup } from './CheckpointPopup'

// OpenFreeMap is free and needs no key. Override with VITE_MAP_STYLE_URL if it's ever down.
const STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL || 'https://tiles.openfreemap.org/styles/liberty'
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }
// Matches the single-column breakpoint in index.css.
const NARROW_SCREEN = '(max-width: 800px)'

// Selection lives in feature-state, so changing it restyles on the GPU without
// sending any geometry back to the map's worker.
const SELECTED: ExpressionSpecification = ['boolean', ['feature-state', 'selected'], false]
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
  selectedRouteId: string | null
  onSelectRoute: (id: string) => void
}

export function MapView({ plan, selectedRouteId, onSelectRoute }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const onSelectRef = useRef(onSelectRoute)
  // The map's event handlers are bound once, so they read the latest props from refs.
  const planRef = useRef(plan)
  const selectedRef = useRef(selectedRouteId)
  const popupRef = useRef<Popup | null>(null)
  const [ready, setReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const errorDismissed = useRef(false)

  useEffect(() => {
    onSelectRef.current = onSelectRoute
    planRef.current = plan
    selectedRef.current = selectedRouteId
  }, [onSelectRoute, plan, selectedRouteId])

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
      if (errorDismissed.current) return
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

      // TODO(day 3): add the heatmap source and layer here, before the route layers,
      // so routes draw on top.

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
        paint: { 'line-color': '#ffffff', 'line-width': 7 },
      })
      map.addLayer({
        id: 'routes-line',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#7d8aa0', 'line-width': 4 },
      })
      // The selected route is drawn a second time, above the others, so it stays on
      // top where routes share a road. Only the selected one is visible here.
      map.addLayer({
        id: 'routes-selected-casing',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#ffffff', 'line-width': 10, 'line-opacity': ['case', SELECTED, 1, 0] },
      })
      map.addLayer({
        id: 'routes-selected',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#1f5fbf', 'line-width': 6, 'line-opacity': ['case', SELECTED, 1, 0] },
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
          'circle-stroke-color': '#ffffff',
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
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
        },
      })

      bindCheckpointPopup(map, popup, planRef, selectedRef, onSelectRef)
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
    ;(map.getSource('routes') as GeoJSONSource).setData(routesGeoJSON(plan))
    ;(map.getSource('checkpoints') as GeoJSONSource).setData(checkpointsGeoJSON(plan))
  }, [plan, ready])

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
    if (!map || !ready || !plan) return
    const bounds = new LngLatBounds()
    plan.routes.forEach((r) => r.geometry.forEach((c) => bounds.extend(c)))
    map.fitBounds(bounds, { padding: 60, duration: 600 })
  }, [plan, ready])

  return (
    <div className="map-wrap">
      <div ref={containerRef} className="map" />
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
      <ul className="legend" aria-label="Risk levels">
        {LEVELS.map((l) => (
          <li key={l}>
            <span className="swatch" style={{ background: LEVEL_COLOR[l] }} />
            {LEVEL_LABEL[l]}
          </li>
        ))}
      </ul>
    </div>
  )
}

// Hover shows a checkpoint's details; a click or tap pins them and selects its route.
// A click on a bare route line selects it; a click on empty map closes the popup.
function bindCheckpointPopup(
  map: MapLibreMap,
  popup: Popup,
  planRef: { current: Plan | null },
  selectedRef: { current: string | null },
  onSelectRef: { current: (id: string) => void },
) {
  let shown: string | null = null
  let pinned = false
  popup.on('close', () => {
    shown = null
    pinned = false
  })

  const hitsAt = (point: PointLike) => map.queryRenderedFeatures(point, { layers: ['checkpoints', 'routes-hit'] })

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
    const route = planRef.current?.routes.find((r) => r.id === routeId)
    const cp = route?.checkpoints.find((c) => c.index === index)
    if (!route || !cp) return
    popup.setLngLat([cp.lon, cp.lat]).setDOMContent(checkpointPopup(route, cp))
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
        properties: { routeId: r.id, index: c.index, level: c.risk.level },
        geometry: { type: 'Point' as const, coordinates: [c.lon, c.lat] },
      })),
    ),
  }
}
