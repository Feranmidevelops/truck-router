// Current weather over the whole map, from two public services that need no key and allow
// requests from any site. Route risk is the forecast for when the truck gets there; these are
// what the sky looks like now, so they're labelled "now" and never shown with the risk shading.

export type LiveLayer = 'radar' | 'satellite'
export type MapWeather = 'off' | LiveLayer

const MINUTE = 60_000

interface LiveLayerInfo {
  name: string // "radar", as in "Live radar unavailable"
  refreshMs: number // how often to look for a newer image
  staleMs: number // an image older than this is flagged as out of date
  attribution: string
}

// GOES-East makes a new image every 10 minutes and GIBS has it about 25 minutes later.
// The radar mosaic updates every few minutes.
export const LIVE_LAYERS: Record<LiveLayer, LiveLayerInfo> = {
  satellite: {
    name: 'satellite',
    refreshMs: 10 * MINUTE,
    staleMs: 45 * MINUTE,
    attribution: 'Imagery: NASA GIBS (ESDIS), GOES-East ABI',
  },
  radar: {
    name: 'radar',
    refreshMs: 5 * MINUTE,
    staleMs: 20 * MINUTE,
    attribution: 'Radar: NOAA nowCOAST / NWS MRMS',
  },
}

// Satellite: GOES-East infrared (band 13), coloured by cloud-top temperature, from NASA GIBS.
const GIBS = 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best'
const GIBS_LAYER = 'GOES-East_ABI_Band13_Clean_Infrared'
const GIBS_MATRIX = 'GoogleMapsCompatible_Level6' // zoom 0 to 6; the map stretches it beyond

// time null asks GIBS for its latest image without knowing when that was.
// WMTS puts the row before the column, so it's {y}/{x}.
export function satelliteTiles(time: string | null) {
  return `${GIBS}/${GIBS_LAYER}/default/${time ?? 'default'}/${GIBS_MATRIX}/{z}/{y}/{x}.png`
}

// GIBS lists the image times it has in a window as ranges, "start/end/PT10M", oldest first.
// The end of the last range is the newest image.
export async function latestSatelliteTime(signal: AbortSignal) {
  const end = new Date()
  const start = new Date(end.getTime() - 6 * 60 * MINUTE)
  const doc = await fetchXml(
    `${GIBS}/1.0.0/${GIBS_LAYER}/default/${GIBS_MATRIX}/all/${isoSeconds(start)}--${isoSeconds(end)}.xml`,
    signal,
  )
  const ranges = doc.getElementsByTagName('Domain')[0]?.textContent ?? ''
  const last = ranges.split(',').pop()?.trim().split('/') ?? []
  return checkedTime(last.length === 3 ? last[1] : last[0])
}

// Radar: the NWS base reflectivity mosaic for the lower 48, from NOAA nowCOAST.
const NOWCOAST = 'https://nowcoast.noaa.gov/geoserver'
const RADAR_LAYER = 'conus_base_reflectivity_mosaic'
export const RADAR_BOUNDS: [number, number, number, number] = [-127, 21, -65, 51]

// The time is always sent: without it a cache along the way can serve an old frame.
export function radarTiles(time: string) {
  const params = new URLSearchParams({
    SERVICE: 'WMS',
    REQUEST: 'GetMap',
    VERSION: '1.3.0',
    FORMAT: 'image/png',
    TRANSPARENT: 'TRUE',
    STYLES: '',
    CRS: 'EPSG:3857',
    WIDTH: '512',
    HEIGHT: '512',
    LAYERS: `weather_radar:${RADAR_LAYER}`,
    TIME: time,
  })
  // Added after, as URLSearchParams would escape the braces the map fills in
  return `${NOWCOAST}/ows?${params}&BBOX={bbox-epsg-3857}`
}

// The newest frame is the default of the layer's time dimension (the capabilities are ~10 kB)
export async function latestRadarTime(signal: AbortSignal) {
  const doc = await fetchXml(
    `${NOWCOAST}/weather_radar/${RADAR_LAYER}/ows?service=WMS&request=GetCapabilities&version=1.3.0`,
    signal,
  )
  const time = Array.from(doc.getElementsByTagName('Dimension')).find((d) => d.getAttribute('name') === 'time')
  return checkedTime(time?.getAttribute('default'))
}

async function fetchXml(url: string, signal: AbortSignal) {
  const res = await fetch(url, { signal })
  if (!res.ok) throw new Error(`${res.status} from ${new URL(url).host}`)
  const doc = new DOMParser().parseFromString(await res.text(), 'application/xml')
  if (doc.getElementsByTagName('parsererror').length) throw new Error(`Unreadable reply from ${new URL(url).host}`)
  return doc
}

function checkedTime(time: string | null | undefined) {
  if (!time || Number.isNaN(Date.parse(time))) throw new Error('No image time in the reply')
  return time
}

// "2026-10-26T07:03:48Z": GIBS wants whole seconds
function isoSeconds(date: Date) {
  return date.toISOString().replace(/\.\d+Z$/, 'Z')
}
