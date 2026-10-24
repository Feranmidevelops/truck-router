import type { ExpressionSpecification } from '@maplibre/maplibre-gl-style-spec'
import type { RiskLevel } from '../api/types'

export const LEVELS: RiskLevel[] = ['LOW', 'MODERATE', 'HIGH', 'SEVERE', 'NO_TRAVEL']

export const LEVEL_LABEL: Record<RiskLevel, string> = {
  LOW: 'Low',
  MODERATE: 'Moderate',
  HIGH: 'High',
  SEVERE: 'Severe',
  NO_TRAVEL: 'No Travel',
}

// Same order as LEVELS; index = numeric score used by the heatmap.
export const LEVEL_COLOR: Record<RiskLevel, string> = {
  LOW: '#2e9e5b',
  MODERATE: '#e0b400',
  HIGH: '#f07d1a',
  SEVERE: '#d7263d',
  NO_TRAVEL: '#6a1b9a',
}

// MapLibre "match" expression: ['match', ['get', 'level'], 'LOW', '#..', ..., fallback]
export const levelColorExpression = [
  'match',
  ['get', 'level'],
  ...LEVELS.flatMap((l) => [l, LEVEL_COLOR[l]]),
  '#888888',
] as unknown as ExpressionSpecification

// One decimal, as the API rounds them, so the card matches the recommendation reason
// ("Fewest no-travel miles (12.5 vs 25.0)") and a short stretch never prints as "0 mi"
export function formatMiles(miles: number) {
  return `${miles.toFixed(1)} mi`
}

// Round to the minute before splitting, so 59.5 min is "1 h 0 min", never "60 min".
// Same half-up rule as the backend's recommendation reason, so the two always agree.
export function formatDuration(seconds: number) {
  const minutes = Math.round(seconds / 60)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h ? `${h} h ${m} min` : `${m} min`
}

// Plan times show in the plan's zone (the origin's), not the viewer's: "Mon 9:49 PM CDT".
// timeZone undefined means the viewer's own zone.
export function formatTime(iso: string, timeZone?: string) {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
    timeZone,
  })
}

// For long lists of times, like the checkpoint table: the formatters are built once, and the
// day, clock time and zone name come separately so a table can group rows by day and print
// the zone once. timeZone undefined means the viewer's own zone.
export function timeFormatter(timeZone?: string) {
  const day = new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'short', day: 'numeric', timeZone })
  const clock = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit', timeZone })
  const zone = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short', timeZone })
  return {
    day: (iso: string) => day.format(new Date(iso)),
    clock: (iso: string) => clock.format(new Date(iso)),
    zone: (iso: string) => zone.formatToParts(new Date(iso)).find((p) => p.type === 'timeZoneName')?.value ?? '',
  }
}

export type TimeFormatter = ReturnType<typeof timeFormatter>
