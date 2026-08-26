"""
Erzeugt eine synthetische Sensor-Log-Fixture für `mission.py --sim-sensor`.

`--sim-sensor` braucht vorhandene NI-CSVs unter SIMULATE_DIR/SIMULATE_PREFIX
(config.py), um beim GPS-SITL-Test einen simulierten Sensor einzuspeisen —
die Zeitstempel werden ohnehin auf den Start des echten GPS-Fluges verschoben
(siehe flight_data.load_sensor_csv, align_start). Es kommt also nur auf
Dauer und Abtastrate an, nicht auf die absoluten Zeitstempel.

Verwendung:
  python generate_sim_sensor_log.py
  python src/mission.py --sim-sensor --sitl --port tcp:127.0.0.1:5760
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent / "src"))
from config import SIMULATE_DIR, SIMULATE_PREFIX, STREAM_PERIOD_MS  # noqa: E402

DURATION_MIN = 30   # länger als jeder SITL-Testflug halten


def main():
    out_dir = Path(SIMULATE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    period_s = STREAM_PERIOD_MS / 1000.0
    n = int(DURATION_MIN * 60 / period_s)
    t0 = datetime(2026, 4, 28, 12, 0, 0)
    timestamps = [t0 + timedelta(seconds=i * period_s) for i in range(n)]

    rng = np.random.default_rng(42)
    # 8 Kanäle x 4 Dioden, langsam wandernder Grundwert + Rauschen (grob NIR-typisch)
    base = 1200 + 400 * np.sin(np.linspace(0, 4 * np.pi, n))[:, None]
    ni = base + rng.normal(0, 60, size=(n, 32))
    ni = np.clip(ni, 50, 4000)

    ts_path = out_dir / f"{SIMULATE_PREFIX}_timestamps.csv"
    ni_path = out_dir / f"{SIMULATE_PREFIX}_NIs.csv"

    with open(ts_path, "w", encoding="utf-8") as f:
        f.write("timestamp\n")
        # Immer mit Mikrosekunden schreiben — sonst ist das Format je nach
        # Zeile inkonsistent (isoformat() lässt .000000 sonst weg) und
        # pandas' Datumsparser scheitert beim Einlesen.
        f.writelines(t.strftime("%Y-%m-%dT%H:%M:%S.%f") + "\n" for t in timestamps)

    header = ",".join(f"NI_{ch}_{d}" for ch in range(1, 9) for d in range(1, 5))
    np.savetxt(ni_path, ni, delimiter=",", header=header, comments="", fmt="%.1f")

    print(f"Fixture erzeugt: {ts_path}")
    print(f"                 {ni_path}")
    print(f"{n} Zeilen, {DURATION_MIN} min @ {1 / period_s:.0f} Hz")


if __name__ == "__main__":
    main()
