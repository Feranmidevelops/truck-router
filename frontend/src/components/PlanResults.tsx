import type { Plan } from '../api/types'
import { formatDuration } from '../lib/risk'
import { AlongRoute } from './AlongRoute'
import { RouteList } from './RouteList'

interface Props {
  plan: Plan | null
  loading: boolean
  error: string | null
  selectedRouteId: string | null
  onSelect: (id: string) => void
  onRetry?: () => void // only when the same request again could work
  onPlanTrip: () => void // back to the form
}

// The results panel (the bottom sheet on phones): loading and error states, route cards,
// checkpoints. Each part is a section with its own heading, so more can slot in.
export function PlanResults({ plan, loading, error, selectedRouteId, onSelect, onRetry, onPlanTrip }: Props) {
  const selectedRoute = plan?.routes.find((r) => r.id === selectedRouteId)

  return (
    <div className="results">
      <div className="results-head">
        <h2 className="results-title">Routes</h2>
        {plan && <span className="results-meta">{plan.routes.length} compared, safest first</span>}
      </div>
      {loading && <LoadingNote />}
      {error && <PlanError message={error} onRetry={onRetry} />}
      {loading && !plan && <RouteSkeleton />}
      {!plan && !loading && !error && <NoTrip onPlanTrip={onPlanTrip} />}
      {plan && (
        // The previous plan stays on screen, dimmed and inert, until the new one arrives
        <div className={loading ? 'results-body stale' : 'results-body'} inert={loading}>
          <RouteList plan={plan} selectedRouteId={selectedRouteId} onSelect={onSelect} />
          {selectedRoute && (
            <AlongRoute
              route={selectedRoute}
              timeZone={plan.input.timezone}
              intervalMiles={plan.input.interval_miles}
            />
          )}
        </div>
      )}
    </div>
  )
}

type PeekProps = Pick<Props, 'plan' | 'loading' | 'error' | 'selectedRouteId' | 'onRetry'>

// What a phone shows with its sheet down: where the plan is at, in a few lines
export function PlanPeek({ plan, loading, error, selectedRouteId, onRetry }: PeekProps) {
  const route = plan?.routes.find((r) => r.id === selectedRouteId)
  if (error) return <PlanError message={error} onRetry={onRetry} />
  if (loading && !plan) return <LoadingNote />
  if (!plan || !route) return <p className="peek-note">No trip yet. Plan one above and the routes show up here.</p>
  return (
    <div className={loading ? 'peek-route stale' : 'peek-route'}>
      <p className="peek-label">
        <strong>{route.label}</strong>
        {route.id === plan.recommended_route_id && <span className="tag tag-dark">Recommended</span>}
      </p>
      <p className="peek-numbers">
        {formatDuration(route.duration_s)} · {route.distance_miles.toFixed(0)} mi
      </p>
      <p className="peek-hint">{loading ? 'Planning the new trip…' : 'Swipe up for every route and checkpoint'}</p>
    </div>
  )
}

function LoadingNote() {
  return (
    <p className="loading-note" role="status">
      <span className="spinner" aria-hidden />
      Getting routes and forecasts…
    </p>
  )
}

function PlanError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="banner banner-alert plan-error" role="alert">
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="button button-outline" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  )
}

function NoTrip({ onPlanTrip }: { onPlanTrip: () => void }) {
  return (
    <div className="no-trip">
      <p>
        Plan a trip to compare routes here. Each route goes on the map with a dot for every checkpoint, coloured by
        the weather risk when the truck gets there.
      </p>
      <button type="button" className="button" onClick={onPlanTrip}>
        Plan a trip
      </button>
    </div>
  )
}

function RouteSkeleton() {
  return (
    <div className="routes" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} className="skeleton-card">
          <span className="skeleton-line wide" />
          <span className="skeleton-line" />
          <span className="skeleton-line bar" />
          <span className="skeleton-line short" />
        </div>
      ))}
    </div>
  )
}
