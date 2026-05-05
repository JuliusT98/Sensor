'use client'

import { useEffect, useRef, useMemo } from 'react'
import { FlightData } from '@/lib/types'

export default function Heatmap({ data }: { data: FlightData }) {
  const canvasRef    = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  const bounds = useMemo(() => {
    const lats = data.points.map(p => p.lat)
    const lons = data.points.map(p => p.lon)
    const pad  = 0.0003
    return {
      minLat: Math.min(...lats) - pad,
      maxLat: Math.max(...lats) + pad,
      minLon: Math.min(...lons) - pad,
      maxLon: Math.max(...lons) + pad,
    }
  }, [data])

  useEffect(() => {
    const canvas    = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const W = container.clientWidth
    const H = container.clientHeight
    canvas.width  = W
    canvas.height = H

    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = '#05070f'
    ctx.fillRect(0, 0, W, H)

    const toPixel = (lat: number, lon: number): [number, number] => [
      ((lon - bounds.minLon) / (bounds.maxLon - bounds.minLon)) * W,
      H - ((lat - bounds.minLat) / (bounds.maxLat - bounds.minLat)) * H,
    ]

    // Flight path (faint)
    ctx.strokeStyle = 'rgba(100,180,255,0.2)'
    ctx.lineWidth   = 1
    ctx.beginPath()
    data.path.forEach((p, i) => {
      const [x, y] = toPixel(p.lat, p.lon)
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    })
    ctx.stroke()

    // Heatmap blobs
    const radius = Math.min(W, H) * 0.05
    data.points.forEach(p => {
      const [x, y] = toPixel(p.lat, p.lon)
      const grad   = ctx.createRadialGradient(x, y, 0, x, y, radius)
      grad.addColorStop(0,   `rgba(${p.r},${p.g},${p.b},0.55)`)
      grad.addColorStop(0.4, `rgba(${p.r},${p.g},${p.b},0.18)`)
      grad.addColorStop(1,   `rgba(${p.r},${p.g},${p.b},0)`)
      ctx.fillStyle = grad
      ctx.beginPath()
      ctx.arc(x, y, radius, 0, Math.PI * 2)
      ctx.fill()
    })

    // Individual points (crisp)
    data.points.forEach(p => {
      const [x, y] = toPixel(p.lat, p.lon)
      ctx.fillStyle = `rgb(${p.r},${p.g},${p.b})`
      ctx.beginPath()
      ctx.arc(x, y, 2, 0, Math.PI * 2)
      ctx.fill()
    })

    // Start marker
    const [sx, sy] = toPixel(data.path[0].lat, data.path[0].lon)
    ctx.fillStyle   = '#00ff88'
    ctx.shadowColor = '#00ff88'
    ctx.shadowBlur  = 8
    ctx.beginPath()
    ctx.arc(sx, sy, 5, 0, Math.PI * 2)
    ctx.fill()
    ctx.shadowBlur  = 0
    ctx.font        = '11px sans-serif'
    ctx.fillText('Start', sx + 8, sy + 4)

    // Landing marker
    const [ex, ey] = toPixel(
      data.path[data.path.length - 1].lat,
      data.path[data.path.length - 1].lon
    )
    ctx.fillStyle   = '#ff4444'
    ctx.shadowColor = '#ff4444'
    ctx.shadowBlur  = 8
    ctx.beginPath()
    ctx.arc(ex, ey, 5, 0, Math.PI * 2)
    ctx.fill()
    ctx.shadowBlur  = 0
    ctx.fillText('Landung', ex + 8, ey + 4)

    // Color bar (bottom)
    const barW = 200, barH = 10, barX = 16, barY = H - 32
    const barGrad = ctx.createLinearGradient(barX, 0, barX + barW, 0)
    barGrad.addColorStop(0,    '#006837')
    barGrad.addColorStop(0.25, '#66bd63')
    barGrad.addColorStop(0.5,  '#d9ef8b')
    barGrad.addColorStop(0.75, '#f46d43')
    barGrad.addColorStop(1,    '#a50026')
    ctx.fillStyle = barGrad
    ctx.beginPath()
    ctx.roundRect(barX, barY, barW, barH, 3)
    ctx.fill()

    ctx.fillStyle = 'rgba(200,210,220,0.8)'
    ctx.font      = '10px sans-serif'
    ctx.fillText(data.meta.v_min.toFixed(0), barX, barY + barH + 12)
    ctx.fillText(data.meta.v_max.toFixed(0), barX + barW - 24, barY + barH + 12)
    ctx.fillText(data.meta.label, barX, barY - 6)

  }, [data, bounds])

  return (
    <div ref={containerRef} className="w-full h-full">
      <canvas ref={canvasRef} className="w-full h-full" />
    </div>
  )
}
