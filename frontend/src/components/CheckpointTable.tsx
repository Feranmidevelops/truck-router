import { memo, useMemo } from 'react'
import type { Checkpoint, Route } from '../api/types'
import { LEVEL_COLOR, LEVEL_LABEL, timeFormatter, type TimeFormatter } from '../lib/risk'

interface Props {
  route: Route
}

const COLUMNS = 7

// memo: only re-renders when the selected route changes. Rows are keyed by checkpoint index,
// so switching routes updates the existing rows in place instead of rebuilding the table.
export const CheckpointTable = memo(function CheckpointTable({ route }: Props) {
  const time = useMemo(() => timeFormatter(), [])
  const days = useMemo(() => groupByDay(route.checkpoints, time), [route, time])
  const zone = route.checkpoints.length ? time.zone(route.checkpoints[0].eta) : ''

  return (
    <section className="checkpoints" aria-labelledby="checkpoints-title">
      <h2 id="checkpoints-title">
        Checkpoints <span className="checkpoints-route">{route.label}</span>
      </h2>
      <table className="checkpoint-table">
        <thead>
          <tr>
            <th scope="col" className="num">
              Mile
            </th>
            <th scope="col">
              ETA <span className="unit">{zone}</span>
            </th>
            <th scope="col" className="num">
              Wind <span className="unit">mph (gust)</span>
            </th>
            <th scope="col" className="num">
              Rain <span className="unit">in/h</span>
            </th>
            <th scope="col" className="num">
              Snow <span className="unit">in/h</span>
            </th>
            <th scope="col">Level</th>
            <th scope="col">Drivers</th>
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
              return (
                <tr key={c.index}>
                  <td className="num">{c.mile.toFixed(0)}</td>
                  <td>
                    {time.clock(c.eta)}
                    {rowZone !== zone && ` ${rowZone}`}
                  </td>
                  <td className="num">
                    {c.weather.wind_mph.toFixed(0)} <span className="gust">({c.weather.gust_mph.toFixed(0)})</span>
                  </td>
                  <td className="num">{c.weather.rain_in_hr.toFixed(2)}</td>
                  <td className="num">{c.weather.snow_in_hr.toFixed(2)}</td>
                  <td>
                    <span className="level">
                      <span className="level-dot" style={{ background: LEVEL_COLOR[c.risk.level] }} />
                      {LEVEL_LABEL[c.risk.level]}
                      {c.risk.load_adjusted && (
                        <abbr className="load-mark" title="Wind level raised for the load weight">
                          *
                        </abbr>
                      )}
                    </span>
                  </td>
                  <td className="drivers">{c.risk.drivers.join(', ')}</td>
                </tr>
              )
            })}
          </tbody>
        ))}
      </table>
      {route.checkpoints.some((c) => c.risk.load_adjusted) && (
        <p className="table-note">* Wind level raised for the load weight.</p>
      )}
    </section>
  )
})

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
