import type { ReactNode } from 'react'

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
