"""
Flugdaten laden, parsen und zeitlich synchronisieren.

- Sensor-Log (ZIP vom RPi) parsen → CSV-Verzeichnis + Prefix
- GPS-Buffer → DataFrame (+ Persistenz für --remap)
- Sensor-CSVs einlesen (inkl. Uhr-Offset / Timezone-Fallback)
- Zeitoffset per Kreuzkorrelation schätzen (Fallback ohne Uhr-Kalibrierung)
"""

from pathlib import Path

import numpy as np
import pandas as pd

from config import GPS_CSV, SENSOR_UTC_OFFSET_H


# ---------------------------------------------------------------------------
# Sensor-Log parsen
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
# GPS-Buffer → DataFrame
# ---------------------------------------------------------------------------

def gps_to_dataframe(buf) -> pd.DataFrame:
    buf.close_csv()   # inkrementelles Live-Log abschließen
    df = buf.to_dataframe()
    if df.empty:
        raise RuntimeError("GPS-Buffer ist leer.")
    df = df[~df.index.duplicated(keep="first")]
    # Bereinigte Version (dedupliziert) über das Live-Log schreiben — für --remap
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
# Sensor-CSVs einlesen
# ---------------------------------------------------------------------------

def load_sensor_csv(sensor_dir: str, prefix: str, clock_offset_s: float = None,
                    align_start: pd.Timestamp = None) -> pd.DataFrame:
    base = Path(sensor_dir)
    ts  = pd.read_csv(base / f"{prefix}_timestamps.csv")
    nis = pd.read_csv(base / f"{prefix}_NIs.csv")
    df  = pd.concat([ts, nis], axis=1)

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

    # Test-Modus (--sim-sensor): gespeicherte Sensor-Zeit auf den GPS-Flug schieben,
    # sonst gibt es keinen zeitlichen Überlapp mit dem Live-SITL-GPS.
    if align_start is not None:
        df.index = df.index - (df.index.min() - align_start)
        print(f"  Sensor-Zeit auf GPS-Start verschoben (--sim-sensor): {align_start}")

    rate = 1 / df.index.to_series().diff().median().total_seconds()
    print(f"  Sensor-Punkte: {len(df)} | ~{rate:.0f} Hz | {df.index.min()} – {df.index.max()}")
    return df


# ---------------------------------------------------------------------------
# Zeitoffset schätzen (Kreuzkorrelation Sensor-Aktivität ↔ GPS-Geschwindigkeit)
# ---------------------------------------------------------------------------

def estimate_offset(sensor_df: pd.DataFrame, gps_df: pd.DataFrame) -> float:
    from scipy.signal import correlate

    ni_cols  = [c for c in sensor_df.columns if c.startswith("NI_")]
    activity = sensor_df[ni_cols].std(axis=1)

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
