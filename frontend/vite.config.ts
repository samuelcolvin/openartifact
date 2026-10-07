/**
 * Vite builds the web app in `app/` (the artifact list and editor; React) to `dist/app/`, served by the Python
 * server at `/` and `/edit/<id>` with its assets under `/app/`. The artifact runtime, `src/main.ts`, is a separate
 * esbuild bundle (see package.json). `pnpm app:dev` runs Vite's dev server on :5173 and proxies everything else to
 * the Python server on :8765, so the session cookie set by `/login` works for both.
 */

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const BACKEND = 'http://127.0.0.1:8765'
const PROXIED = ['/api', '/artifacts', '/login', '/logout', '/openartifact.js', '/health', '/print']

export default defineConfig({
  root: 'app',
  base: '/app/',
  plugins: [react(), tailwindcss()],
  build: { outDir: '../dist/app', emptyOutDir: true },
  server: { port: 5173, proxy: Object.fromEntries(PROXIED.map((path) => [path, BACKEND])) },
})
