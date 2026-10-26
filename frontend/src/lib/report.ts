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
