import { useEffect, useId, useState } from 'react'
import { geocode, isAbortError } from '../api/client'
import type { Place } from '../api/types'

interface Props {
  label: string
  value: Place | null
  onChange: (place: Place | null) => void
}

// The latest finished search: what was asked, and what came back
interface Search {
  q: string
  places: Place[]
  failed: boolean
}

export function PlaceInput({ label, value, onChange }: Props) {
  const id = useId()
  const [text, setText] = useState(value?.label ?? '')
  const [search, setSearch] = useState<Search>({ q: '', places: [], failed: false })
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (value && text === value.label) return
    const q = text.trim()
    if (q.length < 2) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      geocode(q, ctrl.signal)
        .then((places) => setSearch({ q, places, failed: false }))
        .catch((e: unknown) => {
          if (!isAbortError(e)) setSearch({ q, places: [], failed: true })
        })
    }, 250)
    return () => {
      clearTimeout(timer)
      ctrl.abort()
    }
  }, [text, value])

  const q = text.trim()
  const searching = !value && q.length >= 2
  const showOptions = open && searching && search.places.length > 0
  const noMatches = open && searching && search.q === q && !search.failed && search.places.length === 0

  function choose(place: Place) {
    onChange(place)
    setText(place.label)
    setOpen(false)
  }

  const listId = `${id}-list`

  return (
    <div className="field place-input">
      <label id={`${id}-label`} htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showOptions}
        aria-controls={showOptions ? listId : undefined}
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
          if (e.key !== 'Enter' || !showOptions) return
          e.preventDefault()
          // Only when the list answers what's in the box now, not an older query still on screen
          if (search.q === q) choose(search.places[0])
        }}
      />
      {showOptions && (
        <ul id={listId} className="suggestions" role="listbox" aria-labelledby={`${id}-label`}>
          {search.places.map((p, i) => (
            <li
              key={`${p.lat},${p.lon}`}
              id={`${id}-option-${i}`}
              role="option"
              aria-selected={false}
              onMouseDown={() => choose(p)}
            >
              {p.label}
            </li>
          ))}
        </ul>
      )}
      {(searching && search.failed) || noMatches ? (
        <p className={search.failed ? 'place-status failed' : 'place-status'} role="status">
          {search.failed ? "Place search isn't working right now." : 'No places found.'}
        </p>
      ) : null}
    </div>
  )
}
