import type { CheckpointWeather, JourneyReport, ReportStretch } from '../api/types'
import { formatSky, type TimeFormatter } from './risk'

// What the sky is doing, in the groups the icons draw. From the WMO weather code Open-Meteo
// reports; null for no code, and for the few codes outside these groups.
export type Sky = 'clear' | 'cloud' | 'fog' | 'rain' | 'snow' | 'storm'

export function skyFromCode(code: number | null): Sky | null {
  if (code === null) return null
  if (code <= 1) return 'clear'
  if (code <= 3) return 'cloud'
  if (code >= 45 && code <= 48) return 'fog'
  if ((code >= 51 && code <= 67) || (code >= 80 && code <= 82)) return 'rain'
  if ((code >= 71 && code <= 77) || (code >= 85 && code <= 86)) return 'snow'
  if (code >= 95 && code <= 99) return 'storm'
  return null
}

export function formatTemp(tempF: number | null) {
  return tempF === null ? '—' : `${Math.round(tempF)}°F`
}

// "wind 13 mph", or "wind 13 mph, gusts 18" when the gusts are stronger
export function formatWind(windMph: number, gustMph: number) {
  const wind = Math.round(windMph)
  const gust = Math.round(gustMph)
  return gust > wind ? `wind ${wind} mph, gusts ${gust}` : `wind ${wind} mph`
}

// "38–45°F", or "41°F" when the stretch keeps one temperature. Empty when none is known.
export function formatTempRange(min: number | null, max: number | null) {
  if (min === null || max === null) return ''
  const low = Math.round(min)
  const high = Math.round(max)
  return low === high ? `${low}°F` : `${low}–${high}°F`
}

// Rain and snow print to two decimals; anything that would print as 0.00 isn't worth a line
export function hasPrecip(inPerHour: number) {
  return inPerHour >= 0.005
}

const FACTOR_WORD = { wind: 'wind', rain: 'rain', snow: 'snow' } as const

// What made the stretch risky, and where: "Snow and wind near Raton, NM"
export function stretchCause(stretch: ReportStretch) {
  const words = stretch.drivers.map((d) => FACTOR_WORD[d])
  const what = words.length ? words.join(' and ') : 'weather'
  return `${what[0].toUpperCase()}${what.slice(1)} ${stretch.where}`
}

// "miles 525–550", or "mile 675" for a stretch of one checkpoint
export function stretchMiles(stretch: ReportStretch) {
  const start = Math.round(stretch.start_mile)
  const end = Math.round(stretch.end_mile)
  return start === end ? `mile ${start}` : `miles ${start}–${end}`
}

// When each line of the report happens, in the plan's zone. The first line names the day, and
// a later one only when the day has changed, so a long trip reads
// "Mon 8:00 AM … 11:17 PM–Tue 12:05 AM … 5:08 AM".
export function reportTimes(report: JourneyReport, time: TimeFormatter) {
  let day = time.day(report.departure.time)
  const at = (iso: string) => {
    const next = time.day(iso)
    const label = next === day ? time.clock(iso) : time.dayClock(iso)
    day = next
    return label
  }
  return {
    departure: time.dayClock(report.departure.time),
    stretches: report.stretches.map((s) =>
      s.first_index === s.last_index ? at(s.start_eta) : `${at(s.start_eta)}–${at(s.end_eta)}`,
    ),
    arrival: at(report.arrival.time),
  }
}

// "Clear, 60°F · wind 5 mph": the sky and temperature when known, then the wind
export function placeWeather(weather: CheckpointWeather) {
  return [formatSky(weather), formatWind(weather.wind_mph, weather.gust_mph)].filter(Boolean).join(' · ')
}
