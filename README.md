# Weather-Aware Truck Routing

Plan a truck trip and pick the safest of three routes, based on the weather the truck will meet
at each point along the way, the load weight and the travel time.

- **Live app:** TODO
- **Loom walkthrough:** TODO

## What it does

1. Enter origin, destination, departure time, load weight, and how often to check the weather
   (every 10, 25 or 50 miles).
2. The backend gets three truck routes from TomTom and places checkpoints along each one.
3. For each checkpoint it works out when the truck gets there and reads the forecast for that hour
   from Open-Meteo.
4. Each checkpoint gets a risk level from the wind, rain and snow thresholds and the load rules.
5. Routes are ranked: fewest Severe miles, then fewest High miles, then lowest average risk, then
   shortest travel time.
6. The map shows all three routes, coloured checkpoints, and a weather-risk heatmap along the
   corridor with a 0-48 hour slider.

## Stack

| Part | Choice |
| --- | --- |
| Frontend | React 19, TypeScript, Vite, MapLibre GL |
| Backend | Django 5.2, Django REST Framework, httpx |
| Routing and geocoding | TomTom Routing API (truck mode) and Search API |
| Weather | Open-Meteo hourly forecast |
| Map tiles | OpenFreeMap |

## Run locally

Backend (Python 3.11+):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows; on mac/linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add TOMTOM_API_KEY, set USE_FIXTURES=false for live data
python manage.py runserver
pytest
```

Frontend (Node 20+):

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

With `USE_FIXTURES=true` the API serves a canned Dallas -> Denver trip, so the UI runs without
any API keys.

## Environment variables

| Name | Where | Purpose |
| --- | --- | --- |
| `TOMTOM_API_KEY` | backend | Routing and geocoding |
| `USE_FIXTURES` | backend | `true` serves canned data |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | backend | Django |
| `CORS_ALLOWED_ORIGINS` | backend | Frontend origin(s), comma-separated |
| `VITE_API_BASE_URL` | frontend | Backend URL in production (empty in dev) |
| `VITE_MAP_STYLE_URL` | frontend | Optional map style override |

## API

| Method | Path | Returns |
| --- | --- | --- |
| `POST` | `/api/plan` | Three ranked routes with checkpoints, risk and the recommendation |
| `GET` | `/api/heatmap?plan_id=` | Risk level per grid point for each hour, 0-48 h |
| `GET` | `/api/geocode?q=` | Place suggestions, each with its IANA time zone |
| `GET` | `/api/health` | Health check |

Types: `frontend/src/api/types.ts`.

## Decisions

Where the brief left room for interpretation, and what I chose.

### Risk rules

- **Shared boundaries go to the higher, safer band.** Rain 0.25 in/hr is High, snow 1.0 in/hr is
  High, wind 35 mph is High.
- **An explicit operator in the brief wins.** No Travel is "> 1.00" for rain and "> 3.0" for snow,
  so exactly 1.00 and 3.0 stay Severe.
- **Wind bands are half-open.** The brief writes 25-34 and 35-44 as whole numbers; 34.9 mph is
  Moderate.
- **Load rules use a strict "greater than".** Exactly 30,000 lb at 50 mph stays Severe; 30,001 lb
  is No Travel. A load rule can raise the wind level but never lower it.
- **A checkpoint's level is the worst of wind (after load rules), rain and snow.** Every factor at
  that level is listed as a driver.
- **Wind means sustained wind at 10 m.** Gusts are shown in the popup but not scored.
- **Rain rate is Open-Meteo `rain + showers` for the hour; snow rate is `snowfall`.** Units are
  read from the response and converted.
- **Load weight is cargo weight.** The truck weight sent to TomTom is cargo plus an assumed
  35,000 lb empty tractor-trailer.

### Checkpoints and ETAs

- **Checkpoints sit at mile 0, every interval, and the destination.** The destination is skipped
  when the last interval checkpoint is within half a mile of it.
- **Each checkpoint owns the road centred on it**, from halfway back to the previous checkpoint to
  halfway on to the next. "Severe miles" is the sum of the miles owned by Severe checkpoints, and
  the owned miles of a route add up to its length.
- **ETA comes from TomTom's own timing along the route**, interpolated between its turn-by-turn
  instructions, not from an average speed. Slow city miles and fast interstate miles keep their
  real speeds.
- **Forecast hour.** Wind uses the nearest forecast hour. Rain and snow use the first hour at or
  after the ETA, because Open-Meteo reports precipitation as the total over the preceding hour.

### Recommendation

Routes are sorted on these keys, in order:

0. **Fewest No Travel miles.** Not in the brief, but a route with No Travel segments should never
   beat one without. If every route has some, the least bad is still recommended and the UI warns.
1. **Fewest Severe miles**, then 2. **fewest High miles** (both compared to 0.1 mile).
3. **Lowest average risk**: the distance-weighted mean of the level scores (Low 0 to No Travel 4).
   Compared at 2 decimals only to remove floating-point noise, so a lower average wins even when
   it is slower. That keeps the brief's order exactly.
4. **Shortest travel time**, which only decides true ties.

The reason shown under the recommended route names the first key that separates it from the
runner-up, for example "Fewest severe miles (0.0 vs 25.0)".

### Routes

- TomTom's own truck alternatives come first. When it returns fewer than three, extra routes are
  requested through points offset to the left and right of the trip's midpoint.
- Any candidate that takes over 1.5 times as long as the fastest route, or overlaps a route
  already kept by more than 90%, is dropped. If that leaves fewer than three, the app shows what
  it has rather than padding with a silly detour.

### Weather requests

- Open-Meteo's free tier allows 600 locations a minute. Coordinates are rounded to 0.1 degrees and
  de-duplicated before the request, so checkpoints on shared road cost one lookup.
- Responses are cached for 30 minutes, and a rate-limited request is retried once before the app
  shows a clear "try again in a minute".

### Time zones

- **The departure is local time at the origin**, not the viewer's time zone. The backend looks up
  the origin's zone from its coordinates (offline, with `tzfpy`), reads the typed time there,
  and returns the zone with the plan so every time on screen is shown in it. A time sent with an
  offset is kept as that instant. The "1 hour ago to 5 days ahead" check runs on the result.
- **Clock-change nights:** a time that happens twice (01:30 when clocks go back) means the first
  one; a time that is skipped (02:30 when clocks go forward) moves on by the jump, to 03:30. This
  is what JavaScript's `Date` does too.

## Tests

`cd backend && pytest` covers every threshold boundary, the load rules, ranking order,
checkpoint sampling and ETAs, the weather client's unit handling, and the API contract.

## Deploy

- Backend: `render.yaml` (Render blueprint). Free Render instances sleep after a period of no
  traffic, and the first request after that is slow, so keep it warm during the review window or
  use a paid instance.
- Frontend: Netlify with the root `netlify.toml`. Set `VITE_API_BASE_URL`.

## Known limits

- TODO
