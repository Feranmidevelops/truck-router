import { useRef, useState } from 'react'
import { ApiError, getHeatmap, isAbortError, planTrip } from './api/client'
import type { Plan, PlanRequest } from './api/types'
import type { HeatmapState } from './components/HeatmapSlider'
import { Hero } from './components/Hero'
import { HowItWorks } from './components/HowItWorks'
import { SampleDataBanner } from './components/SampleDataBanner'
import { SiteFooter } from './components/SiteFooter'
import { SiteHeader } from './components/SiteHeader'
import { TripBar } from './components/TripBar'
import { TripForm } from './components/TripForm'
import { Workspace } from './components/Workspace'
import { scrollToElement } from './lib/layout'
import { tripFromPlan, tripFromRequest } from './lib/trip'

const PLAN_TRIP_ID = 'plan-trip'

export default function App() {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  // retry: the same request again could work (a network or server problem). It can't when the
  // server turned the trip down, e.g. a departure in the past or no truck route.
  const [error, setError] = useState<{ message: string; retry: boolean } | null>(null)
  const pending = useRef<AbortController | null>(null)
  const lastRequest = useRef<PlanRequest | null>(null)
  const [planning, setPlanning] = useState<PlanRequest | null>(null) // for the trip bar
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
    setPlanning(req)
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

  // Back up to the form, ready to type a new origin
  function editTrip() {
    const card = document.getElementById(PLAN_TRIP_ID)
    card?.querySelector('input')?.focus({ preventScroll: true })
    scrollToElement(card, 'center')
  }

  const trip = loading && planning ? tripFromRequest(planning) : plan ? tripFromPlan(plan) : null

  return (
    <>
      <SiteHeader />
      <main id="top">
        <Hero cardId={PLAN_TRIP_ID}>
          <SampleDataBanner />
          <TripForm loading={loading} onSubmit={handlePlan} />
        </Hero>
        {/* The trip bar sticks to the top of the window anywhere below the hero */}
        <div className="below-hero">
          <TripBar trip={trip} planning={loading} onEdit={editTrip} />
          <Workspace
            plan={plan}
            loading={loading}
            error={error?.message ?? null}
            onRetry={error?.retry ? () => lastRequest.current && handlePlan(lastRequest.current) : undefined}
            selectedRouteId={selectedRouteId}
            onSelectRoute={setSelectedRouteId}
            heatmap={heatmap}
            onPlanTrip={editTrip}
          />
          <HowItWorks />
        </div>
      </main>
      <SiteFooter />
    </>
  )
}
