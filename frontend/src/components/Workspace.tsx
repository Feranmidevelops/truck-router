import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import type { Plan } from '../api/types'
import { createChannel } from '../lib/channel'
import { NARROW_SCREEN, useMediaQuery } from '../lib/layout'
import { afterLoadWhenIdle, loadMapView } from '../lib/loadMap'
import { BottomSheet } from './BottomSheet'
import { ErrorBoundary } from './ErrorBoundary'
import type { HeatmapState } from './HeatmapSlider'
import { PlanPeek, PlanResults } from './PlanResults'
import './Workspace.css'

const MapView = lazy(() => loadMapView().then((m) => ({ default: m.MapView })))

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

  // The map loads once the first screen has settled, as the workspace scrolls near, or as
  // soon as a plan is on its way, whichever comes first. Until then a light grid holds its place.
  const sectionRef = useRef<HTMLElement>(null)
  const [mapWanted, setMapWanted] = useState(false)
  const showMap = mapWanted || loading || plan !== null
  useEffect(() => {
    if (showMap) return
    const want = () => setMapWanted(true)
    const cancelIdle = afterLoadWhenIdle(want)
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) want()
      },
      { rootMargin: '50% 0px' },
    )
    if (sectionRef.current) observer.observe(sectionRef.current)
    return () => {
      cancelIdle()
      observer.disconnect()
    }
  }, [showMap])

  return (
    <section ref={sectionRef} id="workspace" className="workspace" aria-label="Routes and map">
      {showMap ? (
        <ErrorBoundary fallback={<MapPlaceholder failed />}>
          <Suspense fallback={<MapPlaceholder />}>
            <MapView
              plan={plan}
              loading={loading}
              selectedRouteId={selectedRouteId}
              onSelectRoute={onSelectRoute}
              heatmap={heatmap}
              coverRef={coverRef}
              hoverChannel={hoverChannel}
            />
          </Suspense>
        </ErrorBoundary>
      ) : (
        <MapPlaceholder />
      )}
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

// Same size as the map, so nothing moves when it arrives
function MapPlaceholder({ failed = false }: { failed?: boolean }) {
  return (
    <div className="map-wrap map-placeholder">
      {failed ? (
        <p className="map-placeholder-note" role="status">
          The map didn't load, but the route list still works. Reload the page to try again.
        </p>
      ) : (
        <p className="map-placeholder-note" aria-hidden>
          Loading the map…
        </p>
      )}
    </div>
  )
}
