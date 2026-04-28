"""
Demo-Karte: reale Messdaten proportional auf GPS-Flugpfad mappen.
Verwendung: python demo_map.py
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.interpolate import interp1d

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------

gps = pd.read_csv("gps_last_flight.csv")
gps["timestamp"] = pd.to_datetime(gps["timestamp"], utc=True)
gps = gps.sort_values("timestamp").reset_index(drop=True)
print(f"GPS: {len(gps)} Punkte  |  {gps.lat.min():.5f},{gps.lon.min():.5f} bis {gps.lat.max():.5f},{gps.lon.max():.5f}")

sensor_dir = Path("log_files/measurements_2026-04-28_1")
prefix     = "NI_2026-04-28_1_sensor1"
df = pd.concat([
    pd.read_csv(sensor_dir / f"{prefix}_timestamps.csv"),
    pd.read_csv(sensor_dir / f"{prefix}_MURs.csv"),
], axis=1)
print(f"Sensor: {len(df)} Punkte")


# ---------------------------------------------------------------------------
# Proportionale Ausrichtung: Sensor [0..1] → GPS [0..1]
# ---------------------------------------------------------------------------

t_s = np.linspace(0, 1, len(df))
t_g = np.linspace(0, 1, len(gps))

for col in ["lat", "lon", "alt"]:
    df[col] = interp1d(t_g, gps[col].values, kind="linear")(t_s)

mur_cols   = [c for c in df.columns if c.startswith("MUR_")]
df["value"] = df[mur_cols].mean(axis=1)
v_min = float(np.percentile(df["value"], 2))
v_max = float(np.percentile(df["value"], 98))
print(f"MUR: {v_min:.3f} – {v_max:.3f}")


# ---------------------------------------------------------------------------
# Farben
# ---------------------------------------------------------------------------

cmap = plt.get_cmap("RdYlGn_r")
norm = mcolors.Normalize(vmin=v_min, vmax=v_max)

def to_rgb(v):
    r, g, b, _ = cmap(norm(float(np.clip(v, v_min, v_max))))
    return int(r * 255), int(g * 255), int(b * 255)


# ---------------------------------------------------------------------------
# GeoJSON + Flugpfad
# ---------------------------------------------------------------------------

features = []
for _, row in df.iterrows():
    r, g, b = to_rgb(row["value"])
    features.append({
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [float(row["lon"]), float(row["lat"]), float(row["alt"])]
        },
        "properties": {
            "value": round(float(row["value"]), 4),
            "alt":   round(float(row["alt"]), 1),
            "r": r, "g": g, "b": b,
        }
    })

path_coords = []
for _, row in gps.iterrows():
    path_coords += [float(row["lon"]), float(row["lat"]), float(row["alt"])]

geojson_str  = json.dumps({"type": "FeatureCollection", "features": features})
path_str     = json.dumps(path_coords)
center_lat   = float(df["lat"].mean())
center_lon   = float(df["lon"].mean())
center_alt   = float(df["alt"].mean()) + 150
label        = "MUR Mittelwert (alle Kanäle)"

legend_html = ""
for i in range(6):
    v = v_min + (v_max - v_min) * i / 5
    r, g, b = to_rgb(v)
    legend_html += (
        f'<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
        f'<div style="width:20px;height:12px;background:rgb({r},{g},{b});border-radius:3px"></div>'
        f'{v:.3f}</div>'
    )


# ---------------------------------------------------------------------------
# MapLibre GL JS HTML (kein Token nötig)
# ---------------------------------------------------------------------------

path_line = json.dumps({
    "type": "Feature",
    "geometry": {"type": "LineString",
                 "coordinates": [[float(r["lon"]), float(r["lat"])] for _, r in gps.iterrows()]}
})

html = """<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Sensor Map</title>
  <script src="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.js"></script>
  <link href="https://unpkg.com/maplibre-gl@4.1.2/dist/maplibre-gl.css" rel="stylesheet">
  <style>
    * { margin:0; padding:0; box-sizing:border-box; }
    #map { width:100vw; height:100vh; }
    #legend {
      position:absolute; top:16px; right:16px; z-index:10;
      background:rgba(15,15,15,0.88); color:#fff;
      padding:14px 16px; border-radius:10px;
      font:13px/1.7 sans-serif; backdrop-filter:blur(8px);
      min-width:160px;
    }
    #legend b { font-size:14px; display:block; margin-bottom:8px; }
    .swatch { width:22px; height:13px; border-radius:3px; flex-shrink:0; }
    .leg-row { display:flex; align-items:center; gap:9px; margin:2px 0; }
    #tooltip {
      position:absolute; pointer-events:none;
      background:rgba(15,15,15,0.92); color:#fff;
      padding:8px 13px; border-radius:8px;
      font:13px/1.6 sans-serif; display:none;
    }
    #sat-btn {
      position:absolute; top:16px; left:16px; z-index:10;
      padding:8px 14px; background:rgba(15,15,15,0.85); color:#fff;
      border:none; border-radius:8px; cursor:pointer;
      font:13px sans-serif; backdrop-filter:blur(6px);
    }
    #sat-btn:hover { background:rgba(40,40,40,0.95); }
  </style>
</head>
<body>
  <div id="map"></div>
  <button id="sat-btn">Satellit</button>
  <div id="legend"><b>LABEL</b>LEGEND</div>
  <div id="tooltip"></div>
  <script>
    const map = new maplibregl.Map({
      container: "map",
      style: {
        version: 8,
        glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
        sources: {
          sat: {
            type: "raster",
            tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
            tileSize: 256, attribution: "Esri World Imagery",
          },
          osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256, attribution: "OpenStreetMap",
          },
        },
        layers: [
          { id: "sat-layer", type: "raster", source: "sat" },
          { id: "osm-layer", type: "raster", source: "osm", layout: { visibility: "none" } },
        ],
      },
      center: [CENTER_LON, CENTER_LAT],
      zoom: 16,
    });

    map.addControl(new maplibregl.NavigationControl(), "bottom-right");
    map.addControl(new maplibregl.ScaleControl(), "bottom-left");

    let isSat = true;
    document.getElementById("sat-btn").onclick = () => {
      isSat = !isSat;
      map.setLayoutProperty("sat-layer", "visibility", isSat ? "visible" : "none");
      map.setLayoutProperty("osm-layer", "visibility", isSat ? "none" : "visible");
      document.getElementById("sat-btn").textContent = isSat ? "Karte" : "Satellit";
    };

    map.on("load", () => {

      // Flugpfad Glow
      map.addSource("path", { type: "geojson", data: PATH_LINE });
      map.addLayer({ id: "path-glow", type: "line", source: "path",
        paint: { "line-color": "#2196F3", "line-width": 8, "line-opacity": 0.2, "line-blur": 6 }});
      map.addLayer({ id: "path-line", type: "line", source: "path",
        paint: { "line-color": "#64B5F6", "line-width": 2, "line-opacity": 0.95 }});

      // Start / Landung
      map.addSource("markers", { type: "geojson", data: {
        type: "FeatureCollection",
        features: [
          { type:"Feature", geometry:{type:"Point",coordinates:[START_LON,START_LAT]}, properties:{label:"Start",  color:"#4CAF50"} },
          { type:"Feature", geometry:{type:"Point",coordinates:[END_LON,  END_LAT  ]}, properties:{label:"Landung",color:"#F44336"} },
        ]
      }});
      map.addLayer({ id:"marker-circle", type:"circle", source:"markers",
        paint:{ "circle-radius":9, "circle-color":["get","color"],
                "circle-stroke-width":2, "circle-stroke-color":"#fff" }});
      map.addLayer({ id:"marker-label", type:"symbol", source:"markers",
        layout:{ "text-field":["get","label"], "text-font":["Open Sans Regular"],
                 "text-offset":[0,-1.6], "text-size":12 },
        paint:{ "text-color":"#fff", "text-halo-color":"#000", "text-halo-width":1 }});

      // Messpunkte
      map.addSource("pts", { type: "geojson", data: GEOJSON });
      map.addLayer({
        id: "pts-layer", type: "circle", source: "pts",
        paint: {
          "circle-radius": ["interpolate",["linear"],["zoom"], 14,3, 18,7, 21,12],
          "circle-color":  ["rgb",["get","r"],["get","g"],["get","b"]],
          "circle-opacity": 0.88,
          "circle-stroke-width": 0.8,
          "circle-stroke-color": "rgba(255,255,255,0.25)",
        }
      });

      // Tooltip
      const tip = document.getElementById("tooltip");
      map.on("mousemove", "pts-layer", e => {
        map.getCanvas().style.cursor = "crosshair";
        const p = e.features[0].properties;
        tip.style.display = "block";
        tip.style.left = (e.point.x + 16) + "px";
        tip.style.top  = (e.point.y - 10) + "px";
        tip.innerHTML  = "<b>LABEL:</b> " + p.value + "<br><b>Hoehe:</b> " + p.alt + " m";
      });
      map.on("mouseleave", "pts-layer", () => {
        map.getCanvas().style.cursor = "";
        tip.style.display = "none";
      });
    });
  </script>
</body>
</html>"""

start_lon = float(gps.iloc[0]["lon"])
start_lat = float(gps.iloc[0]["lat"])
end_lon   = float(gps.iloc[-1]["lon"])
end_lat   = float(gps.iloc[-1]["lat"])

html = (html
    .replace("LABEL",      label)
    .replace("LEGEND",     legend_html)
    .replace("PATH_LINE",  path_line)
    .replace("GEOJSON",    geojson_str)
    .replace("CENTER_LAT", str(center_lat))
    .replace("CENTER_LON", str(center_lon))
    .replace("START_LON",  str(start_lon))
    .replace("START_LAT",  str(start_lat))
    .replace("END_LON",    str(end_lon))
    .replace("END_LAT",    str(end_lat))
)

Path("sensor_map.html").write_text(html, encoding="utf-8")
print("Karte gespeichert: sensor_map.html")
