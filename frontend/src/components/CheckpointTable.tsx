import { memo, useMemo } from 'react'
import type { Checkpoint, CheckpointRisk, Route } from '../api/types'
import { LEVEL_COLOR, LEVEL_LABEL, type TimeFormatter } from '../lib/risk'
import type { Highlight } from './AlongRoute'

interface Props {
  id: string
  route: Route
  time: TimeFormatter // in the plan's zone (the origin's)
  zone: string // that zone's name at departure, for the header
  highlight: Highlight // a hovered row's checkpoint gets a ring on the map
}

const COLUMNS = 6

// Every checkpoint on the route, grouped by day. The values that set a checkpoint's level
// are bold. memo: only re-renders when the selected route changes. Rows are keyed by
// checkpoint index, so switching routes updates the existing rows in place.
export const CheckpointTable = memo(function CheckpointTable({ id, route, time, zone, highlight }: Props) {
  const days = useMemo(() => groupByDay(route.checkpoints, time), [route, time])

  return (
    <div id={id} className="checkpoints">
      <table className="checkpoint-table">
        <caption className="visually-hidden">Every checkpoint on {route.label}</caption>
        <thead>
          <tr>
            <th scope="col">
              Time <span className="unit">{zone}</span>
            </th>
            <th scope="col" className="num">
              Mile
            </th>
            <th scope="col" className="num">
              Wind <span className="unit">mph</span>
            </th>
            <th scope="col" className="num">
              Rain <span className="unit">in/h</span>
            </th>
            <th scope="col" className="num">
              Snow <span className="unit">in/h</span>
            </th>
            <th scope="col">Level</th>
          </tr>
        </thead>
        {days.map(({ day, checkpoints }) => (
          <tbody key={day}>
            <tr className="day-row">
              <th scope="rowgroup" colSpan={COLUMNS}>
                {day}
              </th>
            </tr>
            {checkpoints.map((c) => {
              // Only differs from the header when the trip crosses a daylight saving change
              const rowZone = time.zone(c.eta)
              const { weather: w, risk } = c
              return (
                <tr key={c.index} onMouseEnter={() => highlight(c.index)} onMouseLeave={() => highlight(null)}>
                  <td>
                    {time.clock(c.eta)}
                    {rowZone !== zone && ` ${rowZone}`}
                  </td>
                  <td className="num">{c.mile.toFixed(0)}</td>
                  <td className={cell(risk, 'wind')}>
                    {w.wind_mph.toFixed(0)}
                    {Math.round(w.gust_mph) > Math.round(w.wind_mph) && (
                      <span className="gust"> ({w.gust_mph.toFixed(0)})</span>
                    )}
                  </td>
                  <td className={cell(risk, 'rain')}>{w.rain_in_hr.toFixed(2)}</td>
                  <td className={cell(risk, 'snow')}>{w.snow_in_hr.toFixed(2)}</td>
                  <td>
                    <span className="level">
                      <span className="level-dot" style={{ background: LEVEL_COLOR[risk.level] }} />
                      {LEVEL_LABEL[risk.level]}
                      {risk.load_adjusted && (
                        <abbr className="load-mark" title="Wind level raised for the load weight">
                          *
                        </abbr>
                      )}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        ))}
      </table>
      <p className="table-note">
        Bold: the reading that sets the level. Wind gusts in brackets.
        {route.checkpoints.some((c) => c.risk.load_adjusted) && ' * Wind level raised for the load weight.'}
      </p>
    </div>
  )
})

function cell(risk: CheckpointRisk, factor: CheckpointRisk['drivers'][number]) {
  return risk.drivers.includes(factor) ? 'num driver' : 'num'
}

function groupByDay(checkpoints: Checkpoint[], time: TimeFormatter) {
  const days: Array<{ day: string; checkpoints: Checkpoint[] }> = []
  for (const c of checkpoints) {
    const day = time.day(c.eta)
    const last = days.at(-1)
    if (last?.day === day) last.checkpoints.push(c)
    else days.push({ day, checkpoints: [c] })
  }
  return days
}
