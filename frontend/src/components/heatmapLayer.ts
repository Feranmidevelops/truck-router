import type { GeoJSONSource, Map as MapLibreMap } from 'maplibre-gl'
import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'
import type { FeatureCollection, Point } from 'geojson'
import type { Heatmap } from '../api/types'
import { LEVELS, LEVEL_COLOR } from '../lib/risk'

export const HEAT_LAYER = 'heat'
const EMPTY: FeatureCollection<Point> = { type: 'FeatureCollection', features: [] }

// Each grid point is a soft circle in its own level's colour, so every cell reads as
// its real level. (A density heatmap blends neighbours: a lone Severe cell next to Low
// ones came out green, and Low cells showed nothing at all.) Low is kept faint so
// risk stands out.
const HEAT_FILL = LEVELS.flatMap((level, score) => [score, withAlpha(LEVEL_COLOR[level], level === 'LOW' ? 0.2 : 0.55)])

function heatColor(hour: number) {
  return ['match', ['get', `h${hour}`], ...HEAT_FILL, 'rgba(0, 0, 0, 0)'] as unknown as ExpressionSpecification
}

// 1.3 cell widths, mostly feathered by the blur, so neighbours overlap into one field.
// The radius doubles with each zoom level, as the map does, so the overlap never changes.
function cellRadius(cellDeg: number): ExpressionSpecification {
  const atZoom0 = 1.3 * cellDeg * (512 / 360) // the world is 512 px wide at zoom 0
  return ['interpolate', ['exponential', 2], ['zoom'], 0, atZoom0, 22, atZoom0 * 2 ** 22]
}

// Added once when the map loads, under the basemap's labels and the route layers.
export function addHeatmapLayer(map: MapLibreMap) {
  // buffer 0: each point lands in exactly one tile, so no circle is drawn twice
  // along tile edges (circles aren't clipped to their tile).
  map.addSource(HEAT_LAYER, { type: 'geojson', data: EMPTY, buffer: 0 })
  const firstLabel = map.getLayersOrder().find((id) => map.getLayer(id)?.type === 'symbol')
  map.addLayer(
    {
      id: HEAT_LAYER,
      type: 'circle',
      source: HEAT_LAYER,
      paint: {
        'circle-color': heatColor(0),
        'circle-radius': cellRadius(0.25),
        'circle-blur': 0.8,
        'circle-pitch-alignment': 'map',
      },
    },
    firstLabel,
  )
}

// The only setData for the heatmap: once per heatmap, with all 49 hours attached.
export function setHeatmapData(map: MapLibreMap, heatmap: Heatmap | null) {
  ;(map.getSource(HEAT_LAYER) as GeoJSONSource).setData(heatmap ? heatmapGeoJSON(heatmap) : EMPTY)
  if (heatmap) map.setPaintProperty(HEAT_LAYER, 'circle-radius', cellRadius(gridSpacing(heatmap)))
  showHeatmapHour(map, 0)
}

// The slider only swaps which hour property the colour reads. The points and their
// 49 hourly values are already on the map, so nothing is fetched or re-sent.
export function showHeatmapHour(map: MapLibreMap, hour: number) {
  map.setPaintProperty(HEAT_LAYER, 'circle-color', heatColor(hour))
}

// Converts the API's v[] into h0..h48 properties once, when the heatmap arrives.
function heatmapGeoJSON(heatmap: Heatmap): FeatureCollection<Point> {
  return {
    type: 'FeatureCollection',
    features: heatmap.points.map((p) => ({
      type: 'Feature',
      properties: Object.fromEntries(p.v.map((level, hour) => [`h${hour}`, level])),
      geometry: { type: 'Point', coordinates: [p.lon, p.lat] },
    })),
  }
}

// The backend widens the grid past 0.25 deg on long trips, so read it from the data.
// Both axes are checked, since a straight east-west trip can have a single row.
function gridSpacing(heatmap: Heatmap) {
  const spacing = Math.min(smallestGap(heatmap.points.map((p) => p.lat)), smallestGap(heatmap.points.map((p) => p.lon)))
  return Number.isFinite(spacing) ? spacing : 0.25
}

function smallestGap(values: number[]) {
  const sorted = [...new Set(values)].sort((a, b) => a - b)
  let gap = Infinity
  for (let i = 1; i < sorted.length; i++) gap = Math.min(gap, sorted[i] - sorted[i - 1])
  return gap
}

function withAlpha(hex: string, alpha: number) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}
