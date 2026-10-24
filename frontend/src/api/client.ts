import type { Heatmap, Place, Plan, PlanRequest } from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  // Only with a body: on a cross-origin GET this header would force a CORS preflight per request
  if (init.body != null) headers.set('Content-Type', 'application/json')
  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  const body = parseJson(await res.text())
  if (!res.ok) {
    throw new ApiError(res.status, formatError(body) ?? `Request failed (${res.status})`)
  }
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

// DRF errors come back as {detail: "..."} or {field: ["..."]}
function formatError(body: unknown): string | undefined {
  if (!isObject(body)) return undefined
  if (typeof body.detail === 'string') return body.detail
  const parts = Object.entries(body).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(' ') : JSON.stringify(v)}`)
  return parts.length ? parts.join('; ') : undefined
}

export async function geocode(q: string, signal?: AbortSignal): Promise<Place[]> {
  const body = await request<{ results?: Place[] }>(`/api/geocode?q=${encodeURIComponent(q)}`, { signal })
  return Array.isArray(body.results) ? body.results : []
}

export function planTrip(body: PlanRequest, signal?: AbortSignal) {
  return request<Plan>('/api/plan', { method: 'POST', body: JSON.stringify(body), signal })
}

export function getHeatmap(planId: string, signal?: AbortSignal) {
  return request<Heatmap>(`/api/heatmap?plan_id=${encodeURIComponent(planId)}`, { signal })
}
