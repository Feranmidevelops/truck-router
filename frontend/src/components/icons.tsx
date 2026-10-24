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
      <path
        className="logo-route"
        d="M9.5 22.5c0-6 13-4 13-13"
        fill="none"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeDasharray="0.01 4.6"
      />
      <circle className="logo-stop" cx="9.5" cy="22.5" r="2.6" />
      <circle className="logo-stop" cx="22.5" cy="9.5" r="2.6" />
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
