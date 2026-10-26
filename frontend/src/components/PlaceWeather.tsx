import { memo, useId } from 'react'
import type { Checkpoint, ReportStop, Route } from '../api/types'
import { formatTemp, formatWind, isNight } from '../lib/report'
import { formatTime } from '../lib/risk'
import { placeName } from '../lib/trip'
import { SkyIcon } from './icons'
import './PlaceWeather.css'

interface Props {
  route: Route
  timeZone: string // the plan's zone (the origin's)
}

// What the two ends of the trip are like when the truck is there: the origin as it leaves,
// the destination as it arrives on the selected route. Shown, not scored.
export const PlaceWeather = memo(function PlaceWeather({ route, timeZone }: Props) {
  const titleId = useId()
  const { departure, arrival } = route.report
  // The first checkpoint is the origin and the last the destination
  const first = route.checkpoints.at(0)
  const last = route.checkpoints.at(-1)
  return (
    <section className="place-weather" aria-labelledby={titleId}>
      <h3 id={titleId} className="visually-hidden">
        Weather at each end of {route.label}
      </h3>
      <Stop label="Leave" stop={departure} at={first} timeZone={timeZone} />
      <Stop label="Arrive" stop={arrival} at={last} timeZone={timeZone} />
    </section>
  )
})

interface StopProps {
  label: string // "Leave" or "Arrive"
  stop: ReportStop
  at: Checkpoint | undefined // where it is, to tell a clear night from a clear day
  timeZone: string
}

function Stop({ label, stop, at, timeZone }: StopProps) {
  const { weather: w } = stop
  const night = at ? isNight(at.lat, at.lon, stop.time) : false
  return (
    <div className="place-stop">
      <p className="place-when">
        {label} {formatTime(stop.time, timeZone)}
      </p>
      <p className="place-name">{placeName(stop.place)}</p>
      <p className="place-temp">
        <SkyIcon code={w.weather_code} night={night} size={22} />
        {formatTemp(w.temp_f)}
      </p>
      <p className="place-sky">{w.weather_code === null ? 'Sky unknown' : w.conditions}</p>
      <p className="place-wind">{formatWind(w.wind_mph, w.gust_mph)}</p>
    </div>
  )
}
