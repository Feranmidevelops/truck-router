import type { ReactNode } from 'react'
import { ArrowDown } from './icons'
import './Hero.css'

interface Props {
  children: ReactNode // the trip form, shown in the card
}

// Facts about this app, not marketing numbers
const STATS = [
  { value: '3', label: 'truck routes compared' },
  { value: '10–50 mi', label: 'between weather checks' },
  { value: '48 h', label: 'of forecast on the map' },
  { value: '5', label: 'risk levels, Low to No Travel' },
]

// The first screen: photo, headline, the trip form and a row of facts.
// The photo files are preloaded from index.html; keep the two lists in step.
export function Hero({ children }: Props) {
  return (
    <section className="hero" aria-labelledby="hero-title">
      <picture className="hero-photo">
        <source
          media="(max-width: 640px)"
          srcSet="/images/truck-highway-portrait-960.webp 960w, /images/truck-highway-portrait-1440.webp 1440w"
          sizes="100vw"
          width={960}
          height={1478}
        />
        <img
          src="/images/truck-highway-1920.webp"
          srcSet="/images/truck-highway-1280.webp 1280w, /images/truck-highway-1920.webp 1920w"
          sizes="100vw"
          width={1920}
          height={1080}
          alt="A white semi truck on a lakeside highway, with a snow-capped mountain behind"
          fetchPriority="high"
        />
      </picture>
      <div className="hero-inner">
        <div className="hero-copy">
          <h1 id="hero-title" className="hero-title display">
            Route around the storm, not through it.
          </h1>
          <p className="hero-lede">
            Compare three truck routes by the wind, rain and snow forecast for the hour you reach each stretch of
            road.
          </p>
          <a className="hero-link" href="#how-it-works">
            How it works
            <ArrowDown size={16} />
          </a>
        </div>
        <div className="hero-card" id="plan-trip">
          <h2 className="hero-card-title">Plan a trip</h2>
          {children}
        </div>
      </div>
      <ul className="hero-stats">
        {STATS.map((s) => (
          <li key={s.label}>
            <span className="hero-stat-value display">{s.value}</span>
            <span className="hero-stat-label">{s.label}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
