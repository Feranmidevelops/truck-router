import type { Plan } from '../api/types'
import type { Channel } from '../lib/channel'
import { AlongRoute } from './AlongRoute'
import { PlaceWeather } from './PlaceWeather'
import { RiskBar, RouteFigures, RouteList } from './RouteList'

interface Props {
  plan: Plan | null
  loading: boolean
  error: string | null
  selectedRouteId: string | null
  onSelect: (id: string) => void
  onRetry?: () => void // only when the same request again could work
  onPlanTrip: () => void // back to the form
  hoverChannel?: Channel<string | null> // the checkpoint hovered in the list, for the map
}

// The results panel (the bottom sheet on phones): loading and error states, the weather at
// each end, route cards, checkpoints. Each part is a section with its own heading, so more
// can slot in.
export function PlanResults({
  plan,
  loading,
  error,
  selectedRouteId,
  onSelect,
  onRetry,
  onPlanTrip,
  hoverChannel,
}: Props) {
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
          {selectedRoute && <PlaceWeather route={selectedRoute} timeZone={plan.input.timezone} />}
          <RouteList plan={plan} selectedRouteId={selectedRouteId} onSelect={onSelect} />
          {selectedRoute && (
            <AlongRoute
              route={selectedRoute}
              timeZone={plan.input.timezone}
              intervalMiles={plan.input.interval_miles}
              hoverChannel={hoverChannel}
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
  const recommended = route.id === plan.recommended_route_id
  // As on the cards: with No Travel on every route, the best one is only the least risky
  const noSafeRoute = route.summary.miles_by_level.NO_TRAVEL > 0
  return (
    <div className={loading ? 'peek-route stale' : 'peek-route'}>
      <p className="peek-label">
        <strong>{route.label}</strong>
        {recommended && <span className="tag tag-dark">{noSafeRoute ? 'Least risky' : 'Recommended'}</span>}
        <span className="peek-hint">{loading ? 'Planning the new trip…' : 'Swipe up for details'}</span>
      </p>
      <RouteFigures route={route} />
      <RiskBar route={route} />
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
          <span className="skeleton-line short" />
          <span className="skeleton-figures">
            <span className="skeleton-line figure" />
            <span className="skeleton-line figure narrow" />
          </span>
          <span className="skeleton-line" />
          <span className="skeleton-line bar" />
        </div>
      ))}
    </div>
  )
}
