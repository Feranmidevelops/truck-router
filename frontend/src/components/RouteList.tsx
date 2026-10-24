import { memo } from 'react'
import type { Plan, Route } from '../api/types'
import { LEVELS, LEVEL_COLOR, LEVEL_LABEL, durationParts, formatMiles, formatTime } from '../lib/risk'
import { LevelBadge } from './Level'
import './RouteList.css'

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
        <p key={plan.plan_id} className="banner banner-alert" role="alert">
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
      {plan.routes.map((r) => (
        <RouteCard
          key={r.id}
          route={r}
          tag={r.id === plan.recommended_route_id ? (noSafeRoute ? 'Least risky' : 'Recommended') : null}
          reason={r.id === plan.recommended_route_id ? plan.recommendation_reason : null}
          selected={r.id === selectedRouteId}
          timeZone={plan.input.timezone}
          onSelect={onSelect}
        />
      ))}
    </section>
  )
})

interface CardProps {
  route: Route
  tag: string | null // "Recommended", or "Least risky" when every route has No Travel miles
  reason: string | null // why it's the recommended one
  selected: boolean
  timeZone: string
  onSelect: (id: string) => void
}

// Stat-card style: the two numbers a driver compares first, big; the weather risk under them.
// A button may only hold inline content, so the rows are spans laid out by CSS.
function RouteCard({ route, tag, reason, selected, timeZone, onSelect }: CardProps) {
  const { miles_by_level: miles, avg_risk } = route.summary
  const total = LEVELS.reduce((sum, l) => sum + miles[l], 0) || 1
  const risky = LEVELS.filter((l) => l !== 'LOW' && miles[l] > 0)
  const { h, m } = durationParts(route.duration_s)

  return (
    <button
      type="button"
      className={selected ? 'route-card selected' : 'route-card'}
      aria-pressed={selected}
      onClick={() => onSelect(route.id)}
    >
      <span className="route-head">
        <span className="route-name">{route.label}</span>
        {tag && <span className="tag tag-dark">{tag}</span>}
      </span>
      {/* The units say which is which: no labels, so three cards fit a laptop's panel */}
      <span className="route-figures">
        <span className="route-figure">
          {h > 0 && (
            <>
              {h}
              <small>h</small>{' '}
            </>
          )}
          {m}
          <small>min</small>
        </span>
        <span className="route-figure">
          {route.distance_miles.toFixed(0)}
          <small>mi</small>
        </span>
      </span>
      <span className="route-arrival">Arrives {formatTime(route.arrival_time, timeZone)}</span>
      <span className="risk-bar" aria-hidden>
        {LEVELS.map((l) =>
          miles[l] > 0 ? (
            <span key={l} style={{ width: `${(miles[l] / total) * 100}%`, background: LEVEL_COLOR[l] }} />
          ) : null,
        )}
      </span>
      <span className="route-levels">
        {risky.length ? (
          risky.map((l) => (
            <LevelBadge key={l} level={l}>
              {LEVEL_LABEL[l]} {formatMiles(miles[l])}
            </LevelBadge>
          ))
        ) : (
          <LevelBadge level="LOW">Low all the way</LevelBadge>
        )}
        <span className="route-avg">Avg risk {avg_risk.toFixed(2)}</span>
      </span>
      {reason && <span className="route-reason">{reason}</span>}
    </button>
  )
}
