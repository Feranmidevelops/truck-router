import { PHOTOGRAPHER_URL, PHOTO_URL, REPO_URL } from '../lib/links'
import { LogoMark } from './icons'
import './SiteFooter.css'

export function SiteFooter() {
  return (
    <footer className="site-footer">
      <div className="site-footer-inner">
        <p className="site-footer-brand">
          <LogoMark size={24} />
          Truck Route Weather
        </p>
        <p className="site-footer-sources">
          Routes and place search by TomTom. Forecasts by Open-Meteo. Map by OpenFreeMap, data © OpenStreetMap
          contributors.
        </p>
        <p className="site-footer-credit">
          Photo by <a href={PHOTOGRAPHER_URL}>Mason Gemelke</a> on <a href={PHOTO_URL}>Unsplash</a>
          <span aria-hidden="true"> · </span>
          <a href={REPO_URL}>Source on GitHub</a>
        </p>
      </div>
    </footer>
  )
}
