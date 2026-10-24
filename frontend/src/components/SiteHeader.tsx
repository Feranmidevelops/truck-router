import { REPO_URL } from '../lib/links'
import { ArrowUpRight, LogoMark } from './icons'
import './SiteHeader.css'

// The slim bar over the hero photo: wordmark and two links
export function SiteHeader() {
  return (
    <header className="site-header">
      <a className="wordmark" href="#top" aria-label="Truck Route Weather, back to the top">
        <LogoMark />
        <span>Truck Route Weather</span>
      </a>
      <nav className="site-nav" aria-label="Main">
        <a className="site-nav-how" href="#how-it-works">
          How it works
        </a>
        <a className="site-nav-pill" href={REPO_URL} target="_blank" rel="noreferrer">
          GitHub
          <span className="visually-hidden"> (opens in a new tab)</span>
          <ArrowUpRight size={14} />
        </a>
      </nav>
    </header>
  )
}
