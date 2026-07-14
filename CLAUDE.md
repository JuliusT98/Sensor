# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

UAV multispectral sensor pipeline for spray-optimization flights. Python backend collects GPS telemetry (MAVLink/Pixhawk) and sensor measurements (HTTP API), synchronizes them, and outputs georeferenced maps.

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

## Architecture

### Data Flow

```
Hardware → Python backend → output/flight_data.json
```

1. **`src/mission.py`** orchestrates a full mission (thin CLI layer). The actual logic lives in dedicated modules:
   - **`src/config.py`** — all constants (hardware addresses, thresholds, paths)
   - **`src/gps_tracker.py`** — `GpsBuffer` (thread-safe, crash-safe live CSV) + `GpsTracker` (MAVLink thread: GPS time via SYSTEM_TIME, QGC survey progress, HDOP gating, link watchdog + reconnect, landing detection via disarm/landed_state)
   - **`src/sensor_client.py`** — `SensorClient`, a robust wrapper (timeouts, retries, clock calibration, in-flight health monitor) around the vendor's `command_dispatcher_controller.py` (do not modify the vendor files `command_dispatcher_controller.py` / `log_file_parser.py`)
   - **`src/flight_data.py`** — loading/parsing sensor CSVs and GPS logs, cross-correlation time offset (fallback only)
   - **`src/map_builder.py`** — `MapBuilder` (CesiumJS/MapLibre HTML map + `flight_data.json` export)
2. **`src/ndvi_pipeline.py`** does the same pipeline offline from existing `.bin` (ArduPilot) or `.ulg` (PX4) log files.
3. **`src/live_mission.py`** runs on a Raspberry Pi — continuously polls GPS + sensor, regenerates a map PNG every 5 s, and serves it on port 8080.
4. **`output/flight_data.json`** contains `meta` (statistics, bounds, timestamps) and `points` (lat, lon, alt, value, RGB color).

### Key Hardware Constants (in Python scripts)

- Sensor PC HTTP API: `http://192.168.2.30:8100`
- MAVLink telemetry port: `COM3` (Windows) / `/dev/ttyUSB0` (Linux)
- Baud rate: 57600
- Sensor polling rate: 10 Hz

## Development Notes

- The Python environment is in `venv/` — activate before running scripts.
- `data/` holds raw sensor ZIP archives and GPS log files; `output/` holds generated maps. Both are gitignored.
