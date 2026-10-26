import { memo, useEffect, useId, useMemo, useState, type CSSProperties } from 'react'
import type { ReportStop, ReportStretch, RiskLevel, Route } from '../api/types'
import type { Channel, CheckpointRun } from '../lib/channel'
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
  focusChannel?: Channel<CheckpointRun | null> // the stretch picked here, for the map to frame
}

// The selected route as a story: leave, the stretches above Low in driving order, arrive.
// Picking a stretch rings its checkpoints on the map and zooms to them; picking it again
// shows the whole trip. memo: only re-renders when the selected route changes.
export const JourneyReport = memo(function JourneyReport({ route, timeZone, focusChannel }: Props) {
  const titleId = useId()
  const hintId = useId()
  const time = useMemo(() => timeFormatter(timeZone), [timeZone])
  const times = useMemo(() => reportTimes(route.report, time), [route, time])
  const { departure, stretches, arrival } = route.report

  // The picked stretch, by its first checkpoint. Another route starts with none picked;
  // adjusted while rendering rather than in an effect, so there's no extra render.
  const [picked, setPicked] = useState<number | null>(null)
  const [pickedOn, setPickedOn] = useState(route)
  if (route !== pickedOn) {
    setPickedOn(route)
    setPicked(null)
  }

  useEffect(() => {
    const s = route.report.stretches.find((x) => x.first_index === picked)
    focusChannel?.set(s ? { routeId: route.id, first: s.first_index, last: s.last_index } : null)
  }, [focusChannel, route, picked])
  // Nothing stays ringed on the map once the report goes away
  useEffect(() => () => focusChannel?.set(null), [focusChannel])

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
          <Stretch
            key={s.first_index}
            stretch={s}
            when={times.stretches[i]}
            picked={s.first_index === picked}
            hintId={hintId}
            onPick={() => setPicked((p) => (p === s.first_index ? null : s.first_index))}
          />
        ))}
        {!stretches.length && (
          <li className="journey-item journey-calm" style={levelStyle('LOW')}>
            No weather above Low on this route.
          </li>
        )}
        <Stop verb="Arrive" stop={arrival} when={times.arrival} />
      </ol>
      {stretches.length > 0 && (
        <p id={hintId} className="journey-hint">
          Select a stretch to zoom the map to it.
        </p>
      )}
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

interface StretchProps {
  stretch: ReportStretch
  when: string // its times, as reportTimes() prints them
  picked: boolean // on the map now
  hintId: string // the line that says what picking does
  onPick: () => void
}

// A button holds only inline content, so the lines are spans laid out by CSS
function Stretch({ stretch: s, when, picked, hintId, onPick }: StretchProps) {
  const temps = formatTempRange(s.min_temp_f, s.max_temp_f)
  const gust = Math.round(s.max_gust_mph) > Math.round(s.max_wind_mph) ? Math.round(s.max_gust_mph) : null
  const drives = (factor: ReportStretch['drivers'][number]) => s.drivers.includes(factor)
  return (
    <li className="journey-item journey-stretch" style={levelStyle(s.level)}>
      <button type="button" className="stretch" aria-pressed={picked} aria-describedby={hintId} onClick={onPick}>
        <span className="stretch-head">
          <LevelBadge level={s.level} />
          <span className="stretch-cause">{stretchCause(s)}</span>
        </span>
        <span className="stretch-when">
          {stretchMiles(s)} · {when}
        </span>
        <span className="stretch-peaks">
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
        </span>
      </button>
    </li>
  )
}
