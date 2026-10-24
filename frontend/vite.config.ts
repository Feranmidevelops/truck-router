import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, /api is proxied to Django so no CORS setup is needed locally.
// In production, set VITE_API_BASE_URL to the backend URL.
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1100, // the maplibre-gl chunk alone is ~1 MB minified
    rolldownOptions: {
      output: {
        // maplibre-gl gets its own chunk: it loads with the map, after the first screen
        // (see lib/loadMap.ts), and stays cached when only our code changes.
        codeSplitting: {
          groups: [{ name: 'maplibre', test: /node_modules[\\/]maplibre-gl[\\/]/ }],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
