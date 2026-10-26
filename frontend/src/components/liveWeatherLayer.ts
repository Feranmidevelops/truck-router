import { useEffect, useState, type RefObject } from 'react'
import type { Map as MapLibreMap, RasterLayerSpecification, RasterSourceSpecification, RasterTileSource } from 'maplibre-gl'
import {
  LIVE_LAYERS,
  RADAR_BOUNDS,
  latestRadarTime,
  latestSatelliteTime,
  radarTiles,
  satelliteTiles,
  type LiveLayer,
} from '../lib/liveWeather'

export type LiveStatus =
  | { state: 'loading' }
  | { state: 'live'; observed: string | null } // null: the newest satellite image, time unknown
  | { state: 'unavailable' }

const LAYER_ID: Record<LiveLayer, string> = { satellite: 'live-satellite', radar: 'live-radar' }
const ALL = Object.keys(LAYER_ID) as LiveLayer[]
// A tile or two can fail without losing the layer; this many for one image and it goes
const MAX_TILE_ERRORS = 3
// How often to see whether a newer image is due. Cheap: it only compares times.
const CHECK_EVERY_MS = 60_000

const SOURCE: Record<LiveLayer, Omit<RasterSourceSpecification, 'tiles'>> = {
  satellite: { type: 'raster', tileSize: 256, maxzoom: 6, attribution: LIVE_LAYERS.satellite.attribution },
  // About 1 km pixels, so nothing new past zoom 10. The bounds keep requests to the lower 48.
  radar: {
    type: 'raster',
    tileSize: 512,
    maxzoom: 10,
    bounds: RADAR_BOUNDS,
    attribution: LIVE_LAYERS.radar.attribution,
  },
}

// See-through enough to read the roads and towns underneath. The radar swaps frames
// without a fade, so two frames never blend into echoes that weren't there.
const PAINT: Record<LiveLayer, RasterLayerSpecification['paint']> = {
  satellite: { 'raster-opacity': 0.55 },
  radar: { 'raster-opacity': 0.8, 'raster-fade-duration': 0 },
}

export function isLiveWeatherSource(id: unknown) {
  return ALL.some((layer) => LAYER_ID[layer] === id)
}

// Shows one live layer, or none, and keeps it current: a newer image is looked for every
// few minutes while the tab is visible, and the tiles are swapped only when there is one.
// A layer picked again shows its last image straight away while a newer one is looked for.
// active: the map has loaded and been on screen. retry: bump it to try again after a failure.
export function useLiveWeather(
  mapRef: RefObject<MapLibreMap | null>,
  active: boolean,
  layer: LiveLayer | null,
  retry: number,
): LiveStatus | null {
  const [reports, setReports] = useState<Partial<Record<LiveLayer, LiveStatus>>>({})

  useEffect(() => {
    const map = mapRef.current
    if (!map || !active) return
    for (const other of ALL) if (other !== layer) hideLayer(map, other)
    if (!layer) return

    const set = (status: LiveStatus) => setReports((r) => ({ ...r, [layer]: status }))
    let ctrl = new AbortController()
    let checkedAt = 0
    let errors = 0
    let shown = map.getLayer(LAYER_ID[layer]) !== undefined
    if (shown) map.setLayoutProperty(LAYER_ID[layer], 'visibility', 'visible')

    const fail = () => {
      clearInterval(timer)
      ctrl.abort()
      removeLayer(map, layer) // so trying again starts from fresh tiles
      set({ state: 'unavailable' })
    }

    const check = async () => {
      checkedAt = Date.now()
      ctrl.abort()
      ctrl = new AbortController()
      const { signal } = ctrl
      let time: string | null
      try {
        time = await (layer === 'radar' ? latestRadarTime(signal) : latestSatelliteTime(signal))
      } catch (e) {
        if (signal.aborted) return
        console.error(e)
        if (shown) return // keep the last image; the badge says how old it is
        if (layer === 'radar') return fail() // never drawn without a time, see radarTiles
        time = null // GIBS can still send its newest image, just not say when it was taken
      }
      if (signal.aborted) return
      if (showLayer(map, layer, time)) errors = 0
      shown = true
      set({ state: 'live', observed: time })
    }

    const onError = (e: object) => {
      if (!('sourceId' in e) || e.sourceId !== LAYER_ID[layer]) return
      errors += 1
      if (errors === MAX_TILE_ERRORS) fail()
    }

    const checkIfDue = () => {
      if (document.visibilityState === 'visible' && Date.now() - checkedAt >= LIVE_LAYERS[layer].refreshMs) {
        void check()
      }
    }

    const timer = setInterval(checkIfDue, CHECK_EVERY_MS)
    const errorSub = map.on('error', onError)
    document.addEventListener('visibilitychange', checkIfDue)
    void check()
    return () => {
      clearInterval(timer)
      ctrl.abort()
      errorSub.unsubscribe()
      document.removeEventListener('visibilitychange', checkIfDue)
      // A failure is tried again whenever the layer is picked, so it isn't kept
      setReports((r) => (r[layer]?.state === 'unavailable' ? { ...r, [layer]: undefined } : r))
      // The layer itself is hidden by the next run: on unmount the map is already gone
    }
  }, [mapRef, active, layer, retry])

  if (!layer) return null
  // Picked but still waiting for the map counts as loading, so its key is there from the start
  return reports[layer] ?? { state: 'loading' }
}

// Adds the layer the first time, under the basemap's labels and the routes. Returns true when
// it starts drawing a new image.
function showLayer(map: MapLibreMap, layer: LiveLayer, time: string | null) {
  const id = LAYER_ID[layer]
  const tiles = time !== null && layer === 'radar' ? radarTiles(time) : satelliteTiles(time)
  const source = map.getSource<RasterTileSource>(id)
  if (!source) {
    map.addSource(id, { ...SOURCE[layer], tiles: [tiles] })
    const firstLabel = map.getLayersOrder().find((l) => map.getLayer(l)?.type === 'symbol')
    map.addLayer({ id, type: 'raster', source: id, paint: PAINT[layer] }, firstLabel)
    return true
  }
  map.setLayoutProperty(id, 'visibility', 'visible')
  if (source.tiles[0] === tiles) return false
  source.setTiles([tiles])
  return true
}

function hideLayer(map: MapLibreMap, layer: LiveLayer) {
  if (map.getLayer(LAYER_ID[layer])) map.setLayoutProperty(LAYER_ID[layer], 'visibility', 'none')
}

function removeLayer(map: MapLibreMap, layer: LiveLayer) {
  const id = LAYER_ID[layer]
  if (map.getLayer(id)) map.removeLayer(id)
  if (map.getSource(id)) map.removeSource(id)
}
