import { useRef, useState } from 'react'
import { isAbortError, planTrip } from './api/client'
import type { Plan, PlanRequest } from './api/types'
import { MapView } from './components/MapView'
import { PlanResults } from './components/PlanResults'
import { SampleDataBanner } from './components/SampleDataBanner'
import { TripForm } from './components/TripForm'

export default function App() {
  const [plan, setPlan] = useState<Plan | null>(null)
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pending = useRef<AbortController | null>(null)

  async function handlePlan(req: PlanRequest) {
    pending.current?.abort()
    const ctrl = new AbortController()
    pending.current = ctrl
    setLoading(true)
    setError(null)
    try {
      const result = await planTrip(req, ctrl.signal)
      setPlan(result)
      setSelectedRouteId(result.recommended_route_id)
      // TODO(day 3): fetch getHeatmap(result.plan_id) here and pass it to MapView + a 0-48h slider.
    } catch (e) {
      if (!isAbortError(e)) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (pending.current === ctrl) setLoading(false)
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <header>
          <h1>Truck Route Weather</h1>
          <p>Compare three truck routes by the weather you'll drive into, then pick the safest.</p>
        </header>
        <SampleDataBanner />
        <TripForm loading={loading} onSubmit={handlePlan} />
        <PlanResults
          plan={plan}
          loading={loading}
          error={error}
          selectedRouteId={selectedRouteId}
          onSelect={setSelectedRouteId}
        />
      </aside>
      <main className="main">
        <MapView plan={plan} selectedRouteId={selectedRouteId} onSelectRoute={setSelectedRouteId} />
      </main>
    </div>
  )
}
