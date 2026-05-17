import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forward /api/* to the FastAPI backend during dev. Default port
      // is 8000; override via COCKPIT_BACKEND_PORT env var if 8000 is
      // in use. Production build is workstation-local; reverse-proxy
      // would serve both /api/* and / from the same origin.
      '/api': {
        target: `http://localhost:${process.env.COCKPIT_BACKEND_PORT ?? '8000'}`,
        changeOrigin: false,
      },
    },
  },
})
