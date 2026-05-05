"""
Sensor Map Pipeline: ArduPilot SITL / Pixhawk 4 + Multispektral-Sensor
Ablauf: Log einlesen → GPS interpolieren → MUR-Werte georeferenzieren → GeoTIFF + PNG

Unterstützte Log-Formate:
    ArduPilot .bin  →  pip install pymavlink
    PX4 ULog  .ulg  →  pip install pyulog

Weitere Abhängigkeiten:
    pip install pandas numpy scipy rasterio pyproj matplotlib
"""

import numpy as np
import pandas as pd
from pathlib import Path
from scipy.interpolate import interp1d
from scipy.signal import correlate


# ---------------------------------------------------------------------------
# Konfiguration — hier anpassen
# ---------------------------------------------------------------------------

LOG_FILE        = "data/flight_logs/00000001.BIN"  # ArduPilot .bin  ODER  PX4 .ulg

SENSOR_DIR      = "data/sensor_logs/measurements_2026-04-13_1"
SENSOR_PREFIX   = "NI_2026-04-13_1_sensor1"

# Welcher Kanal soll gemappt werden?
# None = Mittelwert aller 32 Spots
# N    = Mittelwert der 4 Detektoren von Kanal N  (z.B. MAP_CHANNEL = 4)
MAP_CHANNEL     = None

# Zeitoffset: Sensor-PC-Zeit minus GPS-Zeit in Sekunden
# AUTO_SYNC=True  → wird automatisch geschätzt (empfohlen)
# AUTO_SYNC=False → TIME_OFFSET_S manuell setzen
AUTO_SYNC       = True
TIME_OFFSET_S   = 0.0

RESOLUTION_M    = 0.5    # Pixelgröße in Metern
OUTPUT_TIF      = "output/sensor_map.tif"
OUTPUT_PNG      = "output/sensor_map.png"

UTM_EPSG        = 32633  # 32632=West-DE, 32633=Ost-DE/Österreich, 32634=weiter östlich
MIN_HDOP        = 2.5
MAX_GPS_GAP_S   = 2.0


# ---------------------------------------------------------------------------
# 1. Log-Format erkennen und GPS einlesen
# ---------------------------------------------------------------------------

def detect_log_format(log_file: str) -> str:
    path = Path(log_file)
    if path.suffix.lower() == ".ulg":
        return "ulg"
    # .bin und .BIN → ArduPilot binary prüfen
    with open(log_file, "rb") as f:
        header = f.read(4)
    if header[:2] == bytes([0xA3, 0x95]):
        return "bin"
    raise ValueError(
        f"Unbekanntes Log-Format: {path.suffix}  (erwartet: .bin oder .ulg)"
    )


def parse_ardupilot_gps(bin_file: str) -> pd.DataFrame:
    """GPS aus ArduPilot .bin (SITL und echter Pixhawk)."""
    from pymavlink import mavutil

    print(f"  Format: ArduPilot .bin")
    mav = mavutil.mavlink_connection(bin_file)
    records = []

    while True:
        msg = mav.recv_match(type=["GPS"], blocking=False)
        if msg is None:
            break
        if msg.Status < 3:      # kein 3D-Fix
            continue
        if msg.GWk == 0:        # GPS-Zeit noch nicht bekannt
            continue
        records.append({
            "gps_week": int(msg.GWk),
            "gps_ms":   int(msg.GMS),
            "lat":      msg.Lat,
            "lon":      msg.Lng,
            "alt":      msg.Alt / 100.0,   # cm → m
            "hdop":     msg.HDop / 100.0,
        })

    if not records:
        raise ValueError(
            "Keine GPS-Nachrichten mit 3D-Fix im .bin gefunden.\n"
            "SITL: Stelle sicher dass die Simulation genug Zeit hatte einen GPS-Fix zu bekommen."
        )

    df = pd.DataFrame(records)

    # GPS-Woche + ms → UTC
    GPS_EPOCH = pd.Timestamp("1980-01-06", tz="UTC")
    df["timestamp"] = (
        GPS_EPOCH
        + pd.to_timedelta(df["gps_week"].astype(int), unit="W")
        + pd.to_timedelta(df["gps_ms"].astype(int),   unit="ms")
    )
    df = df.drop(columns=["gps_week", "gps_ms"])
    df = df[df["hdop"] <= MIN_HDOP]
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def parse_px4_gps(ulg_file: str) -> pd.DataFrame:
    """GPS aus PX4 ULog (.ulg)."""
    from pyulog import ULog

    print(f"  Format: PX4 ULog")
    ulog     = ULog(ulg_file, message_name_filter_list=["vehicle_gps_position"])
    gps_data = next((d for d in ulog.data_list if d.name == "vehicle_gps_position"), None)

    if gps_data is None:
        raise ValueError("Kein 'vehicle_gps_position' Topic im ULog gefunden.")

    df = pd.DataFrame({
        "time_utc_usec": gps_data.data["time_utc_usec"],
        "lat":           gps_data.data["lat"] / 1e7,
        "lon":           gps_data.data["lon"] / 1e7,
        "alt":           gps_data.data["alt"] / 1000.0,
        "fix_type":      gps_data.data["fix_type"],
        "hdop":          gps_data.data["hdop"] / 100.0,
    })
    df = df[(df["fix_type"] >= 3) & (df["hdop"] <= MIN_HDOP) & (df["time_utc_usec"] > 0)]

    if df.empty:
        raise ValueError("Keine GPS-Punkte mit Fix und UTC-Zeit im ULog.")

    df["timestamp"] = pd.to_datetime(df["time_utc_usec"], unit="us", utc=True)
    df = df.drop(columns=["fix_type", "time_utc_usec"])
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def parse_gps(log_file: str) -> pd.DataFrame:
    fmt = detect_log_format(log_file)
    if fmt == "bin":
        df = parse_ardupilot_gps(log_file)
    else:
        df = parse_px4_gps(log_file)
    print(f"  GPS-Punkte: {len(df)} | {df.index.min()} – {df.index.max()}")
    return df


# ---------------------------------------------------------------------------
# 2. Sensordaten einlesen
# ---------------------------------------------------------------------------

def parse_sensor_data(sensor_dir: str, prefix: str) -> pd.DataFrame:
    base     = Path(sensor_dir)
    ts_path  = base / f"{prefix}_timestamps.csv"
    ni_path = base / f"{prefix}_NIs.csv"

    for p in [ts_path, ni_path]:
        if not p.exists():
            raise FileNotFoundError(f"Nicht gefunden: {p}")

    df = pd.concat([pd.read_csv(ts_path), pd.read_csv(ni_path)], axis=1)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    df = df[~df.index.duplicated(keep="first")]

    rate = 1 / df.index.to_series().diff().median().total_seconds()
    print(f"  Sensor-Punkte: {len(df)} | Rate: ~{rate:.0f} Hz")
    print(f"  Zeitraum: {df.index.min()} – {df.index.max()}")
    return df


# ---------------------------------------------------------------------------
# 3. Zeitoffset schätzen
# ---------------------------------------------------------------------------

def estimate_time_offset(sensor_df: pd.DataFrame, gps_df: pd.DataFrame) -> float:
    ni_cols  = [c for c in sensor_df.columns if c.startswith("NI_")]
    activity = sensor_df[ni_cols].std(axis=1)

    t_start = max(sensor_df.index.min(), gps_df.index.min())
    t_end   = min(sensor_df.index.max(), gps_df.index.max())

    if t_start >= t_end:
        print("  Kein zeitlicher Überlapp — Offset wird auf 0 gesetzt.")
        print("  Tipp: Setze AUTO_SYNC=False und TIME_OFFSET_S manuell.")
        return 0.0

    idx = pd.date_range(start=t_start, end=t_end, freq="100ms")
    s   = activity.reindex(idx, method="nearest", tolerance=pd.Timedelta("200ms")).fillna(0)
    g   = (gps_df["alt"]
           .reindex(idx, method="nearest", tolerance=pd.Timedelta("500ms"))
           .ffill().diff().abs().fillna(0))

    corr    = correlate(s.values - s.mean(), g.values - g.mean(), mode="full")
    lag_idx = int(corr.argmax()) - (len(g) - 1)
    offset  = lag_idx * 0.1
    print(f"  Geschätzter Zeitoffset: {offset:.2f}s")
    return offset


# ---------------------------------------------------------------------------
# 4. GPS auf Sensor-Timestamps interpolieren
# ---------------------------------------------------------------------------

def interpolate_gps_to_sensor(
    sensor_df: pd.DataFrame,
    gps_df: pd.DataFrame,
    offset_s: float,
) -> pd.DataFrame:
    df = sensor_df.copy()
    df.index = df.index - pd.Timedelta(seconds=offset_s)

    gps_ns = gps_df.index.astype(np.int64)
    sen_ns = df.index.astype(np.int64)

    for col in ["lat", "lon", "alt"]:
        fn       = interp1d(gps_ns, gps_df[col].values, kind="linear",
                            bounds_error=False, fill_value=np.nan)
        df[col]  = fn(sen_ns)

    gps_s = gps_df.index.astype(np.int64) / 1e9
    sen_s = df.index.astype(np.int64) / 1e9
    gap   = np.array([np.min(np.abs(gps_s - t)) for t in sen_s])

    df = df[(gap <= MAX_GPS_GAP_S) & df["lat"].notna()]
    df.index = df.index + pd.Timedelta(seconds=offset_s)

    print(f"  Georeferenzierte Punkte: {len(df)}")
    return df


# ---------------------------------------------------------------------------
# 5. Messwert auswählen
# ---------------------------------------------------------------------------

def extract_map_value(df: pd.DataFrame, channel: int | None) -> pd.DataFrame:
    result   = df.copy()
    ni_cols = [c for c in df.columns if c.startswith("NI_")]

    if channel is None:
        result["value"] = df[ni_cols].mean(axis=1)
        label = "NIR Mittelwert (alle Kanäle)"
    else:
        ch_cols = [f"NI_{channel}_{d}" for d in range(1, 5)]
        missing = [c for c in ch_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Spalten nicht gefunden: {missing}")
        result["value"] = df[ch_cols].mean(axis=1)
        label = f"NIR Kanal {channel}"

    result.attrs["value_label"] = label
    print(f"  {label}: {result['value'].min():.3f} – {result['value'].max():.3f} "
          f"(Median: {result['value'].median():.3f})")
    return result


# ---------------------------------------------------------------------------
# 6. Karte erstellen und exportieren
# ---------------------------------------------------------------------------

def build_and_export_map(georef_df: pd.DataFrame):
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    from scipy.interpolate import griddata
    from pyproj import Transformer
    import matplotlib.pyplot as plt

    lats   = georef_df["lat"].values
    lons   = georef_df["lon"].values
    values = georef_df["value"].values
    label  = georef_df.attrs.get("value_label", "MUR")

    if len(lats) < 4:
        raise ValueError(f"Zu wenige Punkte ({len(lats)}) für eine Karte.")

    to_utm   = Transformer.from_crs("EPSG:4326", f"EPSG:{UTM_EPSG}", always_xy=True)
    from_utm = Transformer.from_crs(f"EPSG:{UTM_EPSG}", "EPSG:4326", always_xy=True)

    x, y     = to_utm.transform(lons, lats)
    x_min, x_max = x.min(), x.max()
    y_min, y_max = y.min(), y.max()

    if (x_max - x_min) < RESOLUTION_M or (y_max - y_min) < RESOLUTION_M:
        raise ValueError(
            f"Fläche {x_max-x_min:.1f}m × {y_max-y_min:.1f}m zu klein "
            f"für {RESOLUTION_M}m Auflösung."
        )

    nx = max(2, int((x_max - x_min) / RESOLUTION_M) + 1)
    ny = max(2, int((y_max - y_min) / RESOLUTION_M) + 1)

    gx, gy = np.meshgrid(np.linspace(x_min, x_max, nx),
                          np.linspace(y_min, y_max, ny))

    grid = griddata(np.column_stack([x, y]), values, (gx, gy), method="linear")
    grid = np.flipud(grid)

    lon_min, lat_min = from_utm.transform(x_min, y_min)
    lon_max, lat_max = from_utm.transform(x_max, y_max)
    tf = from_bounds(lon_min, lat_min, lon_max, lat_max, nx, ny)

    with rasterio.open(OUTPUT_TIF, "w", driver="GTiff",
                       height=ny, width=nx, count=1,
                       dtype=rasterio.float32, crs=CRS.from_epsg(4326),
                       transform=tf, nodata=np.nan) as dst:
        dst.write(grid.astype(np.float32), 1)
    print(f"GeoTIFF: {OUTPUT_TIF}  ({nx}×{ny} px)")

    v_min = np.nanpercentile(values, 2)
    v_max = np.nanpercentile(values, 98)
    cmap  = plt.get_cmap("plasma")

    fig, ax = plt.subplots(figsize=(10, 8))
    ax.imshow(grid, cmap=cmap, vmin=v_min, vmax=v_max,
              extent=[lon_min, lon_max, lat_min, lat_max],
              origin="upper", alpha=0.85)
    sc = ax.scatter(lons, lats, c=values, cmap=cmap,
                    vmin=v_min, vmax=v_max, s=8, alpha=0.5, linewidths=0)
    plt.colorbar(sc, ax=ax, label=label, fraction=0.03, pad=0.04)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_title(f"{label}  |  {RESOLUTION_M}m/px")
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150)
    print(f"PNG: {OUTPUT_PNG}")
    plt.show()


# ---------------------------------------------------------------------------
# Haupt-Pipeline
# ---------------------------------------------------------------------------

def run():
    Path("output").mkdir(exist_ok=True)
    print("=== Sensor Map Pipeline ===\n")

    print("[ 1 ] Log einlesen ...")
    gps_df = parse_gps(LOG_FILE)

    print("\n[ 2 ] Sensordaten einlesen ...")
    sensor_df = parse_sensor_data(SENSOR_DIR, SENSOR_PREFIX)

    print("\n[ 3 ] Zeitoffset ...")
    offset_s = estimate_time_offset(sensor_df, gps_df) if AUTO_SYNC else TIME_OFFSET_S
    if not AUTO_SYNC:
        print(f"  Manuell: {offset_s}s")

    print("\n[ 4 ] GPS interpolieren ...")
    georef_df = interpolate_gps_to_sensor(sensor_df, gps_df, offset_s)

    print("\n[ 5 ] Messwert auswählen ...")
    georef_df = extract_map_value(georef_df, channel=MAP_CHANNEL)

    print("\n[ 6 ] Karte erstellen ...")
    build_and_export_map(georef_df)

    print("\nFertig.")


if __name__ == "__main__":
    run()
