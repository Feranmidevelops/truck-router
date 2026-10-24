import { useRef, useState } from 'react'
import { ApiError, getHeatmap, isAbortError, planTrip } from './api/client'
import type { Plan, PlanRequest } from './api/types'
import type { HeatmapState } from './components/HeatmapSlider'
import { Hero } from './components/Hero'
import { HowItWorks } from './components/HowItWorks'
import { MapView } from './components/MapView'
import { PlanResults } from './components/PlanResults'
import { SampleDataBanner } from './components/SampleDataBanner'
import { SiteHeader } from './components/SiteHeader'
import { TripForm } from './components/TripForm'

export default function App() {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // retry: the same request again could work (a network or server problem). It can't when the
  // server turned the trip down, e.g. a departure in the past or no truck route.
  const [error, setError] = useState<{ message: string; retry: boolean } | null>(null)
  const pending = useRef<AbortController | null>(null)
  const lastRequest = useRef<PlanRequest | null>(null)
  const [heatmap, setHeatmap] = useState<HeatmapState>({ status: 'idle' })
  const heatmapPending = useRef<AbortController | null>(null)

  // Separate, slower request: routes show first and the weather overlay fills in after.
  // Starting one aborts the last, so an old plan's heatmap can never land on a new plan.
  function loadHeatmap(planId: string) {
    heatmapPending.current?.abort()
    const ctrl = new AbortController()
    heatmapPending.current = ctrl
    setHeatmap({ status: 'loading' })
    getHeatmap(planId, ctrl.signal)
      .then((data) => {
        if (!ctrl.signal.aborted) setHeatmap({ status: 'ready', data })
      })
      .catch((e: Error) => {
        if (!ctrl.signal.aborted) setHeatmap({ status: 'error', message: e.message })
      })
  }

  async function handlePlan(req: PlanRequest) {
    lastRequest.current = req
    pending.current?.abort()
    const ctrl = new AbortController()
    pending.current = ctrl
    setLoading(true)
    setError(null)
    try {
      const result = await planTrip(req, ctrl.signal)
      setPlan(result)
      setSelectedRouteId(result.recommended_route_id)
      loadHeatmap(result.plan_id)
    } catch (e) {
      if (!isAbortError(e)) {
        const retry = !(e instanceof ApiError) || e.status === 0 || e.status === 429 || e.status >= 500
        setError({ message: e instanceof Error ? e.message : String(e), retry })
      }
    } finally {
      if (pending.current === ctrl) setLoading(false)
    }
  }

  return (
    <>
      <SiteHeader />
      <main id="top">
        <Hero>
          <SampleDataBanner />
          <TripForm loading={loading} onSubmit={handlePlan} />
        </Hero>
        <section className="app" id="workspace" aria-label="Routes and map">
          <div className="sidebar">
            <PlanResults
              plan={plan}
              loading={loading}
              error={error?.message ?? null}
              selectedRouteId={selectedRouteId}
              onSelect={setSelectedRouteId}
              onRetry={error?.retry ? () => lastRequest.current && handlePlan(lastRequest.current) : undefined}
            />
          </div>
          <div className="map-area">
            <MapView
              plan={plan}
              loading={loading}
              selectedRouteId={selectedRouteId}
              onSelectRoute={setSelectedRouteId}
              heatmap={heatmap}
            />
          </div>
        </section>
        <HowItWorks />
      </main>
    </>
  )
}
