import { memo } from 'react'
import type { Plan } from '../api/types'
import { LEVELS, LEVEL_COLOR, LEVEL_LABEL, formatDuration, formatMiles, formatTime } from '../lib/risk'

interface Props {
  plan: Plan
  selectedRouteId: string | null
  onSelect: (id: string) => void
}

// memo: App re-renders for state these cards don't show (loading, later the heatmap hour)
export const RouteList = memo(function RouteList({ plan, selectedRouteId, onSelect }: Props) {
  const best = plan.routes.find((r) => r.id === plan.recommended_route_id)
  // No Travel miles are the first ranking key, so if the best route has any, every route does
  const noSafeRoute = (best?.summary.miles_by_level.NO_TRAVEL ?? 0) > 0

  return (
    <section className="routes" aria-label="Route options">
      {noSafeRoute && (
        <p key={plan.plan_id} className="banner banner-danger" role="alert">
          Every route crosses No Travel conditions. Consider a later departure.
        </p>
      )}
      {plan.routes.length < 3 && (
        <p className="routes-note">
          {plan.routes.length === 1
            ? 'TomTom found only one route for this trip.'
            : `TomTom found only ${plan.routes.length} distinct routes for this trip.`}
        </p>
      )}
      {plan.routes.map((r) => {
        const recommended = r.id === plan.recommended_route_id
        const total = LEVELS.reduce((sum, l) => sum + r.summary.miles_by_level[l], 0) || 1
        // A button may only hold inline content, so the card's rows are spans; the card is a
        // flex column, which lays them out as blocks
        return (
          <button
            key={r.id}
            type="button"
            className={`route-card${r.id === selectedRouteId ? ' selected' : ''}`}
            onClick={() => onSelect(r.id)}
            aria-pressed={r.id === selectedRouteId}
          >
            <span className="route-head">
              <strong>{r.label}</strong>
              {recommended && (
                <span className={noSafeRoute ? 'badge badge-danger' : 'badge'}>
                  {noSafeRoute ? 'Least risky' : 'Recommended'}
                </span>
              )}
            </span>
            <span className="route-stats">
              <span>{r.distance_miles.toFixed(0)} mi</span>
              <span>{formatDuration(r.duration_s)}</span>
              <span>Arrive {formatTime(r.arrival_time)}</span>
            </span>
            <span className="risk-bar" aria-hidden>
              {LEVELS.map((l) =>
                r.summary.miles_by_level[l] > 0 ? (
                  <span
                    key={l}
                    style={{ width: `${(r.summary.miles_by_level[l] / total) * 100}%`, background: LEVEL_COLOR[l] }}
                  />
                ) : null,
              )}
            </span>
            <span className="route-risk">
              {LEVELS.filter((l) => l !== 'LOW' && r.summary.miles_by_level[l] > 0).map((l) => (
                <span key={l}>
                  {LEVEL_LABEL[l]}: {formatMiles(r.summary.miles_by_level[l])}
                </span>
              ))}
              <span>Avg risk {r.summary.avg_risk.toFixed(2)}</span>
            </span>
            {recommended && <span className="reason">{plan.recommendation_reason}</span>}
          </button>
        )
      })}
    </section>
  )
})
