import { useEffect, useState } from 'react'
import { getHealth } from '../api/client'

// Shown when the backend runs with USE_FIXTURES, so nobody mistakes the canned trip for live data
export function SampleDataBanner() {
  const [fixtures, setFixtures] = useState(false)

  useEffect(() => {
    const ctrl = new AbortController()
    getHealth(ctrl.signal)
      .then((h) => setFixtures(h.fixtures === true))
      // Nothing to show if health fails; a failing plan request explains itself
      .catch(() => {})
    return () => ctrl.abort()
  }, [])

  if (!fixtures) return null
  return (
    <p className="banner banner-note">
      <strong>Sample data:</strong> every search shows the same Dallas to Denver trip.
    </p>
  )
}
