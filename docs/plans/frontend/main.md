# Frontend Dashboard Plan

## Overview
Professional analytics dashboard at `/dashboard` with dark/light theme toggle.
Split into separate HTML, JS, CSS files. Public access (no auth).

## Tech Stack
- **Tailwind CSS** (CDN) — styling
- **Alpine.js** (CDN) — reactivity, tabs, filters, theme toggle
- **Chart.js** (CDN) — timeseries charts
- **Fetch API** — calls `/v1/stats`, `/v1/logs`, `/v1/stats/timeseries`

## File Structure
```
app/static/
├── dashboard.html    # Layout, structure, Alpine.js components
├── dashboard.js      # Data fetching, chart rendering, state management
└── dashboard.css     # Custom styles, theme variables, transitions
```

## Phases

### Phase F1: Layout + Stats Cards
- Skeleton HTML with navigation
- Dark/light theme toggle (stored in localStorage)
- Stats cards from `/v1/stats` (requests, cost, avg TPS, errors, p50/p95, TTFT)
- By-model breakdown cards
- Serve static files from FastAPI

### Phase F2: Generations Table
- Paginated logs table from `/v1/logs`
- Filters: model, origin, is_stream
- Pagination controls
- Formatted timestamps, cost, TPS

### Phase F3: Charts
- Throughput line chart (avg TPS over time)
- Cost/requests bar chart
- Bucket selector (hour/day)
- Responsive canvas sizing

### Phase F4: Polish
- Loading skeletons
- Auto-refresh toggle (30s interval)
- Error states
- Responsive mobile layout
- Smooth transitions between themes

## Route
`GET /dashboard` → serves `dashboard.html`
Static files at `/static/` → serves JS/CSS
