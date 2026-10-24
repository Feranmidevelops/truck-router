import { useState, type FormEvent } from 'react'
import type { Place, PlanRequest } from '../api/types'
import { PlaceInput } from './PlaceInput'

interface Props {
  loading: boolean
  onSubmit: (req: PlanRequest) => void
}

const MAX_LOAD_LB = 80000
const HOUR_MS = 60 * 60 * 1000

// An hour from now, rounded down to the hour, on the origin's clock. Until an origin is
// picked, timeZone is undefined and this device's clock stands in.
function defaultDeparture(timeZone?: string) {
  return `${wallClock(new Date(Date.now() + HOUR_MS), timeZone).slice(0, -2)}00`
}

// What a clock in timeZone shows at that instant, in the datetime-local format "2026-10-26T08:00".
// en-US keeps the digits 0-9 whatever the browser's language; h23 makes midnight 00, not 24.
function wallClock(date: Date, timeZone?: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`
}

// The zone's short name at the typed departure: "CDT", or "CST" once the clocks have gone back.
// Which offset applies depends on the instant being looked for, so read the time as UTC first
// and correct it by the zone's offset twice.
function zoneNameAt(local: string, timeZone: string) {
  const asUtc = Date.parse(`${local}Z`)
  let at = asUtc
  if (Number.isNaN(asUtc)) at = Date.now() // the field is empty or half typed
  else for (let i = 0; i < 2; i++) at = asUtc - offsetMs(at, timeZone)
  const parts = new Intl.DateTimeFormat(undefined, { timeZone, timeZoneName: 'short' }).formatToParts(at)
  return parts.find((p) => p.type === 'timeZoneName')?.value ?? timeZone
}

// How far the zone's clock is ahead of UTC at that instant (negative west of Greenwich)
function offsetMs(at: number, timeZone: string) {
  const minute = at - (at % 60_000) // wallClock drops the seconds
  return Date.parse(`${wallClock(new Date(minute), timeZone)}Z`) - minute
}

// Pounds as typed ("42350", "42,350"), rounded to a whole number. null if not a number in range.
function parseLoad(text: string): number | null {
  const clean = text.replace(/[,\s]/g, '')
  if (!/^\d+(\.\d+)?$/.test(clean)) return null
  const lb = Math.round(Number(clean))
  return lb <= MAX_LOAD_LB ? lb : null
}

export function TripForm({ loading, onSubmit }: Props) {
  const [origin, setOrigin] = useState<Place | null>(null)
  const [destination, setDestination] = useState<Place | null>(null)
  const [departure, setDeparture] = useState(() => defaultDeparture())
  const [departureEdited, setDepartureEdited] = useState(false)
  const [loadText, setLoadText] = useState('40000')
  const [interval, setIntervalMiles] = useState<PlanRequest['interval_miles']>(25)

  const load = parseLoad(loadText)
  const loadError =
    load !== null ? null : loadText.trim() ? 'Enter a whole number from 0 to 80,000.' : 'Enter the load in pounds.'
  const ready = origin && destination && departure && load !== null
  // "Dallas" from "Dallas, TX"
  const departureLabel = origin?.timezone
    ? `Departure (local time in ${origin.label.split(',')[0]}, ${zoneNameAt(departure, origin.timezone)})`
    : 'Departure (local time at the origin)'

  // Until the driver sets a departure, the default follows the origin's clock
  function chooseOrigin(place: Place | null) {
    setOrigin(place)
    if (place?.timezone && !departureEdited) setDeparture(defaultDeparture(place.timezone))
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!origin || !destination || load === null) return
    onSubmit({
      origin,
      destination,
      // As typed: the server reads it as local time at the origin, whatever zone this device is in
      departure_time: departure,
      load_weight_lb: load,
      interval_miles: interval,
    })
  }

  return (
    <form className="trip-form" onSubmit={submit}>
      <PlaceInput label="Origin" value={origin} onChange={chooseOrigin} />
      <PlaceInput label="Destination" value={destination} onChange={setDestination} />
      {(!origin || !destination) && <p className="hint">Pick a place from the suggestions</p>}
      <div className="field">
        <label htmlFor="departure">{departureLabel}</label>
        <input
          id="departure"
          type="datetime-local"
          value={departure}
          onChange={(e) => {
            setDeparture(e.target.value)
            setDepartureEdited(true)
          }}
        />
      </div>
      <div className="row">
        <div className="field">
          <label htmlFor="load">Load (lb)</label>
          <input
            id="load"
            inputMode="numeric"
            autoComplete="off"
            value={loadText}
            onChange={(e) => setLoadText(e.target.value)}
            aria-invalid={loadError !== null}
            aria-describedby={loadError ? 'load-error' : undefined}
          />
        </div>
        <div className="field">
          <label htmlFor="interval">Check weather every</label>
          <select
            id="interval"
            value={interval}
            onChange={(e) => setIntervalMiles(Number(e.target.value) as PlanRequest['interval_miles'])}
          >
            <option value={10}>10 miles</option>
            <option value={25}>25 miles</option>
            <option value={50}>50 miles</option>
          </select>
        </div>
      </div>
      {loadError && (
        <p id="load-error" className="field-error">
          {loadError}
        </p>
      )}
      <button type="submit" disabled={!ready || loading}>
        {loading ? 'Planning…' : 'Plan routes'}
      </button>
    </form>
  )
}
