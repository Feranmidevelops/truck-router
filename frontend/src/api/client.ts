import type { Heatmap, Place, Plan, PlanRequest } from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError(res.status, formatError(body) ?? `Request failed (${res.status})`)
  }
  return body as T
}

// DRF errors come back as {detail: "..."} or {field: ["..."]}
function formatError(body: unknown): string | undefined {
  if (!body || typeof body !== 'object') return undefined
  const b = body as Record<string, unknown>
  if (typeof b.detail === 'string') return b.detail
  const parts = Object.entries(b).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(' ') : JSON.stringify(v)}`)
  return parts.length ? parts.join('; ') : undefined
}

export function geocode(q: string, signal?: AbortSignal) {
  return request<{ results: Place[] }>(`/api/geocode?q=${encodeURIComponent(q)}`, { signal })
}

export function planTrip(body: PlanRequest, signal?: AbortSignal) {
  return request<Plan>('/api/plan', { method: 'POST', body: JSON.stringify(body), signal })
}

export function getHeatmap(planId: string, signal?: AbortSignal) {
  return request<Heatmap>(`/api/heatmap?plan_id=${encodeURIComponent(planId)}`, { signal })
}
