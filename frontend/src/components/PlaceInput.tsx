import { useEffect, useId, useState, type KeyboardEvent } from 'react'
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
  const [active, setActive] = useState(-1) // option highlighted with the arrow keys, -1 for none

  useEffect(() => {
    if (value && text === value.label) return
    const q = text.trim()
    if (q.length < 2) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      geocode(q, ctrl.signal)
        .then((places) => {
          setSearch({ q, places, failed: false })
          setActive(-1)
        })
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
  const activePlace = showOptions ? search.places[active] : undefined

  function choose(place: Place) {
    onChange(place)
    setText(place.label)
    close()
  }

  function close() {
    setOpen(false)
    setActive(-1)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    const count = search.places.length
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && searching && count > 0) {
      e.preventDefault()
      const step = e.key === 'ArrowDown' ? 1 : -1
      setOpen(true)
      setActive((i) => (i < 0 || !showOptions ? (step > 0 ? 0 : count - 1) : (i + step + count) % count))
    } else if (e.key === 'Enter' && showOptions) {
      e.preventDefault()
      if (activePlace) choose(activePlace)
      // Nothing highlighted: take the first suggestion, but only if the list answers what's in
      // the box now, not an older query still on screen
      else if (search.q === q) choose(search.places[0])
    } else if (e.key === 'Escape' && showOptions) {
      e.preventDefault()
      close()
    }
  }

  const listId = `${id}-list`

  return (
    <div className="field place-input">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={showOptions}
        aria-controls={showOptions ? listId : undefined}
        aria-activedescendant={activePlace ? `${id}-option-${active}` : undefined}
        value={text}
        autoComplete="off"
        placeholder="City or address"
        onChange={(e) => {
          setText(e.target.value)
          setOpen(true)
          setActive(-1)
          if (value) onChange(null)
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(close, 150)}
        onKeyDown={handleKeyDown}
      />
      {showOptions && (
        <ul id={listId} className="suggestions" role="listbox" aria-label={`${label} suggestions`}>
          {search.places.map((p, i) => (
            <li
              key={`${p.lat},${p.lon}`}
              id={`${id}-option-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
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
