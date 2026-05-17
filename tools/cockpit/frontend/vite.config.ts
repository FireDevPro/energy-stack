import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forward /api/* to the FastAPI backend during dev. Backend port
      // is pinned to 8000 to match start-cockpit.ps1 — keep them in sync
      // if either ever moves. Production build is workstation-local;
      // reverse-proxy would serve both /api/* and / from the same origin.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: false,
      },
    },
  },
})
