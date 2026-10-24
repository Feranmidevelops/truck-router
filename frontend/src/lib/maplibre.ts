import { setWorkerUrl } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url'

// maplibre-gl v6 finds its worker relative to its own module URL. Vite's
// pre-bundling (dev) and hashing (build) break that path, so point it at the
// worker file explicitly. Without this the map stays blank with
// "Worker failed to load".
setWorkerUrl(workerUrl)
