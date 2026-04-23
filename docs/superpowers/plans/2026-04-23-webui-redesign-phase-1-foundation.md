# Web UI Redesign — Phase 1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `web/` React/Vite/Tailwind/shadcn project, wire FastAPI to serve it at `/beta`, and update the Dockerfile to do a multi-stage build. Ends with an empty-but-real React shell rendering at `http://<host>/beta` showing "Dashboard" / "Print" tab chrome, dark theme, Inter/JetBrains Mono fonts, and no errors in the console. Old Jinja UI at `/` and `/settings` is untouched.

**Architecture:** A new top-level `web/` directory holds a standalone Vite+React+TypeScript project. Production build outputs to `app/static/dist/{index.html, assets/*}`. FastAPI mounts `/beta/assets` for hashed asset files and has a catch-all that serves `dist/index.html` for `/beta` and any `/beta/*` path so client-side routing works. The Dockerfile gains a Node build stage that runs `npm ci && npm run build` and copies the output into the Python runtime image.

**Tech Stack:** React 18, TypeScript 5, Vite 5, Tailwind CSS 3, shadcn/ui (Lucide icons), react-router v6, @tanstack/react-query v5, sonner, @fontsource-variable/inter, @fontsource-variable/jetbrains-mono. Backend unchanged except for two new routes in `app/main.py`.

**Scope note:** This is Phase 1 of 6. Subsequent phases (Dashboard read-only → Dashboard controls → Print flow → Settings → Cutover) each get their own plan after Phase 1 ships. This plan intentionally produces no new user-visible features yet — its value is the infrastructure for everything after.

**Spec reference:** `docs/superpowers/specs/2026-04-23-webui-redesign-design.md` (sections "Stack & build chain" and "Incremental ship plan" step 1).

---

## File structure

**Created in this plan:**

```
web/
├── .gitignore
├── .npmrc                         # engine-strict for reproducibility
├── index.html                     # Vite entry HTML
├── package.json
├── package-lock.json              # committed
├── postcss.config.js
├── tailwind.config.ts             # design tokens mapped from spec
├── tsconfig.json
├── tsconfig.app.json
├── tsconfig.node.json
├── vite.config.ts                 # includes /api proxy for dev
├── components.json                # shadcn/ui config
├── public/
│   └── .gitkeep
└── src/
    ├── main.tsx                   # React entry + providers
    ├── App.tsx                    # Router + AppShell outlet
    ├── index.css                  # Tailwind directives + CSS variables (shadcn slots)
    ├── lib/
    │   └── utils.ts               # shadcn cn() helper
    ├── components/
    │   ├── app-shell.tsx          # brand + tabs + settings pill + <Outlet/>
    │   └── ui/                    # shadcn primitives (installed via CLI, committed)
    │       ├── button.tsx
    │       └── sonner.tsx
    └── routes/
        ├── dashboard.tsx          # placeholder: <h1>Dashboard</h1>
        ├── print.tsx              # placeholder: <h1>Print</h1>
        └── settings.tsx           # placeholder: <h1>Settings</h1>
```

**Modified in this plan:**

- `app/main.py` — add `/beta/assets` mount and `/beta`, `/beta/{path:path}` routes (two small edits near existing `app.mount("/static", ...)` block at line 236).
- `Dockerfile` — add Node build stage before the existing Python stage; copy built `dist/` into `app/static/`.
- `.dockerignore` — create (does not exist) to keep `web/node_modules` out of the build context.
- `.gitignore` — append `web/node_modules/`, `web/dist/`, `app/static/dist/`.

**Untouched in this plan:**

- `app/templates/index.html`, `app/templates/settings.html`, `app/static/style.css` — still serving the old UI at `/` and `/settings`.
- All `/api/*` routes and `app/` Python modules.
- Backend pytest suite.

## Prerequisites

The implementing engineer needs:

- Node.js ≥ 20 LTS (for Vite 5). Check with `node -v`.
- npm ≥ 10 (comes with Node 20).
- Docker 24+ for the Dockerfile smoke-test at the end.
- Python 3.13 and the existing `.venv/` activated (the plan will run the FastAPI dev server to verify serving).

---

## Task 1: Create `web/` directory with Vite + React + TypeScript

**Files:**
- Create: `web/package.json`, `web/tsconfig.json`, `web/tsconfig.app.json`, `web/tsconfig.node.json`, `web/vite.config.ts`, `web/index.html`, `web/src/main.tsx`, `web/src/App.tsx`, `web/src/vite-env.d.ts`, `web/.gitignore`, `web/.npmrc`, `web/public/.gitkeep`
- Modify: `.gitignore` (append three lines)

- [ ] **Step 1.1: Scaffold Vite project using the official template**

Run from the repo root:

```bash
npm create vite@latest web -- --template react-ts
```

Expected output: `Scaffolding project in /Users/.../bambu-gateway/web...` followed by instructions. Do **not** run the follow-up `cd web && npm install` yet — we're going to tweak config first.

- [ ] **Step 1.2: Update `web/package.json` scripts and pin a reproducibility guard**

Replace the generated `web/package.json` with:

```json
{
  "name": "bambu-gateway-web",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "lint": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "~5.6.3",
    "vite": "^5.4.11"
  },
  "engines": {
    "node": ">=20"
  }
}
```

Then create `web/.npmrc` with a single line:

```
engine-strict=true
```

- [ ] **Step 1.3: Configure Vite to output to `app/static/dist/` and proxy `/api` in dev**

Replace `web/vite.config.ts`:

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  base: '/beta/',
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: path.resolve(__dirname, '../app/static/dist'),
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:4844',
    },
  },
});
```

Note: `base: '/beta/'` is critical — Vite emits asset URLs like `/beta/assets/index-HASH.js` which matches the FastAPI mount we add in Task 6.

- [ ] **Step 1.4: Simplify the generated `App.tsx` and `main.tsx` to minimal placeholders**

Replace `web/src/App.tsx`:

```tsx
export default function App() {
  return (
    <div style={{ color: 'white', padding: '2rem' }}>
      <h1>Bambu Gateway — /beta foundation</h1>
      <p>If you can read this, Vite + React + TypeScript is wired up.</p>
    </div>
  );
}
```

Replace `web/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

Delete the files `web/src/App.css`, `web/src/index.css`, and `web/src/assets/react.svg` if the scaffold created them — we'll add our own `index.css` in Task 2.

- [ ] **Step 1.5: Simplify `web/index.html`**

Replace `web/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <link rel="icon" type="image/svg+xml" href="/beta/vite.svg" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <meta name="color-scheme" content="dark" />
    <title>Bambu Gateway</title>
  </head>
  <body style="background: #0B0D17; margin: 0;">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 1.6: Install dependencies**

```bash
cd web && npm install
```

Expected: creates `web/node_modules/`, `web/package-lock.json`. No errors. Takes ~30s.

- [ ] **Step 1.7: Verify dev server starts and renders**

```bash
cd web && npm run dev
```

Expected: Vite prints `Local: http://localhost:5173/beta/`. Open that URL in a browser — you should see "Bambu Gateway — /beta foundation" on a dark background. Press `Ctrl+C` to stop.

- [ ] **Step 1.8: Verify production build succeeds and outputs to `app/static/dist/`**

```bash
cd web && npm run build
```

Expected: `tsc -b` passes (no type errors), then `vite build` produces output. Verify:

```bash
ls app/static/dist/
```

Expected output (hash suffixes will differ):

```
assets  index.html  vite.svg
```

- [ ] **Step 1.9: Add `web/` ignores and `app/static/dist/` ignore to `.gitignore`**

Append to the repo root `.gitignore`:

```
# Frontend (web/)
web/node_modules/
web/dist/
app/static/dist/
```

The `web/dist/` line is belt-and-suspenders — Vite is configured to write outside `web/`, but if someone runs `vite build` with the default config it'd land in `web/dist/`.

- [ ] **Step 1.10: Commit**

```bash
git add web/ .gitignore
git commit -m "$(cat <<'EOF'
Scaffold web/ Vite + React + TypeScript project

- New top-level web/ directory with Vite 5 + React 18 + TS 5 scaffold
- Build output redirected to app/static/dist/ (served by FastAPI later)
- base URL set to /beta/ so asset paths match the future FastAPI mount
- Dev server proxies /api to FastAPI at localhost:4844
- Adds web/node_modules, web/dist, and app/static/dist to .gitignore

No user-visible change yet — old Jinja UI at / is untouched.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Install Tailwind CSS and wire design tokens

**Files:**
- Create: `web/tailwind.config.ts`, `web/postcss.config.js`, `web/src/index.css`
- Modify: `web/src/main.tsx` (add `import './index.css';`)

- [ ] **Step 2.1: Install Tailwind and PostCSS**

```bash
cd web && npm install -D tailwindcss@^3.4.17 postcss@^8.4.49 autoprefixer@^10.4.20
```

Expected: adds three devDependencies. `tailwindcss@3` intentional — shadcn/ui's CLI v0.9 currently emits Tailwind v3 configs; we'll migrate to v4 in a later phase if shadcn updates.

- [ ] **Step 2.2: Create `web/postcss.config.js`**

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 2.3: Create `web/tailwind.config.ts` with tokens from the spec**

```ts
import type { Config } from 'tailwindcss';

export default {
  darkMode: 'class', // always-on via <html class="dark">
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: {
          0: '#0B0D17',
          1: '#11151F',
        },
        surface: {
          1: '#1A2030',
          2: '#232A3D',
          3: '#2D3650',
        },
        border: {
          DEFAULT: '#1F2937',
        },
        text: {
          hi: '#FFFFFF',
          0: '#E5E7EB',
          1: '#9CA3AF',
          2: '#6B7280',
        },
        accent: {
          DEFAULT: '#60A5FA',
          strong: '#3B82F6',
        },
        warm: {
          DEFAULT: '#FBBF24',
          hot: '#F97316',
        },
        success: '#22C55E',
        danger: '#EF4444',
        info: '#A855F7',
        // shadcn slots (referenced by installed primitives)
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
      },
      fontFamily: {
        sans: ['"Inter Variable"', 'Inter', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono Variable"', '"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      borderRadius: {
        lg: '12px',
        xl: '16px',
        '2xl': '16px',
      },
      transitionDuration: {
        fast: '120ms',
        base: '200ms',
        slow: '300ms',
      },
      transitionTimingFunction: {
        standard: 'cubic-bezier(0.2, 0.0, 0, 1)',
      },
    },
  },
  plugins: [],
} satisfies Config;
```

- [ ] **Step 2.4: Create `web/src/index.css` with Tailwind directives and shadcn CSS variables**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    /* shadcn/ui dark theme variables — HSL triplets (no hsl() wrapper) */
    --background: 225 35% 7%;          /* #0B0D17 */
    --foreground: 220 13% 91%;         /* #E5E7EB */
    --card: 225 29% 10%;               /* #11151F */
    --card-foreground: 220 13% 91%;
    --popover: 223 25% 15%;            /* #1A2030 */
    --popover-foreground: 220 13% 91%;
    --primary: 217 91% 60%;            /* #3B82F6 */
    --primary-foreground: 0 0% 100%;
    --secondary: 223 24% 23%;          /* #2D3650 */
    --secondary-foreground: 0 0% 100%;
    --muted: 225 14% 22%;              /* surface-2-ish */
    --muted-foreground: 220 9% 55%;    /* #6B7280 */
    --accent: 213 94% 68%;             /* #60A5FA */
    --accent-foreground: 0 0% 100%;
    --destructive: 0 84% 60%;          /* #EF4444 */
    --destructive-foreground: 0 0% 100%;
    --border: 215 20% 17%;             /* #1F2937 */
    --input: 215 20% 17%;
    --ring: 213 94% 68%;               /* accent blue for focus */
    --radius: 12px;
  }

  html {
    color-scheme: dark;
    background-color: #0B0D17;
  }

  body {
    @apply bg-bg-0 text-text-0 font-sans antialiased;
    font-feature-settings: 'cv02', 'cv03', 'cv04', 'cv11'; /* Inter stylistic sets */
  }

  /* Tabular numerals for any element opting in via font-mono or font-variant-numeric */
  .font-mono,
  [data-tabular] {
    font-variant-numeric: tabular-nums;
  }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      transition-duration: 0ms !important;
      animation-duration: 0ms !important;
    }
    /* Progress bars intentionally keep animating (convey state) */
    [role='progressbar'] > * {
      transition-duration: revert !important;
    }
  }
}
```

- [ ] **Step 2.5: Wire `index.css` into `main.tsx` and apply the `dark` class to `<html>`**

Replace `web/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import './index.css';

document.documentElement.classList.add('dark');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

Update `web/src/App.tsx` to use Tailwind classes so we can verify tokens work:

```tsx
export default function App() {
  return (
    <div className="min-h-dvh bg-bg-0 text-text-0 p-8 font-sans">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">
        Bambu Gateway
      </h1>
      <p className="text-text-1 text-sm mt-2">
        Foundation ready · <span className="text-accent">/beta</span>
      </p>
      <p className="font-mono text-xs text-text-2 mt-4" data-tabular>
        245/245° · tabular numerals
      </p>
    </div>
  );
}
```

- [ ] **Step 2.6: Verify Tailwind is compiling**

```bash
cd web && npm run dev
```

Expected at `http://localhost:5173/beta/`:
- Near-black background (`#0B0D17`, not Vite-default gray)
- Title "Bambu Gateway" in white, large+bold
- Subtitle with muted text and blue-accented "/beta"
- Monospaced `245/245°` with tabular alignment

Press `Ctrl+C` to stop.

- [ ] **Step 2.7: Verify `npm run build` still succeeds**

```bash
cd web && npm run build
```

Expected: builds without errors. Inspect `app/static/dist/assets/` — should contain a hashed `.css` file and hashed `.js` file.

- [ ] **Step 2.8: Commit**

```bash
git add web/tailwind.config.ts web/postcss.config.js web/src/index.css web/src/App.tsx web/src/main.tsx web/package.json web/package-lock.json
git commit -m "$(cat <<'EOF'
Wire Tailwind CSS with design tokens from spec

- Adds Tailwind 3 + PostCSS + autoprefixer
- tailwind.config.ts carries the full token palette (bg/surface/text/
  accent/warm/success/danger/info) and font families from the spec
- src/index.css declares shadcn CSS variables (HSL triplets) for the
  dark theme; dark class applied on <html> so it's always on
- prefers-reduced-motion collapses transitions to 0ms, except for
  progress bars which retain their animation because they convey state
- App.tsx updated to use tokens so the wiring is visibly verified

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Install shadcn/ui and the `Button` + `Sonner` primitives

**Files:**
- Create: `web/components.json`, `web/src/lib/utils.ts`, `web/src/components/ui/button.tsx`, `web/src/components/ui/sonner.tsx`

- [ ] **Step 3.1: Install shadcn/ui peer dependencies**

```bash
cd web && npm install class-variance-authority@^0.7.1 clsx@^2.1.1 tailwind-merge@^2.5.5 lucide-react@^0.460.0 tailwindcss-animate@^1.0.7 sonner@^1.7.1
```

Then add `tailwindcss-animate` to the plugins list in `web/tailwind.config.ts`:

```ts
// at the bottom of the config object, replace:
//   plugins: [],
// with:
  plugins: [require('tailwindcss-animate')],
```

- [ ] **Step 3.2: Create `web/components.json` (shadcn CLI config)**

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "new-york",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "src/index.css",
    "baseColor": "slate",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/utils",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

- [ ] **Step 3.3: Create `web/src/lib/utils.ts`**

```ts
import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 3.4: Install the `Button` component via shadcn CLI**

```bash
cd web && npx shadcn@latest add button --yes
```

Expected: creates `web/src/components/ui/button.tsx`. If the CLI asks interactive questions, the `--yes` flag should accept defaults; if it still asks, answer with our chosen settings (TypeScript: yes, Tailwind config path: `tailwind.config.ts`, CSS file: `src/index.css`, CSS variables: yes, alias `@/*`: confirm).

Verify the file exists:

```bash
ls web/src/components/ui/
```

Expected: `button.tsx`

- [ ] **Step 3.5: Install the `Sonner` toast component**

```bash
cd web && npx shadcn@latest add sonner --yes
```

Expected: creates `web/src/components/ui/sonner.tsx`.

- [ ] **Step 3.6: Smoke-test shadcn components in `App.tsx`**

Replace `web/src/App.tsx`:

```tsx
import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/sonner';
import { toast } from 'sonner';

export default function App() {
  return (
    <div className="min-h-dvh bg-bg-0 text-text-0 p-8 font-sans">
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">
        Bambu Gateway
      </h1>
      <p className="text-text-1 text-sm mt-2">
        Foundation ready · <span className="text-accent">/beta</span>
      </p>

      <div className="mt-6 flex gap-3">
        <Button onClick={() => toast.success('Primary action fired')}>
          Primary
        </Button>
        <Button variant="secondary" onClick={() => toast('Secondary action')}>
          Secondary
        </Button>
        <Button variant="destructive" onClick={() => toast.error('Destructive action')}>
          Destructive
        </Button>
        <Button variant="ghost">Ghost</Button>
      </div>

      <Toaster />
    </div>
  );
}
```

- [ ] **Step 3.7: Verify buttons render and toasts fire**

```bash
cd web && npm run dev
```

Visit `http://localhost:5173/beta/`. Expected:
- Four buttons: Primary (blue), Secondary (dark gray), Destructive (red), Ghost (transparent).
- Clicking each shows a toast at the bottom-right with matching tone.
- Focus ring is visible on `Tab` — a blue outline.

Press `Ctrl+C` to stop.

- [ ] **Step 3.8: Commit**

```bash
git add web/
git commit -m "$(cat <<'EOF'
Install shadcn/ui with Button and Sonner primitives

- Adds class-variance-authority, clsx, tailwind-merge, lucide-react,
  tailwindcss-animate, sonner as dependencies
- shadcn CLI initialized via components.json (new-york style, dark
  theme through CSS variables from index.css)
- Button and Sonner installed via the CLI; both wired into App.tsx
  as a smoke test (clicking any button fires a themed toast)
- lib/utils.ts exposes the canonical cn() helper

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Load Inter and JetBrains Mono fonts

**Files:**
- Modify: `web/src/main.tsx` (add font imports)

- [ ] **Step 4.1: Install variable font packages**

```bash
cd web && npm install @fontsource-variable/inter@^5.1.0 @fontsource-variable/jetbrains-mono@^5.1.1
```

These packages are ~1MB unpacked, but Vite tree-shakes to only the weights we import and emits one hashed `.woff2` per weight range into `dist/assets/`.

- [ ] **Step 4.2: Import the fonts in `main.tsx`**

Replace `web/src/main.tsx`:

```tsx
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';

// Variable font files — one per axis range. Vite bundles them into dist/assets/.
import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';

import './index.css';

document.documentElement.classList.add('dark');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 4.3: Verify fonts are loaded**

```bash
cd web && npm run dev
```

Visit `http://localhost:5173/beta/`. Open browser DevTools → Network tab → filter by "Font". You should see two `.woff2` files served: `inter-latin-wght-normal-*.woff2` (ish) and `jetbrains-mono-latin-wght-normal-*.woff2`. The page text should render in Inter (not the system fallback).

A quick visual check: the "Bambu Gateway" title should have Inter's distinctive round dots on lowercase letters (none in this title, but headings in later phases will show it). The monospaced `245/245°` line should be JetBrains Mono, not the default OS monospace.

Press `Ctrl+C`.

- [ ] **Step 4.4: Verify production build includes the font files**

```bash
cd web && npm run build
ls app/static/dist/assets/ | grep -i 'woff\|inter\|jetbrains'
```

Expected: at least two `.woff2` files listed.

- [ ] **Step 4.5: Commit**

```bash
git add web/package.json web/package-lock.json web/src/main.tsx
git commit -m "$(cat <<'EOF'
Self-host Inter and JetBrains Mono variable fonts

- Uses @fontsource-variable/inter and @fontsource-variable/jetbrains-mono
- Vite bundles the .woff2 files into app/static/dist/assets/ so no
  CDN round-trip and no FOUT in production
- font-sans and font-mono Tailwind utilities resolve to these families
  (with system fallbacks)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire React Router and the minimal AppShell

**Files:**
- Create: `web/src/components/app-shell.tsx`, `web/src/routes/dashboard.tsx`, `web/src/routes/print.tsx`, `web/src/routes/settings.tsx`
- Modify: `web/src/App.tsx`, `web/src/main.tsx`

- [ ] **Step 5.1: Install routing and query libraries**

```bash
cd web && npm install react-router-dom@^6.28.0 @tanstack/react-query@^5.62.7
```

- [ ] **Step 5.2: Create `web/src/routes/dashboard.tsx`, `print.tsx`, `settings.tsx`**

`web/src/routes/dashboard.tsx`:

```tsx
export default function DashboardRoute() {
  return (
    <>
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Dashboard</h1>
      <p className="text-text-1 text-sm mt-2">Phase 2 will fill this in.</p>
    </>
  );
}
```

`web/src/routes/print.tsx`:

```tsx
export default function PrintRoute() {
  return (
    <>
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Print</h1>
      <p className="text-text-1 text-sm mt-2">Phase 4 will fill this in.</p>
    </>
  );
}
```

`web/src/routes/settings.tsx`:

```tsx
export default function SettingsRoute() {
  return (
    <>
      <h1 className="text-[28px] font-extrabold tracking-tight text-white">Settings</h1>
      <p className="text-text-1 text-sm mt-2">Phase 5 will fill this in.</p>
    </>
  );
}
```

- [ ] **Step 5.3: Create `web/src/components/app-shell.tsx`**

This is Phase 1's minimal shell — just enough to prove routing works. Phase 2 will upgrade it.

```tsx
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { cn } from '@/lib/utils';

export function AppShell() {
  const location = useLocation();
  const onSettings = location.pathname === '/settings';

  return (
    <div className="min-h-dvh bg-bg-0 text-text-0 font-sans">
      <header className="flex items-center justify-between px-6 py-3.5 border-b border-border">
        {/* Brand */}
        <div className="flex items-center gap-2.5 text-[15px] font-bold text-white">
          <div className="w-2.5 h-2.5 rounded-[3px] bg-gradient-to-br from-accent to-accent-strong" />
          Bambu Gateway
        </div>

        {/* Tabs */}
        <nav
          className={cn(
            'flex gap-1 bg-bg-1 border border-border rounded-full p-[3px]',
            onSettings && 'opacity-50 pointer-events-none',
          )}
        >
          <TabLink to="/">Dashboard</TabLink>
          <TabLink to="/print">Print</TabLink>
        </nav>

        {/* Settings */}
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            cn(
              'flex items-center gap-1.5 px-3.5 py-1.5 rounded-full border border-border bg-bg-1 text-[13px] text-text-1 transition-colors duration-fast',
              !isActive && 'hover:text-white hover:bg-surface-1',
              isActive && 'text-white',
            )
          }
        >
          <span aria-hidden>⚙</span> Settings
        </NavLink>
      </header>

      <main className="max-w-[720px] mx-auto px-4 sm:px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}

function TabLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        cn(
          'px-4 py-1.5 rounded-full text-[13px] font-medium transition-colors duration-fast',
          isActive
            ? 'bg-surface-3 text-white font-semibold'
            : 'text-text-1 hover:text-white',
        )
      }
    >
      {children}
    </NavLink>
  );
}
```

- [ ] **Step 5.4: Rewrite `web/src/App.tsx` to set up routing**

Replace `web/src/App.tsx`:

```tsx
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AppShell } from '@/components/app-shell';
import DashboardRoute from '@/routes/dashboard';
import PrintRoute from '@/routes/print';
import SettingsRoute from '@/routes/settings';
import { Toaster } from '@/components/ui/sonner';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 4_000,
      refetchOnWindowFocus: true,
    },
  },
});

const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <AppShell />,
      children: [
        { index: true, element: <DashboardRoute /> },
        { path: 'print', element: <PrintRoute /> },
        { path: 'settings', element: <SettingsRoute /> },
      ],
    },
  ],
  { basename: '/beta' },
);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster />
    </QueryClientProvider>
  );
}
```

Note: `basename: '/beta'` tells React Router to treat `/beta/*` as the app root. So `<NavLink to="/">` actually resolves to `/beta/` in the browser address bar.

- [ ] **Step 5.5: Verify routing works end-to-end**

```bash
cd web && npm run dev
```

Test in the browser:
- `http://localhost:5173/beta/` → shows "Dashboard" title; Dashboard tab is the active pill
- `http://localhost:5173/beta/print` → shows "Print" title; Print tab is the active pill
- `http://localhost:5173/beta/settings` → shows "Settings" title; tabs pill looks dimmed, Settings pill is highlighted
- Clicking tab pills navigates without a full page reload (watch Network tab — no document fetch)
- Browser Back/Forward work correctly
- Direct-hit to `/beta/print` (hard refresh) works (Vite dev server is SPA-aware)

Press `Ctrl+C`.

- [ ] **Step 5.6: Verify production build still succeeds**

```bash
cd web && npm run build
```

Expected: no TypeScript errors, output written to `app/static/dist/`. Check that `app/static/dist/index.html` references `/beta/assets/index-HASH.js`.

```bash
grep -o '/beta/assets/[^"]*' app/static/dist/index.html | head -3
```

Expected: at least one line like `/beta/assets/index-abc123.js`.

- [ ] **Step 5.7: Commit**

```bash
git add web/
git commit -m "$(cat <<'EOF'
Add AppShell with routed Dashboard/Print/Settings tabs

- Installs react-router-dom v6 and @tanstack/react-query v5
- Router uses basename /beta so the app mounts under FastAPI's /beta route
- AppShell header: brand left, segmented tab pill center (Dashboard /
  Print), Settings pill right; tab pill dims when on /settings so only
  the Settings pill is highlighted
- Routes: /, /print, /settings — each is a placeholder with a page title
  that Phases 2, 4, 5 will build out
- QueryClientProvider wraps the router; 4s default staleTime matches
  the current poll cadence

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Wire FastAPI to serve the React app at `/beta`

**Files:**
- Modify: `app/main.py` (two small edits near line 236)

- [ ] **Step 6.1: Add imports and helper paths in `app/main.py`**

Open `app/main.py`. Find line 73:

```python
_APP_DIR = Path(__file__).resolve().parent
```

Add directly below it:

```python
_DIST_DIR = _APP_DIR / "static" / "dist"
```

Find the top-level `FileResponse` usage — if `FileResponse` isn't already imported from `fastapi.responses`, add it. Check the existing import:

```bash
grep "from fastapi.responses" app/main.py
```

If the line is currently `from fastapi.responses import HTMLResponse, Response, StreamingResponse`, change it to:

```python
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
```

- [ ] **Step 6.2: Mount `/beta/assets` and add SPA catch-all routes**

Find this block in `app/main.py` (around line 236):

```python
app.mount("/static", StaticFiles(directory=str(_APP_DIR / "static")), name="static")


# --- Web UI ---


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings.html")
```

Add *after* `app.mount("/static", ...)` and *before* the `# --- Web UI ---` comment:

```python
# --- New React UI (staged at /beta during Phase 1-5; becomes / at cutover) ---

if _DIST_DIR.exists():
    app.mount(
        "/beta/assets",
        StaticFiles(directory=str(_DIST_DIR / "assets")),
        name="beta-assets",
    )


@app.get("/beta", response_class=HTMLResponse)
@app.get("/beta/{path:path}", response_class=HTMLResponse)
async def beta_spa(path: str = ""):
    """Serve the React SPA shell for any /beta or /beta/<route> request.

    Hashed assets under /beta/assets/* are served by StaticFiles above.
    Everything else returns index.html so client-side routing works.
    """
    index_path = _DIST_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(
            status_code=503,
            detail="React bundle not built. Run 'cd web && npm run build'.",
        )
    return FileResponse(index_path, media_type="text/html")
```

The `if _DIST_DIR.exists()` guard on the mount is important: when a developer runs `python -m app` before ever running `npm run build`, the app still starts — only `/beta/*` returns 503 with a helpful message, while `/` continues to serve the Jinja UI.

- [ ] **Step 6.3: Build the React bundle so FastAPI has something to serve**

```bash
cd web && npm run build && cd ..
```

Verify `app/static/dist/index.html` exists.

- [ ] **Step 6.4: Start FastAPI and verify `/beta` serves the React app**

In a fresh terminal (from repo root, with venv activated):

```bash
python -m app
```

Expected: uvicorn starts on `:4844` (default port). In another terminal:

```bash
curl -sI http://localhost:4844/beta | head -1
curl -s http://localhost:4844/beta | grep -o '<title>[^<]*</title>'
curl -sI http://localhost:4844/beta/print | head -1
curl -sI http://localhost:4844/beta/assets/ | head -1
```

Expected:
- First: `HTTP/1.1 200 OK`
- Second: `<title>Bambu Gateway</title>`
- Third: `HTTP/1.1 200 OK` (SPA catch-all)
- Fourth: `HTTP/1.1 404 Not Found` (the directory listing isn't served, but asset files inside would be)

Open the browser to `http://localhost:4844/beta/`. Expected: same UI as the Vite dev server — dark background, header with Dashboard/Print tabs and Settings pill, "Dashboard" title in the body. Click tabs — they navigate without a full reload.

Also visit `http://localhost:4844/` — the **old Jinja UI** should still render unchanged (important: this is the zero-regression check).

Press `Ctrl+C` to stop FastAPI.

- [ ] **Step 6.5: Verify the 503 fallback behavior**

Temporarily remove the built bundle to confirm the friendly error:

```bash
rm -rf app/static/dist
python -m app &
sleep 2
curl -s http://localhost:4844/beta
curl -s http://localhost:4844/
kill %1
```

Expected:
- First curl: JSON like `{"detail":"React bundle not built. Run 'cd web && npm run build'."}` (status 503).
- Second curl: returns the old Jinja `<!DOCTYPE html>` — still works.

Rebuild:

```bash
cd web && npm run build && cd ..
```

- [ ] **Step 6.6: Commit**

```bash
git add app/main.py
git commit -m "$(cat <<'EOF'
Serve React SPA at /beta alongside existing Jinja UI

- Mounts /beta/assets for hashed JS/CSS/font files from app/static/dist
- /beta and /beta/{path:path} catch-all return dist/index.html so
  react-router's client-side routes work on hard-refresh and deep-link
- Guard: if dist/ is missing (fresh checkout with no npm run build yet),
  /beta returns 503 with a helpful message instead of 500; Jinja UI
  at / continues to work either way
- FileResponse added to the fastapi.responses import

Old UI at / and /settings is unchanged. Cutover happens in Phase 6.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Multi-stage Dockerfile build

**Files:**
- Modify: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 7.1: Create `.dockerignore` at the repo root**

```
.git
.gitignore
.venv
.env
.claude
.superpowers
__pycache__
*.pyc
*.pyo
data
printers.json
docs
tests
web/node_modules
web/dist
app/static/dist
.DS_Store
```

This keeps `web/node_modules` and any pre-built `dist/` out of the build context so the Node stage always builds from a clean `npm ci`.

- [ ] **Step 7.2: Replace `Dockerfile` with a multi-stage build**

```dockerfile
# syntax=docker/dockerfile:1

# --- Stage 1: Build the React frontend ---
FROM node:20-alpine AS web-builder

WORKDIR /web

# Copy lockfile first for better layer caching
COPY web/package.json web/package-lock.json ./
RUN npm ci

# Copy the rest of the frontend sources and build
COPY web/ ./
RUN npm run build
# npm run build writes to /web/../app/static/dist per vite.config.ts,
# so the output lands at /app/static/dist inside the build container.


# --- Stage 2: Python runtime ---
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/

# Overlay the frontend build output from stage 1
COPY --from=web-builder /app/static/dist /app/app/static/dist

VOLUME /data

EXPOSE 4844

CMD ["python", "-m", "app", "-c", "/data/printers.json"]
```

Two subtleties:

1. **Output path inside the Node stage.** Vite's `outDir` is `../app/static/dist` relative to `web/`. When the Node stage has `WORKDIR /web`, the computed absolute path is `/app/static/dist`. That's why the copy source is `/app/static/dist`.
2. **Target path in the runtime stage.** WORKDIR is `/app` and the Python app code is copied to `/app/app/`, so the frontend dist needs to land at `/app/app/static/dist` to be picked up by `_DIST_DIR = _APP_DIR / "static" / "dist"` at runtime.

- [ ] **Step 7.3: Build the image locally (not via deploy-docker.sh)**

Per the user's global CLAUDE.md, the team uses `deploy-docker.sh` for real deployments and does **not** build on this machine. But for Phase 1's smoke test we need to verify the Dockerfile is correct — build locally once, confirm, then let the normal deploy flow take over.

```bash
docker build -t bambu-gateway:phase-1-smoke .
```

Expected:
- Stage 1 (`web-builder`) runs `npm ci` (takes ~30-60s) then `npm run build` (~5s)
- Stage 2 installs Python deps and copies `app/` + the frontend dist
- Final image tagged `bambu-gateway:phase-1-smoke`
- Total time ~2 min depending on network

- [ ] **Step 7.4: Run the built image and confirm `/beta` works inside Docker**

```bash
docker run --rm -d --name bg-smoke -p 4844:4844 \
  -v "$(pwd)/printers.example.json:/data/printers.json:ro" \
  bambu-gateway:phase-1-smoke
sleep 3
curl -sI http://localhost:4844/beta | head -1
curl -s http://localhost:4844/beta | grep -o '<title>[^<]*</title>'
curl -sI http://localhost:4844/ | head -1
docker stop bg-smoke
```

Expected:
- `/beta` → `HTTP/1.1 200 OK`, title `<title>Bambu Gateway</title>`
- `/` → `HTTP/1.1 200 OK` (old Jinja UI still served)

If the container exits immediately or `curl` fails, inspect `docker logs bg-smoke` before stopping.

- [ ] **Step 7.5: Remove the smoke-test image**

```bash
docker image rm bambu-gateway:phase-1-smoke
```

- [ ] **Step 7.6: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "$(cat <<'EOF'
Multi-stage Dockerfile builds React frontend with Node, serves from Python

- New web-builder stage on node:20-alpine runs npm ci + npm run build;
  Vite writes to /app/static/dist inside that stage
- Python stage copies the built dist into /app/app/static/dist so the
  FastAPI catch-all at /beta can serve it
- .dockerignore added to keep node_modules, dist, docs, tests, and
  local state (.venv, printers.json, .claude) out of the build context
- Smoke-tested locally with docker build + run; both / (Jinja) and
  /beta (React) respond 200

Normal deploys continue via deploy-docker.sh; this commit only updates
the recipe so the remote build produces the new layout.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Final verification + README note

**Files:**
- Modify: `README.md` (short new section)

- [ ] **Step 8.1: Re-verify the dev workflow from a clean state**

```bash
cd web && npm run dev
```

In another terminal:

```bash
python -m app
```

Expected: Vite on `:5173`, FastAPI on `:4844`. Both of these URLs should work:

- `http://localhost:5173/beta/` (Vite dev server with HMR; proxies `/api/*` to `:4844`)
- `http://localhost:4844/beta/` (FastAPI serving the last `npm run build` output)
- `http://localhost:4844/` (old Jinja UI)

Stop both.

- [ ] **Step 8.2: Append a short section to `README.md`**

Find the existing "Running the App" section in `README.md` and append the following **after** that section (not inside it):

```markdown
### Frontend (new UI, staged at /beta)

The new React UI is in `web/` (Vite + React + TypeScript + Tailwind + shadcn/ui). During the rollout it's served at `/beta` alongside the existing Jinja UI at `/`; the two coexist until the Phase 6 cutover.

**Dev:**

```bash
cd web
npm install
npm run dev       # http://localhost:5173/beta/ with HMR; /api proxied to :4844
```

Run the Python backend in a separate terminal (`python -m app` or `uvicorn app.main:app --reload`).

**Production build:**

```bash
cd web
npm run build     # writes app/static/dist/{index.html, assets/*}
```

FastAPI picks up the build output automatically — restart `python -m app` and visit `http://localhost:4844/beta/`.

**Docker:** the Dockerfile runs the Node build stage automatically; no extra steps needed for `docker compose up -d` or the usual `deploy-docker.sh` flow.
```

- [ ] **Step 8.3: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
README: document the new /beta frontend workflow

- Dev: cd web && npm install && npm run dev (Vite on 5173, HMR, /api proxy)
- Production: npm run build writes to app/static/dist
- Docker: multi-stage build handles it automatically

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 8.4: Final audit checklist**

Run these checks — all must pass:

```bash
# 1. Clean build succeeds
cd web && rm -rf node_modules ../app/static/dist && npm ci && npm run build && cd ..

# 2. Python app starts without import errors
python -c "from app.main import app; print('OK')"

# 3. TypeScript is happy
cd web && npm run lint && cd ..

# 4. Git status is clean (the plan committed everything it should have)
git status
```

Expected:
1. Build completes, `app/static/dist/index.html` exists.
2. Prints `OK`.
3. `tsc --noEmit` exits 0.
4. `nothing to commit, working tree clean` (no stray files).

---

## Done criteria (Phase 1)

All of the following hold:

- `cd web && npm run dev` serves a working React app at `http://localhost:5173/beta/` with HMR and a working `/api` proxy to FastAPI.
- `cd web && npm run build` produces `app/static/dist/index.html` and `dist/assets/*.{js,css,woff2}` with `/beta/` as the base path.
- `python -m app` serves:
  - `/` → old Jinja UI (unchanged)
  - `/settings` → old Jinja settings (unchanged)
  - `/beta` → React app
  - `/beta/print`, `/beta/settings` → React app (SPA catch-all)
  - `/beta/assets/*` → hashed static files
  - `/api/*` → unchanged FastAPI endpoints
- Client-side routing works on hard-refresh and deep-link (e.g. pasting `http://localhost:4844/beta/print` into a fresh tab lands on the Print route).
- `docker build .` succeeds and the resulting image responds 200 on both `/` and `/beta`.
- `git status` is clean; all 8 commits are on `main`.
- No new dependencies or code in `app/` beyond the two edits to `app/main.py`.

---

## Self-review notes

**Spec coverage (Phase 1 scope only):**

Phase 1 of the spec's "Incremental ship plan" states:

> Add `web/` Vite project, Tailwind config with tokens, shadcn scaffold, FastAPI wiring that serves `app/static/dist/index.html` for `/beta` and `/beta/*`, Dockerfile multi-stage build. Ship a minimal React shell (header + empty Dashboard route) visible at `/beta`. The existing Jinja routes at `/` and `/settings` are untouched.

Mapped to tasks:
- `web/` Vite project → Task 1 ✓
- Tailwind config with tokens → Task 2 ✓
- shadcn scaffold → Task 3 ✓
- (Spec also implies fonts) → Task 4 ✓
- (Spec also implies routing under `/beta`) → Task 5 ✓
- FastAPI wiring for `/beta` and `/beta/*` → Task 6 ✓
- Dockerfile multi-stage build → Task 7 ✓
- "Minimal React shell (header + empty Dashboard route) visible at `/beta`" → Task 5 delivers AppShell + Dashboard placeholder ✓
- "Existing Jinja routes at `/` and `/settings` are untouched" → verified in Task 6 Step 6.4 ✓

**Placeholder scan:** No "TBD"/"TODO"/"implement later"/"handle edge cases"/"similar to Task N" patterns in any task. All code blocks are complete.

**Type consistency:** Token names (`bg-0`, `surface-1`, `accent`, etc.) used consistently between `tailwind.config.ts` (Task 2) and component class names in `app-shell.tsx` (Task 5). Font family tokens `font-sans` and `font-mono` resolve to the packages installed in Task 4. Router `basename: '/beta'` (Task 5) matches the FastAPI catch-all mount (Task 6) and Vite `base: '/beta/'` (Task 1). Dockerfile copy paths (Task 7) match the build output location from `vite.config.ts` (Task 1).

**Assumptions worth flagging at execution time:**
- Node ≥ 20 on the build machine (enforced in `web/package.json` `engines` + `.npmrc engine-strict=true`).
- FastAPI port is 4844 (the current default — confirmed in the existing Dockerfile and the `EXPOSE 4844` line).
- `printers.example.json` exists in the repo root (used in Task 7 smoke-test). Already present per `ls` run during brainstorming.
