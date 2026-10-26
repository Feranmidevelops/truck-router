import { useId, useLayoutEffect, useRef, useState, type MouseEvent, type PointerEvent, type ReactNode } from 'react'
import './BottomSheet.css'

interface Props {
  label: string // what the sheet holds, for the handle's name: "route details"
  peek: ReactNode // shown while the sheet is down
  children: ReactNode // the whole content, shown when the sheet is up
  resetKey?: string // when this changes (a new plan, a stretch to see on the map), the sheet drops back down
}

// How far the finger has to travel before a press on the handle counts as a drag, not a tap
const DRAG_START_PX = 6
// A drag shorter than this snaps back to where it started
const SNAP_PX = 48

// Phones: the results slide up over the map. Down, it shows a one-line summary and the map
// stays clear; up, it holds everything the desktop side panel does. Drag the top edge or tap it.
export function BottomSheet({ label, peek, children, resetKey }: Props) {
  const bodyId = useId()
  const [open, setOpen] = useState(false)
  // A new plan drops the sheet so the map shows the new routes. Adjusted while rendering
  // rather than in an effect, so there's no extra render.
  const [shownKey, setShownKey] = useState(resetKey)
  if (resetKey !== shownKey) {
    setShownKey(resetKey)
    setOpen(false)
  }

  const sheetRef = useRef<HTMLDivElement>(null)
  const handleRef = useRef<HTMLButtonElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  // Dropping the sheet with focus inside it (a stretch picked from the keyboard) would leave
  // focus on nothing once the body goes inert: hand it to the handle. Before paint, while
  // the focused element is still the one inside.
  useLayoutEffect(() => {
    if (!open && bodyRef.current?.contains(document.activeElement)) handleRef.current?.focus()
  }, [open])
  const drag = useRef<{ startY: number; moved: boolean } | null>(null)
  const dragged = useRef(false) // the click that ends a drag shouldn't also toggle

  // While dragging, the sheet follows the finger through an inline transform (no re-renders)
  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return
    drag.current = { startY: e.clientY, moved: false }
    dragged.current = false
  }

  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    const d = drag.current
    const sheet = sheetRef.current
    if (!d || !sheet) return
    const dy = e.clientY - d.startY
    if (!d.moved) {
      if (Math.abs(dy) < DRAG_START_PX) return
      d.moved = true
      e.currentTarget.setPointerCapture(e.pointerId)
      sheet.dataset.dragging = ''
    }
    const down = closedOffset(sheet)
    const y = Math.min(down, Math.max(0, (open ? 0 : down) + dy))
    sheet.style.transform = `translateY(${y}px)`
  }

  function onPointerEnd(e: PointerEvent<HTMLDivElement>) {
    const d = drag.current
    const sheet = sheetRef.current
    drag.current = null
    if (!d?.moved || !sheet) return
    // Swallow the click this pointerup may fire; clicks are dispatched before timers run
    dragged.current = true
    setTimeout(() => (dragged.current = false))
    delete sheet.dataset.dragging
    sheet.style.transform = ''
    const dy = e.clientY - d.startY
    if (Math.abs(dy) >= SNAP_PX) setOpen(dy < 0)
  }

  function toggle() {
    if (dragged.current) dragged.current = false
    else setOpen((o) => !o)
  }

  // Tapping the summary opens the sheet, unless the tap was on one of its own buttons
  function openFromPeek(e: MouseEvent<HTMLDivElement>) {
    if (e.target instanceof Element && e.target.closest('button')) return
    if (dragged.current) dragged.current = false
    else setOpen(true)
  }

  return (
    <div ref={sheetRef} className="sheet" data-open={open || undefined}>
      <div
        className="sheet-head"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
      >
        <button
          ref={handleRef}
          type="button"
          className="sheet-handle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={toggle}
        >
          <span className="sheet-grip" aria-hidden />
          <span className="visually-hidden">
            {open ? 'Hide' : 'Show'} {label}
          </span>
        </button>
        {!open && (
          <div className="sheet-peek" onClick={openFromPeek}>
            {peek}
          </div>
        )}
      </div>
      {/* Inert while down: its contents sit off-screen and mustn't take focus */}
      <div ref={bodyRef} id={bodyId} className="sheet-body" inert={!open}>
        {children}
      </div>
    </div>
  )
}

// The sheet's offset when down: its height less the part that peeks out
function closedOffset(sheet: HTMLElement) {
  const peek = parseFloat(getComputedStyle(sheet).getPropertyValue('--sheet-peek')) || 0
  return Math.max(0, sheet.offsetHeight - peek)
}
