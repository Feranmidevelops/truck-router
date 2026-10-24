import { useRef, useState } from 'react'
import type { Plan } from '../api/types'
import { createChannel } from '../lib/channel'
import { NARROW_SCREEN, useMediaQuery } from '../lib/layout'
import { BottomSheet } from './BottomSheet'
import type { HeatmapState } from './HeatmapSlider'
import { MapView } from './MapView'
import { PlanPeek, PlanResults } from './PlanResults'
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
  const narrow = useMediaQuery(NARROW_SCREEN)
  const coverRef = useRef<HTMLDivElement>(null)
  // The checkpoint hovered in the list, for the map to ring. Never changes identity.
  const [hoverChannel] = useState(() => createChannel<string | null>(null))

  const results = (
    <PlanResults
      plan={plan}
      loading={loading}
      error={error}
      selectedRouteId={selectedRouteId}
      onSelect={onSelectRoute}
      onRetry={onRetry}
      onPlanTrip={onPlanTrip}
      hoverChannel={hoverChannel}
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
        hoverChannel={hoverChannel}
      />
      {narrow ? (
        <>
          {/* The strip the sheet covers while it's down: the map fits the routes above it */}
          <div ref={coverRef} className="sheet-dock" aria-hidden />
          <BottomSheet
            label="route details"
            resetKey={plan?.plan_id}
            peek={
              <PlanPeek
                plan={plan}
                loading={loading}
                error={error}
                selectedRouteId={selectedRouteId}
                onRetry={onRetry}
              />
            }
          >
            {results}
          </BottomSheet>
        </>
      ) : (
        <div ref={coverRef} className="results-panel">
          {results}
        </div>
      )}
    </section>
  )
}
