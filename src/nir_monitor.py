#!/usr/bin/env python3
"""
Live-Konsolenanzeige der NIR-Messwerte des Sensors.

Verwendung:
  python src/nir_monitor.py                  Sensor läuft bereits
  python src/nir_monitor.py --start          Sensor starten + am Ende stoppen
  python src/nir_monitor.py --debug          Ersten API-Response roh ausgeben
"""

import argparse
import json
import sys
import time
from datetime import datetime

import requests

SENSOR_URL = "http://192.168.2.30:8100"
POLL_HZ    = 10
SENSOR_ID  = 1
N_CH       = 8
N_DET      = 4
BAR_WIDTH  = 30


def fetch(url: str) -> dict:
    r = requests.get(url + "/get_measurements/", timeout=2.0)
    r.raise_for_status()
    return r.json()


def extract_nir(data: dict, sensor_id: int) -> list[float] | None:
    for sensor in data.get("sensors", []):
        if sensor.get("id") != sensor_id:
            continue
        values = []
    
        for ch in range(1, N_CH + 1):
            ch_data = sensor.get(f"ch{ch}", {})
            det_vals = [ch_data.get(f"d{d}_ni1") for d in range(1, N_DET + 1)]
            det_vals = [v for v in det_vals if v is not None]
            values.append(sum(det_vals) / len(det_vals) if det_vals else float("nan"))
        return values
    return None


def bar(value: float, v_min: float, v_max: float, width: int = BAR_WIDTH) -> str:
    if v_max <= v_min:
        filled = 0
    else:
        filled = int((value - v_min) / (v_max - v_min) * width)
    return "█" * max(0, min(width, filled)) + "░" * (width - max(0, min(width, filled)))


def clear_lines(n: int):
    for _ in range(n):
        sys.stdout.write("\033[A\033[2K")


def render(values: list[float], v_min: float, v_max: float, elapsed: float, count: int):
    mean_all = sum(v for v in values if v == v) / len(values)
    print(f"  Zeit:    {datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
          f"   Messungen: {count}   ∅: {mean_all:8.2f}")
    print(f"  Bereich: [{v_min:.1f} … {v_max:.1f}]   Laufzeit: {elapsed:.0f}s")
    print(f"  {'Kanal':<6}  {'NIR-Mittel':>10}  Balken")
    print("  " + "─" * (BAR_WIDTH + 22))
    for i, v in enumerate(values, start=1):
        print(f"  CH {i:<3}  {v:>10.2f}  {bar(v, v_min, v_max)}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Live NIR-Monitor")
    parser.add_argument("--url",    default=SENSOR_URL)
    parser.add_argument("--hz",     type=float, default=POLL_HZ)
    parser.add_argument("--sensor", type=int,   default=SENSOR_ID)
    parser.add_argument("--start",  action="store_true",
                        help="Sensor starten (NI+MUR) und am Ende stoppen")
    parser.add_argument("--debug",  action="store_true",
                        help="Ersten API-Response roh ausgeben und beenden")
    args = parser.parse_args()

    if args.debug:
        try:
            data = fetch(args.url)
            print(json.dumps(data, indent=2))
        except Exception as e:
            print(f"Fehler: {e}")
        return

    if args.start:
        try:
            print(f"Starte Sensor-Messung ({args.url}) ...")
            r = requests.post(
                args.url + "/receive_measurement/",
                data=f'[{{"sensorID":{args.sensor},"ni":true,"mur":true,"datastreamrate":100}}]',
                timeout=5.0,
            )
            r.raise_for_status()
            requests.post(args.url + "/start_measurement_all_sensors/", timeout=5.0).raise_for_status()
            print("Sensor gestartet.\n")
            time.sleep(0.5)
        except Exception as e:
            print(f"Fehler beim Starten: {e}")
            sys.exit(1)

    interval  = 1.0 / args.hz
    n_lines   = N_CH + 5
    count     = 0
    v_min_run = float("inf")
    v_max_run = float("-inf")
    t_start   = time.monotonic()
    first     = True

    print(f"NIR-Monitor  →  {args.url}   Sensor {args.sensor}   {args.hz} Hz")
    print("Ctrl+C zum Beenden\n")

    try:
        while True:
            t0 = time.monotonic()
            try:
                data   = fetch(args.url)
                values = extract_nir(data, args.sensor)
            except requests.RequestException as e:
                if not first:
                    clear_lines(n_lines)
                print(f"  [Fehler] {e}")
                time.sleep(interval)
                continue

            if values is None:
                if not first:
                    clear_lines(n_lines)
                print(f"  [Fehler] Sensor-ID {args.sensor} nicht gefunden — "
                      f"starte mit --start oder prüfe --sensor N\n"
                      f"  Tipp: python nir_monitor.py --debug  zeigt die rohe API-Antwort")
                time.sleep(interval)
                continue

            # Prüfen ob alle Werte 0 sind (Sensor noch nicht bereit)
            if all(v == 0.0 for v in values):
                if not first:
                    clear_lines(1)
                print(f"  Warte auf Messdaten ... (alle Werte = 0, starte mit --start?)")
                time.sleep(interval)
                first = True
                continue

            count     += 1
            v_min_run  = min(v_min_run, *values)
            v_max_run  = max(v_max_run, *values)

            if not first:
                clear_lines(n_lines)
            first = False

            render(values, v_min_run, v_max_run, time.monotonic() - t_start, count)
            time.sleep(max(0.0, interval - (time.monotonic() - t0)))

    except KeyboardInterrupt:
        print("\nBeendet.")

    if args.start:
        try:
            requests.post(args.url + "/stop_measurement_all_sensors/", timeout=5.0)
            print("Sensor gestoppt.")
        except Exception:
            pass


if __name__ == "__main__":
    main()
