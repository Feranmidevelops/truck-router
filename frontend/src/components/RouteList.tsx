import type { Plan } from '../api/types'
import { LEVELS, LEVEL_COLOR, LEVEL_LABEL, formatDuration, formatTime } from '../lib/risk'

interface Props {
  plan: Plan
  selectedRouteId: string | null
  onSelect: (id: string) => void
}

// TODO(day 2): checkpoint table for the selected route (mile, ETA, conditions, level).
export function RouteList({ plan, selectedRouteId, onSelect }: Props) {
  return (
    <section className="routes" aria-label="Route options">
      {plan.routes.map((r) => {
        const recommended = r.id === plan.recommended_route_id
        const total = LEVELS.reduce((sum, l) => sum + r.summary.miles_by_level[l], 0) || 1
        return (
          <button
            key={r.id}
            type="button"
            className={`route-card${r.id === selectedRouteId ? ' selected' : ''}`}
            onClick={() => onSelect(r.id)}
            aria-pressed={r.id === selectedRouteId}
          >
            <div className="route-head">
              <strong>{r.label}</strong>
              {recommended && <span className="badge">Recommended</span>}
            </div>
            <div className="route-stats">
              <span>{r.distance_miles.toFixed(0)} mi</span>
              <span>{formatDuration(r.duration_s)}</span>
              <span>Arrive {formatTime(r.arrival_time)}</span>
            </div>
            <div className="risk-bar" aria-hidden>
              {LEVELS.map((l) =>
                r.summary.miles_by_level[l] > 0 ? (
                  <span
                    key={l}
                    style={{ width: `${(r.summary.miles_by_level[l] / total) * 100}%`, background: LEVEL_COLOR[l] }}
                  />
                ) : null,
              )}
            </div>
            <div className="route-risk">
              {LEVELS.filter((l) => l !== 'LOW' && r.summary.miles_by_level[l] > 0).map((l) => (
                <span key={l}>
                  {LEVEL_LABEL[l]}: {r.summary.miles_by_level[l].toFixed(0)} mi
                </span>
              ))}
              <span>Avg risk {r.summary.avg_risk.toFixed(2)}</span>
            </div>
            {recommended && <p className="reason">{plan.recommendation_reason}</p>}
          </button>
        )
      })}
    </section>
  )
}
