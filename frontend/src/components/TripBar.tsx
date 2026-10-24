import { placeName, type TripSummary } from '../lib/trip'
import { ArrowRight } from './icons'
import './TripBar.css'

interface Props {
  trip: TripSummary | null
  // planning: a plan for this trip is on its way. failed: it came back with an error.
  status: 'planning' | 'failed' | 'ready'
  onEdit: () => void // back to the form in the hero
}

const pounds = new Intl.NumberFormat('en-US')

// Sticks to the top of the window below the hero: the trip on screen and a way back to the form
export function TripBar({ trip, status, onEdit }: Props) {
  return (
    <div className="trip-bar">
      <div className="trip-bar-inner">
        {trip ? (
          <p className="trip-summary">
            <span className="trip-route">
              <span className="trip-place" title={trip.from}>
                {placeName(trip.from)}
              </span>
              <ArrowRight size={16} className="trip-arrow" />
              <span className="visually-hidden"> to </span>
              <span className="trip-place" title={trip.to}>
                {placeName(trip.to)}
              </span>
            </span>
            <span className="trip-meta">
              {status === 'planning' ? (
                <span className="trip-planning">
                  <span className="spinner" aria-hidden />
                  Planning…
                </span>
              ) : status === 'failed' ? (
                <span>Not planned: see the message below</span>
              ) : (
                <>
                  <span>Leaves {trip.departure}</span>
                  <span>{pounds.format(trip.loadLb)} lb</span>
                  <span className="trip-interval">Weather every {trip.intervalMiles} mi</span>
                </>
              )}
            </span>
          </p>
        ) : (
          <p className="trip-summary trip-empty">No trip planned yet</p>
        )}
        <button type="button" className="button button-outline trip-edit" onClick={onEdit}>
          {trip ? 'Edit trip' : 'Plan a trip'}
        </button>
      </div>
    </div>
  )
}
