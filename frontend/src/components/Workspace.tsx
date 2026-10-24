import { useRef } from 'react'
import type { Plan } from '../api/types'
import type { HeatmapState } from './HeatmapSlider'
import { MapView } from './MapView'
import { PlanResults } from './PlanResults'
import './Workspace.css'

interface Props {
  plan: Plan | null
  loading: boolean
  error: string | null
  onRetry?: () => void
  selectedRouteId: string | null
  onSelectRoute: (id: string) => void
  heatmap: HeatmapState
  onPlanTrip: () => void
}

// The map fills the space below the trip bar. The results float over it: a panel on the left
// on wide screens, a sheet from the bottom on phones. Either way the map keeps the routes
// clear of them (see fitPadding in MapView).
export function Workspace({ plan, loading, error, onRetry, selectedRouteId, onSelectRoute, heatmap, onPlanTrip }: Props) {
  const coverRef = useRef<HTMLDivElement>(null)

  const results = (
    <PlanResults
      plan={plan}
      loading={loading}
      error={error}
      selectedRouteId={selectedRouteId}
      onSelect={onSelectRoute}
      onRetry={onRetry}
      onPlanTrip={onPlanTrip}
    />
  )

  return (
    <section id="workspace" className="workspace" aria-label="Routes and map">
      <MapView
        plan={plan}
        loading={loading}
        selectedRouteId={selectedRouteId}
        onSelectRoute={onSelectRoute}
        heatmap={heatmap}
        coverRef={coverRef}
      />
      <div ref={coverRef} className="results-panel">
        {results}
      </div>
    </section>
  )
}
