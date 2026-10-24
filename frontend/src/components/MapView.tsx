import { useEffect, useRef, useState } from 'react'
import { LngLatBounds, Map as MapLibreMap, NavigationControl, type GeoJSONSource } from 'maplibre-gl'
import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'
import 'maplibre-gl/dist/maplibre-gl.css'
import '../lib/maplibre'
import './MapView.css'
import type { FeatureCollection, LineString, Point } from 'geojson'
import type { Plan } from '../api/types'
import { LEVELS, LEVEL_COLOR, LEVEL_LABEL, levelColorExpression } from '../lib/risk'

// OpenFreeMap is free and needs no key. Override with VITE_MAP_STYLE_URL if it's ever down.
const STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL || 'https://tiles.openfreemap.org/styles/liberty'
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

// Selection lives in feature-state, so changing it restyles on the GPU without
// sending any geometry back to the map's worker.
const SELECTED: ExpressionSpecification = ['boolean', ['feature-state', 'selected'], false]

interface Props {
  plan: Plan | null
  selectedRouteId: string | null
  onSelectRoute: (id: string) => void
}

export function MapView({ plan, selectedRouteId, onSelectRoute }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const onSelectRef = useRef(onSelectRoute)
  const [ready, setReady] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const errorDismissed = useRef(false)

  useEffect(() => {
    onSelectRef.current = onSelectRoute
  }, [onSelectRoute])

  // Create the map once. Sources and layers are added once; later updates only call setData.
  useEffect(() => {
    if (!containerRef.current) return
    const map = new MapLibreMap({
      container: containerRef.current,
      style: STYLE_URL,
      center: [-98.5, 39.5],
      zoom: 3.6,
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')

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
          'circle-radius': ['case', SELECTED, 6, 4],
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
          'circle-radius': 6,
          'circle-opacity': ['case', SELECTED, 1, 0],
          'circle-stroke-opacity': ['case', SELECTED, 1, 0],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
        },
      })

      map.on('click', 'routes-line', (e) => {
        const id = e.features?.[0]?.properties?.id
        if (typeof id === 'string') onSelectRef.current(id)
      })
      map.on('mouseenter', 'routes-line', () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', 'routes-line', () => (map.getCanvas().style.cursor = ''))
      // TODO(day 2): checkpoint popup on hover/click: mile, ETA, wind/rain/snow, level, drivers.

      setReady(true)
    })

    mapRef.current = map
    return () => {
      map.remove()
      mapRef.current = null
      setReady(false)
    }
  }, [])

  // Geometry is uploaded only when the plan changes, never on selection.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
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
