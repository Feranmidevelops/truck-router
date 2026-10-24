import type { Heatmap, Place, Plan, PlanRequest } from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

const OFFLINE_MESSAGE = "Can't reach the server. Check your connection and try again."
// A live plan takes 5-15 s and a heatmap a few seconds; past these something is stuck
const PLAN_TIMEOUT_S = 45
const HEATMAP_TIMEOUT_S = 30

export class ApiError extends Error {
  readonly status: number // 0 when no HTTP response came back
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

// GET /api/health. Not part of the plan contract, so it lives here rather than in types.ts.
export interface Health {
  ok: boolean
  fixtures: boolean // the API serves the canned Dallas -> Denver trip
}

// The caller cancelled (a newer search or plan replaced this one). Not worth showing.
export function isAbortError(e: unknown) {
  return e instanceof Error && e.name === 'AbortError'
}

// timeoutS: give up after this long, as well as when init.signal aborts
async function request<T>(path: string, init: RequestInit = {}, timeoutS?: number): Promise<T> {
  const headers = new Headers(init.headers)
  // Only with a body: on a cross-origin GET this header would force a CORS preflight per request
  if (init.body != null) headers.set('Content-Type', 'application/json')
  let signal = init.signal
  if (timeoutS) {
    const timeout = AbortSignal.timeout(timeoutS * 1000)
    signal = signal ? AbortSignal.any([signal, timeout]) : timeout
  }

  let res: Response
  let text: string
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers, signal })
    text = await res.text()
  } catch (e) {
    if (isAbortError(e)) throw e
    // AbortSignal.timeout() rejects with a TimeoutError rather than an AbortError
    if (e instanceof Error && e.name === 'TimeoutError') {
      throw new ApiError(0, `The server took longer than ${timeoutS} seconds to answer. Try again in a moment.`)
    }
    throw new ApiError(0, OFFLINE_MESSAGE)
  }

  const body = parseJson(text)
  if (!res.ok) throw new ApiError(res.status, errorMessage(res.status, body))
  // e.g. a misconfigured deploy answering /api/... with index.html
  if (!isObject(body)) {
    throw new ApiError(res.status, 'The server sent an unexpected response. Try again in a moment.')
  }
  return body as T
}

function parseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return undefined
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function errorMessage(status: number, body: unknown): string {
  const detail = isObject(body) && typeof body.detail === 'string' ? body.detail : undefined
  if (status === 429) {
    // DRF: "Request was throttled. Expected available in 42 seconds."
    const wait = detail?.match(/(\d+) seconds?/)?.[1]
    return wait
      ? `Too many requests. Try again in ${wait} second${wait === '1' ? '' : 's'}.`
      : 'Too many requests. Try again in a minute.'
  }
  if (detail) return detail
  const fields = validationMessages(body)
  if (fields.length) return fields.join(' ')
  if (status >= 500) return `The server had a problem (${status}). Try again in a moment.`
  return `Request failed (${status}).`
}

const FIELD_LABEL: Record<string, string> = {
  origin: 'Origin',
  destination: 'Destination',
  departure_time: 'Departure',
  load_weight_lb: 'Load',
  interval_miles: 'Weather interval',
}

// DRF validation errors: {field: ["..."]}, {origin: {lat: ["..."]}} or {non_field_errors: ["..."]}
function validationMessages(body: unknown): string[] {
  if (!isObject(body)) return []
  return Object.entries(body).flatMap(([key, value]) => {
    const text = leafMessages(value).join(' ')
    if (!text) return []
    if (key === 'non_field_errors') return [text]
    const label = FIELD_LABEL[key] ?? key.charAt(0).toUpperCase() + key.slice(1).replaceAll('_', ' ')
    // "Departure time is in the past." already names its field
    return [text.startsWith(label) ? text : `${label}: ${text}`]
  })
}

function leafMessages(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(leafMessages)
  if (isObject(value)) return Object.values(value).flatMap(leafMessages)
  return []
}

export function getHealth(signal?: AbortSignal) {
  return request<Health>('/api/health', { signal })
}

export async function geocode(q: string, signal?: AbortSignal): Promise<Place[]> {
  const body = await request<{ results?: Place[] }>(`/api/geocode?q=${encodeURIComponent(q)}`, { signal })
  return Array.isArray(body.results) ? body.results : []
}

export function planTrip(body: PlanRequest, signal?: AbortSignal) {
  return request<Plan>('/api/plan', { method: 'POST', body: JSON.stringify(body), signal }, PLAN_TIMEOUT_S)
}

export function getHeatmap(planId: string, signal?: AbortSignal) {
  return request<Heatmap>(`/api/heatmap?plan_id=${encodeURIComponent(planId)}`, { signal }, HEATMAP_TIMEOUT_S)
}
