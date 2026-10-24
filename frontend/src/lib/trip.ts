import type { Plan, PlanRequest } from '../api/types'
import { formatTime, formatWallClock } from './risk'

// What the trip bar shows: the trip on screen, or the one being planned
export interface TripSummary {
  from: string
  to: string
  departure: string // "Mon 8:00 AM CDT", or without the zone while it's still being planned
  loadLb: number
  intervalMiles: number
}

// The plan's own echo of the trip: the departure as the server read it, in the origin's zone
export function tripFromPlan(plan: Plan): TripSummary {
  const { input } = plan
  return {
    from: input.origin.label,
    to: input.destination.label,
    departure: formatTime(input.departure_time, input.timezone),
    loadLb: input.load_weight_lb,
    intervalMiles: input.interval_miles,
  }
}

export function tripFromRequest(req: PlanRequest): TripSummary {
  return {
    from: req.origin.label,
    to: req.destination.label,
    departure: formatWallClock(req.departure_time),
    loadLb: req.load_weight_lb,
    intervalMiles: req.interval_miles,
  }
}

// "Dallas" from "Dallas, TX"
export function placeName(label: string) {
  return label.split(',')[0]
}
