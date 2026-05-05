'use client'

import { useEffect, useRef, useMemo } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { FlightData } from '@/lib/types'

export default function Heatmap({ data }: { data: FlightData }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef       = useRef<maplibregl.Map | null>(null)

  const ptGeojson = useMemo(() => ({
    type: 'FeatureCollection' as const,
    features: data.points.map(p => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: [p.lon, p.lat] },
      properties: { value: p.value, r: p.r, g: p.g, b: p.b, time: p.time },
    })),
  }), [data])

  const pathGeojson = useMemo(() => ({
    type: 'Feature' as const,
    geometry: {
      type: 'LineString' as const,
      coordinates: data.path.map(p => [p.lon, p.lat]),
    },
    properties: {},
  }), [data])

  const bounds = useMemo(() => {
    const lats = data.points.map(p => p.lat)
    const lons = data.points.map(p => p.lon)
    return new maplibregl.LngLatBounds(
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    )
  }, [data])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: {
        version: 8,
        glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
        sources: {
          sat: {
            type: 'raster',
            tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'],
            tileSize: 256,
            attribution: 'Esri World Imagery',
          },
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            attribution: '© OpenStreetMap',
          },
        },
        layers: [
          { id: 'sat-layer', type: 'raster', source: 'sat' },
          { id: 'osm-layer', type: 'raster', source: 'osm', layout: { visibility: 'none' } },
        ],
      },
      bounds,
      fitBoundsOptions: { padding: 60 },
    })

    mapRef.current = map
    map.addControl(new maplibregl.NavigationControl(), 'bottom-right')
    map.addControl(new maplibregl.ScaleControl(), 'bottom-left')

    // Karte/Satellit-Toggle
    let isSat = true
    const toggleBtn = document.createElement('button')
    toggleBtn.textContent = 'Karte'
    toggleBtn.style.cssText = `
      position:absolute;top:10px;left:10px;z-index:10;
      padding:6px 12px;background:rgba(13,17,23,0.85);color:#fff;
      border:1px solid #1e2433;border-radius:6px;cursor:pointer;
      font:12px sans-serif;backdrop-filter:blur(4px);
    `
    toggleBtn.onclick = () => {
      isSat = !isSat
      map.setLayoutProperty('sat-layer', 'visibility', isSat ? 'visible' : 'none')
      map.setLayoutProperty('osm-layer', 'visibility', isSat ? 'none' : 'visible')
      toggleBtn.textContent = isSat ? 'Karte' : 'Satellit'
    }
    containerRef.current?.appendChild(toggleBtn)

    map.on('load', () => {
      map.addSource('pts', { type: 'geojson', data: ptGeojson })

      // ── Heatmap-Layer (Jet-Farbskala, wertbasiert) ──────────────────────────
      map.addLayer({
        id: 'heatmap-layer',
        type: 'heatmap',
        source: 'pts',
        paint: {
          // Gewichtung nach Sensorwert → hoher Wert = "heiß"
          'heatmap-weight': [
            'interpolate', ['linear'], ['get', 'value'],
            data.meta.v_min, 0,
            data.meta.v_max, 1,
          ],
          // Radius wächst mit Zoom
          'heatmap-radius': [
            'interpolate', ['linear'], ['zoom'],
            12, 18,
            15, 35,
            18, 70,
          ],
          // Jet-Farbverlauf: blau → cyan → grün → gelb → rot → weiß
          'heatmap-color': [
            'interpolate', ['linear'], ['heatmap-density'],
            0,    'rgba(20,0,130,0)',
            0.08, 'rgba(30,0,220,0.65)',
            0.22, 'rgba(0,200,255,0.78)',
            0.42, 'rgba(0,230,80,0.84)',
            0.62, 'rgba(220,255,0,0.89)',
            0.78, 'rgba(255,140,0,0.93)',
            0.90, 'rgba(255,20,0,0.96)',
            1,    'rgba(255,255,255,1)',
          ],
          'heatmap-intensity': [
            'interpolate', ['linear'], ['zoom'],
            12, 0.8,
            18, 2.5,
          ],
          'heatmap-opacity': 0.82,
        },
      })

      // ── Einzelpunkte bei hohem Zoom ──────────────────────────────────────────
      map.addLayer({
        id: 'pts-layer',
        type: 'circle',
        source: 'pts',
        minzoom: 17,
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'], 17, 3, 21, 10],
          'circle-color':  ['rgb', ['get', 'r'], ['get', 'g'], ['get', 'b']],
          'circle-opacity': 0.9,
          'circle-stroke-width': 0.8,
          'circle-stroke-color': 'rgba(0,0,0,0.4)',
        },
      })

      // ── Flugpfad ─────────────────────────────────────────────────────────────
      map.addSource('path', { type: 'geojson', data: pathGeojson })
      map.addLayer({
        id: 'path-glow', type: 'line', source: 'path',
        paint: { 'line-color': '#ffdd00', 'line-width': 7, 'line-opacity': 0.15, 'line-blur': 5 },
      })
      map.addLayer({
        id: 'path-line', type: 'line', source: 'path',
        paint: { 'line-color': '#ffdd00', 'line-width': 1.5, 'line-opacity': 0.8 },
      })

      // ── Start / Landung ───────────────────────────────────────────────────────
      const mkStart = document.createElement('div')
      mkStart.style.cssText = 'width:11px;height:11px;border-radius:50%;background:#00ff88;border:2px solid #fff;box-shadow:0 0 8px #00ff88'
      new maplibregl.Marker({ element: mkStart })
        .setLngLat([data.path[0].lon, data.path[0].lat])
        .setPopup(new maplibregl.Popup({ offset: 14 }).setHTML('<b style="font:12px sans-serif">Start</b>'))
        .addTo(map)

      const mkEnd = document.createElement('div')
      mkEnd.style.cssText = 'width:11px;height:11px;border-radius:50%;background:#ff4444;border:2px solid #fff;box-shadow:0 0 8px #ff4444'
      new maplibregl.Marker({ element: mkEnd })
        .setLngLat([data.path[data.path.length - 1].lon, data.path[data.path.length - 1].lat])
        .setPopup(new maplibregl.Popup({ offset: 14 }).setHTML('<b style="font:12px sans-serif">Landung</b>'))
        .addTo(map)

      // ── Hover-Tooltip (ab Zoom 17 auf Einzelpunkten) ─────────────────────────
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12 })

      map.on('mousemove', 'pts-layer', e => {
        map.getCanvas().style.cursor = 'crosshair'
        const p = e.features![0].properties as { value: number; time: string }
        popup
          .setLngLat(e.lngLat)
          .setHTML(`
            <div style="font:12px/1.6 sans-serif;color:#fff;background:rgba(13,17,23,0.92);
                        padding:7px 11px;border-radius:7px;border:1px solid #1e2433">
              <b>${data.meta.label}:</b> ${p.value}<br>
              <span style="color:#94a3b8">Zeit: ${p.time}</span>
            </div>`)
          .addTo(map)
      })
      map.on('mouseleave', 'pts-layer', () => {
        map.getCanvas().style.cursor = ''
        popup.remove()
      })
    })

    return () => { map.remove(); mapRef.current = null }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="w-full h-full relative">
      <div ref={containerRef} className="w-full h-full" />

      {/* Jet-Legende */}
      <div className="absolute bottom-8 right-4 z-10 bg-black/70 backdrop-blur-sm rounded-lg px-3 py-2.5 text-xs text-white pointer-events-none">
        <div className="mb-1.5 font-medium text-slate-300">{data.meta.label}</div>
        <div className="w-32 h-3 rounded mb-1" style={{
          background: 'linear-gradient(to right, #1400dc, #00c8ff, #00e650, #dcff00, #ff8c00, #ff1400, #ffffff)',
        }} />
        <div className="flex justify-between w-32 text-slate-400">
          <span>{data.meta.v_min.toFixed(1)}</span>
          <span>{data.meta.v_max.toFixed(1)}</span>
        </div>
      </div>
    </div>
  )
}
