'use client'

import { useEffect, useState } from 'react'
import dynamic from 'next/dynamic'
import { AnimatePresence, motion } from 'framer-motion'
import { Box, Flame, AlertTriangle, Terminal } from 'lucide-react'
import { FlightData } from '@/lib/types'
import StatsPanel from '@/components/StatsPanel'
import Heatmap from '@/components/Heatmap'

const FlightMap3D = dynamic(() => import('@/components/FlightMap3D'), {
  ssr: false,
  loading: () => (
    <div className="w-full h-full flex items-center justify-center text-[var(--muted)] text-sm gap-2">
      <motion.div
        animate={{ rotate: 360 }}
        transition={{ repeat: Infinity, duration: 1.2, ease: 'linear' }}
        className="w-4 h-4 border-2 border-[var(--accent)] border-t-transparent rounded-full"
      />
      3D Engine lädt…
    </div>
  ),
})

type View = '3d' | 'heatmap'

const VIEWS: { key: View; label: string; icon: React.ReactNode }[] = [
  { key: '3d',      label: '3D Flugpfad', icon: <Box size={13} /> },
  { key: 'heatmap', label: 'Heatmap',     icon: <Flame size={13} /> },
]

export default function Dashboard() {
  const [data,  setData]  = useState<FlightData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [view,  setView]  = useState<View>('3d')

  useEffect(() => {
    fetch('/api/flight-data')
      .then(r => r.json())
      .then(d => { if (d.error) setError(d.error); else setData(d) })
      .catch(() => setError('Verbindungsfehler zur API'))
  }, [])

  if (error) return (
    <div className="h-screen flex items-center justify-center flex-col gap-4">
      <div className="w-12 h-12 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center">
        <AlertTriangle size={22} className="text-red-400" />
      </div>
      <p className="text-slate-300 text-sm">{error}</p>
      <div className="flex items-center gap-2 bg-[var(--surface)] border border-[var(--border)] rounded-lg px-3 py-2">
        <Terminal size={12} className="text-[var(--muted)]" />
        <code className="text-xs text-[var(--muted)] font-mono">python src/mission.py --simulate</code>
      </div>
    </div>
  )

  if (!data) return (
    <div className="h-screen flex items-center justify-center gap-1.5">
      {[0, 150, 300].map(delay => (
        <motion.div
          key={delay}
          className="w-1.5 h-1.5 rounded-full bg-[var(--accent)]"
          animate={{ y: [-4, 4, -4] }}
          transition={{ repeat: Infinity, duration: 0.9, delay: delay / 1000, ease: 'easeInOut' }}
        />
      ))}
    </div>
  )

  return (
    <div className="h-screen flex flex-col" style={{ background: 'var(--bg)' }}>

      {/* ── Header ────────────────────────────────────────────────────────────── */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-[var(--border)] shrink-0 backdrop-blur-sm">
        <div className="flex items-center gap-3">
          <div className="relative flex items-center justify-center w-6 h-6">
            <div className="absolute w-full h-full rounded-full bg-[var(--accent)] opacity-20 animate-ping" />
            <div className="w-2 h-2 rounded-full bg-[var(--accent)] shadow-[0_0_8px_var(--accent)]" />
          </div>
          <span className="text-white font-semibold text-sm tracking-wide">
            Spraylogic <span className="text-[var(--muted)] font-normal">·</span> Sensor Dashboard
          </span>
        </div>

        <div className="flex items-center gap-4 text-xs text-[var(--muted)]">
          <span>
            <span className="text-white font-mono font-medium">{data.meta.point_count.toLocaleString()}</span>
            {' '}Messpunkte
          </span>
          <div className="w-px h-3 bg-[var(--border-2)]" />
          <span className="text-slate-300">{data.meta.label}</span>
          <div className="w-px h-3 bg-[var(--border-2)]" />
          <span>{new Date(data.meta.generated_at).toLocaleString('de-AT')}</span>
        </div>
      </header>

      {/* ── Body ──────────────────────────────────────────────────────────────── */}
      <div className="flex flex-1 overflow-hidden">

        {/* Main view */}
        <div className="flex flex-col flex-1 min-w-0 p-4 gap-3">

          {/* View toggle */}
          <div className="flex gap-1.5 shrink-0">
            {VIEWS.map(({ key, label, icon }) => (
              <button
                key={key}
                onClick={() => setView(key)}
                className={`relative flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                  view === key
                    ? 'text-black'
                    : 'text-[var(--muted)] hover:text-slate-200 border border-[var(--border)] hover:border-[var(--border-2)]'
                }`}
              >
                {view === key && (
                  <motion.div
                    layoutId="active-tab"
                    className="absolute inset-0 rounded-lg bg-[var(--accent)]"
                    style={{ boxShadow: '0 0 16px rgba(0,212,255,0.4)' }}
                    transition={{ type: 'spring', bounce: 0.2, duration: 0.4 }}
                  />
                )}
                <span className="relative flex items-center gap-1.5">{icon}{label}</span>
              </button>
            ))}
          </div>

          {/* Canvas */}
          <div className="flex-1 relative overflow-hidden rounded-xl border border-[var(--border)]">
            <AnimatePresence mode="wait">
              <motion.div
                key={view}
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
                className="absolute inset-0"
              >
                {view === '3d'      && <FlightMap3D data={data} />}
                {view === 'heatmap' && <Heatmap data={data} />}
              </motion.div>
            </AnimatePresence>
          </div>
        </div>

        {/* Stats sidebar */}
        <aside className="w-72 shrink-0 border-l border-[var(--border)] overflow-y-auto">
          <StatsPanel data={data} />
        </aside>
      </div>
    </div>
  )
}
