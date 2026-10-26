import { memo, useId } from 'react'
import type { ReportStop, Route } from '../api/types'
import { formatTemp, formatWind } from '../lib/report'
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
  return (
    <section className="place-weather" aria-labelledby={titleId}>
      <h3 id={titleId} className="visually-hidden">
        Weather at each end of {route.label}
      </h3>
      <Stop label="Leave" stop={departure} timeZone={timeZone} />
      <Stop label="Arrive" stop={arrival} timeZone={timeZone} />
    </section>
  )
})

function Stop({ label, stop, timeZone }: { label: string; stop: ReportStop; timeZone: string }) {
  const { weather: w } = stop
  return (
    <div className="place-stop">
      <p className="place-when">
        {label} {formatTime(stop.time, timeZone)}
      </p>
      <p className="place-name">{placeName(stop.place)}</p>
      <p className="place-temp">
        <SkyIcon code={w.weather_code} size={22} />
        {formatTemp(w.temp_f)}
      </p>
      <p className="place-sky">{w.weather_code === null ? 'Sky unknown' : w.conditions}</p>
      <p className="place-wind">{formatWind(w.wind_mph, w.gust_mph)}</p>
    </div>
  )
}
