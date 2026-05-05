'use client'

import { useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { FlightData } from '@/lib/types'
import StatsPanel from '@/components/StatsPanel'
import Heatmap from '@/components/Heatmap'

const FlightMap3D = dynamic(() => import('@/components/FlightMap3D'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center text-slate-500 text-sm">
      Initialisiere 3D Engine...
    </div>
  ),
})

type View = '3d' | 'heatmap'

export default function Dashboard() {
  const [data, setData] = useState<FlightData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<View>('3d')

  useEffect(() => {
    fetch('/api/flight-data')
      .then(r => r.json())
      .then(d => { if (d.error) setError(d.error); else setData(d) })
      .catch(() => setError('Verbindungsfehler zur API'))
  }, [])

  if (error) {
    return (
      <div className="h-screen flex items-center justify-center flex-col gap-3">
        <div className="text-3xl">⚠️</div>
        <p className="text-slate-300">{error}</p>
        <p className="text-slate-600 text-xs font-mono">python src/mission.py --simulate</p>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="h-screen flex items-center justify-center gap-2 text-slate-500">
        <div className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:0ms]" />
        <div className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:150ms]" />
        <div className="w-1.5 h-1.5 rounded-full bg-accent animate-bounce [animation-delay:300ms]" />
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col" style={{ background: '#05070f' }}>

      {/* ── Header ── */}
      <header className="flex items-center justify-between px-5 py-2.5 border-b border-[#1e2433] shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-[#00d4ff] shadow-[0_0_8px_#00d4ff] animate-pulse" />
          <span className="text-white font-semibold text-sm tracking-wide">
            Spraylogic · Sensor Dashboard
          </span>
        </div>
        <div className="flex items-center gap-3 text-xs text-slate-500">
          <span className="text-slate-300 font-mono">{data.meta.point_count}</span>
          <span>Messpunkte</span>
          <span className="text-[#1e2433]">│</span>
          <span className="text-slate-300">{data.meta.label}</span>
          <span className="text-[#1e2433]">│</span>
          <span>{new Date(data.meta.generated_at).toLocaleString('de-AT')}</span>
        </div>
      </header>

      {/* ── Body ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left: main view */}
        <div className="flex flex-col flex-1 min-w-0">

          {/* View toggle */}
          <div className="flex gap-1.5 px-4 pt-3 pb-2 shrink-0">
            {([
              { key: '3d',      label: '3D Flugpfad' },
              { key: 'heatmap', label: 'Heatmap' },
            ] as { key: View; label: string }[]).map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setView(key)}
                className={`px-3 py-1 rounded text-xs font-medium transition-all ${
                  view === key
                    ? 'bg-[#00d4ff] text-black shadow-[0_0_12px_rgba(0,212,255,0.4)]'
                    : 'text-slate-400 hover:text-white border border-[#1e2433] hover:border-[#2d3748]'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* View canvas */}
          <div className="flex-1 relative overflow-hidden rounded-lg mx-4 mb-4 border border-[#1e2433]">
            {view === '3d'      && <FlightMap3D data={data} />}
            {view === 'heatmap' && <Heatmap data={data} />}
          </div>
        </div>

        {/* Right: stats */}
        <aside className="w-72 shrink-0 border-l border-[#1e2433] overflow-y-auto">
          <StatsPanel data={data} />
        </aside>
      </div>
    </div>
  )
}
