import { useEffect, useRef } from 'react'
import type { Plan } from '../api/types'
import { CheckpointTable } from './CheckpointTable'
import { RouteList } from './RouteList'

// Same breakpoint as index.css: below it the map sits under the sidebar
const NARROW = '(max-width: 800px)'

interface Props {
  plan: Plan | null
  loading: boolean
  error: string | null
  selectedRouteId: string | null
  onSelect: (id: string) => void
  onRetry?: () => void // only when the same request again could work
}

// Everything under the form: loading and error states, route cards, checkpoint table.
export function PlanResults({ plan, loading, error, selectedRouteId, onSelect, onRetry }: Props) {
  const selectedRoute = plan?.routes.find((r) => r.id === selectedRouteId)
  const ref = useRef<HTMLDivElement>(null)

  // On a phone the new route cards land below the fold, so bring them up once per new plan
  useEffect(() => {
    if (!plan || !window.matchMedia(NARROW).matches) return
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    ref.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' })
  }, [plan])

  return (
    <div ref={ref} className="results">
      {loading && (
        <p className="loading-note" role="status">
          <span className="spinner" aria-hidden />
          Getting routes and forecasts…
        </p>
      )}
      {error && (
        <div className="banner banner-danger plan-error" role="alert">
          <p>{error}</p>
          {onRetry && (
            <button type="button" className="button button-outline" onClick={onRetry}>
              Try again
            </button>
          )}
        </div>
      )}
      {loading && !plan && <RouteSkeleton />}
      {plan && (
        // The previous plan stays on screen, dimmed and inert, until the new one arrives
        <div className={loading ? 'results-body stale' : 'results-body'} inert={loading}>
          <RouteList plan={plan} selectedRouteId={selectedRouteId} onSelect={onSelect} />
          {selectedRoute && <CheckpointTable route={selectedRoute} timeZone={plan.input.timezone} />}
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
