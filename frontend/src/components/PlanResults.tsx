import type { Plan } from '../api/types'
import { CheckpointTable } from './CheckpointTable'
import { RouteList } from './RouteList'

interface Props {
  plan: Plan | null
  loading: boolean
  error: string | null
  selectedRouteId: string | null
  onSelect: (id: string) => void
}

// Everything under the form: loading and error states, route cards, checkpoint table.
export function PlanResults({ plan, loading, error, selectedRouteId, onSelect }: Props) {
  const selectedRoute = plan?.routes.find((r) => r.id === selectedRouteId)

  return (
    <div className="results">
      {loading && (
        <p className="loading-note" role="status">
          <span className="spinner" aria-hidden />
          Getting routes and forecasts…
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && !plan && <RouteSkeleton />}
      {plan && (
        // The previous plan stays on screen, dimmed and inert, until the new one arrives
        <div className={loading ? 'results-body stale' : 'results-body'} inert={loading}>
          <RouteList plan={plan} selectedRouteId={selectedRouteId} onSelect={onSelect} />
          {selectedRoute && <CheckpointTable route={selectedRoute} />}
        </div>
      )}
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
