import type { Checkpoint, RiskLevel, Route } from '../api/types'
import { LEVEL_COLOR, LEVEL_LABEL, formatSky, formatTime } from '../lib/risk'
import './CheckpointPopup.css'

type Factor = 'wind' | 'rain' | 'snow'

// Built with DOM APIs so route labels from the API are always treated as text.
// timeZone is the plan's (the origin's), so the ETA reads the same as in the checkpoint table.
export function checkpointPopup(route: Route, cp: Checkpoint, timeZone: string): HTMLElement {
  const { weather, risk } = cp
  const root = el('div', 'cp')

  const head = el('div', 'cp-head')
  head.append(levelTag(risk.level), el('span', 'cp-mile', `Mile ${Math.round(cp.mile)}`))
  root.append(head, el('div', 'cp-route', route.label), el('div', 'cp-eta', `ETA ${formatTime(cp.eta, timeZone)}`))
  const sky = formatSky(weather)
  if (sky) root.append(el('div', 'cp-sky', sky))

  const rows = el('dl', 'cp-rows')
  const gust = weather.gust_mph > weather.wind_mph ? `, gusts ${Math.round(weather.gust_mph)}` : ''
  rows.append(
    row('Wind', `${Math.round(weather.wind_mph)} mph${gust}`, risk.wind, risk.drivers.includes('wind')),
    row('Rain', `${weather.rain_in_hr.toFixed(2)} in/hr`, risk.rain, risk.drivers.includes('rain')),
    row('Snow', `${weather.snow_in_hr.toFixed(2)} in/hr`, risk.snow, risk.drivers.includes('snow')),
  )
  root.append(rows)

  if (risk.drivers.length) root.append(el('p', 'cp-note', `Driven by ${listFactors(risk.drivers)}`))
  if (risk.load_adjusted) root.append(el('p', 'cp-note cp-load', 'Load rule applied'))
  return root
}

function levelTag(level: RiskLevel) {
  const tag = el('span', 'cp-level', LEVEL_LABEL[level])
  tag.style.setProperty('--cp-level-color', LEVEL_COLOR[level])
  return tag
}

function row(name: string, value: string, level: RiskLevel, driver: boolean) {
  const wrap = el('div', driver ? 'cp-row cp-driver' : 'cp-row')
  const dt = el('dt', '', name)
  const dot = el('span', 'cp-dot')
  dot.style.setProperty('--cp-level-color', LEVEL_COLOR[level])
  dot.title = `${name} risk: ${LEVEL_LABEL[level]}`
  dt.prepend(dot)
  wrap.append(dt, el('dd', '', value))
  return wrap
}

const factorList = new Intl.ListFormat('en', { type: 'conjunction' })

function listFactors(factors: Factor[]) {
  return factorList.format(factors)
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text?: string) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}
