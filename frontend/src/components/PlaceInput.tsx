import { useEffect, useId, useState } from 'react'
import { geocode } from '../api/client'
import type { Place } from '../api/types'

interface Props {
  label: string
  value: Place | null
  onChange: (place: Place | null) => void
}

export function PlaceInput({ label, value, onChange }: Props) {
  const id = useId()
  const [text, setText] = useState(value?.label ?? '')
  const [options, setOptions] = useState<Place[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (value && text === value.label) return
    const q = text.trim()
    if (q.length < 2) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      geocode(q, ctrl.signal)
        .then((r) => setOptions(r.results))
        .catch(() => setOptions([]))
    }, 250)
    return () => {
      clearTimeout(timer)
      ctrl.abort()
    }
  }, [text, value])

  const showOptions = open && text.trim().length >= 2 && options.length > 0

  function choose(place: Place) {
    onChange(place)
    setText(place.label)
    setOpen(false)
  }

  return (
    <div className="field place-input">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        value={text}
        autoComplete="off"
        placeholder="City or address"
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
          if (value) onChange(null)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && showOptions) {
            e.preventDefault()
            choose(options[0])
          }
        }}
      />
      {showOptions && (
        <ul className="suggestions" role="listbox">
          {options.map((p) => (
            <li key={`${p.lat},${p.lon}`} role="option" aria-selected={false} onMouseDown={() => choose(p)}>
              {p.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
