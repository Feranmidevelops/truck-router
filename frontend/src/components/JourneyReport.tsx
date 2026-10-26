import { memo, useId, useMemo, type CSSProperties } from 'react'
import type { ReportStop, ReportStretch, RiskLevel, Route } from '../api/types'
import {
  formatTempRange,
  hasPrecip,
  placeWeather,
  reportTimes,
  stretchCause,
  stretchMiles,
} from '../lib/report'
import { LEVEL_COLOR, timeFormatter } from '../lib/risk'
import { placeName } from '../lib/trip'
import { Reading } from './AlongRoute'
import { RainIcon, SnowIcon, WindIcon } from './icons'
import { LevelBadge } from './Level'
import './JourneyReport.css'

interface Props {
  route: Route
  timeZone: string // the plan's zone (the origin's): every time here reads in it
}

// The selected route as a story: leave, the stretches above Low in driving order, arrive.
// memo: only re-renders when the selected route changes.
export const JourneyReport = memo(function JourneyReport({ route, timeZone }: Props) {
  const titleId = useId()
  const time = useMemo(() => timeFormatter(timeZone), [timeZone])
  const times = useMemo(() => reportTimes(route.report, time), [route, time])
  const { departure, stretches, arrival } = route.report

  return (
    <section className="journey" aria-labelledby={titleId}>
      <div className="journey-head">
        <h3 id={titleId} className="journey-title">
          Journey report
        </h3>
        <p className="journey-meta">
          {route.label}, times in {time.zone(departure.time)}.
        </p>
      </div>
      <ol className="journey-list">
        <Stop verb="Leave" stop={departure} when={times.departure} />
        {stretches.map((s, i) => (
          <Stretch key={s.first_index} stretch={s} when={times.stretches[i]} />
        ))}
        {!stretches.length && (
          <li className="journey-item journey-calm" style={levelStyle('LOW')}>
            No weather above Low on this route.
          </li>
        )}
        <Stop verb="Arrive" stop={arrival} when={times.arrival} />
      </ol>
    </section>
  )
})

function levelStyle(level: RiskLevel) {
  return { '--level': LEVEL_COLOR[level] } as CSSProperties
}

function Stop({ verb, stop, when }: { verb: string; stop: ReportStop; when: string }) {
  return (
    <li className="journey-item journey-stop">
      <p className="journey-line">
        <strong>
          {verb} {placeName(stop.place)}
        </strong>{' '}
        <span className="journey-time">{when}</span>
      </p>
      <p className="journey-weather">{placeWeather(stop.weather)}</p>
    </li>
  )
}

function Stretch({ stretch: s, when }: { stretch: ReportStretch; when: string }) {
  const temps = formatTempRange(s.min_temp_f, s.max_temp_f)
  const gust = Math.round(s.max_gust_mph) > Math.round(s.max_wind_mph) ? Math.round(s.max_gust_mph) : null
  const drives = (factor: ReportStretch['drivers'][number]) => s.drivers.includes(factor)
  return (
    <li className="journey-item journey-stretch" style={levelStyle(s.level)}>
      <div className="stretch">
        <p className="stretch-head">
          <LevelBadge level={s.level} />
          <span className="stretch-cause">{stretchCause(s)}</span>
        </p>
        <p className="stretch-when">
          {stretchMiles(s)} · {when}
        </p>
        <p className="stretch-peaks">
          {(hasPrecip(s.max_rain_in_hr) || drives('rain')) && (
            <Reading icon={<RainIcon size={15} />} label="Peak rain" level={s.level} driver={drives('rain')}>
              {s.max_rain_in_hr.toFixed(2)} in/h
            </Reading>
          )}
          {(hasPrecip(s.max_snow_in_hr) || drives('snow')) && (
            <Reading icon={<SnowIcon size={15} />} label="Peak snow" level={s.level} driver={drives('snow')}>
              {s.max_snow_in_hr.toFixed(2)} in/h
            </Reading>
          )}
          <Reading icon={<WindIcon size={15} />} label="Peak wind" level={s.level} driver={drives('wind')}>
            {Math.round(s.max_wind_mph)} mph{gust !== null && <span className="reading-extra">, gusts {gust}</span>}
          </Reading>
          {temps && (
            <span className="reading">
              <span className="visually-hidden">Temperature: </span>
              {temps}
            </span>
          )}
        </p>
      </div>
    </li>
  )
}
