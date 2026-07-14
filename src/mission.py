"""
Mission Pipeline: GPS live per Telemetrie tracken → nach Landung Sensor-Log laden → Karte

Ablauf:
  1. Sensor-Messung starten  (HTTP → RPi 1)
  2. GPS live loggen         (Telemetrie-Radio → Pixhawk)
  3. Landung erkennen        (Disarm / landed_state, manuell per ENTER)
  4. Sensor-Log herunterladen (HTTP → RPi 1)
  5. GPS + Sensor zusammenführen → Karte erstellen

Module:
  config.py          Konstanten (Adressen, Schwellwerte, Pfade)
  gps_tracker.py     GpsBuffer + GpsTracker (MAVLink-Thread)
  sensor_client.py   SensorClient (robuste Hülle um die Hersteller-Klasse)
  flight_data.py     CSVs laden/parsen, Zeitoffset schätzen
  map_builder.py     MapBuilder (Cesium/MapLibre-Karte + JSON-Export)

Verwendung:
  python mission.py                         Vollautomatisch (QGC-Survey in AUTO fliegen)
  python mission.py --port COM4             Telemetrie-Port manuell
  python mission.py --sitl                  SITL-Modus (aktiviert Simulator-Workarounds)
  python mission.py --simulate              Test mit vorhandenen Daten (kein Sensor/Radio)
  python mission.py --manual-stop           Landung manuell per Enter bestätigen

Landungserkennung: Disarm bzw. landed_state (EXTENDED_SYS_STATE) sind maßgeblich.
Die Höhen/Geschwindigkeits-Heuristik gibt nur noch einen Hinweis aus.
Zeitstempel: GPS-Zeit vom Autopiloten (SYSTEM_TIME), RPi-Uhr wird beim Log-Start
und -Stop gegen die PC-Uhr kalibriert (inkl. Drift-Check).

Abhängigkeiten:
  pip install pymavlink requests pandas numpy scipy rasterio pyproj matplotlib jsbeautifier
"""

import argparse
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    AUTO_SYNC, GPS_CSV, LOG_DIR, ROOT, SENSOR_UTC_OFFSET_H, SIMULATE_DIR,
    SIMULATE_PREFIX, TELEMETRY_PORT, TIME_OFFSET_S,
)
from flight_data import (
    estimate_offset, gps_to_dataframe, load_gps_csv, load_sensor_csv, parse_sensor_log,
)
from gps_tracker import GpsTracker
from map_builder import MapBuilder
from sensor_client import SensorClient


# ---------------------------------------------------------------------------
# Auf Landung warten (Konsolen-Interaktion)
# ---------------------------------------------------------------------------

def wait_for_landing(buf, manual: bool):
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
# Simuliertes GPS (für --simulate)
# ---------------------------------------------------------------------------

def make_simulated_gps() -> pd.DataFrame:
    ts_path = Path(SIMULATE_DIR) / f"{SIMULATE_PREFIX}_timestamps.csv"
    # Timezone-Korrektur auch für Fake-GPS anwenden
    times = (pd.to_datetime(pd.read_csv(ts_path)["timestamp"], utc=False)
             - pd.Timedelta(hours=SENSOR_UTC_OFFSET_H)).dt.tz_localize("UTC")
    t0, t1 = times.min(), times.max()
    idx    = pd.date_range(start=t0, end=t1, freq="200ms")
    n      = len(idx)
    return pd.DataFrame({
        "lat":     np.linspace(48.2000, 48.2020, n),
        "lon":     np.linspace(16.3700, 16.3720, n),
        "alt":     50 + np.sin(np.linspace(0, np.pi, n)) * 2,
        "rel_alt": 50.0,
        "speed":   np.abs(np.sin(np.linspace(0, 2 * np.pi, n))) * 3,
    }, index=idx)


# ---------------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------------

def run(simulate: bool, sim_sensor: bool, manual_stop: bool, port: str,
        remap: bool = False, sitl: bool = False):
    (ROOT / "output").mkdir(exist_ok=True)
    clock_offset_s = None
    sensor = None
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
        gps_df     = make_simulated_gps()
        sensor_dir = SIMULATE_DIR
        prefix     = SIMULATE_PREFIX

    else:
        if not sim_sensor:
            print("\n[ 1 ] Sensor starten ...")
            sensor = SensorClient()
            sensor.start()
        else:
            print("\n[ 1 ] Sensor übersprungen (--sim-sensor)")

        print("\n[ 2 ] GPS tracken (SITL / Telemetrie) ...")
        tracker = GpsTracker(port, sitl=sitl, csv_path=GPS_CSV)  # schreibt live mit → crash-sicher
        tracker.start()

        if sensor is not None:
            sensor.start_health_monitor(stop_event=tracker.landed)

        print("\n[ 3 ] Warte auf Landung ...")
        wait_for_landing(tracker, manual=manual_stop)

        if not sim_sensor:
            print("\n[ 4 ] Sensor-Log herunterladen ...")
            zip_path           = sensor.stop_and_download()
            print("\n[ 5 ] Sensor-Log parsen ...")
            sensor_dir, prefix = parse_sensor_log(zip_path)
            print("\n[ 5b] RPi-Uhr kalibrieren ...")
            clock_offset_s = sensor.calibrate_clock_offset(sensor_dir, prefix)
        else:
            print("\n[ 4 ] Sensor-Daten: vorhandene CSVs verwenden")
            sensor_dir     = SIMULATE_DIR
            prefix         = SIMULATE_PREFIX
            clock_offset_s = None

        print("\n[ 6 ] GPS-Daten aufbereiten ...")
        gps_df = gps_to_dataframe(tracker.buffer)

    print("\n[ 7 ] Sensor-CSVs laden ...")
    # clock_offset_s ist nur im echten Hardware-Pfad gesetzt; sonst None (Fallback -SENSOR_UTC_OFFSET_H)
    # Bei --sim-sensor die Sensor-Zeit auf den Live-GPS-Flug schieben (sonst kein Überlapp).
    align_start = gps_df.index.min() if (sim_sensor and not simulate) else None
    sensor_df = load_sensor_csv(sensor_dir, prefix, clock_offset_s=clock_offset_s,
                                align_start=align_start)

    print("\n[ 8 ] Zeitoffset ...")
    if clock_offset_s is not None:
        # Uhren sind direkt kalibriert — Kreuzkorrelation nur noch als Sanity-Check
        sanity = estimate_offset(sensor_df, gps_df)
        if abs(sanity - TIME_OFFSET_S) > 2.0:
            print(f"  Hinweis: Kreuzkorrelation ({sanity:+.2f}s) weicht ab — "
                  "bei leerer Karte TIME_OFFSET_S prüfen.")
        offset_s = TIME_OFFSET_S
        print(f"  Verwende kalibrierten Offset (TIME_OFFSET_S = {TIME_OFFSET_S:+.1f}s)")
    else:
        offset_s = estimate_offset(sensor_df, gps_df) if AUTO_SYNC else TIME_OFFSET_S

    print("\n[ 9 ] Karte erstellen ...")
    html_path = MapBuilder().build(sensor_df, gps_df, offset_s)

    print(f"\nFertig — {html_path} erstellt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sensor Map Mission")
    parser.add_argument("--simulate",    action="store_true", help="Alles simuliert (kein Sensor, kein GPS)")
    parser.add_argument("--sim-sensor",  action="store_true", help="Nur Sensor simulieren, echtes SITL-GPS verwenden")
    parser.add_argument("--manual-stop", action="store_true", help="Landung manuell per Enter bestätigen")
    parser.add_argument("--remap",       action="store_true", help="Letzten Flug neu karten (kein neuer Flug nötig)")
    parser.add_argument("--sitl",        action="store_true", help="SITL-Modus: Simulator-Workarounds aktivieren (rel_alt-Filter)")
    parser.add_argument("--port",        default=None,        help="MAVLink-Port (COM3, tcp:127.0.0.1:5760, udp:127.0.0.1:14550)")
    args = parser.parse_args()

    port = args.port or TELEMETRY_PORT
    run(simulate=args.simulate, sim_sensor=args.sim_sensor,
        manual_stop=args.manual_stop, port=port, remap=args.remap, sitl=args.sitl)
