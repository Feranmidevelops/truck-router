import { useState, type FormEvent } from 'react'
import type { Place, PlanRequest } from '../api/types'
import { PlaceInput } from './PlaceInput'

interface Props {
  loading: boolean
  onSubmit: (req: PlanRequest) => void
}

function defaultDeparture() {
  const d = new Date(Date.now() + 60 * 60 * 1000)
  d.setMinutes(0, 0, 0)
  // datetime-local wants local time without offset
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function TripForm({ loading, onSubmit }: Props) {
  const [origin, setOrigin] = useState<Place | null>(null)
  const [destination, setDestination] = useState<Place | null>(null)
  const [departure, setDeparture] = useState(defaultDeparture)
  const [load, setLoad] = useState(40000)
  const [interval, setIntervalMiles] = useState<PlanRequest['interval_miles']>(25)

  const ready = origin && destination && departure && load >= 0

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!origin || !destination) return
    onSubmit({
      origin,
      destination,
      departure_time: new Date(departure).toISOString(),
      load_weight_lb: load,
      interval_miles: interval,
    })
  }

  return (
    <form className="trip-form" onSubmit={submit}>
      <PlaceInput label="Origin" value={origin} onChange={setOrigin} />
      <PlaceInput label="Destination" value={destination} onChange={setDestination} />
      <div className="field">
        <label htmlFor="departure">Departure</label>
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
            type="number"
            min={0}
            max={80000}
            step={500}
            value={load}
            onChange={(e) => setLoad(Number(e.target.value))}
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
      <button type="submit" disabled={!ready || loading}>
        {loading ? 'Planning…' : 'Plan routes'}
      </button>
    </form>
  )
}
