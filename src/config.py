"""
Zentrale Konfiguration der Mission-Pipeline.

Alle Hardware-Adressen, Schwellwerte und Pfade an einer Stelle —
die anderen Module importieren von hier.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent  # Projektroot, egal von wo das Script gestartet wird

# ---------------------------------------------------------------------------
# Sensor (HTTP-API auf dem RPi)
# ---------------------------------------------------------------------------

SENSOR_URL          = "http://192.168.2.30:8100"
SENSOR_MODULE_ID    = 1
STREAM_PERIOD_MS    = 100

# Zeitzone des Sensor-PCs relativ zu UTC (z.B. 2 für CEST, 1 für CET, 0 für UTC)
SENSOR_UTC_OFFSET_H = 2

# HTTP — Timeouts/Retries um die Hersteller-Klasse herum
HTTP_TIMEOUT_S          = 10
HTTP_DOWNLOAD_TIMEOUT_S = 120   # Log-Download kann bei großen ZIPs dauern
HTTP_RETRIES            = 3
SENSOR_HEALTH_PERIOD_S  = 5.0   # Sensor-Status-Poll während des Flugs

# ---------------------------------------------------------------------------
# Telemetrie (MAVLink / Pixhawk)
# ---------------------------------------------------------------------------

TELEMETRY_PORT   = "COM3"     # Windows: COM3 / Linux (RPi): /dev/ttyUSB0
TELEMETRY_BAUD   = 57600

# Telemetrie-Watchdog
LINK_WARN_S      = 10.0       # Warnung, wenn so lange keine Telemetrie ankommt
LINK_LOST_S      = 30.0       # danach Reconnect-Versuch
MISSION_MAX_S    = 45 * 60    # Not-Stopp, falls weder Disarm noch ENTER kommt

# Landungs-Erkennung: Disarm / landed_state sind maßgeblich.
# Die Höhen-Heuristik gibt nur noch einen Hinweis aus (Baro-Rauschen bei ~1.5 m Flughöhe).
LAND_ALT_M       = 0.3        # Unter dieser Höhe (relativ) gilt Drohne als "tief"
LAND_SPEED_MS    = 0.5        # Unter dieser Geschwindigkeit gilt Drohne als "langsam"
LAND_CONFIRM_S   = 3.0        # Wie lange die Bedingungen erfüllt sein müssen
TAKEOFF_ALT_M    = 1.0        # Über dieser Höhe gilt Drohne als "abgehoben" — erst danach zählt Landungserkennung

# ---------------------------------------------------------------------------
# Daten & Karte
# ---------------------------------------------------------------------------

LOG_DIR          = ROOT / "data/sensor_logs"
GPS_CSV          = ROOT / "data/gps_last_flight.csv"
MAP_CHANNEL      = None       # None = alle Kanäle mitteln, N = Kanal N
OUTPUT_PNG       = str(ROOT / "output/sensor_map.png")
MIN_HDOP         = 3.0        # Punkte mit schlechterem HDOP werden verworfen
MAX_GPS_GAP_S    = 2.0
AUTO_SYNC        = True       # Nur relevant, wenn keine Uhr-Kalibrierung möglich war
TIME_OFFSET_S    = 0.0

# Visualisierungs-Engine: "cesium" | "maplibre"
MAP_ENGINE       = "maplibre"

# Interaktive HTML-Karte erzeugen? False auf dem Raspberry Pi — dort wird nur
# GeoJSON/JSON exportiert (die Karte öffnet ohnehin niemand auf dem Pi selbst).
BUILD_HTML_MAP   = False

# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

SIMULATE_DIR     = str(ROOT / "data/sensor_logs/measurements_2026-04-28_1")
SIMULATE_PREFIX  = "NI_2026-04-28_1_sensor1"
