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

export function formatDuration(seconds: number) {
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return h ? `${h} h ${m} min` : `${m} min`
}

export function formatTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    weekday: 'short',
    hour: 'numeric',
    minute: '2-digit',
    timeZoneName: 'short',
  })
}
