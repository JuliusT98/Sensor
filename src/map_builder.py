"""
MapBuilder — georeferenzierte Sensor-Karte aus Sensor- + GPS-DataFrame.

Interpoliert GPS auf die Sensor-Timestamps, färbt die Messwerte ein und
erzeugt eine interaktive HTML-Karte (CesiumJS oder MapLibre GL) sowie
output/flight_data.json für Weiterverarbeitung.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from config import MAP_CHANNEL, MAP_ENGINE, MAX_GPS_GAP_S, OUTPUT_PNG, ROOT


class MapBuilder:
    def __init__(self, engine: str = MAP_ENGINE, channel=MAP_CHANNEL,
                 output_png: str = OUTPUT_PNG):
        self.engine     = engine
        self.channel    = channel
        self.output_png = output_png

    # -----------------------------------------------------------------------
    # Öffentliche API
    # -----------------------------------------------------------------------

    def build(self, sensor_df: pd.DataFrame, gps_df: pd.DataFrame, offset_s: float) -> str:
        """Erzeugt Karte + JSON, gibt den HTML-Pfad zurück."""
        df = self._interpolate_gps(sensor_df, gps_df, offset_s)
        print(f"  Georeferenzierte Punkte: {len(df)}")

        if len(df) < 4:
            raise RuntimeError(
                f"Nur {len(df)} Punkte — Karte nicht möglich.\n"
                "  → TIME_OFFSET_S anpassen und nochmal ausführen."
            )

        # Messwert
        ni_cols = [c for c in df.columns if c.startswith("NI_")]
        if self.channel is None:
            df["value"] = df[ni_cols].mean(axis=1)
            label = "NIR Mittelwert"
        else:
            ch_cols     = [f"NI_{self.channel}_{d}" for d in range(1, 5)]
            df["value"] = df[ch_cols].mean(axis=1)
            label = f"NIR Kanal {self.channel}"

        lats     = df["lat"].values
        lons     = df["lon"].values
        values   = df["value"].values
        alts     = df["alt"].values
        rel_alts = df["rel_alt"].values
        times    = df.index.strftime("%H:%M:%S").values
        v_min  = float(np.nanpercentile(values, 2))
        v_max  = float(np.nanpercentile(values, 98))
        print(f"  {label}: {values.min():.3f} – {values.max():.3f}")

        html_path = self.output_png.replace(".png", ".html")
        if self.engine == "cesium":
            self._build_cesium(df, gps_df, label, v_min, v_max, html_path)
        elif self.engine == "maplibre":
            self._build_maplibre(df, gps_df, label, v_min, v_max, html_path)
        else:
            raise ValueError(f"Unbekannte MAP_ENGINE: {self.engine}. Wähle 'cesium' oder 'maplibre'.")

        self._export_json(lats, lons, alts, rel_alts, values, times, label, v_min, v_max, gps_df)
        return html_path

    # -----------------------------------------------------------------------
    # GPS auf Sensor-Timestamps interpolieren
    # -----------------------------------------------------------------------

    def _interpolate_gps(self, sensor_df, gps_df, offset_s):
        from scipy.interpolate import interp1d
        df       = sensor_df.copy()
        df.index = df.index - pd.Timedelta(seconds=offset_s)
        gps_ns   = gps_df.index.astype(np.int64)
        sen_ns   = df.index.astype(np.int64)
        for col in ["lat", "lon", "alt", "rel_alt"]:
            fn      = interp1d(gps_ns, gps_df[col].values, kind="linear",
                               bounds_error=False, fill_value=np.nan)
            df[col] = fn(sen_ns)
        gps_s = gps_df.index.astype(np.int64) / 1e9
        sen_s = df.index.astype(np.int64) / 1e9
        gap   = np.array([np.min(np.abs(gps_s - t)) for t in sen_s])
        df    = df[(gap <= MAX_GPS_GAP_S) & df["lat"].notna()]
        df.index = df.index + pd.Timedelta(seconds=offset_s)
        return df

    # -----------------------------------------------------------------------
    # Farben
    # -----------------------------------------------------------------------

    @staticmethod
    def _value_to_rgb(value: float, v_min: float, v_max: float) -> tuple:
        """Wert → RGB-Tuple (0-255) über RdYlGn Farbskala."""
        import matplotlib.pyplot as plt
        import matplotlib.colors as mcolors
        cmap = plt.get_cmap("RdYlGn_r")
        norm = mcolors.Normalize(vmin=v_min, vmax=v_max)
        r, g, b, _ = cmap(norm(float(np.clip(value, v_min, v_max))))
        return int(r * 255), int(g * 255), int(b * 255)

    def _legend_steps(self, v_min, v_max, row_html):
        steps = []
        for i in range(6):
            v = v_min + (v_max - v_min) * i / 5
            r, g, b = self._value_to_rgb(v, v_min, v_max)
            steps.append(row_html.format(r=r, g=g, b=b, v=v))
        return steps

    # -----------------------------------------------------------------------
    # CesiumJS
    # -----------------------------------------------------------------------

    def _build_cesium(self, df, gps_df, label, v_min, v_max, html_path):
        """CesiumJS: 3D-Globus, animierter Flugpfad, farbige Messpunkte."""
        # Flugpfad als CesiumJS CZML (animierbar)
        path_coords = []
        for _, row in gps_df.iterrows():
            path_coords += [float(row["lon"]), float(row["lat"]), float(row["alt"])]

        # Messpunkte als GeoJSON
        features = []
        for idx, row in df.iterrows():
            r, g, b = self._value_to_rgb(row["value"], v_min, v_max)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"]), float(row["alt"])]},
                "properties": {
                    "value":   round(float(row["value"]), 4),
                    "alt":     round(float(row["alt"]), 1),
                    "rel_alt": round(float(row["rel_alt"]), 1),
                    "time":    str(idx),
                    "color":   f"rgb({r},{g},{b})",
                    "r": r, "g": g, "b": b,
                }
            })

        geojson     = json.dumps({"type": "FeatureCollection", "features": features})
        path_json   = json.dumps(path_coords)
        center_lat  = float(df["lat"].mean())
        center_lon  = float(df["lon"].mean())
        center_alt  = float(df["alt"].mean()) + 100

        legend_html = "\n".join(self._legend_steps(
            v_min, v_max,
            '<div style="display:flex;align-items:center;gap:8px;margin:3px 0">'
            '<div style="width:20px;height:12px;background:rgb({r},{g},{b});border-radius:3px"></div>'
            '{v:.3f}</div>'
        ))

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
          <tr><td>Höhe AGL</td><td>${{p.rel_alt}} m</td></tr>
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

    # -----------------------------------------------------------------------
    # MapLibre GL
    # -----------------------------------------------------------------------

    def _build_maplibre(self, df, gps_df, label, v_min, v_max, html_path):
        """MapLibre GL JS: moderner WebGL-Kartenviewer, kein API-Key."""
        features = []
        for idx, row in df.iterrows():
            r, g, b = self._value_to_rgb(row["value"], v_min, v_max)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                "properties": {"value": round(float(row["value"]), 4), "alt": round(float(row["alt"]), 1),
                               "rel_alt": round(float(row["rel_alt"]), 1), "time": str(idx), "r": r, "g": g, "b": b}
            })

        path_coords = [[float(row["lon"]), float(row["lat"])]
                       for _, row in gps_df.iterrows()]

        geojson   = json.dumps({"type": "FeatureCollection", "features": features})
        path_json = json.dumps({"type": "Feature", "geometry": {"type": "LineString", "coordinates": path_coords}})
        center    = [float(df["lon"].mean()), float(df["lat"].mean())]

        legend_steps = self._legend_steps(
            v_min, v_max,
            '<div class="leg-row"><div class="swatch" style="background:rgb({r},{g},{b})"></div>{v:.3f}</div>'
        )

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
          sat: {{ type:"raster", tiles:["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}"], tileSize:256, attribution:"Esri World Imagery" }},
          osm: {{ type:"raster", tiles:["https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png"], tileSize:256, attribution:"© OpenStreetMap" }},
        }},
        layers: [{{ id:"sat-layer",type:"raster",source:"sat" }}]
      }},
      center: {center},
      zoom: 17,
    }});

    // Karte-Toggle
    let isSat = true;
    const btn = document.createElement("button");
    btn.textContent = "🗺 Karte";
    Object.assign(btn.style, {{ position:"absolute",top:"16px",left:"16px",zIndex:10,
      padding:"8px 14px",background:"rgba(18,18,18,0.85)",color:"#fff",border:"none",
      borderRadius:"8px",cursor:"pointer",font:"13px sans-serif",backdropFilter:"blur(4px)" }});
    btn.onclick = () => {{
      isSat = !isSat;
      map.setLayoutProperty("sat-layer", "visibility", isSat ? "visible" : "none");
      map.setLayoutProperty("osm-layer", "visibility", isSat ? "none" : "visible");
      btn.textContent = isSat ? "🗺 Karte" : "🛰 Satellit";
    }};
    document.body.appendChild(btn);

    map.on("load", () => {{
      map.addLayer({{ id:"osm-layer",type:"raster",source:"osm",layout:{{visibility:"none"}} }});

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
        tip.innerHTML = `<b>{label}:</b> ${{p.value}}<br>Höhe AGL: ${{p.rel_alt}} m<br>Zeit: ${{p.time}}`;
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

    # -----------------------------------------------------------------------
    # JSON-Export
    # -----------------------------------------------------------------------

    def _export_json(self, lats, lons, alts, rel_alts, values, times,
                     label, v_min, v_max, gps_df):
        points = []
        for i in range(len(lats)):
            r, g, b = self._value_to_rgb(float(values[i]), v_min, v_max)
            points.append({
                "lat":     round(float(lats[i]), 7),
                "lon":     round(float(lons[i]), 7),
                "alt":     round(float(alts[i]), 2),
                "rel_alt": round(float(rel_alts[i]), 2),
                "value":   round(float(values[i]), 3),
                "time":    str(times[i]),
                "r": r, "g": g, "b": b,
            })

        path = [
            {"lat": round(float(row.lat), 7), "lon": round(float(row.lon), 7), "alt": round(float(row.alt), 2)}
            for _, row in gps_df.iterrows()
        ]

        data = {
            "meta": {
                "label": label,
                "point_count": len(points),
                "v_min":  round(float(v_min), 3),
                "v_max":  round(float(v_max), 3),
                "v_mean": round(float(np.mean(values)), 3),
                "v_std":  round(float(np.std(values)), 3),
                "center_lat": round(float(np.mean(lats)), 7),
                "center_lon": round(float(np.mean(lons)), 7),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
            "points": points,
            "path":   path,
        }

        json_path = ROOT / "output" / "flight_data.json"
        json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"  JSON: {json_path}")
