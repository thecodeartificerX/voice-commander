# Vite 5 — Quick Reference

**Package:** `vite` ^5.4.10 + `@vitejs/plugin-react` ^4.3.3  
**Docs:** https://vite.dev (v5)

---

## Configuration (`vite.config.ts`)

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: '/static/builder/',           // URL prefix for all assets
  build: {
    outDir: '../../src/voice_commander/web/static/builder',
    emptyOutDir: true,                // clean before each build
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),  // import '@/components/...'
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api':    { target: 'http://localhost:8765', changeOrigin: true },
      '/events': { target: 'http://localhost:8765', changeOrigin: true, ws: false },
      '/page':   { target: 'http://localhost:8765', changeOrigin: true },
    },
  },
})
```

This is the **actual config** in `web/builder-ui/vite.config.ts`.

---

## React Plugin (`@vitejs/plugin-react`)

```ts
import react from '@vitejs/plugin-react'

plugins: [react()]
```

Provides:
- **JSX transform** — uses the React 17+ automatic runtime (`react/jsx-runtime`); no `import React` needed in every file.
- **Fast Refresh** — hot-replaces components on save without losing state. Works for function components; class components force a full reload.
- **Babel-based transform** — uses Babel under the hood (slower startup than `@vitejs/plugin-react-swc`, but more compatible with plugins).

---

## Dev Server

```bash
cd web/builder-ui && pnpm dev
```

Starts at `http://localhost:5173`. The proxy config forwards:
- `/api/*` → `http://localhost:8765/api/*` (daemon REST)
- `/events` → `http://localhost:8765/events` (daemon SSE)
- `/page/*` → `http://localhost:8765/page/*` (daemon pages)

This means the SPA dev server works against a live daemon without CORS issues. FastAPI must be running separately.

**HMR (Hot Module Replacement):** Vite uses native ESM to push per-module updates. React components update in place via Fast Refresh. CSS changes apply instantly without a page reload.

---

## Build

```bash
cd web/builder-ui && pnpm build
# equivalent: tsc -b && vite build
```

Output goes to `../../src/voice_commander/web/static/builder/` (relative to `web/builder-ui/`).

Key build behaviours:
- **`emptyOutDir: true`** — clears `static/builder/` before each build. Prevents stale hashed chunks.
- **`base: '/static/builder/'`** — all asset URLs are prefixed, matching how FastAPI serves them at `/static/builder/`.
- **Code splitting** — Vite automatically splits vendor chunks (`react`, `reactflow`, etc.) and lazy-loaded routes. No manual `splitChunks` config needed.
- **TypeScript** — `tsc -b` runs first (type checking + declaration emit), then Vite bundles with esbuild (transpile-only, no type checking in Vite itself).

### Makefile targets

```bash
make builder-install   # cd web/builder-ui && pnpm install
make builder-build     # cd web/builder-ui && pnpm build
```

---

## TypeScript Integration

Vite uses **esbuild** for transpilation — fast but no type checking. Type errors will not fail the Vite build.

To type-check:

```bash
pnpm typecheck   # tsc -b (no emit, check only)
```

The `pnpm build` script runs `tsc -b && vite build` — so a type error **does** fail a production build.

`tsconfig.json` key settings for this project:
- `"strict": true` — all strict checks enabled
- `"moduleResolution": "bundler"` — Vite-compatible module resolution
- `"jsx": "react-jsx"` — automatic JSX transform

---

## Environment Variables

All env vars must be prefixed `VITE_` to be exposed to client code:

```ts
// .env or .env.local
VITE_API_BASE=http://localhost:8765

// In code
const base = import.meta.env.VITE_API_BASE;
```

`import.meta.env.MODE` — `"development"` in dev, `"production"` in build.  
`import.meta.env.DEV` — `true` in dev mode.  
`import.meta.env.PROD` — `true` in production build.

**Note:** This project does not currently use `.env` files — the API base URL is always proxied in dev and served from the same origin in production.

---

## CSS & PostCSS

Vite has built-in PostCSS support. `postcss.config.js` (or inline in `vite.config.ts`) is auto-discovered.

This project uses **Tailwind CSS** via PostCSS:

```js
// postcss.config.js
export default {
  plugins: { tailwindcss: {}, autoprefixer: {} },
}
```

```ts
// tailwind.config.ts
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: { extend: {} },
  plugins: [],
}
```

CSS imported in `src/styles/globals.css` includes `@tailwind base/components/utilities` directives. Imported once in `main.tsx`.

---

## Path Aliases

The `@` alias resolves to `web/builder-ui/src/`:

```ts
import { cn } from '@/lib/utils';           // → src/lib/utils.ts
import { Button } from '@/components/ui/button';  // → src/components/ui/button.tsx
```

Configured in both `vite.config.ts` (`resolve.alias`) and `tsconfig.json` (`paths`).

---

## Project-Specific Notes

| Detail | Value |
|---|---|
| Build output | `src/voice_commander/web/static/builder/` |
| `base` URL | `/static/builder/` |
| Dev port | `5173` |
| Daemon proxy target | `http://localhost:8765` |
| Build command | `pnpm build` (`tsc -b && vite build`) |
| Watch mode | `pnpm dev` |
| Gitignored | `src/voice_commander/web/static/builder/` — see gotcha #30 |

The build output is gitignored. Every fresh clone requires running `pnpm install && pnpm build` (or `make builder-install && make builder-build`). See gotcha #29.

**ADR:** [`decisions/0071-builder-react-spa.md`](decisions/0071-builder-react-spa.md)
