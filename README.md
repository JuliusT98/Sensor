# Drone Multispectral Sensor Mapping System

A UAV-based agricultural monitoring system that synchronizes GPS telemetry from a Pixhawk autopilot with multispectral sensor measurements to produce georeferenced maps and GeoTIFF exports.

---

## Hardware Requirements

| Component | Details |
|-----------|---------|
| **Flight Controller** | Pixhawk 4 (or compatible ArduPilot/PX4) |
| **Telemetry Radio** | MAVLink-compatible (57600 baud), USB to ground station |
| **Multispectral Sensor** | HTTP API at `http://192.168.2.30:8100`, 8 channels × 4 detectors |
| **Ground Station PC** | Windows (COM3) or Raspberry Pi (headless, live mapping) |
| **Sensor PC** | Hosts the sensor HTTP API on local network `192.168.2.x` |

### Sensor Data Channels
- **NI** — Near-Infrared, 32 channels (8 channels × 4 detectors)
- **MUR** — 32 channels (8 channels × 4 detectors)
- Measurement rate: 10 Hz (100 ms polling interval)

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/JuliusT98/Sensor.git
cd Sensor
```

### 2. Install Python dependencies

Requires **Python 3.8+**.

```bash
pip install -r requirements.txt
```

**Packages installed:**

| Package | Purpose |
|---------|---------|
| `pandas` | CSV data manipulation |
| `numpy` | Numerical computation |
| `scipy` | Signal correlation & interpolation |
| `requests` | HTTP client for sensor API |
| `pymavlink` | MAVLink telemetry (Pixhawk/ArduPilot) |
| `pyulog` | PX4 ULog binary format support |
| `rasterio` | GeoTIFF raster output |
| `pyproj` | Coordinate transformation (UTM ↔ WGS84) |
| `matplotlib` | Map visualization & PNG export |
| `jsbeautifier` | Formatted JSON statistics output |

Optional (for serial port auto-detection):
```bash
pip install pyserial
```

### 3. Network setup

Ensure the sensor PC is reachable at `http://192.168.2.30:8100` on the local network.  
Connect the telemetry radio via USB before running any script.

---

## Configuration

All configuration is defined in constants at the top of each script. Edit these before running:

### `mission.py` (main configuration)

```python
SENSOR_URL          = "http://192.168.2.30:8100"   # Sensor PC IP and port
SENSOR_MODULE_ID    = 1                             # Sensor module index (starts at 1)
STREAM_PERIOD_MS    = 100                           # Measurement interval in ms (10 Hz)

SENSOR_UTC_OFFSET_H = 2                             # Sensor PC timezone offset (e.g. 2 = CEST)
TELEMETRY_PORT      = "COM3"                        # Windows: COM3 | Linux/RPi: /dev/ttyUSB0
TELEMETRY_BAUD      = 57600                         # Telemetry radio baud rate

LOG_DIR             = Path("log_files")             # Where sensor logs are saved
MAP_CHANNEL         = None                          # None = average all channels, N = specific channel
RESOLUTION_M        = 0.5                           # GeoTIFF pixel size in meters
OUTPUT_TIF          = "sensor_map.tif"
OUTPUT_PNG          = "sensor_map.png"
UTM_EPSG            = 32633                         # 32632 = West Germany, 32633 = East Germany/Austria

MIN_HDOP            = 3.0                           # GPS accuracy threshold (lower = stricter)
MAX_GPS_GAP_S       = 2.0                           # Max allowed gap between GPS points (seconds)

LAND_ALT_M          = 2.0                           # Landing detection: altitude threshold (meters)
LAND_SPEED_MS       = 0.5                           # Landing detection: speed threshold (m/s)
LAND_CONFIRM_S      = 3.0                           # Seconds conditions must hold to confirm landing
```

### `live_mission.py` (Raspberry Pi live mapping)

```python
TELEMETRY_PORT      = "/dev/ttyUSB0"               # Adjust to your RPi port
HTTP_PORT           = 8080                          # Browser access port
MAP_INTERVAL_S      = 5                             # Map refresh interval (seconds)
```

### `ndvi_pipeline.py` (offline post-processing)

```python
LOG_FILE            = "logs/00000001.BIN"           # ArduPilot .bin or PX4 .ulg file
SENSOR_DIR          = "log_files/measurements_..."  # Folder with extracted sensor CSVs
SENSOR_PREFIX       = "NI_2026-04-13_1_sensor1"    # CSV filename prefix
AUTO_SYNC           = True                          # Auto time-offset estimation
TIME_OFFSET_S       = 0.0                           # Manual offset override (if AUTO_SYNC=False)
```

---

## Usage

### Option A — Full Automatic Mission (with drone + sensor hardware)

```bash
# Windows (telemetry on COM3)
python src/mission.py

# Linux / Raspberry Pi
python src/mission.py --port /dev/ttyUSB0

# Simulate sensor data (real GPS, no sensor hardware)
python src/mission.py --sim-sensor

# Full simulation (no hardware at all)
python src/mission.py --simulate

# Manual landing confirmation instead of auto-detect
python src/mission.py --manual-stop

# Re-process last flight without flying again
python src/mission.py --remap
```

**What happens step by step:**
1. Starts sensor measurement recording via HTTP
2. Connects to telemetry radio and logs GPS at ~4 Hz
3. Waits for landing (auto-detects via altitude + speed, or press Enter)
4. Downloads sensor log as ZIP archive
5. Extracts and parses CSV data (timestamps, MUR, NI values)
6. Estimates clock offset between sensor PC and GPS timestamps
7. Interpolates GPS positions to every sensor measurement
8. Generates interactive HTML map (CesiumJS 3D globe) + GeoTIFF + PNG

**Output files:**
```
sensor_map.tif      — Georeferenced raster (GeoTIFF)
sensor_map.png      — Visual overview map
sensor_map.html     — Interactive 3D map (open in browser)
gps_last_flight.csv — Raw GPS telemetry log
log_files/          — Downloaded sensor CSVs
```

---

### Option B — Live Real-Time Mapping (Raspberry Pi, headless)

```bash
# Auto-detect telemetry port
python src/live_mission.py

# Specify port manually
python src/live_mission.py --port /dev/ttyUSB0

# List available serial ports
python src/live_mission.py --list-ports
```

**What it does:**
- Polls GPS continuously via MAVLink
- Polls sensor at 10 Hz via HTTP
- Regenerates a map PNG every 5 seconds
- Serves the map at `http://<raspi-ip>:8080` — open in any browser on the same network

---

### Option C — Offline Post-Processing (no drone, existing log files)

Use this when you already have `.bin`/`.ulg` flight logs and sensor CSVs:

```bash
python src/ndvi_pipeline.py
```

Configure `LOG_FILE`, `SENSOR_DIR`, and `SENSOR_PREFIX` at the top of the file before running.

Supported log formats:
- **ArduPilot** `.bin` (Pixhawk with ArduPilot firmware)
- **PX4** `.ulg` (Pixhawk with PX4 firmware)

---

### Utility Scripts

```bash
# Parse a downloaded sensor ZIP archive manually
python src/log_file_parser.py <path_to_zip>

# Test sensor HTTP API connection
python src/command_dispatcher_controller.py

# Demo map visualization with sample data
python src/demo_map.py
```

---

## Project File Overview

```
Sensor/
├── src/
│   ├── mission.py                      # Main mission script (full pipeline)
│   ├── live_mission.py                 # Real-time live mapping (Raspberry Pi)
│   ├── ndvi_pipeline.py                # Offline post-processing pipeline
│   ├── command_dispatcher_controller.py # Low-level sensor HTTP API wrapper
│   ├── log_file_parser.py              # Sensor ZIP log parser
│   └── demo_map.py                     # Demo visualization with sample data
├── data/
│   ├── sensor_logs/                    # Downloaded sensor logs (CSV archives)
│   ├── flight_logs/                    # ArduPilot .bin / PX4 .ulg files
│   └── gps_last_flight.csv             # GPS telemetry from last flight
├── output/                             # Generated files (GeoTIFF, PNG, HTML maps)
├── venv/                               # Virtual environment (not in git)
├── requirements.txt
└── README.md
```

---

## Troubleshooting

**Telemetry port not found**
- Windows: Check Device Manager for the correct COM port
- Linux: Run `python live_mission.py --list-ports` or `ls /dev/tty*`
- Make sure the telemetry radio is plugged in before running

**Sensor API not reachable**
- Verify the sensor PC is on and connected to the same local network
- Ping `192.168.2.30` to test connectivity
- Check that port 8100 is not blocked by a firewall

**Poor GPS quality / missing map points**
- Lower `MIN_HDOP` if too many points are filtered out (default 3.0)
- Ensure open sky view — avoid flying near buildings or trees

**Wrong UTM zone (map appears shifted)**
- Set `UTM_EPSG` to the correct zone:
  - `32632` — West Germany, western Austria
  - `32633` — East Germany, eastern Austria, most of Central Europe

**Clock sync issues (sensor and GPS data don't align)**
- Set `SENSOR_UTC_OFFSET_H` to match the sensor PC's UTC offset (e.g. `2` for CEST)
- Enable `AUTO_SYNC = True` in `ndvi_pipeline.py` for automatic correction
