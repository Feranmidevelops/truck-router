// Mirrors backend/routing/services/planner.py. Change both together.

export type RiskLevel = 'LOW' | 'MODERATE' | 'HIGH' | 'SEVERE' | 'NO_TRAVEL'

export interface Place {
  label: string
  lat: number
  lon: number
  timezone?: string // IANA zone at the place, e.g. "America/Chicago"; set on geocode results
}

export interface PlanRequest {
  origin: Place
  destination: Place
  // Local time at the origin without an offset, e.g. "2026-10-26T08:00", read in the origin's
  // time zone. An ISO 8601 time with an offset is also accepted and kept as that instant.
  departure_time: string
  load_weight_lb: number
  interval_miles: 10 | 25 | 50
}

export interface PlanInput extends PlanRequest {
  departure_time: string // ISO 8601 with the origin's offset, e.g. "2026-10-26T08:00:00-05:00"
  timezone: string // the origin's IANA zone; show every time in the plan in this zone
}

export interface CheckpointWeather {
  wind_mph: number
  gust_mph: number
  rain_in_hr: number
  snow_in_hr: number
  forecast_time: string
}

export interface CheckpointRisk {
  level: RiskLevel
  wind: RiskLevel
  rain: RiskLevel
  snow: RiskLevel
  load_adjusted: boolean
  drivers: Array<'wind' | 'rain' | 'snow'>
}

export interface Checkpoint {
  index: number
  mile: number
  lat: number
  lon: number
  segment_miles: number
  eta: string
  weather: CheckpointWeather
  risk: CheckpointRisk
}

export interface RouteSummary {
  miles_by_level: Record<RiskLevel, number>
  avg_risk: number
  max_level: RiskLevel
}

export interface Route {
  id: string
  label: string
  rank: number
  source: 'tomtom' | 'via-fallback'
  distance_miles: number
  duration_s: number
  departure_time: string
  arrival_time: string
  geometry: Array<[number, number]> // [lon, lat]
  summary: RouteSummary
  checkpoints: Checkpoint[]
}

export interface Plan {
  plan_id: string
  input: PlanInput
  recommended_route_id: string
  recommendation_reason: string
  routes: Route[] // sorted best first
}

export interface HeatmapPoint {
  lat: number
  lon: number
  v: number[] // risk level 0-4 for each hour offset 0..hours
}

export interface Heatmap {
  plan_id: string
  start_time: string
  hours: number
  points: HeatmapPoint[]
}
