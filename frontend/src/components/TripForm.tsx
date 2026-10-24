import { useState, type FormEvent } from 'react'
import type { Place, PlanRequest } from '../api/types'
import { PlaceInput } from './PlaceInput'

interface Props {
  loading: boolean
  onSubmit: (req: PlanRequest) => void
}

const MAX_LOAD_LB = 80000

function defaultDeparture() {
  const d = new Date(Date.now() + 60 * 60 * 1000)
  d.setMinutes(0, 0, 0)
  // datetime-local wants local time without offset
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
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
  const [departure, setDeparture] = useState(defaultDeparture)
  const [loadText, setLoadText] = useState('40000')
  const [interval, setIntervalMiles] = useState<PlanRequest['interval_miles']>(25)

  const load = parseLoad(loadText)
  const loadError =
    load !== null ? null : loadText.trim() ? 'Enter a whole number from 0 to 80,000.' : 'Enter the load in pounds.'
  const ready = origin && destination && departure && load !== null

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
      <PlaceInput label="Origin" value={origin} onChange={setOrigin} />
      <PlaceInput label="Destination" value={destination} onChange={setDestination} />
      {(!origin || !destination) && <p className="hint">Pick a place from the suggestions</p>}
      <div className="field">
        <label htmlFor="departure">Departure (local time at the origin)</label>
        <input
          id="departure"
          type="datetime-local"
          value={departure}
          onChange={(e) => setDeparture(e.target.value)}
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
