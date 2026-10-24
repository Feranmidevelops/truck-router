import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev, /api is proxied to Django so no CORS setup is needed locally.
// In production, set VITE_API_BASE_URL to the backend URL.
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1500, // maplibre-gl alone is ~1 MB minified
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
