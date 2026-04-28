"""
Live Mission: Echtzeit-Sensor-Mapping während des Fluges (Headless RPi)

Architektur:
  Thread GPS    → MAVLink Telemetrie-Radio → GPS-Punkte
  Thread Sensor → HTTP API                 → MUR-Messwerte
  Thread Map    → Karte alle MAP_INTERVAL_S Sekunden als PNG speichern
  HTTP-Server   → PNG über Browser abrufbar (http://<raspi-ip>:8080)

Zeitstempel: Beide Threads nutzen datetime.now(UTC) → kein Offset nötig.

Verwendung:
  python live_mission.py                        Autoerkennung Telemetrie-Port
  python live_mission.py --port /dev/ttyUSB0    Port manuell setzen
  python live_mission.py --list-ports           Verfügbare Ports anzeigen

Abhängigkeiten (RPi):
  pip install pymavlink requests pandas numpy scipy pyproj matplotlib
"""

import argparse
import io
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # kein Display nötig — muss vor pyplot-Import stehen
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.interpolate import griddata, interp1d
from pyproj import Transformer

from command_dispatcher_controller import CommandDispatcherController


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

SENSOR_URL       = "http://192.168.2.30:8100"
SENSOR_MODULE_ID = 1
SENSOR_POLL_HZ   = 10

TELEMETRY_PORT   = "/dev/ttyUSB0"   # RPi: /dev/ttyUSB0 oder /dev/ttyAMA0
TELEMETRY_BAUD   = 57600

MAP_CHANNEL      = None    # None = alle Kanäle mitteln, N = Kanal N
MAP_INTERVAL_S   = 5       # Karte alle N Sekunden neu generieren
MIN_POINTS_MAP   = 10
RESOLUTION_M     = 0.5

OUTPUT_PNG       = "/tmp/sensor_map.png"   # Wird vom HTTP-Server ausgeliefert
OUTPUT_TIF       = "sensor_map_live.tif"   # Wird am Ende gespeichert
HTTP_PORT        = 8080                    # Browser: http://<raspi-ip>:8080
UTM_EPSG         = 32633
MIN_HDOP         = 3.0
BUFFER_SIZE      = 0      # 0 = unbegrenzt


# ---------------------------------------------------------------------------
# Datenpuffer (thread-safe)
# ---------------------------------------------------------------------------

class DataBuffer:
    def __init__(self):
        self._lock        = threading.Lock()
        self._gps         = deque(maxlen=BUFFER_SIZE or None)
        self._sensor      = deque(maxlen=BUFFER_SIZE or None)
        self.running      = True
        self.gps_count    = 0
        self.sensor_count = 0
        self.status       = "Starte ..."

    def add_gps(self, ts, lat, lon, alt):
        with self._lock:
            self._gps.append((ts, lat, lon, alt))
            self.gps_count += 1

    def add_sensor(self, ts, mur_values: dict):
        with self._lock:
            self._sensor.append((ts, mur_values))
            self.sensor_count += 1

    def get_merged(self) -> pd.DataFrame:
        with self._lock:
            gps_list    = list(self._gps)
            sensor_list = list(self._sensor)

        if len(gps_list) < 2 or len(sensor_list) < 2:
            return pd.DataFrame()

        gps_df = pd.DataFrame(gps_list, columns=["timestamp", "lat", "lon", "alt"])
        gps_df = gps_df.set_index("timestamp").sort_index()

        rows = [{"timestamp": ts, **mur} for ts, mur in sensor_list]
        sen_df = pd.DataFrame(rows).set_index("timestamp").sort_index()

        t0 = max(gps_df.index.min(), sen_df.index.min())
        t1 = min(gps_df.index.max(), sen_df.index.max())
        if t0 >= t1:
            return pd.DataFrame()

        sen_df = sen_df[(sen_df.index >= t0) & (sen_df.index <= t1)]
        gps_df = gps_df[(gps_df.index >= t0) & (gps_df.index <= t1)]

        gps_ns = gps_df.index.astype(np.int64)
        sen_ns = sen_df.index.astype(np.int64)
        for col in ["lat", "lon", "alt"]:
            fn           = interp1d(gps_ns, gps_df[col].values, kind="linear",
                                    bounds_error=False, fill_value=np.nan)
            sen_df[col]  = fn(sen_ns)

        return sen_df.dropna(subset=["lat", "lon"])

    def get_stats(self) -> dict:
        df = self.get_merged()
        return {
            "gps_count":    self.gps_count,
            "sensor_count": self.sensor_count,
            "merged_points": len(df),
            "status":       self.status,
        }


# ---------------------------------------------------------------------------
# Thread 1: GPS via MAVLink
# ---------------------------------------------------------------------------

def gps_thread(buf: DataBuffer, port: str, baud: int):
    from pymavlink import mavutil

    buf.status = f"GPS: Verbinde {port} ..."
    print(f"[GPS] Verbinde {port} @ {baud} baud ...")

    try:
        mav = mavutil.mavlink_connection(port, baud=baud)
        mav.wait_heartbeat(timeout=15)
        print(f"[GPS] Verbunden — System {mav.target_system}")
        buf.status = "GPS: Verbunden"
    except Exception as e:
        print(f"[GPS] Fehler: {e}")
        buf.status = f"GPS: Fehler ({e})"
        return

    while buf.running:
        msg = mav.recv_match(
            type=["GLOBAL_POSITION_INT", "GPS_RAW_INT"],
            blocking=True, timeout=2.0
        )
        if msg is None:
            continue

        ts = datetime.now(timezone.utc)

        if msg.get_type() == "GLOBAL_POSITION_INT":
            lat = msg.lat / 1e7
            lon = msg.lon / 1e7
            if lat == 0.0 and lon == 0.0:
                continue
            buf.add_gps(ts, lat, lon, msg.alt / 1000.0)

        elif msg.get_type() == "GPS_RAW_INT":
            if msg.fix_type < 3:
                continue
            if msg.eph != 65535 and (msg.eph / 100.0) > MIN_HDOP:
                continue
            buf.add_gps(ts, msg.lat / 1e7, msg.lon / 1e7, msg.alt / 1000.0)

    print("[GPS] Thread beendet.")


# ---------------------------------------------------------------------------
# Thread 2: Sensor via HTTP
# ---------------------------------------------------------------------------

def sensor_thread(buf: DataBuffer):
    ctrl     = CommandDispatcherController(SENSOR_URL)
    interval = 1.0 / SENSOR_POLL_HZ

    buf.status = "Sensor: Verbinde ..."
    print(f"[Sensor] Polling @ {SENSOR_POLL_HZ} Hz ...")

    while buf.running:
        t0 = time.monotonic()
        try:
            measurements = ctrl.get_measurements()
            ts           = datetime.now(timezone.utc)
            mur_values   = {}

            for sensor in measurements.get("sensors", []):
                if sensor.get("id") != SENSOR_MODULE_ID:
                    continue
                for ch in range(1, 9):
                    for d in range(1, 5):
                        val = sensor.get(f"ch{ch}", {}).get(f"d{d}_ni1")
                        if val is not None:
                            mur_values[f"MUR_{ch}_{d}"] = val / 100.0

            if mur_values:
                buf.add_sensor(ts, mur_values)
                buf.status = "Läuft"

        except Exception as e:
            buf.status = f"Sensor: Fehler ({e})"

        time.sleep(max(0, interval - (time.monotonic() - t0)))

    print("[Sensor] Thread beendet.")


# ---------------------------------------------------------------------------
# Thread 3: Karte generieren und als PNG speichern
# ---------------------------------------------------------------------------

def map_thread(buf: DataBuffer):
    to_utm   = Transformer.from_crs("EPSG:4326", f"EPSG:{UTM_EPSG}", always_xy=True)
    from_utm = Transformer.from_crs(f"EPSG:{UTM_EPSG}", "EPSG:4326", always_xy=True)

    print(f"[Map] Generiere Karte alle {MAP_INTERVAL_S}s → {OUTPUT_PNG}")

    while buf.running:
        time.sleep(MAP_INTERVAL_S)
        df = buf.get_merged()

        if len(df) < MIN_POINTS_MAP:
            print(f"[Map] Warte auf Daten ({len(df)}/{MIN_POINTS_MAP} Punkte) ...")
            continue

        mur_cols = [c for c in df.columns if c.startswith("MUR_")]
        if not mur_cols:
            continue

        if MAP_CHANNEL is None:
            values = df[mur_cols].mean(axis=1).values
            label  = "MUR Mittelwert"
        else:
            ch_cols = [f"MUR_{MAP_CHANNEL}_{d}" for d in range(1, 5) if f"MUR_{MAP_CHANNEL}_{d}" in df.columns]
            values  = df[ch_cols].mean(axis=1).values
            label   = f"MUR Kanal {MAP_CHANNEL}"

        lats = df["lat"].values
        lons = df["lon"].values

        try:
            x, y = to_utm.transform(lons, lats)
            dx, dy = x.max() - x.min(), y.max() - y.min()
            v_min  = np.nanpercentile(values, 2)
            v_max  = np.nanpercentile(values, 98)
            if v_min == v_max:
                continue

            fig, ax = plt.subplots(figsize=(10, 8))
            stats   = buf.get_stats()
            ax.set_title(
                f"{label}  |  GPS: {stats['gps_count']}  "
                f"Sensor: {stats['sensor_count']}  Punkte: {stats['merged_points']}  "
                f"[{datetime.now().strftime('%H:%M:%S')}]",
                fontsize=9
            )

            if dx >= RESOLUTION_M and dy >= RESOLUTION_M:
                nx = max(2, int(dx / RESOLUTION_M) + 1)
                ny = max(2, int(dy / RESOLUTION_M) + 1)
                gx, gy = np.meshgrid(np.linspace(x.min(), x.max(), nx),
                                      np.linspace(y.min(), y.max(), ny))
                grid = np.flipud(
                    griddata(np.column_stack([x, y]), values, (gx, gy), method="linear")
                )
                lon_min, lat_min = from_utm.transform(x.min(), y.min())
                lon_max, lat_max = from_utm.transform(x.max(), y.max())
                ax.imshow(grid, cmap="plasma", vmin=v_min, vmax=v_max,
                          extent=[lon_min, lon_max, lat_min, lat_max],
                          origin="upper", alpha=0.6, aspect="auto")

            sc = ax.scatter(lons, lats, c=values, cmap="plasma",
                            vmin=v_min, vmax=v_max, s=10, alpha=0.8, linewidths=0)
            plt.colorbar(sc, ax=ax, label=label, fraction=0.03, pad=0.04)
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")
            plt.tight_layout()
            plt.savefig(OUTPUT_PNG, dpi=120)
            plt.close(fig)
            print(f"[Map] Aktualisiert — {stats['merged_points']} Punkte")

        except Exception as e:
            print(f"[Map] Fehler: {e}")
            plt.close("all")

    print("[Map] Thread beendet.")


# ---------------------------------------------------------------------------
# HTTP-Server: PNG + Status per Browser abrufbar
# ---------------------------------------------------------------------------

def make_http_handler(buf: DataBuffer):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/map":
                # HTML-Seite mit Auto-Refresh alle 5s
                html = f"""<!DOCTYPE html>
<html><head>
  <meta http-equiv="refresh" content="{MAP_INTERVAL_S}">
  <title>Live Sensor Map</title>
  <style>body{{background:#111;color:#eee;font-family:sans-serif;text-align:center}}</style>
</head><body>
  <h2>Live Sensor Map</h2>
  <img src="/map.png" style="max-width:100%;border:1px solid #444">
  <p>Aktualisiert alle {MAP_INTERVAL_S}s — Seite lädt automatisch neu</p>
</body></html>"""
                self._send(200, "text/html", html.encode())

            elif self.path == "/map.png":
                try:
                    data = Path(OUTPUT_PNG).read_bytes()
                    self._send(200, "image/png", data)
                except FileNotFoundError:
                    self._send(404, "text/plain", b"Noch keine Karte")

            elif self.path == "/status":
                data = json.dumps(buf.get_stats(), indent=2).encode()
                self._send(200, "application/json", data)

            else:
                self._send(404, "text/plain", b"Not found")

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass   # HTTP-Logs unterdrücken

    return Handler


# ---------------------------------------------------------------------------
# Port-Erkennung
# ---------------------------------------------------------------------------

def list_ports():
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            print(f"  {p.device:20s}  {p.description}")
    except ImportError:
        print("pip install pyserial für Port-Erkennung")


def auto_detect_port() -> str | None:
    try:
        import serial.tools.list_ports
        keywords = ["radio", "sik", "ftdi", "usb serial", "cp210", "ch340"]
        for p in serial.tools.list_ports.comports():
            if any(k in p.description.lower() for k in keywords):
                print(f"[GPS] Automatisch erkannt: {p.device} ({p.description})")
                return p.device
    except ImportError:
        pass
    return None


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def run(port: str):
    buf  = DataBuffer()
    ctrl = CommandDispatcherController(SENSOR_URL)

    # Sensor starten
    print("Starte Sensor-Messung ...")
    try:
        ctrl.set_data_stream_configuration(
            SENSOR_MODULE_ID, NI_stream_active=True,
            MUR_stream_active=True, data_stream_period=100
        )
        ctrl.start_measurements()
        print(f"Sensor bereit — Verbundene Module: {ctrl.get_connected_sensors()}")
    except Exception as e:
        print(f"Sensor nicht erreichbar: {e} — starte trotzdem.")

    # Threads
    threads = [
        threading.Thread(target=gps_thread,    args=(buf, port, TELEMETRY_BAUD), daemon=True, name="GPS"),
        threading.Thread(target=sensor_thread, args=(buf,),                      daemon=True, name="Sensor"),
        threading.Thread(target=map_thread,    args=(buf,),                      daemon=True, name="Map"),
    ]
    for t in threads:
        t.start()

    # HTTP-Server
    server = HTTPServer(("0.0.0.0", HTTP_PORT), make_http_handler(buf))
    t_http = threading.Thread(target=server.serve_forever, daemon=True, name="HTTP")
    t_http.start()

    import socket
    ip = socket.gethostbyname(socket.gethostname())
    print(f"\nLive-Karte abrufbar unter: http://{ip}:{HTTP_PORT}")
    print(f"Status-API:                 http://{ip}:{HTTP_PORT}/status")
    print("\nCTRL+C zum Beenden.\n")

    try:
        while True:
            time.sleep(5)
            s = buf.get_stats()
            print(f"[Status] GPS={s['gps_count']}  Sensor={s['sensor_count']}  "
                  f"Merged={s['merged_points']}  {s['status']}")
    except KeyboardInterrupt:
        pass
    finally:
        print("\nStoppe ...")
        buf.running = False
        server.shutdown()
        try:
            ctrl.stop_measurements()
        except Exception:
            pass
        print("Fertig.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live Sensor Map (Headless RPi)")
    parser.add_argument("--port",       default=None,        help="Telemetrie-Port (z.B. /dev/ttyUSB0)")
    parser.add_argument("--list-ports", action="store_true", help="Verfügbare Ports anzeigen")
    args = parser.parse_args()

    if args.list_ports:
        list_ports()
    else:
        port = args.port or auto_detect_port() or TELEMETRY_PORT
        print(f"Telemetrie-Port: {port}")
        run(port)
