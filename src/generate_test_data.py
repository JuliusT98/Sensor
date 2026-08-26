"""
Erzeugt mehrere Test-GeoJSON-Dateien mit unterschiedlichen NIR-Profilen.
Ausgabe: testdata/flight_*.geojson
"""
import json, math, random, os
from datetime import datetime, timedelta, timezone

os.makedirs("testdata", exist_ok=True)

# Wels/OÖ als Basis
CENTER_LAT = 48.1567
CENTER_LON = 14.0234
ROWS = 8
COLS = 30


def make_flight(seed, nir_fn, label, filename, start_time):
    random.seed(seed)
    features = []
    ts = start_time
    alt = 40.0

    for row in range(ROWS):
        lat = CENTER_LAT - 0.0015 + row * (0.003 / (ROWS - 1))
        cols_range = range(COLS) if row % 2 == 0 else range(COLS - 1, -1, -1)
        for col in cols_range:
            lon = CENTER_LON - 0.004 + col * (0.008 / (COLS - 1))
            alt += random.uniform(-0.4, 0.4)
            alt = max(38.0, min(42.0, alt))

            base = nir_fn(lat, lon, row, col)
            nir_values = [
                round(max(50.0, base + random.gauss(0, base * 0.035)), 2)
                for _ in range(8)
            ]
            nir_mean = round(sum(nir_values) / 8, 2)

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(lon, 7), round(lat, 7), round(alt, 2)]
                },
                "properties": {
                    "timestamp": ts.isoformat(),
                    "nir_values": nir_values,
                    "nir_mean": nir_mean,
                    "label": label,
                }
            })
            ts += timedelta(milliseconds=200)

    collection = {"type": "FeatureCollection", "features": features}
    path = f"testdata/{filename}"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(collection, f, ensure_ascii=False, indent=2)

    valid = [ft for ft in features if ft["geometry"]]
    nir_vals = [ft["properties"]["nir_mean"] for ft in valid]
    print(f"  {filename:45s}  {len(features):>3} Punkte  "
          f"NIR {min(nir_vals):.0f}-{max(nir_vals):.0f}  ({label})")


t0 = datetime(2026, 5, 7, 9, 0, 0, tzinfo=timezone.utc)

print("Erzeuge Test-GeoJSON-Dateien ...\n")

# 1) Gesundes Feld — hohe, gleichmässige NIR-Werte
make_flight(
    seed=1, label="Gesundes Feld – hohe NIR",
    filename="flight_gesund.geojson",
    start_time=t0,
    nir_fn=lambda lat, lon, row, col:
        1600 + 200 * math.sin(col / COLS * math.pi)
)

# 2) Trockenstress — niedrige NIR, Gradient von links nach rechts
make_flight(
    seed=2, label="Trockenstress – niedriger NIR",
    filename="flight_trocken.geojson",
    start_time=t0 + timedelta(hours=1),
    nir_fn=lambda lat, lon, row, col:
        500 + 300 * (col / COLS)
)

# 3) Zwei Hotspots (z. B. Bewässerungszonen)
def two_hotspots(lat, lon, row, col):
    d1 = math.hypot(lat - (CENTER_LAT + 0.001), lon - (CENTER_LON - 0.002))
    d2 = math.hypot(lat - (CENTER_LAT - 0.001), lon - (CENTER_LON + 0.002))
    return 900 + 900 * math.exp(-d1 / 0.0008) + 700 * math.exp(-d2 / 0.0009)

make_flight(
    seed=3, label="Zwei Bewässerungs-Hotspots",
    filename="flight_hotspots.geojson",
    start_time=t0 + timedelta(hours=2),
    nir_fn=two_hotspots
)

# 4) Schachbrettmuster — abwechselnd hohe/niedrige Streifen (Unkraut/Frucht)
make_flight(
    seed=4, label="Streifenmuster – Unkrautbefall",
    filename="flight_streifen.geojson",
    start_time=t0 + timedelta(hours=3),
    nir_fn=lambda lat, lon, row, col:
        1400 if col % 4 < 2 else 600
)

# 5) Kreisförmiger Coldspot in der Mitte (z. B. Schädlingsbefall)
def ring_damage(lat, lon, row, col):
    d = math.hypot(lat - CENTER_LAT, lon - CENTER_LON)
    damage = math.exp(-((d - 0.001) ** 2) / (0.0003 ** 2))
    return max(200, 1500 - 1200 * damage)

make_flight(
    seed=5, label="Schädlingsbefall – Ringschaden",
    filename="flight_ringschaden.geojson",
    start_time=t0 + timedelta(hours=4),
    nir_fn=ring_damage
)

# 6) Gleichmässig mittleres Feld — Referenzflug
make_flight(
    seed=6, label="Referenzflug – mittlerer NIR",
    filename="flight_referenz.geojson",
    start_time=t0 + timedelta(hours=5),
    nir_fn=lambda lat, lon, row, col:
        1100 + 80 * math.sin(row * 0.8) + 60 * math.cos(col * 0.3)
)

print("\nFertig. Dateien liegen in testdata/")
