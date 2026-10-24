import type { CSSProperties, ReactNode } from 'react'
import type { RiskLevel } from '../api/types'
import { LEVEL_COLOR, LEVEL_LABEL, levelTint } from '../lib/risk'
import './Level.css'

// A pill in the level's colour: a solid dot on a faint tint of it, with black text so it
// reads at any level. Children replace the level's name, e.g. "Severe 25.0 mi".
export function LevelBadge({ level, children }: { level: RiskLevel; children?: ReactNode }) {
  const style = { '--level': LEVEL_COLOR[level], '--level-tint': levelTint(level, 0.14) } as CSSProperties
  return (
    <span className="level-badge" style={style}>
      <span className="level-badge-dot" aria-hidden />
      {children ?? LEVEL_LABEL[level]}
    </span>
  )
}
