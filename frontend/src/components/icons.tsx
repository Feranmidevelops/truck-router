import type { ReactNode } from 'react'
import { skyFromCode, type Sky } from '../lib/report'

// Small line icons, drawn in the text colour. All are decorative: the text next to them
// carries the meaning, so they're hidden from screen readers.

interface IconProps {
  size?: number
  className?: string
}

function Svg({ size = 16, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  )
}

// The wordmark's sign: a route between two stops, on a filled tile
export function LogoMark({ size = 28, className }: IconProps) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="9" fill="currentColor" />
      <path className="logo-route" d="M9 23V18q0-3 3-3h8q3 0 3-3V9" fill="none" strokeWidth="2.6" strokeLinecap="round" />
      <circle className="logo-stop" cx="9" cy="23" r="3" />
      <circle className="logo-stop" cx="23" cy="9" r="3" />
    </svg>
  )
}

export function ArrowRight(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Svg>
  )
}

export function ArrowDown(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 5v14M6 13l6 6 6-6" />
    </Svg>
  )
}

export function ArrowUpRight(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7 17 17 7M8 7h9v9" />
    </Svg>
  )
}

export function ChevronDown(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="m6 9 6 6 6-6" />
    </Svg>
  )
}

export function WindIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 8h10a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h6" />
    </Svg>
  )
}

export function RainIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3s6 6.4 6 10.8A6 6 0 0 1 6 13.8C6 9.4 12 3 12 3Z" />
    </Svg>
  )
}

export function SnowIcon(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 2v20M3.3 7l17.4 10M20.7 7 3.3 17M9 4l3 2 3-2M9 20l3-2 3 2" />
    </Svg>
  )
}

// The sky at a place, from its WMO weather code: sun, cloud, fog, rain, snow or a storm.
// Nothing for an unknown code; the words next to it still say what the forecast is.
export function SkyIcon({ code, ...props }: IconProps & { code: number | null }) {
  const sky = skyFromCode(code)
  return sky ? <Svg {...props}>{SKY_SHAPES[sky]}</Svg> : null
}

// A full cloud, and a smaller one higher up to leave room for what falls from it
const CLOUD = 'M6 18a4 4 0 0 1 0-8 6 6 0 0 1 11.3-1.9A5 5 0 0 1 17 18Z'
const HIGH_CLOUD = 'M7 14a3 3 0 0 1 0-6 4.6 4.6 0 0 1 9-1 3.6 3.6 0 0 1 .5 7Z'

const SKY_SHAPES: Record<Sky, ReactNode> = {
  clear: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>
  ),
  cloud: <path d={CLOUD} />,
  fog: <path d="M4 7h16M3 11h18M5 15h14M8 19h8" />,
  rain: <path d={`${HIGH_CLOUD}M8 17l-1 3M12 17l-1 3M16 17l-1 3`} />,
  snow: <path d={`${HIGH_CLOUD}M8 18h.01M12 18h.01M16 18h.01M10 21h.01M14 21h.01`} />,
  storm: <path d={`${HIGH_CLOUD}M12.5 15.5l-2 3.5h3l-2 3.5`} />,
}
