import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forward /api/* to the FastAPI backend during dev. Backend port
      // is pinned to 8765 to match start-cockpit.ps1 — keep them in sync
      // if either ever moves. Production is the Pi compose service: the
      // backend container serves the built dist/ and /api/* same-origin,
      // so this proxy is dev-only.
      '/api': {
        target: 'http://localhost:8765',
        changeOrigin: false,
      },
    },
  },
})
