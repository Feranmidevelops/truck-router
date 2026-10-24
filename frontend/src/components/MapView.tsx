import { useEffect, useRef, useState } from 'react'
import { LngLatBounds, Map as MapLibreMap, NavigationControl, type GeoJSONSource } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import '../lib/maplibre'
import type { FeatureCollection, LineString, Point } from 'geojson'
import type { Plan } from '../api/types'
import { LEVELS, LEVEL_COLOR, LEVEL_LABEL, levelColorExpression } from '../lib/risk'

// OpenFreeMap is free and needs no key. Override with VITE_MAP_STYLE_URL if it's ever down.
const STYLE_URL = import.meta.env.VITE_MAP_STYLE_URL || 'https://tiles.openfreemap.org/styles/liberty'
const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }

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

    map.on('load', () => {
      map.addSource('routes', { type: 'geojson', data: EMPTY })
      map.addSource('checkpoints', { type: 'geojson', data: EMPTY })

      // TODO(day 3): add the heatmap source and layer here, before the route layers,
      // so routes draw on top.

      map.addLayer({
        id: 'routes-casing',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: { 'line-color': '#ffffff', 'line-width': ['case', ['get', 'selected'], 10, 7] },
      })
      map.addLayer({
        id: 'routes-line',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': ['case', ['get', 'selected'], '#1f5fbf', '#7d8aa0'],
          'line-width': ['case', ['get', 'selected'], 6, 4],
        },
      })
      map.addLayer({
        id: 'checkpoints',
        type: 'circle',
        source: 'checkpoints',
        paint: {
          'circle-color': levelColorExpression,
          'circle-radius': ['case', ['get', 'selected'], 6, 4],
          'circle-opacity': ['case', ['get', 'selected'], 1, 0.55],
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

  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready) return
    ;(map.getSource('routes') as GeoJSONSource).setData(routesGeoJSON(plan, selectedRouteId))
    ;(map.getSource('checkpoints') as GeoJSONSource).setData(checkpointsGeoJSON(plan, selectedRouteId))
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

// Selected route last so it draws on top.
function routesGeoJSON(plan: Plan | null, selectedId: string | null): FeatureCollection<LineString> {
  if (!plan) return { type: 'FeatureCollection', features: [] }
  const routes = [...plan.routes].sort((a, b) => Number(a.id === selectedId) - Number(b.id === selectedId))
  return {
    type: 'FeatureCollection',
    features: routes.map((r) => ({
      type: 'Feature',
      properties: { id: r.id, selected: r.id === selectedId },
      geometry: { type: 'LineString', coordinates: r.geometry },
    })),
  }
}

function checkpointsGeoJSON(plan: Plan | null, selectedId: string | null): FeatureCollection<Point> {
  if (!plan) return { type: 'FeatureCollection', features: [] }
  const routes = [...plan.routes].sort((a, b) => Number(a.id === selectedId) - Number(b.id === selectedId))
  return {
    type: 'FeatureCollection',
    features: routes.flatMap((r) =>
      r.checkpoints.map((c) => ({
        type: 'Feature' as const,
        properties: { routeId: r.id, index: c.index, level: c.risk.level, selected: r.id === selectedId },
        geometry: { type: 'Point' as const, coordinates: [c.lon, c.lat] },
      })),
    ),
  }
}
