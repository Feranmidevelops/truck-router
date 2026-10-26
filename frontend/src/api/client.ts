import type { ForecastRequest, Heatmap, Place, Plan, PlanRequest } from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

const OFFLINE_MESSAGE = "Can't reach the server. Check your connection and try again."
const WEATHER_DOWN = "Couldn't get the weather forecast from Open-Meteo. Try again in a minute."
const WEATHER_MINUTE = "Open-Meteo's per-minute forecast limit for your network is reached. Try again in a minute."
const WEATHER_LIMIT =
  "Open-Meteo's free forecast limit for your network is used up for now. Try again later or from another network."
// A live plan takes 5-15 s (coast to coast about 30) and a heatmap a few seconds. Each request to
// the server gets the first two; the whole plan or heatmap, forecasts included, the totals.
const PLAN_TIMEOUT_S = 45
const HEATMAP_TIMEOUT_S = 30
const PLAN_TOTAL_S = 120 // room for a free Render server waking up
const HEATMAP_TOTAL_S = 60
// For each Open-Meteo request, and the wait before its one retry
const FORECAST_TIMEOUT_S = 20
const FORECAST_RETRY_MS = 2000
// Open-Meteo answers "Too many concurrent requests" past about 5 at once from one address
const FORECASTS_AT_ONCE = 4
// Replies are kept this long, so planning the same trip again (another load, say) costs no new
// forecast locations: Open-Meteo counts every one against its per-minute and daily limits.
const FORECAST_KEEP_MS = 30 * 60 * 1000
const FORECASTS_KEPT = 30 // about 50 locations each

export class ApiError extends Error {
  readonly status: number // 0 when no HTTP response came back
  // Whether trying again can help, where that isn't what the status suggests
  readonly retry?: boolean
  constructor(status: number, message: string, retry?: boolean) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retry = retry
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

export function planTrip(body: PlanRequest, signal?: AbortSignal): Promise<Plan> {
  return withDeadline('Planning', PLAN_TOTAL_S, signal, async (signal) => {
    const init = { method: 'POST', body: JSON.stringify(body), signal }
    const first = await request<Plan | ForecastRequest>('/api/plan', init, PLAN_TIMEOUT_S)
    if (!needsForecasts(first)) return first
    return sendForecasts<Plan>('/api/plan/forecasts', first, PLAN_TIMEOUT_S, signal)
  })
}

export function getHeatmap(planId: string, signal?: AbortSignal): Promise<Heatmap> {
  return withDeadline('Loading the forecast', HEATMAP_TOTAL_S, signal, async (signal) => {
    const path = `/api/heatmap?plan_id=${encodeURIComponent(planId)}`
    const first = await request<Heatmap | ForecastRequest>(path, { signal }, HEATMAP_TIMEOUT_S)
    if (!needsForecasts(first)) return first
    return sendForecasts<Heatmap>('/api/heatmap/forecasts', first, HEATMAP_TIMEOUT_S, signal)
  })
}

// Runs `work` with a signal that also fires after `seconds`, and says how long it all took then
async function withDeadline<T>(
  what: string,
  seconds: number,
  signal: AbortSignal | undefined,
  work: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const deadline = AbortSignal.timeout(seconds * 1000)
  try {
    return await work(signal ? AbortSignal.any([signal, deadline]) : deadline)
  } catch (e) {
    if (deadline.aborted && !signal?.aborted) {
      throw new ApiError(0, `${what} took longer than ${seconds} seconds. Try again in a moment.`)
    }
    throw e
  }
}

// Sample data comes back whole. Live data comes back as the forecasts to fetch first.
function needsForecasts(body: object): body is ForecastRequest {
  return 'forecast_urls' in body
}

// Open-Meteo's free limit is per IP address, and the server shares its address with other apps,
// so the browser fetches the forecasts the server lists and posts the replies back to be scored.
async function sendForecasts<T>(path: string, req: ForecastRequest, timeoutS: number, signal: AbortSignal) {
  const forecasts = await untilAborted(Promise.all(req.forecast_urls.map(forecast)), signal)
  const body = JSON.stringify({ plan_id: req.plan_id, forecasts })
  try {
    return await request<T>(path, { method: 'POST', body, signal }, timeoutS)
  } catch (e) {
    // The server lost the plan between the two steps (it keeps it 15 minutes): planning again works
    if (e instanceof ApiError && e.status === 404) throw new ApiError(404, e.message, true)
    throw e
  }
}

const kept = new Map<string, { at: number; reply: Promise<unknown> }>()

// Open-Meteo's reply to a forecast URL, from the ones kept when there is one. A fetch is shared by
// everyone who asks for the same URL meanwhile, so it isn't stopped when one of them gives up.
function forecast(url: string): Promise<unknown> {
  const hit = kept.get(url)
  if (hit && Date.now() - hit.at < FORECAST_KEEP_MS) return hit.reply
  const reply = inTurn(() => fetchForecast(url))
  kept.delete(url) // so it goes back in as the newest
  kept.set(url, { at: Date.now(), reply })
  reply.catch(() => {
    if (kept.get(url)?.reply === reply) kept.delete(url) // a failure isn't kept
  })
  for (const oldest of kept.keys()) {
    if (kept.size <= FORECASTS_KEPT) break
    kept.delete(oldest)
  }
  return reply
}

let running = 0
const queued: Array<() => void> = []

// At most FORECASTS_AT_ONCE requests to Open-Meteo at a time, from the whole page
async function inTurn<T>(task: () => Promise<T>): Promise<T> {
  if (running < FORECASTS_AT_ONCE) running++
  else await new Promise<void>((go) => queued.push(go)) // a finishing task hands over its turn
  try {
    return await task()
  } finally {
    const next = queued.shift()
    if (next) next()
    else running--
  }
}

// The promise's result, unless the signal fires first. The work behind it carries on regardless.
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(signal.reason)
  return new Promise<T>((resolve, reject) => {
    const stop = () => reject(signal.reason)
    signal.addEventListener('abort', stop, { once: true })
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', stop))
  })
}

// Tried once more after a short wait when the network, a timeout, a server error or too many
// requests at once get in the way. Open-Meteo's per-minute, hourly and daily limits won't clear
// in that time, so those are said straight away.
async function fetchForecast(url: string, retry = true): Promise<unknown> {
  const again = async () => {
    await new Promise((resolve) => setTimeout(resolve, FORECAST_RETRY_MS))
    return fetchForecast(url, false)
  }
  let res: Response
  let text: string
  try {
    res = await fetch(url, { signal: AbortSignal.timeout(FORECAST_TIMEOUT_S * 1000) })
    text = await res.text()
  } catch {
    if (retry) return again()
    throw new ApiError(0, WEATHER_DOWN)
  }

  const body = parseJson(text)
  if (res.ok && body !== undefined) return body
  // e.g. {"error": true, "reason": "Daily API request limit exceeded. Please try again tomorrow."}
  const reason = isObject(body) && typeof body.reason === 'string' ? body.reason : ''
  if (res.status === 429 && /daily|hourly/i.test(reason)) throw new ApiError(429, WEATHER_LIMIT, false)
  if (res.status === 429 && /minute/i.test(reason)) throw new ApiError(429, WEATHER_MINUTE)
  if (retry && (res.status === 429 || res.status >= 500)) return again()
  throw new ApiError(res.status, WEATHER_DOWN)
}
