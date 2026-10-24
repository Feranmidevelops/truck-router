import { useState, type FormEvent } from 'react'
import { geocode } from '../api/client'
import type { Place, PlanRequest } from '../api/types'
import { ArrowRight } from './icons'
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

// The best match for a place the driver typed but didn't pick from the list. null, with the
// reason passed to fail, when there's no match or search is down.
async function bestMatch(text: string, fail: (message: string) => void): Promise<Place | null> {
  const q = text.trim()
  try {
    const [best] = await geocode(q)
    if (!best) fail(`No place found for "${q}". Try a city name.`)
    return best ?? null
  } catch {
    fail("Place search isn't working right now. Try again in a moment.")
    return null
  }
}

export function TripForm({ loading, onSubmit }: Props) {
  const [origin, setOrigin] = useState<Place | null>(null)
  const [destination, setDestination] = useState<Place | null>(null)
  const [originText, setOriginText] = useState('')
  const [destinationText, setDestinationText] = useState('')
  const [originError, setOriginError] = useState<string | null>(null)
  const [destinationError, setDestinationError] = useState<string | null>(null)
  const [finding, setFinding] = useState(false)
  const [departure, setDeparture] = useState(() => defaultDeparture())
  const [departureEdited, setDepartureEdited] = useState(false)
  const [loadText, setLoadText] = useState('40000')
  const [interval, setIntervalMiles] = useState<PlanRequest['interval_miles']>(25)

  const load = parseLoad(loadText)
  const loadError =
    load !== null ? null : loadText.trim() ? 'Enter a whole number from 0 to 80,000.' : 'Enter the load in pounds.'
  // A place typed but not picked is fine: the best match is looked up when the trip is planned
  const named = (place: Place | null, text: string) => place !== null || text.trim().length >= 2
  const ready = named(origin, originText) && named(destination, destinationText) && departure && load !== null
  // "Dallas" from "Dallas, TX"
  const departureLabel = origin?.timezone
    ? `Departure (local time in ${origin.label.split(',')[0]}, ${zoneNameAt(departure, origin.timezone)})`
    : 'Departure (local time at the origin)'

  // Until the driver sets a departure, the default follows the origin's clock
  function chooseOrigin(place: Place | null) {
    setOrigin(place)
    if (place?.timezone && !departureEdited) setDeparture(defaultDeparture(place.timezone))
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (load === null) return
    setFinding(true)
    const [from, to] = await Promise.all([
      origin ?? bestMatch(originText, setOriginError),
      destination ?? bestMatch(destinationText, setDestinationError),
    ])
    setFinding(false)
    if (!from || !to) return
    // Show which places were used, and move the default departure onto the origin's clock
    if (!origin) chooseOrigin(from)
    if (!destination) setDestination(to)
    const departureTime = !origin && from.timezone && !departureEdited ? defaultDeparture(from.timezone) : departure
    onSubmit({
      origin: from,
      destination: to,
      // As typed: the server reads it as local time at the origin, whatever zone this device is in
      departure_time: departureTime,
      load_weight_lb: load,
      interval_miles: interval,
    })
  }

  return (
    <form className="trip-form" onSubmit={submit}>
      <PlaceInput
        label="Origin"
        value={origin}
        onChange={chooseOrigin}
        onTextChange={(text) => {
          setOriginText(text)
          setOriginError(null)
        }}
        error={originError}
      />
      <PlaceInput
        label="Destination"
        value={destination}
        onChange={setDestination}
        onTextChange={(text) => {
          setDestinationText(text)
          setDestinationError(null)
        }}
        error={destinationError}
      />
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
      <button type="submit" className="button button-block" disabled={!ready || loading || finding}>
        {finding ? 'Finding places…' : loading ? 'Planning…' : 'Plan routes'}
        <span className="button-arrow">
          <ArrowRight size={16} />
        </span>
      </button>
    </form>
  )
}
