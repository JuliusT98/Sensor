"""
Mission Pipeline: GPS live per Telemetrie tracken → nach Landung Sensor-Log laden → Karte

Ablauf:
  1. Sensor-Messung starten  (HTTP → RPi 1)
  2. GPS live loggen         (Telemetrie-Radio → Pixhawk)
  3. Landung erkennen        (automatisch via MAVLink oder manuell)
  4. Sensor-Log herunterladen (HTTP → RPi 1)
  5. GPS + Sensor zusammenführen → Karte erstellen

Verwendung:
  python mission.py                         Vollautomatisch
  python mission.py --port COM4             Telemetrie-Port manuell
  python mission.py --simulate              Test mit vorhandenen Daten (kein Sensor/Radio)
  python mission.py --manual-stop           Landung manuell per Enter bestätigen

Abhängigkeiten:
  pip install pymavlink requests pandas numpy scipy rasterio pyproj matplotlib jsbeautifier
"""

import argparse
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

SENSOR_URL          = "http://192.168.2.30:8100"
SENSOR_MODULE_ID    = 1
STREAM_PERIOD_MS    = 100

# Zeitzone des Sensor-PCs relativ zu UTC (z.B. 2 für CEST, 1 für CET, 0 für UTC)
SENSOR_UTC_OFFSET_H = 2

TELEMETRY_PORT   = "COM3"     # Windows: COM3 / Linux (RPi): /dev/ttyUSB0
TELEMETRY_BAUD   = 57600

LOG_DIR          = Path("log_files")
MAP_CHANNEL      = None       # None = alle Kanäle mitteln, N = Kanal N
RESOLUTION_M     = 0.5
OUTPUT_TIF       = "sensor_map.tif"
OUTPUT_PNG       = "sensor_map.png"
UTM_EPSG         = 32633
MIN_HDOP         = 3.0
MAX_GPS_GAP_S    = 2.0
AUTO_SYNC        = True
TIME_OFFSET_S    = 0.0

# Landungs-Erkennung
LAND_ALT_M       = 2.0        # Unter dieser Höhe (relativ) gilt Drohne als "tief"
LAND_SPEED_MS    = 0.5        # Unter dieser Geschwindigkeit gilt Drohne als "langsam"
LAND_CONFIRM_S   = 3.0        # Wie lange die Bedingungen erfüllt sein müssen

# Simulation
SIMULATE_DIR     = "log_files/measurements_2026-04-28_1"
SIMULATE_PREFIX  = "NI_2026-04-28_1_sensor1"


# ---------------------------------------------------------------------------
# GPS-Buffer (thread-safe)
# ---------------------------------------------------------------------------

class GpsBuffer:
    def __init__(self):
        self._lock    = threading.Lock()
        self._data    = deque()
        self.count    = 0
        self.landed   = threading.Event()
        self.armed    = False

    def add(self, ts, lat, lon, alt, rel_alt, speed):
        with self._lock:
            self._data.append((ts, lat, lon, alt, rel_alt, speed))
            self.count += 1

    def to_dataframe(self) -> pd.DataFrame:
        with self._lock:
            rows = list(self._data)
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=["timestamp", "lat", "lon", "alt", "rel_alt", "speed"])
        return df.set_index("timestamp").sort_index()


# ---------------------------------------------------------------------------
# Schritt 1: Sensor starten
# ---------------------------------------------------------------------------

def start_sensor():
    from command_dispatcher_controller import CommandDispatcherController
    ctrl = CommandDispatcherController(SENSOR_URL)

    print("  Verbinde Sensor ...")
    connected = ctrl.get_connected_sensors()
    if not connected:
        raise RuntimeError(f"Kein Sensor unter {SENSOR_URL}")
    print(f"  Verbundene Module: {connected}")

    ctrl.set_data_stream_configuration(
        SENSOR_MODULE_ID,
        NI_stream_active=True,
        MUR_stream_active=True,
        data_stream_period=STREAM_PERIOD_MS,
    )
    ctrl.start_measurements()
    ctrl.delete_measurement_log()

    # PC-Zeit exakt beim Start des Logs merken → für Uhr-Kalibrierung
    ctrl._log_start_pc_utc = datetime.now(timezone.utc)
    ctrl.start_measurement_log()
    print("  Sensor-Aufzeichnung läuft.")
    return ctrl


def calibrate_clock_offset(ctrl, sensor_dir: str, prefix: str) -> float:
    """
    Vergleicht PC-Zeit beim Log-Start mit erstem Sensor-Timestamp.
    Gibt den Offset in Sekunden zurück: sensor_utc = sensor_raw - offset
    """
    ts_path = Path(sensor_dir) / f"{prefix}_timestamps.csv"
    first_ts = pd.read_csv(ts_path)["timestamp"].iloc[0]
    first_ts_raw = pd.to_datetime(first_ts)  # ohne Timezone-Annahme

    log_start_pc = ctrl._log_start_pc_utc.replace(tzinfo=None)
    offset_s = (first_ts_raw - log_start_pc).total_seconds()

    print(f"  PC-Zeit Log-Start: {log_start_pc}")
    print(f"  Erster Sensor-TS:  {first_ts_raw}")
    print(f"  RPi-Uhr Offset:    {offset_s:+.1f}s  ({offset_s/3600:+.3f}h)")
    return offset_s


# ---------------------------------------------------------------------------
# Schritt 2: GPS-Thread
# ---------------------------------------------------------------------------

def gps_logging_thread(buf: GpsBuffer, port: str, baud: int):
    from pymavlink import mavutil

    print(f"[GPS] Verbinde {port} ...")
    try:
        mav = mavutil.mavlink_connection(port, baud=baud)
        hb  = mav.wait_heartbeat(timeout=15)
        if hb is None:
            raise RuntimeError("Kein Heartbeat erhalten (Timeout 15s)")
        print(f"[GPS] Verbunden (System {mav.target_system}) — fordere GPS-Stream an ...")
    except Exception as e:
        print(f"[GPS] Fehler: {e}")
        buf.landed.set()
        return

    # GPS-Datenstream explizit anfordern (nötig bei SITL und manchen Setups)
    mav.mav.request_data_stream_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_POSITION, 4, 1   # 4 Hz, start
    )
    mav.mav.request_data_stream_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS, 2, 1
    )
    mav.mav.request_data_stream_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_EXTRA1, 4, 1
    )

    land_since = None

    while not buf.landed.is_set():
        msg = mav.recv_match(
            type=["GLOBAL_POSITION_INT", "HEARTBEAT", "EXTENDED_SYS_STATE", "GPS_RAW_INT"],
            blocking=True, timeout=1.0
        )
        if msg is None:
            continue

        t = msg.get_type()

        if t == "HEARTBEAT":
            armed = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if not buf.armed and armed:
                print("[GPS] Drohne armed — starte Aufzeichnung.")
            if buf.armed and not armed:
                print("[GPS] Disarm erkannt → Landung bestätigt.")
                buf.landed.set()
            buf.armed = armed

        elif t == "GLOBAL_POSITION_INT":
            ts      = datetime.now(timezone.utc)
            lat     = msg.lat / 1e7
            lon     = msg.lon / 1e7
            alt     = msg.alt / 1000.0
            rel_alt = msg.relative_alt / 1000.0
            speed   = np.sqrt(msg.vx**2 + msg.vy**2) / 100.0

            if lat != 0.0 and lon != 0.0:
                buf.add(ts, lat, lon, alt, rel_alt, speed)

            if buf.armed and rel_alt < LAND_ALT_M and speed < LAND_SPEED_MS:
                if land_since is None:
                    land_since = time.monotonic()
                elif time.monotonic() - land_since >= LAND_CONFIRM_S:
                    print("[GPS] Landung erkannt (Höhe + Geschwindigkeit).")
                    buf.landed.set()
            else:
                land_since = None

        elif t == "GPS_RAW_INT":
            if msg.fix_type >= 3:
                ts = datetime.now(timezone.utc)
                buf.add(ts, msg.lat / 1e7, msg.lon / 1e7,
                        msg.alt / 1000.0, 0.0, 0.0)

        elif t == "EXTENDED_SYS_STATE":
            if msg.landed_state == 1 and buf.armed:
                print("[GPS] Landung erkannt (landed_state).")
                buf.landed.set()

    print(f"[GPS] Beendet — {buf.count} GPS-Punkte geloggt.")


# ---------------------------------------------------------------------------
# Schritt 3: Auf Landung warten
# ---------------------------------------------------------------------------

def wait_for_landing(buf: GpsBuffer, manual: bool):
    def print_gps_status():
        while not buf.landed.is_set():
            time.sleep(3)
            if not buf.landed.is_set():
                print(f"  [GPS] {buf.count} Punkte geloggt ...")

    t_status = threading.Thread(target=print_gps_status, daemon=True)
    t_status.start()

    if manual:
        print("  Warte auf GPS-Daten ...")
        # Warte bis mindestens ein GPS-Punkt da ist
        while buf.count == 0:
            time.sleep(0.5)
        print(f"  GPS aktiv — {buf.count} Punkte.")
        print("  Fliege die Mission, dann drücke ENTER nach der Landung.")
        input()
        buf.landed.set()
    else:
        print("  Warte auf automatische Landungserkennung ...")
        print("  (ENTER für manuellen Stop)")

        def wait_enter():
            input()
            print("  Manueller Stop.")
            buf.landed.set()

        threading.Thread(target=wait_enter, daemon=True).start()
        buf.landed.wait()

    print(f"  Gelandet — {buf.count} GPS-Punkte geloggt.")


# ---------------------------------------------------------------------------
# Schritt 4: Sensor-Log herunterladen
# ---------------------------------------------------------------------------

def stop_and_download(ctrl) -> Path:
    print("  Stoppe Aufzeichnung ...")
    ctrl.stop_measurement_log()
    ctrl.stop_measurements()

    print("  Lade Sensor-Log herunter ...")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = ctrl.download_measurement_log(directory=str(LOG_DIR))
    if zip_path is None:
        raise RuntimeError("Download fehlgeschlagen.")

    print(f"  Gespeichert: {zip_path}")
    return Path(zip_path)


# ---------------------------------------------------------------------------
# Schritt 5: Sensor-Log parsen
# ---------------------------------------------------------------------------

def parse_sensor_log(zip_path: Path) -> tuple[str, str]:
    from log_file_parser import LogFileParser

    print(f"  Parse {zip_path.name} ...")
    results = LogFileParser().extract_data(zip_path)
    if not results:
        raise RuntimeError("LogFileParser: keine Daten gefunden.")

    data_path_str, data, _ = results[0]
    data_path    = Path(data_path_str)
    first_sensor = list(data.keys())[0]          # z.B. "sensor1"
    prefix       = f"{data_path.stem}_{first_sensor}"   # z.B. "NI_2026-04-28_1_sensor1"
    print(f"  Sensor-Daten: {data_path.parent / prefix}_*.csv")
    return str(data_path.parent), prefix


# ---------------------------------------------------------------------------
# Schritt 6: GPS-Buffer → DataFrame
# ---------------------------------------------------------------------------

GPS_CSV = Path("gps_last_flight.csv")

def gps_to_dataframe(buf: GpsBuffer) -> pd.DataFrame:
    df = buf.to_dataframe()
    if df.empty:
        raise RuntimeError("GPS-Buffer ist leer.")
    df = df[~df.index.duplicated(keep="first")]
    # Speichern für spätere Nutzung mit --remap
    df.reset_index().to_csv(GPS_CSV, index=False)
    print(f"  GPS-Punkte: {len(df)} | {df.index.min()} – {df.index.max()}")
    print(f"  GPS gespeichert: {GPS_CSV}")
    return df


def load_gps_csv() -> pd.DataFrame:
    if not GPS_CSV.exists():
        raise FileNotFoundError(f"Kein gespeicherter GPS-Flug gefunden: {GPS_CSV}")
    df = pd.read_csv(GPS_CSV)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    print(f"  GPS geladen: {len(df)} Punkte | {df.index.min()} – {df.index.max()}")
    return df


# ---------------------------------------------------------------------------
# Schritt 7: Sensor-CSVs einlesen
# ---------------------------------------------------------------------------

def load_sensor_csv(sensor_dir: str, prefix: str, clock_offset_s: float = None) -> pd.DataFrame:
    base = Path(sensor_dir)
    ts   = pd.read_csv(base / f"{prefix}_timestamps.csv")
    murs = pd.read_csv(base / f"{prefix}_MURs.csv")
    df   = pd.concat([ts, murs], axis=1)

    # Sensor-Timestamps in UTC konvertieren
    # clock_offset_s = Differenz zwischen RPi-Uhr und PC-UTC (automatisch kalibriert)
    # SENSOR_UTC_OFFSET_H = Fallback falls keine Kalibrierung möglich
    if clock_offset_s is not None:
        df["timestamp"] = (
            pd.to_datetime(df["timestamp"], utc=False)
            - pd.Timedelta(seconds=clock_offset_s)
        ).dt.tz_localize("UTC")
        print(f"  Uhr-Offset angewendet: {clock_offset_s:+.1f}s")
    else:
        df["timestamp"] = (
            pd.to_datetime(df["timestamp"], utc=False)
            - pd.Timedelta(hours=SENSOR_UTC_OFFSET_H)
        ).dt.tz_localize("UTC")
        print(f"  Timezone-Offset angewendet: -{SENSOR_UTC_OFFSET_H}h (Fallback)")

    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    rate = 1 / df.index.to_series().diff().median().total_seconds()
    print(f"  Sensor-Punkte: {len(df)} | ~{rate:.0f} Hz | {df.index.min()} – {df.index.max()}")
    return df


# ---------------------------------------------------------------------------
# Schritt 8: Zeitoffset schätzen
# ---------------------------------------------------------------------------

def estimate_offset(sensor_df: pd.DataFrame, gps_df: pd.DataFrame) -> float:
    from scipy.signal import correlate

    mur_cols = [c for c in sensor_df.columns if c.startswith("MUR_")]
    activity = sensor_df[mur_cols].std(axis=1)

    t0 = max(sensor_df.index.min(), gps_df.index.min())
    t1 = min(sensor_df.index.max(), gps_df.index.max())

    if t0 >= t1:
        print("  Kein zeitlicher Überlapp — Offset = 0")
        print("  Tipp: TIME_OFFSET_S manuell setzen wenn Karte leer bleibt.")
        return 0.0

    idx = pd.date_range(start=t0, end=t1, freq="100ms")
    s   = activity.reindex(idx, method="nearest", tolerance=pd.Timedelta("200ms")).fillna(0)
    g   = (gps_df["speed"]
           .reindex(idx, method="nearest", tolerance=pd.Timedelta("500ms"))
           .ffill().fillna(0))

    corr   = correlate(s.values - s.mean(), g.values - g.mean(), mode="full")
    offset = (int(corr.argmax()) - (len(g) - 1)) * 0.1
    print(f"  Zeitoffset: {offset:.2f}s")
    return offset


# ---------------------------------------------------------------------------
# Schritt 9: GPS interpolieren + Karte erstellen
# ---------------------------------------------------------------------------

def _interpolate_gps(sensor_df, gps_df, offset_s):
    from scipy.interpolate import interp1d
    df       = sensor_df.copy()
    df.index = df.index - pd.Timedelta(seconds=offset_s)
    gps_ns   = gps_df.index.astype(np.int64)
    sen_ns   = df.index.astype(np.int64)
    for col in ["lat", "lon", "alt"]:
        fn      = interp1d(gps_ns, gps_df[col].values, kind="linear",
                           bounds_error=False, fill_value=np.nan)
        df[col] = fn(sen_ns)
    gps_s = gps_df.index.astype(np.int64) / 1e9
    sen_s = df.index.astype(np.int64) / 1e9
    gap   = np.array([np.min(np.abs(gps_s - t)) for t in sen_s])
    df    = df[(gap <= MAX_GPS_GAP_S) & df["lat"].notna()]
    df.index = df.index + pd.Timedelta(seconds=offset_s)
    return df


# Visualisierungs-Engine: "cesium" | "maplibre" | "folium"
MAP_ENGINE = "cesium"


def _value_to_rgb(value: float, v_min: float, v_max: float) -> tuple:
    """Wert → RGB-Tuple (0-255) über RdYlGn Farbskala."""
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    cmap = plt.get_cmap("RdYlGn_r")
    norm = mcolors.Normalize(vmin=v_min, vmax=v_max)
    r, g, b, _ = cmap(norm(float(np.clip(value, v_min, v_max))))
    return int(r * 255), int(g * 255), int(b * 255)


def build_cesium_map(df, gps_df, label, v_min, v_max, html_path):
    """CesiumJS: 3D-Globus, animierter Flugpfad, farbige Messpunkte."""
    import json

    # Flugpfad als CesiumJS CZML (animierbar)
    path_coords = []
    for _, row in gps_df.iterrows():
        path_coords += [float(row["lon"]), float(row["lat"]), float(row["alt"])]

    # Messpunkte als GeoJSON
    features = []
    for idx, row in df.iterrows():
        r, g, b = _value_to_rgb(row["value"], v_min, v_max)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"]), float(row["alt"])]},
            "properties": {
                "value": round(float(row["value"]), 4),
                "alt":   round(float(row["alt"]), 1),
                "time":  str(idx),
                "color": f"rgb({r},{g},{b})",
                "r": r, "g": g, "b": b,
            }
        })

    geojson     = json.dumps({"type": "FeatureCollection", "features": features})
    path_json   = json.dumps(path_coords)
    center_lat  = float(df["lat"].mean())
    center_lon  = float(df["lon"].mean())
    center_alt  = float(df["alt"].mean()) + 100

    # Farbskala für Legende
    legend_steps = []
    for i in range(6):
        v = v_min + (v_max - v_min) * i / 5
        r, g, b = _value_to_rgb(v, v_min, v_max)
        legend_steps.append(f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
                            f'<div style="width:20px;height:12px;background:rgb({r},{g},{b});border-radius:3px"></div>'
                            f'{v:.3f}</div>')
    legend_html = "\n".join(legend_steps)

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Sensor Map</title>
  <script src="https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Cesium.js"></script>
  <link href="https://cesium.com/downloads/cesiumjs/releases/1.114/Build/Cesium/Widgets/widgets.css" rel="stylesheet">
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    #cesiumContainer {{ width:100vw; height:100vh; }}
    #legend {{
      position:absolute; top:16px; right:16px; z-index:999;
      background:rgba(30,30,30,0.85); color:#fff; padding:12px 16px;
      border-radius:8px; font:13px/1.5 sans-serif; backdrop-filter:blur(4px);
    }}
    #legend b {{ font-size:14px; display:block; margin-bottom:6px; }}
    #info {{
      position:absolute; bottom:16px; left:16px; z-index:999;
      background:rgba(30,30,30,0.85); color:#fff; padding:10px 14px;
      border-radius:8px; font:13px sans-serif; min-width:200px;
    }}
  </style>
</head>
<body>
  <div id="cesiumContainer"></div>
  <div id="legend">
    <b>{label}</b>
    {legend_html}
  </div>
  <div id="info">Klick auf einen Punkt für Details</div>

  <script>
    // Kein Ion-Token nötig für Open-Source Tiles
    Cesium.Ion.defaultAccessToken = "";

    const viewer = new Cesium.Viewer("cesiumContainer", {{
      imageryProvider: new Cesium.UrlTemplateImageryProvider({{
        url: "https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png",
        credit: "© OpenStreetMap",
        maximumLevel: 19,
      }}),
      terrainProvider: new Cesium.EllipsoidTerrainProvider(),
      baseLayerPicker: true,
      geocoder: false,
      homeButton: false,
      sceneModePicker: true,
      navigationHelpButton: false,
      animation: false,
      timeline: false,
      fullscreenButton: true,
    }});

    // Satellit als zweite Option
    viewer.baseLayerPicker.viewModel.imageryProviderViewModels.unshift(
      new Cesium.ProviderViewModel({{
        name: "Esri Satellite",
        iconUrl: "",
        tooltip: "Esri World Imagery",
        creationFunction: () => new Cesium.UrlTemplateImageryProvider({{
          url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}",
          credit: "Esri",
        }})
      }})
    );

    // Flugpfad
    const pathCoords = {path_json};
    const positions = [];
    for (let i = 0; i < pathCoords.length; i += 3) {{
      positions.push(Cesium.Cartesian3.fromDegrees(pathCoords[i], pathCoords[i+1], pathCoords[i+2]));
    }}
    viewer.entities.add({{
      name: "Flugpfad",
      polyline: {{
        positions,
        width: 3,
        material: new Cesium.PolylineGlowMaterialProperty({{
          glowPower: 0.2,
          color: Cesium.Color.fromCssColorString("#2196F3"),
        }}),
        clampToGround: false,
      }}
    }});

    // Start / Landung
    viewer.entities.add({{
      position: positions[0],
      point: {{ pixelSize: 14, color: Cesium.Color.GREEN, outlineColor: Cesium.Color.WHITE, outlineWidth: 2 }},
      label: {{ text: "Start", font: "13px sans-serif", fillColor: Cesium.Color.WHITE,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE, outlineWidth: 2,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -12) }}
    }});
    viewer.entities.add({{
      position: positions[positions.length - 1],
      point: {{ pixelSize: 14, color: Cesium.Color.RED, outlineColor: Cesium.Color.WHITE, outlineWidth: 2 }},
      label: {{ text: "Landung", font: "13px sans-serif", fillColor: Cesium.Color.WHITE,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE, outlineWidth: 2,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -12) }}
    }});

    // Messpunkte
    const geojson = {geojson};
    geojson.features.forEach(f => {{
      const [lon, lat, alt] = f.geometry.coordinates;
      const p = f.properties;
      const c = Cesium.Color.fromBytes(p.r, p.g, p.b, 220);
      viewer.entities.add({{
        position: Cesium.Cartesian3.fromDegrees(lon, lat, alt),
        point: {{ pixelSize: 8, color: c, outlineColor: Cesium.Color.WHITE.withAlpha(0.3), outlineWidth: 1 }},
        description: `<table style="font:13px sans-serif;color:#222">
          <tr><td><b>{label}</b></td><td>${{p.value}}</td></tr>
          <tr><td>Höhe</td><td>${{p.alt}} m</td></tr>
          <tr><td>Zeit</td><td>${{p.time}}</td></tr>
        </table>`,
      }});
    }});

    // Kamera auf Fluggebiet
    viewer.camera.flyTo({{
      destination: Cesium.Cartesian3.fromDegrees({center_lon}, {center_lat}, {center_alt}),
      orientation: {{ heading: 0, pitch: Cesium.Math.toRadians(-45), roll: 0 }},
      duration: 2,
    }});

    // Klick-Info
    const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction(click => {{
      const picked = viewer.scene.pick(click.position);
      if (Cesium.defined(picked) && picked.id && picked.id.description) {{
        document.getElementById("info").innerHTML = picked.id.description.getValue();
      }}
    }}, Cesium.ScreenSpaceEventType.LEFT_CLICK);
  </script>
</body>
</html>"""

    Path(html_path).write_text(html, encoding="utf-8")
    print(f"  CesiumJS Karte: {html_path}")


def build_maplibre_map(df, gps_df, label, v_min, v_max, html_path):
    """MapLibre GL JS: moderner WebGL-Kartenviewer, kein API-Key."""
    import json

    features = []
    for idx, row in df.iterrows():
        r, g, b = _value_to_rgb(row["value"], v_min, v_max)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
            "properties": {"value": round(float(row["value"]), 4), "alt": round(float(row["alt"]), 1),
                           "time": str(idx), "r": r, "g": g, "b": b}
        })

    path_coords = [[float(row["lon"]), float(row["lat"])]
                   for _, row in gps_df.iterrows()]

    geojson   = json.dumps({"type": "FeatureCollection", "features": features})
    path_json = json.dumps({"type": "Feature", "geometry": {"type": "LineString", "coordinates": path_coords}})
    center    = [float(df["lon"].mean()), float(df["lat"].mean())]

    legend_steps = []
    for i in range(6):
        v = v_min + (v_max - v_min) * i / 5
        r, g, b = _value_to_rgb(v, v_min, v_max)
        legend_steps.append(f'<div class="leg-row"><div class="swatch" style="background:rgb({r},{g},{b})"></div>{v:.3f}</div>')

    html = f"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Sensor Map</title>
  <script src="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.js"></script>
  <link href="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.css" rel="stylesheet">
  <style>
    * {{ margin:0;padding:0;box-sizing:border-box }}
    #map {{ width:100vw;height:100vh }}
    #legend {{
      position:absolute;top:16px;right:16px;background:rgba(18,18,18,0.88);
      color:#fff;padding:14px 16px;border-radius:10px;font:13px/1.6 sans-serif;
      backdrop-filter:blur(6px);min-width:160px;
    }}
    #legend b {{ font-size:14px;display:block;margin-bottom:8px }}
    .leg-row {{ display:flex;align-items:center;gap:9px;margin:3px 0 }}
    .swatch {{ width:22px;height:13px;border-radius:3px;flex-shrink:0 }}
    #tooltip {{
      position:absolute;pointer-events:none;background:rgba(18,18,18,0.9);
      color:#fff;padding:8px 12px;border-radius:7px;font:13px sans-serif;
      display:none;max-width:200px;line-height:1.5;
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="legend"><b>{label}</b>{"".join(legend_steps)}</div>
  <div id="tooltip"></div>
  <script>
    const map = new maplibregl.Map({{
      container: "map",
      style: {{
        version: 8,
        sources: {{
          osm: {{ type:"raster", tiles:["https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png"], tileSize:256, attribution:"© OpenStreetMap" }},
          sat: {{ type:"raster", tiles:["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}"], tileSize:256, attribution:"Esri" }},
        }},
        layers: [{{ id:"osm",type:"raster",source:"osm" }}]
      }},
      center: {center},
      zoom: 17,
    }});

    // Satellit-Toggle
    let isSat = false;
    const btn = document.createElement("button");
    btn.textContent = "🛰 Satellit";
    Object.assign(btn.style, {{ position:"absolute",top:"16px",left:"16px",zIndex:10,
      padding:"8px 14px",background:"rgba(18,18,18,0.85)",color:"#fff",border:"none",
      borderRadius:"8px",cursor:"pointer",font:"13px sans-serif",backdropFilter:"blur(4px)" }});
    btn.onclick = () => {{
      isSat = !isSat;
      map.setLayoutProperty("osm", "visibility", isSat ? "none" : "visible");
      map.setLayoutProperty("sat", "visibility", isSat ? "visible" : "none");
      btn.textContent = isSat ? "🗺 Karte" : "🛰 Satellit";
    }};
    document.body.appendChild(btn);

    map.on("load", () => {{
      map.addLayer({{ id:"sat",type:"raster",source:"sat",layout:{{visibility:"none"}} }});

      // Flugpfad
      map.addSource("path", {{ type:"geojson", data:{path_json} }});
      map.addLayer({{ id:"path-glow", type:"line", source:"path",
        paint:{{ "line-color":"#2196F3","line-width":6,"line-opacity":0.25,"line-blur":4 }} }});
      map.addLayer({{ id:"path", type:"line", source:"path",
        paint:{{ "line-color":"#64B5F6","line-width":2,"line-opacity":0.9 }} }});

      // Messpunkte
      map.addSource("pts", {{ type:"geojson", data:{geojson} }});
      map.addLayer({{
        id:"pts-layer", type:"circle", source:"pts",
        paint:{{
          "circle-radius": ["interpolate",["linear"],["zoom"],15,4,20,9],
          "circle-color": ["rgb",["get","r"],["get","g"],["get","b"]],
          "circle-opacity": 0.85,
          "circle-stroke-width": 1,
          "circle-stroke-color": "rgba(255,255,255,0.3)",
        }}
      }});

      // Tooltip
      const tip = document.getElementById("tooltip");
      map.on("mousemove","pts-layer", e => {{
        map.getCanvas().style.cursor = "pointer";
        const p = e.features[0].properties;
        tip.style.display = "block";
        tip.style.left = e.point.x + 14 + "px";
        tip.style.top  = e.point.y + "px";
        tip.innerHTML = `<b>{label}:</b> ${{p.value}}<br>Höhe: ${{p.alt}} m<br>Zeit: ${{p.time}}`;
      }});
      map.on("mouseleave","pts-layer", () => {{
        map.getCanvas().style.cursor = "";
        tip.style.display = "none";
      }});
    }});
  </script>
</body>
</html>"""

    Path(html_path).write_text(html, encoding="utf-8")
    print(f"  MapLibre Karte: {html_path}")


def build_map(sensor_df: pd.DataFrame, gps_df: pd.DataFrame, offset_s: float):
    import matplotlib
    matplotlib.use("Agg")
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    from scipy.interpolate import griddata
    from pyproj import Transformer

    df = _interpolate_gps(sensor_df, gps_df, offset_s)
    print(f"  Georeferenzierte Punkte: {len(df)}")

    if len(df) < 4:
        raise RuntimeError(
            f"Nur {len(df)} Punkte — Karte nicht möglich.\n"
            "  → TIME_OFFSET_S anpassen und nochmal ausführen."
        )

    # Messwert
    mur_cols = [c for c in df.columns if c.startswith("MUR_")]
    if MAP_CHANNEL is None:
        df["value"] = df[mur_cols].mean(axis=1)
        label = "MUR Mittelwert"
    else:
        ch_cols     = [f"MUR_{MAP_CHANNEL}_{d}" for d in range(1, 5)]
        df["value"] = df[ch_cols].mean(axis=1)
        label = f"MUR Kanal {MAP_CHANNEL}"

    lats   = df["lat"].values
    lons   = df["lon"].values
    values = df["value"].values
    alts   = df["alt"].values
    times  = df.index.strftime("%H:%M:%S").values
    v_min  = float(np.nanpercentile(values, 2))
    v_max  = float(np.nanpercentile(values, 98))
    print(f"  {label}: {values.min():.3f} – {values.max():.3f}")

    # -----------------------------------------------------------------------
    # Interaktive Karte (Engine wählbar oben mit MAP_ENGINE)
    # -----------------------------------------------------------------------
    html_path = OUTPUT_PNG.replace(".png", ".html")
    if MAP_ENGINE == "cesium":
        build_cesium_map(df, gps_df, label, v_min, v_max, html_path)
    elif MAP_ENGINE == "maplibre":
        build_maplibre_map(df, gps_df, label, v_min, v_max, html_path)
    else:
        raise ValueError(f"Unbekannte MAP_ENGINE: {MAP_ENGINE}. Wähle 'cesium' oder 'maplibre'.")

    # -----------------------------------------------------------------------
    # GeoTIFF
    # -----------------------------------------------------------------------
    to_utm   = Transformer.from_crs("EPSG:4326", f"EPSG:{UTM_EPSG}", always_xy=True)
    from_utm = Transformer.from_crs(f"EPSG:{UTM_EPSG}", "EPSG:4326", always_xy=True)
    x, y     = to_utm.transform(lons, lats)
    nx       = max(2, int((x.max() - x.min()) / RESOLUTION_M) + 1)
    ny       = max(2, int((y.max() - y.min()) / RESOLUTION_M) + 1)
    gx, gy   = np.meshgrid(np.linspace(x.min(), x.max(), nx),
                             np.linspace(y.min(), y.max(), ny))
    grid     = np.flipud(griddata(np.column_stack([x, y]), values, (gx, gy), method="linear"))
    lon_min, lat_min = from_utm.transform(x.min(), y.min())
    lon_max, lat_max = from_utm.transform(x.max(), y.max())
    tf = from_bounds(lon_min, lat_min, lon_max, lat_max, nx, ny)
    with rasterio.open(OUTPUT_TIF, "w", driver="GTiff", height=ny, width=nx,
                       count=1, dtype=rasterio.float32, crs=CRS.from_epsg(4326),
                       transform=tf, nodata=np.nan) as dst:
        dst.write(grid.astype(np.float32), 1)
    print(f"  GeoTIFF: {OUTPUT_TIF}  ({nx}x{ny} px)")


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def run(simulate: bool, sim_sensor: bool, manual_stop: bool, port: str, remap: bool = False):
    print("=" * 50)
    print("  Sensor Map Mission")
    print("=" * 50)

    if remap:
        # Letzten Flug neu karten ohne neu zu fliegen
        print("\n[REMAP] Lade gespeicherte GPS + Sensor-Daten\n")
        print("[ 6 ] GPS laden ...")
        gps_df = load_gps_csv()
        # Letztes Sensor-Log automatisch finden
        sensor_dirs = sorted([d for d in LOG_DIR.glob("measurements_*") if d.is_dir()])
        if not sensor_dirs:
            raise FileNotFoundError("Keine Sensor-Logs in log_files/ gefunden.")
        last_dir    = sensor_dirs[-1]
        csv_files   = list(last_dir.glob("*_timestamps.csv"))
        if not csv_files:
            raise FileNotFoundError(f"Keine timestamps.csv in {last_dir}")
        prefix      = csv_files[0].stem.replace("_timestamps", "")
        sensor_dir  = str(last_dir)
        print(f"  Sensor-Log: {last_dir.name}/{prefix}")

    elif simulate:
        # Alles simuliert: kein Sensor, kein GPS
        print("\n[SIMULATION] Kein Sensor, kein Telemetrie-Radio\n")
        ts_path = Path(SIMULATE_DIR) / f"{SIMULATE_PREFIX}_timestamps.csv"
        # Timezone-Korrektur auch für Fake-GPS anwenden
        times = (pd.to_datetime(pd.read_csv(ts_path)["timestamp"], utc=False)
                 - pd.Timedelta(hours=SENSOR_UTC_OFFSET_H)).dt.tz_localize("UTC")
        t0, t1 = times.min(), times.max()
        idx    = pd.date_range(start=t0, end=t1, freq="200ms")
        n      = len(idx)
        gps_df = pd.DataFrame({
            "lat":     np.linspace(48.2000, 48.2020, n),
            "lon":     np.linspace(16.3700, 16.3720, n),
            "alt":     50 + np.sin(np.linspace(0, np.pi, n)) * 2,
            "rel_alt": 50.0,
            "speed":   np.abs(np.sin(np.linspace(0, 2 * np.pi, n))) * 3,
        }, index=idx)
        sensor_dir = SIMULATE_DIR
        prefix     = SIMULATE_PREFIX

    else:
        if not sim_sensor:
            print("\n[ 1 ] Sensor starten ...")
            ctrl = start_sensor()
        else:
            print("\n[ 1 ] Sensor übersprungen (--sim-sensor)")
            ctrl = None

        print("\n[ 2 ] GPS tracken (SITL / Telemetrie) ...")
        buf   = GpsBuffer()
        t_gps = threading.Thread(
            target=gps_logging_thread, args=(buf, port, TELEMETRY_BAUD), daemon=True
        )
        t_gps.start()

        print("\n[ 3 ] Warte auf Landung ...")
        wait_for_landing(buf, manual=manual_stop)

        if not sim_sensor:
            print("\n[ 4 ] Sensor-Log herunterladen ...")
            zip_path           = stop_and_download(ctrl)
            print("\n[ 5 ] Sensor-Log parsen ...")
            sensor_dir, prefix = parse_sensor_log(zip_path)
            print("\n[ 5b] RPi-Uhr kalibrieren ...")
            clock_offset_s = calibrate_clock_offset(ctrl, sensor_dir, prefix)
        else:
            print("\n[ 4 ] Sensor-Daten: vorhandene CSVs verwenden")
            sensor_dir     = SIMULATE_DIR
            prefix         = SIMULATE_PREFIX
            clock_offset_s = None

        print("\n[ 6 ] GPS-Daten aufbereiten ...")
        gps_df = gps_to_dataframe(buf)

    if not remap:
        pass

    print("\n[ 7 ] Sensor-CSVs laden ...")
    sensor_df = load_sensor_csv(sensor_dir, prefix,
                                clock_offset_s=None if (simulate or remap) else clock_offset_s)

    print("\n[ 8 ] Zeitoffset ...")
    offset_s = estimate_offset(sensor_df, gps_df) if AUTO_SYNC else TIME_OFFSET_S

    print("\n[ 9 ] Karte erstellen ...")
    build_map(sensor_df, gps_df, offset_s)

    print(f"\nFertig — {OUTPUT_PNG} und {OUTPUT_TIF} erstellt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sensor Map Mission")
    parser.add_argument("--simulate",    action="store_true", help="Alles simuliert (kein Sensor, kein GPS)")
    parser.add_argument("--sim-sensor",  action="store_true", help="Nur Sensor simulieren, echtes SITL-GPS verwenden")
    parser.add_argument("--manual-stop", action="store_true", help="Landung manuell per Enter bestätigen")
    parser.add_argument("--remap",       action="store_true", help="Letzten Flug neu karten (kein neuer Flug nötig)")
    parser.add_argument("--port",        default=None,        help="MAVLink-Port (COM3, tcp:127.0.0.1:5760, udp:127.0.0.1:14550)")
    args = parser.parse_args()

    port = args.port or TELEMETRY_PORT
    run(simulate=args.simulate, sim_sensor=args.sim_sensor,
        manual_stop=args.manual_stop, port=port, remap=args.remap)
