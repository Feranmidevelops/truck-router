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
  temp_f: number | null // shown, not scored; null when the forecast has no value
  conditions: string // e.g. "Partly cloudy", "Light snow"; "Unknown" when weather_code is null
  weather_code: number | null // WMO code that conditions describes, e.g. for picking an icon
  forecast_time: string // the hour wind, gusts, temperature and conditions come from
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

export interface ReportStop {
  place: string // the origin's or destination's label, as entered
  time: string // departure, or this route's arrival
  level: RiskLevel
  weather: CheckpointWeather // the first or last checkpoint's: what the place is like then
}

// A run of consecutive checkpoints at Moderate or worse. A Low checkpoint in between splits it.
export interface ReportStretch {
  where: string // "near Amarillo, TX", or "at mile 412" when the place has no name
  first_index: number // checkpoints[first_index..last_index], both included, make up the stretch
  last_index: number
  start_mile: number // the first and last checkpoint's mile and ETA
  end_mile: number
  start_eta: string
  end_eta: string
  miles: number // road its checkpoints own, as counted in summary.miles_by_level
  level: RiskLevel // the worst level in the stretch, never LOW
  drivers: Array<'wind' | 'rain' | 'snow'> // factors at that level, at any of its checkpoints
  max_wind_mph: number
  max_gust_mph: number
  max_rain_in_hr: number
  max_snow_in_hr: number
  min_temp_f: number | null // null when no checkpoint in it has a temperature
  max_temp_f: number | null
}

export interface JourneyReport {
  departure: ReportStop
  stretches: ReportStretch[] // in driving order; empty when the route is Low all the way
  arrival: ReportStop
}

export interface Route {
  id: string // "r0", "r1", "r2": unique within a plan
  label: string // "Route A" is always the recommended route, then B, C in rank order
  rank: number
  source: 'tomtom' // every route, the ones found round avoided areas too, is TomTom's
  distance_miles: number
  duration_s: number
  departure_time: string
  arrival_time: string
  geometry: Array<[number, number]> // [lon, lat]
  summary: RouteSummary
  // The first is the origin at departure and the last the destination at this route's arrival,
  // so their weather is what each place is like when the truck is there (as in report).
  checkpoints: Checkpoint[]
  report: JourneyReport
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
