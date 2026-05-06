# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

UAV multispectral sensor pipeline for spray-optimization flights. Python backend collects GPS telemetry (MAVLink/Pixhawk) and sensor measurements (HTTP API), synchronizes them, and outputs georeferenced maps. A Next.js dashboard renders the results interactively.

## Commands

### Python Backend

```bash
# Run a full simulated mission (no real hardware needed)
python src/mission.py --simulate --sim-sensor

# Run with real hardware (adjust --port as needed)
python src/mission.py --port COM3

# Post-process an existing flight log
python src/ndvi_pipeline.py

# Headless real-time mode (Raspberry Pi)
python src/live_mission.py
```

### Dashboard (Next.js)

```bash
cd dashboard

# Development server
npm run dev

# Production build + start
npm run build && npm start

# Run Playwright E2E tests
npx playwright test

# Run a single test file
npx playwright test tests/dashboard.spec.ts
```

## Architecture

### Data Flow

```
Hardware → Python backend → output/flight_data.json → Dashboard API → UI
```

1. **`src/mission.py`** orchestrates a full mission: starts sensor via HTTP, logs GPS via MAVLink, detects landing, downloads sensor logs, synchronizes timestamps (via cross-correlation), and outputs georeferenced maps.
2. **`src/ndvi_pipeline.py`** does the same pipeline offline from existing `.bin` (ArduPilot) or `.ulg` (PX4) log files.
3. **`src/live_mission.py`** runs on a Raspberry Pi — continuously polls GPS + sensor, regenerates a map PNG every 5 s, and serves it on port 8080.
4. **`output/flight_data.json`** is the contract between Python and the dashboard: contains `meta` (statistics, bounds, timestamps) and `points` (lat, lon, alt, value, RGB color).

### Key Hardware Constants (in Python scripts)

- Sensor PC HTTP API: `http://192.168.2.30:8100`
- MAVLink telemetry port: `COM3` (Windows) / `/dev/ttyUSB0` (Linux)
- Baud rate: 57600
- Sensor polling rate: 10 Hz

### Dashboard API Routes

- `GET /api/flight-data` — reads `../output/flight_data.json` (relative to the dashboard directory)
- `GET /api/tile` — proxies ArcGIS World Imagery satellite tiles; cached 24 h server-side

### Dashboard Components

- **`FlightMap3D.tsx`** — Three.js/React Three Fiber: satellite tile plane + 3D flight path with orbit controls
- **`Heatmap.tsx`** — Cesium.js viewer with a custom jet-colormap Gaussian blur overlay
- **`StatsPanel.tsx`** — Recharts histogram, hotspot/coldspot lists, color legend
- **`page.tsx`** — toggles between the two views; all UI text is German (`de-AT` locale)

### TypeScript Types (`src/lib/types.ts`)

`FlightPoint { lat, lon, alt, value, r, g, b }` — one georeferenced measurement  
`FlightData { meta: FlightMeta, points: FlightPoint[], path: [lat,lon,alt][] }`  
`FlightMeta { min, max, mean, std, count, bounds, label, timestamp }`

## Development Notes

- The Python environment is in `venv/` — activate before running scripts.
- `data/` holds raw sensor ZIP archives and GPS log files; `output/` holds generated maps. Both are gitignored.
- Playwright tests mock the `/api/flight-data` endpoint — they do not require a running Python backend.
