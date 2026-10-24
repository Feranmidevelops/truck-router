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
| `NUM_PROXIES` | backend | Proxies that append to `X-Forwarded-For` (0 locally, 2 on Render) |
| `GEOCODE_COUNTRY_SET` | backend | Countries for place search (default `US`) |
| `VITE_API_BASE_URL` | frontend | Backend URL in production (empty in dev) |
| `VITE_MAP_STYLE_URL` | frontend | Optional map style override |

## API

| Method | Path | Returns |
| --- | --- | --- |
| `POST` | `/api/plan` | Three ranked routes with checkpoints, risk and the recommendation |
| `GET` | `/api/heatmap?plan_id=` | Risk level per grid point for each hour, 0-48 h |
| `GET` | `/api/geocode?q=` | Place suggestions, each with its IANA time zone |
| `GET` | `/api/health` | Health check |

Types: `frontend/src/api/types.ts`. Errors come back as `{"detail": "..."}`: 400 for bad input,
422 when TomTom has no truck route between the places, 429 when a client sends too many
requests, and 502 with a "try again in a minute" message when TomTom or Open-Meteo fails.

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
- **A departure in the past starts now.** The form accepts a time up to an hour ago, but TomTom
  can only route from now, so the whole plan (ETAs, arrival, the departure shown and the
  heatmap's hour 0) starts at the later of the typed time and now.
- **Forecast hour.** Wind uses the nearest forecast hour. Rain and snow use the first hour at or
  after the ETA, because Open-Meteo reports precipitation as the total over the preceding hour.
  Exactly half past goes to the later hour, and gusts are read from the same hour as the wind.

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
runner-up, for example "Fewest severe miles (0.0 vs 25.0)". Routes are lettered in that order,
so Route A is always the recommended one.

### Routes

- TomTom's own truck alternatives come first. It often returns fewer than three, so two extra
  routes are requested through points 15% of the trip to the left and right of the straight
  origin -> destination line's midpoint.
- **Those two go out at the same time as the main request, not after it.** Live, the main
  request (with alternatives) took 6-10 s and a via-point request 2-3 s, so sending them one
  after the other added 2-3 s to every plan that needed a fallback. On Dallas -> Denver the
  fallback step went from 2.3-3.3 s to 0.1 s. Once the main request is back, a via-point
  request still out gets 2 s more, then it is ignored; any via-point request gives up after
  10 s.
- If that still isn't three, more routes are requested through points off the fastest route's
  midpoint, 15% then 25%. A point within 1% of the trip of one already asked is skipped, and a
  plan never asks for more than 6 via-point routes.
- Any candidate that takes over 1.5 times as long as the fastest route, or overlaps a route
  already kept by more than 90%, is dropped. If that leaves fewer than three, the app shows what
  it has rather than padding with a silly detour.
- If TomTom has no route for the trip itself, the answer is "no route", even when a via-point
  request found one.
- **The cost is TomTom requests.** Every plan now makes at least 3, even when TomTom's own
  three routes were enough (that used to be 1), and at most 7. The free tier's 2,500 requests a
  day covers about 830 plans, or 350 if every plan needed every fallback.

### Weather requests

- Open-Meteo's free tier allows 600 locations a minute. Coordinates are rounded to 0.1 degrees and
  de-duplicated before the request, so checkpoints on shared road cost one lookup.
- Responses are cached for 30 minutes, and a rate-limited request is retried once before the app
  shows a clear "try again in a minute".

### Heatmap

- **Where it samples.** Cell centres on a fixed 0.25 degree grid (about 17 x 14 miles) within 30
  miles of any route, capped at 250 points. Long trips get coarser cells until they fit, and the
  cell each stretch of road passes through is always kept, so the road never has a hole in it.
- **What it shows.** The risk level (Low to No Travel) at each point for every hour from the
  departure, rounded down to the hour, to 48 hours later, using the same rules and load as the
  checkpoints, so the shading and the dots agree.
- **Drawn as blurred circles in each point's exact level colour.** A density heatmap was tried
  first, but it blends neighbours by weight: a lone Severe cell came out faint green and Low
  showed nothing at all. Circles keep every point's colour honest and still read as a smooth
  field.
- **The slider never refetches.** All 49 hours arrive in one request and sit on every point as
  `h0`..`h48`. Moving the slider only changes which property the colour expression reads
  (`setPaintProperty`): no network, no re-sent data. Measured at about 3 ms per tick and a steady
  60 fps while dragging.

### Rate limits

- Each visitor gets 10 plans, 120 place searches and 30 heatmaps a minute; a limited request gets
  a 429 with `Retry-After`. Health checks are never limited.
- On Render, Cloudflare sits in front of Render's own proxy and both append to
  `X-Forwarded-For`, so the visitor's address is the second entry from the end
  (`NUM_PROXIES=2`). The last entry would be a Cloudflare server shared by strangers; the first
  is whatever the client chose to send.

### Response size

- **Route lines are simplified before they are sent.** TomTom returns about 10,000 points per
  route. The map gets a Douglas-Peucker copy that never strays more than 20 m from the real line
  (about 900 points), which takes a Dallas -> Denver plan from ~700 kB to ~95 kB of JSON, ~18 kB
  gzipped. Checkpoints and ETAs are still worked out on the full line.

### Time zones

- **The departure is local time at the origin**, not the viewer's time zone. The backend looks up
  the origin's zone from its coordinates (offline, with `tzfpy`), reads the typed time there,
  and returns the zone with the plan so every time on screen is shown in it. A time sent with an
  offset is kept as that instant. The "1 hour ago to 5 days ahead" check runs on the result.
- **Clock-change nights:** a time that happens twice (01:30 when clocks go back) means the first
  one; a time that is skipped (02:30 when clocks go forward) moves on by the jump, to 03:30. This
  is what JavaScript's `Date` does too.

## Tests

`cd backend && pytest` runs about 400 tests in a few seconds, with no network access.

- **Rules:** every wind, rain and snow boundary just below, at and above its edge, a grid of load
  rule edges, each level's score in the average, and every ranking key and reason.
- **Checked against wrong code:** the rule tests were run against deliberately broken versions
  (a `>=` where the brief says `>`, snow read from the wind's hour, overlap measured the wrong
  way round, de-duplication per chunk) to make sure each one fails a test.
- **Real data:** a trimmed real TomTom Dallas -> Denver response, and Open-Meteo unit strings
  confirmed against a live call.
- **End to end:** a whole trip goes through the API, the real HTTP clients and the planner
  against fake TomTom and Open-Meteo servers, including "no route" and failure cases.
- **Contract:** a test reads `frontend/src/api/types.ts` and checks every API response and sample
  file against it, so the backend and the frontend can't drift apart silently.
- **Frontend:** `npm run build` type-checks it and `npm run lint` lints it.

## Deploy

- Backend: `render.yaml` (Render blueprint). Free Render instances sleep after a period of no
  traffic, and the first request after that is slow, so keep it warm during the review window or
  use a paid instance.
- Frontend: Netlify with the root `netlify.toml`. Set `VITE_API_BASE_URL`.

## Known limits

- **ETAs assume nonstop driving.** US hours-of-service rules (a 30-minute break after 8 hours,
  at most 11 hours driving, then 10 hours off) would push later checkpoints into later forecast
  hours. Dallas -> Denver is about 12 hours, so a real driver would stop. Adding the breaks to
  the timing profile is the next step.
- **Weather between checkpoints isn't scored.** At a 50-mile interval a storm between two
  checkpoints doesn't count towards a route's miles; that's the brief's sampling design. The
  heatmap shows the corridor in between, and a 10-mile interval narrows the gap.
- **Gusts are shown but not scored**, because the brief's wind thresholds read as sustained wind.
- **Forecast detail.** Open-Meteo blends weather models on grids of a few kilometres, and
  lookups are rounded to 0.1 degrees (about 7 miles), so very local storms can be missed or
  smeared.
- **Routes depend on TomTom.** It often returns two truck alternatives, so the third is usually
  a fallback that's slower, and with live traffic the routes can change between runs.
- **One clock per trip.** Every time is shown on the origin's clock, even after the truck
  crosses into another time zone.
- **US only.** Place search is limited to the US (`GEOCODE_COUNTRY_SET`), and the thresholds are
  in mph and inches.
- **Free tiers.** TomTom allows 2,500 requests a day (about 830 plans), Open-Meteo 600 locations
  a minute, and a free Render instance sleeps when idle, so the first request after a quiet
  spell is slow.
