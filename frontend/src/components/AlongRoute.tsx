import { memo, useEffect, useId, useMemo, useState, type CSSProperties, type ReactNode } from 'react'
import type { Checkpoint, RiskLevel, Route } from '../api/types'
import { checkpointKey, type Channel } from '../lib/channel'
import { LEVEL_COLOR, formatSky, timeFormatter, type TimeFormatter } from '../lib/risk'
import { CheckpointTable } from './CheckpointTable'
import { ChevronDown, RainIcon, SnowIcon, WindIcon } from './icons'
import { LevelBadge } from './Level'
import './AlongRoute.css'

interface Props {
  route: Route
  timeZone: string // the plan's zone (the origin's): every time here reads in it
  intervalMiles: number
  hoverChannel?: Channel<string | null> // rows hovered or focused here get a ring on the map
}

// Called with a row's checkpoint as the pointer or focus arrives, and null as it leaves
export type Highlight = (index: number | null) => void

// The selected route, checkpoint by checkpoint. The ones above Low come first, as readable
// rows; the full list, grouped by day, opens on request.
// memo: only re-renders when the selected route changes.
export const AlongRoute = memo(function AlongRoute({ route, timeZone, intervalMiles, hoverChannel }: Props) {
  const titleId = useId()
  const listId = useId()
  const [showAll, setShowAll] = useState(false)
  const time = useMemo(() => timeFormatter(timeZone), [timeZone])
  const notable = useMemo(() => route.checkpoints.filter((c) => c.risk.level !== 'LOW'), [route])
  const count = route.checkpoints.length
  const zone = count ? time.zone(route.checkpoints[0].eta) : ''
  const highlight = useMemo<Highlight>(
    () => (index) => hoverChannel?.set(index === null ? null : checkpointKey(route.id, index)),
    [hoverChannel, route.id],
  )
  // A row that goes away under the pointer never reports leaving it: clear the ring
  useEffect(() => () => highlight(null), [highlight, route])

  return (
    <section className="along" aria-labelledby={titleId}>
      <div className="along-head">
        <h3 id={titleId} className="along-title">
          Along {route.label}
        </h3>
        <p className="along-meta">
          {count} checkpoints, one every {intervalMiles} mi. Times in {zone}.
        </p>
      </div>
      {notable.length ? (
        <ol className="along-list" aria-label="Checkpoints above Low">
          {notable.map((c) => (
            <NotableRow key={c.index} checkpoint={c} time={time} highlight={highlight} />
          ))}
        </ol>
      ) : (
        <p className="along-calm">
          <LevelBadge level="LOW" />
          Calm all the way: every checkpoint is Low.
        </p>
      )}
      <button
        type="button"
        className="along-toggle"
        aria-expanded={showAll}
        aria-controls={listId}
        onClick={() => setShowAll((v) => !v)}
      >
        {showAll ? 'Hide the full list' : `Show all ${count} checkpoints`}
        <ChevronDown size={16} />
      </button>
      {showAll && <CheckpointTable id={listId} route={route} time={time} zone={zone} highlight={highlight} />}
    </section>
  )
})

interface RowProps {
  checkpoint: Checkpoint
  time: TimeFormatter
  highlight: Highlight
}

// Focusable, so keyboard users can light up a checkpoint on the map too
function NotableRow({ checkpoint: c, time, highlight }: RowProps) {
  const { weather: w, risk } = c
  const gust = Math.round(w.gust_mph) > Math.round(w.wind_mph) ? `gusts ${Math.round(w.gust_mph)}` : undefined
  const sky = formatSky(w)
  return (
    <li
      className="along-row"
      tabIndex={0}
      onMouseEnter={() => highlight(c.index)}
      onMouseLeave={() => highlight(null)}
      onFocus={() => highlight(c.index)}
      onBlur={() => highlight(null)}
    >
      <span className="along-when">
        <span className="along-time">{time.dayClock(c.eta)}</span>
        <span className="along-mile">mile {Math.round(c.mile)}</span>
      </span>
      <LevelBadge level={risk.level} />
      {sky && <span className="along-sky">{sky}</span>}
      <span className="along-readings">
        <Reading icon={<WindIcon size={15} />} label="Wind" level={risk.wind} driver={risk.drivers.includes('wind')}>
          {Math.round(w.wind_mph)} mph{gust && <span className="reading-extra">, {gust}</span>}
        </Reading>
        <Reading icon={<RainIcon size={15} />} label="Rain" level={risk.rain} driver={risk.drivers.includes('rain')}>
          {w.rain_in_hr.toFixed(2)} in/h
        </Reading>
        <Reading icon={<SnowIcon size={15} />} label="Snow" level={risk.snow} driver={risk.drivers.includes('snow')}>
          {w.snow_in_hr.toFixed(2)} in/h
        </Reading>
      </span>
      {risk.load_adjusted && <span className="along-note">Wind level raised for the load weight</span>}
    </li>
  )
}

interface ReadingProps {
  icon: ReactNode
  label: string // for screen readers; the icon says it on screen
  level: RiskLevel // this factor's own level
  driver: boolean // it sets the checkpoint's level
  children: ReactNode
}

// One weather value. The ones that set the checkpoint's level are bold and underlined in it.
export function Reading({ icon, label, level, driver, children }: ReadingProps) {
  return (
    <span
      className={driver ? 'reading driver' : 'reading'}
      style={driver ? ({ '--level': LEVEL_COLOR[level] } as CSSProperties) : undefined}
    >
      {icon}
      <span className="visually-hidden">{label}: </span>
      <span className="reading-value">{children}</span>
    </span>
  )
}
