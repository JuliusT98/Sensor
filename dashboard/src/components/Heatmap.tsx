'use client'

import { useEffect, useRef, useMemo } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { FlightData } from '@/lib/types'

export default function Heatmap({ data }: { data: FlightData }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef       = useRef<maplibregl.Map | null>(null)

  const bounds = useMemo<[[number, number], [number, number]]>(() => {
    const lats = data.points.map(p => p.lat)
    const lons = data.points.map(p => p.lon)
    const pad  = 0.0008
    return [
      [Math.min(...lons) - pad, Math.min(...lats) - pad],
      [Math.max(...lons) + pad, Math.max(...lats) + pad],
    ]
  }, [data])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const { v_min, v_max } = data.meta
    const q = (t: number) => v_min + t * (v_max - v_min)

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        sources: {
          satellite: {
            type:    'raster',
            tiles:   ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
            tileSize: 256,
            maxzoom:  19,
            attribution: 'Esri World Imagery',
          },
        },
        layers: [{ id: 'satellite', type: 'raster', source: 'satellite' }],
      },
      bounds,
      fitBoundsOptions: { padding: 48 },
      attributionControl: false,
    })
    mapRef.current = map

    map.on('load', () => {
      // ── Messpunkte ────────────────────────────────────────────────────────────
      map.addSource('points', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: data.points.map(p => ({
            type:       'Feature',
            geometry:   { type: 'Point', coordinates: [p.lon, p.lat] },
            properties: { value: p.value },
          })),
        },
      })

      // Heatmap-Layer: heatmap-weight steuert die Intensität pro Punkt
      // → Bereiche mit hohen Sensorwerten erscheinen rot/gelb, niedrige blau
      map.addLayer({
        id:     'sensor-heatmap',
        type:   'heatmap',
        source: 'points',
        paint: {
          'heatmap-weight': [
            'interpolate', ['linear'], ['get', 'value'],
            v_min, 0.05,
            v_max, 1,
          ],
          // Radius wächst beim Reinzoomen damit Lücken gefüllt bleiben
          'heatmap-radius': [
            'interpolate', ['linear'], ['zoom'],
            10, 22,
            18, 60,
          ],
          'heatmap-intensity': 1.5,
          'heatmap-opacity':   0.85,
          // Jet-Farbskala: transparent (keine Daten) → blau → grün → gelb → rot
          'heatmap-color': [
            'interpolate', ['linear'], ['heatmap-density'],
            0,    'rgba(0,0,0,0)',
            0.05, '#1400dc',
            0.25, '#00c8ff',
            0.5,  '#00e650',
            0.75, '#dcff00',
            0.9,  '#ff8c00',
            1,    '#ff1400',
          ],
        },
      })

      // Präzise Punkte bei hohem Zoom (ab Zoom 17)
      map.addLayer({
        id:      'sensor-circles',
        type:    'circle',
        source:  'points',
        minzoom: 17,
        paint: {
          'circle-radius':  4,
          'circle-opacity': ['interpolate', ['linear'], ['zoom'], 17, 0, 18, 0.9],
          'circle-color': [
            'interpolate', ['linear'], ['get', 'value'],
            q(0),    '#1400dc',
            q(0.25), '#00c8ff',
            q(0.5),  '#00e650',
            q(0.75), '#dcff00',
            q(1),    '#ff1400',
          ],
        },
      })

      // ── Flugpfad ─────────────────────────────────────────────────────────────
      map.addSource('path', {
        type: 'geojson',
        data: {
          type:       'Feature',
          geometry:   { type: 'LineString', coordinates: data.path.map(p => [p.lon, p.lat]) },
          properties: {},
        },
      })
      map.addLayer({
        id:     'flight-path',
        type:   'line',
        source: 'path',
        paint:  { 'line-color': '#ffdd00', 'line-width': 1.5, 'line-opacity': 0.7 },
      })

      // ── Start / Landung ───────────────────────────────────────────────────────
      const marker = (color: string, label: string, lngLat: [number, number]) => {
        const el = document.createElement('div')
        el.style.cssText = `width:10px;height:10px;border-radius:50%;background:${color};border:2px solid white;cursor:pointer`
        new maplibregl.Marker({ element: el })
          .setLngLat(lngLat)
          .setPopup(new maplibregl.Popup({ offset: 12, closeButton: false }).setText(label))
          .addTo(map)
      }

      marker('#00ff88', 'Start',   [data.path[0].lon,                       data.path[0].lat])
      marker('#ff4444', 'Landung', [data.path[data.path.length - 1].lon, data.path[data.path.length - 1].lat])
    })

    return () => { map.remove(); mapRef.current = null }
  }, [data, bounds]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="w-full h-full relative">
      <div ref={containerRef} className="w-full h-full" />

      {/* Jet-Legende */}
      <div className="absolute bottom-4 right-4 z-10 bg-black/70 backdrop-blur-sm rounded-lg px-3 py-2.5 text-xs text-white pointer-events-none">
        <div className="mb-1.5 font-medium text-slate-300">{data.meta.label}</div>
        <div className="w-32 h-3 rounded mb-1" style={{
          background: 'linear-gradient(to right, #1400dc, #00c8ff, #00e650, #dcff00, #ff8c00, #ff1400)',
        }} />
        <div className="flex justify-between w-32 text-slate-400">
          <span>{data.meta.v_min.toFixed(1)}</span>
          <span>{data.meta.v_max.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}
